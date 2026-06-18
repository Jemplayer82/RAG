"""
SQLAlchemy ORM models for RAG v2.0 multi-user system.
Tables: User, Document, IngestionJob, LLMProviderConfig
"""

import logging
import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime,
    ForeignKey, Text, Boolean, Float, UniqueConstraint, text
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

from config import DATABASE_URL

logger = logging.getLogger(__name__)

# ============================================================================
# API KEY ENCRYPTION
# ============================================================================

_PLACEHOLDER_ENCRYPTION_KEYS = {
    "",
    "changeme",
    "changeme_generate_fernet_key",
}
_GENERATE_KEY_CMD = (
    'python -c "from cryptography.fernet import Fernet; '
    'print(Fernet.generate_key().decode())"'
)
_cipher = None


def _get_cipher():
    """
    Return a cached Fernet cipher.

    - If ENCRYPTION_KEY is unset or still a placeholder, generate a random key
      for this process and log a warning. Stored API keys won't survive restart.
    - If ENCRYPTION_KEY is set to something that isn't a valid Fernet key,
      raise with a clear message rather than the cryptic ValueError from Fernet.
    """
    global _cipher
    if _cipher is not None:
        return _cipher

    from cryptography.fernet import Fernet

    key = os.getenv("ENCRYPTION_KEY", "").strip()
    if key in _PLACEHOLDER_ENCRYPTION_KEYS:
        key = Fernet.generate_key().decode()
        logger.warning(
            "ENCRYPTION_KEY is not set — generated an ephemeral key for this process. "
            "Stored API keys will be unreadable after restart. Generate a permanent "
            "key with: %s",
            _GENERATE_KEY_CMD,
        )
    try:
        _cipher = Fernet(key.encode())
    except ValueError as e:
        raise RuntimeError(
            f"ENCRYPTION_KEY is not a valid Fernet key ({e}). "
            f"Generate one with: {_GENERATE_KEY_CMD}"
        ) from e
    return _cipher


def encrypt_api_key(plain_key: str) -> str:
    return _get_cipher().encrypt(plain_key.encode()).decode()


def decrypt_api_key(encrypted_key: str) -> str:
    return _get_cipher().decrypt(encrypted_key.encode()).decode()

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, index=True, nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    documents = relationship("Document", back_populates="owner", cascade="all, delete-orphan")
    jobs = relationship("IngestionJob", back_populates="owner", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    doc_type = Column(String(16), nullable=False)   # "pdf", "txt", "url"
    url = Column(Text, default="")
    cached_path = Column(Text, default="")
    chunks = Column(Integer, default=0)
    qdrant_collection = Column(String(128), nullable=False)  # denormalized cache of library.collection_name
    library_id = Column(Integer, ForeignKey("libraries.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="documents")
    library = relationship("Library", back_populates="documents")
    jobs = relationship("IngestionJob", back_populates="document", cascade="all, delete-orphan")


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=True)
    rq_job_id = Column(String(128), nullable=True)      # Redis RQ job ID
    status = Column(String(16), default="queued")        # queued, running, complete, error
    error_msg = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    owner = relationship("User", back_populates="jobs")
    document = relationship("Document", back_populates="jobs")


class Library(Base):
    """
    A named document collection. Each library maps 1:1 to a Qdrant collection
    (collection_name). The admin creates libraries and adds documents to a chosen
    one; chat queries exactly one library at a time.
    """
    __tablename__ = "libraries"

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, default="")
    # Qdrant collection backing this library. The original library adopts the
    # legacy "user_{admin_id}" collection (zero-loss); new ones use "lib_{id}".
    collection_name = Column(String(128), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_library_owner_name"),)

    owner = relationship("User")
    # No cascade: deleting a library also drops its Qdrant collection, which is
    # coordinated explicitly in the delete route (an ORM cascade can't do that).
    documents = relationship("Document", back_populates="library")


class LLMProviderConfig(Base):
    """Global LLM provider configuration — admin-only, one active row."""
    __tablename__ = "llm_provider_configs"

    id = Column(Integer, primary_key=True)
    provider = Column(String(50), nullable=False, default="ollama")   # openai | anthropic | ollama | generic
    model = Column(String(255), nullable=False, default="")
    api_key = Column(Text, nullable=True)        # Fernet-encrypted
    base_url = Column(String(500), nullable=True)  # Ollama or generic endpoint
    temperature = Column(Float, default=0.3)
    top_p = Column(Float, default=0.9)
    max_tokens = Column(Integer, default=2048)
    embed_device = Column(String(16), default="cpu")  # cpu | cuda | rocm
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)


# ============================================================================
# DB INIT HELPERS
# ============================================================================

# Name given to the library that adopts the pre-v1.2 single knowledge base.
DEFAULT_ADOPTED_LIBRARY_NAME = "Spinal Cord Injury"
# Name for the starter library created on a fresh install at admin registration.
FRESH_INSTALL_LIBRARY_NAME = "My Library"


def get_engine():
    return create_engine(DATABASE_URL, pool_pre_ping=True)


def get_session_local():
    engine = get_engine()
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def ensure_default_library(db, admin):
    """
    Guarantee the given admin has at least one library, creating a starter one
    (backed by the legacy per-user collection) if none exists. Idempotent.
    Called at first-admin registration so a fresh install is usable immediately.
    Returns the admin's first library.
    """
    existing = (
        db.query(Library)
        .filter(Library.owner_id == admin.id)
        .order_by(Library.created_at.asc())
        .first()
    )
    if existing:
        return existing
    lib = Library(
        owner_id=admin.id,
        name=FRESH_INSTALL_LIBRARY_NAME,
        description="Starter library.",
        collection_name=f"user_{admin.id}",
    )
    db.add(lib)
    db.commit()
    db.refresh(lib)
    logger.info("[LIBRARY] Created starter library '%s' (collection=%s) for admin %s",
                FRESH_INSTALL_LIBRARY_NAME, lib.collection_name, admin.id)
    return lib


# Arbitrary constant key for the Postgres advisory lock that serializes schema
# init + migration across the multiple Gunicorn workers (each runs lifespan).
_SCHEMA_LOCK_KEY = 0x52474C49  # "RGLI"


def _init_schema_locked(conn):
    """
    Create tables, add the v1.2 `documents.library_id` column, and adopt any
    pre-existing corpus as the first library — all on a single connection that
    holds the advisory lock, so concurrent workers can't race each other.

    Idempotent: safe on every boot, on a fresh empty DB, and before any admin
    exists. Raw SQL for the seed avoids ORM-session complications inside the
    locked transaction. Postgres-specific (ADD COLUMN IF NOT EXISTS, advisory lock).
    """
    # create_all builds the new `libraries` table; it never alters the existing
    # `documents` table, so we add `library_id` by hand. We deliberately skip a
    # raw FK constraint: fresh DBs get it from create_all, existing prod relies
    # on app-level validation — adding it here would double-up on fresh DBs.
    Base.metadata.create_all(bind=conn)
    conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS library_id INTEGER"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_documents_library_id ON documents (library_id)"))

    if conn.execute(text("SELECT 1 FROM libraries LIMIT 1")).first() is not None:
        return  # already seeded — idempotent bail
    admin = conn.execute(
        text("SELECT id FROM users WHERE is_admin = true ORDER BY id LIMIT 1")
    ).first()
    if admin is None:
        logger.info("[MIGRATION] No admin yet; deferring default-library seed.")
        return
    admin_id = admin[0]
    collection = f"user_{admin_id}"  # adopt the existing collection in place
    conn.execute(
        text("INSERT INTO libraries (owner_id, name, description, collection_name, created_at) "
             "VALUES (:o, :n, :d, :c, now())"),
        {"o": admin_id, "n": DEFAULT_ADOPTED_LIBRARY_NAME,
         "d": "Adopted from the original knowledge base.", "c": collection},
    )
    lib_id = conn.execute(
        text("SELECT id FROM libraries WHERE owner_id = :o AND name = :n"),
        {"o": admin_id, "n": DEFAULT_ADOPTED_LIBRARY_NAME},
    ).first()[0]
    res = conn.execute(
        text("UPDATE documents SET library_id = :lid "
             "WHERE library_id IS NULL AND user_id = :admin"),
        {"lid": lib_id, "admin": admin_id},
    )
    logger.info(
        "[MIGRATION] Created default library '%s' (collection=%s); backfilled %s document(s).",
        DEFAULT_ADOPTED_LIBRARY_NAME, collection, res.rowcount,
    )


def init_db(max_attempts: int = 60, delay: float = 1.0):
    """
    Wait for Postgres, then run schema creation + the v1.2 library migration
    inside a single advisory-locked transaction. The lock serializes startup
    across Gunicorn workers so they can't race (each worker runs the lifespan).
    """
    import time

    engine = get_engine()
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            break
        except Exception as e:
            last_error = e
            logger.warning(
                "DB not reachable yet (attempt %d/%d): %s",
                attempt, max_attempts, type(e).__name__,
            )
            time.sleep(delay)
    else:
        raise RuntimeError(
            f"Could not connect to the database after {max_attempts} attempts. "
            f"Last error: {last_error}"
        )

    # Connected. Serialize schema init + migration with a transaction-scoped
    # advisory lock; concurrent workers block here, then see the work is done
    # and bail. Fail loudly on a real migration error.
    try:
        with engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _SCHEMA_LOCK_KEY})
            _init_schema_locked(conn)
    except Exception as e:
        logger.error("[MIGRATION] Schema init/migration FAILED: %s", e, exc_info=True)
        raise
