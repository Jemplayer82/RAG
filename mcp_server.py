#!/usr/bin/env python3
"""
RAG MCP Server — exposes the RAG knowledge base as MCP tools.

Runs locally as a stdio MCP server; talks to the deployed RAG API over HTTP.
Zero changes required to the Docker container.

Requirements
------------
- Python 3.10+ (uses `X | None` type annotations). The MCP runs on YOUR local
  interpreter, not the 3.11 Docker image — make sure it's 3.10 or newer.
- Deps isolated in requirements-mcp.txt (kept out of the server image):
      pip install -r requirements-mcp.txt

Setup
-----
1. Set environment variables (any of these combos work):
       RAG_BASE_URL    — base URL of your RAG app (default: http://localhost:8000)
       RAG_TOKEN       — pre-generated JWT (browser DevTools → localStorage → rag_token)
       RAG_USERNAME    — username to auto-login (used if RAG_TOKEN is not set)
       RAG_PASSWORD    — password to auto-login

   Prefer RAG_USERNAME + RAG_PASSWORD: the server re-logins transparently when a
   token expires. A static RAG_TOKEN works too but cannot self-refresh.

2. Add to Claude Desktop (claude_desktop_config.json) or Claude Code (.claude/mcp.json):
   {
     "mcpServers": {
       "rag": {
         "command": "python",
         "args": ["C:/path/to/mcp_server.py"],
         "env": {
           "RAG_BASE_URL": "http://100.112.40.124:8000",
           "RAG_USERNAME": "admin",
           "RAG_PASSWORD": "<password>"
         }
       }
     }
   }

   For Claude Code you can also run:
       claude mcp add rag -- python /path/to/mcp_server.py
   then set the env vars in .claude/mcp.json.

Tools
-----
  query            — ask a question (any authenticated user)
  list_libraries   — list libraries + IDs (any authenticated user)
  list_documents   — list documents (any authenticated user)
  add_file         — ingest a LOCAL file (admin only)
  add_url          — ingest a web URL (admin only)
  get_job_status   — check an ingestion job (admin only)
  delete_document  — remove a document (admin only)
"""

import os
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

# ── Config ─────────────────────────────────────────────────────────────────

RAG_BASE_URL = os.environ.get("RAG_BASE_URL", "http://localhost:8000").rstrip("/")
_token: str = os.environ.get("RAG_TOKEN", "")
_username: str = os.environ.get("RAG_USERNAME", "")
_password: str = os.environ.get("RAG_PASSWORD", "")

# Shared client → connection reuse across calls.
_client = httpx.Client(timeout=30)

# MIME type per supported extension (cosmetic — the API routes on the `type`
# form field, not the file's content-type — but good hygiene).
_MIME = {
    "pdf": "application/pdf",
    "txt": "text/plain",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

# ── Auth helpers ────────────────────────────────────────────────────────────

def _get_token() -> str:
    """Return a bearer token, logging in lazily if only creds were provided."""
    global _token
    if _token:
        return _token
    if _username and _password:
        resp = _client.post(
            f"{RAG_BASE_URL}/api/auth/login",
            data={"username": _username, "password": _password},
            timeout=15,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Login failed ({resp.status_code}) — check RAG_USERNAME / RAG_PASSWORD."
            )
        _token = resp.json()["access_token"]
        return _token
    raise RuntimeError("No credentials. Set RAG_TOKEN, or RAG_USERNAME + RAG_PASSWORD.")


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_get_token()}"}


def _api(method: str, path: str, *, timeout: float = 30, _retry: bool = True, **kwargs):
    """
    Authenticated JSON request — the single HTTP chokepoint.

    - Surfaces FastAPI `{"detail": ...}` errors as clean messages.
    - On 401, clears the cached token and (if creds exist) re-logins and retries
      once, so token expiry is transparent.
    - Forwards json=/data=/files= through **kwargs for JSON, form, and multipart.
    """
    global _token
    resp = _client.request(
        method, f"{RAG_BASE_URL}{path}", headers=_headers(), timeout=timeout, **kwargs
    )

    if resp.status_code == 401:
        _token = ""
        if _retry and _username and _password:
            return _api(method, path, timeout=timeout, _retry=False, **kwargs)
        raise RuntimeError(
            "Authentication failed (401) — token expired or invalid. "
            "Set RAG_USERNAME/RAG_PASSWORD for automatic refresh, or update RAG_TOKEN."
        )

    if resp.status_code >= 400:
        detail = ""
        try:
            detail = resp.json().get("detail", "")
        except Exception:
            detail = (resp.text or "")[:200]
        raise RuntimeError(f"API error {resp.status_code}: {detail or resp.reason_phrase}")

    return resp.json()


def _doc_chunks(document_id) -> int | None:
    """Look up a document's indexed chunk count (None if not found)."""
    if document_id is None:
        return None
    try:
        docs = _api("GET", "/api/library").get("documents", [])
        for d in docs:
            if d.get("id") == document_id:
                return d.get("chunks", 0)
    except Exception:
        pass
    return None

# ── MCP server ──────────────────────────────────────────────────────────────

mcp = FastMCP(
    "RAG Knowledge Base",
    instructions=(
        "Tools for querying and managing a self-hosted RAG knowledge base. "
        "Use `list_libraries` first to discover available libraries and their IDs, "
        "then `query` to ask questions. Ingestion/management tools (add_file, "
        "add_url, get_job_status, delete_document) require admin credentials."
    ),
)

# ── Tools ───────────────────────────────────────────────────────────────────

@mcp.tool()
def query(question: str, library_ids: list[int] | None = None) -> str:
    """
    Ask a question against the RAG knowledge base.

    Performs semantic + BM25 retrieval, then returns an LLM-generated answer with
    numbered source citations.

    Args:
        question:    Natural-language question to answer.
        library_ids: Libraries to search. Omit to search ALL libraries; pass a
                     list of IDs (from list_libraries) to narrow the search.
    """
    # Empty selection means "everything" — the API would otherwise fall back to
    # only the oldest library, so expand to all IDs explicitly.
    if not library_ids:
        libs = _api("GET", "/api/libraries").get("libraries", [])
        if not libs:
            return "No libraries exist yet. Create one and add documents first."
        library_ids = [l["id"] for l in libs]

    # LLM generation on a local model can be slow — give it room.
    result = _api("POST", "/api/chat", json={"question": question, "library_ids": library_ids}, timeout=180)
    answer = result.get("answer", "(no answer returned)")
    sources = result.get("sources", [])

    out = answer
    if sources:
        out += "\n\nSources:"
        for s in sources:
            url = s.get("anchor_url") or s.get("page_url") or s.get("url") or ""
            out += f"\n  [{s['index']}] {s.get('citation', 'Unknown')} ({s.get('doc_type', '')})"
            excerpt = (s.get("excerpt") or "").strip()
            if excerpt:
                out += f'\n       "{excerpt}…"'
            if url:
                out += f"\n       {url}"
    return out


@mcp.tool()
def list_libraries() -> str:
    """
    List all libraries in the knowledge base with their document counts.
    Returns library IDs needed for other tools.
    """
    libs = _api("GET", "/api/libraries").get("libraries", [])
    if not libs:
        return "No libraries found. Create one at /admin/libraries."
    lines = [f"{len(libs)} library(s) available:\n"]
    for lib in libs:
        desc = f" — {lib['description']}" if lib.get("description") else ""
        lines.append(f"  ID {lib['id']}: {lib['name']} ({lib.get('document_count', 0)} docs){desc}")
    return "\n".join(lines)


@mcp.tool()
def list_documents(library_id: int | None = None, search: str = "") -> str:
    """
    List documents in the knowledge base.

    Args:
        library_id: Filter to a specific library. Omit for all libraries.
        search:     Optional substring filter on document title (case-insensitive).
    """
    path = "/api/library"
    if library_id is not None:
        path += f"?library_id={library_id}"
    docs = _api("GET", path).get("documents", [])

    if search:
        q = search.lower()
        docs = [d for d in docs if q in (d.get("title") or "").lower()]

    if not docs:
        return "No documents found." + (f" (search: '{search}')" if search else "")

    lines = [f"{len(docs)} document(s):\n"]
    for d in docs:
        lines.append(
            f"  ID {d['id']}: [{d['doc_type']}] {d['title']}"
            f" — {d.get('chunks', 0)} chunks, {d.get('status', 'unknown')}"
        )
    return "\n".join(lines)


@mcp.tool()
def add_file(file_path: str, library_id: int, title: str | None = None) -> str:
    """
    Ingest a LOCAL file (PDF, TXT, DOC, DOCX) into a library. Requires admin credentials.

    Reads the file from this machine's filesystem and uploads it. The file is
    chunked, embedded, and stored asynchronously — poll get_job_status() for progress.

    Args:
        file_path:  Path to a local file (max 50 MB).
        library_id: Target library ID (from list_libraries).
        title:      Display name. Defaults to the file name (without extension).
    """
    p = Path(file_path).expanduser()
    if not p.is_file():
        raise RuntimeError(f"File not found: {p}")
    ext = p.suffix.lower().lstrip(".")
    if ext not in _MIME:
        raise RuntimeError(f"Unsupported type '.{ext}'. Allowed: pdf, txt, doc, docx.")

    data = {"type": ext, "title": title or p.stem, "library_id": str(library_id)}
    # Read into memory so a transparent 401-retry can resend (no consumed pointer).
    files = {"file": (p.name, p.read_bytes(), _MIME[ext])}

    result = _api("POST", "/api/sources", data=data, files=files, timeout=120)
    job_id = result.get("job_id")
    return (
        f"File queued for ingestion.\n"
        f"  Document ID: {result.get('document_id')}\n"
        f"  Job ID:      {job_id}\n"
        f"Check progress: get_job_status({job_id})"
    )


@mcp.tool()
def add_url(url: str, title: str, library_id: int, crawl: bool = False, max_pages: int = 20) -> str:
    """
    Add a web URL as a source document to a library. Requires admin credentials.

    The page is fetched, chunked, embedded, and stored asynchronously.
    Poll get_job_status() for progress.

    Args:
        url:        URL to fetch and ingest.
        title:      Display name for this document.
        library_id: Target library ID (from list_libraries).
        crawl:      Follow links from this page and ingest them too (default: False).
        max_pages:  Max pages to crawl when crawl=True (default: 20).
    """
    form: dict[str, str] = {
        "type": "url",
        "url": url,
        "title": title,
        "library_id": str(library_id),
        "crawl": "true" if crawl else "false",
    }
    if crawl:
        form["max_pages"] = str(max_pages)

    result = _api("POST", "/api/sources", data=form, timeout=60)
    job_id = result.get("job_id")
    return (
        f"URL queued for ingestion.\n"
        f"  Document ID: {result.get('document_id')}\n"
        f"  Job ID:      {job_id}\n"
        f"Check progress: get_job_status({job_id})"
    )


@mcp.tool()
def get_job_status(job_id: int) -> str:
    """
    Check the status of a background ingestion job. Requires admin credentials.

    Args:
        job_id: Job ID returned by add_file / add_url, or shown on the Activity page.
    """
    result = _api("GET", f"/api/sources/jobs/{job_id}")
    status = result.get("status", "unknown")

    if status == "complete":
        chunks = _doc_chunks(result.get("document_id"))
        return (
            f"Job {job_id}: complete — {chunks} chunks indexed."
            if chunks is not None
            else f"Job {job_id}: complete."
        )
    if status == "error":
        return f"Job {job_id}: error — {result.get('error') or 'unknown error'}"
    if status == "running":
        return f"Job {job_id}: currently processing…"
    return f"Job {job_id}: {status}"


@mcp.tool()
def delete_document(document_id: int) -> str:
    """
    Delete a document from the knowledge base. Requires admin credentials.
    Removes the source file, all vector chunks, and ingestion records.

    Args:
        document_id: ID of the document to delete (from list_documents).
    """
    _api("DELETE", f"/api/sources/{document_id}")
    return f"Document {document_id} deleted."


# ── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
