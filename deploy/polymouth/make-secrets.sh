#!/usr/bin/env bash
# Writes ${INSTALL_DIR}/secrets.env with freshly generated keys and passwords.
#
# Usage: make-secrets.sh <conf>
#
# Keeps an existing secrets.env: the database passwords inside it must keep
# matching the roles db-setup.sql created. To rotate, edit the file
# by hand and ALTER ROLE to match.
#
# The UofT key comes from the caller's environment, so it stays out of shell
# history and out of this repo:
#   read -rs UOFT_OPENAI_KEY && export UOFT_OPENAI_KEY && ./make-secrets.sh staging.conf
# Left unset, the file gets an empty value. Fill it in before the first real call.

source "$(dirname "$0")/lib.sh"
load_conf "${1:-}"

target="${INSTALL_DIR}/secrets.env"
if [[ -e "$target" ]]; then
  log "keeping existing $target"
  exit 0
fi

# Stdlib-only generators. A Fernet key is 32 random bytes, urlsafe base64.
hex() { python3 -c "import secrets; print(secrets.token_hex($1))"; }
fernet() { python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"; }

admin_pw="$(hex 32)"
app_pw="$(hex 32)"
db_base="${PG_HOST}:${PG_PORT}/${PG_DB}"

if [[ -z "${UOFT_OPENAI_KEY:-}" ]]; then
  log "UOFT_OPENAI_KEY is unset; gpt-4o calls fail until you add it to $target"
fi

umask 077
cat > "$target" <<EOF
# Secrets for the AI telemetry gateway. Generated $(date -u +%Y-%m-%dT%H:%M:%SZ).
# Readable by mateo and the ${SERVICE_USER} group. Keep it out of git.

# Callers authenticate with this, or with a virtual key minted from it.
LITELLM_MASTER_KEY=sk-$(hex 24)
# Encrypts provider keys at rest in LiteLLM's own tables.
LITELLM_SALT_KEY=$(hex 32)
# Encrypts provider keys at rest in aitg.api_keys (lib/encryption.py).
AITG_ENCRYPTION_KEY=$(fernet)

# Role passwords. render.py copies them into deploy/db-setup.sql.
AITG_ADMIN_PASSWORD=${admin_pw}
AITG_APP_PASSWORD=${app_pw}

# LiteLLM's Prisma client connects as the owner role. LiteLLM rewrites this
# variable at runtime, so the gateway modules read AITG_DATABASE_URL instead.
DATABASE_URL=postgresql://aitg_admin:${admin_pw}@${db_base}
AITG_DATABASE_URL=postgresql://aitg_app:${app_pw}@${db_base}

# UofT ITS AI gateway key, used by the gpt-4o model entry.
UOFT_OPENAI_KEY=${UOFT_OPENAI_KEY:-}
EOF

chgrp "$SERVICE_USER" "$target"
chmod 640 "$target"
log "wrote $target"
