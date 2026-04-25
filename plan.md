# RAG Site — End-to-End Verification & Debug Runbook

## Context

There are no automated tests in this repo (confirmed: no `tests/`, `test_*.py`, `conftest.py`, Makefile, or shell scripts beyond `docker-compose.yml` and `nginx.conf`). The only diagnostic endpoint is `/api/health`. The user has just landed three ingestion fixes on `claude/fix-data-ingestion-Yw96r` (PR #1) and wants a repeatable way to confirm every feature works and to localise any failure.

This runbook walks the full stack top-to-bottom (services → auth → ingest → library → query → admin) with a pass/fail signal and a debug pointer for every step. Each step lists **what to run**, **what you should see**, and **where to look if it breaks** (log source + likely file/line). No code changes are required for the runbook itself — it's executed against a running deployment. Step 10 captures issues surfaced during mapping that are worth fixing after the runbook is proven.

---

## 1. Preflight — services are up

Run from the host:

```bash
docker compose ps                       # all five services "Up / healthy"
curl -s localhost:8001/api/health       # {"status":"ok",...}
docker compose exec postgres pg_isready -U rag
docker compose exec redis redis-cli ping           # PONG
curl -s localhost:6333/collections                 # Qdrant {"status":"ok",...}
curl -s $OLLAMA_BASE_URL/api/tags                  # or skip if using OpenAI/Anthropic
```

- **Pass**: all commands succeed; `docker compose ps` shows `postgres`, `qdrant`, `redis`, `rag`, `rag-worker`.
- **Fail → debug**:
  - `rag` unhealthy → `docker compose logs rag` (startup: `app_fastapi.py:753` `startup()`).
  - `rag-worker` restarting → `docker compose logs rag-worker` (`worker.py:34`). Most common cause: Redis not reachable (`REDIS_URL`).
  - Qdrant 404 → collection not created yet; that's normal pre-ingest.
  - Ollama 000/connection refused → check `OLLAMA_BASE_URL` in `.env`; docker uses `host.docker.internal` (`docker-compose.yml:44`).

## 2. Auth smoke test

```bash
# First user becomes admin (app_fastapi.py:284)
curl -s -X POST localhost:8001/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","email":"a@b.c","password":"pw"}'
# → {...,"is_admin":true,...}

TOKEN=$(curl -s -X POST localhost:8001/api/auth/login \
  -d 'username=admin&password=pw' | jq -r .access_token)

curl -s localhost:8001/api/auth/me -H "Authorization: Bearer $TOKEN"
# → is_admin:true
```

- **Fail → debug**: `500` on register = Postgres/init_db; `401` on login = wrong password or hash mismatch; `is_admin:false` on first user = there was already a user row (`app_fastapi.py:284`).

## 3. Ingestion smoke tests — one per input type

Use the UI at `/upload` (logged in as admin) OR `curl`. For each type, watch both the API and `rag-worker` logs:

```bash
docker compose logs -f rag rag-worker
```

### 3a. PDF upload
```bash
curl -s -X POST localhost:8001/api/sources \
  -H "Authorization: Bearer $TOKEN" \
  -F type=pdf -F title=smoke-pdf -F file=@sample.pdf
# → {"status":"queued","job_id":N,"document_id":M,...}
```
Poll: `curl -s localhost:8001/api/sources/jobs/$JOB_ID -H "Authorization: Bearer $TOKEN"`
- **Pass**: job transitions `queued → running → complete`; worker log shows `[PDF] … pages → N chunks` then `[QDRANT] Upserted N chunks`.
- **Fail → debug**:
  - `422` → form field mismatch (`app_fastapi.py:369` signature).
  - Stuck on `queued` → worker not consuming; check `rag-worker` logs. If Redis is down the inline fallback runs (`app_fastapi.py:453`, now uses its own DB session after the recent fix).
  - `error` with `ValueError: Failed to read PDF` → `ingest.py:163`; scanned/image PDF with no extractable text.
  - `error` with no detail → UI previously dropped the field; confirm poll returns the message and the UI reads `job.error` (`app_fastapi.py:528`, `templates/upload.html:315`).

### 3b. TXT / DOCX / DOC
Same flow. DOC requires the `antiword` binary which the Dockerfile installs (`Dockerfile:8`); outside Docker it will raise `RuntimeError: antiword is not installed` (`ingest.py:231`).

### 3c. URL
```bash
curl -s -X POST localhost:8001/api/sources \
  -H "Authorization: Bearer $TOKEN" \
  -F type=url -F title=smoke-url -F url=https://example.com
```
- **Pass**: worker log shows `[URL] Scrapling extracted N chars` or `requests fallback extracted N chars`, then chunks upserted.
- **Fail → debug**:
  - `422` → the form is posting JSON instead of multipart (fixed in this branch at `templates/upload.html:234`).
  - `Insufficient text extracted` → `ingest.py:306`; site blocks bots or needs JS; Playwright path in `ingest.py:271` should take over.
  - Playwright failing to launch → check `chromium` install in `Dockerfile:10-13` and `playwright install` line.

## 4. Library & listing verification

- `/upload` → **Manage Sources** tab: should list the docs just ingested. This hits `/api/library` client-side.
- `/library` page: after the fix in this branch (`templates/library.html`), it also fetches `/api/library` on load. Should show the same rows plus Documents / Chunks / Last-Indexed stats.
- `DELETE /api/sources/{id}` (via Remove button): row vanishes from both places and the Qdrant collection (`app_fastapi.py:534`, `ingest_async.py:84`).

- **Fail → debug**:
  - Empty Library but non-empty Manage Sources → library fetch not wired (pre-fix state); hard-refresh to clear cached HTML.
  - Docs show with `chunks=0` → ingestion failed but Document row still exists; the list filters them out (`templates/upload.html:376`, `templates/library.html` loadLibrary).

## 5. Query smoke test

```bash
curl -s -X POST localhost:8001/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"question":"What is in my documents?","chat_history":[]}'
```
- **Pass**: returns `{answer, sources:[{index,citation,doc_type,url}], metadata:{retrieval_count>0}}`. Each source cites something ingested in step 3.
- **Fail → debug**:
  - `503 "No admin account configured yet"` → no admin user (`app_fastapi.py:147`).
  - `answer:"I could not find relevant documents..."` → retrieval empty; check Qdrant has vectors: `curl -s localhost:6333/collections/user_1/points/count -X POST -H 'Content-Type: application/json' -d '{"exact":true}'`. Also confirm the embedder loaded on the expected device (`rag_async.py:53`).
  - `503 "Cannot connect to LLM"` → provider down; go to step 7.
  - LLM returns garbage / hallucinates — check `RAG_PROMPT_TEMPLATE` (`config.py:85`) and that sources actually contain the answer (inspect `sources_text` by logging at `rag_async.py:189`).

## 6. Settings & admin

- `GET /api/settings` with admin token → user profile JSON.
- `POST /api/admin/llm-settings` from `/admin/llm-settings` UI → update provider/model/keys. Encrypted with Fernet (`models.py:30`); `ENCRYPTION_KEY` must be set in `.env`.
- `POST /api/admin/llm-settings/test` (`app_fastapi.py:706`) → round-trips a prompt to the configured provider. This is the fastest LLM connectivity probe.
- `POST /api/admin/embed-device` with `{"embed_device":"cuda"}` → resets the lazy embedder (`rag_async._embedder = None` at `app_fastapi.py:688`) so the next query reloads on the new device. **Note**: this only affects the `rag` process — the `rag-worker` still uses the `EMBED_DEVICE` env var (`ingest_async.py:194`). If you change the device you must recreate `rag-worker` for ingestion to follow.

## 7. LLM connectivity isolation

If chat fails but retrieval works, hit `/api/admin/llm-settings/test` to isolate provider/network from prompt logic. Providers live in `llm_provider.py`:
- Ollama: `_call_ollama` at line 84. Check `base_url` reachable from inside the `rag` container (`docker compose exec rag curl -s $OLLAMA_BASE_URL/api/tags`).
- OpenAI/Anthropic/generic: `_call_openai:49`, `_call_anthropic:67`, `_call_generic:103`. API key decryption at `models.py:34`; a mismatched `ENCRYPTION_KEY` on restart will make all existing keys undecryptable — rotate keys via the UI.

## 8. Edge cases to exercise

- Upload an unsupported extension (e.g. `.md`) → UI rejects before POST (`templates/upload.html:139`).
- Upload a PDF with no extractable text (image-only) → job goes to `error` with `No content extracted` (`ingest_async.py:190`).
- Submit a URL that 403s → worker log shows Scrapling failure then `requests` fallback failure; job `error` with real message now that `job.error` is read correctly.
- Stop `rag-worker` (`docker compose stop rag-worker`), upload a file — job should stay `queued` (RQ path) **or** run inline (if Redis enqueue itself fails; see `app_fastapi.py:431`). The inline path now completes correctly with its own DB session.
- Delete a doc while its ingestion job is still running → Document row disappears; RQ job may still try to upsert to Qdrant, then the next Library load won't show it. Not a correctness bug but worth noting.

## 9. Debug reference — where each signal surfaces

| Symptom | First log to check | Then |
|---|---|---|
| Upload 422 | `rag` | Compare form fields with `app_fastapi.py:369` |
| Job stuck `queued` | `rag-worker` | Is worker alive? Is Redis queue named `ingestion`? (`worker.py:39`) |
| Job `error`, no message | `rag` (poll response) | Fixed; verify UI reads `job.error` |
| Chat: "could not find" | `rag` | Qdrant point count, embedder device mismatch |
| Chat: 503 LLM | `rag` | `/api/admin/llm-settings/test` → `llm_provider.py` |
| Library empty after upload | Browser devtools | `/api/library` response; fixed on this branch |
| DB connection error | `rag` startup | `DATABASE_URL` env, postgres healthy |
| Embedder slow/OOM | `rag` | `embed_device` in `LLMProviderConfig`; GPU visible? |

## 10. Issues surfaced by mapping (NOT fixed in this plan — propose follow-ups)

- **Dead Re-index button** on `/library` (`templates/library.html` reindex modal) POSTs to `/reindex` and polls `/reindex-progress`. Neither endpoint exists in `app_fastapi.py`. Either implement them (wrap `build_index()` from `ingest.py:402` for the admin's docs) or remove the button. Follow-up after runbook validates.
- **Worker device drift**: changing the embed device via the admin UI only resets the main process embedder (`app_fastapi.py:687-689`). `rag-worker` still reads `EMBED_DEVICE` at import time (`ingest_async.py:194`). Either persist and re-read per job, or document that users must `docker compose restart rag-worker`.
- **No streaming chat**: `/api/chat` is one-shot (`app_fastapi.py:320`, `templates/chat.html:90`). Fine, but worth flagging for UX.
- **`chat_history` is accepted but unused** in `rag_async.query_async` (`rag_async.py:144`). Either plumb it into the prompt or drop the param.

## Verification — how to run this runbook

1. Deploy the current branch: `docker compose up -d --build`.
2. Tail logs in a second terminal: `docker compose logs -f rag rag-worker`.
3. Walk through sections 1–8 in order. At each step check the **Pass** criterion; on failure go to its **debug** pointer before proceeding — several steps assume the previous one passed (e.g. step 5 needs step 3 to have ingested something).
4. If everything passes, merge PR #1 and open follow-up issues for items in section 10.
5. If a step fails in a way the debug pointer doesn't cover, capture the exact log lines and file/line from this doc, then decide: code fix (new branch off master), config fix (`.env` / `docker-compose.yml`), or doc update.

No code changes will be made as part of this plan — all fixes will land in separate branches scoped to the specific issue the runbook surfaces.
