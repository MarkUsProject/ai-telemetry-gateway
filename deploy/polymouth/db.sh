#!/usr/bin/env bash
# Runs the installed db/migrate.sh as aitg_admin.
#
# Usage: db.sh <conf> up|seed|status
#
#   up      apply db/migrations/*.sql (idempotent)
#   seed    apply the rendered staging seeds (idempotent)
#   status  list applied migrations
#
# Reads the aitg_admin connection string from secrets.env, keeping passwords
# off the command line.

source "$(dirname "$0")/lib.sh"
load_conf "${1:-}"
action="${2:-}"
case "$action" in
  up|seed|status) ;;
  *) die "usage: $0 <conf> up|seed|status" ;;
esac

load_secrets
exec bash "$INSTALL_DIR/db/migrate.sh" "$action"
