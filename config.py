"""
Central configuration for generic RAG system.
- Paths: configurable data directory (default: ./data/)
- Models: Mistral Small 3.1 22B (Ollama), BAAI/bge-large-en-v1.5 (embeddings)
- Chunking: unified size for any document type
- Sources: PDF files, plain-text files, arbitrary web URLs
"""

import os
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ============================================================================
# DATA PATHS
# ============================================================================

def _resolve_data_dir() -> Path:
    """
    Resolve the data directory in priority order:
      1. settings.json  data_dir  (user changed via web UI)
      2. RAG_DATA_DIR   env var   (set in .env or shell)
      3. ./data/                  (project-local default)
    """
    settings_file = Path(__file__).parent / "settings.json"
    if settings_file.exists():
        try:
            s = json.loads(settings_file.read_text(encoding="utf-8"))
            if s.get("data_dir"):
                return Path(s["data_dir"])
        except Exception:
            pass
    env_dir = os.getenv("RAG_DATA_DIR", "")
    if env_dir:
        return Path(env_dir)
    return Path(__file__).parent / "data"

CACHE_ROOT = _resolve_data_dir()
RAW_DIR = CACHE_ROOT / "raw"
CHROMA_DIR = CACHE_ROOT / "chroma"

# Create directories
RAW_DIR.mkdir(parents=True, exist_ok=True)
(RAW_DIR / "uploads").mkdir(exist_ok=True)
CHROMA_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================================
# LLM & EMBEDDING MODELS
# ============================================================================

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
LLM_MODEL = os.getenv("LLM_MODEL", "mistral-small3.1")

EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-large-en-v1.5")
EMBED_DEVICE = os.getenv("EMBED_DEVICE", "cpu")

CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION", "rag_documents")

# ============================================================================
# DATABASE & SERVICES (v2.0 multi-user)
# ============================================================================

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://rag:rag_password@postgres:5432/rag_db")
QDRANT_HOST = os.getenv("QDRANT_HOST", "qdrant")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

# ============================================================================
# CHUNKING PARAMETERS
# ============================================================================

CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "600"))      # tokens
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "100")) # tokens

# Query retrieval
TOP_K = int(os.getenv("TOP_K", "8"))           # chunks to retrieve per query
RERANK_TOP_K = int(os.getenv("RERANK_TOP_K", "5"))  # after BM25 re-ranking

# ============================================================================
# PROMPT TEMPLATE
# ============================================================================

RAG_PROMPT_TEMPLATE = """You are a knowledgeable research assistant. Answer the user's question based ONLY on the provided source excerpts. For each factual claim in your answer, cite the specific source with [N] reference.

SOURCES:
{sources_text}

QUESTION: {question}

INSTRUCTIONS:
1. Answer in clear, plain language.
2. Use paragraph breaks, bullet points, and bold for key terms as appropriate.
3. Cite sources inline as [1], [2], etc. with every factual claim.
4. If the sources do not contain enough information to answer, say so explicitly.
5. Do not cite sources not listed above.
6. If sources contradict each other, note the disagreement and cite both.

ANSWER:"""

# ============================================================================
# LOGGING & DEBUG
# ============================================================================

DEBUG = os.getenv("DEBUG", "false").lower() == "true"
LOG_INGESTION = True


if __name__ == "__main__":
    print(f"CACHE_ROOT: {CACHE_ROOT}")
    print(f"RAW_DIR: {RAW_DIR}")
    print(f"CHROMA_DIR: {CHROMA_DIR}")
    print(f"LLM_MODEL: {LLM_MODEL}")
    print(f"EMBED_MODEL: {EMBED_MODEL}")
    print(f"CHROMA_COLLECTION: {CHROMA_COLLECTION}")
    print("Directories created successfully.")
