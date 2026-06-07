"""Shared pytest fixtures.

Tests run inside the `tests` service in docker-compose. The migrate service
has already applied all migrations against the postgres service, and the
seed step has run, so each test starts from a known good baseline.

DATABASE_URL is injected by docker-compose; AITG_ENCRYPTION_KEY similarly.
"""

from __future__ import annotations

import os

import psycopg
import pytest


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.fail("DATABASE_URL not set. Run tests via `docker compose run --rm tests`.")
    return url


@pytest.fixture
def conn(database_url: str):
    """A psycopg3 connection with autocommit off. Rolled back after the test."""
    with psycopg.connect(database_url) as connection:
        connection.execute("SET search_path TO aitg;")
        yield connection
        connection.rollback()


@pytest.fixture
def app_conn(database_url: str):
    """Connection as the aitg_app role (CRUD-only)."""
    # Switch role within the existing admin connection — simpler than juggling
    # a second password.
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE aitg_app;")
        connection.execute("SET search_path TO aitg;")
        yield connection
        connection.rollback()
