# RAG Site — End-to-End Verification & Debug Runbook

## Context

There are no automated tests in this repo (confirmed: no `tests/`, `test_*.py`, `conftest.py`, Makefile, or shell scripts beyond `docker-compose.yml` and `nginx.conf`). The only diagnostic endpoint is `/api/health`. This runbook is a repeatable way to confirm every feature works and to localise any failure.

It walks the full stack top-to-bottom (services → auth → ingest → library → query → admin) with a pass/fail signal and a debug pointer for every step. Each step lists **what to run**, **what you should see**, and **where to look if it breaks** (log source + likely file/line). No code changes are required for the runbook itself — it's executed against a running deployment. Step 10 captures issues surfaced during mapping that are worth fixing after the runbook is proven.

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
  - `rag` unhealthy → `docker compose logs rag` (startup: `app_fastapi.py` `startup()`).
  - `rag-worker` restarting → `docker compose logs rag-worker` (`worker.py`). Most common cause: Redis not reachable (`REDIS_URL`).
  - Qdrant 404 → collection not created yet; that's normal pre-ingest.
  - Ollama 000/connection refused → check `OLLAMA_BASE_URL` in `.env`; docker uses `host.docker.internal`.

## 2. Auth smoke test

```bash
# First user becomes admin (unless ADMIN_USERNAME pins it)
curl -s -X POST localhost:8001/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","email":"a@b.c","password":"pw"}'
# → {...,"is_admin":true,...}

TOKEN=$(curl -s -X POST localhost:8001/api/auth/login \
  -d 'username=admin&password=pw' | jq -r .access_token)

curl -s localhost:8001/api/auth/me -H "Authorization: Bearer $TOKEN"
# → is_admin:true
```

- **Fail → debug**: `500` on register = Postgres/init_db; `401` on login = wrong password or hash mismatch; `is_admin:false` on first user = there was already a user row, or `ADMIN_USERNAME` is set and points elsewhere.

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
  - `422` → form field mismatch in `/api/sources` (`app_fastapi.py` add_source signature).
  - Stuck on `queued` → worker not consuming; check `rag-worker` logs. If Redis is down the inline fallback runs.
  - `error` with `ValueError: Failed to read PDF` → `ingest.py` ingest_pdf; scanned/image PDF with no extractable text.
  - `error` with no detail → confirm poll returns the message and the UI reads `job.error` (not `job.error_msg`).

### 3b. TXT / DOCX / DOC
Same flow. DOC requires the `antiword` binary which the Dockerfile installs; outside Docker it will raise `RuntimeError: antiword is not installed`.

### 3c. URL
```bash
curl -s -X POST localhost:8001/api/sources \
  -H "Authorization: Bearer $TOKEN" \
  -F type=url -F title=smoke-url -F url=https://example.com
```
- **Pass**: worker log shows `[URL] Scrapling extracted N chars` or `requests fallback extracted N chars`, then chunks upserted.
- **Fail → debug**:
  - `422` → the URL form is posting JSON instead of multipart (must send FormData).
  - `Insufficient text extracted` → site blocks bots or needs JS; Playwright path should take over.
  - Playwright failing to launch → check `chromium` install in `Dockerfile` and the `playwright install` line.
  - SSRF guard rejection → URL resolves to a private IP; expected behaviour, surface in worker log.

## 4. Library & listing verification

- `/upload` → **Manage Sources** tab: should list the docs just ingested. This hits `/api/library` client-side.
- `/library` page: should also fetch `/api/library` on load and show the same rows plus Documents / Chunks / Last-Indexed stats.
- `DELETE /api/sources/{id}` (via Remove button): row vanishes from both places and the Qdrant collection.

- **Fail → debug**:
  - Empty Library but non-empty Manage Sources → library fetch not wired; hard-refresh to clear cached HTML.
  - Docs show with `chunks=0` → ingestion failed but Document row still exists; the list filters them out.

## 5. Query smoke test

```bash
curl -s -X POST localhost:8001/api/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"question":"What is in my documents?","chat_history":[]}'
```
- **Pass**: returns `{answer, sources:[{index,citation,doc_type,url}], metadata:{retrieval_count>0}}`. Each source cites something ingested in step 3.
- **Fail → debug**:
  - `503 "No admin account configured yet"` → no admin user.
  - `answer:"I could not find relevant documents..."` → retrieval empty; check Qdrant has vectors:
    `curl -s localhost:6333/collections/user_1/points/count -X POST -H 'Content-Type: application/json' -d '{"exact":true}'`.
    Also confirm the embedder loaded on the expected device (`rag_async._get_embedder`).
  - `503 "Cannot connect to LLM"` → provider down; go to step 7.
  - LLM returns garbage / hallucinates — check `RAG_PROMPT_TEMPLATE` in `config.py` and that sources actually contain the answer.

## 6. Settings & admin

- `GET /api/settings` with admin token → user profile JSON.
- `POST /api/admin/llm-settings` from `/admin/llm-settings` UI → update provider/model/keys. Encrypted with Fernet (`models.encrypt_api_key`); `ENCRYPTION_KEY` must be set in `.env`.
- `POST /api/admin/llm-settings/test` → round-trips a prompt to the configured provider. This is the fastest LLM connectivity probe.
- `POST /api/admin/embed-device` with `{"embed_device":"cuda"}` → resets the lazy embedder so the next query reloads on the new device. **Note**: only affects the `rag` process — `rag-worker` reads `EMBED_DEVICE` env at import time. After changing the device, recreate `rag-worker` for ingestion to follow.

## 7. LLM connectivity isolation

If chat fails but retrieval works, hit `/api/admin/llm-settings/test` to isolate provider/network from prompt logic. Providers live in `llm_provider.py`:
- Ollama: `_call_ollama`. Check `base_url` reachable from inside the `rag` container (`docker compose exec rag curl -s $OLLAMA_BASE_URL/api/tags`).
- OpenAI/Anthropic/generic: `_call_openai`, `_call_anthropic`, `_call_generic`. API key decryption at `models.decrypt_api_key`; a mismatched `ENCRYPTION_KEY` on restart will make all existing keys undecryptable — rotate keys via the UI.

## 8. Edge cases to exercise

- Upload an unsupported extension (e.g. `.md`) → UI rejects before POST.
- Upload a PDF with no extractable text (image-only) → job goes to `error` with `No content extracted`.
- Submit a URL that 403s → worker log shows Scrapling failure then `requests` fallback failure; job `error` surfaces the real message in the UI.
- Stop `rag-worker` (`docker compose stop rag-worker`), upload a file — job should stay `queued` (RQ path) **or** run inline (if Redis enqueue itself fails). The inline path must complete with its own DB session, not the request-scoped one.
- Delete a doc while its ingestion job is still running → Document row disappears; RQ job may still try to upsert to Qdrant, then the next Library load won't show it. Cosmetic, not a correctness bug.
- Submit a URL pointing at a private IP (e.g. `http://127.0.0.1`) → SSRF guard should reject before fetch.

## 9. Debug reference — where each signal surfaces

| Symptom | First log to check | Then |
|---|---|---|
| Upload 422 | `rag` | Compare form fields with `/api/sources` signature |
| Job stuck `queued` | `rag-worker` | Is worker alive? Is Redis queue named `ingestion`? |
| Job `error`, no message | `rag` (poll response) | UI must read `job.error`, not `job.error_msg` |
| Chat: "could not find" | `rag` | Qdrant point count, embedder device mismatch |
| Chat: 503 LLM | `rag` | `/api/admin/llm-settings/test` → `llm_provider.py` |
| Library empty after upload | Browser devtools | `/api/library` response; library page must fetch client-side |
| DB connection error | `rag` startup | `DATABASE_URL` env, postgres healthy |
| Embedder slow/OOM | `rag` | `embed_device` in `LLMProviderConfig`; GPU visible? |
| 403 on URL ingest | `rag-worker` | SSRF guard — resolves to private IP, expected |

## 10. Issues to investigate / follow up

- **Dead Re-index button** on `/library` (reindex modal) POSTs to `/reindex` and polls `/reindex-progress`. Confirm both endpoints exist; if not, either implement them or remove the button.
- **Worker device drift**: changing the embed device via the admin UI may only reset the main process embedder. If `rag-worker` reads `EMBED_DEVICE` at import time, document that users must `docker compose restart rag-worker`.
- **No streaming chat**: `/api/chat` is one-shot. Fine, but worth flagging for UX if responses feel slow.
- **`chat_history` plumbing**: confirm it's wired into the prompt (recent fixes claim it is — verify with a multi-turn smoke test that the second answer references the first question).
- **GHCR vs local build**: confirm `pull_policy:build` in `docker-compose.yml` so local builds aren't masked by stale `:latest` from GHCR.

## Verification — how to run this runbook

1. Deploy current branch: `docker compose up -d --build`.
2. Tail logs in a second terminal: `docker compose logs -f rag rag-worker`.
3. Walk through sections 1–8 in order. At each step check the **Pass** criterion; on failure go to its **debug** pointer before proceeding — several steps assume the previous one passed (e.g. step 5 needs step 3 to have ingested something).
4. If everything passes, open follow-up issues for items in section 10.
5. If a step fails in a way the debug pointer doesn't cover, capture the exact log lines and file/line, then decide: code fix (new branch off master), config fix (`.env` / `docker-compose.yml`), or doc update.
