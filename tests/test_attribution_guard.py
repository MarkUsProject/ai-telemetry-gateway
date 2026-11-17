"""Tests for the proxy-boundary attribution guard (lib/attribution_guard.py).

Pure-function and hook behaviour, no running proxy and no litellm install
required. The hook is async, so we drive it with asyncio.run to avoid a
pytest-asyncio dependency.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import attribution_guard  # noqa: E402
from attribution_guard import (  # noqa: E402
    AttributionGuard,
    extract_attribution,
    missing_fields,
)

FULL_METADATA = {
    "instance": "markus.example.edu",
    "course_id": 12,
    "assignment_id": 34,
    "group_id": 56,
    "batch_id": None,
    "category": "student",
}


def _call(data, call_type="completion"):
    guard = AttributionGuard()
    return asyncio.run(guard.async_pre_call_hook(None, None, data, call_type))


def test_extract_from_litellm_metadata():
    data = {"litellm_metadata": {"spend_logs_metadata": FULL_METADATA}}
    assert extract_attribution(data) == FULL_METADATA


def test_extract_from_metadata_fallback():
    data = {"metadata": {"spend_logs_metadata": FULL_METADATA}}
    assert extract_attribution(data) == FULL_METADATA


def test_extract_returns_empty_when_absent():
    assert extract_attribution({"model": "gpt-4o-mini"}) == {}


def test_missing_fields_none_when_complete():
    assert missing_fields(FULL_METADATA) == []


def test_missing_fields_lists_absent_and_null():
    attribution = {"instance": "m.edu", "course_id": None, "assignment_id": 34}
    assert missing_fields(attribution) == ["course_id", "group_id"]


def test_hook_allows_complete_attribution():
    data = {"litellm_metadata": {"spend_logs_metadata": FULL_METADATA}}
    assert _call(data) is data


def test_hook_rejects_missing_attribution():
    data = {"litellm_metadata": {"spend_logs_metadata": {"instance": "m.edu"}}}
    with pytest.raises(Exception) as exc:
        _call(data)
    message = str(getattr(exc.value, "detail", exc.value))
    assert "course_id" in message and "assignment_id" in message and "group_id" in message


def test_hook_rejects_when_metadata_entirely_absent():
    with pytest.raises(Exception):
        _call({"model": "gpt-4o-mini"})


# The names LiteLLM gives the routes that can reach a model. "atext_completion"
# and "aembedding" are what /v1/completions and /v1/embeddings pass today.
OTHER_CALL_TYPES = [
    "acompletion",
    "text_completion",
    "atext_completion",
    "embedding",
    "aembedding",
    "aresponses",
    "anthropic_messages",
    "aimage_generation",
    "pass_through_endpoint",
    "a_call_type_litellm_adds_later",
]


@pytest.mark.parametrize("call_type", OTHER_CALL_TYPES)
def test_hook_rejects_every_call_type_without_attribution(call_type):
    with pytest.raises(Exception) as exc:
        _call({"model": "gpt-4o"}, call_type=call_type)
    assert "Missing required MarkUs attribution" in str(getattr(exc.value, "detail", exc.value))


@pytest.mark.parametrize("call_type", OTHER_CALL_TYPES)
def test_hook_allows_every_call_type_with_attribution(call_type):
    data = {"litellm_metadata": {"spend_logs_metadata": FULL_METADATA}}
    assert _call(data, call_type=call_type) is data


def test_no_call_type_is_let_through_today():
    # Adding a name here opens a way around the guards. Change this test in
    # the same commit, so the choice shows up in review.
    assert attribution_guard.UNGUARDED_CALL_TYPES == frozenset()


def test_hook_skips_a_call_type_let_through_on_purpose(monkeypatch):
    monkeypatch.setattr(attribution_guard, "UNGUARDED_CALL_TYPES", frozenset({"health_probe"}))
    data = {"model": "gpt-4o"}
    assert _call(data, call_type="health_probe") is data
