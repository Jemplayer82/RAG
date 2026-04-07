"""
Async RAG Query Engine for v2.0 multi-user system.
- Retrieves from Qdrant (per-user collection) instead of ChromaDB
- Re-ranks with BM25 (same as v1.0)
- Calls Ollama via httpx (async HTTP)
- Per-user namespace isolation
"""

import asyncio
import logging
from typing import Dict, List, Optional

import httpx
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

from config import (
    EMBED_MODEL, EMBED_DEVICE,
    OLLAMA_BASE_URL, LLM_MODEL,
    TOP_K, RERANK_TOP_K,
    RAG_PROMPT_TEMPLATE
)
from ingest_async import QdrantManager

logger = logging.getLogger(__name__)

# Global embedder (shared across requests, loaded once)
_embedder: Optional[SentenceTransformer] = None


def _get_embedder() -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        logger.info(f"Loading embedder: {EMBED_MODEL}")
        _embedder = SentenceTransformer(EMBED_MODEL, device=EMBED_DEVICE)
    return _embedder


# ============================================================================
# RETRIEVAL: Semantic search + BM25 re-ranking (per user)
# ============================================================================

def _retrieve_sources_sync(question: str, user_id: int, k: int = TOP_K) -> List[Dict]:
    """
    Sync retrieval: semantic search in Qdrant + BM25 re-ranking.
    Wrapped in asyncio.to_thread() for async use.
    """
    embedder = _get_embedder()
    qm = QdrantManager(user_id=user_id)

    # Embed query
    query_vector = embedder.encode(question, convert_to_tensor=False).tolist()

    # Semantic search from Qdrant
    raw_results = qm.search(query_vector, top_k=k)

    if not raw_results:
        logger.warning(f"[RAG] No results for user {user_id}")
        return []

    # BM25 re-ranking
    if len(raw_results) > 1:
        corpus_tokens = [r["text"].lower().split() for r in raw_results]
        bm25 = BM25Okapi(corpus_tokens)
        question_tokens = question.lower().split()
        bm25_scores = bm25.get_scores(question_tokens)

        combined = []
        for i, result in enumerate(raw_results):
            semantic_score = result.get("score", 0.0)
            bm25_score = float(bm25_scores[i]) if i < len(bm25_scores) else 0.0
            combined_score = 0.6 * semantic_score + 0.4 * bm25_score
            combined.append((i, combined_score))

        combined.sort(key=lambda x: x[1], reverse=True)
        top_indices = [idx for idx, _ in combined[:RERANK_TOP_K]]
    else:
        top_indices = [0]

    sources = [raw_results[i] for i in top_indices if i < len(raw_results)]
    logger.info(f"[RAG] Retrieved {len(sources)} sources for user {user_id}")
    return sources


# ============================================================================
# LLM: Async Ollama call via httpx
# ============================================================================

async def _call_ollama_async(prompt: str) -> str:
    """Call Ollama API asynchronously using httpx."""
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": LLM_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.3,
                    "top_p": 0.9,
                }
            }
        )
        response.raise_for_status()
        return response.json().get("response", "")


# ============================================================================
# MAIN ASYNC QUERY INTERFACE
# ============================================================================

async def query_async(
    question: str,
    user_id: int,
    chat_history: Optional[List[Dict]] = None
) -> Dict:
    """
    Main async RAG query interface.

    Args:
        question: User's natural language question
        user_id: ID of the authenticated user (for Qdrant namespace)
        chat_history: Previous messages (optional, for context)

    Returns:
        Dict with keys: answer, sources, metadata
    """
    logger.info(f"[RAG] Query from user {user_id}: {question[:80]}")

    # Retrieve in thread pool (blocking I/O)
    sources = await asyncio.to_thread(_retrieve_sources_sync, question, user_id, TOP_K)

    if not sources:
        return {
            "answer": "I could not find relevant documents to answer your question. Please add documents via the Add Sources page and try again.",
            "sources": [],
            "metadata": {"retrieval_count": 0, "embedder": EMBED_MODEL}
        }

    # Build context
    sources_text = ""
    source_citations = []
    for i, source in enumerate(sources, start=1):
        meta = source["metadata"]
        citation = meta.get("source") or meta.get("case_name") or "Unknown source"
        url = meta.get("url", "")

        if url:
            sources_text += f"\n[{i}] {citation}\nURL: {url}\n{source['text']}\n"
        else:
            sources_text += f"\n[{i}] {citation}\n{source['text']}\n"

        source_citations.append({
            "index": i,
            "citation": citation,
            "doc_type": meta.get("doc_type", "unknown"),
            "url": url,
        })

    prompt = RAG_PROMPT_TEMPLATE.format(
        sources_text=sources_text,
        question=question
    )

    # Call LLM asynchronously
    answer = await _call_ollama_async(prompt)

    return {
        "answer": answer,
        "sources": source_citations,
        "metadata": {
            "retrieval_count": len(sources),
            "embedder": EMBED_MODEL,
            "question": question,
        }
    }
