-- Local-dev seed: the global AI feature flag, switched on, so a fresh local
-- stack accepts calls without an extra step.
--
-- PROD: do NOT run this. The deploy kit seeds the flag off, and an admin
-- turns it on with db/queries/enable_global.sql.
-- Idempotent: re-running leaves an existing row untouched, so a flag you
-- turned off by hand stays off.

SET search_path TO aitg;

INSERT INTO global_settings (ai_enabled)
VALUES (TRUE)
ON CONFLICT (singleton) DO NOTHING;
