<!-- refreshed: 2026-06-16 -->
# Architecture

**Analysis Date:** 2026-06-16

## System Overview

```text
┌──────────────────────────────────────────────────────────────────┐
│                     HTTP Clients (Browser)                        │
│              JWT in localStorage — Bearer header on API           │
└───────────────────────────┬──────────────────────────────────────┘
                            │ :8000
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│  rag service — Gunicorn + 4 × UvicornWorker                      │
│  `app_fastapi.py`  FastAPI app                                    │
│  ├── HTML pages (Jinja2)   GET / /login /register /library …      │
│  ├── Auth API              POST /api/auth/register|login           │
│  ├── Chat API              POST /api/chat                          │
│  ├── Library API           GET  /api/library                       │
│  ├── Source mgmt           POST|DELETE /api/sources                │
│  ├── Settings API          GET|POST /api/settings                  │
│  └── Admin APIs            /api/admin/llm-settings /ollama/…       │
└────┬─────────────┬─────────────────────────────┬─────────────────┘
     │             │                             │
     ▼             ▼                             ▼
┌─────────┐  ┌──────────┐             ┌──────────────────┐
│ postgres │  │  redis   │             │     qdrant       │
│ :5432    │  │  :6379   │             │     :6333        │
│ users    │  │ ingestion│             │ user_{id}        │
│ documents│  │  queue   │             │ collections      │
│ ingestion│  └────┬─────┘             │ (cosine, 1024d)  │
│ _jobs    │       │ dequeue           └──────────────────┘
│ llm_     │       ▼                            ▲
│ provider │  ┌──────────────────────┐          │ upsert/search
│ _configs │  │  rag-worker service  │          │
└─────────┘  │  `worker.py`         │──────────┘
             │  `ingest_async.py`   │
             │  `ingest.py`         │
             └──────────────────────┘
                            ▲
                            │ pull models / generate
                            ▼
                     ┌──────────────┐
                     │   ollama     │
                     │   :11434     │
                     └──────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| FastAPI app | Routes, auth, Pydantic schemas, startup | `app_fastapi.py` |
| ORM models | User/Document/IngestionJob/LLMProviderConfig tables, Fernet encryption, DB init retry | `models.py` |
| Config | Env vars, data-dir resolution, chunking constants, RAG prompt template | `config.py` |
| RAG query engine | Async retrieval: Qdrant semantic search + BM25 rerank → LLM prompt | `rag_async.py` |
| LLM provider layer | Provider dispatch: OpenAI / Anthropic / Ollama / generic HTTP | `llm_provider.py` |
| Ingestion pipeline (async) | QdrantManager, run_ingestion_job (RQ job target) | `ingest_async.py` |
| Ingestion pipeline (sync) | Text extraction (PDF/TXT/DOCX/DOC/URL/crawl), chunk_text | `ingest.py` |
| RQ worker | Listens on `ingestion` queue, reaps stale jobs on boot | `worker.py` |
| Secrets bootstrap | Auto-generates/persists JWT_SECRET + ENCRYPTION_KEY on first boot | `secrets_bootstrap.py` |
| Settings persistence | JSON-based runtime settings (data_dir, llm_model) | `settings.py` |

## Pattern Overview

**Overall:** Multi-service RAG with async FastAPI front-end, Redis RQ background workers, and per-user Qdrant vector namespaces.

**Key Characteristics:**
- Admin-centric model: first registered user auto-becomes admin; all chat and library queries run against `user_{admin_id}` Qdrant collection
- Sync heavy-lifting runs in `asyncio.to_thread()` so Uvicorn workers stay unblocked
- Blocking ingestion (embedding, Qdrant writes) is pushed to the `rag-worker` container via Redis RQ; falls back to `asyncio.create_task` inline if Redis is unreachable
- LLM and embedding config live in the `llm_provider_configs` DB table, updated by admin at runtime — no restart required

## Layers

**HTTP Layer:**
- Purpose: Request validation, auth, response serialization
- Location: `app_fastapi.py`
- Contains: Route handlers, Pydantic schemas, JWT helpers, OAuth2 dependency, admin guards
- Depends on: `rag_async`, `ingest_async`, `models`, `config`
- Used by: Clients via HTTP

**Retrieval Layer:**
- Purpose: Convert a natural-language question into cited answer
- Location: `rag_async.py`
- Contains: `query_async()`, `_retrieve_sources_sync()`, `_call_llm_async()`
- Depends on: `ingest_async.QdrantManager`, `llm_provider.query_llm_async`, `config`
- Used by: `app_fastapi.py` → `POST /api/chat`

**LLM Provider Layer:**
- Purpose: Abstract provider differences behind a single async function
- Location: `llm_provider.py`
- Contains: `query_llm_async()`, `_call_openai/anthropic/ollama/generic()`
- Depends on: `openai`, `anthropic`, `httpx`, `models.decrypt_api_key`
- Used by: `rag_async._call_llm_async()`, `/api/admin/llm-settings/test`

**Ingestion Layer:**
- Purpose: Extract text, chunk, embed, store in Qdrant
- Location: `ingest_async.py` (async orchestration + QdrantManager), `ingest.py` (sync extraction)
- Contains: `QdrantManager`, `run_ingestion_job()`, extraction functions, BFS crawler
- Depends on: `qdrant_client`, `sentence_transformers`, `pdfplumber`, `BeautifulSoup`, `Scrapling`
- Used by: `worker.py` (via RQ), `app_fastapi.py` (inline fallback)

**Data / ORM Layer:**
- Purpose: Persistent relational state for users, documents, jobs, LLM config
- Location: `models.py`
- Contains: SQLAlchemy models, `init_db()`, `encrypt_api_key()`, `decrypt_api_key()`
- Depends on: `sqlalchemy`, `cryptography.fernet`, `config.DATABASE_URL`
- Used by: Every layer that needs to read/write PostgreSQL state

**Config Layer:**
- Purpose: Single source of truth for env vars, paths, constants, prompt template
- Location: `config.py`
- Contains: `CACHE_ROOT`, `DATABASE_URL`, `QDRANT_HOST/PORT`, `REDIS_URL`, `EMBED_MODEL`, `RAG_PROMPT_TEMPLATE`
- Depends on: `python-dotenv`
- Used by: All other modules

## Data Flow

### Ingestion Request (happy path — Redis available)

1. Admin POST `multipart/form-data` to `POST /api/sources` (`app_fastapi.py:374`)
2. File saved async to `{CACHE_ROOT}/uploads/{user_id}_{filename}` via `aiofiles`
3. `Document` row created (`chunks=0`, `qdrant_collection="user_{id}"`) in PostgreSQL
4. `IngestionJob` row created with `status="queued"`
5. `run_ingestion_job` enqueued on Redis `ingestion` queue via RQ; `rq_job_id` saved
6. Response `{"status": "queued", "job_id": N}` returned immediately
7. `rag-worker` (`worker.py`) dequeues → calls `run_ingestion_job()` (`ingest_async.py:161`)
8. Dispatcher calls appropriate extractor in `ingest.py` (PDF → `pdfplumber`; TXT → file read; URL → `Scrapling`/`requests`+`BeautifulSoup`; crawl → BFS)
9. `chunk_text()` (`ingest.py:56`) splits into ~600-token segments with 100-token overlap
10. `SentenceTransformer(BAAI/bge-large-en-v1.5)` embeds all chunks
11. `QdrantManager.upsert_chunks()` (`ingest_async.py:59`) writes 1024-dim vectors to `user_{admin_id}` collection
12. Job returns `chunk_count`; polling `GET /api/sources/jobs/{id}` syncs status from RQ

### Ingestion Fallback (Redis unavailable)

Steps 1–5 identical. Step 6: `asyncio.create_task(run_inline())` executes `run_ingestion_job` via `asyncio.to_thread()` in the FastAPI process. `IngestionJob.status` updated directly in-process.

### Chat Request

1. Authenticated user POST to `POST /api/chat` with `{question, chat_history}` (`app_fastapi.py:325`)
2. `get_admin_user_id(db)` resolves admin's DB id — raises HTTP 503 if no admin exists
3. `query_async(question, user_id=admin_id)` called (`rag_async.py:141`)
4. `_retrieve_sources_sync()` runs in thread pool via `asyncio.to_thread`:
   - `SentenceTransformer` embeds query → 1024-dim vector
   - `QdrantManager.search()` → cosine top-8 from `user_{admin_id}` collection
   - BM25Okapi re-ranks: `score = 0.6 × semantic + 0.4 × bm25`, top-5 kept
5. Sources assembled with `[N]` numbered citations and URL Text Fragment anchors
6. `RAG_PROMPT_TEMPLATE` filled with sources + question (`config.py:86`)
7. `_call_llm_async()` fetches active `LLMProviderConfig` from DB → `query_llm_async()` (`llm_provider.py:22`)
8. Provider-specific call (Ollama `/api/generate`, OpenAI Chat completions, Anthropic Messages, generic OpenAI-compat)
9. Response `{answer, sources, metadata}` returned to client

### Admin LLM Config Update

1. Admin PATCH `POST /api/admin/llm-settings` → `LLMProviderConfig` row upserted in PostgreSQL
2. `api_key` encrypted with Fernet before storage (`models.encrypt_api_key`)
3. Next `query_async` call reads updated row; no restart needed
4. Embed device change (`POST /api/admin/embed-device`) also sets `os.environ["EMBED_DEVICE"]` and nulls `rag_async._embedder` to force reload on next request

**State Management:**
- PostgreSQL: authoritative state (users, documents, jobs, LLM config)
- Qdrant: vector index (per-user collections, `user_{id}`)
- Redis: transient job queue only; job terminal state written back to PostgreSQL
- `rag_async._embedder`: module-level singleton, lazy-loaded, reset on device change

## Key Abstractions

**QdrantManager:**
- Purpose: Scoped Qdrant operations for a single user's collection
- Examples: `ingest_async.py:37`
- Pattern: Instantiated per-operation with `user_id`; auto-creates collection on init if missing

**run_ingestion_job:**
- Purpose: Synchronous, serializable job target for Redis RQ
- Examples: `ingest_async.py:161`
- Pattern: No async; called by worker fork or wrapped in `asyncio.to_thread` for inline fallback

**query_llm_async:**
- Purpose: Single entry point for all LLM calls regardless of provider
- Examples: `llm_provider.py:22`
- Pattern: Config dict (from DB row or env fallback) dispatched to provider-specific private function

**RAG_PROMPT_TEMPLATE:**
- Purpose: System prompt instructing LLM to answer only from numbered source excerpts
- Examples: `config.py:86`
- Pattern: Single `str.format(sources_text=..., question=...)` call in `rag_async.py:202`

## Entry Points

**rag service:**
- Location: `app_fastapi.py` → `app` FastAPI instance
- Triggers: Gunicorn spawns 4 UvicornWorker processes on container start; `startup` event calls `bootstrap_secrets()` then `init_db()`
- Responsibilities: All HTTP traffic, auth, job enqueueing, streaming Ollama pull progress

**rag-worker service:**
- Location: `worker.py`
- Triggers: Docker CMD `python worker.py`
- Responsibilities: Dequeue from Redis `ingestion` queue → run `run_ingestion_job`; reap stale jobs on boot; uses `SimpleWorker` on Windows (no fork), `Worker` on Linux

**secrets_bootstrap:**
- Location: `secrets_bootstrap.py:60` → `bootstrap_secrets()`
- Triggers: Top-level `import` in `app_fastapi.py:41` and `worker.py:17`, before any other module
- Responsibilities: Ensure `JWT_SECRET` and `ENCRYPTION_KEY` in `os.environ`; persist to `{CACHE_ROOT}/.secrets.env` on first boot; survive image pulls while data volume persists

## Architectural Constraints

- **Threading:** FastAPI runs on Uvicorn (async event loop). Sync work (embedding, Qdrant client, PDF extraction) must use `asyncio.to_thread()` — never called directly from async route handlers.
- **Global state:** `rag_async._embedder` is a module-level singleton shared across all requests in a worker process. Reset to `None` on embed device change via admin API.
- **Admin-only collection:** Chat and library APIs always resolve `admin_id` dynamically and query `user_{admin_id}` in Qdrant. Non-admin users can chat but cannot write documents.
- **First-user bootstrap:** First `POST /api/auth/register` sets `is_admin=True` (`app_fastapi.py:289`). No separate admin creation step.
- **Circular imports:** `rag_async.py` imports from `ingest_async.py` (for `QdrantManager`); `ingest_async.py` imports from `ingest.py`; `llm_provider.py` imports from `models.py` for `decrypt_api_key`. No cycles.
- **RQ job serialization:** `run_ingestion_job` is a top-level function in `ingest_async.py`; all arguments must be JSON-serializable primitives.

## Anti-Patterns

### Calling sync Qdrant/embedding code directly in async routes

**What happens:** `QdrantManager` and `SentenceTransformer` are synchronous. Calling them directly in an `async def` route blocks the event loop.
**Why it's wrong:** Blocks all other concurrent requests on the Uvicorn worker.
**Do this instead:** Wrap in `asyncio.to_thread(fn, *args)` as done in `rag_async.py:160` and `app_fastapi.py:595`.

### Bypassing `require_admin` on write endpoints

**What happens:** `POST /api/sources` and `DELETE /api/sources/{id}` require `require_admin`. Adding new write routes with only `get_current_user` would let any authenticated user modify the shared library.
**Why it's wrong:** All users share the admin's Qdrant collection; a rogue write corrupts everyone's chat context.
**Do this instead:** Always use `Depends(require_admin)` for any endpoint that calls `QdrantManager.upsert_chunks` or `delete_document`.

### Hardcoding `ENCRYPTION_KEY` as a placeholder

**What happens:** `models.py` detects placeholder values and generates an ephemeral Fernet key per process.
**Why it's wrong:** Stored API keys (OpenAI, Anthropic) in PostgreSQL become unreadable after any restart.
**Do this instead:** Let `secrets_bootstrap.py` auto-generate and persist a real key on first boot, or set `ENCRYPTION_KEY` explicitly in the host environment.

## Error Handling

**Strategy:** Exceptions propagate to FastAPI exception handlers; `logger.error` before re-raise. Provider-level connection errors caught and re-raised as `RuntimeError` with user-facing messages.

**Patterns:**
- HTTP 401/403: `get_current_user` and `require_admin` dependencies raise `HTTPException` directly
- HTTP 503: `get_admin_user_id` raises if no admin exists; LLM connect errors mapped to 503 in chat route
- HTTP 502: Ollama proxy errors (list/pull/delete model) surface as 502
- Ingestion errors: `job.status = "error"` + `job.error_msg` in PostgreSQL; polled by client
- DB startup: `init_db()` retries up to 60× with 1s delay before raising `RuntimeError`

## Cross-Cutting Concerns

**Logging:** `logging.basicConfig` in each entry-point file; level `DEBUG` when `config.DEBUG=True`, else `INFO`. Logger names match module names. Format: `[LEVEL] name — message`.
**Validation:** Pydantic models for all API request/response bodies; `werkzeug.utils.secure_filename` for uploaded filenames; explicit `doc_type` allowlist check in `add_source`.
**Authentication:** JWT HS256, 30-day expiry, stored in browser `localStorage`. `oauth2_scheme` extracts Bearer token; `get_current_user` dependency validates on every protected route. HTML pages are unauthenticated shells — auth is enforced in the API layer.

---

*Architecture analysis: 2026-06-16*
