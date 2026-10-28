# AI Grading Gateway: Overview, Concepts, and How to Test

This guide has three parts. Part 1 tells what the system does in plain words, free of tech detail. Part 2 tells the same story in a little more depth. Part 3 walks through how to set it up and test it. Every step in Part 3 matches the running stack.

---

## Part 1: What we built, in plain words

Every AI grading request from MarkUs passes through one gate. The gate records each request. It checks the budget. It refuses any request that lacks a label or breaks a rule.

### A walk through one request

Say a teacher in CSC108 asks for AI feedback on Bob's assignment.

1. MarkUs sends the request to the autotester, the helper that runs grading.
2. The autotester puts four labels on the request: which MarkUs site, which course, which assignment, which student group. It adds two more facts:
   - the batch number
   - who started it, a student or a teacher
3. The request reaches the gate.
4. The gate runs a checklist:
   - Are all four labels there?
   - Is the term still under its budget?
   - Is this course still under its budget?
   - Are the kill switches off?
   - Does the request say how long a reply can be?
5. If any check fails, the call stops. A message names the problem.
6. If all checks pass, the gate does three things:
   - It sends the request to OpenAI.
   - It reads the answer.
   - It writes one row to a logbook, with the cost in Canadian dollars.

### Examples of each guard

Each guard sends back its own message.

| What happens | What the teacher sees |
|---|---|
| No labels | "Missing required MarkUs attribution: instance, course_id, assignment_id, group_id." |
| No reply-size limit | "max_tokens is required." |
| A student asks for a 99,999-token reply | "max_tokens=99999 exceeds the gateway ceiling of 4096." |
| CSC108 has spent $1.50 of a $1.00 budget | "Course budget exhausted for course_id=12: spent CAD 1.50 of CAD 1.00." |
| The term ran out of money | "Global term budget exhausted." |
| A teacher flips the course kill switch | "Course AI features are disabled." |
| The database is down | "Gateway temporarily unavailable." |

### What gets saved per call

| Field | Meaning |
|---|---|
| MarkUs site, course, assignment, group | Which work the AI graded |
| Batch | The group of students graded together |
| Role | Student or teacher |
| Tokens | Pieces of text the AI counts as it reads and writes |
| Price in Canadian dollars | What the call cost, turned from US dollars at today's Bank of Canada rate |
| Time | When the call happened |

### What never happens

- A call goes to OpenAI without a label.
- A logbook row appears with a blank cost.
- A call passes when the budget is empty.
- The system keeps going through an outage.

---

## Part 2: How it works, with a little more detail

### Four projects play a part

| Project | Role |
|---|---|
| MarkUs | The grading website. Teachers and students log in here. |
| Autotester | A helper service. It runs grading jobs from MarkUs. |
| AI feedback library | A small program the autotester runs to talk to OpenAI. |
| Gateway (LiteLLM + Postgres) | The new gate. It records every call. It enforces budgets. |

### The gate has three checkpoints, in order

1. **Attribution guard.** Reads the four labels off the request. Stops any call that lacks one.
2. **Gatekeeper.** Runs the steps below. Any failure stops the call.
   - It looks up today's term budget.
   - It sums the slot counters.
   - It looks up this course's budget.
   - It looks up this course's kill switch.
   - It checks the reply-size rule.
3. **Telemetry adapter.** Runs after the call returns. It does two things:
   - It writes one row to `usage_logs`.
   - It adds the cost to a random "slot". With slots, many workers can record at once without slowing each other down.

### The logbook

The logbook holds one row per paid call. Each row holds:

- the four labels
- the batch
- the role
- every token count
- the per-token price
- the total cost in Canadian dollars
- the time
- a unique provider-side request id

If the same id arrives twice, the database refuses the second copy.

### The cost math

OpenAI quotes the cost in US dollars. The gate turns it into Canadian dollars:

- The gate fetches today's USD-to-CAD rate from the Bank of Canada. The U of T finance office uses the same rate.
- The gate multiplies the cost by that rate.
- The rate refreshes once a day.
- If the gate fails to reach the Bank, it reuses the last rate it had.
- On a cold start without network access, a fallback rate of 1.36 keeps the system from losing a row.

The total cost is `(input tokens + output tokens) × per-token price`. The row records cached and reasoning tokens. The cost leaves them out.

### When the database is down

Every row survives the outage. A row that fails to land in the database goes into a small file instead, called a "dead letter". When the database returns, a separate drainer reads that file. It replays the rows. The unique request id rule stops duplicates.

### How the slots work

The term total is the sum of ten rows, called "slots", for the active term. Every paid call adds its cost to one random slot. Ten workers can write at once without any two picking the same row. When a new term begins, writes start landing in the new term's ten slots. Old rows stay as history.

### Budgets and kill switches

| Setting | Who controls it | What it does |
|---|---|---|
| `global_budget_periods.max_budget` | Admin | The cap for the term, in Canadian dollars |
| `global_budget_periods.is_active` | Admin | Whole-term kill switch |
| `course_budgets.max_budget` | Admin | The cap per course |
| `course_budgets.alert_threshold` | Admin | When to warn the teacher |
| `course_budgets.is_active` | Admin | Per-course kill switch |
| `api_keys.is_active` | Admin | Kill switch for the upstream OpenAI key |

The alert fires once per crossing. The first time a course passes its threshold, the system stamps a column. Later calls see the stamp and stay quiet. A new term requires an admin to clear the stamp.

### Reply-size limit

Every call must say how many output tokens it allows (`max_tokens`). The gate rejects a call that leaves the value out. It rejects a value above its ceiling, which is 4096 by default. This stops a runaway prompt from burning the term's budget on one call.

### Fail-closed everywhere

Every layer halts on failure. It never passes the call through.

- If the database stops responding, the gate returns "Gateway temporarily unavailable" until the database comes back.
- Without an active term, the call stops.
- If someone flips a kill switch, the call stops.

A call never sneaks through on "best effort."

---

## Part 3: How to set it up and test it

Every command and route in this part matches the running stack and the source repos.

### Before you start

Confirm each part below before Step 1.

| Component | Folder on disk | How to confirm |
|---|---|---|
| The gateway stack | `~/work/ai-telemetry-gateway/local-stack` | `docker compose ps` shows `aitg-litellm` and `aitg-postgres` healthy |
| The autotester | `~/work/autotesting` | `master` at `6aaa0a6` or later, with #731 and #761 (`git log --oneline master \| grep -E '#731\|#761'`) |
| The AI feedback library | `~/work/autograding-feedback-py` | `main` has `ai_feedback/models/OpenAIRemoteModel.py` |
| MarkUs | `~/work/Markus` | `master`. The autotester reads attribution from the `file_url` MarkUs already sends. |

### Step 1: Start the gateway

```bash
cd ~/work/ai-telemetry-gateway/local-stack
docker compose up -d
docker compose ps
```

Expected:

```
NAME            STATUS                 PORTS
aitg-litellm    Up (healthy)           127.0.0.1:4000->4000/tcp
aitg-postgres   Up (healthy)           127.0.0.1:5434->5432/tcp
```

Apply the migrations, then the seed. Both are idempotent, so a second run is safe:

```bash
docker compose run --rm migrate up
docker compose run --rm migrate seed
```

### Step 2: Confirm the gate rejects unlabeled calls

This step proves the stack has the attribution guard and the gatekeeper wired in. It never touches MarkUs.

```bash
KEY=$(grep ^LITELLM_MASTER_KEY= .env | cut -d= -f2-)

curl -s -X POST http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}],"max_tokens":5}'
```

Expected response (HTTP 400):

```
{"error":{"message":"Missing required MarkUs attribution: instance, course_id, assignment_id, group_id. Every gateway call must send instance, course_id, assignment_id and group_id as JSON in the x-litellm-spend-logs-metadata header.","type":"None","param":"None","code":"400"}}
```

### Step 3: Confirm the gate enforces `max_tokens`

```bash
curl -s -X POST http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -H 'x-litellm-spend-logs-metadata: {"instance":"markus.example.edu","course_id":12,"assignment_id":34,"group_id":56,"batch_id":null,"category":"student"}' \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}],"max_tokens":99999}'
```

Expected (HTTP 400):

```
{"error":{"message":"max_tokens=99999 exceeds the gateway ceiling of 4096."}}
```

### Step 4: Confirm the gate accepts a fully labeled call

```bash
curl -s -X POST http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -H 'x-litellm-spend-logs-metadata: {"instance":"markus.example.edu","course_id":12,"assignment_id":34,"group_id":56,"batch_id":null,"category":"student"}' \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}],"max_tokens":5}'
```

This call returns **HTTP 200 with a canned reply. It never contacts OpenAI.** The shipped local config gives `gpt-4o-mini` a `mock_response` (see `litellm-config.yaml`). The call still writes a row to `usage_logs`, because the success hook fires on mock responses too. That row proves the gate accepted and forwarded the labeled call.

To test a *real* upstream call, use a model without a mock (`gpt-4o`). With a placeholder `OPENAI_API_KEY`, it returns HTTP 401 from OpenAI. The 401 shows the gate accepted and forwarded the call. For a real reply:

1. Set a real `OPENAI_API_KEY` in `.env`.
2. Recreate the litellm container.
3. Send the same call. It returns HTTP 200. It writes a row with the real cost.

### Step 5: Drive each budget path with the database

These commands change live state. Run the reset lines when you finish.

```bash
USER=$(grep ^POSTGRES_USER= .env | cut -d= -f2-)
PASS=$(grep ^POSTGRES_PASSWORD= .env | cut -d= -f2-)
PSQL="PGPASSWORD=$PASS psql -h 127.0.0.1 -p 5434 -U $USER -d aitg -q"

# Course spend is SUM(total_cost) over usage_logs for that (instance, course_id)
# in the active period — so to exhaust the cap you must have spend on record. Insert a
# $2.00 spend row, then drop the cap below it.
eval "$PSQL -c \"INSERT INTO aitg.usage_logs (provider_request_id, api_key_id, instance, course_id, assignment_id, group_id, total_cost) VALUES ('manual-budget-test', (SELECT id FROM aitg.api_keys LIMIT 1), 'markus.example.edu', 12, 34, 56, 2.00);\""
eval "$PSQL -c \"UPDATE aitg.course_budgets SET max_budget=1.00 WHERE instance='markus.example.edu' AND course_id=12;\""
# Now the same Step 4 curl returns 400: "Course budget exhausted for course_id=12: spent CAD 2.00 of CAD 1.00."

# Reset (delete the test spend row and restore the cap).
eval "$PSQL -c \"DELETE FROM aitg.usage_logs WHERE provider_request_id='manual-budget-test';\""
eval "$PSQL -c \"UPDATE aitg.course_budgets SET max_budget=100.00, is_active=TRUE, alert_sent_at=NULL WHERE instance='markus.example.edu' AND course_id=12;\""

# Flip the course kill switch.
eval "$PSQL -c \"UPDATE aitg.course_budgets SET is_active=FALSE WHERE instance='markus.example.edu' AND course_id=12;\""
# Now: 400 "Course AI features are disabled for course_id=12 (is_active = false)."

# Reset.
eval "$PSQL -c \"UPDATE aitg.course_budgets SET is_active=TRUE WHERE instance='markus.example.edu' AND course_id=12;\""

# Flip the term kill switch.
eval "$PSQL -c \"UPDATE aitg.global_budget_periods SET is_active=FALSE;\""
# Now: 400 "No active budget period covers the current time."

# Reset.
eval "$PSQL -c \"UPDATE aitg.global_budget_periods SET is_active=TRUE;\""
```

### Step 6: Point the autotester at the gateway

Edit `~/work/autotesting/server/autotest_server/settings.yml`:

```yaml
default_remote_url: https://polymouth.teach.cs.toronto.edu:443/chat
remote_url_whitelist:
  - https://polymouth.teach.cs.toronto.edu:443/chat
  - http://localhost:4000/v1            # the LiteLLM gateway
```

Restart the autotester. Then call MarkUs's "refresh autotest schema" (Step 7). That makes the new URL show up as a choice for assignments.

### Step 7: Set up MarkUs and its connection to the autotester

Running MarkUs locally takes a full Rails environment setup: `bundle install`, Postgres, Redis, and an asset build. Once MarkUs runs:

1. Log in as an admin.
2. Open course settings.
3. Set the autotester URL. Click **Test connection**. The Rails route is `GET /admin/courses/:id/test_autotest_connection`.
4. Click **Refresh autotest schema**. The Rails route is `POST /admin/courses/:id/refresh_autotest_schema`.

### Step 8: Configure an assignment to use AI feedback through the gateway

1. As an instructor, open the assignment.
2. Go to **Automated Tests → Manage**. The Rails route is `GET /courses/:course_id/assignments/:assignment_id/automated_tests/manage`.
3. Add a tester of type **ai**.
4. Leave **AI Feedback library version** at `main`.
   - When the settings save, the autotester builds the tester's virtualenv. It installs `git+https://github.com/MarkUsProject/ai-autograding-feedback.git@<that value>`.
   - `main` has the `openai-remote` provider. It reads `GATEWAY_API_KEY`.
   - The old `ai-telemetry-gateway-connection` branch reads `LITELLM_API_KEY`. Runs on that branch fail with a missing key.
5. In the test group `config`, set:
   - `model`: `openai-remote`
   - `remote_url`: the gateway URL from your whitelist, for example `http://localhost:4000/v1`
   - `prompt`, `submission`: the usual AI feedback library choices.
   - `scope`: `code` or `text`. The image path leaves out `max_tokens` for now, so the gateway rejects `scope: image` with 400 "max_tokens is required".
   - `output`: one of `overall_comment`, `annotations`, `message`.
6. Save.

> **What `model: openai-remote` is.** It is a *provider key* in the AI feedback library's `ModelFactory`, as opposed to an upstream model name. On `main`, `ai_feedback/models/__init__.py` registers it as `"openai-remote": OpenAIRemoteModel`.
>
> `OpenAIRemoteModel` subclasses `OpenAIModel`, with three changes:
>
> - It points the OpenAI client at the LiteLLM gateway (`remote_url`) in place of `api.openai.com`.
> - It authenticates with the LiteLLM virtual key as `Authorization: Bearer`.
> - It forwards the `x-litellm-spend-logs-metadata` attribution header.
>
> The actual upstream model (e.g. `gpt-4o-mini`) is a separate value. The gateway resolves it from its own `model_list` in `litellm-config.yaml`.
>
> Compare `model: remote`, or `RemoteModel`. It targets the `markus-ai-server` "polymouth" proxy. It sends a custom payload with an `X-API-KEY` header.

> **Authentication.** The tester process must see `GATEWAY_API_KEY`. That is the LiteLLM virtual key the gateway accepts as `Authorization: Bearer`.
>
> - For local testing, the master key in `local-stack/.env` works.
> - Put the key in the autotester server's `settings.local.yml`, under `ai_tester_env`. It sits next to `REMOTE_API_KEY` for polymouth.
> - The server hands those keys to the AI tester alone.
> - Keep the file at mode 600. Then restart the autotest server.
> - Without the key, every run errors with `The AI tester is missing GATEWAY_API_KEY`.
> - To make that key, see "Make a key for callers" in `local-stack/README.md`.

> **Reply-size limit.** The gateway rejects any call that leaves out `max_tokens` (see Part 2). The autotester's tester config lacks a field for model options. To fill that gap, `OpenAIRemoteModel` sends `max_tokens=1024` on its own whenever the caller leaves the value unset. The gateway ceiling is 4096.

The autotester schema field for `remote_url` lists the URLs in your whitelist as its drop-down choices. That field lives in `~/work/autotesting/server/autotest_server/testers/ai/settings_schema.json`.

### Step 9: Run a test and watch the row land

1. As an instructor or student, start a test run on a submission.
2. While the run goes, tail the gateway logs:

   ```bash
   docker logs -f aitg-litellm
   ```

3. After the run, query the logbook:

   ```bash
   eval "$PSQL -c \"SELECT provider_request_id, instance, course_id, total_cost, created_at FROM aitg.usage_logs ORDER BY id DESC LIMIT 5;\""
   ```

   A row appears. It holds the four labels and the CAD cost.

   With caching on, a re-run of the same test on the same submission adds zero new rows. LiteLLM replays the saved reply. The call costs zero. The ledger stays the same. The log says `served from cache` each time. This stack ships with caching off. Every re-run bills and logs.

### Step 10: Drive the alert path

Set the alert threshold low, so the next call crosses it:

```bash
eval "$PSQL -c \"UPDATE aitg.course_budgets SET alert_threshold=0.01 WHERE instance='markus.example.edu' AND course_id=12;\""
```

Run another test. Check `course_budgets.alert_sent_at`:

```bash
eval "$PSQL -c \"SELECT instance, course_id, alert_sent_at FROM aitg.course_budgets;\""
```

The column now holds the stamp. The next call skips the alert. The planned next piece wires the actual instructor email to MarkUs's `NotificationMailer` (decision-record §"Alert delivery").

### A quick check without MarkUs

Steps 2 through 5 cover the gate end-to-end. Standing up MarkUs end-to-end is a long setup. If that is too much for one sitting, use those steps. The repo's automated checks cover every code path:

```bash
cd ~/work/ai-telemetry-gateway
python -m venv .venv && .venv/bin/python -m ensurepip --upgrade
.venv/bin/python -m pip install -r requirements.txt
USER=$(grep ^POSTGRES_USER= local-stack/.env | cut -d= -f2-)
PASS=$(grep ^POSTGRES_PASSWORD= local-stack/.env | cut -d= -f2-)
KEY=$(grep ^AITG_ENCRYPTION_KEY= local-stack/.env | cut -d= -f2-)
URL="postgresql://${USER}:${PASS}@127.0.0.1:5434/aitg"
AITG_TEST_DATABASE_URL="$URL" AITG_ENCRYPTION_KEY="$KEY" \
  .venv/bin/python -m pytest tests/ -q
```

The verified count today:

- 75 pass against the live database.
- Without it, 62 pass. The other 13 skip.

The tests cover:

- the schema
- role privileges
- encryption
- the attribution guard
- the telemetry adapter
- the gatekeeper
- the CAD FX cascade
- the dead-letter drain
- the three health-check queries

### Where to look when something goes wrong

| Symptom | First place to look |
|---|---|
| `argument --model: invalid choice: 'openai-remote'` | The tester virtualenv installed `ai_feedback` from a ref that lacks the provider. In the tester settings, set **AI Feedback library version** to `main`. Save again so the virtualenv rebuilds. |
| `The AI tester is missing GATEWAY_API_KEY` | Put the LiteLLM virtual key in the autotester server's `settings.local.yml`, under `ai_tester_env`. Then restart the autotest server. |
| `LITELLM_API_KEY is not set` | The tester virtualenv runs the old `ai-telemetry-gateway-connection` branch. Set **AI Feedback library version** to `main`. Save again. |
| 400 "max_tokens is required" | The tester virtualenv holds an `ai_feedback` build from before `OpenAIRemoteModel` set a default `max_tokens`. Rebuild it from `main` (step 8). |
| Every call returns 400 "Missing required MarkUs attribution" | The autotester leaves out the `x-litellm-spend-logs-metadata` header. Check that the autotester runs `master` with #731. Check that `ai_feedback` runs `main`. Check that `model: openai-remote` is set. |
| Every call returns 400 "Gateway temporarily unavailable" | The litellm container fails to reach the database. `docker compose logs litellm` shows the exception. |
| Zero new rows after re-running the same test | Check `docker logs aitg-litellm`. A line with `served from cache ... the ledger is correct` means LiteLLM replayed the reply at zero cost. The missing row is correct. A line with `reusing request ids ... under-reporting spend` points to a real fault. |
| Costs read as 0 | `OPENAI_API_KEY` is unset or fake. OpenAI returned 401. The success hook did not fire. |
| Alert email never arrives | The gatekeeper stamps `alert_sent_at`. The MarkUs mailer wiring is the next step. |
