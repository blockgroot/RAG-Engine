"""Link provenance: an answer may only carry a link its sources carried.

Every real prompt-injection leak from a RAG assistant went out through a URL:
EchoLeak (an image URL the client fetched), Slack AI (a "click here to
reauthenticate" link), ChatGPT and Bard (image URLs on an allowlisted domain).
The model writes the URL, so the model can be steered into putting a tenant's
data in its query string. This is the deterministic answer to all of them: a
URL survives only if it appears VERBATIM in the text the model was shown, so
the model can repeat a link but never compose one. No model call, no latency.

A host on the allowlist (the connected tools' own domains) is also kept, but
cut to scheme+host+path: a Notion or GitHub link the model recalls is useful,
while a query string on it (a prefilled Google Form, `?title=<secret>`) is
exactly where data rides out. Everything else becomes ``[link removed]``.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit

from .untrusted import normalize_untrusted

logger = logging.getLogger(__name__)

REMOVED = "[link removed]"

# Absolute URLs, `www.` hosts, and the schemes that act on a click
# (`mailto:attacker?body=<secret>`, `javascript:`, `data:`). The URL itself is
# what gets replaced, so inline `[t](url)`, reference-style `[ref]: url`
# (EchoLeak's bypass), images `![a](url)` and bare links are all covered by the
# one pattern: every form contains the URL.
URL_PATTERN = re.compile(
    r"(?i)\b(?:https?://|www\.|mailto:|javascript:|data:)[^\s<>()\[\]{}\"'`]+"
    # Slack links a bare `host.tld/path` too, so a scheme-less URL with a path
    # or query counts. A dotted host with no path ("e.g." or "v1.2") does not.
    r"|\b(?:[a-z0-9-]+\.)+[a-z]{2,}[/?][^\s<>()\[\]{}\"'`]*"
)
_TRAILING = ".,;:!?)]}'\""


def _host(url: str) -> str:
    parts = urlsplit(url if "://" in url else f"http://{url}")
    return (parts.hostname or "").lower()


def _on_allowlist(host: str, allowlist: tuple[str, ...]) -> bool:
    return any(host == h or host.endswith("." + h) for h in allowlist)


def enforce_link_provenance(
    answer: str, sources: list[str] | tuple[str, ...], allowlist: tuple[str, ...]
) -> str:
    """Remove every URL in ``answer`` the ``sources`` text did not contain.

    ``sources`` are the exact texts the model was shown: retrieved chunks,
    attachment text, web results, GitHub evidence. They are compared raw and
    normalized, because the model read the normalized text.
    """
    if not answer:
        return answer
    seen = "\n".join(sources)
    seen_norm = normalize_untrusted(seen)

    def check(match: re.Match[str]) -> str:
        raw = match.group(0)
        url = raw.rstrip(_TRAILING)
        tail = raw[len(url):]
        if url in seen or url in seen_norm:
            return raw
        scheme = url.split(":", 1)[0].lower()
        host = _host(url)
        if scheme in ("http", "https") or url.lower().startswith("www."):
            if _on_allowlist(host, allowlist):
                parts = urlsplit(url if "://" in url else f"https://{url}")
                return f"{parts.scheme}://{parts.netloc}{parts.path}{tail}"
        logger.info("security.link_removed host=%s", host or scheme)
        return REMOVED + tail

    return URL_PATTERN.sub(check, answer)


def strip_links(text: str) -> str:
    """Every URL in ``text`` replaced with ``[link removed]``.

    For model-written text that is STORED and read back as context (the
    running conversation summary): it resolves "what about that one?", which
    never needs a URL, and a stored URL is one a later answer could repeat as
    if a source had carried it.
    """
    return URL_PATTERN.sub(REMOVED, text) if text else text
