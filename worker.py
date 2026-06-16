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
    On worker boot: mark a queued/running IngestionJob as error ONLY if it is
    not still live in Redis. A job that is still queued/started (or already
    finished) in RQ is left alone — the worker will process it or the poller
    will sync it. Blindly erroring every queued/running row would falsely fail
    jobs that Redis is about to run, causing the UI to delete a document that
    actually ingested successfully.
    """
    try:
        from models import IngestionJob, get_session_local
        from rq.job import Job
        from rq.exceptions import NoSuchJobError

        SessionLocal = get_session_local()
        db = SessionLocal()
        try:
            stale = db.query(IngestionJob).filter(
                IngestionJob.status.in_(["queued", "running"])
            ).all()
            if not stale:
                return

            redis_conn = get_redis_connection()
            now = datetime.utcnow()
            reaped = 0
            for job in stale:
                if job.rq_job_id:
                    try:
                        rq_status = Job.fetch(job.rq_job_id, connection=redis_conn).get_status()
                        # Still live, or already succeeded — leave for the worker/poller.
                        if rq_status in ("queued", "started", "deferred", "scheduled", "finished"):
                            continue
                    except NoSuchJobError:
                        pass  # gone from Redis → truly stale
                # No rq_job_id, missing from Redis, or failed/stopped/canceled → reap.
                job.status = "error"
                job.error_msg = "Interrupted by worker restart; please re-submit the source."
                job.completed_at = now
                reaped += 1

            if reaped:
                db.commit()
                logger.info(f"Reaped {reaped} stale ingestion job(s) on boot.")
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
