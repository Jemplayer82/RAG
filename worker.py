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

from redis import Redis
from rq import Worker, Queue

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

    worker = Worker(queues, connection=redis_conn)
    logger.info("Worker ready. Listening for jobs on queue: ingestion")
    worker.work(with_scheduler=True)
