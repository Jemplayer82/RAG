---
phase: 4
title: Security Audit
status: complete
date: 2026-06-16
asvs_level: 1
---

# Security Audit — RAG Assistant (gsd/stabilize-and-harden)

**Method:** (1) Semgrep (`p/python` + `p/security-audit`, 209 rules / 51 files) via Docker; (2) adversarial AI security review of the full `master..HEAD` diff; (3) **live re-probing** of the running app for each security fix.

## Original security requirements → status

| Req | Threat | Status | Evidence |
|-----|--------|--------|----------|
| SEC-01 | SSRF via URL ingest/crawl | ✅ Fixed + verified | Live probes blocked: direct (`qdrant:6333`, `postgres:5432`), IP (`169.254.169.254`), `localhost`, **and a redirect (httpbin 302 → `169.254.169.254`)**. Per-hop validation in `_safe_get`; Scrapling gated behind the validated final URL. |
| SEC-02 | Admin land-grab | ✅ Fixed + verified | `tester` (first registrant) did NOT get admin; `qadmin` (matches `ADMIN_USERNAME`) did; `interloper` (3rd) blocked. Never a 2nd admin. |
| SEC-03 | CORS credentialed-wildcard | ✅ Fixed | `allow_credentials=False` (Bearer-token API, no cookies). |
| SEC-04 | Upload abuse | ✅ Fixed + verified | 50 MB streamed cap, safe-filename fallback, extension-authoritative type. Malformed PDF → clean job error. |
| SEC-05 | Error-detail leakage | ✅ Fixed | Chat returns generic 500; detail logged server-side only. |
| SEC-06 | Injection / XSS | ✅ Fixed + verified | Ollama-pull JSON via `json.dumps`; sources-panel + citation-badge hrefs escaped & `^https?://`-guarded; prompt-injection doc did not hijack answers. |

## Phase-4 review findings (new, on the hardening diff)

| # | Finding | Severity | Disposition |
|---|---------|----------|-------------|
| 1 | Scrapling fallback followed redirects internally, bypassing per-hop SSRF validation | HIGH | **Fixed** — `_extract_text_scrapling` now resolves the chain via `_safe_get` and hands Scrapling the already-validated final URL; blocked hops abort. Verified live (httpbin redirect probe blocked). |
| 2 | Citation badges in `addMessageToHistory` set `href` from doc metadata without the `^https?://` scheme guard (the sources-panel fix missed this path) → stored `javascript:` XSS | MEDIUM | **Fixed** — same scheme guard applied to badge hrefs. |
| 3 | `ADMIN_USERNAME` read per-request from env; case-sensitive exact match | MEDIUM→info | **Accepted / no change.** Env vars are trusted (the exploit requires operator-controlled env changes — out of scope per standard precedent). Exact-match is intentional (case-insensitive would grant admin to case-variant usernames). |

## Semgrep results — 3 findings, all WARNING, none actionable

| Rule | Location | Disposition |
|------|----------|-------------|
| `wildcard-cors` | `app_fastapi.py:246` | Accepted — wildcard origin WITH credentials disabled is safe for a Bearer-token API (no cookies to exfiltrate). |
| `insecure-hash-algorithm-sha1` | `ingest_async.py:87` | False positive — sha1 is used for **deterministic point IDs**, not security. |
| `python-logger-credential-disclosure` | `secrets_bootstrap.py:103` | False positive — logs secret **names** (`["JWT_SECRET","ENCRYPTION_KEY"]`) + file path, never values. Pre-existing (not changed by this work). |

## Accepted residual risks (documented, not fixed)

- **DNS-rebinding TOCTOU** on the SSRF guard: `getaddrinfo` validates once, `requests` re-resolves at connect. Closing it needs IP-pinned connections; requires attacker-controlled DNS + admin access (high bar). Self-hosted single-admin threat model.
- **Scrapling UA-cloaking edge**: a site that serves a 302→internal *only* to Scrapling's user-agent (not to the `requests` probe) could still be followed. Narrow, admin-only, sophisticated.
- **JWT**: 30-day token, no revocation/logout invalidation (tracked as OPS-01, v2).
- **HTML pages** served without server-side auth (client-side JWT only) — acceptable because all JSON APIs enforce `get_current_user`/`require_admin`; pages contain no sensitive data.

## Verdict

No HIGH or MEDIUM vulnerability remains open. The two real review findings (1 HIGH, 1 MEDIUM) are fixed and the SSRF fix was re-verified live. Semgrep is clean of actionable issues. Residuals are documented and low-risk under the single-admin self-hosted threat model.
