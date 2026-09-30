"""The live-tools gateway: the ONLY code that decrypts a token for a live read.

Mode A (plan D5): the server -- never the model -- picks what to refresh, from
the hits retrieval already returned for this asker, and resolves each one to
``(provider, external_id)`` from its own ``documents`` row, pinned to the same
org and space. So a live read can never reach an object the index did not
already clear (plan D3), and no target is ever named by a model.

Every failure degrades to the indexed answer except one: the provider saying
the object is gone or no longer readable, which withholds the stale copy too
(plan D16, §5). Nothing here raises into the answer path.
"""

from __future__ import annotations

import contextvars
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timezone
from typing import Callable

from ..config.settings import GuardSettings, LiveToolsSettings
from ..db.connection import get_connection
from . import audit, base, drive, linear, notion, slack, trigger
from .base import LiveRead, LiveRefresh, ProviderRead
from .context import LiveRequest

logger = logging.getLogger("livetools")

#: provider -> reader(token, external_id). A provider absent here is never read
#: live, whatever LIVE_TOOLS_PROVIDERS says.
READERS: dict[str, Callable[[str, str], ProviderRead]] = {
    "linear": linear.read_issue,
    "google": drive.read_file,
    "notion": notion.read_page,
    "slack": slack.read_thread,  # listed, but off until the D10 tier check
}

_LABELS = {"linear": "Linear", "google": "Google Drive", "notion": "Notion", "slack": "Slack"}

_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="livetools")


def refresh(
    hits: list,
    request: LiveRequest | None,
    *,
    settings: LiveToolsSettings | None = None,
    guard_settings: GuardSettings | None = None,
    mode: str = "refresh",
) -> LiveRefresh:
    """Re-read the best refreshable hits live. Never raises.

    ``mode`` is the audit's: ``refresh`` (mode A, the server picked) or
    ``model`` (mode B, the model named one of this request's handles).
    """
    if request is None or not hits:
        return LiveRefresh()
    try:
        settings = settings or LiveToolsSettings.from_env()
        if not settings.allows(request.org_id) or not request.user_id:
            # No identity => no live read at all (plan §5): a live read is a
            # read on someone's behalf, and the audit must say whose.
            return LiveRefresh()
        if mode == "refresh" and not trigger.wants_live(request.needs_live):
            # Nothing about this question moves day to day: the synced copy is
            # the answer, and a live read would only cost time.
            logger.info("livetools.skip reason=not_current_state verdict=%s", request.needs_live)
            return LiveRefresh()
        targets = _targets(hits, request, settings)
        if mode == "refresh":
            targets = _drop_freshly_synced(targets, request)
        if not targets:
            return LiveRefresh()
        return LiveRefresh(reads=_read_all(targets, request, guard_settings, mode))
    except Exception:  # noqa: BLE001 - a live read may only ever add
        logger.warning("live refresh skipped", exc_info=True)
        return LiveRefresh()


def _targets(hits: list, request: LiveRequest, settings: LiveToolsSettings) -> list[tuple[str, str, str]]:
    """``[(document_id, provider, external_id)]``, at most MAX_REFRESHES."""
    return [c[:3] for c in candidates(hits, request, settings, limit=base.MAX_REFRESHES)]


def candidates(
    hits: list, request: LiveRequest, settings: LiveToolsSettings | None = None,
    *, limit: int = base.CANDIDATE_DOCUMENTS,
) -> list[tuple[str, str, str, str]]:
    """``[(document_id, provider, external_id)]`` for the best refreshable hits.

    Looked up from ``documents`` by id, pinned to the request's org AND space:
    the hit says WHICH document, the row says where it lives. Reused hits
    (``_try_reuse``) carry no provider, so the row is also the only place that
    can say whether a hit is refreshable at all.
    """
    order: list[str] = []
    for hit in hits:
        doc = getattr(hit, "document_id", None)
        if doc and doc not in order:
            order.append(doc)
        if len(order) >= base.CANDIDATE_DOCUMENTS:
            break
    if not order:
        return []
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id::text, source_provider, source_external_id, coalesce(title, '')
            FROM documents
            WHERE org_id = %s::uuid
              AND workspace_id IS NOT DISTINCT FROM %s::uuid
              AND id = ANY(%s::uuid[])
            """,
            (request.org_id, request.workspace_id, order),
        ).fetchall()
    settings = settings or LiveToolsSettings.from_env()
    found = {r[0]: (r[1], r[2], r[3]) for r in rows}
    out = []
    for doc in order:
        provider, external_id, title = found.get(doc, (None, None, ""))
        if provider in READERS and provider in settings.providers and external_id:
            out.append((doc, provider, external_id, title))
        if len(out) >= limit:
            break
    return out


def _drop_freshly_synced(targets, request: LiveRequest) -> list:
    """Drop items whose tool last synced SUCCESSFULLY within ``FRESH_SECONDS``.

    A success time, not ``oauth_connections.last_sync_at``: that is stamped on
    ATTEMPT, so a failing sync would read as fresh and suppress exactly the
    live read that would have covered for it. Per provider, same org AND
    space. A lookup failure keeps the targets -- reading live is the safe
    direction when freshness is unknown.
    """
    providers = sorted({provider for _, provider, _ in targets})
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT c.provider,
                       max(j.finished_at) > now() - make_interval(secs => %s)
                FROM oauth_connections c
                JOIN ingestion_jobs j ON j.connection_id = c.id
                WHERE c.org_id = %s::uuid
                  AND c.workspace_id IS NOT DISTINCT FROM %s::uuid
                  AND c.provider = ANY(%s)
                  AND j.status = 'succeeded'
                GROUP BY c.provider
                """,
                (base.FRESH_SECONDS, request.org_id, request.workspace_id, providers),
            ).fetchall()
    except Exception:  # noqa: BLE001 - unknown freshness reads live
        logger.warning("livetools: freshness lookup failed", exc_info=True)
        return targets
    fresh = {provider for provider, is_fresh in rows if is_fresh}
    if fresh:
        logger.info("livetools.skip reason=fresh providers=%s", ",".join(sorted(fresh)))
    return [t for t in targets if t[1] not in fresh]


def _read_all(targets, request: LiveRequest, guard_settings: GuardSettings | None,
              mode: str = "refresh") -> list[LiveRead]:
    tokens: dict[str, tuple[str | None, str | None]] = {}
    for _, provider, _ in targets:
        if provider not in tokens:
            tokens[provider] = _token(request, provider)

    started = time.perf_counter()
    futures = []
    for doc, provider, external_id in targets:
        token, failure = tokens[provider]
        if token is None:
            futures.append((doc, provider, external_id, None, failure))
            continue
        future = _POOL.submit(
            contextvars.copy_context().run, READERS[provider], token, external_id
        )
        futures.append((doc, provider, external_id, future, None))

    reads: list[LiveRead] = []
    for doc, provider, external_id, future, failure in futures:
        if future is None:
            result = ProviderRead(failure or base.NOT_CONNECTED)
        else:
            remaining = max(0.1, base.TIMEOUT_SECONDS - (time.perf_counter() - started))
            try:
                result = future.result(timeout=remaining)
            except FutureTimeout:
                result = ProviderRead(base.TIMEOUT)
            except Exception as exc:  # noqa: BLE001
                result = ProviderRead(base.ERROR, reason=type(exc).__name__)
        if result.outcome == base.REAUTH:
            _mark_reauth(request, provider)
        if result.reason and result.outcome != base.OK:
            logger.info("livetools.reason provider=%s outcome=%s reason=%s",
                        provider, result.outcome, result.reason)
        reads.append(_to_live_read(doc, provider, external_id, result))

    reads = _screen(reads, request, guard_settings)
    latency = round((time.perf_counter() - started) * 1000)
    for read in reads:
        audit.record(request, read, mode=mode, latency_ms=latency)
    return reads


def _token(request: LiveRequest, provider: str) -> tuple[str | None, str | None]:
    """``(token, None)`` or ``(None, outcome)``. Same org AND space, no fallback."""
    from ..auth.credentials import get_live_connection_token
    from ..core.exceptions import ConfigurationError, OAuthReauthRequiredError

    try:
        return get_live_connection_token(request.org_id, provider, request.workspace_id), None
    except OAuthReauthRequiredError:
        return None, base.REAUTH
    except ConfigurationError:
        return None, base.NOT_CONNECTED
    except Exception:  # noqa: BLE001
        logger.warning("live token lookup failed provider=%s", provider, exc_info=True)
        return None, base.ERROR


def _mark_reauth(request: LiveRequest, provider: str) -> None:
    try:
        from ..auth.credentials import mark_needs_reauth

        mark_needs_reauth(request.org_id, provider, request.workspace_id,
                          f"{_LABELS.get(provider, provider)} rejected the stored token (401).")
    except Exception:  # noqa: BLE001
        logger.warning("could not mark %s needs_reauth", provider, exc_info=True)


def _to_live_read(doc: str, provider: str, external_id: str, result: ProviderRead) -> LiveRead:
    if result.outcome != base.OK or not result.text.strip():
        outcome = result.outcome if result.outcome != base.OK else base.ERROR
        return LiveRead(provider, doc, external_id, outcome)
    fetched = datetime.now(timezone.utc)
    body = result.text.strip()
    truncated = len(body) > base.MAX_CHARS
    if truncated:
        body = body[: base.MAX_CHARS - 1].rstrip() + "…\n(Live read truncated — only the first part is shown.)"
    header = (
        f"Live from {_LABELS.get(provider, provider)}, fetched "
        f"{fetched.strftime('%d %b %Y %H:%M UTC')} (current: supersedes any older "
        f"indexed copy of the same item)"
    )
    return LiveRead(provider, doc, external_id, base.OK, text=f"{header}\n{body}",
                    fetched_at=fetched, truncated=truncated)


def _screen(reads: list[LiveRead], request: LiveRequest,
            guard_settings: GuardSettings | None) -> list[LiveRead]:
    """Injection guard over the live blocks, one batched call (plan D9).

    Enforce drops a flagged block -- the indexed copy still answers, since a
    planted comment is not evidence the issue is gone. Shadow only logs. A
    guard failure keeps the block: fence, scrub and link provenance still apply.
    """
    live = [r for r in reads if r.outcome == base.OK]
    if not live:
        return reads
    settings = guard_settings or GuardSettings.from_env()
    if not settings.enabled:
        return reads
    try:
        from ..guard import build_injection_guard

        guard = build_injection_guard(settings)
        if guard is None:
            return reads
        scores = guard.score([r.text for r in live])
    except Exception:  # noqa: BLE001 - fail open to the other layers
        return reads
    flagged = {
        id(r) for r, s in zip(live, scores)
        if s is not None and s >= settings.threshold
    }
    out = []
    for r in reads:
        if id(r) in flagged:
            logger.warning("guard.flagged_live mode=%s org=%s provider=%s",
                           settings.mode, request.org_id, r.provider)
            if settings.mode == "enforce":
                r = LiveRead(r.provider, r.document_id, r.external_id, base.GUARD_FLAGGED)
        out.append(r)
    return out
