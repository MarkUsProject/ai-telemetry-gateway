# AI Telemetry Gateway on Staging

## Quick test

Spend a few cents from MarkUs. Then find that spend in two places: the ledger and the LiteLLM dashboard. The gateway works when both show the same call.

1. **Run feedback in MarkUs.**
   - Open `https://ben.teach.cs.toronto.edu/test250`.
   - Pick course 1, 2, or 3.
   - Open an assignment with an **ai** tester. Make one first with "Set up the tester" below.
   - Run the tests on one submission.
2. **Read the ledger.** The ledger is the `aitg.usage_logs` table. Run this on charlotte:

   ```bash
   ssh charlotte
   cd /data/ai-telemetry-gateway
   set -a; . ./secrets.env; set +a
   psql "$DATABASE_URL" --no-psqlrc -c "SELECT created_at, course_id, assignment_id, group_id, input_tokens, output_tokens, total_cost FROM aitg.usage_logs ORDER BY id DESC LIMIT 3;"
   ```

   The top row is your call. `total_cost` is in Canadian dollars. It is above 0. The ids match the MarkUs page.
3. **Open the dashboard.** The gateway listens on charlotte alone. Open a tunnel in a new terminal:

   ```bash
   ssh -L 4001:127.0.0.1:4001 charlotte
   ```

   Copy the master key to your clipboard. Run this on your Mac. The key stays off the screen:

   ```bash
   ssh charlotte "grep '^LITELLM_MASTER_KEY=' /data/ai-telemetry-gateway/secrets.env" | cut -d= -f2- | pbcopy
   ```

   Then visit `http://localhost:4001/ui`. Sign in with user `admin`. Paste the key as the password.
4. **Find the call.** Open **Logs**. Look for your call by its time. The model reads `gpt-4o`. The token counts match the ledger row. The dashboard shows US dollars, so its cost differs from the ledger.

The test passes when the ledger row exists, the dashboard shows the same call, and the tokens match.

## Set up the tester

Do this once per assignment.

1. As an admin, open the course settings. Click **Refresh autotest schema**.
2. As an instructor, open the assignment. Go to **Automated Tests**, then **Manage**.
3. Add a tester of type **ai**. Keep **AI Feedback library version** at `main`.
4. In the test group, set:
   - `model` to `openai-remote`
   - `model_name` to `gpt-4o`
   - `remote_url` to the staging gateway URL in the list
   - `scope` to `code` or `text`
   - `submission` to the file to read
5. Save.

`model_name` matters. The library default is `gpt-4o-mini`, which staging lacks.

## Guard checks

These checks prove the gateway stops bad calls. Run them on charlotte. Use one `ssh charlotte` session.

```bash
cd /data/ai-telemetry-gateway
set -a; . ./secrets.env; set +a
BASE=http://127.0.0.1:4001
aitg_psql() { psql "$DATABASE_URL" --no-psqlrc "$@"; }
```

### The service runs

```bash
ps -eo user,args | grep '[a]i-telemetry-gateway/venv'
curl -s -w ' [%{http_code}]\n' $BASE/health/liveliness
```

Expect lines that start with `autotst`. Expect `"I'm alive!" [200]`. A missing line means the service is down. Ask Dean to start it.

### The smoke test passes

`smoke-test.sh` runs six checks. One check makes a real `gpt-4o` call of 5 tokens or less.

```bash
cd src/deploy/polymouth
./smoke-test.sh staging.conf gpt-4o
cd /data/ai-telemetry-gateway
```

Expect six `PASS` lines, then `all checks passed`. The script cleans up after itself.

### A course switch stops a call

Switch course 3 off. Send a call. Switch it on again.

```bash
aitg_psql -c "UPDATE aitg.course_budgets SET is_active=FALSE WHERE instance='ben.teach.cs.toronto.edu' AND course_id=3;"
curl -s -X POST $BASE/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -H 'x-litellm-spend-logs-metadata: {"instance":"ben.teach.cs.toronto.edu","course_id":3,"assignment_id":1,"group_id":1,"batch_id":null,"category":"instructor"}' \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"Say hi."}],"max_tokens":5}'
```

Expect HTTP 400 with `Course AI features are disabled for course_id=3`.

Reset the switch. Run this line even when the check fails:

```bash
aitg_psql -c "UPDATE aitg.course_budgets SET is_active=TRUE WHERE instance='ben.teach.cs.toronto.edu' AND course_id=3;"
```

## When a check fails

Read the gateway log first:

```bash
tail -n 50 /data/ai-telemetry-gateway/logs/current
```

| Symptom | Cause | Fix |
|---|---|---|
| Missing `ps` line | The service stopped | Ask Dean to start it |
| 400 `Missing required MarkUs attribution` | The call lacks its labels | Use `model` `openai-remote`. Check the autotester runs PR #731. |
| 400 `max_tokens is required` | The call sends no reply limit | Use library version `main`. Use scope `code` or `text`. |
| 400 `Gateway temporarily unavailable` | The gateway lost the database | Run the `aitg_psql` line from "Guard checks" |
| 400 `Course budget exhausted` | The course spent its CAD 10.00 | Raise `max_budget` in `aitg.course_budgets` |
| `Invalid model name` on `qwen3:4b-instruct` or `gemma4:12b` | Staging lists `gpt-4o` only. Charlotte has no Ollama. | Use `gpt-4o` |
| Upstream 401 on `gpt-4o` | The UofT key is empty or wrong | Ask the gateway owner |
| Reply 200 with no ledger row | The write failed | Read `var/dead-letter.jsonl`. Each line is a lost row. Tell the gateway owner. |

## Staging facts

| Fact | Value |
|---|---|
| Host | `charlotte.teach.cs.toronto.edu` |
| Gateway | `http://127.0.0.1:4001` |
| Folder | `/data/ai-telemetry-gateway` |
| MarkUs site label | `ben.teach.cs.toronto.edu` |
| Funded courses | 1, 2, and 3, at CAD 10.00 each |
| Term budget | CAD 30.00 for `staging_2026` |

The values come from `deploy/polymouth/staging.conf`.
