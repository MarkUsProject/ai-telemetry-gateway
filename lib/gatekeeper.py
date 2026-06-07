"""Budget enforcement and slotted accounting for the LiteLLM gateway.

This module is the second pre-call hook (after ``attribution_guard``) and the
third logger callback (alongside ``telemetry_adapter``). Every billable call
runs the same six checks in order; the first that fails halts the call with
an operator-actionable error:

1. ``max_tokens`` is present and at or below the configured ceiling.
2. An active ``global_budget_periods`` row covers the current timestamp.
3. The upstream key (``api_keys.is_active``) is on.
4. The active period (``global_budget_periods.is_active``) is on.
5. Global spend (SUM of ten slot rows) is at or below the period's cap.
6. The course budget exists, its kill switch is on, and per-course spend
   (from ``usage_logs``) is at or below the course cap.

On success after upstream completion, the slot-write hook adds the call cost
to a random slot for the active period — the same mechanism that lets 120
workers update spend without serialising on one row.

Per-course spend lives in ``usage_logs`` rather than a per-course slot table.
``db/queries/course_spend.sql`` is the canonical query; this hook embeds the
same SQL inline so it does not depend on a file at runtime.

Fail-closed: any DB error, any unexpected exception → halt the call. This is
non-negotiable and applies even at the cost of unavailability during an outage.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import sys
from datetime import datetime, timezone
from typing import Optional

try:  # litellm only inside the proxy container
    from litellm.integrations.custom_logger import CustomLogger as _Base
except ImportError:  # pragma: no cover

    class _Base:
        pass


sys.path.insert(0, os.path.dirname(__file__))
import attribution_guard  # noqa: E402
import telemetry_adapter  # noqa: E402

_LOG = logging.getLogger("aitg.gatekeeper")

# Per-model ceiling could be a future refinement. We read a single integer; an
# unset value means "no ceiling but missing max_tokens still rejects", which
# preserves the rule that absence cannot bypass enforcement.
_MAX_TOKENS_CEILING_ENV = "AITG_MAX_TOKENS_CEILING"
_DEFAULT_MAX_TOKENS_CEILING = 4096
_SLOT_COUNT = 10
_GUARDED_CALL_TYPES = ("completion", "acompletion", "text_completion")


def _ceiling() -> int:
    raw = os.getenv(_MAX_TOKENS_CEILING_ENV)
    if not raw:
        return _DEFAULT_MAX_TOKENS_CEILING
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{_MAX_TOKENS_CEILING_ENV} must be an integer, got {raw!r}") from exc


def check_max_tokens(data: dict) -> Optional[str]:
    """Return a rejection message when max_tokens is missing or over the cap.

    Pure function so the rule is testable without a proxy. Returning None
    means "allow".
    """
    max_tokens = data.get("max_tokens")
    if max_tokens is None:
        return (
            "max_tokens is required. The gateway enforces a per-call ceiling; "
            "omitting max_tokens would bypass it."
        )
    cap = _ceiling()
    if int(max_tokens) > cap:
        return f"max_tokens={max_tokens} exceeds the gateway ceiling of {cap}."
    return None


def _rejection(detail: str) -> Exception:
    """Return the typed HTTP exception when fastapi is installed, else a plain
    exception with the same message — same convention as attribution_guard."""
    try:
        from fastapi import HTTPException

        return HTTPException(status_code=400, detail=detail)
    except ImportError:  # pragma: no cover
        return RuntimeError(detail)


def _select_random_slot() -> int:
    """Uniform random over [1, 10]. Non-uniform sources reintroduce hot spots."""
    return random.randint(1, _SLOT_COUNT)


def _fetch_active_period(cur) -> Optional[tuple]:
    """Return the active period row, or None if no period covers NOW().

    The is_active filter is part of the lookup so the kill switch at the
    period level halts the call here ("Global Hard Stop").
    """
    cur.execute(
        """
        SELECT id, max_budget, starts_at, ends_at
          FROM aitg.global_budget_periods
         WHERE NOW() BETWEEN starts_at AND ends_at
           AND is_active
         ORDER BY starts_at DESC
         LIMIT 1
        """
    )
    return cur.fetchone()


def _fetch_global_spend(cur, period_id: int) -> float:
    cur.execute(
        "SELECT COALESCE(SUM(current_value), 0) FROM aitg.budget_slots WHERE period_id = %s",
        (period_id,),
    )
    return float(cur.fetchone()[0])


def _fetch_course_budget(cur, instance: str, course_id: int) -> Optional[tuple]:
    cur.execute(
        """
        SELECT max_budget, alert_threshold, is_active, alert_sent_at
          FROM aitg.course_budgets
         WHERE instance = %s AND course_id = %s
        """,
        (instance, course_id),
    )
    return cur.fetchone()


def _fetch_course_spend(cur, instance: str, course_id: int, starts_at, ends_at) -> float:
    cur.execute(
        """
        SELECT COALESCE(SUM(total_cost), 0)
          FROM aitg.usage_logs
         WHERE instance      = %s
           AND course_id     = %s
           AND created_at   >= %s
           AND created_at   <  %s
        """,
        (instance, course_id, starts_at, ends_at),
    )
    return float(cur.fetchone()[0])


def _fetch_api_key_is_active(cur, api_key_id: int) -> bool:
    cur.execute("SELECT is_active FROM aitg.api_keys WHERE id = %s", (api_key_id,))
    row = cur.fetchone()
    return bool(row and row[0])


def _stamp_alert(cur, instance: str, course_id: int) -> bool:
    """Atomically mark the course as alerted. Returns True when this call won
    the race and the alert should fire; False when another worker beat us."""
    cur.execute(
        """
        UPDATE aitg.course_budgets
           SET alert_sent_at = NOW()
         WHERE instance = %s AND course_id = %s
           AND alert_sent_at IS NULL
        """,
        (instance, course_id),
    )
    return cur.rowcount == 1


def _evaluate(data: dict) -> Optional[str]:
    """Run every check synchronously and return a rejection message or None.

    Pre-DB checks (max_tokens, attribution shape) run first as pure functions.
    DB checks run inside one connection in ``_evaluate_against_db``; on any DB
    exception the caller turns it into a 503 halt (fail-closed).
    """
    max_tokens_error = check_max_tokens(data)
    if max_tokens_error:
        return max_tokens_error

    metadata = attribution_guard.extract_attribution(data)
    # Attribution guard ran first; this branch protects against misconfiguration
    # of the callback order, not normal flow.
    missing = attribution_guard.missing_fields(metadata)
    if missing:
        return attribution_guard.rejection_message(missing)

    instance = metadata["instance"]
    course_id = int(metadata["course_id"])

    with telemetry_adapter._open_db() as conn:
        return _evaluate_against_db(conn, instance, course_id)


def _evaluate_against_db(conn, instance: str, course_id: int) -> Optional[str]:
    """Run every database-backed budget check against ``conn``. Returns a
    rejection message on the first failure or None when the call may proceed.
    Side effect: fires the alert when the course just crossed its threshold."""
    api_key_id = telemetry_adapter.resolve_api_key_id(conn)
    with conn.cursor() as cur:
        if not _fetch_api_key_is_active(cur, api_key_id):
            return "Upstream API key is disabled (api_keys.is_active = false)."

        period = _fetch_active_period(cur)
        if period is None:
            return "No active budget period covers the current time. Halting per fail-closed policy."

        period_id, period_cap, period_starts, period_ends = period
        period_cap = float(period_cap)

        global_spend = _fetch_global_spend(cur, period_id)
        if global_spend >= period_cap:
            return (
                f"Global term budget exhausted: spent CAD {global_spend:.2f} of "
                f"CAD {period_cap:.2f} for the active period."
            )

        course = _fetch_course_budget(cur, instance, course_id)
        if course is None:
            return f"No course budget configured for ({instance!r}, course_id={course_id})."

        course_cap, alert_threshold, course_active, alert_sent_at = course
        course_cap = float(course_cap)
        if not course_active:
            return f"Course AI features are disabled for course_id={course_id} (is_active = false)."

        course_spend = _fetch_course_spend(cur, instance, course_id, period_starts, period_ends)
        if course_spend >= course_cap:
            return (
                f"Course budget exhausted for course_id={course_id}: "
                f"spent CAD {course_spend:.2f} of CAD {course_cap:.2f}."
            )

        _maybe_fire_alert(conn, cur, instance, course_id, course_spend, alert_threshold, alert_sent_at)

    return None


def _maybe_fire_alert(
    conn,
    cur,
    instance: str,
    course_id: int,
    course_spend: float,
    alert_threshold,
    alert_sent_at,
) -> None:
    """Stamp ``alert_sent_at`` and emit a warning when this call crosses the
    threshold for the first time. No-op on every other call."""
    if alert_threshold is None or alert_sent_at is not None:
        return
    threshold = float(alert_threshold)
    if course_spend < threshold:
        return
    if not _stamp_alert(cur, instance, course_id):
        return
    conn.commit()
    _LOG.warning(
        "ALERT THRESHOLD CROSSED instance=%s course_id=%d spend=%.2f threshold=%.2f. "
        "Wire AITG_ALERT_WEBHOOK_URL or MarkUs NotificationMailer to deliver the email.",
        instance, course_id, course_spend, threshold,
    )


def _write_slot(cur, period_id: int, cost: float) -> None:
    """Add ``cost`` (CAD) to a random slot for the active period."""
    cur.execute(
        """
        UPDATE aitg.budget_slots
           SET current_value = current_value + %s,
               updated_at = NOW()
         WHERE period_id = %s AND slot_id = %s
        """,
        (cost, period_id, _select_random_slot()),
    )


def _slot_write_for_event(kwargs: dict, response_obj) -> None:
    """Compute CAD cost from the same fields telemetry_adapter uses and
    increment one slot. Skips the row when there is nothing to bill (zero
    upstream cost or no active period)."""
    try:
        row = telemetry_adapter.build_row(kwargs, response_obj)
    except ValueError:
        _LOG.exception("Skipping slot write: required attribution missing")
        return
    cost = float(row["total_cost"])
    if cost <= 0:
        return
    with telemetry_adapter._open_db() as conn, conn.cursor() as cur:
        period = _fetch_active_period(cur)
        if period is None:
            _LOG.warning("No active period at slot-write time; skipping increment")
            return
        _write_slot(cur, period[0], cost)
        conn.commit()


class Gatekeeper(_Base):
    """Pre-call enforcement and post-call slot accounting."""

    async def async_pre_call_hook(self, user_api_key_dict, cache, data: dict, call_type: str):
        if call_type not in _GUARDED_CALL_TYPES:
            return data
        try:
            rejection = await asyncio.to_thread(_evaluate, data)
        except Exception as exc:  # noqa: BLE001 - fail-closed on anything unexpected
            _LOG.exception("Gatekeeper evaluation crashed; failing closed")
            raise _rejection(
                f"Gateway temporarily unavailable: {exc.__class__.__name__}. "
                "Halting per fail-closed policy."
            ) from exc
        if rejection:
            raise _rejection(rejection)
        return data

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        # Slot writes are best-effort accounting; a failure here must not crash
        # LiteLLM (the row already exists in usage_logs via telemetry_adapter).
        try:
            await asyncio.to_thread(_slot_write_for_event, kwargs, response_obj)
        except Exception:  # noqa: BLE001
            _LOG.exception("Slot write failed; usage_logs row already landed via telemetry_adapter")

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        # No billing → no slot write. The partial-success rule covers this.
        return


gatekeeper_instance = Gatekeeper()
