# Coding Conventions

**Analysis Date:** 2026-06-16

## Module-Level Docstrings

Every Python module opens with a triple-quoted docstring summarizing purpose and, where applicable, a route inventory. Follow this pattern exactly — it's the first thing in the file, before imports.

```python
"""
FastAPI web application for RAG v2.0 — multi-user, production-ready.
- Async request handling (non-blocking Gunicorn + Uvicorn workers)
- PostgreSQL user accounts + JWT auth

Routes:
  POST   /api/auth/login        — Get JWT token
  GET    /api/health            — Server health check
"""
```

Files with docstrings: `app_fastapi.py`, `rag_async.py`, `ingest.py`, `ingest_async.py`, `llm_provider.py`, `models.py`, `config.py`.

## Section Banner Comments

All major logical groupings inside a file are separated by `# ===` banners. Use exactly this format — 76 `=` characters, title in ALL CAPS centered on the line below:

```python
# ============================================================================
# RETRIEVAL: Semantic search + BM25 re-ranking (per user)
# ============================================================================
```

Observed section names: `LOGGING`, `AUTH SETUP`, `DATABASE DEPENDENCY`, `REDIS / RQ SETUP`, `PYDANTIC SCHEMAS`, `FASTAPI APP`, `ROUTES: Auth`, `ROUTES: Chat`, `ROUTES: Library`, `ROUTES: Source Management`, `ROUTES: Settings`, `ROUTES: Admin — LLM Provider Settings`, `ROUTES: Health`, `ERROR HANDLERS`, `STARTUP / SHUTDOWN`, `TOKEN COUNTING`, `CHUNKER: Generic text chunker`, `INGESTION: PDF`, `INGESTION: Web URL`, `CHROMADB: Embed and store chunks`, `API KEY ENCRYPTION`, `DB INIT HELPERS`, `MAIN ENTRY POINT`, `PROVIDER IMPLEMENTATIONS`, `ENVIRONMENT VARIABLE FALLBACK`, `QDRANT MANAGER: Per-user vector store operations`.

## Naming Patterns

**Files:**
- `snake_case.py` for all Python modules (`app_fastapi.py`, `rag_async.py`, `ingest_async.py`, `llm_provider.py`, `secrets_bootstrap.py`)
- No test files exist

**Functions:**
- `snake_case` for all functions and methods (`hash_password`, `verify_password`, `create_access_token`, `get_current_user`, `require_admin`, `chunk_text`, `ingest_pdf`, `ingest_url`, `build_index`)
- Private/internal helpers prefixed with `_` (`_retrieve_sources_sync`, `_get_embedder`, `_call_openai`, `_call_anthropic`, `_resolve_ollama_base_url`, `_extract_links`, `_normalize_url`, `_fetch_page`)
- Async versions of sync functions use `_async` suffix (`query_async`, `query_llm_async`, `run_ingestion_job`)

**Variables:**
- `snake_case` for all local variables
- `UPPER_SNAKE_CASE` for module-level constants: `SECRET_KEY`, `ALGORITHM`, `TOKEN_EXPIRE_DAYS`, `EMBED_DIM`, `CACHE_ROOT`, `RAW_DIR`, `CHUNK_SIZE`, `TOP_K`, `RERANK_TOP_K`
- Module-level singletons use `_` prefix: `_embedder`, `_cipher`

**ORM Models:**
- `PascalCase` for SQLAlchemy model classes: `User`, `Document`, `IngestionJob`, `LLMProviderConfig`
- Table names are `snake_case` plural: `"users"`, `"documents"`, `"ingestion_jobs"`, `"llm_provider_configs"`

**Pydantic Schemas:**
- `PascalCase` with descriptive suffix: `UserCreate`, `UserResponse`, `Token`, `ChatRequest`, `ChatResponse`, `LLMSettingsUpdate`

## Logging

**Setup:** `logging.getLogger(__name__)` at module level in every file. Formatting configured once at startup in `app_fastapi.py`:

```python
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="[%(levelname)s] %(name)s — %(message)s"
)
logger = logging.getLogger(__name__)
```

**Bracketed subsystem tags:** All log messages use `[TAG]` prefixes in f-strings to identify the subsystem. Use the established tag for the file you're in:

| File | Tags in use |
|------|-------------|
| `app_fastapi.py` | `[AUTH]`, `[CHAT]`, `[SOURCES]`, `[JOB]`, `[ADMIN]` |
| `rag_async.py` | `[RAG]`, `[EMBEDDER]` |
| `ingest.py` | `[PDF]`, `[TXT]`, `[DOCX]`, `[DOC]`, `[URL]`, `[CRAWL]`, `[SOURCES]`, `[SKIP]`, `[OK]`, `[ERROR]` |
| `ingest_async.py` | `[QDRANT]` |
| `llm_provider.py` | `[LLM]` |

```python
logger.info(f"[AUTH] Registered: {user.username}{' (admin)' if is_first_user else ''}")
logger.warning(f"[SOURCES] Redis unavailable, running inline: {e}")
logger.error(f"[CHAT] Error: {e}")
```

Use `logger.info` for normal operations, `logger.warning` for recoverable failures or fallbacks, `logger.error` for unexpected failures.

## FastAPI Dependency Injection

Three dependency functions are defined in `app_fastapi.py` and composed with `Depends()`. Use them — don't re-implement auth inline:

```python
def get_db() -> Session:           # yields SQLAlchemy session, auto-closes
def get_current_user(...) -> User: # validates JWT, returns User or raises 401
def require_admin(...) -> User:    # wraps get_current_user, raises 403 if not admin
```

Route signatures follow this order: request body → `Depends(get_current_user)` or `Depends(require_admin)` → `Depends(get_db)`:

```python
@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ...

@app.post("/api/sources")
async def add_source(
    ...,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    ...
```

## Pydantic Schemas

All request/response bodies are Pydantic `BaseModel` subclasses defined in `app_fastapi.py` in the `PYDANTIC SCHEMAS` section. Use `from_attributes = True` (not `orm_mode`) on response schemas that serialize ORM objects:

```python
class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    is_admin: bool
    created_at: datetime

    class Config:
        from_attributes = True
```

Use `Optional[T] = None` for nullable fields; use typed defaults (`= []`, `= 0.3`) rather than leaving fields untyped.

## Async Patterns

All route handlers are `async def`. Blocking I/O (model inference, Qdrant calls, file parsing) is offloaded with `asyncio.to_thread()`:

```python
# In rag_async.py
sources = await asyncio.to_thread(_retrieve_sources_sync, question, user_id, TOP_K)

# In app_fastapi.py (inline job fallback)
count = await asyncio.to_thread(
    run_ingestion_job,
    file_path=file_path, title=title, ...
)
```

Sync helper functions that do blocking work are named without `async` and called only inside `asyncio.to_thread()`. Never call them directly from an async route.

File I/O inside routes uses `aiofiles`:

```python
async with aiofiles.open(dest, "wb") as f:
    content = await file.read()
    await f.write(content)
```

## Error Handling

All error responses use `HTTPException` — never return raw dicts with error keys:

```python
raise HTTPException(status_code=400, detail="Question cannot be empty")
raise HTTPException(status_code=401, detail="Not authenticated")
raise HTTPException(status_code=403, detail="Admin access required")
raise HTTPException(status_code=404, detail="Document not found")
raise HTTPException(status_code=503, detail="Cannot connect to LLM...")
```

In routes, catch broad exceptions at the boundary, log with `[TAG]`, then re-raise as `HTTPException`:

```python
except Exception as e:
    logger.error(f"[CHAT] Error: {e}")
    if "connect" in str(e).lower():
        raise HTTPException(status_code=503, detail="Cannot connect to LLM...")
    raise HTTPException(status_code=500, detail=str(e))
```

In lower-level modules (`ingest.py`, `llm_provider.py`), raise `ValueError` or `RuntimeError` with descriptive messages — let the route layer convert them to HTTP errors.

## Configuration / Environment Variables

All `os.getenv()` calls are centralized in `config.py`. No other module reads env vars directly for configuration values — they import named constants from `config.py`:

```python
# config.py
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://rag:rag_password@postgres:5432/rag_db")
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "600"))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# other modules
from config import OLLAMA_BASE_URL, LLM_MODEL, DEBUG, CHUNK_SIZE
```

The only exceptions: `app_fastapi.py` reads `JWT_SECRET` directly (auth concern, not app config) and `models.py` reads `ENCRYPTION_KEY` directly (crypto concern). Both are set by `secrets_bootstrap.py` before any module loads.

## Import Organization

**Order within a file:**
1. Standard library (`asyncio`, `logging`, `os`, `json`, `pathlib`, `typing`, `datetime`)
2. Third-party packages (`fastapi`, `pydantic`, `sqlalchemy`, `redis`, `jwt`, `httpx`, etc.)
3. Local application modules (`from config import ...`, `from models import ...`, `from rag_async import ...`)

Blank line between each group. No path aliases — all local imports are by module name.

Lazy imports (inside functions) are used when: (a) the import is heavy and not always needed (`from openai import AsyncOpenAI` inside `_call_openai`), or (b) avoiding circular imports (`from models import decrypt_api_key` inside provider functions, `import rag_async` inside the embed-device route).

## Function Design

**Docstrings:** Public functions and all functions with non-obvious behavior have docstrings with `Args:` and `Returns:` sections:

```python
def chunk_text(text: str, title: str, doc_type: str, url: str = "", extra_meta: Dict = None) -> List[Dict]:
    """
    Chunk text into CHUNK_SIZE token segments with CHUNK_OVERLAP overlap.

    Args:
        text: Full document text
        title: Human-readable document name
        doc_type: "pdf", "txt", or "url"
        url: Source URL (if applicable)
        extra_meta: Additional metadata fields to include

    Returns:
        List of dicts: {text, metadata}
    """
```

Short private helpers (`_normalize_url`, `_get_cipher`) may omit docstrings when the name is self-explanatory.

**Guard clauses:** Return or raise early rather than nesting:

```python
if not req.question.strip():
    raise HTTPException(status_code=400, detail="Question cannot be empty")
if not sources:
    return {"answer": "...", "sources": [], "metadata": {...}}
```

**Return type:** Functions that return dicts inline (route handlers returning JSON) do not declare a `response_model` when the shape is ad-hoc; use `response_model=SomePydanticClass` when the response schema is stable and documented.

## Code Style

**Formatting:** No formatter config file present (no `.prettierrc`, `pyproject.toml` with Black, or `setup.cfg`). Style is consistent with PEP 8: 4-space indentation, ~100 character lines, f-strings for interpolation throughout.

**F-strings:** Used everywhere for string interpolation — no `%` formatting or `.format()` calls in new code.

**Type hints:** Used on function signatures throughout (`str`, `int`, `Optional[str]`, `List[Dict]`, `Dict`, `Tuple[List[Dict], int]`). Not used on local variables.

**Inline comments:** Used sparingly for non-obvious logic, especially in `rag_async.py` and `ingest.py` where algorithm steps benefit from explanation:

```python
# BM25 re-ranking
# Overlap: keep last CHUNK_OVERLAP tokens of previous buffer
# Reload if device changed
```

---

*Convention analysis: 2026-06-16*
*Update when patterns change*
