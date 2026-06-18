FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc libpq-dev curl \
    # Legacy .doc extraction
    antiword \
    # Scrapling browser dependencies
    chromium chromium-driver \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libxcomposite1 libxdamage1 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 libasound2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    (python -m playwright install chromium --with-deps \
     || echo "WARNING: playwright/chromium install failed; JS scraping will fall back to requests")

# Pre-download the embedding model so it is baked into the image rather than
# fetched (~1.3GB) at runtime on the first query. The HF cache is NOT on a
# persistent volume, so without this every container recreation (i.e. every
# deploy) re-downloads the model — making the first query after each deploy
# hang for 20-120s. This layer is cached as long as EMBED_MODEL is unchanged.
ARG EMBED_MODEL=BAAI/bge-large-en-v1.5
ENV EMBED_MODEL=${EMBED_MODEL}
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBED_MODEL}')"

COPY . .

RUN mkdir -p data/raw/uploads

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["gunicorn", "app_fastapi:app", \
     "--workers", "2", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--timeout", "300", \
     "--keep-alive", "5", \
     "--log-level", "info"]
