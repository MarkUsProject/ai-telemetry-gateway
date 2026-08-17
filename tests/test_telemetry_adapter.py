"""Tests for the telemetry adapter.

Three layers:
1. Pure mapping — fake LiteLLM event in, asserted row dict out.
2. Dead-letter — simulate DB failure, confirm the row lands in the JSONL.
3. Integration — write to the running aitg-postgres, confirm one usage_logs row.

Tests run against the local-stack DB only when ``DATABASE_URL`` is set; pure
tests run anywhere.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import types
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import telemetry_adapter as ta  # noqa: E402

FULL_METADATA = {
    "instance": "markus.example.edu",
    "course_id": 12,
    "assignment_id": 34,
    "group_id": 56,
    "batch_id": 7,
    "category": "student",
}


def _fake_response(*, response_id="req_abc", prompt=100, completion=50, cached=20, reasoning=10):
    usage = types.SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        prompt_tokens_details=types.SimpleNamespace(cached_tokens=cached),
        completion_tokens_details=types.SimpleNamespace(reasoning_tokens=reasoning),
    )
    return types.SimpleNamespace(id=response_id, usage=usage)


@pytest.fixture(autouse=True)
def _pin_rate(monkeypatch):
    # Pin the FX rate so cost assertions are exact.
    monkeypatch.setenv("AITG_USD_TO_CAD_RATE", "1.40")


# ---------- pure mapping ----------


def test_extract_metadata_from_litellm_params():
    kwargs = {"litellm_params": {"metadata": {"spend_logs_metadata": FULL_METADATA}}}
    assert ta.extract_metadata(kwargs) == FULL_METADATA


def test_extract_metadata_from_top_level_metadata():
    kwargs = {"metadata": {"spend_logs_metadata": FULL_METADATA}}
    assert ta.extract_metadata(kwargs) == FULL_METADATA


def test_extract_metadata_returns_empty_when_absent():
    assert ta.extract_metadata({"model": "gpt-4o-mini"}) == {}


def test_build_row_maps_every_field():
    kwargs = {
        "litellm_params": {"metadata": {"spend_logs_metadata": FULL_METADATA}},
        "response_cost": 0.01,  # USD
    }
    row = ta.build_row(kwargs, _fake_response())

    assert row["provider_request_id"] == "req_abc"
    assert row["instance"] == "markus.example.edu"
    assert (row["course_id"], row["assignment_id"], row["group_id"]) == (12, 34, 56)
    assert row["batch_id"] == 7
    assert row["requester_role"] == "student"
    assert row["input_tokens"] == 100
    assert row["output_tokens"] == 50
    assert row["cached_tokens"] == 20
    assert row["reasoning_tokens"] == 10

    # total_cost = USD * CAD rate (pinned to 1.40).
    assert row["total_cost"] == pytest.approx(0.01 * 1.40)

    # Formula reproduces: (input + output) * unit_price == total_cost.
    billed = row["input_tokens"] + row["output_tokens"]
    assert billed * row["unit_price"] == pytest.approx(row["total_cost"])


def test_build_row_handles_missing_token_details():
    response = types.SimpleNamespace(
        id="req_no_details",
        usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )
    kwargs = {
        "litellm_params": {"metadata": {"spend_logs_metadata": FULL_METADATA}},
        "response_cost": 0.001,
    }
    row = ta.build_row(kwargs, response)
    assert row["cached_tokens"] == 0
    assert row["reasoning_tokens"] == 0


def test_build_row_zero_billed_tokens_yields_zero_unit_price():
    response = types.SimpleNamespace(
        id="req_zero",
        usage=types.SimpleNamespace(prompt_tokens=0, completion_tokens=0),
    )
    kwargs = {
        "litellm_params": {"metadata": {"spend_logs_metadata": FULL_METADATA}},
        "response_cost": 0.0,
    }
    row = ta.build_row(kwargs, response)
    assert row["unit_price"] == 0.0
    assert row["total_cost"] == 0.0


def test_build_row_rejects_missing_attribution():
    incomplete = {"instance": "markus.example.edu", "course_id": 1}
    kwargs = {"litellm_params": {"metadata": {"spend_logs_metadata": incomplete}}, "response_cost": 0.01}
    with pytest.raises(ValueError, match="missing attribution"):
        ta.build_row(kwargs, _fake_response())


# ---------- dead letter ----------


def test_persist_appends_to_dead_letter_when_db_fails(monkeypatch, tmp_path):
    dead_letter = tmp_path / "dead-letter.jsonl"
    monkeypatch.setenv("AITG_DEAD_LETTER_PATH", str(dead_letter))

    def _broken_db():
        raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(ta, "_open_db", _broken_db)

    row = {
        "provider_request_id": "req_dl_1",
        "instance": "markus.example.edu",
        "course_id": 12, "assignment_id": 34, "group_id": 56,
        "batch_id": None, "requester_role": "student",
        "input_tokens": 10, "cached_tokens": 0, "output_tokens": 5, "reasoning_tokens": 0,
        "unit_price": 0.001, "total_cost": 0.015,
    }
    assert ta.persist(row) is False

    lines = dead_letter.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    envelope = json.loads(lines[0])
    assert envelope["row"]["provider_request_id"] == "req_dl_1"
    assert "recorded_at" in envelope


def test_success_hook_skips_silently_when_attribution_missing(monkeypatch, tmp_path):
    """An event reaching the success hook without attribution must not crash
    and must not write a partial row. The pre-call guard should have stopped it."""
    dead_letter = tmp_path / "dead-letter.jsonl"
    monkeypatch.setenv("AITG_DEAD_LETTER_PATH", str(dead_letter))
    monkeypatch.setattr(ta, "_open_db", lambda: pytest.fail("DB must not be called"))

    asyncio.run(
        ta.telemetry_adapter_instance.async_log_success_event(
            kwargs={"response_cost": 0.01},  # no metadata
            response_obj=_fake_response(),
            start_time=None,
            end_time=None,
        )
    )
    assert not dead_letter.exists()


# ---------- integration (live aitg-postgres) ----------


@pytest.fixture
def live_db_url():
    url = os.environ.get("AITG_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set AITG_TEST_DATABASE_URL to enable live integration tests")
    return url


def _live_row(provider_request_id: str) -> dict:
    """A complete usage_logs row shaped like one ``build_row`` produces."""
    return {
        "provider_request_id": provider_request_id,
        "instance": "markus.example.edu",
        "course_id": 12, "assignment_id": 34, "group_id": 56,
        "batch_id": None, "requester_role": "student",
        "input_tokens": 100, "cached_tokens": 20,
        "output_tokens": 50, "reasoning_tokens": 10,
        "unit_price": 0.0001, "total_cost": 0.015,
    }


def test_persist_writes_one_row_to_live_db(live_db_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", live_db_url)
    # AITG_ENCRYPTION_KEY must already be set in the env for the api_keys
    # sentinel encryption; the test runner inherits it from local-stack/.env.

    unique_id = f"test_{uuid.uuid4().hex}"
    assert ta.persist(_live_row(unique_id)) is True

    import psycopg

    with psycopg.connect(live_db_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT instance, course_id, total_cost FROM aitg.usage_logs WHERE provider_request_id = %s",
            (unique_id,),
        )
        landed = cur.fetchone()
        cur.execute("DELETE FROM aitg.usage_logs WHERE provider_request_id = %s", (unique_id,))
        conn.commit()

    assert landed is not None
    assert landed[0] == "markus.example.edu"
    assert landed[1] == 12
    assert float(landed[2]) == pytest.approx(0.015)


def test_duplicate_provider_request_id_warns_instead_of_vanishing(live_db_url, monkeypatch, caplog):
    """A reused request id must not disappear without a trace.

    ``ON CONFLICT DO NOTHING`` swallows the second write, so before the warning
    the ledger silently recorded only the first call of a run.
    """
    monkeypatch.setenv("DATABASE_URL", live_db_url)

    unique_id = f"test_{uuid.uuid4().hex}"

    import psycopg

    try:
        with caplog.at_level("WARNING", logger="aitg.telemetry_adapter"):
            assert ta.persist(_live_row(unique_id)) is True
            # A clean insert stays quiet; without this the assertions below
            # would also pass for an _insert_row that never reports success.
            assert caplog.text == ""

            assert ta.persist(_live_row(unique_id)) is True

        with psycopg.connect(live_db_url) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM aitg.usage_logs WHERE provider_request_id = %s",
                (unique_id,),
            )
            assert cur.fetchone()[0] == 1
    finally:
        with psycopg.connect(live_db_url) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM aitg.usage_logs WHERE provider_request_id = %s", (unique_id,))
            conn.commit()

    assert "already recorded" in caplog.text
    assert unique_id in caplog.text


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"cache_hit": True}, True),
        ({"litellm_params": {"cache_hit": True}}, True),
        ({"cache_hit": False}, False),
        ({}, False),
    ],
)
def test_is_cache_hit_reads_both_litellm_shapes(kwargs, expected):
    assert ta.is_cache_hit(kwargs) is expected


def test_skip_reason_blames_spend_only_when_nothing_was_cached():
    """Both branches are asserted here so they hold without a live database."""
    assert "served from cache" in ta._skip_reason(True)
    assert "under-reporting spend" not in ta._skip_reason(True)
    assert "under-reporting spend" in ta._skip_reason(False)


def test_cached_replay_says_nothing_was_billed(live_db_url, monkeypatch, caplog):
    """A cached reply must not read as a lost row.

    LiteLLM replays the stored response with its original id, so the insert
    conflicts. No upstream call happened, so the absent row is correct.
    """
    monkeypatch.setenv("DATABASE_URL", live_db_url)
    unique_id = f"test_{uuid.uuid4().hex}"

    import psycopg

    try:
        assert ta.persist(_live_row(unique_id)) is True
        with caplog.at_level("WARNING", logger="aitg.telemetry_adapter"):
            assert ta.persist(_live_row(unique_id), cache_hit=True) is True
    finally:
        with psycopg.connect(live_db_url) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM aitg.usage_logs WHERE provider_request_id = %s", (unique_id,))
            conn.commit()

    assert "served from cache" in caplog.text
    assert "under-reporting spend" not in caplog.text
