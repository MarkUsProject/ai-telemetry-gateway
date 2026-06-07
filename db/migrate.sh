#!/usr/bin/env bash
# Applies SQL migrations in lexicographic order against the database
# identified by $DATABASE_URL (a libpq connection string).
#
# Usage:
#   DATABASE_URL=postgresql://user:pass@host:port/db ./migrate.sh up
#   DATABASE_URL=...                                 ./migrate.sh status
#   DATABASE_URL=...                                 ./migrate.sh seed
#
# Each .sql file in ./migrations/ is treated as one migration. Files are
# applied in filename order; we record applied filenames in the
# `schema_migrations` table so re-running is a no-op.
#
# Each migration runs in its own transaction. Any failure rolls back that
# file's changes and stops the run. There is no automatic "down" — schema
# rollback is done by writing a forward migration that reverses the change.
#
# Mirrors the autotester's "no formal tool, just SQL" approach. See
# docs/decision-record.md §"Migration tool".

set -euo pipefail
shopt -s nullglob

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIGRATIONS_DIR="${SCRIPT_DIR}/migrations"
SEED_DIR="${SCRIPT_DIR}/seed"

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "error: DATABASE_URL is required" >&2
  exit 1
fi

# psql resolves DATABASE_URL automatically when passed via the first arg.
PSQL=(psql "$DATABASE_URL" --quiet --no-psqlrc --set ON_ERROR_STOP=on)

ensure_tracking_table() {
  # The tracking table lives in the aitg schema (created by init-db.sql) so
  # it stays out of LiteLLM/Prisma's reach. Fully-qualified throughout this
  # script so search_path changes inside individual migration files don't
  # break tracking inserts.
  "${PSQL[@]}" <<'SQL'
CREATE TABLE IF NOT EXISTS aitg.schema_migrations (
  filename   VARCHAR(255) PRIMARY KEY,
  applied_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
SQL
}

is_applied() {
  local filename="$1"
  local count
  # Heredoc + :'filename' rather than --command, because psql variable
  # substitution does not run on -c (server-only parse).
  count=$("${PSQL[@]}" --tuples-only --no-align \
    --variable="filename=${filename}" <<-'SQL'
SELECT COUNT(*) FROM aitg.schema_migrations WHERE filename = :'filename';
SQL
  )
  [[ "$count" -gt 0 ]]
}

apply_one() {
  local file="$1"
  local filename
  filename=$(basename "$file")

  if is_applied "$filename"; then
    echo "  [skip] $filename (already applied)"
    return
  fi

  echo "  [apply] $filename"
  # One transaction: the migration plus the tracking insert. \i reads the
  # migration file; :'filename' is auto-quoted by psql so weird filenames
  # cannot break out of the SQL literal.
  "${PSQL[@]}" --single-transaction \
    --variable="filename=${filename}" \
    --variable="filepath=${file}" <<-'SQL'
\i :filepath
INSERT INTO aitg.schema_migrations (filename) VALUES (:'filename');
SQL
}

cmd_up() {
  ensure_tracking_table
  local applied=0
  local files=("${MIGRATIONS_DIR}"/*.sql)
  if [[ ${#files[@]} -eq 0 ]]; then
    echo "no migrations found in ${MIGRATIONS_DIR}"
    return
  fi
  for file in "${files[@]}"; do
    apply_one "$file"
    applied=$((applied + 1))
  done
  echo "done. processed ${applied} file(s)."
}

cmd_status() {
  ensure_tracking_table
  "${PSQL[@]}" <<'SQL'
SELECT filename, applied_at FROM aitg.schema_migrations ORDER BY filename;
SQL
}

cmd_seed() {
  local files=("${SEED_DIR}"/*.sql)
  if [[ ${#files[@]} -eq 0 ]]; then
    echo "no seed files in ${SEED_DIR}"
    return
  fi
  for file in "${files[@]}"; do
    echo "  [seed] $(basename "$file")"
    "${PSQL[@]}" --single-transaction --file="$file"
  done
}

case "${1:-up}" in
  up)     cmd_up ;;
  status) cmd_status ;;
  seed)   cmd_seed ;;
  *)      echo "usage: $0 {up|status|seed}" >&2; exit 1 ;;
esac
