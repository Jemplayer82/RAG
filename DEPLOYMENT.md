# RAG Deployment Guide

How to run the RAG system locally and prepare for production.

---

## Local Development Setup

### 1. System Requirements

- **Python 3.8+** (tested on 3.10, 3.11)
- **Ollama** (https://ollama.ai) — for running LLMs locally
- **4+ GB RAM** (for embeddings + LLM inference)
- **Disk space**: 5-10 GB (for data, models, ChromaDB index)

### 2. Ollama Installation & Model Setup

**Download & install Ollama:**
- macOS/Linux/Windows: https://ollama.ai/download

**Pull a model:**
```bash
ollama pull mistral-small3.1    # Recommended (3.1B, fast)
ollama pull llama3.2            # Alternative (8B, slower)
ollama pull qwen2.5             # Alternative (7B, multilingual)
```

**Start Ollama server** (runs in background):
```bash
ollama serve
# Listens on http://localhost:11434
```

Verify it's running:
```bash
curl http://localhost:11434/api/tags
# Should return list of models
```

### 3. Clone & Install RAG

```bash
# Clone the repository
git clone <your-repo-url> RAG
cd RAG

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 4. Configure (Optional)

Create `.env` from the template:
```bash
cp .env.example .env
```

Edit `.env` to customize (or leave defaults):
```bash
# Use default ./data/ inside project:
# RAG_DATA_DIR=./data

# Or use absolute path:
# RAG_DATA_DIR=/path/to/rag/data

# Ollama endpoint (must match where it's running)
OLLAMA_BASE_URL=http://localhost:11434

# LLM model to use
LLM_MODEL=mistral-small3.1

# Embedding model (auto-downloads on first run)
EMBED_MODEL=BAAI/bge-large-en-v1.5

# Debug mode (verbose logging)
DEBUG=false
```

### 5. Start the Application

```bash
python app.py
# Flask server listens on http://127.0.0.1:5000
```

You should see:
```
 * Running on http://127.0.0.1:5000
```

Open http://localhost:5000 in your browser.

### 6. First-Time Setup in Web UI

1. **Go to Settings**
   - Verify your LLM model is listed
   - Check data directory path

2. **Go to Add Sources**
   - Upload a test PDF or TXT file
   - Or add a public URL (e.g., a blog post)

3. **Go to Chat**
   - Ask a question about your document
   - Verify you get an answer with citations

---

## Troubleshooting Local Setup

### Issue: "Cannot connect to Ollama"
**Symptom:** Error when submitting chat query
```
ConnectionError: Cannot connect to Ollama at http://localhost:11434
```

**Solution:**
1. Check Ollama is running: `ollama serve` (in another terminal)
2. Verify endpoint: `curl http://localhost:11434/api/tags`
3. Update `OLLAMA_BASE_URL` in `.env` if running elsewhere

### Issue: "Model not found"
**Symptom:** Chat returns "unknown model" error

**Solution:**
1. List available models: `curl http://localhost:11434/api/tags`
2. Pull missing model: `ollama pull mistral-small3.1`
3. Refresh model list in Settings UI → LLM Model

### Issue: "No documents indexed"
**Symptom:** Library is empty, chat finds no sources

**Solution:**
1. Upload a PDF/TXT via Add Sources tab
2. Wait 10-30s for embedding (progress shown in browser)
3. Check Library tab to verify document appears

### Issue: "ChromaDB locked" errors
**Symptom:** Multiple processes can't access vector DB

**Solution:**
1. Close all other RAG instances
2. Wait 5s and refresh
3. Check no background processes are using `./data/chroma/`

### Issue: "Out of memory" on first embedding
**Symptom:** System slows down when uploading large PDF

**Solution:**
1. Reduce `CHUNK_SIZE` in `.env` (try 400)
2. Use a smaller embedding model (not recommended)
3. Restart with more available RAM
4. Split large PDFs into smaller files

---

## Production Deployment (v2.0 - Coming)

Once FastAPI + Docker version is ready, deployment will be:

```bash
# 1. Build Docker image
docker build -t rag:latest .

# 2. Start all services
docker-compose up -d

# 3. Create first user
curl -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","email":"admin@test.com","password":"secure_pass"}'

# 4. Access via Nginx (HTTPS)
https://your-domain.com
```

See v2.0 release notes for full Docker Compose setup.

---

## Performance Tips

### Chunking
- **Larger chunks** (800 tokens) = fewer vectors, faster search, less context
- **Smaller chunks** (400 tokens) = more vectors, slower search, better precision
- Default 600 is balanced for most use cases

### Embedding Model
- **BAAI/bge-large** (335M) — default, good quality, ~1.3GB RAM
- Could swap to smaller (`bge-base`, 109M) if memory-constrained

### LLM Model
- **mistral-small3.1** (3.1B) — fast, good quality (recommended)
- **llama3.2** (8B) — slower, higher quality
- **qwen2.5** (7B) — multilingual, good for non-English docs

### Disk Usage
- `./data/raw/` — original files (PDFs, TXT)
- `./data/chroma/` — vector DB index (grows with document count)
- ~1-2MB per document on average

---

## Monitoring & Logs

View application logs:
```bash
# In same terminal as `python app.py`, or:
tail -f flask.log  # If redirected to file
```

Monitor ChromaDB size:
```bash
du -sh ./data/
du -sh ./data/chroma/
```

---

## Backup & Recovery

### Backup
```bash
# Save your documents + index
cp -r ./data /backup/rag-data-$(date +%Y%m%d)
```

### Restore
```bash
cp -r /backup/rag-data-20240101/data ./
```

### Rebuild Index
If index is corrupted:
```bash
# 1. Stop the app
# 2. Delete the index
rm -rf ./data/chroma/

# 3. Restart app — index auto-rebuilds from cached files
python app.py
# Takes time proportional to document size
```

---

## Next: Production Deployment (v2.0)

When ready to scale:
1. **Docker Compose** wraps all services
2. **PostgreSQL** replaces settings.json
3. **Qdrant** replaces ChromaDB
4. **Redis** handles background jobs
5. **Nginx** provides HTTPS + load balancing
6. **Gunicorn** runs FastAPI with multiple workers

See `PLAN.md` for architecture details.

---

## Support

- **Issues?** Check troubleshooting section above
- **Questions?** Review README.md for architecture overview
- **v2.0 planning?** See PLAN.md for production roadmap
