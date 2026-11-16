-- AI feature flag, global: turn AI on.
-- A course still needs its own budget row with is_active = TRUE, and the
-- current term still needs an active budget period.
--
-- Run by an admin (aitg_admin) with psql -f. No variables. Prints the
-- resulting row.

INSERT INTO aitg.global_settings (ai_enabled)
VALUES (TRUE)
ON CONFLICT (singleton) DO UPDATE
   SET ai_enabled = TRUE,
       updated_at = NOW()
RETURNING ai_enabled, updated_at;
