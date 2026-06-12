"""
SQLite database layer for the regulatory QA system.

Tables:
  guidelines         - one row per PDF (main or sub-document)
  chunks             - one row per text chunk
  vec_chunks         - sqlite-vec virtual table, holds the embedding for each chunk
  query_log          - audit log of every Q&A

sqlite-vec is loaded as a SQLite extension into each connection.
The vector search uses the cosine distance metric.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import sqlite_vec
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, List, Optional

from .config import DB_PATH, EMBEDDING_DIM

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

@contextmanager
def connect(db_path: Path = DB_PATH):
    """
    Context manager: yield a sqlite3.Connection with sqlite-vec loaded,
    foreign keys + WAL enabled, and row_factory set to sqlite3.Row.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

# sqlite-vec expects dimension as a literal integer in the CREATE TABLE.
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS guidelines (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT    NOT NULL,
    title           TEXT    NOT NULL,
    source_url      TEXT    NOT NULL UNIQUE,
    local_path      TEXT    NOT NULL,
    sha256          TEXT    NOT NULL,
    page_count      INTEGER NOT NULL DEFAULT 0,
    file_bytes      INTEGER NOT NULL DEFAULT 0,
    is_main         INTEGER NOT NULL DEFAULT 1,
    parent_code     TEXT,
    version_label   TEXT,
    is_repealed     INTEGER NOT NULL DEFAULT 0,
    extraction_engine TEXT,
    extraction_warnings TEXT,
    crawled_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_guidelines_code ON guidelines(code);
CREATE INDEX IF NOT EXISTS idx_guidelines_sha  ON guidelines(sha256);

CREATE TABLE IF NOT EXISTS chunks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guideline_id    INTEGER NOT NULL REFERENCES guidelines(id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,
    page_number     INTEGER NOT NULL,
    char_start      INTEGER NOT NULL,
    char_end        INTEGER NOT NULL,
    text            TEXT    NOT NULL,
    section_number  TEXT    NOT NULL DEFAULT '',
    section_heading TEXT    NOT NULL DEFAULT '',
    char_count      INTEGER NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_chunks_guideline ON chunks(guideline_id);
CREATE INDEX IF NOT EXISTS idx_chunks_section   ON chunks(guideline_id, section_number);

-- FTS5 virtual table for keyword / phrase search.
-- Indexed: the chunk text + section heading + guideline title (joined at query time).
-- tokenize='unicode61' is the default; good for English. For mixed CN/EN we'd
-- swap to 'trigram' or 'unicode61 remove_diacritics 2'.
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_text,
    section_heading,
    content='chunks',
    content_rowid='id',
    tokenize='unicode61'
);

-- Triggers keep FTS in sync with the chunks table
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, chunk_text, section_heading)
    VALUES (new.id, new.text, new.section_heading);
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, chunk_text, section_heading)
    VALUES ('delete', old.id, old.text, old.section_heading);
END;

CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, chunk_text, section_heading)
    VALUES ('delete', old.id, old.text, old.section_heading);
    INSERT INTO chunks_fts(rowid, chunk_text, section_heading)
    VALUES (new.id, new.text, new.section_heading);
END;

-- Vector index. cosine distance so scores are in [0, 2].
CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
    chunk_id INTEGER PRIMARY KEY,
    embedding float[{EMBEDDING_DIM}] distance_metric=cosine
);

-- Audit log: every question, answer, and which chunks were used.
CREATE TABLE IF NOT EXISTS query_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    asked_at        TEXT NOT NULL DEFAULT (datetime('now')),
    user            TEXT,
    question        TEXT NOT NULL,
    retrieved_chunks TEXT,
    llm_provider    TEXT,
    llm_model       TEXT,
    answer          TEXT,
    latency_ms      INTEGER
);

CREATE INDEX IF NOT EXISTS idx_query_log_asked_at ON query_log(asked_at);
"""


def init_db() -> None:
    """
    Create tables if they don't exist.

    Special handling for vec_chunks: if the existing virtual table was created
    with a different EMBEDDING_DIM than the current config, drop and recreate
    it. This lets you swap embedding providers (OpenAI 1536 <-> Jina 1024 <-> 
    OpenAI 3072) without manual SQL. NOTE: dropping vec_chunks loses the
    vectors; you must re-run the crawl to re-embed.
    """
    with connect() as conn:
        conn.executescript(SCHEMA)
        # Check if existing vec_chunks has a different dim
        try:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='vec_chunks'"
            ).fetchone()
            if row and row[0]:
                # The SQL contains "float[N]" — extract N
                import re
                m = re.search(r"float\[(\d+)\]", row[0])
                if m:
                    existing_dim = int(m.group(1))
                    if existing_dim != EMBEDDING_DIM:
                        log.warning(
                            "vec_chunks was created with dim=%d but EMBEDDING_DIM=%d. "
                            "Dropping and recreating. You will need to re-run the crawl "
                            "to re-embed the corpus with the new provider.",
                            existing_dim, EMBEDDING_DIM,
                        )
                        conn.execute("DROP TABLE vec_chunks")
                        conn.executescript(SCHEMA)
        except Exception as e:
            log.debug("vec_chunks dim check skipped: %s", e)
        conn.commit()
    log.info("DB initialized at %s (vec dim=%d)", DB_PATH, EMBEDDING_DIM)


# ---------------------------------------------------------------------------
# Insert helpers
# ---------------------------------------------------------------------------

def upsert_guideline(
    conn: sqlite3.Connection,
    *,
    code: str,
    title: str,
    source_url: str,
    local_path: str,
    sha256: str,
    page_count: int,
    file_bytes: int,
    is_main: bool,
    parent_code: Optional[str] = None,
    version_label: str = "",
    is_repealed: bool = False,
    extraction_engine: str = "",
    extraction_warnings: Optional[List[str]] = None,
) -> int:
    """
    Insert or update a guideline record (matched on source_url).
    Returns the row id.
    """
    warnings_json = json.dumps(extraction_warnings or [])
    cur = conn.execute(
        """
        INSERT INTO guidelines
            (code, title, source_url, local_path, sha256, page_count, file_bytes,
             is_main, parent_code, version_label, is_repealed,
             extraction_engine, extraction_warnings, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(source_url) DO UPDATE SET
            code = excluded.code,
            title = excluded.title,
            local_path = excluded.local_path,
            sha256 = excluded.sha256,
            page_count = excluded.page_count,
            file_bytes = excluded.file_bytes,
            is_main = excluded.is_main,
            parent_code = excluded.parent_code,
            version_label = excluded.version_label,
            is_repealed = excluded.is_repealed,
            extraction_engine = excluded.extraction_engine,
            extraction_warnings = excluded.extraction_warnings,
            updated_at = datetime('now')
        RETURNING id
        """,
        (
            code, title, source_url, local_path, sha256, page_count, file_bytes,
            1 if is_main else 0, parent_code, version_label,
            1 if is_repealed else 0,
            extraction_engine, warnings_json,
        ),
    )
    return cur.fetchone()[0]


def insert_chunks(
    conn: sqlite3.Connection,
    guideline_id: int,
    chunks: Iterable[dict],
) -> int:
    """
    Insert chunks for a guideline. Wipes any existing chunks + their vectors
    for that guideline first (so re-ingestion is clean). Returns count.
    """
    # Remove old chunks for this guideline (cascade removes vec_chunks via
    # trigger? No — we delete vec rows explicitly below)
    cur = conn.execute(
        "SELECT id FROM chunks WHERE guideline_id = ?", (guideline_id,)
    )
    old_chunk_ids = [r[0] for r in cur.fetchall()]
    if old_chunk_ids:
        placeholders = ",".join("?" * len(old_chunk_ids))
        conn.execute(
            f"DELETE FROM vec_chunks WHERE chunk_id IN ({placeholders})",
            old_chunk_ids,
        )
    conn.execute("DELETE FROM chunks WHERE guideline_id = ?", (guideline_id,))

    rows = [
        (
            guideline_id,
            c["chunk_index"],
            c["page_number"],
            c["char_start"],
            c["char_end"],
            c["text"],
            c.get("section_number", ""),
            c.get("section_heading", ""),
            c["char_count"],
        )
        for c in chunks
    ]
    conn.executemany(
        """
        INSERT INTO chunks
            (guideline_id, chunk_index, page_number, char_start, char_end, text,
             section_number, section_heading, char_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def get_chunks_for_guideline(
    conn: sqlite3.Connection, guideline_id: int
) -> List[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM chunks WHERE guideline_id = ? ORDER BY chunk_index",
            (guideline_id,),
        )
    )


# ---------------------------------------------------------------------------
# Vector operations
# ---------------------------------------------------------------------------

def insert_vec(
    conn: sqlite3.Connection, chunk_id: int, embedding: List[float]
) -> None:
    """Insert one chunk embedding (float list -> raw float32 bytes)."""
    blob = sqlite_vec.serialize_float32(embedding)
    conn.execute(
        "INSERT INTO vec_chunks(chunk_id, embedding) VALUES (?, ?)",
        (chunk_id, blob),
    )


def insert_vecs_bulk(
    conn: sqlite3.Connection, items: List[tuple]
) -> None:
    """Bulk insert chunk embeddings."""
    conn.executemany(
        "INSERT INTO vec_chunks(chunk_id, embedding) VALUES (?, ?)",
        [(cid, sqlite_vec.serialize_float32(emb)) for cid, emb in items],
    )


def vector_search(
    conn: sqlite3.Connection,
    query_embedding: List[float],
    top_k: int = 8,
) -> List[dict]:
    """
    Cosine-similarity search. Returns top_k chunks with:
      {chunk_id, guideline_id, code, title, page_number, text, section_number,
       section_heading, distance}
    Lower distance = more similar (cosine distance in [0, 2]).
    """
    qblob = sqlite_vec.serialize_float32(query_embedding)
    rows = conn.execute(
        """
        SELECT
            v.chunk_id,
            c.guideline_id,
            g.code,
            g.title,
            c.page_number,
            c.text,
            c.section_number,
            c.section_heading,
            v.distance
        FROM vec_chunks v
        JOIN chunks c       ON c.id = v.chunk_id
        JOIN guidelines g   ON g.id = c.guideline_id
        WHERE v.embedding MATCH ?
          AND k = ?
        ORDER BY v.distance
        """,
        (qblob, top_k),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# FTS5 keyword search
# ---------------------------------------------------------------------------

def fts_search(
    conn: sqlite3.Connection,
    query: str,
    top_k: int = 8,
) -> List[dict]:
    """
    Full-text keyword search using SQLite FTS5.

    The query is sanitized (quoted special chars escaped) and passed to FTS5
    with prefix matching on the last token, so "aud" matches "audit",
    "auditor", "auditing", etc.

    Returns chunks with the same shape as vector_search, plus a 'fts_score'
    (BM25, lower is better — we negate it for the merge).
    """
    # Sanitize: FTS5 reserved words / syntax. We use a simple OR-of-prefixes
    # form which is safe for typical auditor queries.
    tokens = re.findall(r"[A-Za-z0-9_\-\u4e00-\u9fff]+", query)
    if not tokens:
        return []
    # Build "token1* OR token2* OR ..." — prefix match
    fts_query = " OR ".join(f'"{t}"*' for t in tokens)

    rows = conn.execute(
        """
        SELECT
            c.id AS chunk_id,
            c.guideline_id,
            g.code,
            g.title,
            c.page_number,
            c.text,
            c.section_number,
            c.section_heading,
            bm25(chunks_fts) AS fts_score
        FROM chunks_fts
        JOIN chunks c       ON c.id = chunks_fts.rowid
        JOIN guidelines g   ON g.id = c.guideline_id
        WHERE chunks_fts MATCH ?
        ORDER BY fts_score
        LIMIT ?
        """,
        (fts_query, top_k),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Hybrid retrieval
# ---------------------------------------------------------------------------

def hybrid_search(
    conn: sqlite3.Connection,
    query: str,
    query_embedding: List[float],
    top_k: int = 8,
    *,
    vec_weight: float = 0.5,
    fts_weight: float = 0.3,
    title_boost: float = 0.15,
    heading_boost: float = 0.05,
) -> List[dict]:
    """
    Merge results from vector_search and fts_search, then apply boosts for:
      - exact match in the guideline title (huge boost — user is asking
        about a specific guideline)
      - exact match in the section heading
    Deduplicate by chunk_id; rerank; return top_k.
    """
    # Pull a wider candidate set from each source so the merge has room to work
    CANDIDATE_K = max(top_k * 4, 32)
    vec_hits = vector_search(conn, query_embedding, top_k=CANDIDATE_K)
    fts_hits = fts_search(conn, query, top_k=CANDIDATE_K)

    # Normalize each score to [0, 1] where 1 = best
    def normalize_vec(hits):
        # cosine distance in [0, 2]; lower better. Convert to similarity in [0, 1].
        if not hits:
            return {}
        sims = {h["chunk_id"]: max(0.0, 1.0 - h["distance"] / 2.0) for h in hits}
        return sims

    def normalize_fts(hits):
        # BM25: lower is better. Negate, then min-max into [0, 1].
        if not hits:
            return {}
        raw = [-h["fts_score"] for h in hits]
        lo, hi = min(raw), max(raw)
        if hi - lo < 1e-9:
            return {h["chunk_id"]: 1.0 for h in hits}
        return {h["chunk_id"]: (s - lo) / (hi - lo) for h, s in zip(hits, raw)}

    vec_sims = normalize_vec(vec_hits)
    fts_sims = normalize_fts(fts_hits)

    # Build a dict of chunk_id -> combined record
    all_chunks: dict[int, dict] = {}
    for h in vec_hits + fts_hits:
        cid = h["chunk_id"]
        if cid not in all_chunks:
            all_chunks[cid] = h

    query_lower = query.lower()
    # Apply boosts
    for cid, chunk in all_chunks.items():
        score = (
            vec_weight * vec_sims.get(cid, 0.0)
            + fts_weight * fts_sims.get(cid, 0.0)
        )
        # Title boost: if the guideline code or title appears in the query
        title = (chunk.get("title") or "").lower()
        code = (chunk.get("code") or "").lower()
        if code and code in query_lower.split():
            score += title_boost
        elif title and any(w in query_lower for w in title.split() if len(w) > 3):
            score += title_boost * 0.5
        # Heading boost: if the section heading text appears in the query
        heading = (chunk.get("section_heading") or "").lower()
        if heading and any(w in query_lower for w in heading.split() if len(w) > 4):
            score += heading_boost
        chunk["hybrid_score"] = round(score, 6)

    ranked = sorted(all_chunks.values(), key=lambda x: x["hybrid_score"], reverse=True)
    return ranked[:top_k]


# ---------------------------------------------------------------------------
# Query log
# ---------------------------------------------------------------------------

def log_query(
    conn: sqlite3.Connection,
    *,
    user: str,
    question: str,
    retrieved_chunks: List[dict],
    llm_provider: str,
    llm_model: str,
    answer: str,
    latency_ms: int,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO query_log
            (user, question, retrieved_chunks, llm_provider, llm_model, answer, latency_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user, question, json.dumps(retrieved_chunks),
            llm_provider, llm_model, answer, latency_ms,
        ),
    )
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def stats(conn: Optional[sqlite3.Connection] = None) -> dict:
    close = False
    if conn is None:
        ctx = connect()
        conn = ctx.__enter__()
        close = True
    try:
        guideline_count = conn.execute("SELECT COUNT(*) FROM guidelines").fetchone()[0]
        chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        vec_count = conn.execute("SELECT COUNT(*) FROM vec_chunks").fetchone()[0]
        total_chars = conn.execute(
            "SELECT COALESCE(SUM(char_count), 0) FROM chunks"
        ).fetchone()[0]
        by_code = list(
            conn.execute(
                """
                SELECT g.code, COUNT(c.id) AS n, COALESCE(SUM(c.char_count), 0) AS chars
                FROM guidelines g
                LEFT JOIN chunks c ON c.guideline_id = g.id
                GROUP BY g.code
                ORDER BY g.code
                """
            )
        )
        return {
            "guidelines": guideline_count,
            "chunks": chunk_count,
            "vec_chunks": vec_count,
            "total_chars": total_chars,
            "by_code": [dict(r) for r in by_code],
        }
    finally:
        if close:
            ctx.__exit__(None, None, None)
