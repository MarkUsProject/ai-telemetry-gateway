# Local Stack — LiteLLM + Postgres

Spin up the LiteLLM proxy and PostgreSQL on your machine.

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

Both containers sit on an isolated bridge network (`aitg-net`). They reach
each other by container name. Host ports are bound to `127.0.0.1` so nothing
in the stack is reachable from outside your machine.

## First-time setup

```bash
cp .env.example .env
# Edit .env and fill in: POSTGRES_PASSWORD, LITELLM_MASTER_KEY, LITELLM_SALT_KEY.
# Generate each with: openssl rand -hex 32   (master key gets `sk-` prefix)

docker compose up -d
docker compose ps        # both services should be "healthy"
```

LiteLLM's `prisma migrate deploy` runs on first start and creates its own
tables in the `public` schema. Our 5 tables live in the dedicated
`aitg` schema (see `init-db.sql`) and are created by a separate migration
flow — see "Apply the schema" below.

## Apply the schema

```bash
docker compose run --rm migrate up       # applies db/migrations/*.sql
docker compose run --rm migrate seed     # loads db/seed/*.sql (idempotent)
```

After this runs, the `aitg` schema contains the five tables (`api_keys`,
`global_budget_periods`, `course_budgets`, `budget_slots`, `usage_logs`)
plus the `schema_migrations` tracking table.

## Smoke test

Once both services are healthy:

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

Admin UI: open `http://localhost:4000/ui` and sign in with the master key.

## Make a key for callers

Every call to the proxy carries a key. Use the master key for local tests. Make
a virtual key for anything you share.

**The master key** is the admin key. It sits in your `.env` as
`LITELLM_MASTER_KEY`. It works on every call. Keep it private.

**A virtual key** is a limited key you make from the master key. Give this one to
the autotester. Scope it to a few models. Cap its spend. Set it to expire. Turn
off a leaked virtual key on its own, and the master key stays safe.

Make a virtual key two ways: one command, or the web page.

### Option 1 — one command

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
| `max_budget` | A spend cap in US dollars for this key. Omit it for no cap. |
| `duration` | How long the key lives, such as `30d` or `24h`. Omit it for no expiry. |
| `metadata` | Free notes that tell your keys apart. |

The `max_budget` field caps this one key inside LiteLLM. The gateway's course and
term budgets live in Postgres and apply on top. A call stops at whichever cap it
hits first.

### Option 2 — the web page

Make a key by clicking, with no command.

1. Open `http://localhost:4000/ui`.
2. Sign in with the master key.
3. Open the **Keys** page.
4. Click **Generate Key**.
5. Pick the models the key may call.
6. Set a budget if you want one.
7. Submit the form.
8. Copy the key. It starts with `sk-`. The full key shows once.

### Give the key to the autotester

Put the key in the autotester's `.env`:

```bash
LITELLM_API_KEY=sk-abc123XYZ...
```

The AI tester reads it with `load_dotenv()` and sends it as
`Authorization: Bearer`. Without it, every run fails with `LITELLM_API_KEY is
not set`.

## Stopping

```bash
docker compose down            # stop containers, keep the database
docker compose down -v         # stop and wipe the database (named volume)
```

## Assumptions baked into this local stack

| Topic | Local assumption | Production resolution |
|---|---|---|
| Host | Developer Docker | UofT-managed VM or container host |
| Secret transport | `.env` file, gitignored | Sysadmin's secret store (Vault, K8s Secret, systemd EnvironmentFile, …) |
| Network exposure | `127.0.0.1` only | Private subnet; admin UI behind VPN / jump host |
| Postgres version | 18.4-alpine (pinned) | Same major version, patch follows sysadmin policy |
| TLS to Postgres | none (loopback) | required; managed by sysadmin |
| Backup | none (named volume) | Sysadmin's existing cron / snapshot mechanism |
| Role separation | Admin and app roles (see `init-db.sql`) | Same model; passwords from secret store |

If sysadmin overrides any of these, update the table here and the relevant
inline comment in `docker-compose.yml`.

## What this stack runs

- LiteLLM proxy with three custom hooks loaded from `../lib/`: the
  attribution guard, the gatekeeper, and the telemetry adapter (see
  `litellm-config.yaml` for the wire-up).
- PostgreSQL with the five aitg tables in the `aitg` schema and LiteLLM's
  own Prisma tables in `public`.
- A migration runner service (`docker compose run --rm migrate ...`).
- An on-demand pytest runner (`docker compose --profile tests run --rm tests`).

For the full operator and tester walkthrough, see `../docs/USER_GUIDE.md`.

## Troubleshooting

**LiteLLM container restart-loops.** Check the logs:
```bash
docker compose logs litellm | tail -50
```
The two most common causes locally are a missing `LITELLM_MASTER_KEY` (must
start with `sk-`) and the Postgres container failing its healthcheck.

**Postgres healthcheck stays "starting".** First-boot migration can take 10-20
seconds on cold disks. Wait, then re-check `docker compose ps`.

**Port already in use.** Edit `docker-compose.yml` to change the host port
binding (left side of `127.0.0.1:5434:5432` or `127.0.0.1:4000:4000`).
