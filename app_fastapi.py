"""
FastAPI web application for RAG v2.0 — multi-user, production-ready.
- Async request handling (non-blocking Gunicorn + Uvicorn workers)
- PostgreSQL user accounts + JWT auth
- Qdrant vector DB with per-user namespace isolation
- Redis RQ for background document ingestion
- Serves Jinja2 HTML templates + static files

Routes:
  GET    /                      — Chat page (requires auth)
  GET    /login                 — Login page
  GET    /register              — Register page
  GET    /library               — Library page (requires auth)
  GET    /upload                — Upload page (requires auth)
  GET    /settings              — Settings page (requires auth)

  POST   /api/auth/register     — Create account
  POST   /api/auth/login        — Get JWT token
  GET    /api/auth/me           — Current user info

  POST   /api/chat              — Query RAG (auth required)
  GET    /api/library           — List user's documents (auth required)
  POST   /api/sources           — Add document / URL (auth required)
  DELETE /api/sources/{id}      — Remove document (auth required)
  GET    /api/sources/jobs/{id} — Poll ingestion job status (auth required)

  GET    /api/settings          — User preferences (auth required)
  POST   /api/settings          — Update preferences (auth required)
  GET    /api/health            — Server health check
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import jwt
import aiofiles
from fastapi import (
    FastAPI, Depends, HTTPException, UploadFile, File,
    Form, Request, status
)
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from passlib.context import CryptContext
from pydantic import BaseModel
from redis import Redis
from rq import Queue
from sqlalchemy.orm import Session
from werkzeug.utils import secure_filename

from config import (
    OLLAMA_BASE_URL, LLM_MODEL, EMBED_MODEL,
    DEBUG, CACHE_ROOT, REDIS_URL
)
from models import (
    User, Document, IngestionJob, LLMProviderConfig,
    get_session_local, init_db, encrypt_api_key
)
from rag_async import query_async
from ingest_async import QdrantManager, run_ingestion_job

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="[%(levelname)s] %(name)s — %(message)s"
)
logger = logging.getLogger(__name__)

# ============================================================================
# AUTH SETUP
# ============================================================================

SECRET_KEY = os.getenv("JWT_SECRET", "change_me_in_production_with_random_string")
ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 30

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(username: str) -> str:
    expire = datetime.utcnow() + timedelta(days=TOKEN_EXPIRE_DAYS)
    return jwt.encode({"sub": username, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


# ============================================================================
# DATABASE DEPENDENCY
# ============================================================================

SessionLocal = get_session_local()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    """Extract and validate user from JWT Bearer token."""
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Invalid token")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def get_admin_user_id(db: Session) -> int:
    """Return the first admin user's ID for public queries. Raises 503 if none exists."""
    admin = db.query(User).filter(User.is_admin == True).first()
    if not admin:
        raise HTTPException(status_code=503, detail="No admin account configured yet. Please set up an admin user.")
    return admin.id


# ============================================================================
# REDIS / RQ SETUP
# ============================================================================

def get_redis():
    return Redis.from_url(REDIS_URL)


def get_ingestion_queue():
    return Queue("ingestion", connection=get_redis())


# ============================================================================
# PYDANTIC SCHEMAS
# ============================================================================

class UserCreate(BaseModel):
    username: str
    email: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    is_admin: bool
    created_at: datetime

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str


class ChatRequest(BaseModel):
    question: str
    chat_history: Optional[list] = []


class LLMSettingsUpdate(BaseModel):
    provider: str
    model: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    temperature: float = 0.3
    top_p: float = 0.9
    max_tokens: int = 2048


class ChatResponse(BaseModel):
    answer: str
    sources: list
    metadata: dict


# ============================================================================
# FASTAPI APP
# ============================================================================

app = FastAPI(
    title="RAG Assistant",
    version="2.0.0",
    docs_url="/api/docs" if DEBUG else None,
    openapi_url="/api/openapi.json" if DEBUG else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ============================================================================
# ROUTES: HTML Pages
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def chat_page(request: Request):
    return templates.TemplateResponse("chat.html", {"request": request})


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


@app.get("/library", response_class=HTMLResponse)
async def library_page(request: Request):
    return templates.TemplateResponse("library.html", {"request": request})


@app.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    return templates.TemplateResponse("upload.html", {"request": request})


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse("settings.html", {"request": request})


# ============================================================================
# ROUTES: Auth
# ============================================================================

@app.post("/api/auth/register", response_model=UserResponse)
async def register(user_data: UserCreate, db: Session = Depends(get_db)):
    """Create a new user account."""
    existing = db.query(User).filter(
        (User.username == user_data.username) | (User.email == user_data.email)
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username or email already registered")

    is_first_user = db.query(User).count() == 0
    user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=hash_password(user_data.password),
        is_admin=is_first_user,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info(f"[AUTH] Registered: {user.username}{' (admin)' if is_first_user else ''}")
    return user


@app.post("/api/auth/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """Authenticate and return JWT token."""
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = create_access_token(user.username)
    logger.info(f"[AUTH] Login: {user.username}")
    return {"access_token": token, "token_type": "bearer"}


@app.get("/api/auth/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    """Return current authenticated user info."""
    return user


# ============================================================================
# ROUTES: Chat
# ============================================================================

@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, db: Session = Depends(get_db)):
    """Public chat endpoint — queries admin's document collection."""
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")
    admin_id = get_admin_user_id(db)
    try:
        result = await query_async(
            question=req.question,
            user_id=admin_id,
            chat_history=req.chat_history
        )
        return result
    except Exception as e:
        logger.error(f"[CHAT] Error: {e}")
        if "connect" in str(e).lower():
            raise HTTPException(status_code=503, detail="Cannot connect to LLM. Please check provider settings.")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# ROUTES: Library
# ============================================================================

@app.get("/api/library")
async def get_library(db: Session = Depends(get_db)):
    """Public library — lists admin's indexed documents."""
    admin_id = get_admin_user_id(db)
    docs = db.query(Document).filter(Document.user_id == admin_id).order_by(Document.created_at.desc()).all()
    return {
        "documents": [
            {
                "id": d.id,
                "title": d.title,
                "doc_type": d.doc_type,
                "url": d.url,
                "chunks": d.chunks,
                "created_at": d.created_at.isoformat(),
            }
            for d in docs
        ],
        "total": len(docs)
    }


# ============================================================================
# ROUTES: Source Management
# ============================================================================

@app.post("/api/sources")
async def add_source(
    type: str = Form(...),
    title: str = Form(...),
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Add a new source (PDF, TXT, or URL).
    File is saved immediately; ingestion is queued as a background job.
    Returns job_id for status polling.
    """
    if not title.strip():
        raise HTTPException(status_code=400, detail="Title is required")

    doc_type = type.lower()
    file_path = ""
    source_url = url or ""

    # Save uploaded file to disk
    if doc_type in ("pdf", "txt") and file:
        safe_name = secure_filename(file.filename)
        dest = Path(CACHE_ROOT) / "uploads" / f"{user.id}_{safe_name}"
        dest.parent.mkdir(parents=True, exist_ok=True)

        async with aiofiles.open(dest, "wb") as f:
            content = await file.read()
            await f.write(content)

        file_path = str(dest)

    elif doc_type == "url" and url:
        source_url = url.strip()
    else:
        raise HTTPException(status_code=400, detail="Invalid source type or missing file/URL")

    doc_id_prefix = f"{doc_type}_{title.lower().replace(' ', '_')}"

    # Create Document record (chunks=0 until job completes)
    doc = Document(
        user_id=user.id,
        title=title,
        doc_type=doc_type,
        url=source_url,
        cached_path=file_path,
        chunks=0,
        qdrant_collection=f"user_{user.id}"
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # Create IngestionJob record
    job_record = IngestionJob(user_id=user.id, document_id=doc.id, status="queued")
    db.add(job_record)
    db.commit()
    db.refresh(job_record)

    # Enqueue background job in Redis RQ (falls back to inline on Windows/no Redis)
    use_inline = False
    try:
        q = get_ingestion_queue()
        rq_job = q.enqueue(
            run_ingestion_job,
            kwargs={
                "file_path": file_path,
                "title": title,
                "doc_type": doc_type,
                "user_id": user.id,
                "url": source_url,
                "doc_id_prefix": doc_id_prefix,
            },
            job_timeout=600,
            result_ttl=3600,
        )
        job_record.rq_job_id = rq_job.id
        db.commit()
        logger.info(f"[SOURCES] Queued job {rq_job.id} for user {user.id}: {title}")
    except Exception as e:
        logger.warning(f"[SOURCES] Redis unavailable, running inline: {e}")
        use_inline = True

    if use_inline:
        # Run in background task so polling still works
        async def run_inline():
            job_record.status = "running"
            db.commit()
            try:
                count = await asyncio.to_thread(
                    run_ingestion_job,
                    file_path=file_path, title=title, doc_type=doc_type,
                    user_id=user.id, url=source_url, doc_id_prefix=doc_id_prefix
                )
                doc.chunks = count
                job_record.status = "complete"
                job_record.completed_at = datetime.utcnow()
                db.commit()
                logger.info(f"[SOURCES] Inline ingestion complete: {title} → {count} chunks")
            except Exception as ingest_err:
                job_record.status = "error"
                job_record.error_msg = str(ingest_err)
                db.commit()
                logger.error(f"[SOURCES] Inline ingestion failed: {ingest_err}")

        asyncio.create_task(run_inline())

    return {
        "status": "queued",
        "job_id": job_record.id,
        "document_id": doc.id,
        "title": title,
        "type": doc_type
    }


@app.get("/api/sources/jobs/{job_id}")
async def get_job_status(
    job_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Poll ingestion job status."""
    job = db.query(IngestionJob).filter(
        IngestionJob.id == job_id,
        IngestionJob.user_id == user.id
    ).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Sync status from Redis RQ if we have a job ID and it's not yet terminal
    if job.rq_job_id and job.status in ("queued", "running"):
        try:
            from rq.job import Job as RQJob
            rq_job = RQJob.fetch(job.rq_job_id, connection=get_redis())
            rq_status = rq_job.get_status()

            if rq_status == "finished":
                job.status = "complete"
                job.completed_at = datetime.utcnow()
                if job.document and rq_job.result:
                    job.document.chunks = rq_job.result
                db.commit()
            elif rq_status == "failed":
                job.status = "error"
                job.error_msg = str(rq_job.exc_info or "Unknown error")
                db.commit()
            elif rq_status == "started":
                job.status = "running"
                db.commit()
        except Exception as e:
            logger.warning(f"[JOB] Could not fetch RQ status: {e}")

    return {
        "job_id": job.id,
        "document_id": job.document_id,
        "status": job.status,
        "error": job.error_msg,
        "created_at": job.created_at.isoformat(),
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


@app.delete("/api/sources/{doc_id}")
async def delete_source(
    doc_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Remove a document from Qdrant and PostgreSQL."""
    doc = db.query(Document).filter(
        Document.id == doc_id,
        Document.user_id == user.id
    ).first()

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Remove from Qdrant
    doc_id_prefix = f"{doc.doc_type}_{doc.title.lower().replace(' ', '_')}"
    try:
        qm = QdrantManager(user_id=user.id)
        await asyncio.to_thread(qm.delete_document, doc_id_prefix)
    except Exception as e:
        logger.warning(f"[SOURCES] Qdrant delete warning: {e}")

    # Remove from PostgreSQL
    db.delete(doc)
    db.commit()
    logger.info(f"[SOURCES] Deleted doc {doc_id} for user {user.id}")
    return {"status": "deleted", "id": doc_id}


# ============================================================================
# ROUTES: Settings
# ============================================================================

@app.get("/api/settings")
async def get_settings(user: User = Depends(require_admin)):
    """Return current user settings."""
    return {
        "user_id": user.id,
        "username": user.username,
        "email": user.email,
    }


@app.post("/api/settings")
async def update_settings(
    data: dict,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Update user profile settings."""
    if "email" in data:
        user.email = data["email"].strip()
        db.commit()
    return {"status": "updated"}


# ============================================================================
# ROUTES: Admin — LLM Provider Settings
# ============================================================================

@app.get("/admin/llm-settings", response_class=HTMLResponse)
async def admin_llm_page(request: Request):
    # Auth is handled client-side via JWT in localStorage
    return templates.TemplateResponse(
        "admin_llm_settings.html", {"request": request}
    )


@app.get("/api/admin/llm-settings")
async def get_llm_settings(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    config = db.query(LLMProviderConfig).first()
    if not config:
        return {
            "provider": "ollama",
            "model": LLM_MODEL,
            "base_url": OLLAMA_BASE_URL,
            "temperature": 0.3,
            "top_p": 0.9,
            "max_tokens": 2048,
            "has_api_key": False,
        }
    return {
        "id": config.id,
        "provider": config.provider,
        "model": config.model,
        "base_url": config.base_url or "",
        "temperature": config.temperature,
        "top_p": config.top_p,
        "max_tokens": config.max_tokens,
        "has_api_key": bool(config.api_key),
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
    }


@app.post("/api/admin/llm-settings")
async def update_llm_settings(
    data: LLMSettingsUpdate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    config = db.query(LLMProviderConfig).first()
    if not config:
        config = LLMProviderConfig()
        db.add(config)

    config.provider = data.provider
    config.model = data.model
    config.base_url = data.base_url or ""
    config.temperature = data.temperature
    config.top_p = data.top_p
    config.max_tokens = data.max_tokens
    config.updated_by_id = user.id

    if data.api_key:
        config.api_key = encrypt_api_key(data.api_key)

    db.commit()
    logger.info(f"[ADMIN] LLM config updated by {user.username}: provider={data.provider} model={data.model}")
    return {"status": "updated", "provider": data.provider, "model": data.model}


@app.get("/api/admin/embed-device")
async def get_embed_device(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    config = db.query(LLMProviderConfig).first()
    return {"embed_device": config.embed_device if config else "cpu"}


@app.post("/api/admin/embed-device")
async def set_embed_device(
    data: dict,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    device = data.get("embed_device", "cpu")
    if device not in ("cpu", "cuda", "rocm"):
        raise HTTPException(status_code=400, detail="Invalid device. Must be cpu, cuda, or rocm.")

    config = db.query(LLMProviderConfig).first()
    if not config:
        config = LLMProviderConfig()
        db.add(config)
    config.embed_device = device
    config.updated_by_id = user.id
    db.commit()

    # Update env var and reset embedder so it reloads on next request
    os.environ["EMBED_DEVICE"] = device
    import rag_async
    rag_async._embedder = None
    logger.info(f"[ADMIN] Embed device set to {device} by {user.username}")
    return {"status": "updated", "embed_device": device}


@app.post("/api/admin/llm-settings/test")
async def test_llm_connection(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    from llm_provider import query_llm_async
    config_row = db.query(LLMProviderConfig).first()
    config = None
    if config_row:
        config = {
            "provider": config_row.provider,
            "model": config_row.model,
            "api_key": config_row.api_key or "",
            "base_url": config_row.base_url or "",
            "temperature": config_row.temperature,
            "top_p": config_row.top_p,
            "max_tokens": 64,
        }
    try:
        response = await query_llm_async("Reply with only: OK", config)
        return {"status": "ok", "response": response[:200]}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM connection failed: {e}")


# ============================================================================
# ROUTES: Health
# ============================================================================

@app.get("/api/health")
async def health():
    """Server health check — used by Docker and load balancers."""
    return {
        "status": "ok",
        "version": "2.0.0",
        "timestamp": datetime.utcnow().isoformat(),
    }


# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return JSONResponse(status_code=404, content={"detail": "Not found"})


@app.exception_handler(500)
async def server_error_handler(request: Request, exc):
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ============================================================================
# STARTUP / SHUTDOWN
# ============================================================================

@app.on_event("startup")
async def startup():
    logger.info("RAG v2.0 FastAPI server starting...")
    logger.info(f"Ollama: {OLLAMA_BASE_URL}")
    logger.info(f"Cache: {CACHE_ROOT}")
    init_db()
    logger.info("Database tables initialized")


@app.on_event("shutdown")
async def shutdown():
    logger.info("RAG v2.0 FastAPI server shutting down...")


# ============================================================================
# RUN (development only — production uses Gunicorn)
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app_fastapi:app", host="0.0.0.0", port=8000, reload=DEBUG)
