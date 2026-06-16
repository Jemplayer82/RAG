# Phase 1 Critical Fixes — Adversarial Code Review

**Reviewed:** 2026-06-16  
**Diff:** `git diff f546b2b..HEAD` (9 `fix(phase1)` commits)  
**Files:** worker.py, ingest_async.py, app_fastapi.py, ingest.py, llm_provider.py, Dockerfile, templates/upload.html

---

## Findings

### 1. BLOCKER — `ingest_async.py:48` — `__init__` pre-creates Qdrant collection with hardcoded dim=1024, making the model-derived dim fix in `upsert_chunks` a dead code path

**File:** `ingest_async.py`, lines 48 and 68–72

**Issue:** `QdrantManager.__init__` calls `self._ensure_collection()` with the default `size=EMBED_DIM` (hardcoded 1024) *before* any embeddings are computed. If the collection does not yet exist (i.e. first ingestion for a user), it is created immediately with size=1024. When `upsert_chunks` later calls `self._ensure_collection(size=actual_dim)`, the collection already exists and `_ensure_collection` is a no-op (it only acts when the collection is absent). The model-derived dimension is therefore never used for a *new* collection — which is exactly the scenario the fix is intended to protect. If `EMBED_MODEL` is changed to a 768-dim or 384-dim model, the first ingestion per user will silently create a 1024-dim collection and every `upsert` call will fail with a vector dimension mismatch from Qdrant.

**Fix:** Remove the `_ensure_collection()` call from `__init__` and let `upsert_chunks` own collection creation (it already has the embeddings in hand by that point):

```python
def __init__(self, user_id: int):
    self.user_id = user_id
    self.collection_name = f"user_{user_id}"
    self.client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    # Do NOT call _ensure_collection here — upsert_chunks creates it
    # with the actual model dimension once embeddings are computed.

def upsert_chunks(self, chunks, embedder, doc_id_prefix):
    if not chunks:
        return 0
    texts = [c["text"] for c in chunks]
    embeddings = embedder.encode(texts, convert_to_tensor=False, show_progress_bar=False)
    dim = len(embeddings[0]) if embeddings else EMBED_DIM
    self._ensure_collection(size=dim)   # NOW this actually runs for new collections
    ...
```

Any caller that needs the collection to exist before `upsert_chunks` (e.g. `search`, `count`, `list_documents`) can call `_ensure_collection()` with the known dim or handle the Qdrant `collection not found` error explicitly. Alternatively, keep a `_lazy_ensure(size)` method that is called by every public method except `upsert_chunks`, which passes the real dim.

---

### 2. BLOCKER — `ingest.py:349–367` — Scrapling fetch path bypasses per-hop redirect SSRF guard

**File:** `ingest.py`, lines 349–367 (`_extract_text_scrapling`)

**Issue:** `_safe_get` was added to follow redirects manually, validating each hop with `_assert_url_allowed`. But `_extract_text_scrapling` calls `_assert_url_allowed(url)` *once* on the initial URL, then hands the URL to `scrapling.Fetcher.get()` / `PlayWrightFetcher.get()`, which follow HTTP redirects internally without any per-hop validation. A public URL that returns a `302 Location: http://169.254.169.254/latest/meta-data/` (or `http://qdrant:6333`) will pass the initial check and Scrapling will silently fetch the internal target. This is an SSRF bypass for the primary (non-fallback) URL ingestion path.

**Fix:** Either:

a) Disable redirects in Scrapling and implement manual redirect following (may not be possible depending on Scrapling's API), or  
b) Pre-fetch with `_safe_get` (which validates all hops), then pass the *final resolved URL* to Scrapling if still needed for JS rendering:

```python
def _extract_text_scrapling(url: str) -> str:
    # Resolve redirects through our SSRF-safe getter; get the final URL.
    probe = _safe_get(url, headers={"User-Agent": "Mozilla/5.0"})
    final_url = probe.url  # requests stores final URL after redirects
    _assert_url_allowed(final_url)  # re-validate final destination
    from scrapling import Fetcher, PlayWrightFetcher
    # Now fetch the validated final URL with Scrapling (no further redirects expected)
    ...
```

c) As a simpler immediate fix: move the entire `ingest_url` flow to `_safe_get` + BeautifulSoup as primary (since Scrapling's redirect validation cannot be hooked), and demote Scrapling to an opt-in mode with a documented SSRF caveat.

---

### 3. WARNING — `ingest_async.py:262` — Success writeback skipped entirely when `document_id=0` (edge) and silently skipped when both IDs are `None` without logging

**File:** `ingest_async.py`, line 262

**Issue:** `if document_id or job_id:` uses truthiness, so `document_id=0` (theoretically impossible for auto-increment PKs but allowed by the `int = None` signature) would skip the DB writeback. More practically: if the caller passes neither `document_id` nor `job_id` (both `None` — the old calling convention still accepted by the default-`None` signature), `Document.chunks` is never updated and `IngestionJob` is never marked complete. The function returns a count, but the DB is silently left stale. There is no log warning when both are absent.

**Fix:** Replace with explicit `None` checks and add a warning:

```python
if document_id is None and job_id is None:
    logger.warning("[WORKER] No document_id or job_id provided — DB will not be updated")
else:
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        if document_id is not None:
            doc = session.get(Document, document_id)
            if doc:
                doc.chunks = count
        if job_id is not None:
            job = session.get(IngestionJob, job_id)
            if job:
                job.status = "complete"
                job.completed_at = datetime.utcnow()
        session.commit()
```

---

### 4. WARNING — `app_fastapi.py:298–304` — Admin registration TOCTOU race still present without `ADMIN_USERNAME`

**File:** `app_fastapi.py`, lines 298–304

**Issue:** The land-grab fix reads `admin_exists = db.query(User).filter(...).count() > 0` and then (if false) `is_admin = (db.query(User).count() == 0)`. These two queries are not atomic. With `ADMIN_USERNAME` unset (the default), two concurrent registration requests on a fresh deploy can both see `admin_exists=False, count=0` and both create admin accounts, violating the "exactly one admin" invariant. This was noted in the fix commit but the database-level race was not closed. With `gunicorn --workers 2`, two Uvicorn worker processes share no in-process lock, so the race is real under concurrent first-registrations.

**Fix:** Use a database-level unique constraint or `SELECT ... FOR UPDATE` to serialize admin creation:

```python
# Option A: use an INSERT with a unique partial index on (is_admin=True) at DB level.
# Option B: serialize with an advisory lock or a single atomic upsert.
# Simplest correct fix: set ADMIN_USERNAME in deployment — document this as required.
# At minimum, log a warning if ADMIN_USERNAME is not set:
if not admin_username:
    logger.warning("[AUTH] ADMIN_USERNAME not set — first-registration race possible")
```

---

### 5. WARNING — `ingest_async.py:207–208` — `document_id` and `job_id` typed as `int = None` instead of `Optional[int] = None`

**File:** `ingest_async.py`, lines 207–208

**Issue:** The signature `document_id: int = None` is technically a type error (`None` is not `int`). While Python doesn't enforce it at runtime, any static analysis / mypy run will flag this, and type-aware tooling will incorrectly infer the parameter is always non-None. The same pattern appears in the RQ enqueue kwargs where `None` values serialized through RQ's job payload may behave unexpectedly depending on the serializer.

**Fix:**

```python
from typing import Optional
document_id: Optional[int] = None,
job_id: Optional[int] = None,
```

---

### 6. WARNING — `worker.py:65` — Jobs with `rq_job_id=None` are unconditionally reaped, but inline-fallback jobs have `rq_job_id=NULL` and may still be running

**File:** `worker.py`, lines 65–77

**Issue:** When Redis is unavailable, `add_source` falls back to `asyncio.create_task(run_inline())`, but `job_record.rq_job_id` is never set (remains `NULL`). If the `rag` container is restarted while an inline ingestion is in progress, the worker starts, calls `reap_stale_jobs`, sees `rq_job_id=None`, falls through to the reap block, and marks the job as `error`. The inline task either completes successfully (writing `complete` status after the reap has already written `error`) or is killed by the restart. In either case, the DB state is incoherent: the document may have chunks in Qdrant but shows `error` in the UI.

This is a fundamental tension between the inline fallback design and the reap logic: there is no way to distinguish "inline job that is still running" from "job that was lost". The safest fix is one of:

a) Set a sentinel `rq_job_id = "inline"` for inline-fallback jobs so `reap_stale_jobs` can skip them (or reap with a different message), or  
b) Accept the incoherence as documented and note it as a known limitation of the inline fallback.

**Immediate fix for (a):**

```python
# In add_source, after choosing use_inline:
if use_inline:
    job_record.rq_job_id = "inline"  # sentinel: not an RQ job ID
    db.commit()

# In reap_stale_jobs:
if job.rq_job_id and job.rq_job_id != "inline":
    try:
        rq_status = Job.fetch(job.rq_job_id, ...)
        ...
    except NoSuchJobError:
        pass
elif job.rq_job_id == "inline":
    continue  # inline job; cannot verify, leave it
# else rq_job_id is None: reap
```

---

### 7. WARNING — `ingest.py:88` — DNS rebinding not mitigated: `socket.getaddrinfo` result is checked once but the actual TCP connection resolves DNS again

**File:** `ingest.py`, lines 88–95 (`_assert_url_allowed`)

**Issue:** `_assert_url_allowed` resolves the hostname with `getaddrinfo` and blocks if any returned IP is private. However, `requests.get` (called immediately after in `_safe_get`) performs its *own* DNS lookup at TCP connect time. Between the two lookups, a DNS rebinding attack can swap the public IP for an internal one. This is a known SSRF mitigation gap. In the Docker network environment (internal DNS is `127.0.0.11`), a legitimate attacker-controlled domain could return `1.2.3.4` to the guard and `172.17.0.2` (qdrant container) to `requests`. The risk is real but requires attacker control of DNS, which is a higher bar than a simple redirect bypass.

**Fix:** Use a custom `socket.create_connection` wrapper (or `requests`' `TransportAdapter`) that binds the resolved IP checked by `_ip_is_blocked` to the actual TCP connection, preventing the second resolution. Libraries like `ssrf-py` implement this. As a short-term mitigation, document the limitation.

---

### 8. LOW — `ingest.py:635–698` — `ingest_crawl` has no upfront SSRF guard on `seed_url`, inconsistent with `ingest_url`

**File:** `ingest.py`, line 635

**Issue:** `ingest_url` calls `_assert_url_allowed(url)` immediately on entry (line 385) for a fast, clear error before any I/O. `ingest_crawl` does not — the seed URL is only validated when `_fetch_page` calls `_safe_get` inside the BFS loop. This is not a security bypass (the guard still fires), but it means an invalid seed URL produces a generic `[CRAWL] fetch failed` warning instead of a clear SSRF-blocked error, and `_normalize_url(seed_url)` runs on an unvalidated URL first.

**Fix:** Add `_assert_url_allowed(seed_url)` as the first line of `ingest_crawl`.

---

### 9. LOW — `ingest_async.py:263,282` — `get_session_local()` called twice per job, creating two separate SQLAlchemy engines and connection pools

**File:** `ingest_async.py`, lines 263 and 282

**Issue:** `get_session_local()` calls `get_engine()` which calls `create_engine(...)`, creating a new engine and connection pool each time. `run_ingestion_job` calls it once in the success path (line 263) and once in the error path (line 282). Two engine instances are created per job, each with their own connection pool. The engines are GC'd after the `with` block exits (no explicit dispose), potentially leaving idle connections open until GC runs. Under load (many jobs), this accumulates unclosed connections.

**Fix:** Call `get_session_local()` once at the top of `run_ingestion_job`, before the try/except, and reuse the result in both branches:

```python
from models import Document, IngestionJob, get_session_local
SessionLocal = get_session_local()

try:
    ...
    with SessionLocal() as session:
        ...
except Exception as e:
    ...
    with SessionLocal() as session:
        ...
```

---

### 10. LOW — `templates/upload.html:354` — `job.error` field name change may not match API response key

**File:** `templates/upload.html`, line 354; `app_fastapi.py`, line 589

**Issue:** The fix changes `job.error_msg` to `job.error` in the frontend. The API at `/api/sources/jobs/{id}` returns `"error": job.error_msg` (line 589 of `app_fastapi.py`) — so the key in the JSON response IS `"error"`. This matches the frontend change. However, the field name `error_msg` in `IngestionJob` model (line 120 of `models.py`) is correctly aliased in the API response to `"error"`. The fix is correct but the naming inconsistency between DB column (`error_msg`) and API key (`error`) is a latent confusion source.

No code fix needed, but consider aligning the API key with the DB column name (`error_msg`) or vice versa for consistency.

---

## Summary

| Severity | Count | IDs |
|----------|-------|-----|
| BLOCKER  | 2     | #1 (dim fix dead code), #2 (Scrapling SSRF bypass) |
| WARNING  | 5     | #3 (silent DB skip), #4 (admin TOCTOU), #5 (Optional type), #6 (inline reap incoherence), #7 (DNS rebinding) |
| LOW      | 3     | #8 (crawl no upfront guard), #9 (double engine), #10 (error field naming) |
