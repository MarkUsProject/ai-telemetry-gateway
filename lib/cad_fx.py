"""USD-to-CAD exchange rate sourced from the Bank of Canada's Valet API.

``usage_logs.total_cost`` stores Canadian dollars; LiteLLM emits USD. The
gateway converts at call time and persists the CAD figure, so historical
rows immutably reflect what was actually spent on the day regardless of
later FX moves.

Source: Bank of Canada Valet API series ``FXUSDCAD`` — the same daily noon
rate the U of T finance office uses. No auth, no key. Cached 24 hours in
memory; refreshed lazily on the first call after the TTL expires.

Resilience cascade — every conversion returns *something* so a transient
outage never loses a billable row:

1. ``AITG_USD_TO_CAD_RATE`` env override wins when set (testing / known-bad).
2. Cache hit within TTL.
3. Live BoC fetch.
4. Last-known cached value (stale, logged loud).
5. Hardcoded floor (cold start with no network).

The hook in ``telemetry_adapter`` consumes the returned float; nothing else
should call into BoC directly.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request

_BOC_URL = "https://www.bankofcanada.ca/valet/observations/FXUSDCAD/json?recent=1"
_TTL_SECONDS = 24 * 60 * 60
# Five-year rolling average of FXUSDCAD. Used only on cold start with no network
# and no override. Better than dropping the row; loud log warns the operator.
_COLD_START_FALLBACK = 1.36
_LOG = logging.getLogger("aitg.cad_fx")

_lock = threading.Lock()
_cache = {"rate": None, "fetched_at": 0.0}


def get_cad_rate() -> float:
    """Return the current USD-to-CAD rate. Never raises."""
    override = os.getenv("AITG_USD_TO_CAD_RATE")
    if override:
        try:
            return float(override)
        except ValueError:
            _LOG.error(
                "AITG_USD_TO_CAD_RATE=%r is not numeric; falling through to the BoC cascade.",
                override,
            )

    with _lock:
        now = time.time()
        if _cache["rate"] is not None and now - _cache["fetched_at"] < _TTL_SECONDS:
            return _cache["rate"]

        fresh = _fetch_boc_rate()
        if fresh is not None:
            _cache["rate"] = fresh
            _cache["fetched_at"] = now
            return fresh

        if _cache["rate"] is not None:
            _LOG.warning("BoC fetch failed; reusing last-known rate %s", _cache["rate"])
            return _cache["rate"]

        _LOG.error(
            "BoC fetch failed on cold start; using fallback rate %s. "
            "Set AITG_USD_TO_CAD_RATE to pin a rate while the network is unavailable.",
            _COLD_START_FALLBACK,
        )
        return _COLD_START_FALLBACK


def _reset_cache_for_tests() -> None:
    """Drop cached state. For test isolation; do not call from production code."""
    with _lock:
        _cache["rate"] = None
        _cache["fetched_at"] = 0.0


def _fetch_boc_rate() -> float | None:
    try:
        req = urllib.request.Request(_BOC_URL, headers={"User-Agent": "aitg-gateway/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read())
        return float(payload["observations"][0]["FXUSDCAD"]["v"])
    except Exception:  # noqa: BLE001 - network + parse errors collapse to "no rate"
        _LOG.exception("Bank of Canada Valet fetch failed")
        return None
