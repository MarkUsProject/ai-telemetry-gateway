"""Role privilege tests. aitg_app can CRUD but cannot modify schema."""

from __future__ import annotations

import psycopg
import pytest


def test_app_role_can_select(app_conn):
    rows = app_conn.execute(
        "SELECT period_code FROM global_budget_periods WHERE period_code = 'local_dev'"
    ).fetchall()
    assert rows == [("local_dev",)]


def test_app_role_can_insert(app_conn):
    app_conn.execute(
        """
        INSERT INTO api_keys (provider, key_name, encrypted_key)
        VALUES ('OpenAI', 'priv-test', 'ciphertext')
        """
    )


def test_app_role_cannot_drop_table(app_conn):
    """A leaked aitg_app credential must not be able to delete the audit trail."""
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute("DROP TABLE usage_logs")


def test_app_role_cannot_create_table(app_conn):
    """Schema changes are admin-role only."""
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute("CREATE TABLE app_smuggled (id SERIAL PRIMARY KEY)")


def test_app_role_cannot_truncate(app_conn):
    """TRUNCATE bypasses row-level triggers and would erase audit history."""
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute("TRUNCATE usage_logs")
