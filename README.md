# RAG — Multi-User Document Q&A

A self-hosted web app that lets multiple users upload documents (PDF, TXT, or URLs) and chat with an AI that answers questions grounded in their content.

## What it does

- **Upload** PDFs, text files, or web URLs into a personal document library
- **Chat** with an AI assistant that answers questions using only your documents, with source citations
- **Multi-user** — each account's documents are fully isolated
- **Flexible LLM** — use a local Ollama model or a cloud provider (OpenAI, Anthropic)

## Stack

| Component | Technology |
|-----------|-----------|
| Backend | FastAPI + Gunicorn (4 async workers) |
| Vector DB | Qdrant (per-user collections) |
| Auth DB | PostgreSQL + SQLAlchemy |
| Job queue | Redis + RQ (background ingestion) |
| Embeddings | `BAAI/bge-large-en-v1.5` via sentence-transformers |
| LLM (default) | Ollama — `mistral-small3.1` |
| Frontend | Jinja2 templates + Bootstrap 5.3 |
| Reverse proxy | Nginx with rate limiting + optional TLS |

## Prerequisites

- **Docker** and **Docker Compose** v2+
- **Ollama** running on the host (if using local LLM): `ollama pull mistral-small3.1`
- 4 GB+ RAM, 10 GB+ free disk space (for models + embeddings)

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/jemplayer82/RAG.git
cd RAG

# 2. Configure environment
cp .env.example .env
```

Open `.env` and set **at minimum** these three values:
```env
POSTGRES_PASSWORD=<strong-password>
JWT_SECRET=<64-random-chars>
ENCRYPTION_KEY=<fernet-key>
```

Generate values with:
```bash
# JWT_SECRET
python3 -c "import secrets; print(secrets.token_hex(32))"

# ENCRYPTION_KEY (Fernet-compatible — 32 random bytes, base64url-encoded)
python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

```bash
# 3. Start all services
docker compose up -d

# 4. Verify everything is healthy
docker compose ps
curl http://localhost:8000/api/health
```

Browse to **http://localhost** and register an account.

## First-Time Setup

1. Register a user account at `/register`
2. Log in — your JWT token is stored in the browser
3. Go to **Add Sources** and upload a PDF or enter a URL
4. Wait for the ingestion job to complete (visible in the upload UI)
5. Go to **Chat** and ask questions about your documents

### Admin: Configure LLM Provider

An admin account is the first registered user. Visit `/admin/llm-settings` to:
- Switch between Ollama, OpenAI, Anthropic, or any OpenAI-compatible endpoint
- Set API keys (stored encrypted in the database)
- Tune temperature and max tokens

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `POSTGRES_PASSWORD` | Yes | PostgreSQL password |
| `JWT_SECRET` | Yes | JWT signing key (64 random chars) |
| `ENCRYPTION_KEY` | Yes | Fernet key for encrypting stored API keys |
| `LLM_PROVIDER` | No | `ollama` (default), `openai`, `anthropic`, `generic` |
| `LLM_MODEL` | No | Model name (default: `mistral-small3.1`) |
| `LLM_BASE_URL` | No | Ollama URL (default: `http://host.docker.internal:11434`) |
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
Nginx (80/443)          ← rate limiting, TLS termination
  │
  ▼
FastAPI app (:8000)     ← REST API + Jinja2 templates
  ├── PostgreSQL         ← users, documents, job records
  ├── Qdrant             ← per-user vector collections
  └── Redis → Worker     ← background document ingestion
```

All six services are defined in [`docker-compose.yml`](docker-compose.yml). Data is persisted in named Docker volumes (`postgres_data`, `qdrant_data`, `uploads_data`).

## Production Deployment (VPS)

```bash
# On the server:
git clone https://github.com/jemplayer82/RAG.git /opt/rag
cd /opt/rag
cp .env.example .env && nano .env   # fill in real secrets + domain

# Update nginx.conf with your actual domain name, then:
docker compose up -d
```

For HTTPS, point your domain's A record to the server IP, then run Certbot to issue a Let's Encrypt certificate. Update the `ssl_certificate` paths in [`nginx.conf`](nginx.conf).

CI/CD is configured in [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) — add `VPS_HOST`, `VPS_USER`, and `VPS_SSH_KEY` to your GitHub repository secrets to enable auto-deploy on push to `master`.

## Local Development (without Docker)

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # point OLLAMA_BASE_URL to localhost:11434

# Requires local PostgreSQL, Qdrant, and Redis
uvicorn app_fastapi:app --reload --port 8000
```
