"""
Flask web application for generic RAG.
- Chat interface with session history
- Document library browser
- PDF, TXT, and URL ingestion
- Re-indexing controls

Routes:
- GET  /          → Chat interface
- POST /ask       → Submit query, return answer + sources
- GET  /library   → View indexed documents
- GET  /upload    → Document upload/add page
- POST /reindex   → Rebuild ChromaDB index (background)
- GET  /reindex-progress → Poll reindex status
- GET  /api/sources      → List user-added sources
- POST /api/sources      → Add a new source (pdf, txt, or url)
- DELETE /api/sources/<id> → Remove a source
"""

import logging
import os
from threading import Thread
from datetime import datetime

from flask import Flask, render_template, request, jsonify, session
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename
import chromadb
from sentence_transformers import SentenceTransformer

from rag import query as rag_query, reset_retrieval_engine
from ingest import (
    build_index,
    ingest_pdf, ingest_txt, ingest_url,
    embed_and_store,
    load_custom_sources, save_custom_source, remove_custom_source,
)
from config import (
    CHROMA_DIR, CHROMA_COLLECTION, RAW_DIR,
    EMBED_MODEL, OLLAMA_BASE_URL, LLM_MODEL, CACHE_ROOT, DEBUG
)
from settings import load_settings, save_settings

# ============================================================================
# SETUP
# ============================================================================

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "rag_secret_key_change_in_production")

if DEBUG:
    app.config["DEBUG"] = True

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

reindex_progress = {"status": "idle", "message": "", "timestamp": None}

# ============================================================================
# TEMPLATE FILTERS
# ============================================================================

@app.template_filter("datetime")
def format_datetime(dt):
    if isinstance(dt, str):
        return dt
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "Never"

# ============================================================================
# ROUTES: Chat Interface
# ============================================================================

@app.route("/")
def chat():
    if "chat_history" not in session:
        session["chat_history"] = []
    return render_template("chat.html")


@app.route("/ask", methods=["POST"])
def ask():
    try:
        data = request.json
        question = data.get("question", "").strip()

        if not question:
            return jsonify({"error": "Question cannot be empty"}), 400

        chat_history = session.get("chat_history", [])[-20:]

        logger.info(f"Processing: {question}")
        result = rag_query(question, chat_history=chat_history)

        session["chat_history"] = chat_history + [
            {"role": "user", "content": question},
            {"role": "assistant", "content": result["answer"]}
        ]
        session.modified = True

        return jsonify({
            "answer": result["answer"],
            "sources": result["sources"],
            "error": None
        })

    except ConnectionError as e:
        logger.error(f"Ollama connection error: {e}")
        return jsonify({
            "error": "Cannot connect to Ollama. Make sure Ollama is running: ollama serve"
        }), 503
    except Exception as e:
        logger.error(f"Query error: {e}")
        return jsonify({"error": f"Error: {str(e)}"}), 500

# ============================================================================
# ROUTES: Document Library
# ============================================================================

@app.route("/library")
def library():
    try:
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        collection = client.get_or_create_collection(name=CHROMA_COLLECTION)

        all_docs = collection.get(include=["metadatas"])
        metadatas = all_docs.get("metadatas", [])

        docs_by_source = {}
        for meta in metadatas:
            doc_type = meta.get("doc_type", "unknown")
            source = meta.get("source") or meta.get("case_name", "Unknown")

            if source not in docs_by_source:
                docs_by_source[source] = {
                    "source": source,
                    "doc_type": doc_type,
                    "chunk_count": 0,
                    "url": meta.get("url", ""),
                    "added_by": meta.get("added_by", "user"),
                }
            docs_by_source[source]["chunk_count"] += 1

        # Merge with custom_sources.json to catch sources not yet in ChromaDB
        custom_sources = load_custom_sources()
        for cs in custom_sources:
            key = cs.get("title", "")
            if key and key not in docs_by_source:
                docs_by_source[key] = {
                    "source": key,
                    "doc_type": cs.get("type", "unknown"),
                    "chunk_count": cs.get("chunks", 0),
                    "url": cs.get("url", ""),
                    "added_by": "user",
                }

        documents = sorted(docs_by_source.values(), key=lambda x: x["source"])
        total_chunks = len(metadatas)
        last_indexed = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        return render_template(
            "library.html",
            documents=documents,
            total_chunks=total_chunks,
            last_indexed=last_indexed
        )

    except Exception as e:
        logger.error(f"Library error: {e}")
        return render_template("library.html", error=str(e), documents=[], total_chunks=0)

# ============================================================================
# ROUTES: Upload / Source Management
# ============================================================================

@app.route("/upload")
def upload_page():
    return render_template("upload.html")


@app.route("/api/sources", methods=["GET"])
def api_get_sources():
    try:
        sources = load_custom_sources()
        return jsonify({"sources": sources})
    except Exception as e:
        logger.error(f"Get sources error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/sources", methods=["POST"])
def api_add_source():
    """
    Add a new source.

    For pdf/txt: multipart/form-data with fields: type, title, file
    For url:     application/json with fields: type, title, url
    """
    try:
        # Detect content type to differentiate file upload vs JSON
        content_type = request.content_type or ""

        if "multipart/form-data" in content_type:
            source_type = request.form.get("type", "").lower()
            title = request.form.get("title", "").strip()
            file = request.files.get("file")

            if not title:
                return jsonify({"error": "Document title required"}), 400
            if not file:
                return jsonify({"error": "File required"}), 400

            safe_name = secure_filename(file.filename)
            dest = RAW_DIR / "uploads" / safe_name
            file.save(str(dest))

            if source_type == "pdf":
                chunks, page_count = ingest_pdf(str(dest), title)
                logger.info(f"PDF ingested: {title} ({page_count} pages)")
            elif source_type == "txt":
                chunks, line_count = ingest_txt(str(dest), title)
                logger.info(f"TXT ingested: {title} ({line_count} lines)")
            else:
                return jsonify({"error": f"Unknown file type: {source_type}"}), 400

            doc_id_prefix = f"{source_type}_{title.lower().replace(' ', '_')}"
            cached_path = str(dest)
            url = ""

        else:
            # JSON body for URL ingestion
            data = request.get_json(silent=True) or {}
            source_type = data.get("type", "").lower()
            title = data.get("title", "").strip()
            url = data.get("url", "").strip()

            if source_type != "url":
                return jsonify({"error": f"Unknown source type: {source_type}"}), 400
            if not title:
                return jsonify({"error": "Document title required"}), 400
            if not url:
                return jsonify({"error": "URL required"}), 400

            chunks, word_count = ingest_url(url, title)
            logger.info(f"URL ingested: {title} ({word_count} words)")

            doc_id_prefix = f"url_{title.lower().replace(' ', '_')}"
            cached_path = ""

        if not chunks:
            return jsonify({"error": "No content could be extracted from the source"}), 400

        # Embed and store in ChromaDB
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        collection = client.get_or_create_collection(name=CHROMA_COLLECTION)
        embedder = SentenceTransformer(EMBED_MODEL)

        count = embed_and_store(chunks, collection, embedder, doc_id_prefix)

        # Persist source metadata
        save_custom_source({
            "type": source_type,
            "title": title,
            "url": url,
            "cached_path": cached_path,
            "chunks": count,
        })

        # Reset retrieval engine so BM25 corpus is rebuilt on next query
        reset_retrieval_engine()

        return jsonify({
            "status": "added",
            "type": source_type,
            "title": title,
            "chunks": count
        })

    except ValueError as e:
        logger.error(f"Ingestion error: {e}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Add source error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/sources/<source_id>", methods=["DELETE"])
def api_delete_source(source_id):
    try:
        source_id = source_id.strip()
        logger.info(f"Removing source: {source_id}")
        remove_custom_source(source_id)
        reset_retrieval_engine()
        return jsonify({"status": "removed", "id": source_id})
    except Exception as e:
        logger.error(f"Delete source error: {e}")
        return jsonify({"error": str(e)}), 500

# ============================================================================
# ROUTES: Reindexing (background)
# ============================================================================

def _reindex_worker(force_refresh: bool = False):
    global reindex_progress
    try:
        reindex_progress["status"] = "running"
        reindex_progress["message"] = "Building index..."
        reindex_progress["timestamp"] = datetime.now().isoformat()

        build_index(force_refresh=force_refresh)
        reset_retrieval_engine()

        reindex_progress["status"] = "complete"
        reindex_progress["message"] = "Index built successfully"
        reindex_progress["timestamp"] = datetime.now().isoformat()
        logger.info("Reindexing complete")

    except Exception as e:
        reindex_progress["status"] = "error"
        reindex_progress["message"] = str(e)
        reindex_progress["timestamp"] = datetime.now().isoformat()
        logger.error(f"Reindexing error: {e}")


@app.route("/reindex", methods=["POST"])
def reindex():
    global reindex_progress

    if reindex_progress["status"] == "running":
        return jsonify({"error": "Reindex already in progress"}), 409

    force_refresh = request.args.get("force_refresh", "false").lower() == "true"

    thread = Thread(target=_reindex_worker, args=(force_refresh,), daemon=True)
    thread.start()

    return jsonify({"status": "started", "message": "Reindexing in background..."})


@app.route("/reindex-progress", methods=["GET"])
def reindex_progress_check():
    return jsonify(reindex_progress)

# ============================================================================
# ROUTES: Settings
# ============================================================================

@app.route("/settings")
def settings_page():
    return render_template("settings.html")


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    """Return current user settings merged with config defaults."""
    try:
        current = load_settings()
        return jsonify({
            "data_dir": current.get("data_dir", str(CACHE_ROOT)),
        })
    except Exception as e:
        logger.error(f"Get settings error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    """
    Save one or more settings.

    Request JSON: { "data_dir": "..." }
    data_dir changes require an app restart to take effect.
    """
    try:
        data = request.get_json(silent=True) or {}
        updates = {}

        if "data_dir" in data:
            updates["data_dir"] = data["data_dir"].strip()

        if not updates:
            return jsonify({"error": "No valid settings provided"}), 400

        save_settings(updates)

        restart_required = "data_dir" in updates
        return jsonify({
            "status": "saved",
            "updated": list(updates.keys()),
            "restart_required": restart_required,
        })
    except Exception as e:
        logger.error(f"Save settings error: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": "Internal server error"}), 500



# ============================================================================
# RUN
# ============================================================================

if __name__ == "__main__":
    logger.info("Starting RAG Flask app...")
    logger.info("Open http://localhost:5000 in your browser")
    app.run(host="127.0.0.1", port=5000, debug=DEBUG)
