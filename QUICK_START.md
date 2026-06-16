# 🚀 Quick Start

RAG v2.0 is a **FastAPI + Postgres + Qdrant + Redis/RQ + Ollama** stack, run with Docker Compose. (Earlier docs described a Flask/ChromaDB v1 — that is gone; this is the current app.)

## 1. Prerequisites
- Docker + Docker Compose
- ~6 GB free RAM (embedding model + services); a GPU is optional

## 2. Configure
From a clone of this repo:

```bash
cp .env.example .env
# Edit .env — the only REQUIRED value is POSTGRES_PASSWORD.
# JWT_SECRET and ENCRYPTION_KEY auto-generate + persist on first boot.
# DATA_ROOT defaults to /storage/rag (Linux). On other hosts point it somewhere real, e.g.:
#   DATA_ROOT=C:/Users/you/Projects/RAG/data
# Optional but recommended: ADMIN_USERNAME=<your-username> to pin who can become admin.
```

Create the data subdirectories `DATA_ROOT` points at before the first run:
```bash
mkdir -p "$DATA_ROOT"/{postgres,qdrant,redis,uploads,ollama}
```

## 3. Start
```bash
docker compose up -d --build     # builds locally via docker-compose.override.yml
docker compose ps                # all 6 services should be healthy
curl http://localhost:8000/api/health
```

Open **http://localhost:8000**.

## 4. First-run setup
1. **Register** the first account — it becomes the admin (or the account matching `ADMIN_USERNAME`).
2. Go to **Admin → LLM Provider Settings** (`/admin/llm-settings`).
   - For local inference: provider **ollama**, base URL `http://ollama:11434`, then **Pull** a model (e.g. `llama3.1:8b`) and **Test Connection**.
   - Or pick `openai` / `anthropic` / `generic` and paste an API key (stored Fernet-encrypted).
3. **Add Sources** (`/upload`, admin only) — upload PDF/TXT/DOCX or add a URL (optionally crawl). Ingestion runs in the background worker; the page polls until complete.
4. **Chat** (`/`) — ask questions; answers cite sources `[N]`. Any registered user can chat against the admin's library.

## 5. Useful commands
```bash
docker compose logs -f rag           # app logs
docker compose logs -f rag-worker    # ingestion worker
docker compose exec rag-ollama-1 ollama list   # installed models
docker compose down                  # stop (data persists in DATA_ROOT)
```

## Notes
- `/api/docs` (interactive API) is available only when `DEBUG=true`.
- Embedding model `BAAI/bge-large-en-v1.5` (~1.3 GB) downloads on first ingestion — the first job is slow.
- Uploads are capped at 50 MB; URL ingestion is SSRF-guarded (internal hosts/IPs blocked).
