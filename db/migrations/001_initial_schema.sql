-- Initial schema for the AI Telemetry Gateway.
--
-- This single file replaces what was previously eight migrations
-- (001..008). On a fresh database it creates every table, every index,
-- and the application-role grants in one transaction.
--
-- Three intentional deviations from the original design are called out
-- inline below; the rationale lives in docs/decision-record.md §"Phase 3
-- schema decisions".

SET search_path TO aitg;


-- api_keys ---------------------------------------------------------------
-- Encrypted store for upstream provider credentials. Instructor keys no
-- longer live in .env files.
CREATE TABLE api_keys (
  id            SERIAL       PRIMARY KEY,
  provider      VARCHAR(50)  NOT NULL,                -- e.g., 'OpenAI', 'Azure', 'Anthropic'
  key_name      VARCHAR(100) NOT NULL UNIQUE,         -- nickname, e.g., 'UofT OpenAI'
  encrypted_key TEXT         NOT NULL,                -- ciphertext; see lib/encryption.py
  is_active     BOOLEAN      NOT NULL DEFAULT TRUE    -- kill switch for leaked keys
);

COMMENT ON TABLE  api_keys IS 'Encrypted upstream provider credentials.';
COMMENT ON COLUMN api_keys.encrypted_key IS 'Fernet-encrypted ciphertext. Never store plaintext.';
COMMENT ON COLUMN api_keys.is_active     IS 'Set FALSE to instantly disable a leaked key.';


-- global_budget_periods --------------------------------------------------
-- Term-level caps. The gatekeeper selects the active period by current
-- timestamp; no match → fail-closed.
CREATE TABLE global_budget_periods (
  id           SERIAL        PRIMARY KEY,
  period_code  VARCHAR(20)   NOT NULL UNIQUE,         -- e.g., 'summer_2026'
  display_name VARCHAR(100)  NOT NULL,                -- e.g., 'Summer 2026'
  max_budget   NUMERIC(12,2) NOT NULL,                -- CAD cap for the term
  starts_at    TIMESTAMP     NOT NULL,                -- inclusive
  ends_at      TIMESTAMP     NOT NULL,                -- inclusive
  is_active    BOOLEAN       NOT NULL DEFAULT TRUE,   -- manual kill switch for the whole term
  CONSTRAINT global_budget_periods_chronological CHECK (starts_at < ends_at)
);

COMMENT ON TABLE  global_budget_periods IS 'Term-level budget caps.';
COMMENT ON COLUMN global_budget_periods.is_active IS 'FALSE disables AI spending across all courses for the period.';


-- course_budgets ---------------------------------------------------------
-- Per-course caps and the once-per-crossing alert stamp.
--
-- DEVIATION 1: The original design declared PRIMARY KEY on `course_id`
-- alone, but the `instance` column is also required because multiple
-- MarkUs deployments share this database (Phase 1 decision 5). `course_id`
-- collides across instances. Composite PK on (instance, course_id)
-- preserves the original intent while making cross-instance rows
-- distinguishable.
--
-- `alert_sent_at` is the Phase 6 DB-level idempotency guard for instructor
-- alerts. Stamping it inside the same transaction that decides "we just
-- crossed" ensures exactly one alert per crossing under concurrent workers.
-- NULL means "not yet alerted this period"; period rotation does not reset
-- the column (manual reset on a fresh term: UPDATE ... SET alert_sent_at = NULL).
CREATE TABLE course_budgets (
  instance        VARCHAR(255)  NOT NULL,                -- FQDN, e.g., 'markus.cs.toronto.edu'
  course_id       INTEGER       NOT NULL,                -- MarkUs Course.id local to instance
  max_budget      NUMERIC(10,2) NOT NULL,                -- CAD allowed for the course this term
  alert_threshold NUMERIC(10,2),                         -- NULL = no instructor alert
  is_active       BOOLEAN       NOT NULL DEFAULT TRUE,   -- per-course kill switch
  alert_sent_at   TIMESTAMP,                             -- Phase 6 idempotency stamp
  PRIMARY KEY (instance, course_id)
);

COMMENT ON TABLE  course_budgets IS 'Per-course budget caps. Composite PK resolves cross-instance ambiguity.';
COMMENT ON COLUMN course_budgets.alert_threshold IS 'Spend value at which the instructor email fires once (Phase 6).';
COMMENT ON COLUMN course_budgets.alert_sent_at IS
  'Phase 6: timestamp when the instructor alert fired. NULL = not yet fired. DB-level idempotency guard.';


-- budget_slots -----------------------------------------------------------
-- Ten-slot counter per period: workers pick a random slot and atomically
-- increment its current_value. Total spend = SUM(current_value) per period.
--
-- DEVIATION 2: The original design declared PRIMARY KEY on `slot_id` alone
-- with "IDs 1 through 10", yet period rollover requires workers to switch
-- from "Fall slots" to "Winter slots". 10 rows total cannot carry both
-- per-period scope and the audit trail. Composite PK on (period_id,
-- slot_id) — each period gets its own 10 rows; rotation is non-destructive.
CREATE TABLE budget_slots (
  period_id     INTEGER       NOT NULL REFERENCES global_budget_periods(id),
  slot_id       INTEGER       NOT NULL,
  current_value NUMERIC(12,5) NOT NULL DEFAULT 0,
  updated_at    TIMESTAMP     NOT NULL DEFAULT NOW(),
  PRIMARY KEY (period_id, slot_id),
  CONSTRAINT budget_slots_slot_id_range CHECK (slot_id BETWEEN 1 AND 10),
  CONSTRAINT budget_slots_non_negative  CHECK (current_value >= 0)
);

COMMENT ON TABLE  budget_slots IS '10-slot counter per period. Composite PK scopes slots to a period.';
COMMENT ON COLUMN budget_slots.current_value IS 'CAD spent in this slot for this period. SUM() across 10 slots gives period total.';


-- usage_logs -------------------------------------------------------------
-- One row per billable OpenAI call; populated by the Phase 5 post-call hook.
--
-- DEVIATION 3: The original column `category VARCHAR(20)` is renamed to
-- `requester_role` to match the autotester payload field (decision-record
-- §4). Same semantics, clearer name. Treat the two names as synonymous.
CREATE TABLE usage_logs (
  id                  BIGSERIAL     PRIMARY KEY,
  provider_request_id VARCHAR(255)  UNIQUE,                -- OpenAI request ID; dedup key for replays
  api_key_id          INTEGER       NOT NULL REFERENCES api_keys(id),
  instance            VARCHAR(255)  NOT NULL,              -- e.g., 'markus.cs.toronto.edu'
  course_id           INTEGER       NOT NULL,              -- MarkUs Course.id from file_url
  assignment_id       INTEGER       NOT NULL,              -- MarkUs Assignment.id from file_url
  group_id            INTEGER       NOT NULL,              -- MarkUs Group.id from file_url
  batch_id            INTEGER,                             -- NULL for solo runs
  requester_role      VARCHAR(20),                         -- originally named `category`; see header
  input_tokens        INTEGER       NOT NULL DEFAULT 0,
  cached_tokens       INTEGER       NOT NULL DEFAULT 0,
  output_tokens       INTEGER       NOT NULL DEFAULT 0,
  reasoning_tokens    INTEGER       NOT NULL DEFAULT 0,
  unit_price          NUMERIC(15,10),                      -- CAD per 1k tokens at call time
  total_cost          NUMERIC(10,5),                       -- CAD: (input + output) * unit_price
  created_at          TIMESTAMP     NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  usage_logs IS 'One row per billable call. `requester_role` was originally named `category`.';
COMMENT ON COLUMN usage_logs.provider_request_id IS 'OpenAI Request ID. UNIQUE catches dead-letter replays.';
COMMENT ON COLUMN usage_logs.total_cost IS 'Snapshot of (input_tokens + output_tokens) * unit_price in CAD.';


-- Indexes ----------------------------------------------------------------
-- Justified by exactly one query each. Extra indexes slow down inserts
-- (which run on every chat completion), so keep this minimal.

-- Active-period lookup. The gatekeeper runs this on every call:
--   SELECT id, max_budget FROM global_budget_periods
--    WHERE NOW() BETWEEN starts_at AND ends_at AND is_active LIMIT 1;
CREATE INDEX gbp_active_window_idx
  ON global_budget_periods (starts_at, ends_at)
  WHERE is_active;

-- Global spend total. The gatekeeper runs this on every call:
--   SELECT COALESCE(SUM(current_value), 0) FROM budget_slots WHERE period_id = $1;
-- The composite PK already covers this query, so no extra index is needed.
-- (Documented here so the absence is intentional, not an oversight.)

-- Per-course audit queries. Admin/billing dashboards run this:
--   SELECT * FROM usage_logs
--    WHERE instance = $1 AND course_id = $2
--      AND created_at >= $start AND created_at < $end;
CREATE INDEX usage_logs_instance_course_time_idx
  ON usage_logs (instance, course_id, created_at);

-- Lookup by external request ID (dead-letter replay dedup, audit traces).
-- The UNIQUE constraint on provider_request_id already creates this index.
-- No extra DDL needed; documented here for completeness.


-- Application-role grants -----------------------------------------------
-- The admin role (aitg_admin) keeps schema ownership; the app role
-- (aitg_app) gets CRUD-only access. init-db.sql sets DEFAULT PRIVILEGES
-- so this section covers the tables created above.

GRANT SELECT, INSERT, UPDATE, DELETE
  ON aitg.api_keys, aitg.global_budget_periods, aitg.course_budgets,
     aitg.budget_slots, aitg.usage_logs
  TO aitg_app;

GRANT USAGE, SELECT
  ON ALL SEQUENCES IN SCHEMA aitg
  TO aitg_app;
