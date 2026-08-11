# Backend deploy image — API + in-process ingestion worker (INGEST_WORKER_IN_API
# defaults to true, app/api/main.py), no local embedding/reranker models baked in.
# Point EMBEDDING_BACKEND=remote / RERANKER_BACKEND=remote at a hosted provider
# (see .env.example) before deploying this image — see requirements-deploy.txt
# for why sentence-transformers/torch are deliberately left out.

FROM python:3.12-slim

WORKDIR /app

# psycopg[binary] ships its own libpq; no extra system packages needed.
COPY requirements-deploy.txt .
RUN pip install --no-cache-dir -r requirements-deploy.txt

# Pre-bake the BGE-M3 tokenizer (used for token-aware chunking at ingest,
# app/ingestion/chunk_tokens.py — independent of the embedding backend) into
# the image, so ingestion never depends on reaching huggingface.co at runtime.
RUN python -c "from transformers import AutoTokenizer; AutoTokenizer.from_pretrained('BAAI/bge-m3')"

COPY app/ ./app/
COPY scripts/init_db.py ./scripts/init_db.py
COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

ENTRYPOINT ["docker-entrypoint.sh"]
