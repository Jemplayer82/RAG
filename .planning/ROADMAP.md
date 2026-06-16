# Roadmap: RAG Assistant — Stabilize & Harden

## Overview

Take a freshly-cloned, already-running RAG app from "boots and looks fine" to "verified working and hardened." Phase 1 fixes the correctness/data-integrity/security blockers; Phase 2 cleans up retrieval quality and the remaining lower-severity issues; Phase 3 drives the whole app like a real user against a quantum-computing corpus and breaks it on purpose; Phase 4 is a dedicated security audit. All fixes are drafted first-pass by a local GPU model (`gemma4:e4b`) and gated by the orchestrator.

## Phases

- [x] **Phase 1: Critical Fixes** — correctness, data-integrity, and high-severity security blockers
- [x] **Phase 2: Quality Fixes** — retrieval quality, injection/XSS, cleanup, docs; then rebuild
- [ ] **Phase 3: User Simulation & Break-Testing** — drive the app end-to-end, fix what breaks
- [ ] **Phase 4: Security Audit** — Semgrep + /security-review + targeted re-probe

## Phase Details

### Phase 1: Critical Fixes
**Goal**: Every correctness/data-integrity bug and high-severity security hole that would derail real use is fixed and committed.
**Depends on**: Nothing (first phase)
**Requirements**: STAB-01, STAB-02, STAB-03, STAB-04, STAB-05, STAB-06, STAB-07, STAB-08, SEC-01, SEC-02, SEC-03, SEC-04, SEC-05
**Success Criteria** (what must be TRUE):
  1. Ingesting a document, never polling it, then viewing the Library shows the correct chunk count.
  2. Re-ingesting the same document does not create duplicate vectors; deleting one document never removes another's.
  3. A worker restart with a queued job does not falsely fail/delete it; it completes.
  4. URL ingest of an internal host (`http://qdrant:6333`, `169.254.169.254`, a redirect to one) is refused.
  5. A second registration cannot become admin; CORS is not credentialed-wildcard; oversized/unsafe uploads are rejected; errors don't leak internals.
**Plans**: TBD

Plans:
- [ ] 01-01: TBD (planned via /gsd:plan-phase 1)

### Phase 2: Quality Fixes
**Goal**: Retrieval ranking is sound, injection/XSS surfaces are closed, lower-severity issues and docs are cleaned up, and the images are rebuilt.
**Depends on**: Phase 1
**Requirements**: RAG-01, RAG-02, SEC-06, DOCS-01
**Success Criteria** (what must be TRUE):
  1. BM25/semantic fusion is normalized; a keyword-heavy query and a semantic query both return sensible top results.
  2. Chat renders source citations without XSS; the Ollama-pull error channel can't be broken by a crafted model name.
  3. `CLAUDE.md`/`QUICK_START.md` describe the actual FastAPI/Qdrant app.
  4. `rag` + `rag-worker` images rebuilt and the stack is healthy.
**Plans**: TBD

Plans:
- [ ] 02-01: TBD (planned via /gsd:plan-phase 2)

### Phase 3: User Simulation & Break-Testing
**Goal**: The app is proven to work end-to-end for an admin and a regular user against a real scientific corpus, and deliberate abuse is handled gracefully.
**Depends on**: Phase 2
**Requirements**: UAT-01, UAT-02, UAT-03, UAT-04, UAT-05
**Success Criteria** (what must be TRUE):
  1. Admin + regular user register/login with correct role gating.
  2. Admin loads `llama3.1:8b` and Test Connection passes.
  3. Quantum arXiv PDFs + a Wikipedia URL + a small crawl + a TXT all ingest with correct chunk counts.
  4. Hard cross-document questions get correct answers with `[N]` citations; an out-of-corpus question is refused honestly.
  5. Break attempts (bad files, SSRF probes, dup-upload/delete, prompt-injection) are handled without corruption or crashes.
**Plans**: TBD

Plans:
- [ ] 03-01: TBD

### Phase 4: Security Audit
**Goal**: An independent security pass confirms the security fixes hold and surfaces nothing new unmitigated.
**Depends on**: Phase 3
**Requirements**: SEC-07
**Success Criteria** (what must be TRUE):
  1. Semgrep + /security-review on the full diff are clean or every finding is triaged/fixed.
  2. Re-run SSRF/land-grab/CORS/upload/injection/authz probes all pass.
  3. `SECURITY.md` produced with each original security finding marked fixed/mitigated/accepted.
**Plans**: TBD

Plans:
- [ ] 04-01: TBD

## Progress

**Execution Order:** 1 → 2 → 3 → 4

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Critical Fixes | 1/1 | Complete | 2026-06-16 |
| 2. Quality Fixes | 1/1 | Complete | 2026-06-16 |
| 3. User Simulation | 0/TBD | Not started | - |
| 4. Security Audit | 0/TBD | Not started | - |
