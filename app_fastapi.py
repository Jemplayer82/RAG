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

  POST   /api/chat              — Authenticated query against the shared library
  GET    /api/library           — List shared admin-curated documents (auth required)
  POST   /api/sources           — Add document / URL (admin only)
  DELETE /api/sources/{id}      — Remove document (admin only)
  GET    /api/sources/jobs/{id} — Poll ingestion job status (auth required)

  GET    /api/settings          — User preferences (auth required)
  POST   /api/settings          — Update preferences (auth required)
  GET    /api/health            — Server health check
"""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Ensure JWT_SECRET + ENCRYPTION_KEY are set before any module reads them.
from secrets_bootstrap import bootstrap_secrets
bootstrap_secrets()

import httpx
import jwt
import aiofiles
from fastapi import (
    FastAPI, Depends, HTTPException, UploadFile, File,
    Form, Request, status
)
from fastapi.responses import JSONResponse, HTMLResponse, StreamingResponse, FileResponse, RedirectResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from passlib.context import CryptContext
from pydantic import BaseModel
from redis import Redis
from rq import Queue
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import secure_filename

from config import (
    OLLAMA_BASE_URL, LLM_MODEL, EMBED_MODEL,
    DEBUG, CACHE_ROOT, REDIS_URL
)
from models import (
    User, Document, IngestionJob, LLMProviderConfig, Library,
    get_session_local, init_db, encrypt_api_key, ensure_default_library
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
    admin = db.query(User).filter(User.is_admin == True).order_by(User.id).first()
    if not admin:
        raise HTTPException(status_code=503, detail="No admin account configured yet. Please set up an admin user.")
    return admin.id


def _get_admin_library(db: Session, admin_id: int, library_id: int) -> Library:
    """Fetch a library by id, scoped to the admin who owns it. Raises 404 otherwise."""
    lib = db.query(Library).filter(
        Library.id == library_id,
        Library.owner_id == admin_id,
    ).first()
    if not lib:
        raise HTTPException(status_code=404, detail="Library not found")
    return lib


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
    library_id: Optional[int] = None      # legacy single-select (kept for compat)
    library_ids: Optional[list] = None    # multi-select: takes priority when set


class LibraryCreate(BaseModel):
    name: str
    description: Optional[str] = ""


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

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("RAG v2.0 FastAPI server starting...")
    logger.info(f"Ollama: {OLLAMA_BASE_URL}")
    logger.info(f"Cache: {CACHE_ROOT}")
    init_db()
    logger.info("Database tables initialized")
    yield
    logger.info("RAG v2.0 FastAPI server shutting down...")


app = FastAPI(
    title="RAG Assistant",
    version="2.0.0",
    docs_url="/api/docs" if DEBUG else None,
    openapi_url="/api/openapi.json" if DEBUG else None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # Auth is a Bearer token in localStorage (no cookies), so credentials are
    # not needed. A wildcard origin with credentials is invalid per the CORS
    # spec and makes Starlette reflect any Origin — keep credentials off.
    allow_credentials=False,
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


@app.get("/admin/libraries", response_class=HTMLResponse)
async def admin_libraries_page(request: Request):
    return templates.TemplateResponse("admin_libraries.html", {"request": request})


@app.get("/activity", response_class=HTMLResponse)
async def activity_page(request: Request):
    return templates.TemplateResponse("activity.html", {"request": request})


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

    # Admin assignment, hardened against the "land-grab" (a stranger registering
    # first on a briefly-exposed deploy). A new user becomes admin ONLY if no
    # admin exists yet AND either ADMIN_USERNAME matches them, or ADMIN_USERNAME
    # is unset and they are the very first user. Once an admin exists, no
    # registration can ever mint another admin.
    admin_username = os.getenv("ADMIN_USERNAME", "").strip()
    admin_exists = db.query(User).filter(User.is_admin == True).count() > 0
    if admin_exists:
        is_admin = False
    elif admin_username:
        is_admin = (user_data.username == admin_username)
    else:
        is_admin = (db.query(User).count() == 0)
        if is_admin:
            logger.warning(
                "[AUTH] Granting admin to first registrant without ADMIN_USERNAME set — "
                "set ADMIN_USERNAME to close the first-registration race window."
            )

    user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=hash_password(user_data.password),
        is_admin=is_admin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info(f"[AUTH] Registered: {user.username}{' (admin)' if is_admin else ''}")

    # Fresh install: give the new admin a starter library so the app is usable
    # immediately (chat/upload need at least one library to target).
    if is_admin:
        try:
            ensure_default_library(db, user)
        except Exception as e:
            logger.error("[AUTH] Failed to create starter library: %s", e)

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


@app.get("/api/current-model")
async def get_current_model(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Active LLM provider + model name — readable by any authenticated user."""
    config = db.query(LLMProviderConfig).first()
    if config:
        return {"provider": config.provider, "model": config.model or ""}
    return {"provider": os.getenv("LLM_PROVIDER", "ollama"), "model": os.getenv("LLM_MODEL", "")}


# ============================================================================
# ROUTES: Chat
# ============================================================================

@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Authenticated chat — queries one or more library collections."""
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")
    admin_id = get_admin_user_id(db)

    # Resolve collection names.
    # Priority: library_ids (multi) > library_id (single) > oldest library (default).
    collection_names: list[str] = []

    if req.library_ids:
        ids = [int(i) for i in req.library_ids if i is not None]
        for lid in ids:
            lib = _get_admin_library(db, admin_id, lid)
            collection_names.append(lib.collection_name)
    elif req.library_id is not None:
        lib = _get_admin_library(db, admin_id, req.library_id)
        collection_names = [lib.collection_name]
    else:
        lib = (
            db.query(Library)
            .filter(Library.owner_id == admin_id)
            .order_by(Library.created_at.asc())
            .first()
        )
        if not lib:
            raise HTTPException(status_code=503, detail="No libraries configured yet.")
        collection_names = [lib.collection_name]

    if not collection_names:
        raise HTTPException(status_code=503, detail="No libraries configured yet.")

    try:
        result = await query_async(
            question=req.question,
            collection_names=collection_names,
            chat_history=req.chat_history
        )
        return result
    except Exception as e:
        logger.error(f"[CHAT] Error: {e}")
        if "connect" in str(e).lower():
            raise HTTPException(status_code=503, detail="Cannot connect to LLM. Please check provider settings.")
        # Don't leak internal exception detail to clients; it's logged above.
        raise HTTPException(status_code=500, detail="Failed to process your question. Please try again.")


# ============================================================================
# ROUTES: Library
# ============================================================================

@app.get("/api/library")
async def get_library(
    library_id: Optional[int] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Authenticated library — lists the admin-curated documents, optionally
    filtered to a single library via ?library_id."""
    admin_id = get_admin_user_id(db)
    q = db.query(Document).filter(Document.user_id == admin_id)
    if library_id is not None:
        q = q.filter(Document.library_id == library_id)
    docs = q.order_by(Document.created_at.desc()).all()

    # Attach each doc's latest ingestion status so the manage UI can show
    # queued / processing / error rows (not just completed ones). One query,
    # latest-wins by created_at.
    status_by_doc: dict = {}
    doc_ids = [d.id for d in docs]
    if doc_ids:
        for did, st in (
            db.query(IngestionJob.document_id, IngestionJob.status)
            .filter(IngestionJob.user_id == admin_id, IngestionJob.document_id.in_(doc_ids))
            .order_by(IngestionJob.created_at.asc())
            .all()
        ):
            status_by_doc[did] = st

    def _doc_status(d):
        if d.chunks and d.chunks > 0:
            return "done"
        return status_by_doc.get(d.id, "queued")

    return {
        "documents": [
            {
                "id": d.id,
                "title": d.title,
                "doc_type": d.doc_type,
                "url": d.url,
                "chunks": d.chunks,
                "status": _doc_status(d),
                "library_id": d.library_id,
                "created_at": d.created_at.isoformat(),
            }
            for d in docs
        ],
        "total": len(docs)
    }


# ============================================================================
# ROUTES: Libraries
# ============================================================================

@app.get("/api/libraries")
async def list_libraries(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """List the admin's libraries (oldest first) with a per-library document count.
    Available to any authenticated user — the chat selector needs it."""
    admin_id = get_admin_user_id(db)
    libs = (
        db.query(Library)
        .filter(Library.owner_id == admin_id)
        .order_by(Library.created_at.asc())
        .all()
    )
    # Self-heal: if the admin somehow has no library (e.g. a swallowed failure
    # during first-admin registration), create the starter one now. Idempotent.
    if not libs:
        admin = db.query(User).filter(User.id == admin_id).first()
        if admin:
            ensure_default_library(db, admin)
            libs = (
                db.query(Library)
                .filter(Library.owner_id == admin_id)
                .order_by(Library.created_at.asc())
                .all()
            )
    out = []
    for lib in libs:
        count = db.query(Document).filter(Document.library_id == lib.id).count()
        out.append({
            "id": lib.id,
            "name": lib.name,
            "description": lib.description or "",
            "document_count": count,
            "created_at": lib.created_at.isoformat(),
        })
    return {"libraries": out, "total": len(out)}


@app.post("/api/admin/libraries")
async def create_library(
    payload: LibraryCreate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create a new library (admin only). Its Qdrant collection is created lazily
    on first upload."""
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Library name cannot be empty")
    dup = db.query(Library).filter(
        Library.owner_id == user.id, Library.name == name
    ).first()
    if dup:
        raise HTTPException(status_code=409, detail="A library with that name already exists")

    # Insert with a placeholder collection_name, then derive the stable
    # lib_{id} name once we have the primary key.
    lib = Library(owner_id=user.id, name=name,
                  description=(payload.description or "").strip(), collection_name="")
    db.add(lib)
    db.flush()
    lib.collection_name = f"lib_{lib.id}"
    try:
        db.commit()
    except IntegrityError:
        # Lost a race against a concurrent create with the same name.
        db.rollback()
        raise HTTPException(status_code=409, detail="A library with that name already exists")
    db.refresh(lib)
    logger.info("[LIBRARY] Created '%s' (collection=%s) by %s", lib.name, lib.collection_name, user.username)
    return {"id": lib.id, "name": lib.name, "collection_name": lib.collection_name}


@app.delete("/api/admin/libraries/{library_id}")
async def delete_library(
    library_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Delete a library: drop its Qdrant collection and remove its documents/jobs.
    Refuses to delete the last remaining library."""
    lib = _get_admin_library(db, user.id, library_id)
    total = db.query(Library).filter(Library.owner_id == user.id).count()
    if total <= 1:
        raise HTTPException(status_code=409, detail="Cannot delete the only library. Create another first.")

    collection = lib.collection_name
    name = lib.name

    # Remove documents (their ingestion jobs cascade via Document.jobs), then the
    # library — and COMMIT before touching Qdrant. Dropping the collection is the
    # irreversible side effect, so it must come last: if the DB delete fails we
    # don't want a library left pointing at a vaporized collection.
    docs = db.query(Document).filter(Document.library_id == lib.id).all()
    doc_count = len(docs)
    for d in docs:
        db.delete(d)
    db.delete(lib)
    db.commit()

    # Postgres is consistent now — drop the whole Qdrant collection (1:1).
    try:
        qm = QdrantManager(collection_name=collection)
        await asyncio.to_thread(qm.client.delete_collection, collection)
    except Exception as e:
        logger.warning("[LIBRARY] Qdrant collection drop warning for %s: %s", collection, e)

    logger.info("[LIBRARY] Deleted '%s' (%d docs) by %s", name, doc_count, user.username)
    return {"status": "deleted", "id": library_id, "documents_removed": doc_count}


# ============================================================================
# ROUTES: Source Management
# ============================================================================

@app.post("/api/sources")
async def add_source(
    type: str = Form(...),
    title: str = Form(...),
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
    crawl: bool = Form(False),
    max_depth: int = Form(2),
    max_pages: int = Form(20),
    same_domain_only: bool = Form(True),
    respect_robots: bool = Form(False),
    library_id: int = Form(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Add a new source (PDF, TXT, or URL) to a chosen library.
    File is saved immediately; ingestion is queued as a background job.
    Returns job_id for status polling.
    """
    if not title.strip():
        raise HTTPException(status_code=400, detail="Title is required")

    # Validate the target library belongs to this admin.
    lib = _get_admin_library(db, user.id, library_id)

    doc_type = type.lower()
    file_path = ""
    source_url = url or ""

    # Save uploaded file to disk (size-capped, safe filename, type from extension)
    if doc_type in ("pdf", "txt", "doc", "docx") and file:
        MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB

        # The real file extension is authoritative over the client-sent type,
        # so the correct ingester runs even if `type` was spoofed.
        fname = file.filename or ""
        ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
        if ext in ("pdf", "txt", "doc", "docx"):
            doc_type = ext

        safe_name = secure_filename(fname) or f"upload.{doc_type}"
        dest = Path(CACHE_ROOT) / "uploads" / f"{user.id}_{safe_name}"
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Stream to disk in bounded chunks; never read the whole file into RAM.
        total = 0
        too_large = False
        async with aiofiles.open(dest, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    too_large = True
                    break
                await f.write(chunk)
        if too_large:
            dest.unlink(missing_ok=True)  # handle is closed now (Windows-safe)
            raise HTTPException(status_code=413, detail="File too large (max 500 MB)")

        file_path = str(dest)

    elif doc_type == "url" and url:
        source_url = url.strip()
    else:
        raise HTTPException(status_code=400, detail="Invalid source type or missing file/URL")

    # Create Document record (chunks=0 until job completes). It belongs to the
    # chosen library; qdrant_collection mirrors the library's collection.
    doc = Document(
        user_id=user.id,
        title=title,
        doc_type=doc_type,
        url=source_url,
        cached_path=file_path,
        chunks=0,
        library_id=lib.id,
        qdrant_collection=lib.collection_name,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # Create IngestionJob record
    job_record = IngestionJob(user_id=user.id, document_id=doc.id, status="queued")
    db.add(job_record)
    db.commit()
    db.refresh(job_record)

    # Stable, unique document identifier for Qdrant points, derived from the
    # immutable primary key — used for BOTH upsert and delete so two docs with
    # the same title/type never collide and deletes are exact. Capture plain
    # ids now so the inline-fallback task never touches the request-scoped
    # session/ORM objects (get_db() closes them once this response returns).
    doc_id_prefix = f"doc_{doc.id}"
    document_id = doc.id
    job_record_id = job_record.id
    owner_id = user.id
    collection_name = lib.collection_name

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
                "user_id": owner_id,
                "document_id": document_id,
                "job_id": job_record_id,
                "url": source_url,
                "doc_id_prefix": doc_id_prefix,
                "collection_name": collection_name,
                "crawl": crawl,
                "max_depth": max_depth,
                "max_pages": max_pages,
                "same_domain_only": same_domain_only,
                "respect_robots": respect_robots,
            },
            job_timeout=1800,
            result_ttl=3600,
        )
        job_record.rq_job_id = rq_job.id
        db.commit()
        logger.info(f"[SOURCES] Queued job {rq_job.id} for user {user.id}: {title}")
    except Exception as e:
        logger.warning(f"[SOURCES] Redis unavailable, running inline: {e}")
        use_inline = True

    if use_inline:
        # Mark this as an inline job so a worker restart's reaper can tell it
        # apart from a lost RQ job (a NULL rq_job_id would otherwise be reaped).
        job_record.rq_job_id = "inline"
        db.commit()
        # Run in a background task. run_ingestion_job opens its OWN DB session
        # and writes Document.chunks + job status/error itself, so we hand it
        # only plain ids — never the request-scoped session or ORM objects,
        # which get_db() has already closed by the time this task runs.
        async def run_inline():
            try:
                await asyncio.to_thread(
                    run_ingestion_job,
                    file_path=file_path, title=title, doc_type=doc_type,
                    user_id=owner_id, document_id=document_id, job_id=job_record_id,
                    url=source_url, doc_id_prefix=doc_id_prefix,
                    collection_name=collection_name,
                    crawl=crawl, max_depth=max_depth, max_pages=max_pages,
                    same_domain_only=same_domain_only, respect_robots=respect_robots,
                )
                logger.info(f"[SOURCES] Inline ingestion complete: {title}")
            except Exception as ingest_err:
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


@app.get("/api/sources/{doc_id}/download")
async def download_source(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Stream the original uploaded file. URL-typed docs return a redirect."""
    admin_id = get_admin_user_id(db)
    doc = db.query(Document).filter(Document.id == doc_id, Document.user_id == admin_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.doc_type == "url":
        if not doc.url:
            raise HTTPException(status_code=400, detail="URL document has no URL")
        if not (doc.url.startswith("http://") or doc.url.startswith("https://")):
            raise HTTPException(status_code=400, detail="Refusing to redirect to a non-http(s) URL")
        return RedirectResponse(url=doc.url, status_code=302)

    if not doc.cached_path:
        raise HTTPException(status_code=404, detail="No file path on record")
    p = Path(doc.cached_path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="File missing on disk")
    download_name = p.name.split("_", 1)[-1] if "_" in p.name else p.name
    return FileResponse(p, filename=download_name)


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

    # Remove from Qdrant using the same stable, unique prefix used at ingest
    # time (doc_{id}) — never reconstruct from the title (collisions/orphans).
    # Resolve the collection from the doc's library; fall back to the stored
    # collection name (then legacy per-user) for rows predating libraries.
    collection = doc.qdrant_collection or f"user_{user.id}"
    if doc.library_id:
        lib = db.query(Library).filter(Library.id == doc.library_id).first()
        if lib:
            collection = lib.collection_name
    doc_id_prefix = f"doc_{doc.id}"
    try:
        qm = QdrantManager(collection_name=collection)
        await asyncio.to_thread(qm.delete_document, doc_id_prefix)
    except Exception as e:
        logger.warning(f"[SOURCES] Qdrant delete warning: {e}")

    # Remove from PostgreSQL
    db.delete(doc)
    db.commit()
    logger.info(f"[SOURCES] Deleted doc {doc_id} for user {user.id}")
    return {"status": "deleted", "id": doc_id}


@app.get("/api/sources/jobs")
async def list_jobs(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """
    Ingestion activity for the admin: status summary + recent jobs (joined to
    their document for the title). One endpoint, polled by the /activity page —
    a single periodic GET, never one-poll-per-job (which crashed the server).
    """
    rows = (
        db.query(IngestionJob.status, func.count(IngestionJob.id))
        .filter(IngestionJob.user_id == user.id)
        .group_by(IngestionJob.status)
        .all()
    )
    summary = {"queued": 0, "running": 0, "complete": 0, "error": 0}
    for status_val, cnt in rows:
        summary[status_val] = cnt

    recent = (
        db.query(IngestionJob, Document)
        .outerjoin(Document, IngestionJob.document_id == Document.id)
        .filter(IngestionJob.user_id == user.id)
        .order_by(IngestionJob.created_at.desc())
        .limit(200)
        .all()
    )
    jobs = [
        {
            "job_id": job.id,
            "document_id": job.document_id,
            "title": (doc.title if doc else None) or "—",
            "status": job.status,
            "error": job.error_msg or "",
            "chunks": doc.chunks if doc else 0,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        }
        for job, doc in recent
    ]
    return {"summary": summary, "jobs": jobs}


@app.post("/api/sources/bulk-delete")
async def bulk_delete_sources(
    data: dict,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Delete many documents at once (Qdrant vectors + Postgres rows)."""
    from collections import defaultdict

    raw_ids = data.get("document_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(status_code=400, detail="document_ids must be a non-empty list")
    try:
        ids = [int(i) for i in raw_ids]
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="document_ids must be integers")

    docs = (
        db.query(Document)
        .filter(Document.id.in_(ids), Document.user_id == user.id)
        .all()
    )

    # Group doc prefixes by their backing collection so one QdrantManager handles
    # all docs in a collection. Resolve from the library (source of truth), then
    # fall back to the stored collection / legacy per-user name.
    by_collection: "defaultdict[str, list]" = defaultdict(list)
    lib_cache: dict = {}
    for d in docs:
        collection = d.qdrant_collection or f"user_{user.id}"
        if d.library_id:
            if d.library_id not in lib_cache:
                lib = db.query(Library).filter(Library.id == d.library_id).first()
                lib_cache[d.library_id] = lib.collection_name if lib else collection
            collection = lib_cache[d.library_id]
        by_collection[collection].append(f"doc_{d.id}")

    failed = []
    for collection, prefixes in by_collection.items():
        try:
            qm = QdrantManager(collection_name=collection)
        except Exception as e:
            logger.warning(f"[SOURCES] Qdrant init failed for {collection}: {e}")
            failed.extend(prefixes)
            continue
        for pfx in prefixes:
            try:
                await asyncio.to_thread(qm.delete_document, pfx)
            except Exception as e:
                logger.warning(f"[SOURCES] Qdrant bulk-delete warning for {pfx}: {e}")
                failed.append(pfx)

    deleted = len(docs)
    for d in docs:
        db.delete(d)  # IngestionJobs cascade via Document.jobs
    db.commit()
    logger.info(f"[SOURCES] Bulk-deleted {deleted} doc(s) for user {user.id}")
    return {"deleted": deleted, "failed": failed}


@app.post("/api/sources/cancel-pending")
async def cancel_pending(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Cancel the whole ingestion backlog: drain the RQ queue, delete the stuck
    0-chunk documents (their jobs cascade), and mark any remaining queued/running
    jobs as canceled. The run_ingestion_job orphan guard makes any job already
    mid-flight a no-op once its document is gone.
    """
    purged = 0
    try:
        q = get_ingestion_queue()
        purged = q.count
        q.empty()
    except Exception as e:
        logger.warning(f"[SOURCES] Could not empty RQ queue: {e}")

    stuck = (
        db.query(Document)
        .filter(Document.user_id == user.id, Document.chunks == 0)
        .all()
    )
    docs_removed = len(stuck)
    for d in stuck:
        db.delete(d)
    db.flush()

    jobs_canceled = (
        db.query(IngestionJob)
        .filter(
            IngestionJob.user_id == user.id,
            IngestionJob.status.in_(["queued", "running"]),
        )
        .update(
            {
                "status": "error",
                "error_msg": "Canceled by admin",
                "completed_at": datetime.utcnow(),
            },
            synchronize_session=False,
        )
    )
    db.commit()
    logger.info(
        f"[SOURCES] Cancel-pending by {user.username}: queue_purged={purged} "
        f"docs_removed={docs_removed} jobs_canceled={jobs_canceled}"
    )
    return {"queue_purged": purged, "docs_removed": docs_removed, "jobs_canceled": jobs_canceled}


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
    if "email" in data and data["email"]:
        user.email = str(data["email"]).strip()
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


@app.patch("/api/admin/llm-model")
async def quick_update_llm_model(
    data: dict,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Quick model-only swap — keeps all other settings intact."""
    model = (data.get("model") or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="model is required")
    config = db.query(LLMProviderConfig).first()
    if not config:
        config = LLMProviderConfig(provider="ollama")
        db.add(config)
    config.model = model
    config.updated_by_id = user.id
    db.commit()
    logger.info(f"[ADMIN] LLM model quick-switched to {model!r} by {user.username}")
    return {"model": model}


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


# ============================================================================
# ROUTES: Admin — Ollama model management
# ============================================================================

def _resolve_ollama_base_url(db: Session) -> str:
    """Base URL for the Ollama container — DB config wins, env var falls back."""
    config = db.query(LLMProviderConfig).first()
    if config and config.base_url:
        return config.base_url.rstrip("/")
    return os.getenv("LLM_BASE_URL", os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")).rstrip("/")


@app.get("/api/admin/ollama/models")
async def list_ollama_models(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List models currently installed inside the Ollama container."""
    base_url = _resolve_ollama_base_url(db)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{base_url}/api/tags")
            resp.raise_for_status()
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Cannot reach Ollama at {base_url}: {e}")
    data = resp.json()
    return {
        "base_url": base_url,
        "models": [
            {
                "name": m.get("name", ""),
                "size": m.get("size", 0),
                "modified_at": m.get("modified_at", ""),
            }
            for m in data.get("models", [])
        ],
    }


@app.post("/api/admin/ollama/pull")
async def pull_ollama_model(
    data: dict,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Pull an Ollama model. Streams NDJSON progress from Ollama straight through to the client."""
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Model name required")
    base_url = _resolve_ollama_base_url(db)

    async def stream_pull():
        async with httpx.AsyncClient(timeout=None) as client:
            try:
                async with client.stream(
                    "POST",
                    f"{base_url}/api/pull",
                    json={"name": name, "stream": True},
                ) as resp:
                    if resp.status_code >= 400:
                        body = await resp.aread()
                        yield json.dumps({"error": f"Ollama {resp.status_code}: {body.decode(errors='ignore')[:200]}"}) + "\n"
                        return
                    async for line in resp.aiter_lines():
                        if line:
                            yield line + "\n"
            except httpx.RequestError as e:
                yield json.dumps({"error": f"Cannot reach Ollama at {base_url}: {e}"}) + "\n"

    return StreamingResponse(stream_pull(), media_type="application/x-ndjson")


@app.delete("/api/admin/ollama/models/{model_name:path}")
async def delete_ollama_model(
    model_name: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Remove a pulled Ollama model to free disk space."""
    base_url = _resolve_ollama_base_url(db)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(
                "DELETE",
                f"{base_url}/api/delete",
                json={"name": model_name},
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Cannot reach Ollama at {base_url}: {e}")
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return {"status": "deleted", "name": model_name}


@app.post("/api/admin/llm-settings/test")
async def test_llm_connection(
    pending: Optional[LLMSettingsUpdate] = None,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    from llm_provider import query_llm_async

    if pending is not None:
        config = {
            "provider": pending.provider,
            "model": pending.model,
            "api_key": pending.api_key or "",
            "base_url": pending.base_url or "",
            "temperature": pending.temperature,
            "top_p": pending.top_p,
            "max_tokens": 64,
        }
        if not pending.api_key:
            saved = db.query(LLMProviderConfig).first()
            if saved and saved.api_key:
                config["api_key"] = saved.api_key
    else:
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

# ============================================================================
# RUN (development only — production uses Gunicorn)
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app_fastapi:app", host="0.0.0.0", port=8000, reload=DEBUG)
