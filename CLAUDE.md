# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Run locally (no Docker):**
```bash
pip install -r requirements.txt
cp .env.example .env        # fill in POSTGRES_PASSWORD, JWT_SECRET, ENCRYPTION_KEY
uvicorn app_fastapi:app --reload --port 8000
python worker.py            # separate terminal — processes ingestion jobs
```

**Docker (production):**
```bash
docker compose up -d        # starts all 7 services
docker compose ps           # verify health
docker compose logs -f rag  # tail app logs
docker compose logs -f rag-worker
```

**No test suite exists.** Manual testing via `curl http://localhost:8000/api/health` or the browser UI. The FastAPI interactive docs are available at `/api/docs` only when `DEBUG=true`.

**CI/CD:** Every push to `master` triggers `.github/workflows/deploy.yml` — builds and pushes a Docker image to `ghcr.io/jemplayer82/rag`, then SSH-deploys to a VPS via `docker compose pull && docker compose up -d`.

## Architecture

### Service Map (docker-compose.yml)
| Container | Role |
|-----------|------|
| `rag` | FastAPI + Gunicorn, port 8001→8000 |
| `rag-worker` | Redis RQ worker (`python worker.py`) |
| `postgres` | User accounts, documents, jobs, LLM config |
| `qdrant` | Vector store (per-user collections) |
| `redis` | Job queue for ingestion |
| `nginx` | Reverse proxy on port 80 |
| `ollama` | Local LLM inference |

Data volumes are mounted from `/storage/rag/` on the host (not named Docker volumes).

### Request Flow

**Document ingestion:**
`POST /api/sources` → save file to disk → create `Document` + `IngestionJob` rows in Postgres → enqueue `run_ingestion_job()` on Redis RQ → `rag-worker` picks it up → `ingest.py` extracts text + chunks → `QdrantManager.upsert_chunks()` embeds and stores vectors in `user_{user_id}` Qdrant collection.

If Redis is unavailable, the job falls back to `asyncio.create_task()` running inline in the FastAPI process.

**Chat query:**
`POST /api/chat` → `query_async()` in `rag_async.py` → `QdrantManager.search()` (semantic, top-8) → BM25 re-rank (60% semantic / 40% BM25, keeps top-5) → build prompt from `RAG_PROMPT_TEMPLATE` → `query_llm_async()` in `llm_provider.py` → LLM response with `[N]` citations.

### Key Design Decisions

**Admin-centric model:** Only the first registered user is admin. `POST /api/sources` requires admin. `POST /api/chat` and `GET /api/library` are public but query the admin's Qdrant collection (`user_{admin_id}`), not the caller's. The per-user isolation in `QdrantManager` exists but is only exercised through the admin account in practice.

**LLM provider routing (`llm_provider.py`):** Active config lives in the `llm_provider_configs` table (single row). Admin sets it at `/admin/llm-settings`. Falls back to env vars (`LLM_PROVIDER`, `LLM_MODEL`, etc.) if no DB row exists. Supported providers: `ollama`, `openai`, `anthropic`, `generic` (OpenAI-compatible).

**API key encryption:** LLM provider API keys are Fernet-encrypted before writing to Postgres (`encrypt_api_key` / `decrypt_api_key` in `models.py`). The `ENCRYPTION_KEY` env var must be a base64url-encoded 32-byte key. If missing, a random key is generated per-process (keys become unreadable after restart).

**Embedding model:** Hardcoded dimension `EMBED_DIM = 1024` in `ingest_async.py` for `BAAI/bge-large-en-v1.5`. Changing `EMBED_MODEL` to a different-dimension model requires updating this constant and recreating Qdrant collections.

**Global embedder singleton:** `_embedder` in `rag_async.py` is loaded once and shared across all requests. Changing the embed device (cpu/cuda/rocm) via `/api/admin/embed-device` sets `rag_async._embedder = None` to force a reload on the next request.

**Async pattern:** All blocking I/O (embedding, Qdrant, file parsing) runs inside `asyncio.to_thread()` to avoid blocking FastAPI's event loop.

### File Responsibilities

| File | Purpose |
|------|---------|
| `app_fastapi.py` | All routes, auth middleware, Pydantic schemas, startup |
| `models.py` | SQLAlchemy ORM (`User`, `Document`, `IngestionJob`, `LLMProviderConfig`), DB init, key encryption |
| `config.py` | All env var reads + constants; data dir resolution (settings.json → env → `./data/`) |
| `rag_async.py` | Retrieval pipeline: embed query → Qdrant search → BM25 re-rank → LLM call |
| `llm_provider.py` | Provider dispatch: routes to OpenAI / Anthropic / Ollama / generic HTTP |
| `ingest_async.py` | `QdrantManager` class + `run_ingestion_job()` (the RQ worker payload) |
| `ingest.py` | Raw text extraction (`ingest_pdf`, `ingest_txt`, `ingest_url`, `ingest_docx`) and `chunk_text` |
| `worker.py` | RQ worker entrypoint; uses `SimpleWorker` on Windows, `Worker` on Linux |

### Auth

JWT tokens (HS256, 30-day expiry) stored in browser `localStorage`. The `oauth2_scheme` dependency reads from `Authorization: Bearer` header. HTML pages are served without server-side auth; JavaScript fetches the token from localStorage and includes it in API calls. `get_current_user` validates the token on every protected endpoint. `require_admin` wraps `get_current_user` and checks `user.is_admin`.
