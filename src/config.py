"""
Project configuration.

The LLM provider and embedding provider are swappable here without touching
business code. See PROMPTS/ for the prompt templates.
"""
from __future__ import annotations

import os
from pathlib import Path

# Load .env from the project root (one level up from src/) so that API keys
# are visible to os.getenv(). Safe to call multiple times.
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path, override=False)
except ImportError:
    pass  # python-dotenv not installed; assume env vars are set externally

# ----------------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
PDF_DIR = DATA_DIR / "pdfs" / "IA"
LOG_DIR = PROJECT_ROOT / "logs"
REPORT_DIR = PROJECT_ROOT / "reports"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
DB_PATH = DATA_DIR / "regulatory.db"

for d in (DATA_DIR, PDF_DIR, LOG_DIR, REPORT_DIR, PROMPTS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ----------------------------------------------------------------------------
# Crawler
# ----------------------------------------------------------------------------
# The IA HK page lists all GL* guidelines + sub-documents (FAQs, Q&As, etc.)
GUIDELINES_INDEX_URL = (
    "https://www.ia.org.hk/en/legislative_framework/guidelines.php"
)
# Polite delay between PDF downloads (seconds). Be a good citizen.
CRAWL_DELAY_SEC = 1.0
# User agent — identify ourselves so IA HK can contact us if there's a problem.
HTTP_USER_AGENT = "AuditRegulatoryQA/0.1 (internal-audit use; +mailto:audit@example.com)"

# Sample size for the initial smoke test
SAMPLE_PDF_COUNT = 5

# ----------------------------------------------------------------------------
# LLM provider
# ----------------------------------------------------------------------------
# Pick one of: "openai", "anthropic", "deepseek", "minimax"
# DeepSeek is OpenAI-API-compatible, so it uses the "openai" client with a
# custom base_url — see llm.py.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")

# OpenAI (also used for DeepSeek and other OpenAI-compatible providers)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")  # set to DeepSeek URL etc.

# Anthropic
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")

# DeepSeek (OpenAI-compatible)
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# MiniMax (M2.7 / M3) — wire in your endpoint when ready
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")
MINIMAX_MODEL = os.getenv("MINIMAX_MODEL", "MiniMax-M2.7")
MINIMAX_BASE_URL = os.getenv("MINIMAX_BASE_URL", "")

# ----------------------------------------------------------------------------
# Embedding provider
# ----------------------------------------------------------------------------
# Pick one of: "openai", "minimax", "jina"
# All three plug into the same interface in src/embed.py. Switching provider
# means setting EMBEDDING_PROVIDER and re-running the ingest.
# IMPORTANT: EMBEDDING_DIM must match the provider's model — sqlite-vec stores
# vectors as fixed-size float arrays, so a different dim = different table.
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "openai").lower()
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))

# OpenAI (default)
# text-embedding-3-small: 1536-dim, $0.02/1M tokens, ~$0.07 for our 62-PDF corpus
# text-embedding-3-large: 3072-dim, $0.13/1M tokens, higher quality
OPENAI_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
OPENAI_EMBEDDING_DIM = int(os.getenv("OPENAI_EMBEDDING_DIM", "1536"))

# MiniMax
# embo-01: 1536-dim, free with MiniMax M-series key, requires VPN-free access
MINIMAX_EMBEDDING_MODEL = os.getenv("MINIMAX_EMBEDDING_MODEL", "embo-01")
MINIMAX_EMBEDDING_DIM = int(os.getenv("MINIMAX_EMBEDDING_DIM", "1536"))

# Jina AI (jina.ai)
# jina-embeddings-v3: 1024-dim (default), supports 32k tokens, multilingual
# Free tier: 1M tokens/month. Pro: $0.02/1M tokens.
JINA_API_KEY = os.getenv("JINA_API_KEY", "")
JINA_EMBEDDING_MODEL = os.getenv("JINA_EMBEDDING_MODEL", "jina-embeddings-v3")
JINA_EMBEDDING_DIM = int(os.getenv("JINA_EMBEDDING_DIM", "1024"))
JINA_BASE_URL = os.getenv("JINA_BASE_URL", "https://api.jina.ai/v1")

# ----------------------------------------------------------------------------
# Retrieval
# ----------------------------------------------------------------------------
# How many chunks to retrieve per question
TOP_K_CHUNKS = 8
# Chunk size and overlap (characters). Regulatory text is dense, so 1000/200 is sane.
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200

# ----------------------------------------------------------------------------
# SQLite-vec — loadable extension
# ----------------------------------------------------------------------------
# sqlite-vec is a loadable extension. The Python package ships the binary;
# we just need the path. This is platform-dependent, so we let the loader
# figure it out at import time in db.py.
