# Local Stack: LiteLLM + Postgres

Run the LiteLLM proxy and PostgreSQL on your machine.

## Topology

```
  Your machine
  ┌────────────────────────────────────────────────┐
  │                                                │
  │   localhost:4000 ─► [ LiteLLM container ]      │
  │                          │                     │
  │                          ▼                     │
  │   localhost:5434 ─► [ Postgres container ]     │
  │                                                │
  └────────────────────────────────────────────────┘
```

Both containers sit on an isolated bridge network, `aitg-net`. They reach each
other by container name. The host ports bind to `127.0.0.1`, so your machine
alone can reach the stack.

## First-time setup

```bash
cp .env.example .env
# Edit .env and fill in: POSTGRES_PASSWORD, LITELLM_MASTER_KEY, LITELLM_SALT_KEY.
# Generate each with: openssl rand -hex 32   (master key gets `sk-` prefix)

docker compose up -d
docker compose ps        # both services should be "healthy"
```

On first start, LiteLLM runs `prisma migrate deploy`. That step creates
LiteLLM's own tables in the `public` schema.

Our 5 tables live in their own schema, `aitg`. See `init-db.sql` for that
schema. A separate migration flow creates our tables. See "Apply the schema"
below.

## Apply the schema

```bash
docker compose run --rm migrate up       # applies db/migrations/*.sql
docker compose run --rm migrate seed     # loads db/seed/*.sql (idempotent)
```

After these commands run, the `aitg` schema holds the five tables:

- `api_keys`
- `global_budget_periods`
- `course_budgets`
- `budget_slots`
- `usage_logs`

It holds one more: the `schema_migrations` tracking table.

## Smoke test

Run these checks once both services are healthy:

```bash
# 1. Liveliness probe.
curl -sS http://localhost:4000/health/liveliness
# expect: {"status":"healthy"}

# 2. Confirm the master key authenticates.
curl -sS http://localhost:4000/health \
  -H "Authorization: Bearer $(grep ^LITELLM_MASTER_KEY .env | cut -d= -f2)"

# 3. (Requires OPENAI_API_KEY in .env) Round-trip a chat completion.
curl -sS http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $(grep ^LITELLM_MASTER_KEY .env | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hello"}]}'
```

For the admin UI, open `http://localhost:4000/ui`. Sign in with the master key.

## Make a key for callers

Every call to the proxy carries a key. Use the master key for local tests. Make
a virtual key for anything you share.

**The master key** is the admin key. It sits in your `.env` as
`LITELLM_MASTER_KEY`. It works on every call. Keep it private.

**A virtual key** is a limited key you make from the master key. Give this one to
the autotester. Scope it to a few models. Cap its spend. Set it to expire. If a
virtual key leaks, turn it off on its own. The master key stays safe.

Make a virtual key two ways: one command, or the web page.

### Option 1: one command

Ask the proxy for a key. Prove you are the admin with the master key.

```bash
MASTER=$(grep ^LITELLM_MASTER_KEY .env | cut -d= -f2-)

curl -sS http://localhost:4000/key/generate \
  -H "Authorization: Bearer $MASTER" \
  -H "Content-Type: application/json" \
  -d '{"models":["gpt-4o-mini","gpt-4o"],"max_budget":50,"duration":"30d","metadata":{"purpose":"autotester"}}'
```

The reply is JSON. Your new key is the `key` field. It starts with `sk-`.

```json
{"key":"sk-abc123XYZ...","models":["gpt-4o-mini","gpt-4o"],"max_budget":50}
```

Copy the key now. The full key shows once.

| Field | What it controls |
|---|---|
| `models` | The models this key may call. Match the names in `litellm-config.yaml`. |
| `max_budget` | A spend cap in US dollars for this key. Leave it out for an uncapped key. |
| `duration` | How long the key lives, such as `30d` or `24h`. Leave it out for a key that never expires. |
| `metadata` | Free notes that tell your keys apart. |

The `max_budget` field caps this one key inside LiteLLM. The gateway's course and
term budgets live in Postgres. They apply on top. A call stops at whichever cap
it hits first.

### Option 2: the web page

Make a key by clicking, instead of typing a command.

1. Open `http://localhost:4000/ui`.
2. Sign in with the master key.
3. Open the **Keys** page.
4. Click **Generate Key**.
5. Pick the models the key may call.
6. Set a budget if you want one.
7. Submit the form.
8. Copy the key. It starts with `sk-`. The full key shows once.

### Give the key to the autotester

Put the key in the autotester server's `settings.local.yml`, under
`ai_tester_env`:

```yaml
ai_tester_env:
  GATEWAY_API_KEY: "sk-abc123XYZ..."
```

Keep that file at mode 600. The server refuses to start if the file's group or
other users can read it. Restart the autotest server to load the key.

The server hands the key to the AI tester alone. The tester sends it as
`Authorization: Bearer`. If the key is missing, every run fails with `The AI tester is
missing GATEWAY_API_KEY`.

## Stopping

```bash
docker compose down            # stop containers, keep the database
docker compose down -v         # stop and wipe the database (named volume)
```

## Assumptions baked into this local stack

| Topic | Local assumption | Production resolution |
|---|---|---|
| Host | Docker on a developer's machine | A VM or container host that UofT manages |
| Secret transport | `.env` file, gitignored | The sysadmin's secret store (Vault, K8s Secret, systemd EnvironmentFile, …) |
| Network exposure | Bound to `127.0.0.1` alone | A private subnet. The admin UI sits behind a VPN or jump host. |
| Postgres version | 18.4-alpine, pinned | The same major version. The patch level follows sysadmin policy. |
| TLS to Postgres | None. Traffic stays on loopback. | Required. The sysadmin manages it. |
| Backup | None. Data lives in a named volume. | The sysadmin's existing cron or snapshot setup |
| Role separation | Admin and app roles (see `init-db.sql`) | The same model. Passwords come from the secret store. |

If the sysadmin overrides any of these, update this table. Then update the
matching inline comment in `docker-compose.yml`.

## What this stack runs

- The LiteLLM proxy. It loads three custom hooks from `../lib/`. See
  `litellm-config.yaml` for the wire-up.
  - The attribution guard
  - The gatekeeper
  - The telemetry adapter
- PostgreSQL. It holds two groups of tables:
  - The five aitg tables, in the `aitg` schema
  - LiteLLM's own Prisma tables, in `public`
- A migration runner service (`docker compose run --rm migrate ...`).
- An on-demand pytest runner (`docker compose --profile tests run --rm tests`).

See `../docs/USER_GUIDE.md` for the full operator and tester walkthrough.

## Troubleshooting

**LiteLLM container restart-loops.** Check the logs:
```bash
docker compose logs litellm | tail -50
```
On a local stack, the two most common causes are:

- A missing `LITELLM_MASTER_KEY`, which must start with `sk-`.
- The Postgres container failing its healthcheck.

**Postgres healthcheck stays "starting".** First-boot migration can take 10 to 20
seconds on cold disks. Wait, then re-check `docker compose ps`.

**Port already in use.** Edit `docker-compose.yml` to change the host port
binding. That binding is the left side of `127.0.0.1:5434:5432` or
`127.0.0.1:4000:4000`.

**Edits to `lib/*.py` require clearing the bytecode cache.** After an edit,
`grep` on the container path shows the new source. The running module still
returns the old value. Docker Desktop reports a stale mtime for single-file bind
mounts, so Python keeps loading the cached bytecode. Clear the cache, then
recreate the container:
```bash
docker exec aitg-litellm rm -rf /app/__pycache__
docker compose up -d litellm
```
The `docker compose run --rm tests` path stays current. It sets
`PYTHONDONTWRITEBYTECODE`.
