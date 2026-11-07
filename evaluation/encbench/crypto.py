"""The encryption the benchmark measures: per-company keys, AES-GCM text, word codes.

Shaped like the proposal in docs/encrypted-search-plan.md, not like a product:
a company's working key is "unlocked" by a simulated KMS (a sleep of
``kms_ms``) and kept in memory for ``ttl`` seconds; two keys are derived from
it, one to encrypt text and one to turn words into codes.

Word codes mimic Postgres's English full-text configuration so the keyword leg
matches the same words it does today: lowercase, Postgres's stop words, the
Snowball English stemmer (what `to_tsvector('english', ...)` uses), then a keyed
hash. Tokenising is ``\\w+``: Postgres's parser also keeps hyphenated and
e-mail tokens whole, which this does not, and the accuracy test shows whether
that matters.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import subprocess
import threading
import time
from functools import lru_cache
from pathlib import Path

import snowballstemmer
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_WORD = re.compile(r"\w+")
_STEM = snowballstemmer.stemmer("english")
_STEM_LOCK = threading.Lock()  # snowballstemmer objects are not thread-safe


def _stop_words() -> frozenset[str]:
    share = subprocess.run(["pg_config", "--sharedir"], capture_output=True, text=True).stdout.strip()
    return frozenset(Path(share, "tsearch_data", "english.stop").read_text().split())


STOP = _stop_words()


@lru_cache(maxsize=200_000)
def _stem(word: str) -> str:
    with _STEM_LOCK:
        return _STEM.stemWord(word)


def lexemes(text: str) -> set[str]:
    """The words a keyword search matches on, as Postgres would reduce them."""
    return {_stem(w) for w in _WORD.findall(text.lower()) if w not in STOP}


class OrgKeys:
    """One company's two working keys, as they live in server memory."""

    def __init__(self, working_key: bytes) -> None:
        def derive(label: bytes) -> bytes:
            return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=label).derive(working_key)

        self._aead = AESGCM(derive(b"handbook text"))
        self._code_key = derive(b"handbook word codes")

    def encrypt(self, text: str) -> bytes:
        nonce = os.urandom(12)
        return nonce + self._aead.encrypt(nonce, text.encode(), None)

    def decrypt(self, blob: bytes) -> str:
        blob = bytes(blob)
        return self._aead.decrypt(blob[:12], blob[12:], None).decode()

    def code(self, word: str) -> str:
        return hmac.new(self._code_key, word.encode(), hashlib.sha256).hexdigest()[:16]

    def word_codes(self, text: str) -> list[str]:
        return sorted(self.code(w) for w in lexemes(text))

    def word_ints(self, text: str) -> list[int]:
        """The same codes as 32-bit integers, for Postgres's `intarray` operators.

        Counting shared codes on `int[]` (`icount(a & b)`) ranks ~15x faster
        than intersecting `text[]`. The first 4 bytes of the code's hash,
        signed: two words of one company collide about once per 4 billion
        pairs, which at worst adds a candidate that BM25 then ranks on the
        real text.
        """
        return sorted({int.from_bytes(bytes.fromhex(self.code(w)[:8]), "big", signed=True)
                       for w in lexemes(text)})


class KeyRing:
    """Simulated KMS + the in-memory cache a server would keep.

    ``locked`` stands for the working key as stored in the database (encrypted
    by the company's KMS master key); ``unlock`` costs ``kms_ms`` like a KMS
    round trip and happens only when the cached copy is older than ``ttl``.
    """

    def __init__(self, master: bytes, locked: dict[str, bytes] | None = None,
                 kms_ms: float = 0.0, ttl: float = 300.0) -> None:
        self.kms_ms = kms_ms
        self.ttl = ttl
        self.kms_calls = 0
        self.master = master
        self._master = AESGCM(master)
        self.locked: dict[str, bytes] = dict(locked or {})
        self._cache: dict[str, tuple[float, OrgKeys]] = {}
        self._lock = threading.Lock()

    def save(self, path: Path) -> None:
        path.write_text(json.dumps({"master": self.master.hex(),
                                    "locked": {k: v.hex() for k, v in self.locked.items()}}))

    @classmethod
    def load(cls, path: Path, **kw) -> "KeyRing":
        data = json.loads(path.read_text())
        return cls(bytes.fromhex(data["master"]),
                   {k: bytes.fromhex(v) for k, v in data["locked"].items()}, **kw)

    def create(self, org_id: str) -> None:
        nonce = os.urandom(12)
        self.locked[org_id] = nonce + self._master.encrypt(nonce, os.urandom(32), None)

    def keys(self, org_id: str) -> OrgKeys:
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(org_id)
            if hit and now - hit[0] < self.ttl:
                return hit[1]
        if self.kms_ms:
            time.sleep(self.kms_ms / 1000)
        blob = self.locked[org_id]
        keys = OrgKeys(self._master.decrypt(blob[:12], blob[12:], None))
        with self._lock:
            self.kms_calls += 1
            self._cache[org_id] = (time.monotonic(), keys)
        return keys

    def forget(self) -> None:
        with self._lock:
            self._cache.clear()


if __name__ == "__main__":
    ring = KeyRing(os.urandom(32))
    ring.create("a"), ring.create("b")
    a, b = ring.keys("a"), ring.keys("b")
    text = "Invoices 4521 and 4522 were paid on 3 March."
    assert a.decrypt(a.encrypt(text)) == text
    assert a.encrypt(text) != a.encrypt(text), "a fresh nonce per encryption"
    assert lexemes(text) == {"invoic", "4521", "4522", "paid", "3", "march"}, lexemes(text)
    assert set(a.word_codes("Was invoice 4521 paid?")) <= set(a.word_codes(text))
    assert not set(a.word_codes(text)) & set(b.word_codes(text)), "codes differ per company"
    print("ok")
