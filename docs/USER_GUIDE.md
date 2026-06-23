# AI Grading Gateway — Overview, Concepts, and How to Test

This guide has three parts. Part 1 explains what the system does with no technical detail. Part 2 explains the same thing with a little more depth. Part 3 walks through setting it up and testing it. Every step in Part 3 was checked against the running stack before being written.

---

## Part 1 — What we built, in plain words

Every AI grading request from MarkUs passes through one gate. The gate records the request, checks the budget, and refuses requests that miss a label or break a rule.

### A walk through one request

Imagine a teacher in CSC108 asks for AI feedback on Bob's assignment.

1. MarkUs sends the request to the autotester (the helper that runs grading).
2. The autotester attaches four labels to the request: which MarkUs site, which course, which assignment, which student group. It also adds the batch number and whether a student or a teacher started it.
3. The request reaches the gate.
4. The gate runs a checklist:
   - Are all four labels there?
   - Is the term still under its budget?
   - Is this course still under its budget?
   - Are the kill switches off?
   - Does the request say how long a reply can be?
5. If any check fails, the call stops with a message that names the problem.
6. If all checks pass, the gate sends the request to OpenAI, reads the answer, and writes one row to a logbook with the cost in Canadian dollars.

### Examples of each guard

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
| Tokens | Pieces of text the AI counts when it reads and writes |
| Price in Canadian dollars | What the call cost, converted from US dollars at today's Bank of Canada rate |
| Time | When the call happened |

### What never happens

- A call goes to OpenAI without a label.
- A logbook row appears with no cost.
- A call passes when the budget is empty.
- The system keeps going through an outage.

---

## Part 2 — How it works, with a little more detail

### Four projects play a part

| Project | Role |
|---|---|
| MarkUs | The grading website. Teachers and students log in here. |
| Autotester | A helper service that runs grading jobs from MarkUs. |
| AI feedback library | A small program the autotester runs to talk to OpenAI. |
| Gateway (LiteLLM + Postgres) | The new gate. Records every call and enforces budgets. |

### The gate has three checkpoints, in order

1. **Attribution guard.** Reads the four labels off the request. Stops any call missing one.
2. **Gatekeeper.** Looks up today's term budget, sums the slot counters, looks up this course's budget and its kill switch, checks the reply-size rule. Stops the call on any failure.
3. **Telemetry adapter.** Runs after the call returns. Writes one row to `usage_logs` and adds the cost to a random "slot" so many workers can record at once without slowing each other down.

### The logbook

One row per paid call. Each row has the four labels, the batch, the role, every token count, the per-token price, the total cost in Canadian dollars, and the time. Each row carries a unique provider-side request id. If the same id arrives twice, the database refuses the duplicate.

### The cost math

OpenAI quotes the cost in US dollars. The gate fetches today's USD-to-CAD rate from the Bank of Canada (the same rate the U of T finance office uses) and multiplies. The rate refreshes once a day. If the Bank cannot be reached, the gate reuses the last rate it had. On a cold start with no network, a fallback of 1.36 keeps the system from losing a row.

The total cost is `(input tokens + output tokens) × per-token price`. Cached and reasoning tokens are recorded but not billed.

### When the database is down

A row that cannot land in the database goes into a small file ("dead letter") instead. A separate drainer reads that file when the database returns and replays the rows. The unique request id rule prevents duplicates. No row is lost.

### How the slots work

The term total comes from summing ten rows ("slots") for the active term. Every paid call adds its cost to one random slot. Ten workers can write at once without any two picking the same row. When a new term begins, writes start landing in the new term's ten slots; old rows stay as history.

### Budgets and kill switches

| Setting | Who controls it | What it does |
|---|---|---|
| `global_budget_periods.max_budget` | Admin | The cap for the term in Canadian dollars |
| `global_budget_periods.is_active` | Admin | Whole-term kill switch |
| `course_budgets.max_budget` | Admin | The cap per course |
| `course_budgets.alert_threshold` | Admin | When to warn the teacher |
| `course_budgets.is_active` | Admin | Per-course kill switch |
| `api_keys.is_active` | Admin | Kill switch for the upstream OpenAI key |

The alert fires once per crossing. The system stamps a column the first time a course passes its threshold; later calls see the stamp and stay quiet. A new term needs an admin to clear the stamp.

### Reply-size limit

Every call must say how many output tokens it allows (`max_tokens`). A missing value rejects. A value above the gate's ceiling (4096 by default) rejects. This stops a runaway prompt from burning the term on one call.

### Fail-closed everywhere

Every layer halts on failure rather than passing the call through. If the database stops responding, the gate returns "Gateway temporarily unavailable" until it comes back. If no term is active, the call stops. If a kill switch is flipped, the call stops. No call sneaks through "best effort."

---

## Part 3 — How to set it up and test it

Every command and route in this part was checked against the running stack and the source repos before being written.

### What you need before you start

| Component | Folder on disk | How to confirm |
|---|---|---|
| The gateway stack | `~/work/ai-telemetry-gateway/local-stack` | `docker compose ps` shows `aitg-litellm` and `aitg-postgres` healthy |
| The autotester | `~/work/autotesting` | Branch `ai-telemetry-gateway-connection` present (`git branch \| grep telemetry`) |
| The AI feedback library | `~/work/autograding-feedback-py` | Same branch present |
| MarkUs | `~/work/Markus` | Same branch present |

### Step 1 — Start the gateway

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

Apply migrations and seed (both idempotent):

```bash
docker compose run --rm migrate up
docker compose run --rm migrate seed
```

### Step 2 — Confirm the gate rejects unlabeled calls

This proves the attribution guard and gatekeeper are wired without touching MarkUs at all.

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

### Step 3 — Confirm the gate enforces `max_tokens`

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

### Step 4 — Confirm the gate accepts a fully labeled call

```bash
curl -s -X POST http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -H 'x-litellm-spend-logs-metadata: {"instance":"markus.example.edu","course_id":12,"assignment_id":34,"group_id":56,"batch_id":null,"category":"student"}' \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}],"max_tokens":5}'
```

In the shipped local config, `gpt-4o-mini` has a `mock_response` set (see `litellm-config.yaml`), so this call returns **HTTP 200 with a canned reply without ever contacting OpenAI** — and still writes a row to `usage_logs`, because the success hook fires on mock responses too. That proves the gate accepted and forwarded the labeled call.

To exercise a *real* upstream call, use a model with no mock (`gpt-4o`): with a placeholder `OPENAI_API_KEY` it returns HTTP 401 from OpenAI (the gate accepted and forwarded); set a real `OPENAI_API_KEY` in `.env`, recreate the litellm container, and the same call returns HTTP 200 and writes a row with the real cost.

### Step 5 — Drive each budget path with the database

These commands change live state; reset them when done.

```bash
USER=$(grep ^POSTGRES_USER= .env | cut -d= -f2-)
PASS=$(grep ^POSTGRES_PASSWORD= .env | cut -d= -f2-)
PSQL="PGPASSWORD=$PASS psql -h 127.0.0.1 -p 5434 -U $USER -d aitg -q"

# Course spend is SUM(total_cost) over usage_logs for that (instance, course_id)
# in the active period — so to exhaust the cap you need spend on record. Insert a
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

### Step 6 — Point the autotester at the gateway

Edit `~/work/autotesting/server/autotest_server/settings.yml`:

```yaml
default_remote_url: https://polymouth.teach.cs.toronto.edu:443/chat
remote_url_whitelist:
  - https://polymouth.teach.cs.toronto.edu:443/chat
  - http://localhost:4000/v1            # the LiteLLM gateway
```

Restart the autotester. Calling MarkUs's "refresh autotest schema" (Step 7) makes the new URL appear as a choice for assignments.

### Step 7 — Set up MarkUs and its connection to the autotester

Bringing MarkUs up locally is a full Rails environment setup (`bundle install`, Postgres, Redis, asset build). Once it is running:

1. Log in as an admin.
2. Open course settings.
3. Set the autotester URL and click **Test connection**. The Rails route is `GET /admin/courses/:id/test_autotest_connection`.
4. Click **Refresh autotest schema**. The Rails route is `POST /admin/courses/:id/refresh_autotest_schema`.

### Step 8 — Configure an assignment to use AI feedback through the gateway

1. As an instructor, open the assignment.
2. Go to **Automated Tests → Manage**. The Rails route is `GET /courses/:course_id/assignments/:assignment_id/automated_tests/manage`.
3. Add a tester of type **ai**.
4. In the test group `config`, set:
   - `model`: `openai-remote`
   - `remote_url`: the gateway URL from your whitelist, for example `http://localhost:4000/v1`
   - `prompt`, `scope`, `submission`: the usual AI feedback library choices.
   - `output`: one of `overall_comment`, `annotations`, `message`.
5. Save.

> **What `model: openai-remote` is.** This is a *provider key* in the AI feedback
> library's `ModelFactory`, not an upstream model name. It is registered in
> `ai_feedback/models/__init__.py` (`"openai-remote": OpenAIRemoteModel`) on the
> `ai-telemetry-gateway-connection` branch. `OpenAIRemoteModel` subclasses `OpenAIModel`
> but points the OpenAI client at the LiteLLM gateway (`remote_url`) instead of
> `api.openai.com`, authenticates with the LiteLLM virtual key as
> `Authorization: Bearer`, and forwards the `x-litellm-spend-logs-metadata`
> attribution header. The actual upstream model (e.g. `gpt-4o-mini`) is a separate
> value the gateway resolves from its own `model_list` in `litellm-config.yaml`.
> (Contrast with `model: remote`/`RemoteModel`, which targets the `markus-ai-server`
> "polymouth" proxy with a custom payload and an `X-API-KEY` header.)

The autotester schema field for `remote_url` (`~/work/autotesting/server/autotest_server/testers/ai/settings_schema.json`) presents whichever URLs are in your whitelist as the drop-down choices.

### Step 9 — Run a test and watch the row land

1. As an instructor or student, trigger a test run on a submission.
2. While the run executes, tail the gateway logs:

   ```bash
   docker logs -f aitg-litellm
   ```

3. After the run, query the logbook:

   ```bash
   eval "$PSQL -c \"SELECT provider_request_id, instance, course_id, total_cost, created_at FROM aitg.usage_logs ORDER BY id DESC LIMIT 5;\""
   ```

   A row appears with the four labels and the CAD cost.

### Step 10 — Drive the alert path

Set the alert threshold low so the next call crosses it:

```bash
eval "$PSQL -c \"UPDATE aitg.course_budgets SET alert_threshold=0.01 WHERE instance='markus.example.edu' AND course_id=12;\""
```

Run another test. Check `course_budgets.alert_sent_at`:

```bash
eval "$PSQL -c \"SELECT instance, course_id, alert_sent_at FROM aitg.course_budgets;\""
```

The stamp is set. The next call does not re-fire. Wiring the actual instructor email to MarkUs's `NotificationMailer` is the planned next piece (decision-record §"Alert delivery").

### A quick check without MarkUs

Standing up MarkUs end-to-end is a long setup. If that is too much for one sitting, Steps 2 through 5 cover the gate end-to-end. The repo's automated checks also cover every code path:

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

The verified count today: 57 pass, 11 skipped, against the live database. Coverage spans the schema, role privileges, encryption, the attribution guard, the telemetry adapter, the gatekeeper, the CAD FX cascade, the dead-letter drain, and the three health-check queries.

### Where to look when something goes wrong

| Symptom | First place to look |
|---|---|
| Every call returns 400 "Missing required MarkUs attribution" | The autotester is not sending the `x-litellm-spend-logs-metadata` header. Check the AI tester is on the `ai-telemetry-gateway-connection` branch and `model: openai-remote` is set. |
| Every call returns 400 "Gateway temporarily unavailable" | Database is unreachable from the litellm container. `docker compose logs litellm` shows the exception. |
| Costs read as 0 | `OPENAI_API_KEY` is unset or fake; OpenAI returned 401 and the success hook did not fire. |
| Alert email never arrives | The gatekeeper stamps `alert_sent_at` but the MarkUs mailer wiring is the next step. |
