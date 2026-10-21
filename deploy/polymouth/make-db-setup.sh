#!/usr/bin/env bash
# Writes ${INSTALL_DIR}/deploy/db-setup.sql, the file Dean runs once as the
# postgres superuser (Email 2, step 1).
#
# Usage: make-db-setup.sh <conf>
#
# Run it after install.sh, right before Email 2 (or before trial-pg.sh setup
# in the rehearsal). The file holds both role passwords from secrets.env, so
# delete it once Dean confirms step 1. install.sh skips this file, so a
# re-install keeps it deleted.

source "$(dirname "$0")/lib.sh"
load_conf "${1:-}"
load_secrets

target="$INSTALL_DIR/deploy/db-setup.sql"
export PG_DB

umask 077
python3 "$KIT_DIR/render.py" "$KIT_DIR/templates/db-setup.sql.tmpl" "$target"
# Mateo reads it; Dean reads it as root.
chmod 600 "$target"
log "wrote $target. Delete it after Dean runs it."
