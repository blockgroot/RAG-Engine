"""Load the frozen pilot into the local Onyx (plan §6.3).

``POST /onyx-api/ingestion`` with ``document.id = dsid`` so Onyx's
``top_documents[].document_id`` IS the benchmark's gold id — no mapping table.
Text comes through the same ``read_doc`` as the Handbook loader, so both
systems index byte-identical content. The source label is the corpus folder
name, which is already Onyx's own ``DocumentSource`` value (Onyx built the
dataset). Onyx chunks and embeds it the way it ships; that is the product.

Idempotent: documents Onyx already reports are skipped.

Run: .venv/bin/python -m evaluation.erb.load_onyx --data ~/Desktop/bench1-data [--limit 20]
"""

from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from dotenv import load_dotenv

from .load_handbook import MANIFEST, read_doc

load_dotenv(".env.bench", override=True)


def onyx_client() -> httpx.Client:
    """Logged-in client. The admin account has every permission, so this does
    not depend on API-key groups (Enterprise-only in the free edition)."""
    http = httpx.Client(base_url=os.environ["ONYX_URL"].rstrip("/") + "/", timeout=600)
    r = http.post("auth/login", data={
        "username": os.environ["ONYX_ADMIN_EMAIL"], "password": os.environ["ONYX_ADMIN_PASSWORD"]})
    r.raise_for_status()
    return http


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()
    manifest = json.loads(MANIFEST.read_text())
    http = onyx_client()

    done: set[str] = set()
    r = http.get("onyx-api/ingestion")
    if r.status_code == 200:
        done = {d["document_id"] for d in r.json()}
    ids = [d for d in manifest["gold_doc_ids"] + manifest["noise_doc_ids"] if d not in done][: args.limit]

    def put(dsid: str) -> str | None:
        text = read_doc(args.data, dsid)
        title = text.strip().splitlines()[0][:300] if text.strip() else dsid
        doc = {
            "id": dsid,
            "sections": [{"type": "text", "text": text}],
            "source": manifest["doc_paths"][dsid].split("/")[0],
            "semantic_identifier": title,
            "title": title,
            "metadata": {},
        }
        for attempt in range(4):
            resp = http.post("onyx-api/ingestion", json={"document": doc})
            if resp.status_code == 200:
                return None
            time.sleep(5 * (attempt + 1))
        return f"{dsid}: HTTP {resp.status_code} {resp.text[:200]}"

    t0, failures = time.time(), []
    with ThreadPoolExecutor(args.workers) as pool:
        for i, err in enumerate(pool.map(put, ids), 1):
            if err:
                failures.append(err)
            if i % 50 == 0:
                print(f"{i}/{len(ids)} docs, {len(failures)} failed, {time.time() - t0:.0f}s", flush=True)
    print(f"done: {len(ids)} docs, {len(failures)} failed, {time.time() - t0:.0f}s")
    for f in failures[:10]:
        print("FAILED", f)


if __name__ == "__main__":
    main()
