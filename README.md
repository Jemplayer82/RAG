# RAG Assistant

A **domain-agnostic Retrieval-Augmented Generation (RAG)** system for building intelligent question-answering applications over custom documents.

**Current Version:** Flask single-user (v1.0)
**Production Version (coming):** FastAPI multi-user with Docker (v2.0)

---

## Features

✅ **Document Ingestion**
- PDF files (with PyMuPDF)
- Plain text files
- Web URLs (with automatic HTML parsing)

✅ **Hybrid Retrieval**
- Semantic search (BAAI/bge-large embeddings via ChromaDB)
- BM25 keyword matching (re-ranking)
- 60/40 weighted combination for accuracy

✅ **Chat Interface**
- Session-based conversation history
- Source citations with links
- Real-time answer streaming (Ollama LLM)

✅ **Document Management**
- Browse indexed documents
- View chunk count and metadata
- Delete documents
- Re-index on demand

✅ **User Configuration**
- Select active LLM model from running Ollama
- Change local data storage directory
- Persistent settings (JSON-based)

---

## Quick Start

### Prerequisites
- **Python 3.8+**
- **Ollama** running locally with a model pulled (e.g., `ollama pull mistral-small3.1`)
- **pip** package manager

### Installation

1. **Clone the repository:**
   ```bash
   git clone <your-repo-url> RAG
   cd RAG
   ```

2. **Create a virtual environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment (optional):**
   ```bash
   cp .env.example .env
   # Edit .env to customize paths, ports, models (optional)
   ```

5. **Start the Flask app:**
   ```bash
   python app.py
   ```

6. **Open in browser:**
   ```
   http://localhost:5000
   ```

---

## Usage

### Adding Documents

1. Go to **Add Sources** tab
2. Choose upload method:
   - **Upload File** — select PDF or TXT from disk
   - **Add URL** — paste a web page URL
3. Give it a title
4. Documents are automatically chunked, embedded, and indexed

### Querying

1. Go to **Chat** tab
2. Type your question
3. The system retrieves relevant document chunks and passes them to the LLM
4. Get an answer with source citations

### Managing Documents

- **Library** — see all indexed documents, chunk counts, and metadata
- **Re-index** — rebuild the ChromaDB index from cached sources
- **Delete** — remove a document (from Add Sources → Manage tab)

### Settings

- **LLM Model** — select from models available in your Ollama instance
- **Library Location** — change where documents are stored (default: `./data/`)

---

## Architecture

```
app.py                ← Flask web server
├── rag.py           ← Semantic + BM25 retrieval engine
├── ingest.py        ← PDF/TXT/URL ingestion pipeline
├── config.py        ← Configuration (models, paths, chunking)
├── settings.py      ← User preferences persistence
└── templates/       ← HTML templates (Bootstrap 5)
    ├── base.html
    ├── chat.html
    ├── library.html
    ├── upload.html
    └── settings.html
```

**Data Flow:**
```
User Question
    ↓
[chat.html] Submit via /ask endpoint
    ↓
[rag.py] Semantic search in ChromaDB
    ↓
[rag.py] BM25 re-rank
    ↓
[Ollama LLM] Generate answer with context
    ↓
Return answer + source citations
```

---

## Configuration

### `config.py`

**LLM & Embeddings:**
- `OLLAMA_BASE_URL` — Ollama endpoint (default: `http://localhost:11434`)
- `LLM_MODEL` — Active model name (default: `mistral-small3.1`)
- `EMBED_MODEL` — Embedding model (default: `BAAI/bge-large-en-v1.5`)
- `EMBED_DEVICE` — CPU or CUDA (default: `cpu`)

**Chunking:**
- `CHUNK_SIZE` — Token size per chunk (default: 600)
- `CHUNK_OVERLAP` — Token overlap between chunks (default: 100)

**Retrieval:**
- `TOP_K` — Initial chunks retrieved (default: 8)
- `RERANK_TOP_K` — Final chunks after BM25 (default: 5)

**Storage:**
- `CACHE_ROOT` — Base directory for all data (default: `./data/`)
  - `./data/raw/` — cached source files
  - `./data/chroma/` — ChromaDB vector index

### Environment Variables (`.env`)

```bash
RAG_DATA_DIR=/path/to/data           # Override default ./data/
OLLAMA_BASE_URL=http://localhost:11434
LLM_MODEL=mistral-small3.1
CHUNK_SIZE=600
DEBUG=false
```

---

## Dependencies

| Package | Version | Purpose |
|---|---|---|
| flask | 3.0.0 | Web framework |
| chromadb | 0.4.24 | Vector database |
| sentence-transformers | 3.0.1 | Embeddings |
| rank-bm25 | 0.2.2 | BM25 re-ranking |
| requests | 2.31.0 | HTTP client |
| beautifulsoup4 | 4.12.2 | HTML parsing |
| PyMuPDF | 1.24.0 | PDF extraction |
| python-dotenv | 1.0.1 | Environment management |

See `requirements.txt` for exact versions.

---

## Troubleshooting

### "Cannot connect to Ollama"
- Make sure Ollama is running: `ollama serve`
- Check `OLLAMA_BASE_URL` in config or `.env` matches your setup

### "No documents found in ChromaDB"
- Upload PDFs/TXT files via the **Add Sources** page
- Or run `python ingest.py` if using the CLI ingestion pipeline

### "Model not found in Ollama"
- Pull the model: `ollama pull mistral-small3.1`
- Refresh the model list in Settings → LLM Model

### ChromaDB lock errors
- ChromaDB uses SQLite internally; avoid running multiple instances simultaneously
- Check that no other processes are accessing `./data/chroma/`

---

## Next Steps (v2.0 - Production)

This is the **single-user Flask version**. The next major release will include:

- ✅ **FastAPI** (async, multi-worker)
- ✅ **PostgreSQL** (user authentication + JWT)
- ✅ **Qdrant** (distributed vector DB)
- ✅ **Redis** (job queue for async ingestion)
- ✅ **Docker Compose** (one-command deployment)
- ✅ **Nginx** (reverse proxy, HTTPS)

Designed to handle **50+ concurrent users** in production.

---

## License

MIT (or your preferred license)

---

## Contributing

For the v2.0 production migration, see `PLAN.md` (architecture overview).

Questions? Open an issue or check the deployment guide.
