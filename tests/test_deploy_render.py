"""Tests for the polymouth deploy kit's template renderer (deploy/polymouth/render.py).

Pure checks on placeholder values. No database needed.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "deploy", "polymouth"))
import render  # noqa: E402

SEED = "INSERT ... VALUES (${COURSE_IDS}, ${COURSE_BUDGET_CAD}, ${COURSE_ALERT_CAD});"


@pytest.fixture
def budget_env(monkeypatch):
    monkeypatch.setenv("COURSE_IDS", "1,2,3")
    monkeypatch.setenv("COURSE_BUDGET_CAD", "10.00")
    monkeypatch.setenv("COURSE_ALERT_CAD", "8.00")


def test_collect_values_returns_checked_values(budget_env):
    assert render.collect_values(SEED) == {
        "COURSE_IDS": "1,2,3",
        "COURSE_BUDGET_CAD": "10.00",
        "COURSE_ALERT_CAD": "8.00",
    }


def test_collect_values_rejects_a_bad_value(budget_env, monkeypatch):
    monkeypatch.setenv("COURSE_IDS", "1; DROP TABLE x")
    with pytest.raises(SystemExit, match="COURSE_IDS"):
        render.collect_values(SEED)


def test_collect_values_rejects_an_unknown_placeholder():
    with pytest.raises(SystemExit, match="unknown placeholders"):
        render.collect_values("SELECT ${NOT_A_SETTING};")


def test_check_relationships_rejects_alert_at_or_above_budget():
    with pytest.raises(SystemExit, match="COURSE_ALERT_CAD"):
        render.check_relationships({"COURSE_BUDGET_CAD": "10.00", "COURSE_ALERT_CAD": "10.00"})


def test_check_relationships_rejects_period_end_before_start():
    with pytest.raises(SystemExit, match="PERIOD_START"):
        render.check_relationships(
            {"PERIOD_START": "2027-04-30 00:00:00", "PERIOD_END": "2026-10-01 00:00:00"}
        )


def test_check_relationships_ignores_unchecked_values(monkeypatch):
    # A template without budget placeholders skips the budget check, even with
    # a malformed budget in the environment.
    monkeypatch.setenv("COURSE_BUDGET_CAD", "10,00")
    render.check_relationships({})
