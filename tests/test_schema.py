"""Schema tests: tables exist, columns match expectations, constraints reject bad input.

Verifies the migrations in db/migrations/ produced the exact shape we expect.
"""

from __future__ import annotations

import psycopg
import pytest


EXPECTED_TABLES = {
    "api_keys",
    "global_budget_periods",
    "course_budgets",
    "budget_slots",
    "usage_logs",
}


def test_all_expected_tables_exist_in_aitg_schema(conn):
    rows = conn.execute(
        """
        SELECT table_name
          FROM information_schema.tables
         WHERE table_schema = 'aitg'
        """
    ).fetchall()
    found = {r[0] for r in rows}
    missing = EXPECTED_TABLES - found
    assert not missing, f"missing tables in aitg schema: {missing}"


def test_no_aitg_tables_leaked_into_public_schema(conn):
    """LiteLLM owns `public`. Our tables must not collide there."""
    rows = conn.execute(
        """
        SELECT table_name
          FROM information_schema.tables
         WHERE table_schema = 'public'
           AND table_name = ANY(%s)
        """,
        (list(EXPECTED_TABLES),),
    ).fetchall()
    assert rows == [], f"aitg tables leaked into public schema: {rows}"


def test_usage_logs_columns_match_expected(conn):
    """usage_logs has 16 columns. Renamed column is `requester_role`
    (intentional deviation from the original `category` name)."""
    rows = conn.execute(
        """
        SELECT column_name, data_type, is_nullable
          FROM information_schema.columns
         WHERE table_schema = 'aitg' AND table_name = 'usage_logs'
         ORDER BY ordinal_position
        """
    ).fetchall()
    names = [r[0] for r in rows]
    expected = [
        "id", "provider_request_id", "api_key_id",
        "instance", "course_id", "assignment_id", "group_id", "batch_id",
        "requester_role",
        "input_tokens", "cached_tokens", "output_tokens", "reasoning_tokens",
        "unit_price", "total_cost", "created_at",
    ]
    assert names == expected, f"usage_logs columns mismatch:\n  got: {names}\n  expected: {expected}"


def _primary_key_columns(conn, table: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT kcu.column_name
          FROM information_schema.table_constraints tc
          JOIN information_schema.key_column_usage kcu
            ON  kcu.constraint_name   = tc.constraint_name
            AND kcu.table_schema      = tc.table_schema
         WHERE tc.table_schema    = 'aitg'
           AND tc.table_name      = %s
           AND tc.constraint_type = 'PRIMARY KEY'
         ORDER BY kcu.ordinal_position
        """,
        (table,),
    ).fetchall()
    return [r[0] for r in rows]


def test_course_budgets_composite_primary_key(conn):
    """Ambiguity resolution: PK is (instance, course_id), not course_id alone."""
    assert _primary_key_columns(conn, "course_budgets") == ["instance", "course_id"]


def test_budget_slots_composite_primary_key(conn):
    """Ambiguity resolution: PK is (period_id, slot_id), not slot_id alone."""
    assert _primary_key_columns(conn, "budget_slots") == ["period_id", "slot_id"]


def test_slot_id_range_constraint_rejects_out_of_range(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO budget_slots (period_id, slot_id, current_value) VALUES (1, 11, 0)"
        )


def test_slot_id_range_constraint_rejects_zero(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO budget_slots (period_id, slot_id, current_value) VALUES (1, 0, 0)"
        )


def test_chronological_constraint_rejects_starts_after_ends(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            """
            INSERT INTO global_budget_periods
                (period_code, display_name, max_budget, starts_at, ends_at)
            VALUES ('bad', 'Bad', 100, '2026-01-01', '2025-01-01')
            """
        )


def test_provider_request_id_unique(conn):
    # FK constraint requires an api_keys row first.
    api_key_id = conn.execute(
        """
        INSERT INTO api_keys (provider, key_name, encrypted_key)
        VALUES ('OpenAI', 'dedup-test', 'ciphertext')
        RETURNING id
        """
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO usage_logs
            (provider_request_id, api_key_id, instance, course_id,
             assignment_id, group_id, total_cost)
        VALUES ('req-dedup-test', %s, 'a.example', 1, 1, 1, 0.001)
        """,
        (api_key_id,),
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            """
            INSERT INTO usage_logs
                (provider_request_id, api_key_id, instance, course_id,
                 assignment_id, group_id, total_cost)
            VALUES ('req-dedup-test', %s, 'a.example', 1, 1, 1, 0.001)
            """,
            (api_key_id,),
        )


def test_course_budgets_multi_instance_allowed(conn):
    """Two instances may have the same course_id with independent budgets."""
    conn.execute(
        "INSERT INTO course_budgets (instance, course_id, max_budget) VALUES ('a.example', 99, 50)"
    )
    conn.execute(
        "INSERT INTO course_budgets (instance, course_id, max_budget) VALUES ('b.example', 99, 75)"
    )
    rows = conn.execute(
        "SELECT instance, max_budget FROM course_budgets WHERE course_id = 99 ORDER BY instance"
    ).fetchall()
    assert rows == [("a.example", 50), ("b.example", 75)]
