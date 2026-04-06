"""
RAG Query Engine.
- Retrieves top-K chunks from ChromaDB (semantic search)
- Re-ranks with BM25 (keyword match)
- Calls LLM via Ollama
- Returns answer + source citations

Usage:
    from rag import query
    result = query("What does the document say about X?")
    print(result["answer"])
    print(result["sources"])
"""

import logging
from typing import Dict, List, Optional
import json

import chromadb
from sentence_transformers import SentenceTransformer
from rank_bm25 import BM25Okapi
import requests

from config import (
    CHROMA_DIR, CHROMA_COLLECTION,
    EMBED_MODEL, EMBED_DEVICE,
    OLLAMA_BASE_URL, LLM_MODEL,
    TOP_K, RERANK_TOP_K,
    RAG_PROMPT_TEMPLATE,
    DEBUG
)

logging.basicConfig(level=logging.DEBUG if DEBUG else logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# GLOBAL STATE: ChromaDB client, embedder (loaded once)
# ============================================================================

_chroma_client = None
_collection = None
_embedder = None
_bm25_corpus = None


def _init_retrieval_engine():
    """Lazy-load ChromaDB, embedder, and BM25 on first query."""
    global _chroma_client, _collection, _embedder, _bm25_corpus

    if _chroma_client is None:
        logger.info("Initializing retrieval engine...")
        _chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        _collection = _chroma_client.get_or_create_collection(name=CHROMA_COLLECTION)
        _embedder = SentenceTransformer(EMBED_MODEL, device=EMBED_DEVICE)

        all_docs = _collection.get(include=["documents", "metadatas"])
        documents = all_docs.get("documents", [])

        if documents:
            corpus_tokens = [doc.lower().split() for doc in documents]
            _bm25_corpus = BM25Okapi(corpus_tokens)
            logger.info(f"BM25 indexed {len(documents)} documents")
        else:
            logger.warning("No documents found in ChromaDB. Add sources via the web UI.")
            _bm25_corpus = None


def reset_retrieval_engine():
    """Force re-initialization on the next query (call after ingesting new documents)."""
    global _chroma_client, _collection, _embedder, _bm25_corpus
    _chroma_client = None
    _collection = None
    _embedder = None
    _bm25_corpus = None
    logger.info("Retrieval engine reset — will reinitialize on next query")


# ============================================================================
# RETRIEVAL: Semantic search + BM25 re-ranking
# ============================================================================

def retrieve_sources(question: str, k: int = TOP_K) -> List[Dict]:
    """
    Retrieve top-K relevant chunks from ChromaDB.
    Strategy: semantic search -> BM25 re-rank -> return top RERANK_TOP_K

    Args:
        question: User's query
        k: Number of chunks to retrieve before re-ranking

    Returns:
        List of source dicts: {text, metadata}
    """
    _init_retrieval_engine()

    if _collection is None:
        logger.error("ChromaDB collection not initialized")
        return []

    question_embedding = _embedder.encode(question, convert_to_tensor=False)

    results = _collection.query(
        query_embeddings=[question_embedding.tolist()],
        n_results=k,
        include=["documents", "metadatas", "distances"]
    )

    if not results or not results["documents"]:
        logger.warning("No documents found for semantic search")
        return []

    retrieved = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results["distances"][0]

    if _bm25_corpus and len(retrieved) > 0:
        question_tokens = question.lower().split()
        bm25_scores = _bm25_corpus.get_scores(question_tokens)

        combined_scores = []
        for i, doc in enumerate(retrieved):
            semantic_score = 1.0 - distances[i]
            bm25_score = bm25_scores[i] if i < len(bm25_scores) else 0
            combined = 0.6 * semantic_score + 0.4 * bm25_score
            combined_scores.append((i, combined))

        combined_scores.sort(key=lambda x: x[1], reverse=True)
        top_indices = [idx for idx, _ in combined_scores[:RERANK_TOP_K]]
    else:
        top_indices = list(range(min(RERANK_TOP_K, len(retrieved))))

    sources = []
    for idx in top_indices:
        if idx < len(retrieved):
            sources.append({
                "text": retrieved[idx],
                "metadata": metadatas[idx],
            })

    logger.info(f"Retrieved {len(sources)} sources for query")
    return sources


# ============================================================================
# LLM: Call Ollama
# ============================================================================

def _call_ollama_llm(prompt: str) -> str:
    """
    Call the LLM via Ollama.

    Args:
        prompt: Full prompt text

    Returns:
        Generated response string
    """
    active_model = LLM_MODEL
    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": active_model,
                "prompt": prompt,
                "stream": False,
                "temperature": 0.3,
                "top_p": 0.9,
            },
            timeout=120
        )
        response.raise_for_status()
        result = response.json()
        return result.get("response", "")

    except requests.exceptions.ConnectionError:
        logger.error(f"Cannot connect to Ollama at {OLLAMA_BASE_URL}")
        logger.error("Make sure Ollama is running: ollama serve")
        raise
    except Exception as e:
        logger.error(f"Ollama API error: {e}")
        raise


# ============================================================================
# MAIN QUERY INTERFACE
# ============================================================================

def query(question: str, chat_history: Optional[List[Dict]] = None) -> Dict:
    """
    Main RAG query interface.

    Args:
        question: User's natural language question
        chat_history: Previous messages (optional, for context)

    Returns:
        Dict with keys:
        - answer: Generated answer string
        - sources: List of source citation dicts
        - metadata: Debug info
    """
    logger.info(f"Query: {question}")

    sources = retrieve_sources(question, k=TOP_K)

    if not sources:
        return {
            "answer": "I could not find relevant documents to answer your question. Please add documents via the Add Sources page and try again.",
            "sources": [],
            "metadata": {"retrieval_count": 0, "embedder": EMBED_MODEL}
        }

    sources_text = ""
    source_citations = []
    for i, source in enumerate(sources, start=1):
        citation = _format_citation(source["metadata"])
        url = source["metadata"].get("url", "")

        if url:
            sources_text += f"\n[{i}] {citation}\nURL: {url}\n{source['text']}\n"
        else:
            sources_text += f"\n[{i}] {citation}\n{source['text']}\n"

        source_citations.append({
            "index": i,
            "citation": citation,
            "doc_type": source["metadata"].get("doc_type", "unknown"),
            "url": url,
        })

    prompt = RAG_PROMPT_TEMPLATE.format(
        sources_text=sources_text,
        question=question
    )

    answer = _call_ollama_llm(prompt)

    return {
        "answer": answer,
        "sources": source_citations,
        "metadata": {
            "retrieval_count": len(sources),
            "embedder": EMBED_MODEL,
            "question": question,
        }
    }


# ============================================================================
# FORMATTING
# ============================================================================

def _format_citation(metadata: Dict) -> str:
    """Format metadata dict into a readable citation."""
    return metadata.get("source") or metadata.get("case_name") or "Unknown source"


# ============================================================================
# CLI TESTING
# ============================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        query_text = " ".join(sys.argv[1:])
    else:
        query_text = "Summarize the main topics covered in the documents."

    print(f"\n{'=' * 70}")
    print(f"QUESTION: {query_text}")
    print(f"{'=' * 70}\n")

    result = query(query_text)

    print("ANSWER:")
    print(result["answer"])
    print(f"\n{'=' * 70}")
    print("SOURCES:")
    for source in result["sources"]:
        print(f"  [{source['index']}] {source['citation']}")
    print(f"{'=' * 70}\n")
