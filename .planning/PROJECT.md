# RAG Assistant (v2.0)

## What This Is

A self-hosted, multi-user Retrieval-Augmented Generation web app: an admin curates a knowledge base (PDF/DOCX/TXT uploads + web pages/crawls), and users ask natural-language questions and get answers grounded in those sources with `[N]` citations. FastAPI + Postgres + Qdrant + Redis/RQ + Ollama, all dockerized. For individuals or small teams who want private document Q&A on their own hardware.

## Core Value

A user can ask a question and get an accurate answer grounded in the ingested documents, with correct citations back to the source. If everything else fails, retrieval + cited answering must work.

## Requirements

### Validated

<!-- Inferred from existing, working code -->
- ✓ JWT auth with first-user-is-admin role model — v2.0
- ✓ Document ingestion (PDF/DOCX/TXT/URL/crawl) via Redis RQ background worker — v2.0
- ✓ Qdrant vector store with semantic search + BM25 re-rank — v2.0
- ✓ Pluggable LLM providers (Ollama/OpenAI/Anthropic/generic) with admin UI + encrypted keys — v2.0
- ✓ Dockerized 6-service stack with self-bootstrapping secrets — v2.0

### Active

<!-- This milestone: stabilize, fix, harden, verify -->
- [ ] Fix all 46 confirmed bugs from the code review (correctness, data integrity, security)
- [ ] Prove the app works end-to-end via a full user simulation (admin + user, ingest, scrape, model load, hard Q&A)
- [ ] Pass a dedicated security audit (SSRF, auth, CORS, upload, injection)
- [ ] Correct the stale documentation

### Out of Scope

- Full rewrite — the architecture is sound; this is a patch pass (review verdict).
- True multi-tenant isolation — the per-user Qdrant namespacing exists but the app is admin-centric by design; leaving as-is.
- New product features — this milestone is stabilize-and-harden, not expand.
- A test suite as a deliverable — noted as the top concern, but building one is deferred (manual UAT this milestone).

## Context

- Cloned fresh from `jemplayer82/rag` into `C:\Users\Landon\Projects\RAG`; full 6-service Docker stack already built and healthy.
- A multi-agent adversarial review (38 agents) produced **46 confirmed findings** (9 false positives rejected). See `.planning/codebase/CONCERNS.md`. Verdict: **patch, not rewrite.**
- Working on branch `gsd/stabilize-and-harden`.
- Test corpus: **quantum computing** (arXiv papers + Wikipedia) so cross-document scientific questions are meaningful.

## Constraints

- **Approach**: Patch, not rewrite — fixes must stay within the existing architecture.
- **Coding routing**: Local-first — `gemma4:e4b` (native-host GPU Ollama, `http://localhost:11434`) drafts every fix; the orchestrator reviews/gates and rewrites before anything lands.
- **App LLM**: Ollama on CPU in the Docker stack (`llama3.1:8b`) for Phase-3 answer testing.
- **Host**: Windows 11 + Docker Desktop; data bind-mounted at `C:/Users/Landon/Projects/RAG/data`.
- **Security**: ASVS L1, block on HIGH; the app is single-admin self-hosted but must not be trivially pivotable on its Docker network.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Patch, not rewrite | All 46 findings are localized + patchable; architecture is sound | — Pending |
| Full GSD structure (brownfield) | User wants tracked phases/artifacts for the work | — Pending |
| Local-first coding via `gemma4:e4b`, orchestrator gates all | User wants their GPU model drafting first-pass; quality kept via review | — Pending |
| Quantum-computing test corpus | "Related, not easy" scientific docs for meaningful cross-doc Q&A | — Pending |
| App answers on CPU Ollama `llama3.1:8b` | Realistic local model; 22B too slow on CPU | — Pending |

---
*Last updated: 2026-06-16 after GSD brownfield init*
