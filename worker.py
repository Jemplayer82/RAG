"""
Redis RQ background worker for RAG v2.0.
Processes document ingestion jobs queued by the FastAPI app.

Run with:
    python worker.py

Or inside Docker:
    CMD ["python", "worker.py"]
"""

import logging
import os
from datetime import datetime

# Ensure JWT_SECRET + ENCRYPTION_KEY are set before any module reads them.
from secrets_bootstrap import bootstrap_secrets
bootstrap_secrets()

import sys
from redis import Redis
from rq import Worker, Queue
from rq.worker import SimpleWorker

from config import REDIS_URL

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(asctime)s %(name)s — %(message)s"
)
logger = logging.getLogger(__name__)


def get_redis_connection():
    return Redis.from_url(REDIS_URL)


def reap_stale_jobs():
    """
    On worker boot: any IngestionJob still in queued/running state belongs
    to a previous worker that died. Mark them as error so the UI can show
    the failure and the user can retry.
    """
    try:
        from models import IngestionJob, get_session_local
        SessionLocal = get_session_local()
        db = SessionLocal()
        try:
            stale = db.query(IngestionJob).filter(
                IngestionJob.status.in_(["queued", "running"])
            ).all()
            if not stale:
                return
            now = datetime.utcnow()
            for job in stale:
                job.status = "error"
                job.error_msg = "Interrupted by worker restart; please re-submit the source."
                job.completed_at = now
            db.commit()
            logger.info(f"Reaped {len(stale)} stale ingestion job(s) on boot.")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Stale job reap failed (non-fatal): {e}")


if __name__ == "__main__":
    logger.info("RAG Worker starting...")
    logger.info(f"Redis: {REDIS_URL}")

    reap_stale_jobs()

    redis_conn = get_redis_connection()
    queues = [Queue("ingestion", connection=redis_conn)]

    # Use SimpleWorker on Windows (no fork support), Worker on Linux/Docker
    WorkerClass = SimpleWorker if sys.platform == "win32" else Worker
    worker = WorkerClass(queues, connection=redis_conn)
    logger.info("Worker ready. Listening for jobs on queue: ingestion")
    worker.work(with_scheduler=sys.platform != "win32")
