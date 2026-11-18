-- Global AI feature flag.
--
-- One row, one switch: global_settings.ai_enabled. The gatekeeper reads it
-- before every other database check. FALSE, or a missing row, halts every
-- call on every instance with "AI features are currently disabled".
--
-- This is separate from global_budget_periods.is_active on purpose. That
-- column belongs to one term and a new term's row starts active, so it
-- cannot hold a lasting "off". This switch belongs to no term: it stays off
-- across term rollovers until an admin turns it on.
--
-- The migration creates the table only. The row comes from a seed (local:
-- db/seed/003, on; staging: the deploy kit's 003 template, off) or from
-- db/queries/enable_global.sql / disable_global.sql, which create it on
-- first use.

SET search_path TO aitg;


-- global_settings --------------------------------------------------------
-- The `singleton` primary key with CHECK (singleton) allows exactly one row.
CREATE TABLE global_settings (
  singleton  BOOLEAN   PRIMARY KEY DEFAULT TRUE,
  ai_enabled BOOLEAN   NOT NULL DEFAULT FALSE,   -- global AI feature flag
  updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
  CONSTRAINT global_settings_single_row CHECK (singleton)
);

COMMENT ON TABLE  global_settings IS 'Gateway-wide settings. Exactly one row.';
COMMENT ON COLUMN global_settings.ai_enabled IS
  'Global AI feature flag. FALSE halts every call on every instance, in every term.';


-- Application-role grants -----------------------------------------------
-- Read-only for the app role: the gatekeeper reads the flag, and only an
-- admin flips it. This overrides the CRUD default privileges set at
-- database setup, so a leaked aitg_app credential cannot turn AI back on.
REVOKE ALL ON aitg.global_settings FROM aitg_app;
GRANT SELECT ON aitg.global_settings TO aitg_app;
