"""Pin that the rate limiter identifies the CALLER, not the proxy in front of it.

`request.client.host` is the immediate peer. Behind this deployment's Vercel ->
Render chain that is a proxy, identical for every user, so a "per-IP" budget was
really one global bucket — an availability hole (one script 429s everyone's
login), not just weaker enumeration protection.
"""

from __future__ import annotations

import pytest

from app.security.client_ip import resolve_client_ip


class _Client:
    def __init__(self, host: str) -> None:
        self.host = host


class _Request:
    """Minimal stand-in: resolve_client_ip only reads .headers and .client."""

    def __init__(self, headers: dict | None = None, host: str | None = "10.0.0.1") -> None:
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.client = _Client(host) if host else None


def test_a_platform_set_header_beats_the_proxy_peer():
    request = _Request({"x-vercel-forwarded-for": "203.0.113.9"}, host="10.0.0.1")
    assert resolve_client_ip(request) == "203.0.113.9"


def test_x_real_ip_is_used_when_vercel_header_is_absent():
    assert resolve_client_ip(_Request({"x-real-ip": "198.51.100.4"})) == "198.51.100.4"


def test_forwarded_for_is_a_last_resort_and_takes_the_leftmost_entry():
    request = _Request({"x-forwarded-for": "203.0.113.9, 70.41.3.18, 150.172.238.178"})
    assert resolve_client_ip(request) == "203.0.113.9"


def test_a_trusted_single_value_header_wins_over_forwarded_for():
    """XFF's leftmost entry is caller-controlled, so it must never take priority.

    Otherwise a client sends its own X-Forwarded-For per request, gets a fresh
    bucket each time, and the limiter is bypassed entirely.
    """
    request = _Request(
        {
            "x-forwarded-for": "1.2.3.4",  # attacker-supplied
            "x-real-ip": "198.51.100.4",  # written by the edge
        }
    )
    assert resolve_client_ip(request) == "198.51.100.4"


def test_client_ip_header_can_be_pinned_explicitly(monkeypatch):
    monkeypatch.setenv("CLIENT_IP_HEADER", "cf-connecting-ip")
    request = _Request(
        {"cf-connecting-ip": "203.0.113.77", "x-real-ip": "198.51.100.4"}
    )
    assert resolve_client_ip(request) == "203.0.113.77"


def test_a_pinned_header_that_is_absent_falls_through_rather_than_failing(monkeypatch):
    monkeypatch.setenv("CLIENT_IP_HEADER", "cf-connecting-ip")
    assert resolve_client_ip(_Request({"x-real-ip": "198.51.100.4"})) == "198.51.100.4"


def test_peer_host_is_the_final_fallback():
    assert resolve_client_ip(_Request({}, host="10.0.0.1")) == "10.0.0.1"


def test_an_unidentifiable_caller_shares_one_bucket_rather_than_bypassing_the_limit():
    """Fail into a shared throttled bucket — the safe direction for a limiter."""
    assert resolve_client_ip(_Request({}, host=None)) == "unknown"


@pytest.mark.parametrize("raw", ["", "   ", ",", " , "])
def test_blank_header_values_do_not_become_an_ip(raw):
    assert resolve_client_ip(_Request({"x-real-ip": raw}, host="10.0.0.1")) == "10.0.0.1"
