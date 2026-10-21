#!/usr/bin/env bash
# Mints the virtual key the staging autotester uses, and prints it once.
#
# Usage: make-virtual-key.sh <conf>
#
# Run it on polymouth: it calls /key/generate on the loopback listener, a
# route Apache keeps internal. The key:
#   - may call gpt-4o
#   - may reach the two routes Apache exposes
#   - carries an alias, so LiteLLM's key list shows it by name
#
# Put the printed key in the staging autotester's settings.local.yml, under
# ai_tester_env as GATEWAY_API_KEY (ai_feedback/models/OpenAIRemoteModel.py
# reads that name).

source "$(dirname "$0")/lib.sh"
load_conf "${1:-}"
load_secrets

response="$(curl -s -X POST "http://${LISTEN_HOST}:${LISTEN_PORT}/key/generate" \
  -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
        "key_alias": "staging-autotester",
        "models": ["gpt-4o"],
        "allowed_routes": ["/v1/chat/completions", "/chat/completions", "/v1/models", "/models"],
        "metadata": {"purpose": "staging autotester", "created_by": "make-virtual-key.sh"}
      }')"

key="$(printf '%s' "$response" | python3 -c 'import json, sys; print(json.load(sys.stdin).get("key", ""))' 2>/dev/null || true)"
[[ "$key" == sk-* ]] || die "key/generate failed: $response"

log "copy this key now; LiteLLM shows it once:"
printf '%s\n' "$key"
