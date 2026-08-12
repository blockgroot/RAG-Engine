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

# No tokenizer is pre-baked any more. Chunking defaults to
# CHUNK_TOKEN_BACKEND=heuristic (app/ingestion/chunk_tokens.py), which needs no
# model, no vocab download, and no `transformers`/`tokenizers` package — because
# loading BGE-M3's tokenizer measured at ~611MB RSS against this deployment's
# 512MB hard limit and OOM-killed every ingestion run. The estimator is
# calibrated against that same tokenizer and errs slightly small (safe
# direction) — see the module docstring for the numbers.

COPY app/ ./app/
COPY scripts/init_db.py ./scripts/init_db.py
COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

ENTRYPOINT ["docker-entrypoint.sh"]
