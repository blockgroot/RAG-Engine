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

# Compiled once here, not on every start. The base image ships its standard
# library without .pyc files, and a container starts with none of ours, so a
# cold start on a small CPU (Render's free plan) compiled everything it
# imported before it could answer. pip already compiled the packages.
RUN python -m compileall -q -j 0 -x 'site-packages' /usr/local/lib/python3.12

# No tokenizer is pre-baked any more. Chunking defaults to
# CHUNK_TOKEN_BACKEND=heuristic (app/ingestion/chunk_tokens.py), which needs no
# model, no vocab download, and no `transformers`/`tokenizers` package — because
# loading BGE-M3's tokenizer measured at ~611MB RSS against this deployment's
# 512MB hard limit and OOM-killed every ingestion run. The estimator is
# calibrated against that same tokenizer and errs slightly small (safe
# direction) — see the module docstring for the numbers.

COPY app/ ./app/
RUN python -m compileall -q -j 0 /app/app
# run_worker.py: the self-hosted install runs this same image as its worker.
COPY scripts/boot.py scripts/init_db.py scripts/check_llm_config.py scripts/check_embedding_config.py \
     scripts/run_worker.py scripts/create_company.py scripts/delete_company.py \
     scripts/export_company.py ./scripts/
COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# The release build passes the tag, so an installed copy knows its version
# even when its .env leaves HANDBOOK_VERSION blank (app/config/settings.py).
ARG HANDBOOK_IMAGE_VERSION=""
ENV HANDBOOK_IMAGE_VERSION=${HANDBOOK_IMAGE_VERSION}

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Not root: a code-execution bug in the app is then not root in the container.
# Nothing here writes to the image at run time (bytecode is compiled above).
RUN useradd --system --uid 10001 --no-create-home handbook
USER handbook

ENTRYPOINT ["docker-entrypoint.sh"]
