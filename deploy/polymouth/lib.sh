# shellcheck shell=bash
# Shared helpers for the polymouth deploy scripts. Scripts source this file.
#
# Every script takes a conf file (staging.conf or rehearsal.conf) as its first
# argument and calls load_conf on it before doing anything else.

set -euo pipefail

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC2034  # used by install.sh
REPO_DIR="$(cd "${KIT_DIR}/../.." && pwd)"

log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# Loads a conf file, then checks every required setting is filled in.
# Settings left at CHANGE_ME stop the script, so every conf reaches the
# database complete.
load_conf() {
  local conf="${1:-}"
  [[ -n "$conf" ]] || die "usage: $0 <staging.conf|rehearsal.conf> ..."
  [[ -f "$conf" ]] || die "conf file not found: $conf"
  # shellcheck source=/dev/null
  source "$conf"

  local var
  for var in INSTALL_DIR SERVICE_USER PYTHON LITELLM_VERSION \
             LISTEN_HOST LISTEN_PORT PG_HOST PG_PORT PG_DB \
             MODEL_CONFIG MAX_TOKENS_CEILING MARKUS_INSTANCE COURSE_IDS \
             COURSE_BUDGET_CAD COURSE_ALERT_CAD \
             PERIOD_CODE PERIOD_NAME PERIOD_START PERIOD_END TERM_BUDGET_CAD; do
    [[ -n "${!var:-}" ]] || die "$var is empty in $conf"
    [[ "${!var}" != "CHANGE_ME" ]] || die "$var is still CHANGE_ME in $conf"
  done
}

# Loads secrets.env into the environment. make-secrets.sh writes it.
load_secrets() {
  local file="${INSTALL_DIR}/secrets.env"
  [[ -r "$file" ]] || die "missing $file (run make-secrets.sh first)"
  set -a
  # shellcheck source=/dev/null
  source "$file"
  set +a
}

# psql against the gateway database as aitg_admin.
admin_psql() {
  psql "$DATABASE_URL" --quiet --no-psqlrc --set ON_ERROR_STOP=on "$@"
}
