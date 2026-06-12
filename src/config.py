"""
Project configuration.

The LLM provider and embedding provider are swappable here without touching
business code. See PROMPTS/ for the prompt templates.
"""
from __future__ import annotations

import os
from pathlib import Path

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
# Default MiniMax embo-01 is 1536-dim (set EMBEDDING_DIM in .env if your
# model differs).
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "minimax")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "embo-01")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))

# MiniMax embedding
MINIMAX_EMBEDDING_MODEL = os.getenv("MINIMAX_EMBEDDING_MODEL", "embo-01")
MINIMAX_EMBEDDING_DIM = int(os.getenv("MINIMAX_EMBEDDING_DIM", "1536"))

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
