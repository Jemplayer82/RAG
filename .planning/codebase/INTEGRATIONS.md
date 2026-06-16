# External Integrations

**Analysis Date:** 2026-06-16

## APIs & External Services

**LLM Providers (pluggable — one active at a time):**
- Ollama (self-hosted) — default provider; HTTP to `http://ollama:11434/api/generate`
  - SDK/Client: `httpx` (direct HTTP in `llm_provider.py`), `ollama` 0.6.1 package also present
  - Auth: none by default; optional Bearer token via stored `api_key` field
  - Model selection: admin-configured via `/admin/llm-settings` UI; stored in `LLMProviderConfig` DB table
- OpenAI — cloud provider option
  - SDK/Client: `openai` AsyncOpenAI (`llm_provider.py` → `_call_openai`)
  - Auth: `OPENAI_API_KEY` env var, or Fernet-encrypted key stored in `LLMProviderConfig` DB row
  - Default model: `gpt-4`
- Anthropic — cloud provider option
  - SDK/Client: `anthropic` AsyncAnthropic (`llm_provider.py` → `_call_anthropic`)
  - Auth: `ANTHROPIC_API_KEY` env var, or encrypted key in DB
  - Default model: `claude-3-5-sonnet-20241022`
- Generic OpenAI-compatible — covers Groq, Together, Fireworks, local proxies, etc.
  - SDK/Client: `httpx` direct POST to `config.base_url` (`llm_provider.py` → `_call_generic`)
  - Auth: Bearer token from encrypted DB key or env

**Provider routing logic:** `llm_provider.py` → `query_llm_async()` reads `provider` key from a config dict (loaded from `LLMProviderConfig` DB row or env fallback via `_config_from_env()`).

**Web Scraping (arbitrary user-supplied URLs):**
- Scrapling (`scrapling` 0.2.9+) — primary; uses `Fetcher` for fast requests + anti-bot, falls back to `PlayWrightFetcher` for JS-rendered pages (`ingest.py` → `_extract_text_scrapling`)
- Playwright/Chromium — browser backend for Scrapling JS rendering (installed in Docker image)
- requests + BeautifulSoup4 — fallback scraper when Scrapling fails (`ingest.py` → `_extract_text_requests`)
- protego — `robots.txt` parsing in crawl mode (`ingest.py` → `ingest_crawl`)
- Crawl mode: BFS link-following with configurable `max_depth`, `max_pages`, `same_domain_only`, `respect_robots`

## Data Storage

**Databases:**
- PostgreSQL 15-alpine — primary relational store
  - Docker service: `postgres` (`docker-compose.yml`)
  - Database name: `rag_db`, user: `rag`
  - Connection: `DATABASE_URL` env var (default: `postgresql://rag:{POSTGRES_PASSWORD}@postgres:5432/rag_db`)
  - Client/ORM: SQLAlchemy 2.0 (`models.py`); driver: psycopg2-binary
  - Tables: `User`, `Document`, `IngestionJob`, `LLMProviderConfig`
  - Persistent data path: `${DATA_ROOT:-/storage/rag}/postgres` (host bind mount)

- Qdrant — vector store for embeddings
  - Docker service: `qdrant` (image: `qdrant/qdrant:latest`)
  - Connection: `QDRANT_HOST` + `QDRANT_PORT` (default: `qdrant:6333`)
  - Client: `qdrant-client` 1.9.0 (`ingest_async.py` → `QdrantManager`)
  - Collection naming: `user_{user_id}` — one collection per user, created on first ingest
  - Vector config: 1024 dimensions, cosine distance (BAAI/bge-large-en-v1.5)
  - Persistent data path: `${DATA_ROOT:-/storage/rag}/qdrant` (host bind mount)

**File Storage:**
- Local filesystem — uploaded files stored at `/app/data/raw/uploads/` inside the container
  - Bind-mounted volume: `uploads_data` → `${DATA_ROOT:-/storage/rag}/uploads` on host
  - Shared between `rag` and `rag-worker` containers via named volume

**Caching / Message Broker:**
- Redis 7-alpine — job queue backend for RQ
  - Docker service: `redis`
  - Connection: `REDIS_URL` (default: `redis://redis:6379/0`)
  - Client: `redis` 5.0.0 (`worker.py`)
  - Queue name: `ingestion` (used in `worker.py` and enqueued by `app_fastapi.py`)
  - Persistent data path: `${DATA_ROOT:-/storage/rag}/redis` (host bind mount)

## Authentication & Identity

**Auth Provider:**
- Custom implementation (no third-party auth service)
  - Password hashing: `passlib` + `bcrypt` 4.1.3 (`models.py`)
  - Session tokens: JWT signed with `JWT_SECRET` via `pyjwt` 2.8.0
  - JWT_SECRET: auto-generated on first boot by `secrets_bootstrap.py`, persisted to `/app/data/.secrets.env`

**API Key Storage:**
- LLM provider API keys stored encrypted in the `LLMProviderConfig` DB table
  - Encryption: Fernet symmetric encryption (`cryptography` 42.0+, `models.py` → `_get_cipher()`)
  - Encryption key: `ENCRYPTION_KEY` env var; auto-generated on first boot by `secrets_bootstrap.py`
  - Risk: ephemeral key is generated if `ENCRYPTION_KEY` is unset; keys become unreadable after restart

## Monitoring & Observability

**Error Tracking:**
- None detected (no Sentry, Datadog, etc.)

**Logs:**
- Python `logging` module throughout; format: `[LEVEL] timestamp name — message`
- Log level: `INFO` default; `DEBUG` mode via `DEBUG=true` env var
- Worker boot reaps stale `IngestionJob` rows (queued/running) from previous crashed workers

**Healthchecks:**
- `rag` service: `GET http://localhost:8000/api/health` every 30s (`Dockerfile` HEALTHCHECK)
- `rag-worker` service: `rq info` CLI check every 30s (`docker-compose.yml`)
- `postgres`: `pg_isready -U rag` every 10s
- `redis`: `redis-cli ping` every 10s

## CI/CD & Deployment

**Container Registry:**
- GitHub Container Registry (GHCR) — image: `ghcr.io/jemplayer82/rag:latest`
  - Auth: `GITHUB_TOKEN` secret (automatic in Actions)
  - Tags pushed: `latest` and `{git-sha}`

**CI Pipeline:**
- GitHub Actions — `.github/workflows/deploy.yml`
- Trigger: push to `master` branch, or manual `workflow_dispatch`
- Jobs:
  1. `build-and-push` — runs on `ubuntu-latest`; builds Docker image, pushes to GHCR
  2. `deploy` — runs on self-hosted runner tagged `[self-hosted, webserver]`; pulls new image, runs `docker compose up -d --no-deps rag rag-worker`, prunes old images
- Deploy path on host: `/opt/rag`

**Hosting:**
- Self-hosted Linux server
- Portainer-compatible (stack defined as docker-compose; env vars set via Portainer GUI as alternative to `.env` file)
- No cloud provider detected

## Webhooks & Callbacks

**Incoming:**
- None detected (no webhook receiver endpoints)

**Outgoing:**
- LLM API calls (OpenAI, Anthropic, Ollama, generic) — initiated per user query
- Web scraping HTTP requests — initiated per user-submitted URL/crawl

## Environment Configuration

**Required env vars (must be set before first boot):**
- `POSTGRES_PASSWORD` — no default, startup will fail without it

**Auto-generated on first boot (persisted to `/app/data/.secrets.env`):**
- `JWT_SECRET` — 64-char hex token
- `ENCRYPTION_KEY` — Fernet key (base64-encoded 32 bytes)

**Optional but important:**
- `LLM_PROVIDER` (default: `ollama`)
- `LLM_MODEL` — must be set via admin UI or env; no default model
- `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` — only for cloud providers
- `EMBED_DEVICE` — set to `cuda` or `rocm` for GPU acceleration
- `DATA_ROOT` — host path for all bind-mounted volumes (default: `/storage/rag`)
- `RAG_PORT` — host port for web UI (default: `8000`)

**Secrets location:**
- `.env` file for local dev (gitignored); Portainer env vars GUI for production
- Auto-generated secrets persist in `/app/data/.secrets.env` inside the bind-mounted `uploads_data` volume

---

*Integration audit: 2026-06-16*
