# `$ rag` — MCP server

Part of the [RAG README](../README.md).

## `[ mcp server ]`

`mcp_server.py` (included in this repo) exposes the RAG knowledge base as an MCP server. Connect it to Claude Desktop or Claude Code to query, ingest, and manage the knowledge base without opening the web UI — the server talks to the deployed RAG API over HTTP, so no changes to the Docker stack are needed.

### 1. install deps (local machine only)

```bash
pip install -r requirements-mcp.txt   # mcp + httpx — kept out of the Docker image
```

Requires **Python 3.10+** on the machine running Claude (not inside Docker).

### 2. add to Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "rag": {
      "command": "python",
      "args": ["C:/path/to/RAG/mcp_server.py"],
      "env": {
        "RAG_BASE_URL": "http://YOUR_HOST:8000",
        "RAG_USERNAME": "admin",
        "RAG_PASSWORD": "yourpassword"
      }
    }
  }
}
```

### 2. add to Claude Code

```bash
claude mcp add rag -- python /path/to/RAG/mcp_server.py
```

Then set the env vars in `.claude/mcp.json` under the `rag` entry.

### environment variables

| Variable | Description |
|----------|-------------|
| `RAG_BASE_URL` | Base URL of the RAG app (default: `http://localhost:8000`) |
| `RAG_USERNAME` | Username for auto-login (recommended — token self-refreshes on expiry) |
| `RAG_PASSWORD` | Password for auto-login |
| `RAG_TOKEN` | Pre-generated JWT from `localStorage.rag_token` (static — use creds instead) |

### available tools

| Tool | Auth | Description |
|------|------|-------------|
| `query` | any user | Ask a question; searches one or all libraries |
| `list_libraries` | any user | List libraries with IDs and document counts |
| `list_documents` | any user | List documents; optional library and title filters |
| `add_file` | admin | Ingest a local file (PDF, TXT, DOC, DOCX) |
| `add_url` | admin | Ingest a web URL; optional site crawl |
| `get_job_status` | admin | Check ingestion job progress |
| `delete_document` | admin | Remove a document and its vectors |

---

