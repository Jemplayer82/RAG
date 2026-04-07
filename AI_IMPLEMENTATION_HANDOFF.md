# RAG v2.0 — AI Implementation Handoff

> **Purpose:** This document gives another AI full context to continue building the RAG v2.0 production website. Everything already built is documented here, along with exactly what still needs to be created.

---

## Project Overview

A multi-user Retrieval-Augmented Generation (RAG) web application. Users register, upload documents (PDF, TXT, or URLs), and chat with an AI that answers questions grounded in their documents.

**GitHub Repository:** `https://github.com/Jemplayer82/RAG`

**Stack:**
- **Backend:** FastAPI + Gunicorn (async, 4 workers)
- **Vector DB:** Qdrant (per-user namespaced collections)
- **Auth DB:** PostgreSQL + SQLAlchemy + JWT
- **Job Queue:** Redis + RQ (background ingestion)
- **Embeddings:** `BAAI/bge-large-en-v1.5` via sentence-transformers
- **LLM:** Ollama (local, `mistral-small3.1`)
- **Frontend:** Jinja2 templates + Bootstrap 5.3
- **Deployment:** Docker Compose + GitHub Actions CI/CD → VPS

---

## What Is Already Built

### ✅ Core Python Files (Complete)

#### `config.py`
Central configuration. Reads from `.env` or environment variables.
Key variables:
```python
OLLAMA_BASE_URL = "http://localhost:11434"
LLM_MODEL = "mistral-small3.1"
EMBED_MODEL = "BAAI/bge-large-en-v1.5"
EMBED_DEVICE = "cpu"
CHUNK_SIZE = 600       # tokens
CHUNK_OVERLAP = 100    # tokens
TOP_K = 8              # chunks to retrieve
RERANK_TOP_K = 5       # after BM25 re-ranking
DATABASE_URL = "postgresql://rag:rag_password@postgres:5432/rag_db"
QDRANT_HOST = "qdrant"
QDRANT_PORT = 6333
REDIS_URL = "redis://redis:6379/0"
RAG_PROMPT_TEMPLATE = "..."  # full system prompt with citation instructions
```

#### `models.py`
SQLAlchemy ORM with 3 tables:
```python
User(id, username, email, hashed_password, is_active, created_at)
Document(id, user_id, title, doc_type, url, cached_path, chunks, qdrant_collection, created_at)
IngestionJob(id, user_id, document_id, rq_job_id, status, error_msg, created_at, completed_at)
```
`status` values: `queued`, `running`, `complete`, `error`

Functions: `get_engine()`, `get_session_local()`, `init_db()`

#### `ingest.py` (Flask v1 — reused by async version)
Core ingestion logic — all reusable:
- `chunk_text(text, title, doc_type, url, extra_meta)` → `List[Dict]`
- `ingest_pdf(file_path, title, url_hint)` → `(chunks, page_count)`
- `ingest_txt(file_path, title, url_hint)` → `(chunks, line_count)`
- `ingest_url(url, title)` → `(chunks, word_count)`
- `embed_and_store(chunks, collection, embedder, doc_id_prefix)` → `int`

#### `ingest_async.py`
Async ingestion for v2.0. Key class:
```python
class QdrantManager:
    def __init__(self, user_id: int)
    def upsert_chunks(self, chunks, embedder, doc_id_prefix) -> int
    def delete_document(self, doc_id_prefix) -> None
    def list_documents(self) -> List[Dict]
    def search(self, query_vector, top_k) -> List[Dict]
    def count(self) -> int
```
Async wrappers: `ingest_pdf_async()`, `ingest_txt_async()`, `ingest_url_async()`
Background job function: `run_ingestion_job(file_path, title, doc_type, user_id, url, doc_id_prefix) -> int`

#### `rag.py` (Flask v1 — still used for local mode)
Sync RAG engine:
- `query(question, chat_history)` → `{answer, sources, metadata}`
- `retrieve_sources(question, k)` → `List[Dict]` — ChromaDB + BM25 re-ranking
- `reset_retrieval_engine()` — force re-init after ingestion

#### `rag_async.py`
Async RAG engine for v2.0:
- `query_async(question, user_id, chat_history)` → `{answer, sources, metadata}`
- Uses Qdrant per-user collections + BM25 re-ranking
- Calls Ollama via `httpx.AsyncClient`

#### `worker.py`
Redis RQ worker. Run with `python worker.py` in its own container.
Listens on queue named `"ingestion"`.

#### `app_fastapi.py`
**Complete FastAPI app.** All routes implemented:

| Route | Auth | Status |
|-------|------|--------|
| `GET /` | No | Returns chat.html |
| `GET /login` | No | Returns login.html |
| `GET /register` | No | Returns register.html |
| `GET /library` | No | Returns library.html |
| `GET /upload` | No | Returns upload.html |
| `GET /settings` | No | Returns settings.html |
| `POST /api/auth/register` | No | Create account |
| `POST /api/auth/login` | No | Returns JWT token |
| `GET /api/auth/me` | JWT | Current user info |
| `POST /api/chat` | JWT | Query RAG engine |
| `GET /api/library` | JWT | List user's docs from PostgreSQL |
| `POST /api/sources` | JWT | Save file + enqueue RQ job |
| `GET /api/sources/jobs/{id}` | JWT | Poll job status |
| `DELETE /api/sources/{id}` | JWT | Remove from Qdrant + PostgreSQL |
| `GET /api/settings` | JWT | User profile |
| `POST /api/settings` | JWT | Update email |
| `GET /api/health` | No | Health check |

**Auth:** JWT Bearer tokens via `OAuth2PasswordBearer`. Token stored client-side.
**Database:** Initialized automatically on startup via `init_db()`.

#### `app.py`
Original Flask app — single user, local only. Keep for reference/fallback. **Not used in production.**

---

### ✅ Templates (Complete — Need Auth Updates)

All 5 existing templates use Bootstrap 5.3.2 + Bootstrap Icons. They currently make API calls to Flask routes (`/ask`, `/api/sources`, etc.).

**Templates that need updating for v2.0:**
1. `templates/base.html` — Add login/logout nav buttons, JWT header injection
2. `templates/chat.html` — Change `fetch('/ask', ...)` → `fetch('/api/chat', ...)` with JWT header
3. `templates/upload.html` — Add JWT header to all `fetch('/api/sources', ...)` calls
4. `templates/library.html` — Change to use `/api/library` JSON endpoint instead of Jinja server-side data
5. `templates/settings.html` — Update to use `/api/settings`

**Templates that need creating:**
1. `templates/login.html` — Login form
2. `templates/register.html` — Registration form

#### `static/style.css`
Complete. Custom Bootstrap overrides — chat bubbles, card styling, animations.

---

### ✅ `requirements.txt` (Complete)
```
flask==3.0.0
werkzeug==3.0.1
fastapi==0.115.0
uvicorn==0.30.0
gunicorn==22.0.0
chromadb==0.4.24
sentence-transformers==3.0.1
qdrant-client==1.9.0
rank-bm25==0.2.2
requests==2.31.0
httpx==0.27.0
beautifulsoup4==4.12.2
ollama==0.6.1
pdfplumber==0.10.3
python-dotenv==1.0.1
numpy<2.0
sqlalchemy==2.0.0
psycopg2-binary==2.9.9
pydantic==2.7.0
pyjwt==2.8.0
passlib==1.7.4
bcrypt==4.1.3
aiofiles==23.2.1
redis==5.0.0
rq==1.16.0
```

---

## What Still Needs To Be Built

### 🔴 Priority 1: Docker Setup (Required for deployment)

#### `Dockerfile`
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data directories
RUN mkdir -p data/raw/uploads data/chroma

EXPOSE 8000

CMD ["gunicorn", "app_fastapi:app", \
     "--workers", "4", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--timeout", "120", \
     "--keep-alive", "5", \
     "--log-level", "info"]
```

#### `docker-compose.yml`
```yaml
version: "3.9"

services:
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: rag_db
      POSTGRES_USER: rag
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U rag"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  qdrant:
    image: qdrant/qdrant:latest
    volumes:
      - qdrant_data:/qdrant/storage
    ports:
      - "6333:6333"
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data
    restart: unless-stopped

  rag:
    image: ghcr.io/jemplayer82/rag:latest
    build: .
    ports:
      - "8000:8000"
    env_file: .env
    environment:
      - DATABASE_URL=postgresql://rag:${POSTGRES_PASSWORD}@postgres:5432/rag_db
      - QDRANT_HOST=qdrant
      - REDIS_URL=redis://redis:6379/0
    volumes:
      - uploads_data:/app/data
    depends_on:
      postgres:
        condition: service_healthy
      qdrant:
        condition: service_started
      redis:
        condition: service_started
    restart: unless-stopped

  rag-worker:
    image: ghcr.io/jemplayer82/rag:latest
    build: .
    command: ["python", "worker.py"]
    env_file: .env
    environment:
      - DATABASE_URL=postgresql://rag:${POSTGRES_PASSWORD}@postgres:5432/rag_db
      - QDRANT_HOST=qdrant
      - REDIS_URL=redis://redis:6379/0
    volumes:
      - uploads_data:/app/data
    depends_on:
      - postgres
      - qdrant
      - redis
    restart: unless-stopped

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - certbot_data:/etc/letsencrypt
      - certbot_www:/var/www/certbot
    depends_on:
      - rag
    restart: unless-stopped

volumes:
  postgres_data:
  qdrant_data:
  redis_data:
  uploads_data:
  certbot_data:
  certbot_www:
```

#### `nginx.conf`
```nginx
events {
    worker_connections 1024;
}

http {
    upstream rag_app {
        server rag:8000;
    }

    # Rate limiting
    limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;

    # Redirect HTTP to HTTPS
    server {
        listen 80;
        server_name _;

        location /.well-known/acme-challenge/ {
            root /var/www/certbot;
        }

        location / {
            return 301 https://$host$request_uri;
        }
    }

    # HTTPS
    server {
        listen 443 ssl;
        server_name yourdomain.com;

        ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
        ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;
        ssl_protocols TLSv1.2 TLSv1.3;

        client_max_body_size 100M;

        location /api/ {
            limit_req zone=api burst=20 nodelay;
            proxy_pass http://rag_app;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_read_timeout 180s;
        }

        location / {
            proxy_pass http://rag_app;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }
    }
}
```

#### `.dockerignore`
```
venv/
__pycache__/
*.pyc
data/
.env
settings.json
.git/
*.md
*.bat
*.sh
```

#### `.env.docker` (template — never commit real values)
```bash
# PostgreSQL
POSTGRES_PASSWORD=CHANGE_ME_strong_password

# JWT
JWT_SECRET=CHANGE_ME_generate_64_random_chars

# Qdrant (Docker service name)
QDRANT_HOST=qdrant
QDRANT_PORT=6333

# Redis (Docker service name)
REDIS_URL=redis://redis:6379/0

# Ollama — use host.docker.internal to reach host machine
OLLAMA_BASE_URL=http://host.docker.internal:11434
LLM_MODEL=mistral-small3.1

# Embeddings
EMBED_MODEL=BAAI/bge-large-en-v1.5
EMBED_DEVICE=cpu

# App
DEBUG=false
FLASK_SECRET_KEY=CHANGE_ME_generate_64_random_chars
```

---

### 🔴 Priority 2: GitHub Actions CI/CD

#### `.github/workflows/deploy.yml`
```yaml
name: Build and Deploy

on:
  push:
    branches: [master]

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: jemplayer82/rag

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Login to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push Docker image
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: |
            ghcr.io/${{ env.IMAGE_NAME }}:latest
            ghcr.io/${{ env.IMAGE_NAME }}:${{ github.sha }}

  deploy:
    needs: build-and-push
    runs-on: ubuntu-latest

    steps:
      - name: Deploy to VPS
        uses: appleboy/ssh-action@v1.0.0
        with:
          host: ${{ secrets.VPS_HOST }}
          username: ${{ secrets.VPS_USER }}
          key: ${{ secrets.VPS_SSH_KEY }}
          script: |
            cd /opt/rag
            docker compose pull rag rag-worker
            docker compose up -d --no-deps rag rag-worker
            docker image prune -f
            echo "Deploy complete: $(date)"
```

**GitHub Secrets to add at `https://github.com/Jemplayer82/RAG/settings/secrets/actions`:**
| Secret | Value |
|--------|-------|
| `VPS_HOST` | IP address of your VPS |
| `VPS_USER` | SSH username (e.g., `root` or `ubuntu`) |
| `VPS_SSH_KEY` | Full contents of private SSH key (RSA or ED25519) |

---

### 🟡 Priority 3: Update HTML Templates for v2.0

The current templates talk to Flask routes. They need to be updated to:
1. Send `Authorization: Bearer <token>` header on all API calls
2. Use the new FastAPI API routes
3. Handle unauthenticated state (redirect to `/login`)

#### JWT Helper Pattern (add to every template or a shared JS file)
```javascript
function getToken() {
    return localStorage.getItem('rag_token');
}

function authHeaders() {
    return {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${getToken()}`
    };
}

function requireAuth() {
    if (!getToken()) {
        window.location.href = '/login';
    }
}
```

#### `templates/login.html` — Create new
```html
{% extends "base.html" %}
{% block title %}Login - RAG Assistant{% endblock %}
{% block content %}
<div class="row justify-content-center mt-5">
    <div class="col-md-4">
        <div class="card shadow">
            <div class="card-header bg-primary text-white">
                <h5 class="mb-0"><i class="bi bi-box-arrow-in-right"></i> Login</h5>
            </div>
            <div class="card-body">
                <form id="loginForm">
                    <div class="mb-3">
                        <label class="form-label">Username</label>
                        <input type="text" class="form-control" id="username" required autofocus>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">Password</label>
                        <input type="password" class="form-control" id="password" required>
                    </div>
                    <button type="submit" class="btn btn-primary w-100">Login</button>
                </form>
                <div id="loginError" class="alert alert-danger mt-3" style="display:none;"></div>
                <hr>
                <p class="text-center mb-0">No account? <a href="/register">Register</a></p>
            </div>
        </div>
    </div>
</div>
{% endblock %}
{% block scripts %}
<script>
document.getElementById('loginForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = document.getElementById('username').value;
    const password = document.getElementById('password').value;

    const formData = new URLSearchParams();
    formData.append('username', username);
    formData.append('password', password);

    const resp = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: formData
    });

    if (resp.ok) {
        const data = await resp.json();
        localStorage.setItem('rag_token', data.access_token);
        window.location.href = '/';
    } else {
        const err = await resp.json();
        const el = document.getElementById('loginError');
        el.textContent = err.detail || 'Login failed';
        el.style.display = 'block';
    }
});
</script>
{% endblock %}
```

#### `templates/register.html` — Create new
```html
{% extends "base.html" %}
{% block title %}Register - RAG Assistant{% endblock %}
{% block content %}
<div class="row justify-content-center mt-5">
    <div class="col-md-4">
        <div class="card shadow">
            <div class="card-header bg-success text-white">
                <h5 class="mb-0"><i class="bi bi-person-plus"></i> Create Account</h5>
            </div>
            <div class="card-body">
                <form id="registerForm">
                    <div class="mb-3">
                        <label class="form-label">Username</label>
                        <input type="text" class="form-control" id="username" required autofocus>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">Email</label>
                        <input type="email" class="form-control" id="email" required>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">Password</label>
                        <input type="password" class="form-control" id="password" required minlength="8">
                    </div>
                    <button type="submit" class="btn btn-success w-100">Create Account</button>
                </form>
                <div id="registerError" class="alert alert-danger mt-3" style="display:none;"></div>
                <div id="registerSuccess" class="alert alert-success mt-3" style="display:none;"></div>
                <hr>
                <p class="text-center mb-0">Already registered? <a href="/login">Login</a></p>
            </div>
        </div>
    </div>
</div>
{% endblock %}
{% block scripts %}
<script>
document.getElementById('registerForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const body = {
        username: document.getElementById('username').value,
        email: document.getElementById('email').value,
        password: document.getElementById('password').value,
    };

    const resp = await fetch('/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });

    if (resp.ok) {
        document.getElementById('registerSuccess').textContent = 'Account created! Redirecting to login...';
        document.getElementById('registerSuccess').style.display = 'block';
        setTimeout(() => window.location.href = '/login', 1500);
    } else {
        const err = await resp.json();
        const el = document.getElementById('registerError');
        el.textContent = err.detail || 'Registration failed';
        el.style.display = 'block';
    }
});
</script>
{% endblock %}
```

#### `templates/base.html` — Update nav section
Replace the existing `<div class="d-flex gap-2">` nav section with:
```html
<div class="d-flex gap-2 align-items-center">
    <a href="/" class="btn btn-sm btn-outline-light">
        <i class="bi bi-chat-dots"></i> Chat
    </a>
    <a href="/library" class="btn btn-sm btn-outline-light">
        <i class="bi bi-library"></i> Library
    </a>
    <a href="/upload" class="btn btn-sm btn-outline-light">
        <i class="bi bi-plus-square"></i> Add Sources
    </a>
    <a href="/settings" class="btn btn-sm btn-outline-light">
        <i class="bi bi-gear"></i> Settings
    </a>
    <span id="navUsername" class="text-white-50 small ms-2" style="display:none;"></span>
    <button id="logoutBtn" class="btn btn-sm btn-outline-warning" style="display:none;">
        <i class="bi bi-box-arrow-right"></i> Logout
    </button>
    <a id="loginNavBtn" href="/login" class="btn btn-sm btn-outline-light" style="display:none;">
        <i class="bi bi-box-arrow-in-right"></i> Login
    </a>
</div>
```
Add before `</body>`:
```html
<script>
(async () => {
    const token = localStorage.getItem('rag_token');
    if (token) {
        try {
            const resp = await fetch('/api/auth/me', {
                headers: { 'Authorization': `Bearer ${token}` }
            });
            if (resp.ok) {
                const user = await resp.json();
                document.getElementById('navUsername').textContent = user.username;
                document.getElementById('navUsername').style.display = 'inline';
                document.getElementById('logoutBtn').style.display = 'inline';
            } else {
                localStorage.removeItem('rag_token');
                document.getElementById('loginNavBtn').style.display = 'inline';
            }
        } catch {}
    } else {
        document.getElementById('loginNavBtn').style.display = 'inline';
    }

    document.getElementById('logoutBtn')?.addEventListener('click', () => {
        localStorage.removeItem('rag_token');
        window.location.href = '/login';
    });
})();
</script>
```

#### `templates/chat.html` — Update API call
Change the `fetch('/ask', ...)` call to use JWT and new route:
```javascript
const response = await fetch('/api/chat', {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${localStorage.getItem('rag_token')}`
    },
    body: JSON.stringify({ question, chat_history: [] })
});
if (response.status === 401) {
    window.location.href = '/login';
    return;
}
```
Add `requireAuth()` call at top of script block:
```javascript
if (!localStorage.getItem('rag_token')) window.location.href = '/login';
```

#### `templates/upload.html` — Update API calls
Add JWT headers to all fetch calls:
```javascript
// File upload:
const response = await fetch('/api/sources', {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${localStorage.getItem('rag_token')}` },
    body: formData   // no Content-Type — browser sets multipart boundary
});

// URL ingestion:
const response = await fetch('/api/sources', {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${localStorage.getItem('rag_token')}`
    },
    body: JSON.stringify({ type: 'url', url, title })
});

// Load sources — now uses /api/library:
const response = await fetch('/api/library', {
    headers: { 'Authorization': `Bearer ${localStorage.getItem('rag_token')}` }
});
// Response shape changed: data.documents (array of {id, title, doc_type, url, chunks, created_at})

// Delete source — change to use numeric id:
await fetch(`/api/sources/${id}`, {
    method: 'DELETE',
    headers: { 'Authorization': `Bearer ${localStorage.getItem('rag_token')}` }
});
```
Update `loadSources()` to handle new response shape:
```javascript
async function loadSources() {
    const response = await fetch('/api/library', {
        headers: { 'Authorization': `Bearer ${localStorage.getItem('rag_token')}` }
    });
    const data = await response.json();
    const sources = data.documents || [];
    // sources[i]: { id, title, doc_type, url, chunks, created_at }
}
```

Also add job polling after file/URL submit (sources are now queued, not immediate):
```javascript
async function pollJobStatus(jobId) {
    showStatus('file', 'progress', `Processing document (job #${jobId})...`);
    const poll = setInterval(async () => {
        const r = await fetch(`/api/sources/jobs/${jobId}`, {
            headers: { 'Authorization': `Bearer ${localStorage.getItem('rag_token')}` }
        });
        const job = await r.json();
        if (job.status === 'complete') {
            clearInterval(poll);
            showStatus('file', 'success', 'Document added successfully!');
        } else if (job.status === 'error') {
            clearInterval(poll);
            showStatus('file', 'error', `Error: ${job.error}`);
        }
    }, 2000);
}
```

#### `templates/library.html` — Switch to client-side rendering
The current library.html uses server-side Jinja2 template data. Update to fetch via API:
- Remove all `{% if documents %}` Jinja2 blocks
- On page load, call `GET /api/library` with JWT
- Render the table client-side with JavaScript (same structure as current HTML)

---

### 🟡 Priority 4: VPS First-Time Setup

Once you have a VPS (Ubuntu 22.04 recommended), run these commands:
```bash
# 1. Install Docker
curl -fsSL https://get.docker.com | sh
apt install docker-compose-plugin -y

# 2. Clone repo
git clone https://github.com/Jemplayer82/RAG.git /opt/rag
cd /opt/rag

# 3. Create .env from template
cp .env.docker .env
nano .env   # Fill in real passwords + your domain

# 4. Add your domain's A record pointing to VPS IP
# (Do this in your DNS provider before running certbot)

# 5. Get SSL certificate (replace yourdomain.com)
docker run --rm -p 80:80 \
  -v /opt/rag/certbot_data:/etc/letsencrypt \
  -v /opt/rag/certbot_www:/var/www/certbot \
  certbot/certbot certonly --standalone \
  -d yourdomain.com --email your@email.com --agree-tos

# 6. Update nginx.conf with your actual domain name

# 7. Start all services
docker compose up -d

# 8. Check everything is running
docker compose ps
curl http://localhost:8000/api/health
```

---

## File Tree — Final State

```
RAG/
├── .github/
│   └── workflows/
│       └── deploy.yml          ← 🔴 CREATE
├── templates/
│   ├── base.html               ← 🟡 UPDATE (add auth nav)
│   ├── chat.html               ← 🟡 UPDATE (JWT headers, new route)
│   ├── library.html            ← 🟡 UPDATE (client-side rendering)
│   ├── upload.html             ← 🟡 UPDATE (JWT headers, job polling)
│   ├── settings.html           ← 🟡 UPDATE (JWT headers)
│   ├── login.html              ← 🔴 CREATE
│   └── register.html           ← 🔴 CREATE
├── static/
│   └── style.css               ← ✅ complete
├── app_fastapi.py              ← ✅ complete
├── app.py                      ← ✅ keep (Flask fallback)
├── rag_async.py                ← ✅ complete
├── rag.py                      ← ✅ keep (Flask fallback)
├── ingest_async.py             ← ✅ complete
├── ingest.py                   ← ✅ keep (used by ingest_async)
├── models.py                   ← ✅ complete
├── worker.py                   ← ✅ complete
├── config.py                   ← ✅ complete
├── settings.py                 ← ✅ keep (Flask fallback)
├── requirements.txt            ← ✅ complete
├── Dockerfile                  ← 🔴 CREATE
├── docker-compose.yml          ← 🔴 CREATE
├── nginx.conf                  ← 🔴 CREATE
├── .dockerignore               ← 🔴 CREATE
├── .env.docker                 ← 🔴 CREATE (template only, no real values)
├── .env.example                ← ✅ exists
└── .gitignore                  ← ✅ exists (already excludes .env, data/)
```

---

## Key Design Decisions

1. **Per-user Qdrant collections** — `user_{id}` collection per user → zero cross-user data leakage
2. **JWT in localStorage** — Simple browser auth; token sent as `Authorization: Bearer <token>` header
3. **Redis RQ for background jobs** — Ingestion is slow (embedding); user gets `job_id` immediately, polls for completion
4. **Fallback to inline ingestion** — If Redis unavailable, `app_fastapi.py` runs ingestion synchronously
5. **GitHub Container Registry** — Free, integrated with GitHub Actions, no DockerHub account needed
6. **Gunicorn + Uvicorn workers** — 4 async workers handle 50+ concurrent users efficiently
7. **Nginx rate limiting** — 10 req/s per IP prevents abuse

---

## Verification Checklist

After full implementation:

- [ ] `docker compose up -d` starts all 6 services without errors
- [ ] `curl http://localhost:8000/api/health` returns `{"status":"ok"}`
- [ ] Visit `/register` → create account
- [ ] Visit `/login` → login → JWT stored in localStorage
- [ ] Upload a PDF → job queued → poll until `status: complete`
- [ ] Visit `/library` → document appears
- [ ] Visit `/` (chat) → ask question → answer returned with sources
- [ ] Register second user → verify they see separate empty library
- [ ] Push commit to GitHub master → GitHub Actions builds and deploys automatically
- [ ] `https://yourdomain.com` loads with valid SSL

---

## Notes for the Implementing AI

- **Start with Docker files** (Priority 1) — nothing deploys without them
- **Then update templates** (Priority 3) — the backend is complete, just frontend needs auth wiring
- **GitHub Actions** (Priority 2) — do last, after you verify Docker works locally
- **Do not modify** `app.py`, `rag.py`, or `ingest.py` — these are the working Flask v1 fallback
- **Do not commit** real `.env` values — only commit `.env.docker` as a template
- The `settings.py` file and `settings.json` are only used by the Flask v1 app; the FastAPI app uses PostgreSQL
