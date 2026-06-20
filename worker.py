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

from redis import Redis
from rq import Queue
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
                if job.rq_job_id == "inline":
                    continue  # inline-fallback job; can't verify via RQ, leave it
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

    # SimpleWorker (in-process, no fork) on ALL platforms.
    #
    # The forking Worker spawns a fresh child per job; the child never inherits
    # the parent's _worker_embedder singleton, so EVERY job reloaded the 1.3 GB
    # BAAI/bge-large model from scratch (~7-10 s) just to embed one doc — a ~10x
    # slowdown on large batches. SimpleWorker runs jobs in the long-lived worker
    # process, so the embedder loads ONCE and every later job reuses it.
    # Trade-off: no per-job process isolation (a hard crash kills the worker and
    # the container restarts), which is fine for this CPU embedding workload.
    worker = SimpleWorker(queues, connection=redis_conn)
    logger.info("Worker ready (SimpleWorker, in-process). Listening on queue: ingestion")
    worker.work()
