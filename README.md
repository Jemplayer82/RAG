# RAG — Multi-User Document Q&A

A self-hosted web app that lets multiple users upload documents (PDF, TXT, or URLs) and chat with an AI that answers questions grounded in their content.

## What it does

- **Upload** PDFs, text files, or web URLs into a personal document library
- **Chat** with an AI assistant that answers questions using only your documents, with source citations
- **Shared library** — admin uploads documents into a single collection that all authenticated users can query. Multi-user means multiple authenticated users can chat against the shared library; only the admin can add or remove sources
- **Flexible LLM** — use a local Ollama model or a cloud provider (OpenAI, Anthropic)

## Stack

| Component | Technology |
|-----------|-----------|
| Backend | FastAPI + Gunicorn (4 async workers) |
| Vector DB | Qdrant (per-user collections) |
| Auth DB | PostgreSQL + SQLAlchemy |
| Job queue | Redis + RQ (background ingestion) |
| Embeddings | `BAAI/bge-large-en-v1.5` via sentence-transformers |
| LLM | Ollama (self-contained container) or OpenAI / Anthropic / generic |
| Frontend | Jinja2 templates + Bootstrap 5.3 |

## Prerequisites

- **Docker** and **Docker Compose** v2+
- 4 GB+ RAM, 10 GB+ free disk space (for models + embeddings)

Ollama runs inside the stack as a container — no host install needed. After the stack is up, pick and pull a model from the admin settings UI at `/admin/llm-settings`.

## Prerequisites (one-time, on every host)

```bash
# Pick a host directory. Default is /storage/rag. Override with DATA_ROOT.
export DATA_ROOT=/storage/rag         # or /var/lib/rag, /opt/rag/data, etc.
sudo mkdir -p "$DATA_ROOT"/{postgres,qdrant,redis,uploads,ollama}
```

The stack uses named Docker volumes that bind-mount to `$DATA_ROOT/*` on the host, so these directories must exist before the first deploy. Portainer lists them in its Volumes UI per-stack; the data lives at predictable host paths for backup/restore. Override `DATA_ROOT` in your `.env` (or as a Portainer stack env var) if `/storage/rag` doesn't suit your host layout.

## Quick Start (Docker Compose)

```bash
git clone https://github.com/jemplayer82/RAG.git
cd RAG
cp .env.example .env
# Edit .env — only POSTGRES_PASSWORD must be set
docker compose up -d
curl http://localhost:8000/api/health
```

A cloned repo auto-loads `docker-compose.override.yml`, which builds the image locally from the `Dockerfile`. To skip the build and use the published image instead, run `docker compose -f docker-compose.yml up -d`.

`JWT_SECRET` and `ENCRYPTION_KEY` auto-generate on first boot and persist to `/storage/rag/uploads/.secrets.env`, so they survive image pulls and stack restarts. Override them in `.env` only if you need to pin specific values.

Browse to **http://localhost:8000** and register an account.

## Quick Start (Portainer)

Make sure the host directories exist before deploying (see Prerequisites above) — named volumes with bind-mount `driver_opts` don't auto-create them.

1. In Portainer → **Stacks** → **Add stack** → name it `rag`.
2. Paste the contents of [`docker-compose.yml`](docker-compose.yml) into the web editor. (Do **not** include `docker-compose.override.yml` — that file triggers a local build and will fail in Portainer's paste-compose mode, which has no Dockerfile.)
3. Under **Environment variables**, add:
   - `POSTGRES_PASSWORD` — required, any 32-char alphanumeric string
   - (optional) `RAG_PORT` if you need something other than `8000`
   - (optional) `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` if you plan to use a cloud LLM
4. **Deploy the stack**. Portainer will pull `ghcr.io/jemplayer82/rag:latest` (no build step).
5. Browse to `http://<host>:8000` and register.

The app writes `JWT_SECRET` and `ENCRYPTION_KEY` into the data volume on first boot. Pulling a newer image via Portainer keeps them — they live on the host at `/storage/rag/uploads/.secrets.env`, not inside the container. Back that file up to survive a volume wipe.

## First-Time Setup

1. Register a user account at `/register` — **the first registered user automatically becomes admin**
2. Log in — your JWT token is stored in the browser
3. Visit `/admin/llm-settings` and pick / pull an Ollama model (or configure a cloud provider)
4. Go to **Add Sources** and upload a PDF or enter a URL
5. Wait for the ingestion job to complete (visible in the upload UI)
6. Go to **Chat** and ask questions about your documents

### Admin: Configure LLM Provider

An admin account is the first registered user. Visit `/admin/llm-settings` to:
- Switch between Ollama, OpenAI, Anthropic, or any OpenAI-compatible endpoint
- For Ollama: browse installed models and pull new ones directly from the UI
- Set API keys (stored encrypted in the database)
- Tune temperature and max tokens

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `POSTGRES_PASSWORD` | Yes | PostgreSQL password |
| `JWT_SECRET` | No | Auto-generated + persisted on first boot if unset |
| `ENCRYPTION_KEY` | No | Auto-generated + persisted on first boot if unset |
| `RAG_PORT` | No | Host port to publish the web UI on (default: `8000`) |
| `DATA_ROOT` | No | Host directory that holds data subdirs (default: `/storage/rag`) |
| `LLM_PROVIDER` | No | `ollama` (default), `openai`, `anthropic`, `generic` |
| `LLM_MODEL` | No | Model name. Leave blank to pick from the admin UI |
| `LLM_BASE_URL` | No | Ollama URL (default: `http://ollama:11434` inside the stack) |
| `OPENAI_API_KEY` | No | Required only when using OpenAI |
| `ANTHROPIC_API_KEY` | No | Required only when using Anthropic |
| `EMBED_MODEL` | No | HuggingFace embedding model (default: `BAAI/bge-large-en-v1.5`) |
| `EMBED_DEVICE` | No | `cpu` or `cuda` (default: `cpu`) |
| `CHUNK_SIZE` | No | Token chunk size for ingestion (default: `600`) |

See [`.env.example`](.env.example) for the full list with descriptions.

## Architecture

```
Browser
  │
  ▼
FastAPI app (:8000)        ← the only host-published service
  ├── PostgreSQL            ← users, documents, job records
  ├── Qdrant                ← per-user vector collections
  ├── Redis → rag-worker    ← background document ingestion
  └── Ollama                ← local LLM inference
```

Only the `rag` container exposes a port to the host. Every other service (`postgres`, `qdrant`, `redis`, `ollama`, `rag-worker`) is reachable only from inside the Docker network. Data is persisted to `/storage/rag/` on the host.

## Production Deployment (VPS)

```bash
# On the server:
git clone https://github.com/jemplayer82/RAG.git /opt/rag
cd /opt/rag
cp .env.example .env && nano .env   # fill in real secrets
docker compose up -d
```

For HTTPS on a public domain, put a TLS terminator (Caddy, Cloudflare Tunnel, or an OS-level nginx outside the stack) in front of port 8000. Nothing inside the stack handles TLS — that concern is deliberately left to the host.

CI/CD is configured in [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) — add `VPS_HOST`, `VPS_USER`, and `VPS_SSH_KEY` to your GitHub repository secrets to enable auto-deploy on push to `master`.

## Local Development (without Docker)

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # point OLLAMA_BASE_URL to localhost:11434

# Requires local PostgreSQL, Qdrant, and Redis
uvicorn app_fastapi:app --reload --port 8000
```
