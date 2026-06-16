---
phase: 1
plan: "01-01"
title: Critical Fixes
status: complete
date: 2026-06-16
---

# Phase 1 / Plan 01-01 — Summary

**Outcome:** All 13 critical-fix tasks applied across 8 atomic commits. First-pass drafted by the local model (`gemma4:26b` then `gemma4:e4b` once it pulled), every diff reviewed/gated by the orchestrator. `py_compile` clean on all changed modules.

## What changed

| Task | Req | Commit | Notes |
|------|-----|--------|-------|
| T1 ingestion error in UI | STAB-05 | f92d27f | `upload.html` reads `job.error` (both occurrences — model under-scoped to one). |
| T2 reap only stale jobs | STAB-02 | aa566cf | Checks RQ liveness before erroring; tightened so genuinely-failed RQ jobs still reap. |
| T3 worker writes DB | STAB-01 | 4682678 | `run_ingestion_job` now persists `Document.chunks` + job status/error itself (gained `document_id`/`job_id` params). |
| T4 stable IDs + prefix | STAB-03/04 | 4682678 | `doc_{id}` prefix (no new column needed — derived from PK) for upsert+delete; sha1-based point IDs. |
| T5 inline session | STAB-06 | 4682678 | Inline task captures plain ids; worker owns DB writes (no detached session). |
| T6 dim + embedder cache | STAB-07 | 4682678 | Collection dim from `len(embeddings[0])`; module-level cached worker embedder + DB device. |
| T7 provider/settings guards | STAB-08 | e2d22f1 | Anthropic empty-content + generic empty-choices guards; null-safe settings email. |
| T8 SSRF guard | SEC-01 | 87acf54 | `_assert_url_allowed` + `_safe_get` (per-hop redirect validation) on all fetches. **Unit-tested**: blocks qdrant/ollama/localhost/metadata/loopback/private/`file://`, allows real public URLs. Fixed model's `netloc`-vs-`hostname` bug + IPv4-mapped-IPv6 bypass. |
| T9 admin land-grab | SEC-02 | d698bac | `ADMIN_USERNAME` pin; never a second admin once one exists. |
| T10 CORS | SEC-03 | 0e647f9 | `allow_credentials=False`. |
| T11 upload hardening | SEC-04 | e0f0310 | 50 MB chunked cap, safe-filename fallback, extension-authoritative type. Fixed model's async-unlink + open-file-delete (Windows) bugs. |
| T12 error leakage | SEC-05 | e2d22f1 | Chat returns generic 500; detail logged server-side. |
| T13 worker count | infra | 0e647f9 | Gunicorn 4 → 2 (RAM). |

## Gating highlights (where the local draft was wrong)
- T8 SSRF: used `parsed.netloc` (`qdrant:6333`) instead of `hostname` → would bypass the internal-host block. Rewrote with IP-literal handling, `ipv4_mapped` unwrap, per-hop redirect validation. Unit-tested.
- T11 upload: `await dest.unlink()` (Path.unlink isn't async) and deleting an open handle (fails on Windows). Rewrote to break-then-unlink.
- T3 worker: `with get_session_local() as s` (that returns a sessionmaker, not a session) → fixed to `SessionLocal()`; used `session.get()`.
- T1: only fixed one of two `error_msg` reads.

## Deviations
- No DB migration needed: stable prefix derives from the existing `Document.id` (no new column), so the running Postgres schema is unchanged.
- New optional env `ADMIN_USERNAME` (admin pin) — documented in Phase 2 docs pass.

## Verification
- `py_compile` clean; SSRF guard unit-tested (10/10 cases). Full runtime validation via the post-Phase-1 rebuild + Phase 3 user simulation.
