# RAG — Multi-User Document Q&A

A self-hosted web application that lets multiple authenticated users upload documents and chat with an AI that answers questions grounded in their content. Built for production — async, Dockerized, and configurable to use a local Ollama model or any cloud LLM provider.

## What It Does

- Upload PDFs, text files, or web URLs into a shared document library
- Chat with an AI assistant that answers questions using only your documents, with source citations
- Admin-managed shared library — only the admin adds or removes sources, but all authenticated users can query them
- Switch between local (Ollama) and cloud LLM providers (OpenAI, Anthropic, or any OpenAI-compatible endpoint) from the admin UI

## Stack

| Component | Technology |
|-----------|------------|
| Backend | FastAPI + Gunicorn (async workers) |
| Vector database | Qdrant (per-user collections) |
| Auth database | PostgreSQL + SQLAlchemy |
| Job queue | Redis + RQ (background ingestion) |
| Embeddings | `BAAI/bge-large-en-v1.5` via sentence-transformers |
| LLM | Ollama (bundled container) or OpenAI / Anthropic / generic |
| Frontend | Jinja2 templates + Bootstrap 5.3 |

## Prerequisites

- Docker and Docker Compose v2+
- 4 GB+ RAM and 10 GB+ free disk space (for models and embeddings)

Ollama runs inside the stack as its own container — no host installation needed. After the stack is up, pick and pull a model from the admin UI at `/admin/llm-settings`.

## Quick Start

### 1. Prepare host directories (one-time)

```bash
export DATA_ROOT=/storage/rag
sudo mkdir -p "$DATA_ROOT"/{postgres,qdrant,redis,uploads,ollama}
```

The stack binds named Docker volumes to these paths. Override `DATA_ROOT` in your `.env` if `/storage/rag` doesn't suit your setup.

### 2. Clone and configure

```bash
git clone https://github.com/jemplayer82/RAG.git
cd RAG
cp .env.example .env
# Only POSTGRES_PASSWORD is required — edit .env and set it
```

### 3. Start the stack

```bash
docker compose up -d
curl http://localhost:8000/api/health
```

Browse to **http://localhost:8000** and register an account. The first registered user becomes admin automatically.

> **Note:** A cloned repo auto-loads `docker-compose.override.yml`, which builds the image locally. To use the pre-built published image instead, run `docker compose -f docker-compose.yml up -d`.

## Deploying via Portainer

Make sure the host directories exist first (see Prerequisites), then:

1. Go to **Stacks → Add stack**, name it `rag`
2. Paste the contents of `docker-compose.yml` into the web editor — do **not** include `docker-compose.override.yml` (Portainer's paste mode has no Dockerfile access)
3. Add environment variables:
   - `POSTGRES_PASSWORD` — required
   - `RAG_PORT` — optional, defaults to `8000`
   - `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` — only if using a cloud LLM
4. Deploy — Portainer pulls `ghcr.io/jemplayer82/rag:latest`

## First-Time Setup

1. Register at `/register` — the first account is automatically promoted to admin
2. Log in
3. Visit `/admin/llm-settings` — select a provider and pull or configure a model
4. Go to **Add Sources** and upload a PDF or enter a URL
5. Wait for the ingestion job to finish (progress is visible in the upload UI)
6. Open **Chat** and start asking questions

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `POSTGRES_PASSWORD` | Yes | PostgreSQL password |
| `JWT_SECRET` | No | Auto-generated on first boot and persisted to `/storage/rag/uploads/.secrets.env` |
| `ENCRYPTION_KEY` | No | Auto-generated on first boot (same persistence as above) |
| `RAG_PORT` | No | Host port for the web UI (default: `8000`) |
| `DATA_ROOT` | No | Host base directory for data (default: `/storage/rag`) |
| `LLM_PROVIDER` | No | `ollama` (default), `openai`, `anthropic`, or `generic` |
| `LLM_MODEL` | No | Model name — leave blank to configure from the admin UI |
| `LLM_BASE_URL` | No | Ollama URL inside the stack (default: `http://ollama:11434`) |
| `OPENAI_API_KEY` | No | Required only when using OpenAI |
| `ANTHROPIC_API_KEY` | No | Required only when using Anthropic |
| `EMBED_MODEL` | No | HuggingFace embedding model (default: `BAAI/bge-large-en-v1.5`) |
| `EMBED_DEVICE` | No | `cpu` or `cuda` (default: `cpu`) |
| `CHUNK_SIZE` | No | Token chunk size for ingestion (default: `600`) |

See `.env.example` for the full list with descriptions.

## Architecture

```
Browser
  │
  ▼
FastAPI app (:8000)        ← the only host-published service
  ├── PostgreSQL            ← users, documents, job records
  ├── Qdrant                ← vector collections
  ├── Redis → rag-worker    ← background document ingestion
  └── Ollama                ← local LLM inference
```

Only the `rag` container exposes a port to the host. All other services are reachable only from inside the Docker network.

## Production Deployment (VPS)

```bash
git clone https://github.com/jemplayer82/RAG.git /opt/rag
cd /opt/rag
cp .env.example .env && nano .env
docker compose up -d
```

For HTTPS, place a TLS terminator (Caddy, Cloudflare Tunnel, or nginx) in front of port 8000. TLS is intentionally left to the host layer.

CI/CD is configured in `.github/workflows/deploy.yml`. Add `VPS_HOST`, `VPS_USER`, and `VPS_SSH_KEY` to your GitHub repository secrets to enable auto-deploy on push to `master`.

## Local Development (without Docker)

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # point OLLAMA_BASE_URL to localhost:11434

# Requires local PostgreSQL, Qdrant, and Redis
uvicorn app_fastapi:app --reload --port 8000
```
