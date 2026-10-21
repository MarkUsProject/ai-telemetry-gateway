# Deploying the gateway on polymouth

This folder installs the gateway on polymouth under daemontools, from a Python venv. Mateo runs every step, except 4 root steps that Dean runs from 2 emails.

The staging plan explains the reasoning: `Markus/.claude/plans/AI_Telemetry_Gateway_Staging_Plan.md`.

## What each file does

Each script takes a conf file as its first argument.

| File | Job |
|---|---|
| `staging.conf` | Holds the staging settings. Fill in every `CHANGE_ME` first. |
| `rehearsal.conf` | Swaps in a throwaway folder, a Postgres port (5434) and test budgets |
| `install.sh` | Builds the gateway into the folder the conf names. Safe to re-run. |
| `make-secrets.sh` | Writes `secrets.env` on the first run. Later runs keep it. |
| `make-db-setup.sh` | Writes Dean's database setup file, with both role passwords |
| `render.py` | Fills the SQL templates after checking each value |
| `templates/db-setup.sql.tmpl` | Shapes Dean's database setup |
| `templates/0*.sql.tmpl` | Shapes the staging budget seeds |
| `config/config.yaml` | Lists the paid model for staging |
| `config/config.rehearsal.yaml` | Adds a free mock model for the rehearsal |
| `service/` | Starts LiteLLM as `autotst` under daemontools |
| `trial-pg.sh` | Runs a disposable Postgres, so the rehearsal leaves the real database untouched |
| `db.sh` | Applies migrations or seeds, or lists applied migrations |
| `smoke-test.sh` | Runs user guide steps 2–5 with pass or fail output |
| `make-virtual-key.sh` | Mints the autotester's key |
| `lib.sh` | Loads and checks the conf for every script |

## Rehearsal (Phase 1, before Email 1)

The rehearsal passes when the smoke test prints `all checks passed`. It runs in `/data/otelcol`, which Mateo owns.

1. On your Mac, copy the committed branch to polymouth. The copy skips local edits and `.git`:
   ```
   git archive deploy/polymouth-staging | ssh polymouth 'umask 007 && mkdir /data/otelcol/aitg-src && tar -x -C /data/otelcol/aitg-src'
   ```
2. On polymouth, open the kit:
   ```
   cd /data/otelcol/aitg-src/deploy/polymouth
   ```
3. Build. Write the setup file:
   ```
   ./install.sh rehearsal.conf
   ./make-db-setup.sh rehearsal.conf
   ```
4. Start the throwaway database. Create the roles:
   ```
   ./trial-pg.sh rehearsal.conf start
   ./trial-pg.sh rehearsal.conf setup
   ```
5. Apply the schema and budgets:
   ```
   ./db.sh rehearsal.conf up
   ./db.sh rehearsal.conf seed
   ```
6. Start the gateway in a second terminal:
   ```
   cd /data/otelcol/aitg-rehearsal/service && ./run
   ```
7. Run the checks:
   ```
   ./smoke-test.sh rehearsal.conf gpt-4o-mini-mock
   ```
8. Stop the gateway with Ctrl-C.
9. Delete the rehearsal:
   ```
   ./trial-pg.sh rehearsal.conf destroy
   cd ~
   rm -rf /data/otelcol/aitg-rehearsal /data/otelcol/aitg-src
   ```

## Staging (Phases 0 to 3)

Staging repeats the rehearsal in Dean's folder, with real budgets and the real database.

1. Send Email 1. Dean creates `/data/ai-telemetry-gateway`.
2. On your Mac, copy the committed branch into Dean's folder:
   ```
   git archive deploy/polymouth-staging | ssh polymouth 'umask 007 && mkdir /data/ai-telemetry-gateway/src && tar -x -C /data/ai-telemetry-gateway/src'
   ```
3. On polymouth, type the UofT key at a silent prompt. Then build. The first build stores the key. Later builds keep `secrets.env`.
   ```
   cd /data/ai-telemetry-gateway/src/deploy/polymouth
   read -rs UOFT_OPENAI_KEY && export UOFT_OPENAI_KEY
   ./install.sh staging.conf
   ```
4. Write Dean's setup file:
   ```
   ./make-db-setup.sh staging.conf
   ```
5. Send Email 2. Dean:
   - creates the database
   - starts the service
   - adds the Apache routes
6. Delete the setup file. It holds both role passwords:
   ```
   rm /data/ai-telemetry-gateway/deploy/db-setup.sql
   ```
7. Apply the schema and budgets:
   ```
   ./db.sh staging.conf up
   ./db.sh staging.conf seed
   ```
8. Run the checks through Apache. Check 4 makes one real `gpt-4o` call of at most 5 output tokens.
   ```
   ./smoke-test.sh staging.conf gpt-4o https://polymouth.teach.cs.toronto.edu
   ```
9. Mint the autotester's key:
   ```
   ./make-virtual-key.sh staging.conf
   ```

## Where to look when something breaks

Start with the gateway's own log, then follow the symptom.

| Symptom | First check |
|---|---|
| A script stops at a setting | That setting in the conf file |
| The gateway exits at start | `logs/current`, or the terminal in the rehearsal |
| Prisma reports a missing engine | `var/home/.cache` holds the engine |
| Prisma reports a missing engine after a move | The folder sits where `install.sh` built it |
| Every call returns 502 through Apache | `svstat /service/ai-telemetry-gateway` shows the service up |
| `usage_logs` stays empty | `var/dead-letter.jsonl` holds the failed writes |
