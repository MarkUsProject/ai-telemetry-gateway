"""Tests for the gatekeeper.

Two layers:
1. Pure ``check_max_tokens`` and ``_select_random_slot`` — no DB.
2. Live integration against the running aitg-postgres, exercising the four
   enforcement paths (max_tokens missing, course cap, global cap, kill
   switches) plus the slot-write distribution.

Live tests skip cleanly when ``AITG_TEST_DATABASE_URL`` is unset.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections import Counter

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import attribution_guard  # noqa: E402 - imported so the package side-effect runs
import gatekeeper  # noqa: E402
import telemetry_adapter  # noqa: E402 - source of the api_keys row name below

# The api_keys row the gatekeeper resolves on every call. Sourced from the
# adapter so the fixture cannot drift from what the code under test looks up.
API_KEY_NAME = telemetry_adapter._API_KEY_NAME

# The live tests own this instance, so the fixture can delete its usage_logs
# rows safely. The seeded markus.example.edu course is shared with other files
# and accumulates rows from manual runs — deleting by that key destroys history.
TEST_INSTANCE = "test.gatekeeper.local"
TEST_COURSE_ID = 9012

FULL_METADATA = {
    "instance": TEST_INSTANCE,
    "course_id": TEST_COURSE_ID,
    "assignment_id": 34,
    "group_id": 56,
    "batch_id": None,
    "category": "student",
}

GOOD_DATA = {
    "max_tokens": 100,
    "litellm_metadata": {"spend_logs_metadata": FULL_METADATA},
}


# ---------- pure: max_tokens ----------


def test_max_tokens_required():
    msg = gatekeeper.check_max_tokens({"litellm_metadata": {}})
    assert msg is not None and "required" in msg


def test_max_tokens_at_ceiling_passes(monkeypatch):
    monkeypatch.setenv("AITG_MAX_TOKENS_CEILING", "1000")
    assert gatekeeper.check_max_tokens({"max_tokens": 1000}) is None


def test_max_tokens_over_ceiling_rejected(monkeypatch):
    monkeypatch.setenv("AITG_MAX_TOKENS_CEILING", "1000")
    msg = gatekeeper.check_max_tokens({"max_tokens": 1001})
    assert msg is not None and "1000" in msg and "1001" in msg


def test_slot_selection_is_uniform_enough():
    # 10 000 draws across 10 slots: every slot should land in [800, 1200].
    counts = Counter(gatekeeper._select_random_slot() for _ in range(10_000))
    assert set(counts) == set(range(1, 11))
    for slot, n in counts.items():
        assert 800 <= n <= 1200, f"slot {slot} had {n} hits — distribution looks skewed"


# ---------- live integration ----------


@pytest.fixture
def live_db_url():
    url = os.environ.get("AITG_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set AITG_TEST_DATABASE_URL to enable live gatekeeper tests")
    return url


@pytest.fixture
def live_env(monkeypatch, live_db_url):
    monkeypatch.setenv("DATABASE_URL", live_db_url)
    # Pin the FX rate so cost math in slot writes is deterministic.
    monkeypatch.setenv("AITG_USD_TO_CAD_RATE", "1.40")
    monkeypatch.setenv("AITG_MAX_TOKENS_CEILING", "4096")
    return live_db_url


@pytest.fixture
def fresh_course(live_env):
    """Give each test a clean budget and zeroed counter, then restore everything.

    The gatekeeper reads global state, so the tests must control it. Zeroing it
    permanently would destroy the dev ledger, so every mutation is snapshotted.
    """
    import psycopg

    with psycopg.connect(live_env) as conn, conn.cursor() as cur:
        # Selected in restore-parameter order so teardown can hand each row
        # straight to executemany.
        cur.execute("SELECT current_value, period_id, slot_id FROM aitg.budget_slots")
        slots_before = cur.fetchall()
        cur.execute("SELECT is_active, id FROM aitg.global_budget_periods")
        periods_before = cur.fetchall()
        cur.execute("SELECT is_active, id FROM aitg.api_keys WHERE key_name = %s", (API_KEY_NAME,))
        keys_before = cur.fetchall()

        cur.execute(
            """
            INSERT INTO aitg.course_budgets
                        (instance, course_id, max_budget, alert_threshold, is_active, alert_sent_at)
                 VALUES (%s, %s, 100.00, 80.00, TRUE, NULL)
            ON CONFLICT (instance, course_id) DO UPDATE
                    SET max_budget = 100.00, alert_threshold = 80.00,
                        is_active = TRUE, alert_sent_at = NULL
            """,
            (TEST_INSTANCE, TEST_COURSE_ID),
        )
        cur.execute("UPDATE aitg.budget_slots SET current_value = 0")
        cur.execute("UPDATE aitg.api_keys SET is_active = TRUE WHERE key_name = %s", (API_KEY_NAME,))
        cur.execute("UPDATE aitg.global_budget_periods SET is_active = TRUE")
        conn.commit()

    yield

    with psycopg.connect(live_env) as conn, conn.cursor() as cur:
        cur.executemany(
            "UPDATE aitg.budget_slots SET current_value = %s WHERE period_id = %s AND slot_id = %s",
            slots_before,
        )
        cur.executemany(
            "UPDATE aitg.global_budget_periods SET is_active = %s WHERE id = %s",
            periods_before,
        )
        cur.executemany(
            "UPDATE aitg.api_keys SET is_active = %s WHERE id = %s",
            keys_before,
        )
        cur.execute(
            "DELETE FROM aitg.usage_logs WHERE instance = %s AND course_id = %s",
            (TEST_INSTANCE, TEST_COURSE_ID),
        )
        cur.execute(
            "DELETE FROM aitg.course_budgets WHERE instance = %s AND course_id = %s",
            (TEST_INSTANCE, TEST_COURSE_ID),
        )
        conn.commit()


def _call_hook(data):
    """Drive async_pre_call_hook and return either None (allow) or the rejection
    message string. Any other exception bubbles."""
    try:
        asyncio.run(
            gatekeeper.gatekeeper_instance.async_pre_call_hook(
                user_api_key_dict=None, cache=None, data=data, call_type="completion"
            )
        )
        return None
    except Exception as exc:
        return str(getattr(exc, "detail", exc))


def test_happy_path_passes(fresh_course):
    assert _call_hook(dict(GOOD_DATA)) is None


def test_max_tokens_missing_rejected_live(fresh_course):
    rejection = _call_hook({"litellm_metadata": {"spend_logs_metadata": FULL_METADATA}})
    assert rejection and "max_tokens" in rejection and "required" in rejection


def test_course_cap_exhausted_rejected(fresh_course, live_env):
    import psycopg

    # Drop the course cap below current spend (zero) and seed one usage_logs
    # row that pushes per-course spend past it.
    with psycopg.connect(live_env) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE aitg.course_budgets SET max_budget = 1.00 "
            "WHERE instance = %s AND course_id = %s",
            (TEST_INSTANCE, TEST_COURSE_ID),
        )
        cur.execute(
            """
            INSERT INTO aitg.usage_logs (provider_request_id, api_key_id, instance,
                course_id, assignment_id, group_id, input_tokens, output_tokens,
                unit_price, total_cost)
              VALUES (%s,
                (SELECT id FROM aitg.api_keys WHERE key_name = %s),
                %s, %s, 34, 56, 100, 50, 0.01, 1.50)
            """,
            (f"cap_test_{uuid.uuid4().hex}", API_KEY_NAME, TEST_INSTANCE, TEST_COURSE_ID),
        )
        conn.commit()

    rejection = _call_hook(dict(GOOD_DATA))
    assert rejection and "Course budget exhausted" in rejection


def test_global_term_cap_exhausted_rejected(fresh_course, live_env):
    import psycopg

    with psycopg.connect(live_env) as conn, conn.cursor() as cur:
        # Force one slot past the period cap.
        cur.execute("SELECT id, max_budget FROM aitg.global_budget_periods WHERE is_active")
        period_id, cap = cur.fetchone()
        cur.execute(
            "UPDATE aitg.budget_slots SET current_value = %s "
            "WHERE period_id = %s AND slot_id = 1",
            (float(cap) + 10, period_id),
        )
        conn.commit()

    rejection = _call_hook(dict(GOOD_DATA))
    assert rejection and "Global term budget exhausted" in rejection


def test_course_kill_switch_rejected(fresh_course, live_env):
    import psycopg

    with psycopg.connect(live_env) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE aitg.course_budgets SET is_active = FALSE "
            "WHERE instance = %s AND course_id = %s",
            (TEST_INSTANCE, TEST_COURSE_ID),
        )
        conn.commit()

    rejection = _call_hook(dict(GOOD_DATA))
    assert rejection and "Course AI features are disabled" in rejection


def test_global_period_kill_switch_rejected(fresh_course, live_env):
    import psycopg

    with psycopg.connect(live_env) as conn, conn.cursor() as cur:
        cur.execute("UPDATE aitg.global_budget_periods SET is_active = FALSE")
        conn.commit()

    rejection = _call_hook(dict(GOOD_DATA))
    assert rejection and "No active budget period" in rejection


def test_api_key_kill_switch_rejected(fresh_course, live_env):
    import psycopg

    with psycopg.connect(live_env) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE aitg.api_keys SET is_active = FALSE WHERE key_name = %s", (API_KEY_NAME,)
        )
        conn.commit()

    rejection = _call_hook(dict(GOOD_DATA))
    assert rejection and "Upstream API key is disabled" in rejection


def test_slot_write_increments_a_slot(fresh_course, live_env):
    import psycopg
    import types

    metadata = FULL_METADATA
    kwargs = {"litellm_params": {"metadata": {"spend_logs_metadata": metadata}}, "response_cost": 0.02}
    usage = types.SimpleNamespace(prompt_tokens=10, completion_tokens=5)
    response = types.SimpleNamespace(id=f"slot_{uuid.uuid4().hex}", usage=usage)

    with psycopg.connect(live_env) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM aitg.global_budget_periods WHERE is_active")
        period_id = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(current_value), 0) FROM aitg.budget_slots WHERE period_id = %s", (period_id,))
        before = float(cur.fetchone()[0])

    asyncio.run(
        gatekeeper.gatekeeper_instance.async_log_success_event(
            kwargs=kwargs, response_obj=response, start_time=None, end_time=None
        )
    )

    with psycopg.connect(live_env) as conn, conn.cursor() as cur:
        cur.execute("SELECT COALESCE(SUM(current_value), 0) FROM aitg.budget_slots WHERE period_id = %s", (period_id,))
        after = float(cur.fetchone()[0])

    # 0.02 USD × 1.40 CAD = 0.028 CAD, persisted to NUMERIC(12,5).
    assert after - before == pytest.approx(0.028, abs=1e-4)


def test_alert_threshold_fires_once_and_stamps_db(fresh_course, live_env):
    import psycopg

    with psycopg.connect(live_env) as conn, conn.cursor() as cur:
        # Threshold = 1.00; insert a usage_logs row that pushes spend past it
        # but stays under the cap (set high so we test alert, not budget).
        cur.execute(
            "UPDATE aitg.course_budgets SET max_budget = 100.00, alert_threshold = 1.00 "
            "WHERE instance = %s AND course_id = %s",
            (TEST_INSTANCE, TEST_COURSE_ID),
        )
        cur.execute(
            """
            INSERT INTO aitg.usage_logs (provider_request_id, api_key_id, instance,
                course_id, assignment_id, group_id, input_tokens, output_tokens,
                unit_price, total_cost)
              VALUES (%s,
                (SELECT id FROM aitg.api_keys WHERE key_name = %s),
                %s, %s, 34, 56, 100, 50, 0.02, 2.50)
            """,
            (f"alert_{uuid.uuid4().hex}", API_KEY_NAME, TEST_INSTANCE, TEST_COURSE_ID),
        )
        conn.commit()

    # Call once: alert fires, alert_sent_at is stamped, request still passes.
    assert _call_hook(dict(GOOD_DATA)) is None
    with psycopg.connect(live_env) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT alert_sent_at FROM aitg.course_budgets "
            "WHERE instance = %s AND course_id = %s",
            (TEST_INSTANCE, TEST_COURSE_ID),
        )
        first_stamp = cur.fetchone()[0]
        assert first_stamp is not None

    # Call again: alert does not re-fire; stamp does not change.
    assert _call_hook(dict(GOOD_DATA)) is None
    with psycopg.connect(live_env) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT alert_sent_at FROM aitg.course_budgets "
            "WHERE instance = %s AND course_id = %s",
            (TEST_INSTANCE, TEST_COURSE_ID),
        )
        second_stamp = cur.fetchone()[0]
    assert first_stamp == second_stamp
