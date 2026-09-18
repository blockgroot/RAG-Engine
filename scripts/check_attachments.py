"""Did an uploaded file actually reach the object store?

Prints the database rows, the Cloudinary objects, and whether each row's pair
(original + plaintext) is really there. Three states worth telling apart:

- `key=None, text_offloaded=False` -- a row written BEFORE the move to the
  object store. Its text is still in the `content` column and it answers
  through `_resolve_text`'s legacy fallback. Nothing is wrong.
- `key=... original=yes text=yes` -- the normal state.
- `key=... MISSING` -- the row points at an object that is not there, which
  reads as an empty document on every question. That is the failure
  `save_attachment`'s rollback exists to prevent, so finding one means
  something deleted the object afterwards.

Run:  python -m scripts.check_attachments
"""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

from app.attachments import blobstore
from app.db import get_connection

from app.config.settings import CloudinarySettings

_folder = CloudinarySettings.from_env().folder
print(f"database: the DATABASE_URL in your environment")
print(f"cloudinary folder: {_folder}")
print(
    "  NOTE: these two are configured separately. Reading the PRODUCTION "
    "database\n  while CLOUDINARY_FOLDER points at your dev folder reports "
    "every row as\n  MISSING, because the objects are in the other folder. "
    "Set both to the same\n  environment before trusting the match below.\n"
)

print("=== Postgres rows ===")
with get_connection() as conn:
    rows = conn.execute(
        "SELECT id::text, filename, char_count, storage_key, content IS NULL, created_at "
        "FROM conversation_attachments ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
if not rows:
    print("  (no attachments in the database)")
for r in rows:
    print(f"  {r[1]:<28} chars={r[2]:<8} key={r[3]} text_offloaded={r[4]}  {r[5]:%Y-%m-%d %H:%M}")

print("\n=== Cloudinary objects ===")
try:
    keys = blobstore.list_keys()
    if not keys:
        print("  (nothing stored under this folder)")
    for k in sorted(keys):
        print(f"  {k}")
except Exception as exc:
    print(f"  could not list: {exc}")

print("\n=== Matched? ===")
db_keys = {r[3] for r in rows if r[3]}
for key in sorted(db_keys):
    have_file = key in keys
    have_text = blobstore.plaintext_key(key) in keys
    print(f"  {key}: original={'yes' if have_file else 'MISSING'} text={'yes' if have_text else 'MISSING'}")
