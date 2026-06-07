-- Returns the current global spend total in CAD for the active period.
-- Phase 6 gatekeeper compares this to global_budget_periods.max_budget on
-- every call.
--
-- Total spend = SUM of all 10 slot rows for the active period.
-- Returns 0.00000 (not NULL) when no spend has occurred yet.
--
-- Parameter (positional, psycopg3 placeholder below):
--   1. period_id (id from active_period.sql)

SELECT COALESCE(SUM(current_value), 0) AS total_spend
  FROM aitg.budget_slots
 WHERE period_id = %s;
