"""Tests for db/queries/*.sql — the three health-check queries the gatekeeper
issues on every chat completion.

Each test uses a savepoint so it does not pollute the seeded local_dev period.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest


QUERIES_DIR = Path("/app/db/queries")


def _load(name: str) -> str:
    return (QUERIES_DIR / name).read_text()


def test_active_period_returns_seeded_local_dev(conn):
    """The seed inserts a local_dev period spanning 2020-2099, so it's always active."""
    row = conn.execute(_load("active_period.sql")).fetchone()
    assert row is not None
    assert row[1] == "local_dev"  # period_code


def test_active_period_returns_empty_when_none_active(conn):
    """The fail-closed trigger: no active period → empty result, not an error.
    The conn fixture rolls back after the test, so flipping is_active here is safe."""
    conn.execute("UPDATE global_budget_periods SET is_active = FALSE")
    rows = conn.execute(_load("active_period.sql")).fetchall()
    assert rows == [], "expected empty result when no period is active"


def test_global_spend_returns_zero_when_slots_are_empty(conn):
    """Validation hook: 10 slots at 0 must SUM to 0 (a sentinel of 1 is a bug).

    The zeroing is explicit: a used dev database carries real slot values, and
    the conn fixture rolls this back after the test.
    """
    period_id = conn.execute(
        "SELECT id FROM global_budget_periods WHERE period_code = 'local_dev'"
    ).fetchone()[0]
    conn.execute("UPDATE budget_slots SET current_value = 0 WHERE period_id = %s", (period_id,))
    total = conn.execute(_load("global_spend.sql"), (period_id,)).fetchone()[0]
    assert total == 0


def test_global_spend_reflects_slot_increments(conn):
    """After a worker bumps a slot, the SUM reflects it.

    Asserted as a delta so the test holds on a dev database carrying spend.
    """
    period_id = conn.execute(
        "SELECT id FROM global_budget_periods WHERE period_code = 'local_dev'"
    ).fetchone()[0]
    before = conn.execute(_load("global_spend.sql"), (period_id,)).fetchone()[0]
    conn.execute(
        "UPDATE budget_slots SET current_value = current_value + 0.12345 "
        "WHERE period_id = %s AND slot_id = 3",
        (period_id,),
    )
    total = conn.execute(_load("global_spend.sql"), (period_id,)).fetchone()[0]
    assert total - before == Decimal("0.12345")


def test_course_spend_sums_usage_logs(conn):
    """course_spend.sql aggregates usage_logs.total_cost in (instance, course_id, period)."""
    # Insert a synthetic api_keys row to satisfy FK on usage_logs.
    api_key_id = conn.execute(
        """
        INSERT INTO api_keys (provider, key_name, encrypted_key)
        VALUES ('OpenAI', 'course-spend-test', 'ciphertext-placeholder')
        RETURNING id
        """
    ).fetchone()[0]

    # Two calls on the same course, one on a different course.
    conn.execute(
        """
        INSERT INTO usage_logs
          (api_key_id, instance, course_id, assignment_id, group_id,
           total_cost, created_at)
        VALUES
          (%s, 'markus.example', 100, 1, 1, 0.50, NOW()),
          (%s, 'markus.example', 100, 1, 2, 0.25, NOW()),
          (%s, 'markus.example', 200, 1, 1, 9.99, NOW())
        """,
        (api_key_id, api_key_id, api_key_id),
    )

    period_starts = "2020-01-01"
    period_ends = "2099-12-31"
    course_100 = conn.execute(
        _load("course_spend.sql"),
        ("markus.example", 100, period_starts, period_ends),
    ).fetchone()[0]
    course_200 = conn.execute(
        _load("course_spend.sql"),
        ("markus.example", 200, period_starts, period_ends),
    ).fetchone()[0]
    course_300 = conn.execute(
        _load("course_spend.sql"),
        ("markus.example", 300, period_starts, period_ends),
    ).fetchone()[0]

    assert course_100 == Decimal("0.75")
    assert course_200 == Decimal("9.99")
    assert course_300 == 0  # no rows; query returns 0 not NULL
