-- Local-dev seed: insert a placeholder budget period and its 10 slots so the
-- gatekeeper has something to read against. Idempotent (INSERT ... ON CONFLICT).
--
-- PROD: do NOT run this. Sysadmin or an admin tool inserts the real period
-- rows (fall_2026, winter_2027, etc.) with real budgets. The slot rows are
-- inserted by the same admin step.

SET search_path TO aitg;

-- A placeholder term spanning a wide window so the local stack always has an
-- active period regardless of the developer's clock.
INSERT INTO global_budget_periods (period_code, display_name, max_budget, starts_at, ends_at, is_active)
VALUES ('local_dev', 'Local Development', 1000.00, '2020-01-01 00:00:00', '2099-12-31 23:59:59', TRUE)
ON CONFLICT (period_code) DO NOTHING;

-- Seed all 10 slots at 0. 10 slots per period, workers pick at random.
-- Sum across slots must be 0 immediately after seeding (sentinel of 1 is a bug).
INSERT INTO budget_slots (period_id, slot_id, current_value)
SELECT gbp.id, s.slot_id, 0
  FROM global_budget_periods gbp
  CROSS JOIN generate_series(1, 10) AS s(slot_id)
 WHERE gbp.period_code = 'local_dev'
ON CONFLICT (period_id, slot_id) DO NOTHING;
