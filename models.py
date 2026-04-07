"""
SQLAlchemy ORM models for RAG v2.0 multi-user system.
Tables: User, Document, IngestionJob
"""

from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime,
    ForeignKey, Text, Boolean
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

from config import DATABASE_URL

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, index=True, nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
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


# ============================================================================
# DB INIT HELPERS
# ============================================================================

def get_engine():
    return create_engine(DATABASE_URL, pool_pre_ping=True)


def get_session_local():
    engine = get_engine()
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Create all tables. Call once on startup."""
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
