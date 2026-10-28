#!/usr/bin/env bash
# Throwaway Postgres 16 for the rehearsal, owned by Mateo.
#
# Usage: trial-pg.sh <rehearsal.conf> start|setup|stop|destroy
#
#   start    initdb on first use, then start on 127.0.0.1:${PG_PORT}
#   setup    run the rendered deploy/db-setup.sql as the superuser, exactly
#            as Dean runs it on the real server
#   stop     stop the server, keep its data
#   destroy  stop the server and delete its data
#
# Client-auth rules: the two gateway roles log in over 127.0.0.1 with SCRAM
# passwords, mirroring Email 2. The superuser connects through a socket inside
# the 0700 data folder, which opens for Mateo's account.
#
# The data folder (TRIAL_PG_DIR) sits outside INSTALL_DIR, so install.sh's
# permission pass leaves it at 0700.

source "$(dirname "$0")/lib.sh"
load_conf "${1:-}"
action="${2:-}"

[[ -n "${TRIAL_PG_DIR:-}" ]] || die "TRIAL_PG_DIR is unset. Use rehearsal.conf."
# Port 5432 is polymouth's real cluster. This script runs on other ports.
[[ "$PG_PORT" != "5432" ]] || die "PG_PORT is 5432. Use rehearsal.conf."

PG_BIN=/usr/lib/postgresql/16/bin
data="$TRIAL_PG_DIR"
superuser_psql=(psql -h "$data" -p "$PG_PORT" -U postgres --no-psqlrc --set ON_ERROR_STOP=on)

case "$action" in
  start)
    if [[ ! -f "$data/PG_VERSION" ]]; then
      log "initdb in $data"
      "$PG_BIN/initdb" -D "$data" -U postgres --auth-local=trust --auth-host=reject >/dev/null
      cat > "$data/pg_hba.conf" <<EOF
local all     postgres                           trust
host  ${PG_DB} aitg_admin,aitg_app 127.0.0.1/32 scram-sha-256
EOF
    fi
    log "starting on 127.0.0.1:$PG_PORT"
    # The socket lives in the data folder itself (mode 0700).
    "$PG_BIN/pg_ctl" -D "$data" -l "$data/server.log" -w \
      -o "-p $PG_PORT -c listen_addresses=127.0.0.1 -c unix_socket_directories='$data'" start
    ;;
  setup)
    sql="$INSTALL_DIR/deploy/db-setup.sql"
    [[ -r "$sql" ]] || die "missing $sql (run make-db-setup.sh first)"
    log "running $sql"
    "${superuser_psql[@]}" < "$sql"
    ;;
  stop)
    "$PG_BIN/pg_ctl" -D "$data" -m fast stop
    ;;
  destroy)
    if [[ -f "$data/postmaster.pid" ]]; then
      "$PG_BIN/pg_ctl" -D "$data" -m fast stop
    fi
    rm -rf "$data"
    log "deleted $data"
    ;;
  *)
    die "usage: $0 <rehearsal.conf> start|setup|stop|destroy"
    ;;
esac
