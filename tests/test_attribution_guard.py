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


def test_hook_ignores_non_completion_calls():
    # Embeddings and the like carry no grading attribution; never block them.
    data = {"model": "text-embedding-3-small"}
    assert _call(data, call_type="embedding") is data
