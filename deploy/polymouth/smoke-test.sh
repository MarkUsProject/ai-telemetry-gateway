#!/usr/bin/env bash
# Proves the gateway's guards work end to end: user guide steps 2-5 as one
# script with pass/fail output.
#
# Usage: smoke-test.sh <conf> <model> [base_url]
#
#   rehearsal:  smoke-test.sh rehearsal.conf gpt-4o-mini-mock
#   go-live:    smoke-test.sh staging.conf gpt-4o https://polymouth.teach.cs.toronto.edu
#
# base_url defaults to the loopback listener. Run it on polymouth.
#
# The test funds its own course row (course_id 999999). On exit, pass or fail,
# it deletes that row, its usage rows and its fake spend. Real course rows
# stay as they were. With gpt-4o, check 5 makes one real call of at most
# 5 output tokens; the term's slot counters keep that call's cost.
#
# The test never turns the global AI feature flag on. What it does depends on
# the flag as it finds it:
#   on   it turns the flag off for one call (check 4), then back on. Real
#        calls are refused for about a second.
#   off  it changes nothing. Check 4 confirms the refusal, and checks 5-6,
#        which need a call to pass, are skipped. Run
#        db/queries/enable_global.sql first for the full set.

source "$(dirname "$0")/lib.sh"
load_conf "${1:-}"
model="${2:-}"
[[ -n "$model" ]] || die "usage: $0 <conf> <model> [base_url]"
base_url="${3:-http://${LISTEN_HOST}:${LISTEN_PORT}}"
load_secrets

TEST_COURSE=999999
SPEND_ID="smoke-test-spend-$$"
url="${base_url%/}/v1/chat/completions"
body_file="$(mktemp)"
failures=0
skipped=0
# 1 while the test holds the flag off, so cleanup knows to turn it back on.
flag_held_off=0

attribution="{\"instance\":\"${MARKUS_INSTANCE}\",\"course_id\":${TEST_COURSE},\"assignment_id\":1,\"group_id\":1,\"batch_id\":null,\"category\":\"instructor\"}"

# set_flag <TRUE|FALSE> -> writes the global AI feature flag
set_flag() {
  admin_psql -c "INSERT INTO aitg.global_settings (ai_enabled) VALUES ($1)
                 ON CONFLICT (singleton) DO UPDATE SET ai_enabled = $1, updated_at = NOW();" >/dev/null
}

cleanup() {
  if [[ "$flag_held_off" -eq 1 ]]; then
    set_flag TRUE || printf 'warning: the AI feature flag was on and is now OFF. Run db/queries/enable_global.sql.\n' >&2
  fi
  admin_psql <<SQL >/dev/null || true
DELETE FROM aitg.usage_logs WHERE instance = '${MARKUS_INSTANCE}' AND course_id = ${TEST_COURSE};
DELETE FROM aitg.course_budgets WHERE instance = '${MARKUS_INSTANCE}' AND course_id = ${TEST_COURSE};
SQL
  rm -f "$body_file"
}
trap cleanup EXIT

# post <max_tokens|-> <with_attribution:yes|no> -> prints the HTTP status
post() {
  local max_tokens="$1" with_attribution="$2" payload
  payload="{\"model\":\"${model}\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hi.\"}]"
  [[ "$max_tokens" == "-" ]] || payload+=",\"max_tokens\":${max_tokens}"
  payload+="}"
  local args=(-s -o "$body_file" -w '%{http_code}' -X POST "$url"
              -H "Authorization: Bearer ${LITELLM_MASTER_KEY}"
              -H "Content-Type: application/json")
  [[ "$with_attribution" == "no" ]] || args+=(-H "x-litellm-spend-logs-metadata: ${attribution}")
  curl "${args[@]}" -d "$payload"
}

# check <name> <expected status> <actual status> [text the body must contain]
check() {
  local name="$1" want="$2" got="$3" text="${4:-}"
  if [[ "$got" == "$want" ]] && { [[ -z "$text" ]] || grep -qF "$text" "$body_file"; }; then
    printf 'PASS  %s\n' "$name"
  else
    printf 'FAIL  %s (HTTP %s): %s\n' "$name" "$got" "$(head -c 300 "$body_file")"
    failures=$((failures + 1))
  fi
}

usage_rows() {
  admin_psql --tuples-only --no-align -c \
    "SELECT COUNT(*) FROM aitg.usage_logs WHERE instance = '${MARKUS_INSTANCE}' AND course_id = ${TEST_COURSE};"
}

log "funding test course ${TEST_COURSE} on ${MARKUS_INSTANCE}"
admin_psql -c "INSERT INTO aitg.course_budgets (instance, course_id, max_budget, alert_threshold, is_active)
               VALUES ('${MARKUS_INSTANCE}', ${TEST_COURSE}, 100.00, NULL, TRUE)
               ON CONFLICT (instance, course_id) DO UPDATE SET max_budget = 100.00, is_active = TRUE;" >/dev/null

# t, f, or empty when the row is absent (which the gatekeeper reads as off).
flag="$(admin_psql --tuples-only --no-align -c "SELECT ai_enabled FROM aitg.global_settings;")"

log "calling $url with model $model"

# 1. Attribution guard (user guide step 2).
check "refuses a call with no attribution" 400 "$(post 5 no)" "Missing required MarkUs attribution"

# 2-3. max_tokens rules (user guide step 3).
check "refuses max_tokens above the ceiling" 400 "$(post 99999 yes)" "exceeds the gateway ceiling"
check "refuses a call with no max_tokens" 400 "$(post - yes)" "max_tokens is required"

# 4. Global AI feature flag (user guide, "AI feature flag"): off refuses a
# call that is otherwise complete.
if [[ "$flag" == "t" ]]; then
  flag_held_off=1
  set_flag FALSE
  check "refuses a call while the AI feature flag is off" 400 "$(post 5 yes)" "AI features are currently disabled"
  set_flag TRUE
  flag_held_off=0
else
  check "refuses a call while the AI feature flag is off" 400 "$(post 5 yes)" "AI features are currently disabled"
  printf 'SKIP  accepts a labelled call (the AI feature flag is off)\n'
  printf 'SKIP  writes a usage_logs row (the AI feature flag is off)\n'
  printf 'SKIP  refuses a course over its budget (the AI feature flag is off)\n'
  skipped=3
fi

if [[ "$skipped" -eq 0 ]]; then
# 5. A labelled call passes and writes one ledger row (user guide step 4).
# The telemetry write runs after the reply, so poll for up to 15 seconds.
before="$(usage_rows)"
check "accepts a labelled call" 200 "$(post 5 yes)"
after="$before"
for _ in $(seq 15); do
  after="$(usage_rows)"
  [[ "$after" -gt "$before" ]] && break
  sleep 1
done
if [[ "$after" -gt "$before" ]]; then
  printf 'PASS  writes a usage_logs row\n'
else
  printf 'FAIL  writes a usage_logs row (count stayed at %s after 15 s)\n' "$before"
  failures=$((failures + 1))
fi

# 6. Course budget (user guide step 5): record CAD 2.00 of spend, cap at 1.00.
admin_psql -c "INSERT INTO aitg.usage_logs (provider_request_id, api_key_id, instance, course_id, assignment_id, group_id, total_cost)
               VALUES ('${SPEND_ID}', (SELECT id FROM aitg.api_keys ORDER BY id LIMIT 1), '${MARKUS_INSTANCE}', ${TEST_COURSE}, 1, 1, 2.00);
               UPDATE aitg.course_budgets SET max_budget = 1.00
                WHERE instance = '${MARKUS_INSTANCE}' AND course_id = ${TEST_COURSE};" >/dev/null
check "refuses a course over its budget" 400 "$(post 5 yes)" "Course budget exhausted"
fi

if [[ "$failures" -gt 0 ]]; then
  die "$failures check(s) failed"
fi
if [[ "$skipped" -gt 0 ]]; then
  log "checks passed, $skipped skipped: the AI feature flag is off. Turn it on (db/queries/enable_global.sql) and run this again for the full set."
else
  log "all checks passed"
fi
