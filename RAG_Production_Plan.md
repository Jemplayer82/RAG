# Plan: Production Docker RAG with FastAPI, PostgreSQL, Qdrant

## Context
User has a **single-user RAG** (Flask + ChromaDB + Ollama). Wants a **production-ready multi-user version** in Docker for **50+ concurrent users**.

**Shift from single-user (Flask) to multi-user architecture (FastAPI + async + microservices in Docker).**

Deliverables:
- FastAPI app (async, 4-8 Gunicorn workers)
- PostgreSQL (user accounts + JWT auth)
- Qdrant vector DB (replaces ChromaDB, scales to millions)
- Redis job queue (background PDF ingestion, non-blocking)
- Docker Compose (postgres, qdrant, redis, rag, worker, nginx)
- One-command deployment: `docker-compose up -d`

---

## Architecture

```
docker-compose.yml:
  postgres:5432         ← user auth, job tracking
  qdrant:6333          ← vector DB (namespaced per user)
  redis:6379           ← job queue (RQ)
  rag:8000 (x4 workers) ← FastAPI + Gunicorn
  rag-worker           ← RQ background job processor
  nginx:80/443         ← reverse proxy, HTTPS (optional, prod)
  ollama:11434         ← external (not in compose, already running)
```

---

## Implementation (10-12 hours)

### Step 1: Async RAG Engine (2-3h)
**New file:** `rag_async.py`
- Port logic from `rag.py` → async using `asyncio.to_thread()` for blocking calls
- Keep 60/40 semantic + BM25 retrieval
- Swap ChromaDB → Qdrant client (`qdrant-client` library)
- Per-user namespace support (`user_{user_id}` collections in Qdrant)
- Reuse: ~80% of code from existing `rag.py`

### Step 2: Async Ingestion (2h)
**New file:** `ingest_async.py`
- Port `ingest.py` functions → async: `ingest_pdf_async()`, `ingest_txt_async()`, `ingest_url_async()`
- `QdrantManager` class wraps qdrant-client
- Chunk → embed → upsert to Qdrant (with user namespace isolation)
- Remove all `custom_sources.json` tracking (moved to PostgreSQL)
- Return `job_id` for job queue tracking

### Step 3: FastAPI App (2-3h)
**New file:** `app_fastapi.py` (replaces old Flask `app.py`)
Routes:
- Auth: `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me`
- Chat: `POST /api/chat` (with JWT user isolation)
- Docs: `GET /api/library` (user's own documents only)
- Ingestion: `POST /api/sources` (enqueue job, return immediately), `DELETE /api/sources/{id}`
- Settings: `GET/POST /api/settings`
- Health: `GET /api/health`

Dependencies: FastAPI auth via `get_current_user(jwt_token)` → SQLAlchemy User lookup

### Step 4: Database + ORM (1h)
**New file:** `models.py`
- SQLAlchemy + PostgreSQL
- Tables: `User` (id, username, email, hashed_password), `IngestionJob` (user_id, status, filename), `Document` (user_id, qdrant_collection_id)
- asyncpg for connection pooling

### Step 5: Job Queue Worker (1-2h)
**New file:** `worker.py`
- Redis RQ worker runs in background
- Processes queued ingestion jobs asynchronously
- Reads file from disk → chunks → embeds → stores in Qdrant (with user namespace)
- Updates PostgreSQL job status table
- Runs in separate container (`rag-worker` in compose)

### Step 6: Docker Setup (1-2h)
**New files:**
- `Dockerfile` — FastAPI base image (Python 3.11 + deps)
- `docker-compose.yml` — orchestrates 6 services
- `.env.docker` — dev/prod secrets template
- `nginx.conf` — production reverse proxy (HTTPS, rate limit)
- `.dockerignore`, `.github/workflows/deploy.yml` (optional CI/CD)

Services:
```yaml
postgres:15           — pg_trgm for full-text search, uuid-ossp
qdrant               — vector DB (auto-init collections per user)
redis                — job queue
rag                  — FastAPI app (4 Gunicorn workers, restart policy)
rag-worker           — RQ worker (processes background jobs)
nginx (optional)     — HTTPS reverse proxy
```

### Step 7: Config + Templates (1h)
- Update `config.py`: add Qdrant, PostgreSQL, Redis endpoints
- Copy + adapt existing Flask templates to FastAPI (Jinja2 is same)
- Add login/registration page
- Update chat/library templates for multi-user (show user account, document ownership)

### Step 8: Deployment Guide (1h)
- `DEPLOYMENT.md` — local vs. VPS setup
- `docker-compose.override.yml` (dev tweaks: shared volumes, debug logging)
- Health check scripts
- Environment variable checklist

---

## Files to Create/Modify

| File | Type | Purpose |
|---|---|---|
| `app_fastapi.py` | new | FastAPI app + auth + routes |
| `rag_async.py` | new | Async RAG query engine |
| `ingest_async.py` | new | Async ingestion (PDF/TXT/URL) |
| `worker.py` | new | RQ background job worker |
| `models.py` | new | SQLAlchemy ORM (User, Job, Document) |
| `Dockerfile` | new | FastAPI + Gunicorn container |
| `docker-compose.yml` | new | Full stack orchestration |
| `nginx.conf` | new | Production reverse proxy |
| `config.py` | modify | Add Qdrant, PostgreSQL, Redis URLs |
| `requirements.txt` | modify | Add fastapi, sqlalchemy, redis, qdrant-client, pyjwt, passlib, asyncpg, rq |
| `templates/` | modify | Add login.html, update base/chat/library for FastAPI |

---

## Key Design Decisions

1. **Per-user Qdrant namespaces** — each user gets separate collection → no cross-user data leakage, scales naturally
2. **Redis RQ for async jobs** — simple, no extra infrastructure (unlike Celery)
3. **PostgreSQL user table** — proper auth, session management, audit trail
4. **JWT tokens** — stateless auth, scales horizontally (no session server)
5. **Gunicorn + Uvicorn** — production WSGI with graceful reload + async workers
6. **Qdrant over ChromaDB** — distributed-ready, gRPC API, better namespace/filter support

---

## Deployment Paths

**Local Dev (Docker):**
```bash
docker-compose up -d
# Wait 5s, then:
curl http://localhost:8000/api/health
```

**Production (VPS):**
```bash
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d
# Nginx listens on 443 (HTTPS via Let's Encrypt)
# Logs go to `/var/log/rag/`
```

---

## Verification

1. **Containers spin up:**
   ```bash
   docker-compose ps
   # All 5 services (postgres, qdrant, redis, rag, rag-worker) should be running
   ```

2. **Register user:**
   ```bash
   curl -X POST http://localhost:8000/api/auth/register \
     -H "Content-Type: application/json" \
     -d '{"username":"test","email":"test@test.com","password":"pass123"}'
   ```

3. **Login + get JWT:**
   ```bash
   TOKEN=$(curl -X POST http://localhost:8000/api/auth/login \
     -d "username=test&password=pass123" \
     | jq -r '.access_token')
   ```

4. **Query (with auth):**
   ```bash
   curl -X POST http://localhost:8000/api/chat \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"question":"What is in my documents?"}'
   ```

5. **Upload PDF (background job):**
   ```bash
   curl -X POST http://localhost:8000/api/sources \
     -H "Authorization: Bearer $TOKEN" \
     -F "type=pdf" -F "title=My Doc" -F "file=@example.pdf"
   # Returns job_id immediately
   ```

6. **Multi-user test:**
   - Register user_2
   - Upload different PDF for user_2
   - Verify user_1 library shows only user_1's docs

---

## Time Breakdown
- FastAPI port: 2-3h
- Qdrant + async ingestion: 2h
- PostgreSQL + auth: 1-2h
- Job queue: 1-2h
- Docker setup: 1-2h
- Templates + UI: 1h
- Deployment docs: 1h
- **Total: 10-13 hours**

---

# CURRENT TASK: Remove Model Selection & Hide Model Details

## Context
User wants to simplify the Flask RAG by removing the ability to select LLM models and hiding model details from the UI and logs. The system will use a single hardcoded model (`mistral-small3.1`) without user configuration.

## Changes Required

### Files to Modify (6 files)

1. **rag.py** (Query engine)
   - Line 161: Change `active_model = get_setting("llm_model", LLM_MODEL)` → `active_model = LLM_MODEL` (hardcode)
   - Line 239: Change `active_model = get_setting("llm_model", LLM_MODEL)` → `active_model = LLM_MODEL` (hardcode)
   - Line 240: **Remove** `logger.info(f"Calling {active_model} via Ollama...")` (don't log model name)
   - Lines 248-249: **Remove** `"llm": active_model` from metadata dict
   - Line 213: **Remove** `"llm": LLM_MODEL` from fallback metadata (when no sources found)

2. **app.py** (Flask routes)
   - Lines 401-422: **Remove entirely** `GET /api/ollama/models` endpoint (list available models)
   - Lines 379-380 in `POST /api/settings`: **Remove** model selection handling, keep only `data_dir` updates
   - Line 359 in `GET /api/settings`: **Remove** `"llm_model"` from response dict

3. **templates/settings.html** (Settings UI)
   - Lines 10-48: **Remove entirely** LLM Model card section (UI for model selection)
   - Lines 110-195: **Remove** JavaScript functions: `loadModels()`, save button handler, refresh button handler
   - Keep data_dir section if still needed

4. **templates/base.html** (Navigation)
   - Option A: **Remove** ⚙️ Settings link entirely (if Settings page only had model selection)
   - Option B: **Keep** Settings link if data_dir configuration is still desired

5. **app_fastapi.py** (FastAPI routes, if present)
   - Line 327 in `GET /api/settings`: **Remove** `"llm_model"` from response
   - Lines 340-342 in `POST /api/settings`: **Remove** llm_model update handling

6. **settings.json** (Persistent settings)
   - **Remove** `"llm_model"` line (keep file if other settings exist, or delete if empty)

## Implementation Order

1. **rag.py** — Simplest change, no dependencies (3-5 min)
2. **app.py** — Remove endpoints and response fields (5-10 min)
3. **templates/settings.html** — Remove UI sections (5-10 min)
4. **templates/base.html** — Remove or keep Settings link (2-3 min)
5. **app_fastapi.py** — Same changes as Flask if using FastAPI (3-5 min)
6. **settings.json** — Clean up persisted model selection (1 min)

**Total time: ~30-50 minutes**

## Status: ✅ COMPLETED

**Completion Date:** April 6, 2026

**What was accomplished:**
- ✅ Removed `get_setting()` calls for llm_model from rag.py
- ✅ Hardcoded `LLM_MODEL = mistral-small3.1` in all query calls
- ✅ Removed GET /api/ollama/models endpoint from app.py
- ✅ Removed llm_model from GET/POST /api/settings responses
- ✅ Removed entire LLM Model card from Settings UI (templates/settings.html)
- ✅ Removed model-related JavaScript functions (loadModels, refresh, save handlers)
- ✅ Updated app_fastapi.py to remove model configuration
- ✅ Cleared settings.json to remove persisted llm_model
- ✅ Committed changes to Git: `4a76d27`
- ✅ Pushed to GitHub: https://github.com/Jemplayer82/RAG

**Result:** RAG now uses hardcoded model without UI selection. Settings page only shows data directory configuration.

---

# NEXT TASK: Production Docker Deployment (v2.0)

## Status: PENDING

This plan outlines the migration from single-user Flask RAG to production-ready multi-user FastAPI system in Docker. Ready to begin when user approves.
