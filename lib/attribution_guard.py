"""Proxy-boundary guard: reject chat completions missing MarkUs attribution.

Every cloud OpenAI call must reach the proxy carrying the four attribution
fields so the telemetry ledger knows which course to bill and the gatekeeper
can find the right budget. The autotester sends them as JSON in the
`x-litellm-spend-logs-metadata` request header; LiteLLM parses that header
into the request `data`. This hook fails the call loud at the proxy layer
when any required field is missing, rather than letting an anonymous row (or
one with NULL ids) reach `usage_logs`.

The gatekeeper hook adds the budget and kill-switch checks on top of the same
insertion point.

Wire-up — see local-stack/litellm-config.yaml and docker-compose.yml:
    litellm_settings:
      callbacks: attribution_guard.attribution_guard_instance

The litellm/fastapi imports are guarded so this module imports cleanly (and the
pure validation functions stay unit-testable) in the slim test image, which
does not install litellm. Inside the proxy container both are always present.
"""

from __future__ import annotations

try:  # litellm is only installed inside the proxy container.
    from litellm.integrations.custom_logger import CustomLogger as _Base
except ImportError:  # pragma: no cover - exercised only outside the container

    class _Base:  # minimal stand-in so unit tests can import this module
        pass


# usage_logs columns that are NOT NULL in the schema (db/migrations/005).
# batch_id (nullable) and requester_role/category (nullable) are intentionally
# not required here.
REQUIRED_FIELDS = ("instance", "course_id", "assignment_id", "group_id")

# Call types that carry grading attribution. Embeddings/health/etc. do not.
_GUARDED_CALL_TYPES = ("completion", "acompletion", "text_completion")


def extract_attribution(data: dict) -> dict:
    """Pull the attribution dict out of a LiteLLM request payload.

    LiteLLM stores the parsed `x-litellm-spend-logs-metadata` header under
    `spend_logs_metadata`, nested inside either `litellm_metadata` or
    `metadata` depending on version. We check both so the guard does not break
    when LiteLLM moves it.
    """
    for container_key in ("litellm_metadata", "metadata"):
        container = data.get(container_key)
        if not isinstance(container, dict):
            continue
        found = container.get("spend_logs_metadata")
        if isinstance(found, dict):
            return found
    return {}


def missing_fields(attribution: dict) -> list[str]:
    """Return the required attribution fields that are absent or empty."""
    return [field for field in REQUIRED_FIELDS if attribution.get(field) in (None, "")]


def rejection_message(missing: list[str]) -> str:
    """Operator-actionable error naming exactly what is missing."""
    return (
        "Missing required MarkUs attribution: "
        + ", ".join(missing)
        + ". Every gateway call must send instance, course_id, assignment_id and "
        "group_id as JSON in the x-litellm-spend-logs-metadata header."
    )


def _rejection(detail: str) -> Exception:
    """A 400 the caller can act on. Falls back to a plain error when fastapi
    is absent (outside the proxy container) so the reject path stays testable."""
    try:
        from fastapi import HTTPException

        return HTTPException(status_code=400, detail=detail)
    except ImportError:  # pragma: no cover - exercised only outside the container
        return ValueError(detail)


class AttributionGuard(_Base):
    """LiteLLM pre-call hook enforcing attribution on every grading call."""

    async def async_pre_call_hook(self, user_api_key_dict, cache, data: dict, call_type: str):
        if call_type not in _GUARDED_CALL_TYPES:
            return data
        missing = missing_fields(extract_attribution(data))
        if missing:
            raise _rejection(rejection_message(missing))
        return data


# LiteLLM imports this instance by the dotted path in the config's `callbacks`.
attribution_guard_instance = AttributionGuard()
