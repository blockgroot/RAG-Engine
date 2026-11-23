"""Export one company's data, every table that carries ``org_id``, as JSON lines.

For a company leaving, or a data request. One ``<table>.jsonl`` per table in the
output folder. Left out: credentials (``*_encrypted``, one-time tokens and their
hashes, app keys), embedding vectors (useless outside, and large) and storage
keys. Uploaded file bytes are not fetched; their extracted text is in
``conversation_attachments``. Chunks are read through the company's own
row-level scope.

Run (inside the api container):
    python scripts/export_company.py <org-id> /tmp/acme-export
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

#: Columns never exported, by name.
SECRET_COLUMN = re.compile(r"(_encrypted$|^token$|_token_hash$|^embedding$|^storage_key$|^app_key$)")


def tenant_tables(conn) -> dict[str, list[str]]:
    """``{table: exported columns}`` for every top-level table with ``org_id``
    (a partition is read through its parent)."""
    rows = conn.execute(
        """
        SELECT c.table_name, c.column_name
          FROM information_schema.columns c
          JOIN pg_class k ON k.relname = c.table_name AND k.relkind IN ('r', 'p')
                         AND NOT k.relispartition
          JOIN pg_namespace n ON n.oid = k.relnamespace AND n.nspname = 'public'
         WHERE c.table_schema = 'public'
           AND c.table_name IN (SELECT table_name FROM information_schema.columns
                                 WHERE table_schema = 'public' AND column_name = 'org_id')
         ORDER BY c.table_name, c.ordinal_position
        """
    ).fetchall()
    tables: dict[str, list[str]] = {}
    for table, column in rows:
        if not SECRET_COLUMN.search(column):
            tables.setdefault(table, []).append(column)
    return tables


def export_company(org_id: str, out_dir: Path) -> dict[str, int]:
    """Write the files; returns rows written per table."""
    from psycopg import sql

    from app.db.connection import get_connection

    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    with get_connection(org_id=org_id) as conn:
        cur = conn.execute("SELECT * FROM organizations WHERE id = %s::uuid", (org_id,))
        company = cur.fetchone()
        if company is None:
            raise SystemExit(f"No company {org_id}.")
        # The company's own row is keyed by id, not org_id.
        names = [d.name for d in cur.description]
        (out_dir / "organizations.jsonl").write_text(json.dumps(
            {k: v for k, v in zip(names, company) if not SECRET_COLUMN.search(k)},
            default=str) + "\n")
        counts["organizations"] = 1
        for table, columns in tenant_tables(conn).items():
            query = sql.SQL("SELECT {} FROM {} WHERE org_id = %s::uuid").format(
                sql.SQL(", ").join(map(sql.Identifier, columns)), sql.Identifier(table))
            n = 0
            with (out_dir / f"{table}.jsonl").open("w") as handle, conn.cursor() as cur:
                for row in cur.stream(query, (org_id,)):
                    handle.write(json.dumps(dict(zip(columns, row)), default=str) + "\n")
                    n += 1
            counts[table] = n
    return counts


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Export one company's data.")
    parser.add_argument("org_id")
    parser.add_argument("out_dir", type=Path)
    args = parser.parse_args(argv)
    load_dotenv()
    from app.db import close_pool

    try:
        counts = export_company(args.org_id, args.out_dir)
    finally:
        close_pool()
    for table, n in counts.items():
        print(f"{table}: {n}")
    print(f"Wrote {sum(counts.values())} rows to {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
