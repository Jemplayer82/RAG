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


if __name__ == "__main__":
    logger.info("RAG Worker starting...")
    logger.info(f"Redis: {REDIS_URL}")

    redis_conn = get_redis_connection()
    queues = [Queue("ingestion", connection=redis_conn)]

    # Use SimpleWorker on Windows (no fork support), Worker on Linux/Docker
    WorkerClass = SimpleWorker if sys.platform == "win32" else Worker
    worker = WorkerClass(queues, connection=redis_conn)
    logger.info("Worker ready. Listening for jobs on queue: ingestion")
    worker.work(with_scheduler=sys.platform != "win32")
