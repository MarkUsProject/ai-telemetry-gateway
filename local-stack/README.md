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
