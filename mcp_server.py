#!/usr/bin/env python3
"""
RAG MCP Server — exposes the RAG knowledge base as MCP tools.

Runs locally as a stdio MCP server; talks to the deployed RAG API over HTTP.
Zero changes required to the Docker container.

Setup
-----
1. Install the MCP SDK alongside your existing deps:
       pip install mcp httpx

2. Set environment variables (any of these combos work):
       RAG_BASE_URL    — base URL of your RAG app (default: http://localhost:8000)
       RAG_TOKEN       — pre-generated JWT (grab from browser DevTools → localStorage → rag_token)
       RAG_USERNAME    — username to auto-login (used if RAG_TOKEN is not set)
       RAG_PASSWORD    — password to auto-login

3. Add to Claude Desktop (claude_desktop_config.json) or Claude Code (.claude/mcp.json):
   {
     "mcpServers": {
       "rag": {
         "command": "python",
         "args": ["C:/path/to/mcp_server.py"],
         "env": {
           "RAG_BASE_URL": "http://100.112.40.124:8000",
           "RAG_TOKEN": "<your-30-day-jwt>"
         }
       }
     }
   }

   For Claude Code you can also add it via:
       claude mcp add rag -- python /path/to/mcp_server.py
   then set the env vars in .claude/mcp.json.
"""

import os
import httpx
from mcp.server.fastmcp import FastMCP

# ── Config ─────────────────────────────────────────────────────────────────

RAG_BASE_URL = os.environ.get("RAG_BASE_URL", "http://localhost:8000").rstrip("/")
_token: str = os.environ.get("RAG_TOKEN", "")
_username: str = os.environ.get("RAG_USERNAME", "")
_password: str = os.environ.get("RAG_PASSWORD", "")

# ── Auth helpers ────────────────────────────────────────────────────────────

def _get_token() -> str:
    global _token
    if _token:
        return _token
    if _username and _password:
        resp = httpx.post(
            f"{RAG_BASE_URL}/api/auth/login",
            data={"username": _username, "password": _password},
            timeout=15,
        )
        resp.raise_for_status()
        _token = resp.json()["access_token"]
        return _token
    raise RuntimeError(
        "No credentials. Set RAG_TOKEN, or RAG_USERNAME + RAG_PASSWORD."
    )


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_get_token()}"}


def _api(method: str, path: str, **kwargs):
    """Make an authenticated request; clears the cached token on 401 so the
    next call re-logs-in automatically."""
    global _token
    resp = httpx.request(
        method, f"{RAG_BASE_URL}{path}", headers=_headers(), timeout=30, **kwargs
    )
    if resp.status_code == 401:
        _token = ""
        raise RuntimeError("Authentication failed — token may be expired. Check RAG_TOKEN.")
    resp.raise_for_status()
    return resp.json()

# ── MCP server ──────────────────────────────────────────────────────────────

mcp = FastMCP(
    "RAG Knowledge Base",
    instructions=(
        "Tools for querying and managing a self-hosted RAG knowledge base. "
        "Use `list_libraries` first to discover available libraries and their IDs, "
        "then `query` to ask questions. Admin tools (add_url, delete_document) "
        "require admin credentials."
    ),
)

# ── Tools ───────────────────────────────────────────────────────────────────

@mcp.tool()
def query(
    question: str,
    library_ids: list[int] | None = None,
) -> str:
    """
    Ask a question against the RAG knowledge base.

    Performs semantic + BM25 retrieval across the specified libraries (or all
    libraries if none given), then returns an LLM-generated answer with
    numbered source citations.

    Args:
        question:    Natural-language question to answer.
        library_ids: Optional list of library IDs to search. Leave empty to
                     search across all libraries.
    """
    body: dict = {"question": question}
    if library_ids:
        body["library_ids"] = library_ids

    result = _api("POST", "/api/chat", json=body)
    answer = result.get("answer", "(no answer returned)")
    sources = result.get("sources", [])

    out = answer
    if sources:
        out += "\n\nSources:"
        for s in sources:
            url = s.get("anchor_url") or s.get("page_url") or s.get("url") or ""
            line = f"\n  [{s['index']}] {s.get('citation', 'Unknown')} ({s.get('doc_type', '')})"
            if url:
                line += f"\n       {url}"
            out += line
    return out


@mcp.tool()
def list_libraries() -> str:
    """
    List all libraries in the knowledge base with their document counts.
    Returns library IDs needed for other tools.
    """
    data = _api("GET", "/api/libraries")
    libs = data.get("libraries", [])
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
    url = "/api/library"
    if library_id is not None:
        url += f"?library_id={library_id}"
    data = _api("GET", url)
    docs = data.get("documents", [])

    if search:
        q = search.lower()
        docs = [d for d in docs if q in (d.get("title") or "").lower()]

    if not docs:
        return "No documents found." + (f" (search: '{search}')" if search else "")

    lines = [f"{len(docs)} document(s):\n"]
    for d in docs:
        status = d.get("status", "unknown")
        chunks = d.get("chunks", 0)
        lines.append(
            f"  ID {d['id']}: [{d['doc_type']}] {d['title']}"
            f" — {chunks} chunks, {status}"
        )
    return "\n".join(lines)


@mcp.tool()
def add_url(
    url: str,
    title: str,
    library_id: int,
    crawl: bool = False,
    max_pages: int = 20,
) -> str:
    """
    Add a web URL as a source document to a library. Requires admin credentials.

    The page is fetched, chunked, embedded, and stored in the vector database
    asynchronously. Use get_job_status() to check progress.

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

    resp = httpx.post(
        f"{RAG_BASE_URL}/api/sources",
        headers=_headers(),
        data=form,
        timeout=30,
    )
    if resp.status_code == 401:
        raise RuntimeError("Authentication failed. Admin credentials required.")
    resp.raise_for_status()
    result = resp.json()
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
    Check the status of a background ingestion job.

    Args:
        job_id: Job ID returned by add_url or shown on the Activity page.
    """
    result = _api("GET", f"/api/sources/jobs/{job_id}")
    status = result.get("status", "unknown")
    chunks = result.get("chunks", 0)
    error = result.get("error") or ""

    if status == "complete":
        return f"Job {job_id}: complete — {chunks} chunks indexed."
    if status == "error":
        return f"Job {job_id}: error — {error or 'unknown error'}"
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
