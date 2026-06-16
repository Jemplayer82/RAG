---
phase: 2
plan: "02-01"
title: Quality Fixes
status: complete
date: 2026-06-16
---

# Phase 2 / Plan 02-01 — Summary

**Outcome:** All Q1–Q11 quality fixes applied + the Phase 1 code-review gate's findings addressed. Local-first drafts (`gemma4:e4b`), orchestrator-gated. Rebuilt; stack healthy; lifespan migration validated at runtime.

## Quality fixes
| Task | Req | Notes |
|------|-----|-------|
| Q1 BM25 normalize | RAG-01 | Min-max normalize BM25 to [0,1] before 0.6/0.4 fusion. |
| Q2 chat_history | RAG-02 | Wired into prompt server-side; chat.html now sends/resets recent turns. |
| Q3 dead citation keys | RAG-02 | Removed `case_name`/`doc_id`. |
| Q4 sources XSS | SEC-06 | Escape citation/doc_type; http(s)-only hrefs; `__` → bold. |
| Q5 ollama-pull JSON | SEC-06 | `json.dumps` instead of hand-built f-string JSON. |
| Q6 download redirect | — | http(s)-only redirect target. |
| Q7 lifespan | — | `@app.on_event` → `asynccontextmanager` lifespan (validated: app boots, init_db runs). |
| Q8 upload dir | — | config creates `CACHE_ROOT/uploads` (matches where the app writes). |
| Q9 chunk overlap | — | Overlap snapped to word boundary. |
| Q10 playwright | — | Dockerfile surfaces install failure instead of swallowing. |
| Q11 docs | DOCS-01 | CLAUDE.md auth claim fixed + ADMIN_USERNAME documented; QUICK_START rewritten for the Docker stack. |

## Phase 1 review-gate findings (REVIEW.md) — all addressed
- **BLOCKER #1** dead dim-derivation → lazy collection creation in `upsert_chunks` with the real model dim; reads/deletes tolerate a missing collection.
- **BLOCKER #2** Scrapling SSRF redirect bypass → guarded `requests`/`_safe_get` is now the primary fetch; Scrapling is a thin-result fallback only.
- WARN #3 silent DB skip → explicit `is None` checks + warning. WARN #5 `Optional[int]` types. WARN #6 inline-job reap sentinel (`rq_job_id="inline"`). WARN #4 admin-race warning log + `ADMIN_USERNAME` now wired through compose/.env. LOW #8 crawl upfront SSRF guard. LOW #9 single engine per job.
- **Documented residuals** (not fixed): WARN #7 DNS-rebinding (needs IP-pinned connections; admin-only, high-bar) and LOW #10 `error_msg` vs `error` API naming — to note in SECURITY.md.

## Deviation (improvement)
- The rag container can reach the **native host GPU Ollama** (`host.docker.internal:11434`). Phase 3 will point the app's answer model there with the chosen `llama3.1:8b` (GPU speed) instead of the slow dockerized CPU Ollama — same model, better signal.

## Verification
- `py_compile` clean; rebuilt; `docker compose ps` healthy; lifespan startup confirmed in logs; `ADMIN_USERNAME=qadmin` live in container; SSRF guard re-tested (still blocks internal targets).
