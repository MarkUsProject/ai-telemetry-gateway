"""Tests for the four feature-flag queries in db/queries/.

An admin runs these by hand with ``psql -f``. The course queries take psql
variables (``:'instance'``, ``:course_id``); psycopg has no such syntax, so
the tests swap each variable for a bound parameter and run the same SQL.

Every test rolls back, so the dev database keeps its switches.
"""

from __future__ import annotations

from pathlib import Path

QUERIES_DIR = Path(__file__).resolve().parent.parent / "db" / "queries"

TEST_INSTANCE = "test.flagqueries.local"
TEST_COURSE_ID = 9013


def _load(name: str) -> str:
    return (QUERIES_DIR / name).read_text()


def _load_course_query(name: str) -> str:
    sql = _load(name)
    assert ":'instance'" in sql and ":course_id" in sql, f"{name} lost a psql variable"
    return sql.replace(":'instance'", "%(instance)s").replace(":course_id", "%(course_id)s")


def _flag(conn):
    row = conn.execute("SELECT ai_enabled FROM global_settings").fetchone()
    return None if row is None else row[0]


def _add_course(conn, is_active: bool) -> None:
    conn.execute(
        "INSERT INTO course_budgets (instance, course_id, max_budget, is_active) "
        "VALUES (%s, %s, 50.00, %s)",
        (TEST_INSTANCE, TEST_COURSE_ID, is_active),
    )


def _course_is_active(conn) -> bool:
    return conn.execute(
        "SELECT is_active FROM course_budgets WHERE instance = %s AND course_id = %s",
        (TEST_INSTANCE, TEST_COURSE_ID),
    ).fetchone()[0]


COURSE_PARAMS = {"instance": TEST_INSTANCE, "course_id": TEST_COURSE_ID}


# ---------- global ----------


def test_global_queries_flip_the_flag(conn):
    assert conn.execute(_load("disable_global.sql")).fetchone()[0] is False
    assert _flag(conn) is False
    assert conn.execute(_load("enable_global.sql")).fetchone()[0] is True
    assert _flag(conn) is True
    assert conn.execute("SELECT COUNT(*) FROM global_settings").fetchone()[0] == 1


def test_global_queries_create_the_row_when_it_is_missing(conn):
    """A database that was migrated but never seeded has no row. The queries
    must still work there; an UPDATE alone would change nothing."""
    conn.execute("DELETE FROM global_settings")
    conn.execute(_load("enable_global.sql"))
    assert _flag(conn) is True

    conn.execute("DELETE FROM global_settings")
    conn.execute(_load("disable_global.sql"))
    assert _flag(conn) is False


def test_disable_global_leaves_term_and_course_switches_alone(conn):
    """The flag is its own switch. Turning it off must not rewrite the term
    rows or the course rows, or turning it back on could not restore them."""
    _add_course(conn, is_active=True)
    periods_before = conn.execute(
        "SELECT id, is_active FROM global_budget_periods ORDER BY id"
    ).fetchall()

    conn.execute(_load("disable_global.sql"))

    assert conn.execute(
        "SELECT id, is_active FROM global_budget_periods ORDER BY id"
    ).fetchall() == periods_before
    assert _course_is_active(conn) is True


# ---------- per course ----------


def test_disable_course_turns_one_course_off(conn):
    _add_course(conn, is_active=True)
    rows = conn.execute(_load_course_query("disable_course.sql"), COURSE_PARAMS).fetchall()
    assert rows == [(TEST_INSTANCE, TEST_COURSE_ID, False)]
    assert _course_is_active(conn) is False


def test_enable_course_turns_one_course_on(conn):
    _add_course(conn, is_active=False)
    rows = conn.execute(_load_course_query("enable_course.sql"), COURSE_PARAMS).fetchall()
    assert len(rows) == 1 and rows[0][-1] is True
    assert _course_is_active(conn) is True


def test_course_queries_touch_only_the_named_course(conn):
    """Same course_id on another instance, and another course on the same
    instance, must both stay as they were."""
    _add_course(conn, is_active=True)
    conn.execute(
        "INSERT INTO course_budgets (instance, course_id, max_budget, is_active) VALUES "
        "('other.flagqueries.local', %s, 50.00, TRUE), (%s, %s, 50.00, TRUE)",
        (TEST_COURSE_ID, TEST_INSTANCE, TEST_COURSE_ID + 1),
    )

    conn.execute(_load_course_query("disable_course.sql"), COURSE_PARAMS)

    others = conn.execute(
        "SELECT is_active FROM course_budgets "
        "WHERE (instance, course_id) IN (('other.flagqueries.local', %s), (%s, %s))",
        (TEST_COURSE_ID, TEST_INSTANCE, TEST_COURSE_ID + 1),
    ).fetchall()
    assert others == [(True,), (True,)]


def test_course_queries_change_nothing_for_an_unknown_course(conn):
    """No budget row: the query reports zero rows instead of creating one. A
    course with no row is already refused by the gatekeeper."""
    for name in ("enable_course.sql", "disable_course.sql"):
        assert conn.execute(_load_course_query(name), COURSE_PARAMS).fetchall() == []
    count = conn.execute(
        "SELECT COUNT(*) FROM course_budgets WHERE instance = %s", (TEST_INSTANCE,)
    ).fetchone()[0]
    assert count == 0
