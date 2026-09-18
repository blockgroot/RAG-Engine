"""Admission test for `app/attachments/blobstore.py` — run before trusting uploads.

Every test for the object store fakes it, so the SDK call shapes here are
written from the documented API and have never met a real account. What a
fake cannot check is exactly what breaks a deployment:

- an `authenticated` raw upload must be DELIVERABLE by a signed URL. Cloudinary
  restricts some media types at the account level, and a blocked one 404s at
  delivery while the upload itself succeeds — so a file lands, the row is
  written, and every later question sees an empty document.
- a signed URL must actually expire, and an UNSIGNED one must fail. If the
  asset turns out to be publicly readable, the whole reason this is acceptable
  for tenant documents is gone.
- `destroy` must really remove it, or the TTL sweep is decorative.

Run:  python -m scripts.verify_cloudinary
Exits non-zero on the first failure, and always tries to clean up after itself.
"""

from __future__ import annotations

import sys
import time
import uuid

from dotenv import load_dotenv

load_dotenv()

from app.attachments import blobstore  # noqa: E402
from app.config.settings import CloudinarySettings  # noqa: E402

PROBE_TEXT = "Employees get 25 days of paid annual leave per year."
# A real (tiny) PDF: PDFs are the format most likely to be blocked at delivery
# by an account-level media restriction, and the one members upload most.
PROBE_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 99 9]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)


def _ok(label: str) -> None:
    print(f"  PASS  {label}")


def _fail(label: str, detail: object) -> None:
    print(f"  FAIL  {label}\n        {detail}")


def main() -> int:
    settings = CloudinarySettings.from_env()
    if not settings.configured:
        print(
            "Cloudinary is not configured. Set CLOUDINARY_CLOUD_NAME, "
            "CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET."
        )
        return 2

    print(f"cloud: {settings.cloud_name}   folder: {settings.folder}")
    key = f"verify-{uuid.uuid4().hex[:12]}"
    text_key = blobstore.plaintext_key(key)
    failures = 0

    try:
        # 1. text round trip -- the steady-state read on every turn of a chat
        try:
            blobstore.save_text(text_key, PROBE_TEXT)
            back = blobstore.read_text(text_key)
            assert back == PROBE_TEXT, f"got {back!r}"
            _ok("plaintext upload + signed read")
        except Exception as exc:  # noqa: BLE001
            _fail("plaintext upload + signed read", exc)
            failures += 1

        # 2. a PDF must survive upload AND delivery
        try:
            blobstore.save_bytes(key, PROBE_PDF, content_type="application/pdf")
            raw = blobstore.read_bytes(key)
            assert raw.startswith(b"%PDF"), f"got {raw[:20]!r}"
            _ok("pdf upload + signed read")
        except Exception as exc:  # noqa: BLE001
            _fail(
                "pdf upload + signed read",
                f"{exc}\n        If the upload worked and the READ 404s, check "
                "Cloudinary\n        Settings > Security for a restriction on this "
                "media type.",
            )
            failures += 1

        # 3. the asset must NOT be readable without a signature
        try:
            import httpx

            unsigned = (
                f"https://res.cloudinary.com/{settings.cloud_name}"
                f"/raw/authenticated/{settings.folder}/{key}"
            )
            response = httpx.get(unsigned, timeout=20.0, follow_redirects=True)
            assert response.status_code >= 400, (
                f"an unsigned URL returned {response.status_code} -- this asset is "
                "PUBLIC"
            )
            _ok(f"unsigned URL is refused ({response.status_code})")
        except AssertionError as exc:
            _fail("unsigned URL is refused", exc)
            failures += 1
        except Exception as exc:  # noqa: BLE001
            _fail("unsigned URL is refused", f"could not check: {exc}")
            failures += 1

        # 4. a link handed to a BROWSER must stop working.
        #
        # This is the check that caught `cloudinary_url(sign_url=True,
        # expires_at=...)` silently dropping the expiry -- it returned a
        # byte-identical URL at any TTL, so every "signed" link was a permanent
        # grant. `signed_url` uses `private_download_url` for that reason, and
        # this asserts the behaviour rather than the implementation.
        try:
            import httpx

            live = blobstore.signed_url(key, ttl_seconds=600)
            dead = blobstore.signed_url(key, ttl_seconds=-600)
            assert live != dead, (
                "the same URL at two TTLs -- the expiry is being dropped, so "
                "every link is permanent"
            )
            live_response = httpx.get(live, timeout=20.0, follow_redirects=True)
            assert live_response.status_code == 200, (
                f"a valid signed link returned {live_response.status_code}"
            )
            dead_response = httpx.get(dead, timeout=20.0, follow_redirects=True)
            assert dead_response.status_code >= 400, (
                f"an expired link returned {dead_response.status_code} -- "
                "signatures are not being enforced"
            )
            _ok(
                f"signed link works ({live_response.status_code}) and expires "
                f"({dead_response.status_code})"
            )
        except AssertionError as exc:
            _fail("signed link expires", exc)
            failures += 1
        except Exception as exc:  # noqa: BLE001
            _fail("signed link expires", f"could not check: {exc}")
            failures += 1

        # 5. listing must find what we just wrote (orphan detection depends on it)
        try:
            keys = blobstore.list_keys()
            assert key in keys and text_key in keys, (
                f"listing returned {len(keys)} keys, without this probe's"
            )
            _ok(f"prefix listing sees both objects ({len(keys)} total)")
        except Exception as exc:  # noqa: BLE001
            _fail("prefix listing", exc)
            failures += 1

    finally:
        # 6. delete must really delete -- otherwise the TTL sweep is decorative
        time.sleep(1)
        removed = [blobstore.delete_object(key), blobstore.delete_object(text_key)]
        if all(removed):
            _ok("destroy removes both objects")
        else:
            _fail("destroy removes both objects", f"delete returned {removed}")
            failures += 1

    print()
    if failures:
        print(f"{failures} check(s) failed — do not enable uploads yet.")
        return 1
    print("All checks passed. Uploads are safe to enable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
