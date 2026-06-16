# Codebase Structure

**Analysis Date:** 2026-06-16

## Directory Layout

```
RAG/                              # Project root
├── app_fastapi.py                # FastAPI app — all routes, auth, schemas, startup
├── models.py                     # SQLAlchemy ORM + Fernet encryption + init_db
├── config.py                     # Env vars, paths, constants, RAG_PROMPT_TEMPLATE
├── rag_async.py                  # Retrieval: Qdrant semantic search + BM25 rerank
├── llm_provider.py               # LLM dispatch: OpenAI / Anthropic / Ollama / generic
├── ingest_async.py               # QdrantManager + run_ingestion_job (RQ target)
├── ingest.py                     # Text extraction: PDF/TXT/DOCX/DOC/URL + BFS crawler
├── worker.py                     # Redis RQ worker entrypoint
├── secrets_bootstrap.py          # Auto-generate/persist JWT_SECRET + ENCRYPTION_KEY
├── settings.py                   # JSON-backed runtime settings (data_dir, llm_model)
├── requirements.txt              # Python dependencies
├── Dockerfile                    # python:3.11-slim, antiword, Chromium, Gunicorn CMD
├── docker-compose.yml            # 6-service stack definition (production/Portainer)
├── docker-compose.override.yml   # Local build override (build: . instead of pull)
├── templates/                    # Jinja2 HTML templates
│   ├── base.html                 # Base layout — nav, CSS/JS links
│   ├── chat.html                 # Chat UI
│   ├── library.html              # Document library view
│   ├── upload.html               # Source upload / URL add form
│   ├── settings.html             # User settings page
│   ├── login.html                # Login form
│   ├── register.html             # Registration form
│   └── admin_llm_settings.html   # Admin LLM provider config + Ollama model manager
├── static/
│   └── style.css                 # Application stylesheet
├── data/                         # Runtime data (bind-mounted volume; gitignored)
│   ├── uploads/                  # Uploaded files: {user_id}_{filename}
│   ├── postgres/                 # PostgreSQL data files
│   ├── qdrant/                   # Qdrant vector storage
│   ├── redis/                    # Redis persistence
│   └── ollama/                   # Ollama model blobs and manifests
├── .planning/
│   └── codebase/                 # GSD codebase map documents (ARCHITECTURE.md, etc.)
├── .claude/                      # Claude Code project configuration
├── .github/
│   └── workflows/                # CI/CD (GitHub Actions)
└── settings.json                 # Runtime overrides written by settings.py (gitignored)
```

## Directory Purposes

**Root Python files:**
- Purpose: All application logic lives directly in the project root — no `src/` subdirectory
- Contains: One file per major concern (routes, models, config, rag, llm, ingest, worker)
- Key files: `app_fastapi.py`, `rag_async.py`, `ingest_async.py`, `llm_provider.py`

**`templates/`:**
- Purpose: Jinja2 server-rendered HTML shells; auth and data loading done client-side via fetch + JWT
- Contains: 8 `.html` files; all extend `base.html`
- Key files: `templates/chat.html`, `templates/upload.html`, `templates/admin_llm_settings.html`

**`static/`:**
- Purpose: Served at `/static/`; mounted via `app.mount("/static", StaticFiles(directory="static"))`
- Contains: `style.css` only
- Key files: `static/style.css`

**`data/`:**
- Purpose: Runtime persistent data; bind-mounted into both `rag` and `rag-worker` containers at `/app/data`
- Contains: PostgreSQL files, Qdrant storage, Redis dump, Ollama models, uploaded documents, `.secrets.env`
- Generated: Yes (at runtime)
- Committed: No (gitignored)

**`.planning/codebase/`:**
- Purpose: GSD codebase map documents consumed by `/gsd:plan-phase` and `/gsd:execute-phase`
- Generated: Yes (by `/gsd:map-codebase`)
- Committed: Yes

## Key File Locations

**Entry Points:**
- `app_fastapi.py`: FastAPI `app` object; Gunicorn target `app_fastapi:app`; startup calls `bootstrap_secrets()` then `init_db()`
- `worker.py`: RQ worker entrypoint; `python worker.py`; startup calls `bootstrap_secrets()` then `reap_stale_jobs()`

**Configuration:**
- `config.py`: All env var reads and derived constants; import first in every module
- `docker-compose.yml`: Service definitions, port mappings, volume mounts, env var injection
- `docker-compose.override.yml`: Local dev build override (auto-applied by `docker compose up`)
- `settings.json`: Runtime JSON override written by `settings.py` (not committed)
- `data/.secrets.env`: Auto-generated Fernet/JWT secrets persisted across restarts (not committed)

**Core Logic:**
- `rag_async.py:141`: `query_async()` — main chat pipeline entry point
- `rag_async.py:64`: `_retrieve_sources_sync()` — Qdrant search + BM25 rerank
- `ingest_async.py:37`: `QdrantManager` class — all Qdrant operations scoped to a user
- `ingest_async.py:161`: `run_ingestion_job()` — RQ job target; synchronous
- `ingest.py:56`: `chunk_text()` — paragraph-split → 600-token chunks with 100-token overlap
- `ingest.py:148`: `ingest_pdf()` — pdfplumber extraction with `[Page N]` markers
- `ingest.py:292`: `ingest_url()` — Scrapling primary / requests+BeautifulSoup fallback
- `ingest.py:556`: `ingest_crawl()` — BFS web crawler with robots.txt and same-domain options
- `llm_provider.py:22`: `query_llm_async()` — provider dispatch entry point
- `models.py:70`: `encrypt_api_key()` / `decrypt_api_key()` — Fernet round-trip
- `models.py:158`: `init_db()` — table creation with 60-retry startup loop
- `secrets_bootstrap.py:60`: `bootstrap_secrets()` — first-boot secret generation

**Auth:**
- `app_fastapi.py:123`: `get_current_user()` — JWT decode → DB lookup → User
- `app_fastapi.py:146`: `require_admin()` — wraps `get_current_user`, checks `user.is_admin`
- `app_fastapi.py:152`: `get_admin_user_id()` — resolves admin's DB id for shared-collection queries
- `app_fastapi.py:281`: `POST /api/auth/register` — first user auto-admin logic

**Testing:**
- No test files detected in the repository.

## Naming Conventions

**Files:**
- Snake_case Python modules: `app_fastapi.py`, `rag_async.py`, `ingest_async.py`, `llm_provider.py`
- Suffix `_async` indicates async orchestration layer over a sync counterpart (`ingest_async.py` wraps `ingest.py`)

**Functions:**
- Public async: `query_async`, `ingest_pdf_async`, `run_ingestion_job`
- Private (module-internal): leading underscore: `_retrieve_sources_sync`, `_call_openai`, `_get_embedder`, `_resolve_data_dir`
- Route handlers: named after the HTTP action: `chat`, `register`, `login`, `add_source`, `delete_source`

**Variables/constants:**
- Module-level constants: UPPER_SNAKE_CASE (`EMBED_MODEL`, `CACHE_ROOT`, `TOP_K`)
- Module-level singletons: leading underscore + lowercase (`_embedder`, `_cipher`)

**Templates:**
- Lowercase with underscores matching route names: `chat.html`, `upload.html`, `admin_llm_settings.html`

**Qdrant collections:**
- `user_{user_id}` (e.g. `user_1`, `user_3`) — scoped per user ID

**Uploaded files:**
- `{user_id}_{secure_filename}` stored under `{CACHE_ROOT}/uploads/`

## Where to Add New Code

**New API route:**
- Handler: `app_fastapi.py` — add after the relevant section comment block
- Schema: Define Pydantic `BaseModel` in `app_fastapi.py` near the other schemas (lines 176–217)
- Auth: Use `Depends(get_current_user)` for authenticated, `Depends(require_admin)` for admin-only
- HTML page: add `.html` to `templates/`, extend `base.html`, add `GET` route returning `TemplateResponse`

**New LLM provider:**
- Add `async def _call_{provider}(prompt, config)` in `llm_provider.py`
- Add `elif provider == "{provider}":` branch in `query_llm_async()` (`llm_provider.py:33`)
- Update `LLMSettingsUpdate.provider` validation in `app_fastapi.py:203` if needed

**New document type:**
- Add extractor `ingest_{type}(file_path, title, url_hint)` in `ingest.py` following the existing pattern
- Import in `ingest_async.py:25` alongside existing extractors
- Add `elif doc_type == "{type}":` branch in `run_ingestion_job()` (`ingest_async.py:182`)
- Add type to the allowlist check in `add_source` (`app_fastapi.py:401`)

**New background job type:**
- Write a top-level synchronous function (picklable by RQ) in `ingest_async.py` or a new module
- Enqueue via `Queue("ingestion", connection=get_redis()).enqueue(fn, kwargs={...})`
- Create a corresponding tracking model in `models.py` if job status polling is needed

**New admin setting (DB-persisted):**
- Add column to `LLMProviderConfig` in `models.py:128`
- Expose via `GET/POST /api/admin/llm-settings` handlers in `app_fastapi.py:645–698`
- Update `LLMSettingsUpdate` Pydantic schema in `app_fastapi.py:203`

**New static asset:**
- Place in `static/` and reference as `/static/{filename}` in templates or `base.html`

## Special Directories

**`data/`:**
- Purpose: All runtime persistence — uploads, DB files, vector store, model weights, secrets
- Generated: Yes (by Docker volume mounts and app startup)
- Committed: No — entire directory is gitignored; host path is `${DATA_ROOT:-/storage/rag}/*`

**`data/uploads/`:**
- Purpose: Raw uploaded files saved by `POST /api/sources` before ingestion
- File format: `{user_id}_{secure_filename}` (e.g. `1_report.pdf`)
- Shared: Mounted into both `rag` and `rag-worker` containers via `uploads_data` volume

**`data/.secrets.env`:**
- Purpose: Persisted auto-generated `JWT_SECRET` and `ENCRYPTION_KEY`
- Generated: By `secrets_bootstrap.py` on first boot if values absent
- Committed: No — must survive data volume but never enter source control

**`.planning/codebase/`:**
- Purpose: GSD architecture documents for planning and execution agents
- Generated: By `/gsd:map-codebase`
- Committed: Yes

---

*Structure analysis: 2026-06-16*
