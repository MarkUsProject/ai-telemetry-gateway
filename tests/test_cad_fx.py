"""Tests for the USD-to-CAD rate (lib/cad_fx.py).

The Bank of Canada Valet API is mocked at the urllib layer so tests never
touch the network. The cache is reset between tests for isolation.
"""

from __future__ import annotations

import io
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import cad_fx  # noqa: E402


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("AITG_USD_TO_CAD_RATE", raising=False)
    cad_fx._reset_cache_for_tests()


def _fake_response(rate: str = "1.4321") -> io.BytesIO:
    payload = {"observations": [{"d": "2026-05-29", "FXUSDCAD": {"v": rate}}]}
    return io.BytesIO(json.dumps(payload).encode("utf-8"))


class _CtxResp:
    def __init__(self, body: io.BytesIO):
        self._body = body

    def __enter__(self):
        return self._body

    def __exit__(self, *exc):
        return False


def test_env_override_short_circuits(monkeypatch):
    monkeypatch.setenv("AITG_USD_TO_CAD_RATE", "1.50")

    def _explode(*a, **kw):
        raise AssertionError("urlopen should not be called when override is set")

    monkeypatch.setattr(cad_fx.urllib.request, "urlopen", _explode)
    assert cad_fx.get_cad_rate() == 1.50


def test_fetch_caches_for_24_hours(monkeypatch):
    calls = []

    def _fake_urlopen(req, timeout=10):
        calls.append(req.full_url)
        return _CtxResp(_fake_response("1.4321"))

    monkeypatch.setattr(cad_fx.urllib.request, "urlopen", _fake_urlopen)
    first = cad_fx.get_cad_rate()
    second = cad_fx.get_cad_rate()
    assert first == 1.4321 and second == 1.4321
    assert len(calls) == 1  # second call served from cache


def test_refetches_after_ttl(monkeypatch):
    rates = iter(["1.40", "1.41"])

    def _fake_urlopen(req, timeout=10):
        return _CtxResp(_fake_response(next(rates)))

    monkeypatch.setattr(cad_fx.urllib.request, "urlopen", _fake_urlopen)
    assert cad_fx.get_cad_rate() == 1.40
    # Age the cache past TTL.
    cad_fx._cache["fetched_at"] = time.time() - (cad_fx._TTL_SECONDS + 1)
    assert cad_fx.get_cad_rate() == 1.41


def test_fetch_failure_reuses_last_known(monkeypatch):
    state = {"ok": True}

    def _fake_urlopen(req, timeout=10):
        if state["ok"]:
            state["ok"] = False
            return _CtxResp(_fake_response("1.38"))
        raise ConnectionError("boom")

    monkeypatch.setattr(cad_fx.urllib.request, "urlopen", _fake_urlopen)
    assert cad_fx.get_cad_rate() == 1.38
    cad_fx._cache["fetched_at"] = time.time() - (cad_fx._TTL_SECONDS + 1)
    # Next call: cache expired AND fetch raises — must fall back to last-known.
    assert cad_fx.get_cad_rate() == 1.38


def test_cold_start_failure_uses_floor(monkeypatch):
    def _fake_urlopen(req, timeout=10):
        raise ConnectionError("boom")

    monkeypatch.setattr(cad_fx.urllib.request, "urlopen", _fake_urlopen)
    assert cad_fx.get_cad_rate() == cad_fx._COLD_START_FALLBACK


def test_malformed_override_falls_through_to_cascade(monkeypatch):
    """A non-numeric override must not raise — the contract is 'never raises'."""
    monkeypatch.setenv("AITG_USD_TO_CAD_RATE", "not-a-number")

    def _fake_urlopen(req, timeout=10):
        return _CtxResp(_fake_response("1.39"))

    monkeypatch.setattr(cad_fx.urllib.request, "urlopen", _fake_urlopen)
    assert cad_fx.get_cad_rate() == 1.39
