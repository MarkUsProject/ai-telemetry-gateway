#!/usr/bin/env python3
"""Render a deploy template by filling ${NAME} placeholders from the environment.

Usage: render.py TEMPLATE OUTPUT

install.sh exports the conf file and secrets.env before calling this. Every
value is checked against a strict pattern before it reaches SQL, so a typo in
the conf file stops the install instead of producing broken or unsafe SQL.
Stdlib only: it runs on polymouth's system Python before the venv exists.
"""

from __future__ import annotations

import os
import re
import sys
from decimal import Decimal
from pathlib import Path
from string import Template

_MONEY = r"\d{1,10}(\.\d{1,2})?"
_TIMESTAMP = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"

# Each placeholder a template may use, with the pattern its value must match.
PATTERNS = {
    "AITG_ADMIN_PASSWORD": r"[0-9a-f]{32,128}",
    "AITG_APP_PASSWORD": r"[0-9a-f]{32,128}",
    "PG_DB": r"[a-z_][a-z0-9_]{0,62}",
    "MARKUS_INSTANCE": r"[A-Za-z0-9.-]{1,255}",
    "COURSE_IDS": r"\d+(,\d+)*",
    "COURSE_BUDGET_CAD": _MONEY,
    "COURSE_ALERT_CAD": _MONEY,
    "TERM_BUDGET_CAD": _MONEY,
    "PERIOD_CODE": r"[a-z0-9_]{1,20}",
    "PERIOD_NAME": r"[A-Za-z0-9 _.-]{1,100}",
    "PERIOD_START": _TIMESTAMP,
    "PERIOD_END": _TIMESTAMP,
}


def collect_values(template_text: str) -> dict[str, str]:
    """Return a checked value for every placeholder in the template."""
    names = {
        m.group("braced") or m.group("named")
        for m in Template.pattern.finditer(template_text)
        if m.group("braced") or m.group("named")
    }
    unknown = names - PATTERNS.keys()
    if unknown:
        sys.exit(f"error: template uses unknown placeholders: {sorted(unknown)}")

    values = {}
    for name in sorted(names):
        value = os.environ.get(name, "")
        if not re.fullmatch(PATTERNS[name], value):
            sys.exit(f"error: {name}={value!r} does not match {PATTERNS[name]}")
        values[name] = value
    return values


def check_relationships(values: dict[str, str]) -> None:
    """Catch checked values that are each valid but wrong together."""
    budget, alert = values.get("COURSE_BUDGET_CAD"), values.get("COURSE_ALERT_CAD")
    if budget and alert and Decimal(alert) >= Decimal(budget):
        sys.exit(f"error: COURSE_ALERT_CAD ({alert}) must sit below COURSE_BUDGET_CAD ({budget})")
    start, end = values.get("PERIOD_START"), values.get("PERIOD_END")
    if start and end and start >= end:
        sys.exit(f"error: PERIOD_START ({start}) must come before PERIOD_END ({end})")


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("usage: render.py TEMPLATE OUTPUT")
    template_path, output_path = Path(sys.argv[1]), Path(sys.argv[2])

    text = template_path.read_text()
    values = collect_values(text)
    check_relationships(values)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(Template(text).substitute(values))


if __name__ == "__main__":
    main()
