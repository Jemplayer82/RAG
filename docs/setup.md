# `$ rag` — deployment and setup details

Part of the [RAG README](../README.md).

Make sure the host directories exist first (see the README quick start).

## `[ deploying via portainer ]`

Make sure the host directories exist first (see Prerequisites), then:

1. Go to **Stacks → Add stack**, name it `rag`
2. Paste the contents of `docker-compose.yml` into the web editor — do **not** include `docker-compose.override.yml` (Portainer's paste mode has no Dockerfile access)
3. Add environment variables:
   - `POSTGRES_PASSWORD` — required
   - `RAG_PORT` — optional, defaults to `8000`
   - `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` — only if using a cloud LLM
4. Deploy — Portainer pulls `ghcr.io/jemplayer82/rag:latest`

---

## `[ first-time setup ]`

1. Register at `/register` — the first account is automatically promoted to admin
2. Log in
3. Visit `/admin/llm-settings` — select a provider and pull or configure a model
4. Go to **Libraries** and create at least one library (e.g. "My Documents")
5. Go to **Add Sources**, select the library, and upload a PDF or enter a URL
6. Wait for the ingestion job to finish (progress is visible in the upload UI)
7. Open **Chat**, pick a library from the dropdown, and start asking questions

> [!NOTE]
> On a fresh install, a starter library named "My Library" is created automatically when the first admin registers, so you can skip step 4 and go straight to adding sources.

---

## `[ libraries ]`

Each library is an independent Qdrant collection — documents in different libraries never cross-contaminate. The admin creates and manages libraries; any authenticated user can choose which one to query.

**Common workflows:**

- Admin creates a "Spinal Cord Injury" library and a "Jones Act" library
- Uploads the relevant documents to each
- Users pick a library in the Chat header before asking questions — only that library's documents are searched

**Persistence:** Library data (vectors, documents, uploaded files) is stored on bind-mounted host paths and survives container rebuilds, image updates, and `docker compose down`.

---

## `[ environment variables ]`

| Variable | Required | Description |
|----------|----------|-------------|
| `POSTGRES_PASSWORD` | Yes | PostgreSQL password |
| `JWT_SECRET` | No | Auto-generated on first boot and persisted to `/storage/rag/uploads/.secrets.env` |
| `ENCRYPTION_KEY` | No | Auto-generated on first boot (same persistence as above) |
| `RAG_PORT` | No | Host port for the web UI (default: `8000`) |
| `DATA_ROOT` | No | Host base directory for data (default: `/storage/rag`) |
| `LLM_PROVIDER` | No | `ollama` (default), `openai`, `anthropic`, or `generic` |
| `LLM_MODEL` | No | Model name — leave blank to configure from the admin UI |
| `LLM_BASE_URL` | No | Fixed to the in-stack Ollama (`http://ollama:11434`) in `docker-compose.yml`. For a remote Ollama or cloud endpoint, set the Base URL per-provider in the admin UI instead. |
| `OPENAI_API_KEY` | No | Required only when using OpenAI |
| `ANTHROPIC_API_KEY` | No | Required only when using Anthropic |
| `EMBED_MODEL` | No | HuggingFace embedding model (default: `BAAI/bge-large-en-v1.5`) |
| `EMBED_DEVICE` | No | `cpu` or `cuda` (default: `cpu`) |
| `CHUNK_SIZE` | No | Token chunk size for ingestion (default: `600`) |

See `.env.example` for the full list with descriptions.

---

## `[ architecture ]`

```
Browser
│
▼
FastAPI app (:8000) ← the only host-published service
├── PostgreSQL         ← users, libraries, documents, job records
├── Qdrant             ← isolated per-library vector collections
├── Redis → rag-worker ← background document ingestion
└── Ollama             ← local LLM inference
```

Only the `rag` container exposes a port to the host. All other services are reachable only from inside the Docker network.

---

## `[ production deployment (vps) ]`

```bash
$ git clone https://github.com/jemplayer82/RAG.git /opt/rag
$ cd /opt/rag
$ cp .env.example .env && nano .env
$ docker compose up -d
```

For HTTPS, place a TLS terminator (Caddy, Cloudflare Tunnel, or nginx) in front of port 8000. TLS is intentionally left to the host layer.

CI/CD is configured in `.github/workflows/deploy.yml`. Add `VPS_HOST`, `VPS_USER`, and `VPS_SSH_KEY` to your GitHub repository secrets to enable auto-deploy on push to `master`.

---

## `[ local development (without docker) ]`

```bash
$ python -m venv venv && source venv/bin/activate
$ pip install -r requirements.txt
$ cp .env.example .env
# Add OLLAMA_BASE_URL=http://localhost:11434 to .env if Ollama runs on the host

# Requires local PostgreSQL, Qdrant, and Redis
$ uvicorn app_fastapi:app --reload --port 8000
```

---

