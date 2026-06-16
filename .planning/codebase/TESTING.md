# Testing Patterns

**Analysis Date:** 2026-06-16

## Current State: No Test Suite

There is no automated test suite. Confirmed by:

- No `tests/` directory exists in the project root
- No `pytest.ini`, `pyproject.toml` (with pytest config), `setup.cfg`, `conftest.py`, or `vitest.config.*` file
- No test-related entries in `requirements.txt`
- The CI pipeline (`.github/workflows/deploy.yml`) contains zero test steps — it only builds a Docker image and deploys it
- `CLAUDE.md` explicitly states: "**No test suite exists.**"

This is a deliberate current state, not an oversight that was accidentally committed. The project ships via Docker and the developer validates manually.

## Manual Testing (Current Practice)

All verification is done by hand against a running instance.

**Health check:**
```bash
curl http://localhost:8000/api/health
# Expected: {"status": "ok", "version": "2.0.0", "timestamp": "..."}
```

**Interactive API docs:**
```
http://localhost:8000/api/docs
```
Available only when `DEBUG=true` (controlled by the `DEBUG` env var, read in `config.py` and used in `app_fastapi.py` to conditionally expose `docs_url` and `openapi_url`). In production `DEBUG` is unset, so `/api/docs` returns 404.

**Browser UI:** The full Jinja2 template UI at `http://localhost:8000` is tested manually — login, register, upload, chat, library, settings flows.

**Docker stack smoke test:**
```bash
docker compose up -d
docker compose ps          # all 6 containers healthy
docker compose logs -f rag # watch for startup errors
```

## CI/CD Pipeline (No Tests)

`.github/workflows/deploy.yml` triggers on `push` to `master` and runs two jobs:

1. `build-and-push` — builds the Docker image and pushes to `ghcr.io/jemplayer82/rag`
2. `deploy` — SSH into VPS, pulls new image, runs `docker compose up -d`

There is no lint step, no type-check step, no test step between build and deploy. A broken commit goes straight to production if it passes the Docker build.

## What a Future Test Suite Would Target

If tests are added, the framework to reach for is **pytest** with **pytest-asyncio** (all core logic is async). Below are the areas with highest value and lowest setup cost:

**1. Auth logic (`app_fastapi.py`)**
- `hash_password` / `verify_password` round-trip (pure functions, no mocks needed)
- `create_access_token` → `get_current_user` round-trip
- `get_current_user` raises 401 on expired token, missing token, bad token
- `require_admin` raises 403 for non-admin users
- First registered user gets `is_admin=True`

**2. Ingestion pipeline (`ingest.py`)**
- `chunk_text` with inputs shorter than `CHUNK_SIZE`, longer, and spanning paragraphs
- `count_tokens` approximation
- `ingest_pdf` with a fixture PDF (use `pdfplumber` directly — no mocks needed)
- `ingest_txt` with a fixture text file
- `chunk_text` overlap behavior: tail of previous chunk appears at head of next

**3. Retrieval and re-ranking (`rag_async.py`)**
- `_retrieve_sources_sync` with a mock `QdrantManager` (avoid real Qdrant)
- BM25 re-ranking score combination (60% semantic / 40% BM25)
- `query_async` returns the empty-library response when `sources == []`
- Citation construction: `anchor_url` includes `#:~:text=` fragment

**4. Provider dispatch (`llm_provider.py`)**
- `query_llm_async` routes to the correct `_call_*` function based on `provider` key
- `_config_from_env` reads env vars with correct defaults
- Unknown provider raises `ValueError`
- Each `_call_*` function can be tested with `httpx.MockTransport` or `respx`

**5. Models / encryption (`models.py`)**
- `encrypt_api_key` / `decrypt_api_key` round-trip
- `_get_cipher` generates ephemeral key when `ENCRYPTION_KEY` is unset (and logs warning)
- `_get_cipher` raises `RuntimeError` on invalid key format
- `init_db` retries on connection failure (needs a mock engine)

**6. Settings / config (`config.py`)**
- `_resolve_data_dir` priority: `settings.json` wins over env var wins over default
- `CHUNK_SIZE`, `TOP_K`, etc. parse correctly from string env vars

## Recommended Setup (If Adding Tests)

```
# requirements-dev.txt
pytest>=8.0
pytest-asyncio>=0.23
httpx>=0.27          # already in requirements.txt; needed for TestClient
respx>=0.21          # mock httpx calls (LLM provider tests)
pytest-mock>=3.12

# Directory structure
tests/
  conftest.py          # shared fixtures (test DB, test user, mock Qdrant)
  test_auth.py         # auth functions, JWT, get_current_user
  test_ingest.py       # chunk_text, ingest_pdf, ingest_txt
  test_rag.py          # _retrieve_sources_sync, query_async
  test_llm_provider.py # provider dispatch, env fallback
  test_models.py       # encrypt/decrypt, init_db
  fixtures/
    sample.pdf
    sample.txt
```

**FastAPI test client pattern:**
```python
from fastapi.testclient import TestClient
from app_fastapi import app

client = TestClient(app)

def test_health():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
```

**Async test pattern:**
```python
import pytest

@pytest.mark.asyncio
async def test_query_async_no_sources(monkeypatch):
    monkeypatch.setattr("rag_async._retrieve_sources_sync", lambda *a, **kw: [])
    result = await query_async("anything", user_id=1)
    assert result["sources"] == []
    assert "could not find" in result["answer"].lower()
```

## Coverage Gaps (Priority Order)

| Area | Risk if Untested | Files |
|------|-----------------|-------|
| Auth token validation | Silent auth bypass on JWT lib update | `app_fastapi.py` |
| `chunk_text` overlap | Corrupted context windows | `ingest.py` |
| `init_db` retry loop | Silent boot failure in Docker | `models.py` |
| LLM provider dispatch | Wrong provider called silently | `llm_provider.py` |
| `encrypt/decrypt_api_key` | API keys unreadable after key rotation | `models.py` |
| Ingestion job inline fallback | Data loss when Redis is down | `app_fastapi.py` |
| BM25 re-rank scoring | Degraded retrieval quality | `rag_async.py` |

---

*Testing analysis: 2026-06-16*
*Update when a test suite is introduced*
