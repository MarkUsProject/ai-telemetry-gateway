"""Tests for the dead-letter drain (lib/drain_dead_letter.py).

The drain is exercised end-to-end against the running aitg-postgres when
``AITG_TEST_DATABASE_URL`` is set. The no-DB path is covered by a small
file-only test that confirms the file is restored if the DB cannot be reached.
"""

from __future__ import annotations

import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import drain_dead_letter as drain  # noqa: E402
import telemetry_adapter as ta  # noqa: E402


def _write_envelope(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {"recorded_at": "2026-05-25T00:00:00Z", "row": row}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(envelope) + "\n")


@pytest.fixture
def live_db_url():
    url = os.environ.get("AITG_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set AITG_TEST_DATABASE_URL to enable live drain tests")
    return url


def _row(provider_request_id):
    return {
        "provider_request_id": provider_request_id,
        "instance": "markus.example.edu",
        "course_id": 12, "assignment_id": 34, "group_id": 56,
        "batch_id": None, "requester_role": "student",
        "input_tokens": 10, "cached_tokens": 0,
        "output_tokens": 5, "reasoning_tokens": 0,
        "unit_price": 0.001, "total_cost": 0.005,
    }


def test_drain_noop_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("AITG_DEAD_LETTER_PATH", str(tmp_path / "nothing.jsonl"))
    drained, remaining = drain.drain_once()
    assert (drained, remaining) == (0, 0)


def test_drain_restores_file_when_db_unreachable(monkeypatch, tmp_path):
    dead_letter = tmp_path / "dead-letter.jsonl"
    monkeypatch.setenv("AITG_DEAD_LETTER_PATH", str(dead_letter))
    _write_envelope(dead_letter, _row("req_keep_me"))

    def _broken_db():
        raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(ta, "_open_db", _broken_db)
    drained, remaining = drain.drain_once()

    assert drained == 0
    assert remaining == 1
    assert dead_letter.exists(), "dead letter must be restored when the drain cannot connect"
    # No leftover .draining file.
    assert not (tmp_path / "dead-letter.jsonl.draining").exists()


def test_restore_appends_so_concurrent_writes_are_not_clobbered(monkeypatch, tmp_path):
    """If the live hook appends a fresh row while the drain is running and the
    drain then fails, the restore must keep both the staged and the fresh rows."""
    dead_letter = tmp_path / "dead-letter.jsonl"
    monkeypatch.setenv("AITG_DEAD_LETTER_PATH", str(dead_letter))
    _write_envelope(dead_letter, _row("req_staged"))

    def _broken_db_after_concurrent_write():
        # Simulate the live hook appending to the now-fresh path between
        # rename-to-staging and the DB call inside drain_once.
        _write_envelope(dead_letter, _row("req_concurrent"))
        raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(ta, "_open_db", _broken_db_after_concurrent_write)
    drained, remaining = drain.drain_once()

    assert drained == 0
    assert remaining == 2
    ids = {json.loads(line)["row"]["provider_request_id"] for line in dead_letter.read_text().splitlines() if line.strip()}
    assert ids == {"req_staged", "req_concurrent"}


def test_drain_replays_into_live_db(live_db_url, monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", live_db_url)
    dead_letter = tmp_path / "dead-letter.jsonl"
    monkeypatch.setenv("AITG_DEAD_LETTER_PATH", str(dead_letter))

    unique_id = f"drain_{uuid.uuid4().hex}"
    _write_envelope(dead_letter, _row(unique_id))

    drained, remaining = drain.drain_once()
    assert (drained, remaining) == (1, 0)
    assert not dead_letter.exists() or dead_letter.read_text().strip() == ""

    import psycopg

    with psycopg.connect(live_db_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM aitg.usage_logs WHERE provider_request_id = %s", (unique_id,)
        )
        landed = cur.fetchone()
        cur.execute("DELETE FROM aitg.usage_logs WHERE provider_request_id = %s", (unique_id,))
        conn.commit()
    assert landed is not None
