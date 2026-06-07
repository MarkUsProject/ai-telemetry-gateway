"""Replay the dead-letter JSONL into ``usage_logs`` when the database returns.

``lib/telemetry_adapter`` appends a row to a JSONL file whenever the
``usage_logs`` insert fails (DB unreachable, schema drift, etc.). This module
drains that file: it inserts every row, deduplicates via the
``provider_request_id`` UNIQUE constraint, and rewrites the file with whatever
could not be drained.

Invocation::

    python -m drain_dead_letter            # runs once
    python drain_dead_letter.py --watch    # polls every 60 s

The script is safe to run concurrently with the live telemetry hook: the
dead-letter file is renamed to a sibling ``.draining`` file before any insert
attempt, so a concurrent hook appends to a fresh file. If the drain crashes,
the ``.draining`` file remains for manual inspection.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import telemetry_adapter  # noqa: E402

_LOG = logging.getLogger("aitg.drain_dead_letter")


def drain_once(path: Path | None = None) -> tuple[int, int]:
    """Drain one batch from ``path`` (or the configured default).

    Returns ``(drained, remaining)``. ``drained`` includes rows the DB
    accepted and rows the UNIQUE constraint rejected as duplicates (both
    leave the dead letter, since the ledger has them).
    """
    path = path or telemetry_adapter._dead_letter_path()
    if not path.exists():
        return 0, 0

    staging = path.with_suffix(path.suffix + ".draining")
    path.rename(staging)

    try:
        drained, remaining_lines = _drain_staging(staging)
    except Exception:  # noqa: BLE001 - DB still down: put everything back
        _LOG.exception("Dead-letter drain failed; restoring file")
        _append_staging_to_path(staging, path)
        return 0, _count_lines(path)

    if remaining_lines:
        with path.open("a", encoding="utf-8") as handle:
            handle.writelines(remaining_lines)
    staging.unlink(missing_ok=True)
    return drained, len(remaining_lines)


def _drain_staging(staging: Path) -> tuple[int, list[str]]:
    """Replay every parseable row in ``staging`` into ``usage_logs``.

    Per-row failures (malformed JSON, re-insert errors) stay local — the row
    is logged and either skipped or kept for next time. DB-level failures
    escape so the caller can restore the file.
    """
    drained = 0
    remaining_lines: list[str] = []
    with telemetry_adapter._open_db() as conn:
        api_key_id = telemetry_adapter.resolve_api_key_id(conn)
        with staging.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    envelope = json.loads(line)
                    row = envelope["row"]
                except (json.JSONDecodeError, KeyError):
                    _LOG.exception("Skipping malformed dead-letter line: %s", line[:200])
                    continue
                try:
                    telemetry_adapter._insert_row(conn, row, api_key_id)
                    drained += 1
                except Exception:  # noqa: BLE001 - keep the row for next try
                    _LOG.exception("Re-insert failed; keeping row in dead letter")
                    remaining_lines.append(line + "\n")
        conn.commit()
    return drained, remaining_lines


def _append_staging_to_path(staging: Path, path: Path) -> None:
    """Restore staging rows by appending to path. Append (not rename) so we do
    not clobber rows a concurrent hook wrote into path after the drain began."""
    with staging.open("r", encoding="utf-8") as src, path.open("a", encoding="utf-8") as dst:
        for line in src:
            dst.write(line)
    staging.unlink(missing_ok=True)


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description="Drain the aitg dead-letter into usage_logs.")
    parser.add_argument("--watch", action="store_true", help="Poll forever instead of running once.")
    parser.add_argument("--interval", type=int, default=60, help="Seconds between polls in --watch mode.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    while True:
        drained, remaining = drain_once()
        _LOG.info("drained=%d remaining=%d", drained, remaining)
        if not args.watch:
            return 0 if remaining == 0 else 1
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
