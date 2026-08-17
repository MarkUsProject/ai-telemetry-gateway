"""Post-call telemetry writer for the LiteLLM gateway.

LiteLLM fires ``async_log_success_event`` after every billable upstream call
with the response object and a ``kwargs`` dict that already carries the
computed USD cost. This hook maps that event onto a ``usage_logs`` row,
converts USD→CAD using the Bank of Canada rate (see ``lib/cad_fx``), and
writes the row.

A failed DB write does not lose the data: the structured row appends to a
JSONL dead-letter file (``AITG_DEAD_LETTER_PATH``). A separate drain replays
those rows once the database returns — see ``lib/drain_dead_letter``.

Partial-success rule: the success hook records every billable call. The
failure hook logs and walks away — LiteLLM's failure event fires when the
upstream did not return a usable response, which on OpenAI overwhelmingly
means no billing occurred.

Wire-up — see ``local-stack/litellm-config.yaml``::

    litellm_settings:
      callbacks:
        - attribution_guard.attribution_guard_instance
        - telemetry_adapter.telemetry_adapter_instance
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # litellm is only installed inside the proxy container.
    from litellm.integrations.custom_logger import CustomLogger as _Base
except ImportError:  # pragma: no cover

    class _Base:
        pass


sys.path.insert(0, os.path.dirname(__file__))
import cad_fx  # noqa: E402 — sibling module mounted at /app
import encryption  # noqa: E402 — Fernet helper from lib/encryption.py

_LOG = logging.getLogger("aitg.telemetry_adapter")

# usage_logs columns sourced from the attribution metadata header.
_REQUIRED_ATTRIBUTION = ("instance", "course_id", "assignment_id", "group_id")

# The api_keys row representing the upstream key LiteLLM holds. The real key
# lives in LiteLLM (Prisma store, LITELLM_SALT_KEY-encrypted); this row exists
# to satisfy the usage_logs.api_key_id foreign key. We Fernet-encrypt a
# sentinel string so the column shape is honestly "ciphertext".
_API_KEY_NAME = os.getenv("AITG_DEFAULT_API_KEY_NAME", "UofT OpenAI")
_API_KEY_PROVIDER = "OpenAI"
_API_KEY_SENTINEL_PLAINTEXT = "managed-by-litellm"


def _dead_letter_path() -> Path:
    return Path(os.getenv("AITG_DEAD_LETTER_PATH", "/tmp/aitg-dead-letter.jsonl"))


def extract_metadata(kwargs: dict) -> dict:
    """Pull the spend-logs metadata out of LiteLLM's kwargs.

    LiteLLM places parsed ``x-litellm-spend-logs-metadata`` under
    ``litellm_params.metadata.spend_logs_metadata``. The fallback paths cover
    older versions and the proxy-level metadata key.
    """
    for container in (kwargs.get("litellm_params"), kwargs):
        if not isinstance(container, dict):
            continue
        metadata = container.get("metadata")
        if not isinstance(metadata, dict):
            continue
        found = metadata.get("spend_logs_metadata")
        if isinstance(found, dict):
            return found
    return {}


def _usage_field(usage: Any, *path: str) -> int:
    """Pull a token count out of LiteLLM's nested usage object.

    OpenAI nests cached tokens under ``prompt_tokens_details.cached_tokens``
    and reasoning tokens under ``completion_tokens_details.reasoning_tokens``;
    LiteLLM exposes these as attributes or dict keys depending on version.
    """
    current = usage
    for key in path:
        if current is None:
            return 0
        if isinstance(current, dict):
            current = current.get(key)
        else:
            current = getattr(current, key, None)
    return int(current or 0)


def build_row(kwargs: dict, response_obj: Any) -> dict:
    """Map a LiteLLM event onto a ``usage_logs`` row dict.

    Pure function — no DB, no network. The CAD rate is the one input from the
    outside world; tests pin it via ``AITG_USD_TO_CAD_RATE``.
    """
    metadata = extract_metadata(kwargs)
    missing = [field for field in _REQUIRED_ATTRIBUTION if metadata.get(field) in (None, "")]
    if missing:
        # The pre-call guard should have rejected; reaching here means an event
        # slipped through. Loud failure beats a row with NULL ids.
        raise ValueError(f"Cannot record usage_logs row: missing attribution {missing}")

    usage = getattr(response_obj, "usage", None) or {}
    input_tokens = _usage_field(usage, "prompt_tokens")
    output_tokens = _usage_field(usage, "completion_tokens")
    cached_tokens = _usage_field(usage, "prompt_tokens_details", "cached_tokens")
    reasoning_tokens = _usage_field(usage, "completion_tokens_details", "reasoning_tokens")

    usd_cost = float(kwargs.get("response_cost") or 0.0)
    cad_rate = cad_fx.get_cad_rate()
    total_cost = usd_cost * cad_rate

    billed_tokens = input_tokens + output_tokens
    unit_price = (total_cost / billed_tokens) if billed_tokens > 0 else 0.0

    return {
        "provider_request_id": getattr(response_obj, "id", None) or kwargs.get("response_id"),
        "instance": metadata["instance"],
        "course_id": int(metadata["course_id"]),
        "assignment_id": int(metadata["assignment_id"]),
        "group_id": int(metadata["group_id"]),
        "batch_id": metadata.get("batch_id"),
        "requester_role": metadata.get("category"),
        "input_tokens": input_tokens,
        "cached_tokens": cached_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "unit_price": unit_price,
        "total_cost": total_cost,
    }


def _open_db():
    """Lazy psycopg connection; the hook does not own a pool.

    Reads ``AITG_DATABASE_URL`` first because LiteLLM mutates the standard
    ``DATABASE_URL`` at runtime to add Prisma-only query parameters
    (``connection_limit``, ``pool_timeout``) that psycopg refuses.
    """
    import psycopg  # imported lazily so unit tests need not install it

    return psycopg.connect(os.environ.get("AITG_DATABASE_URL") or os.environ["DATABASE_URL"])


def resolve_api_key_id(conn) -> int:
    """Upsert the single api_keys row representing the upstream key.

    On first call, inserts one row with a Fernet-encrypted sentinel value. On
    every subsequent call, the ON CONFLICT path is a no-op and we return the
    existing id. Cached at the process level by the caller.
    """
    encrypted = encryption.encrypt(_API_KEY_SENTINEL_PLAINTEXT)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO aitg.api_keys (provider, key_name, encrypted_key)
                 VALUES (%s, %s, %s)
            ON CONFLICT (key_name) DO UPDATE
                    SET key_name = EXCLUDED.key_name
              RETURNING id
            """,
            (_API_KEY_PROVIDER, _API_KEY_NAME, encrypted),
        )
        return cur.fetchone()[0]


def _insert_row(conn, row: dict, api_key_id: int) -> bool:
    """Insert one usage_logs row; False when the UNIQUE constraint swallowed it.

    ``ON CONFLICT DO NOTHING`` is silent, so an upstream reusing request ids
    looks exactly like a successful write. Report the difference instead.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO aitg.usage_logs (
                provider_request_id, api_key_id, instance, course_id,
                assignment_id, group_id, batch_id, requester_role,
                input_tokens, cached_tokens, output_tokens, reasoning_tokens,
                unit_price, total_cost
            ) VALUES (
                %(provider_request_id)s, %(api_key_id)s, %(instance)s, %(course_id)s,
                %(assignment_id)s, %(group_id)s, %(batch_id)s, %(requester_role)s,
                %(input_tokens)s, %(cached_tokens)s, %(output_tokens)s, %(reasoning_tokens)s,
                %(unit_price)s, %(total_cost)s
            )
            ON CONFLICT (provider_request_id) DO NOTHING
            """,
            {**row, "api_key_id": api_key_id},
        )
        return cur.rowcount == 1


def _append_dead_letter(row: dict) -> None:
    path = _dead_letter_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {"recorded_at": datetime.now(timezone.utc).isoformat(), "row": row}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(envelope) + "\n")


def persist(row: dict) -> bool:
    """Write the row, falling back to the dead letter on DB failure.

    Returns True if the row landed in the database, False if it landed in the
    dead letter. Either way the data is durable.

    A duplicate still counts as landed — the ledger already holds that call —
    and warns, because a run of them means spend is being under-reported.
    """
    try:
        with _open_db() as conn:
            api_key_id = resolve_api_key_id(conn)
            inserted = _insert_row(conn, row, api_key_id)
            conn.commit()
        if not inserted:
            _LOG.warning(
                "usage_logs row skipped: provider_request_id=%s is already recorded "
                "(instance=%s course_id=%s). Repeated skips mean the upstream is "
                "reusing request ids and the ledger is under-reporting spend.",
                row["provider_request_id"], row["instance"], row["course_id"],
            )
        return True
    except Exception:  # noqa: BLE001 - DB unreachable, schema drift, anything
        _LOG.exception("usage_logs write failed; appending to dead letter")
        _append_dead_letter(row)
        return False


class TelemetryAdapter(_Base):
    """Translates LiteLLM telemetry events into ``usage_logs`` rows."""

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        try:
            row = build_row(kwargs, response_obj)
        except ValueError:
            _LOG.exception("Skipping row: required attribution missing")
            return
        # DB write is sync psycopg; offload so we do not block the event loop.
        await asyncio.to_thread(persist, row)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        # Partial-success rule: upstream failures overwhelmingly do not bill.
        # Log for visibility; do not write to usage_logs.
        provider_request_id = getattr(response_obj, "id", None)
        _LOG.warning("upstream call failed (provider_request_id=%s); skipping ledger row", provider_request_id)


telemetry_adapter_instance = TelemetryAdapter()
