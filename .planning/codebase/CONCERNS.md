# Codebase Concerns

**Analysis Date:** 2026-06-16

> Source: a multi-agent adversarial code review (5 dimensions × independent verification, 38 agents) plus direct reads of every source file. **46 findings confirmed, 9 rejected.** Rejected false positives (do NOT re-chase): JWT-default-secret (mitigated by `secrets_bootstrap.py`), Fernet-ephemeral-key (lazy-loaded, mitigated), Qdrant-metadata-KeyError (dict literal always builds keys), Windows-bind-mount-breaks-Postgres (empirically refuted — Postgres runs healthy), worker-healthcheck-rq-grep (works with pinned rq 1.16), rerank-drops-sources (by design), qdrant-client-1.9-search-deprecation (works), embedder-_current_device-attr (valid nn.Module attr), CI-branch-mismatch (evidence was wrong).
>
> **Verdict: PATCH, not rewrite.** Architecture is sound; all findings are localized, patchable bugs.

## Tech Debt

**Embedder lifecycle inconsistency (`ingest_async.py:202`, `rag_async.py:29-57`):**
- Issue: The RQ worker loads a fresh `SentenceTransformer` per job and reads device from the static `EMBED_DEVICE` env, while the query path caches a module-global embedder and reads device from the DB config.
- Impact: Per-job model-load cost (~1.3GB, seconds–tens of seconds); worker and query path can embed on different devices; concurrent jobs risk OOM.
- Fix approach: Cache the embedder at worker module level; resolve device via `rag_async._get_embed_device()` so both paths agree.

**Four embedder copies in RAM (`Dockerfile:29`):**
- Issue: 4 Gunicorn UvicornWorkers each load their own ~1.3GB BGE model (~5GB just for embedders, plus Torch).
- Impact: Wasteful/OOM-risky on a CPU box; also `os.environ`+`_embedder=None` device reset (`app_fastapi.py:728-732`) only resets one of the four workers.
- Fix approach: Drop to 1–2 workers for this single-admin deployment.

**Hardcoded embed dimension (`ingest_async.py:30,55`):**
- Issue: `EMBED_DIM = 1024` hardcoded, but `EMBED_MODEL` is an env knob.
- Impact: Swapping the model either hard-fails upserts (dim mismatch) or, for a same-dim model, silently returns garbage neighbors.
- Fix approach: Derive dim from `embedder.get_sentence_embedding_dimension()` at collection creation; validate existing collection dim on startup.

**`chat_history` accepted but ignored (`rag_async.py:141-208`, `app_fastapi.py:198-201`):**
- Issue: `ChatRequest.chat_history` is plumbed through `query_async` but never used in the prompt.
- Impact: No real multi-turn memory; the UI sends `chat_history: []` anyway.
- Fix approach: Thread history into the prompt, or drop the param honestly.

**Dead citation metadata keys (`rag_async.py:174,199`):**
- Issue: Builds citations referencing `case_name` and `doc_id` payload keys that are never set anywhere (vestige of a legal-docs origin).
- Impact: Always falls through to defaults; dead code/confusion.
- Fix approach: Remove the unused keys.

**Upload dir mismatch (`config.py:45-46` vs `app_fastapi.py:403`):**
- Issue: `config.py` pre-creates `CACHE_ROOT/raw/uploads`, but the app saves uploads to `CACHE_ROOT/uploads`.
- Impact: A created-but-unused dir; mild confusion. Saves still work (parent created on write).
- Fix approach: Align the two paths.

**Deprecations (`app_fastapi.py:904-915`, and `datetime.utcnow()` across `app_fastapi.py`/`models.py`/`worker.py`):**
- Issue: `@app.on_event("startup"/"shutdown")` and naive `datetime.utcnow()` are deprecated (FastAPI lifespan / Python 3.12+).
- Impact: Works today; deprecation warnings, future breakage.
- Fix approach: Lifespan handler; timezone-aware `datetime.now(UTC)`.

**Stale docs (`CLAUDE.md`, `QUICK_START.md`):**
- Issue: Docs claim `/api/chat` + `/api/library` are public (code requires auth), and QUICK_START describes the dead Flask/ChromaDB v1 stack.
- Impact: Misleads operators/contributors.
- Fix approach: Rewrite to the FastAPI/Qdrant reality.

**Unused per-user isolation:**
- Issue: `QdrantManager` namespaces by `user_{id}`, but chat/library always query the admin's collection — the isolation is dead weight under the admin-centric model.
- Impact: Misleading capability; not exercised.
- Fix approach: Either lean into multi-user or document it as admin-only by design.

## Known Bugs

**Worker reap falsely errors live RQ jobs (`worker.py:38-64`):**
- Symptoms: A successfully-ingested document is reported as failed and auto-deleted by the UI.
- Trigger: Any `rag-worker` restart (deploy/crash) while jobs are queued. On boot, `reap_stale_jobs()` marks ALL queued/running rows `error`; the worker then runs them to success, but the DB stays `error` (terminal in the poll logic) → UI deletes the doc while Qdrant has the vectors.
- Root cause: Blanket reap doesn't check whether the job is still live in Redis.
- Fix: Only reap jobs with no live RQ job (cross-check `rq_job_id` against the registry) or older than a threshold.

**Ingestion errors show no cause (`templates/upload.html:354` vs `app_fastapi.py:540-547`):**
- Symptoms: Every failed ingestion shows "Error: Processing failed"; DevTools shows "Error: undefined".
- Trigger: Any ingestion failure (bad file, unreachable URL).
- Root cause: JS reads `job.error_msg`; the API returns `job.error`.
- Fix: Read `job.error` in the JS.

**`doc.chunks` only set when polled (`app_fastapi.py:518-529`):**
- Symptoms: Library shows "0 chunks" and the job stays `queued`/`running` forever despite vectors existing.
- Trigger: Client doesn't poll the job to completion (tab closed / navigated away). After `result_ttl` (1h) the count is unrecoverable.
- Root cause: The poll endpoint is the only writer of `Document.chunks` and terminal job status; the worker writes nothing to Postgres.
- Fix: Worker writes `status`/`chunks`/`completed_at` directly at end-of-job; poll endpoint becomes read-through.

**Non-deterministic Qdrant point IDs (`ingest_async.py:69`):**
- Symptoms: Re-ingesting a document creates duplicate vectors instead of overwriting.
- Trigger: Any re-ingest across process restarts.
- Root cause: `abs(hash(str)) % 2**63` — Python `str.__hash__` is per-process randomized (PYTHONHASHSEED).
- Fix: Stable hash, e.g. `int.from_bytes(hashlib.sha1(key.encode()).digest()[:8], "big")`.

**Delete reconstructs prefix from title (`app_fastapi.py:592`, `Document` has no prefix column):**
- Symptoms: Deleting one doc wipes another's vectors; or leaves orphaned vectors in Qdrant.
- Trigger: Two docs with same type+title (collide to one prefix), or titles whose normalization doesn't round-trip.
- Root cause: `doc_id_prefix` is derived from the title at both ingest and delete, never stored.
- Fix: Store a stable unique prefix (e.g. `doc_{id}`) on the `Document` row; use it for upsert + delete.

**Detached DB session in inline fallback (`app_fastapi.py:468-491`):**
- Symptoms: Inline-ingestion status/chunks never persist (commits fail).
- Trigger: Only when Redis enqueue throws (NOT in Docker — latent here; real on local/Windows runs).
- Root cause: `run_inline()` runs in `asyncio.create_task` after the request returns, using the request-scoped `db`/ORM objects that `get_db()` already closed.
- Fix: Open a fresh `SessionLocal()` inside the task, re-fetch by id, commit there.

**`update_settings` KeyError on null email (`app_fastapi.py:627-629`):**
- Symptoms: 500 instead of a clean response.
- Trigger: POST `/api/settings` with `email: null`.
- Root cause: `data["email"].strip()` on `None`.
- Fix: Guard for missing/None.

**Anthropic content index (`llm_provider.py:81`):**
- Symptoms: IndexError on empty/non-text responses (e.g. refusal).
- Trigger: Anthropic returns no text block.
- Fix: Guard for empty content / non-text blocks.

**Generic provider silent empty (`llm_provider.py:148-149`):**
- Symptoms: Empty answer on an unexpected response shape, no error.
- Fix: Validate shape; raise a clear error.

**BM25 fusion mixes scales (`rag_async.py:82-97`):**
- Symptoms: Re-ranking dominated by raw BM25, semantic similarity swamped.
- Root cause: `0.6*cosine + 0.4*bm25_raw` — cosine ∈ ~[0,1] but BM25 raw is unbounded.
- Fix: Min-max normalize BM25 (and/or cosine) before the weighted sum.

**Other lower-severity bugs:** chunk-overlap can carry a full oversized buffer forward / split mid-marker (`ingest.py:100-125`); crawl `word_count = total_chars//5` is a rough proxy (`ingest.py:628`); chat renderer maps `__x__` to italic instead of bold (`templates/chat.html:142-147`); job-status response omits the chunk count so the UI never confirms how many chunks indexed (`app_fastapi.py:540-547`).

## Security Considerations

**SSRF via URL ingest + crawler (`ingest.py:262-289,542-553,556-630`):**
- Risk: Admin-supplied URL is fetched server-side with no scheme/host/IP filtering → internal services (`http://qdrant:6333`, `http://ollama:11434`, `http://postgres:5432`), cloud metadata (`169.254.169.254`), `localhost`, internal port scanning; content exfiltrated via library/chat. Redirects are followed (no `allow_redirects=False`), and crawl has no server-side page ceiling.
- Current mitigation: Gated behind the single admin account.
- Recommendations: Resolve host, reject loopback/private/link-local/reserved IPs and internal service hostnames; disable redirects or re-validate each hop and crawl link; same checks for the robots.txt fetch; enforce a server-side max_pages/max_depth.

**Admin land-grab (`app_fastapi.py:280-300`):**
- Risk: Registration is open + unthrottled and the FIRST registrant silently becomes admin → on a briefly-exposed fresh deploy a stranger gets the API-key surface, SSRF crawler, model pull/delete.
- Current mitigation: None (registration order only).
- Recommendations: Disable open registration once an admin exists, and/or pin admin via env / one-time setup token.

**CORS wildcard + credentials (`app_fastapi.py:230-236`):**
- Risk: `allow_origins=["*"]` with `allow_credentials=True` → Starlette reflects any Origin with credentials. Benign today (Bearer-in-localStorage, no cookies) but fail-open.
- Recommendations: Pin explicit origins, or set `allow_credentials=False`.

**File upload (`app_fastapi.py:401-410`):**
- Risk: `await file.read()` loads the whole file into memory (DoS); `secure_filename` can return `""` → predictable/collidable path; `doc_type` is client-controlled with no content validation.
- Recommendations: Enforce a max size (stream + reject); default filename when empty; validate/sniff type.

**Error-detail leakage (`app_fastapi.py:342,532,826,869`, `llm_provider.py:124`, `ingest.py:319,322`):**
- Risk: Raw exception strings returned via `HTTPException(detail=str(e))` (internal hostnames, stack hints).
- Recommendations: Log detail server-side; return generic messages.

**Injection / XSS (`app_fastapi.py:775-827`, `templates/chat.html:184-200`):**
- Risk: Ollama pull/delete interpolate model name + upstream error body into hand-built JSON (`f'{{"error": ...}}'`); chat sources panel injects `citation`/`doc_type`/href into `innerHTML` unescaped (doc titles are admin-controlled but still).
- Recommendations: Build JSON with `json.dumps`; escape all interpolated values in the DOM.

**Open redirect (`app_fastapi.py:550-565`):** `/api/sources/{id}/download` 302s to the stored `doc.url`. Low (admin-set), note only.

**Design-level (accepted / track):** 30-day JWT with no revocation/logout invalidation (`app_fastapi.py:87-105`); all HTML pages served with no server-side auth (client-side JWT only) — acceptable since the JSON APIs are guarded by `get_current_user`/`require_admin`, but worth noting.

## Performance Bottlenecks

**Embedding on CPU (`config.py:58` default `EMBED_DEVICE=cpu`):**
- Problem: First query/ingest downloads BGE (~1.3GB) and embeds on CPU; first request is slow.
- Cause: CPU inference + per-process model load (×4 workers, plus per-job in worker).
- Improvement path: GPU device (`cuda`), fewer web workers, cache the worker embedder.

**LLM answers on CPU Ollama:**
- Problem: A 22B model (mistral-small3.1) is impractically slow on CPU; even 8B is sluggish.
- Improvement path: Smaller model for CPU, or point at a GPU/remote Ollama.

## Fragile Areas

**Ingestion job-status state machine (`app_fastapi.py:502-547`, `worker.py`):**
- Why fragile: Truth about job completion lives in RQ + the poll endpoint, not the worker → polling races, the reap bug, and `chunks=0` rows.
- Safe modification: Make the worker the source of truth (write DB at end-of-job); treat polling as read-through.
- Test coverage: None.

**Qdrant document identity (`ingest_async.py:59-95`):**
- Why fragile: Point IDs (random hash) and delete (reconstructed prefix) are both non-canonical → dupes + cross-doc deletes.
- Safe modification: Introduce a single stored canonical `doc_id_prefix`.
- Test coverage: None.

## Dependencies at Risk

**`numpy<2.0` pin (`requirements.txt:35`):** Correct for this Torch/sentence-transformers combo, but an upgrade trap — bumping Torch later may force a numpy 2 migration.

**`qdrant-client==1.9.0` vs `qdrant/qdrant:latest` server (`requirements.txt:7`, `docker-compose.yml:24`):** Works today; pinned client against a floating server image is drift risk. Verified `.search()` still works (a false-positive deprecation was ruled out), but pin the server image too.

**Scrapling + Playwright/Chromium (`Dockerfile:17-18`):** The `playwright install ... || true` swallows install failures → JS-rendered scraping can silently fall back to requests-only. Surface the failure.

## Test Coverage Gaps

**Everything — there is no test suite:**
- What's not tested: auth/roles, ingestion pipeline, retrieval + BM25 rerank, provider dispatch, crawler, Qdrant identity.
- Risk: Regressions ship silently; the bugs above were found by review, not tests.
- Priority: High.
- Difficulty: Moderate — needs Docker-compose-backed integration fixtures (Postgres/Qdrant/Redis) plus unit tests for `chunk_text`/rerank/ID logic.

---

*Concerns audit: 2026-06-16 (seeded from adversarial review; the fix work is tracked as GSD phases).*
*Update as issues are fixed or new ones discovered.*
