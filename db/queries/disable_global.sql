-- AI feature flag, global: turn AI off for every course on every instance.
-- The gatekeeper then rejects every call with
-- "AI features are currently disabled for all courses."
--
-- The flag belongs to no term, so it stays off across term rollovers until
-- enable_global.sql runs. Budgets, periods and course switches are untouched.
--
-- Run by an admin (aitg_admin) with psql -f. No variables. Prints the
-- resulting row.

INSERT INTO aitg.global_settings (ai_enabled)
VALUES (FALSE)
ON CONFLICT (singleton) DO UPDATE
   SET ai_enabled = FALSE,
       updated_at = NOW()
RETURNING ai_enabled, updated_at;
