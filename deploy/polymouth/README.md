# Deploying the gateway on polymouth

This folder installs the gateway on polymouth under daemontools, from a Python venv. Dean runs the 4 root steps from 2 emails. Mateo runs every other step.

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
| `render.py` | Checks each value, then fills the SQL templates |
| `templates/db-setup.sql.tmpl` | Shapes Dean's database setup |
| `templates/0*.sql.tmpl` | Shapes the staging seeds: the budgets, and the AI feature flag switched off |
| `config/config.yaml` | Lists the paid model for staging |
| `config/config.rehearsal.yaml` | Adds a free mock model for the rehearsal |
| `service/` | Starts LiteLLM as `autotst` under daemontools |
| `trial-pg.sh` | Runs a throwaway Postgres, so the rehearsal leaves the real database untouched |
| `db.sh` | Applies migrations or seeds, or lists applied migrations |
| `smoke-test.sh` | Runs user guide steps 2 to 5 and the AI feature flag check, with pass or fail output |
| `make-virtual-key.sh` | Mints the autotester's virtual key |
| `lib.sh` | Loads and checks the conf for every script |

## Rehearsal (Phase 1, before Email 1)

The rehearsal passes when the smoke test prints `all checks passed`. It runs in `/data/otelcol`, which Mateo owns.

1. On your Mac, copy the committed branch to polymouth. The copy skips local edits. It skips `.git` too:
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
5. Apply the schema. Seed the budgets:
   ```
   ./db.sh rehearsal.conf up
   ./db.sh rehearsal.conf seed
   ```
   The seed leaves the AI feature flag off. Turn it on, so the smoke test runs every check:
   ```
   ( source /data/otelcol/aitg-rehearsal/secrets.env && psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f ../../db/queries/enable_global.sql )
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
3. On polymouth, type the UofT key at a silent prompt. Then build. The first build stores the key in `secrets.env`. Later builds keep that file.
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
7. Apply the schema. Seed the budgets:
   ```
   ./db.sh staging.conf up
   ./db.sh staging.conf seed
   ```
8. Run the checks through Apache. The seed leaves the AI feature flag off, so this run makes no paid call. It checks the guards and the "disabled" refusal. It skips the checks that need a call to pass.
   ```
   ./smoke-test.sh staging.conf gpt-4o https://polymouth.teach.cs.toronto.edu
   ```
9. Mint the autotester's virtual key:
   ```
   ./make-virtual-key.sh staging.conf
   ```
10. The gateway still refuses every call: the AI feature flag is off. When you are ready for AI calls, turn it on:
    ```
    ( source /data/ai-telemetry-gateway/secrets.env && psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f ../../db/queries/enable_global.sql )
    ```
    `disable_global.sql` in the same folder turns it off again. Neither needs a restart.
11. Run the checks again, now in full. Check 5 makes one real `gpt-4o` call of at most 5 output tokens. With the flag on, the test turns it off for one call and back on, so real calls are refused for about a second. The test never turns the flag on by itself.
    ```
    ./smoke-test.sh staging.conf gpt-4o https://polymouth.teach.cs.toronto.edu
    ```

## Upgrading a running gateway

Follow this order. The schema must change before the new code starts.

The running service keeps its old code in memory until it restarts. New code that starts on an old schema refuses every call with "Gateway temporarily unavailable". So `db.sh up` comes before the restart.

1. On your Mac, replace the source copy on polymouth with the new branch:
   ```
   ssh polymouth 'rm -rf /data/ai-telemetry-gateway/src'
   git archive <branch> | ssh polymouth 'umask 007 && mkdir /data/ai-telemetry-gateway/src && tar -x -C /data/ai-telemetry-gateway/src'
   ```
2. On polymouth, rebuild. This copies the new code, migrations and seeds. It keeps `secrets.env`. The service still runs the old code.
   ```
   cd /data/ai-telemetry-gateway/src/deploy/polymouth
   ./install.sh staging.conf
   ```
3. Apply the new migrations. Then load the seeds. Both are safe to re-run. Rows that exist stay as they are.
   ```
   ./db.sh staging.conf up
   ./db.sh staging.conf seed
   ```
4. Check the AI feature flag. An upgrade that adds the flag seeds it off. To keep AI on, turn the flag on before the restart (step 10 of Staging).
5. Restart the service.
6. Run the checks:
   ```
   ./smoke-test.sh staging.conf gpt-4o https://polymouth.teach.cs.toronto.edu
   ```

## Where to look when something breaks

Start with the gateway's own log. Then find the symptom in the table.

| Symptom | First check |
|---|---|
| A script stops at a setting | That setting in the conf file |
| The gateway exits at start | `logs/current`, or the terminal in the rehearsal |
| Prisma reports a missing engine | `var/home/.cache` holds the engine |
| Prisma reports a missing engine after a move | The folder sits where `install.sh` built it |
| Every call returns 502 through Apache | `svstat /service/ai-telemetry-gateway` shows the service up |
| `usage_logs` stays empty | `var/dead-letter.jsonl` holds the failed writes |
| Every call returns 400 "AI features are currently disabled" | The AI feature flag is off. Step 10 turns it on. |
| Every call returns 400 "Gateway temporarily unavailable" after an upgrade | The service restarted before `db.sh up` ran. Run `./db.sh staging.conf up`. No second restart is needed. |
| The smoke test prints `SKIP` | The AI feature flag is off. Step 10 turns it on. |
