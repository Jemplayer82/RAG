# Technology Stack

**Analysis Date:** 2026-06-16

## Languages

**Primary:**
- Python 3.11 - Application runtime inside Docker container (`Dockerfile`)
- Python 3.13 - Host development environment (inferred; Docker target is 3.11)

**Secondary:**
- HTML/CSS/JS - Jinja2-rendered frontend templates (`templates/`, `static/`)

## Runtime

**Environment:**
- CPython 3.11-slim (Docker base image: `python:3.11-slim`)

**Package Manager:**
- pip (no Poetry/PDM)
- Lockfile: absent — `requirements.txt` pins most packages by exact version; some use `>=`

## Frameworks

**Core:**
- FastAPI 0.115.0 — ASGI web framework, all HTTP endpoints (`app_fastapi.py`)
- Uvicorn 0.30.0 — ASGI server (worker class only, not standalone)
- Gunicorn 22.0.0 — Production process manager; spawns 4 UvicornWorker processes (`Dockerfile` CMD)

**Background Jobs:**
- RQ (Redis Queue) 1.16.0 — document ingestion job queue (`worker.py`)
  - Queue name: `ingestion`
  - Uses `Worker` on Linux/Docker, `SimpleWorker` on Windows (no-fork fallback)

**Data / ORM:**
- SQLAlchemy 2.0.36+ — ORM for all relational models (`models.py`)
- Pydantic 2.9+ — request/response validation (FastAPI integration)

**Testing:**
- Not detected

**Build/Dev:**
- python-dotenv 1.0.1 — `.env` loading (`config.py`)
- Werkzeug 3.0+ — utilities (password hashing helpers, used by passlib integration)

## Key Dependencies

**Embeddings:**
- sentence-transformers 3.0.1 — local embedding model inference (`ingest_async.py`, `rag_async.py`)
  - Default model: `BAAI/bge-large-en-v1.5` (1024-dimensional vectors, cosine distance)
  - Device: configurable via `EMBED_DEVICE` (default: `cpu`; supports `cuda`, `rocm`)

**Vector Store:**
- qdrant-client 1.9.0 — Qdrant vector database client (`ingest_async.py`)
  - Per-user collections named `user_{user_id}`
  - Collection config: 1024-dim, cosine distance

**Retrieval / Re-ranking:**
- rank-bm25 0.2.2 — BM25 lexical re-ranking applied after vector search (`rag_async.py`)

**LLM SDKs:**
- openai 1.0.0+ — OpenAI API client, loaded lazily in `llm_provider.py`
- anthropic 0.25.0+ — Anthropic API client, loaded lazily in `llm_provider.py`
- ollama 0.6.1 — Ollama SDK (present in requirements; HTTP calls use httpx directly in `llm_provider.py`)
- httpx 0.27.0 — async HTTP client for Ollama and generic provider calls

**Document Parsing:**
- pdfplumber 0.10.3 — PDF text extraction (`ingest.py` → `ingest_pdf`)
- python-docx 1.1.0+ — `.docx` extraction (`ingest.py` → `ingest_docx`)
- antiword (system binary, installed via `apt-get`) — legacy `.doc` extraction (`ingest.py` → `ingest_doc`)

**Web Scraping:**
- scrapling 0.2.9+ — primary scraper: JS rendering + anti-bot via `Fetcher` / `PlayWrightFetcher` (`ingest.py`)
- playwright (via `python -m playwright install chromium`) — browser automation backend for Scrapling
- chromium + chromium-driver (apt) — headless browser installed in Docker image
- requests 2.31.0 + beautifulsoup4 4.12.2 — fallback scraper for plain HTML pages (`ingest.py`)
- protego 0.4.0+ — `robots.txt` parsing for crawl mode (`ingest.py` → `ingest_crawl`)
- aiofiles 23.2.1 — async file I/O

**Database Driver:**
- psycopg2-binary 2.9.10+ — PostgreSQL driver for SQLAlchemy

**Message Broker:**
- redis 5.0.0 — Redis client (`worker.py`)

**Security / Auth:**
- cryptography 42.0.0+ — Fernet symmetric encryption for stored API keys (`models.py`)
- passlib 1.7.4 + bcrypt 4.1.3 — password hashing
- pyjwt 2.8.0 — JWT token signing/verification
- python-multipart 0.0.9 — multipart form upload support (FastAPI dependency)

**Numerical:**
- numpy <2.0 — required by sentence-transformers; pinned below 2.0 for compatibility

## Configuration

**Environment:**
- Primary source: environment variables (Docker Compose `environment:` block or `.env` file)
- Settings loaded in `config.py` via `python-dotenv`; also overridable via `settings.json` for the data-dir path
- First-boot secret auto-generation: `secrets_bootstrap.py` writes `JWT_SECRET` and `ENCRYPTION_KEY` to `/app/data/.secrets.env` in the bind-mounted volume if not supplied

**Key env vars:**
- `DATABASE_URL` — PostgreSQL connection string
- `QDRANT_HOST` / `QDRANT_PORT` — Qdrant location (default: `qdrant:6333`)
- `REDIS_URL` — Redis connection URL (default: `redis://redis:6379/0`)
- `POSTGRES_PASSWORD` — required; no default
- `JWT_SECRET` / `ENCRYPTION_KEY` — auto-generated on first boot if blank
- `LLM_PROVIDER` — `ollama` | `openai` | `anthropic` | `generic` (default: `ollama`)
- `LLM_MODEL` / `LLM_BASE_URL` / `LLM_TEMPERATURE` / `LLM_TOP_P` / `LLM_MAX_TOKENS`
- `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` — only needed for cloud providers
- `EMBED_MODEL` (default: `BAAI/bge-large-en-v1.5`) / `EMBED_DEVICE` (default: `cpu`)
- `CHUNK_SIZE` (default: 600) / `CHUNK_OVERLAP` (default: 100) / `TOP_K` (8) / `RERANK_TOP_K` (5)
- `RAG_PORT` (default: 8000) / `DEBUG` (default: false) / `DATA_ROOT` (default: `/storage/rag`)

**Build:**
- `Dockerfile` — single-stage, `python:3.11-slim`, installs apt deps (gcc, libpq-dev, antiword, chromium), then pip, then runs `playwright install chromium`
- `docker-compose.yml` — five services: `ollama`, `postgres`, `qdrant`, `redis`, `rag`, `rag-worker`
- `docker-compose.override.yml` — local dev overrides

## Platform Requirements

**Development:**
- Python 3.11+ (Docker) or 3.13 (host)
- Docker + Docker Compose (all services containerized)
- On Windows: `SimpleWorker` used instead of `Worker` (no `os.fork`)

**Production:**
- Self-hosted Linux server with Docker
- Bind-mounted host directories under `DATA_ROOT` (default `/storage/rag`) for all persistent data
- Gunicorn with 4 UvicornWorker processes; port 8000
- Healthcheck: `GET /api/health` every 30s

---

*Stack analysis: 2026-06-16*
