"""
SQLAlchemy ORM models for RAG v2.0 multi-user system.
Tables: User, Document, IngestionJob, LLMProviderConfig
"""

import logging
import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime,
    ForeignKey, Text, Boolean, Float
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
    qdrant_collection = Column(String(128), nullable=False)  # e.g. "user_3"
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="documents")
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

def get_engine():
    return create_engine(DATABASE_URL, pool_pre_ping=True)


def get_session_local():
    engine = get_engine()
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db(max_attempts: int = 60, delay: float = 1.0):
    """
    Create all tables. Retries on transient connection errors so the app can
    ride out the startup window where Postgres is ready but the Docker DNS or
    database creation hasn't finished propagating yet.
    """
    import time

    engine = get_engine()
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            Base.metadata.create_all(bind=engine)
            if attempt > 1:
                logger.info("DB schema initialized on attempt %d/%d", attempt, max_attempts)
            return
        except Exception as e:
            last_error = e
            logger.warning(
                "DB not reachable yet (attempt %d/%d): %s",
                attempt, max_attempts, type(e).__name__,
            )
            time.sleep(delay)
    raise RuntimeError(
        f"Could not connect to the database after {max_attempts} attempts. "
        f"Last error: {last_error}"
    )
