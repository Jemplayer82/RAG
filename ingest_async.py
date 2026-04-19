"""
Async ingestion pipeline for RAG v2.0.
- Reuses chunk_text, ingest_pdf, ingest_txt, ingest_url from ingest.py
- Stores embeddings in Qdrant (per-user collections) instead of ChromaDB
- QdrantManager handles all Qdrant operations for a user namespace
"""

import asyncio
import logging
from typing import Dict, List, Tuple
from datetime import datetime

from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, Filter,
    FieldCondition, MatchValue
)

from config import (
    EMBED_MODEL, EMBED_DEVICE,
    QDRANT_HOST, QDRANT_PORT,
    CHUNK_SIZE, CHUNK_OVERLAP
)
from ingest import chunk_text, ingest_pdf, ingest_txt, ingest_url, ingest_docx, ingest_doc

logger = logging.getLogger(__name__)

# Embedding dimension for BAAI/bge-large-en-v1.5
EMBED_DIM = 1024


# ============================================================================
# QDRANT MANAGER: Per-user vector store operations
# ============================================================================

class QdrantManager:
    """
    Wraps Qdrant client operations scoped to a single user's collection.
    Collection name format: user_{user_id}
    """

    def __init__(self, user_id: int):
        self.user_id = user_id
        self.collection_name = f"user_{user_id}"
        self.client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
        self._ensure_collection()

    def _ensure_collection(self):
        """Create collection if it doesn't exist."""
        existing = [c.name for c in self.client.get_collections().collections]
        if self.collection_name not in existing:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE)
            )
            logger.info(f"[QDRANT] Created collection: {self.collection_name}")

    def upsert_chunks(self, chunks: List[Dict], embedder: SentenceTransformer, doc_id_prefix: str) -> int:
        """Embed chunks and upsert into Qdrant. Returns chunk count."""
        if not chunks:
            return 0

        texts = [c["text"] for c in chunks]
        embeddings = embedder.encode(texts, convert_to_tensor=False, show_progress_bar=False)

        points = []
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            point_id = abs(hash(f"{doc_id_prefix}_{i}")) % (2**63)
            points.append(PointStruct(
                id=point_id,
                vector=embedding.tolist(),
                payload={
                    "text": chunk["text"],
                    "doc_id_prefix": doc_id_prefix,
                    **chunk["metadata"]
                }
            ))

        self.client.upsert(collection_name=self.collection_name, points=points)
        logger.info(f"[QDRANT] Upserted {len(points)} chunks to {self.collection_name}")
        return len(points)

    def delete_document(self, doc_id_prefix: str) -> None:
        """Remove all chunks for a specific document from Qdrant."""
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=Filter(
                must=[FieldCondition(
                    key="doc_id_prefix",
                    match=MatchValue(value=doc_id_prefix)
                )]
            )
        )
        logger.info(f"[QDRANT] Deleted document: {doc_id_prefix} from {self.collection_name}")

    def list_documents(self) -> List[Dict]:
        """List unique documents in this user's collection."""
        result = self.client.scroll(
            collection_name=self.collection_name,
            with_payload=True,
            limit=10000
        )
        seen = {}
        for point in result[0]:
            prefix = point.payload.get("doc_id_prefix", "")
            if prefix and prefix not in seen:
                seen[prefix] = {
                    "doc_id_prefix": prefix,
                    "source": point.payload.get("source", "Unknown"),
                    "doc_type": point.payload.get("doc_type", "unknown"),
                    "url": point.payload.get("url", ""),
                }
        return list(seen.values())

    def search(self, query_vector: List[float], top_k: int = 8) -> List[Dict]:
        """Semantic search in user's collection."""
        results = self.client.search(
            collection_name=self.collection_name,
            query_vector=query_vector,
            limit=top_k,
            with_payload=True
        )
        return [
            {
                "text": r.payload.get("text", ""),
                "metadata": {k: v for k, v in r.payload.items() if k != "text"},
                "score": r.score
            }
            for r in results
        ]

    def count(self) -> int:
        """Return number of vectors in user's collection."""
        return self.client.count(collection_name=self.collection_name).count


# ============================================================================
# ASYNC INGESTION WRAPPERS
# ============================================================================

async def ingest_pdf_async(file_path: str, title: str, url_hint: str = "") -> Tuple[List[Dict], int]:
    """Async wrapper for ingest_pdf."""
    return await asyncio.to_thread(ingest_pdf, file_path, title, url_hint)


async def ingest_txt_async(file_path: str, title: str, url_hint: str = "") -> Tuple[List[Dict], int]:
    """Async wrapper for ingest_txt."""
    return await asyncio.to_thread(ingest_txt, file_path, title, url_hint)


async def ingest_url_async(url: str, title: str) -> Tuple[List[Dict], int]:
    """Async wrapper for ingest_url."""
    return await asyncio.to_thread(ingest_url, url, title)


# ============================================================================
# BACKGROUND JOB FUNCTION (called by Redis RQ worker)
# ============================================================================

def run_ingestion_job(
    file_path: str,
    title: str,
    doc_type: str,
    user_id: int,
    url: str = "",
    doc_id_prefix: str = ""
) -> int:
    """
    Synchronous function executed by Redis RQ worker.
    Ingests a document and stores in Qdrant for the given user.
    Returns number of chunks stored.
    """
    logger.info(f"[WORKER] Starting ingestion: {title} (user={user_id}, type={doc_type})")

    # Ingest based on type
    if doc_type == "pdf":
        chunks, _ = ingest_pdf(file_path, title, url)
    elif doc_type == "txt":
        chunks, _ = ingest_txt(file_path, title, url)
    elif doc_type == "docx":
        chunks, _ = ingest_docx(file_path, title, url)
    elif doc_type == "doc":
        chunks, _ = ingest_doc(file_path, title, url)
    elif doc_type == "url":
        chunks, _ = ingest_url(url, title)
    else:
        raise ValueError(f"Unknown doc_type: {doc_type}")

    if not chunks:
        raise ValueError(f"No content extracted from {title}")

    # Embed and store in Qdrant
    embedder = SentenceTransformer(EMBED_MODEL, device=EMBED_DEVICE)
    qm = QdrantManager(user_id=user_id)

    if not doc_id_prefix:
        doc_id_prefix = f"{doc_type}_{title.lower().replace(' ', '_')}"

    count = qm.upsert_chunks(chunks, embedder, doc_id_prefix)
    logger.info(f"[WORKER] Ingestion complete: {title} → {count} chunks (user={user_id})")
    return count
