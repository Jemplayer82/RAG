# Requirements: RAG Assistant — Stabilize & Harden

**Defined:** 2026-06-16
**Core Value:** A user can ask a question and get an accurate, citation-grounded answer from the ingested documents.

## v1 Requirements

Each maps to a roadmap phase. Derived from the 46-finding review (`.planning/codebase/CONCERNS.md`).

### Stability & Correctness (STAB)

- [ ] **STAB-01**: Ingestion job status + chunk count persist without client polling (worker writes the DB at end-of-job)
- [ ] **STAB-02**: A worker restart never falsely errors/auto-deletes a job that Redis still runs to success (reap only truly-stale)
- [ ] **STAB-03**: Re-ingesting a document is idempotent — stable Qdrant point IDs, no duplicate vectors
- [ ] **STAB-04**: Deleting a document removes exactly its own vectors — stored unique prefix, no same-title collisions or orphans
- [ ] **STAB-05**: Ingestion failures surface the real cause in the UI (not "Processing failed")
- [ ] **STAB-06**: The inline-fallback ingestion path uses a valid (fresh) DB session
- [ ] **STAB-07**: Embedding dimension is derived from the model; the worker caches its embedder and uses the configured device
- [ ] **STAB-08**: LLM provider calls handle empty/malformed responses without a 500 (Anthropic index, generic empty, settings null-email)

### Security (SEC)

- [ ] **SEC-01**: URL ingest + crawl block SSRF to loopback/private/link-local/metadata hosts and internal service names, including redirect and per-link hops
- [ ] **SEC-02**: Registration cannot grant admin once an admin already exists
- [ ] **SEC-03**: CORS is not credentialed-wildcard
- [ ] **SEC-04**: Uploads are size-limited, get safe filenames when `secure_filename` is empty, and validate type
- [ ] **SEC-05**: Error responses don't leak internal exception detail (logged server-side instead)
- [ ] **SEC-06**: No injection in the Ollama-pull JSON and no XSS in the chat sources panel
- [ ] **SEC-07**: Dedicated security audit (Semgrep + /security-review + targeted re-probe) passes or all findings are triaged

### Retrieval Quality (RAG)

- [ ] **RAG-01**: BM25/semantic fusion is normalized so neither score scale dominates re-ranking
- [ ] **RAG-02**: Citations reference only metadata that is actually set; `chat_history` is wired into the prompt or removed honestly

### User Simulation / UAT (UAT)

- [ ] **UAT-01**: Admin + regular user register/login with correct role gating (admin-only routes blocked for users)
- [ ] **UAT-02**: Admin loads an Ollama model via the UI and "Test Connection" passes
- [ ] **UAT-03**: Quantum corpus ingests (PDF batch + URL + crawl + TXT); Library shows correct chunk counts
- [ ] **UAT-04**: Hard cross-document questions are answered with correct `[N]` citations; grounding holds on out-of-corpus questions
- [ ] **UAT-05**: Library / delete / download / settings work; deliberate break attempts are handled gracefully

### Docs (DOCS)

- [ ] **DOCS-01**: `CLAUDE.md` and `QUICK_START.md` corrected to the FastAPI/Qdrant reality

## v2 Requirements

Deferred — tracked, not in this roadmap.

### Testing (TEST)
- **TEST-01**: Automated test suite (auth, ingestion, retrieval/rerank, provider dispatch)

### Operability (OPS)
- **OPS-01**: JWT revocation / shorter-lived tokens
- **OPS-02**: GPU passthrough for the dockerized Ollama (app answers)

## Out of Scope

| Feature | Reason |
|---------|--------|
| Full rewrite | Review verdict: architecture sound, findings patchable |
| Multi-tenant isolation | App is admin-centric by design |
| New product features | This milestone is stabilize-and-harden |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| STAB-01..08 | Phase 1 | Pending |
| SEC-01..05 | Phase 1 | Pending |
| RAG-01, RAG-02 | Phase 2 | Pending |
| SEC-06 | Phase 2 | Pending |
| DOCS-01 | Phase 2 | Pending |
| UAT-01..05 | Phase 3 | Pending |
| SEC-07 | Phase 4 | Pending |

**Coverage:**
- v1 requirements: 24 total (STAB×8, SEC×7, RAG×2, UAT×5, DOCS×1, +SEC-07 audit)
- Mapped to phases: 24
- Unmapped: 0 ✓

---
*Requirements defined: 2026-06-16*
*Last updated: 2026-06-16 after GSD brownfield init*
