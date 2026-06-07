-- Returns the current per-course spend total in CAD for the active period.
-- Phase 6 gatekeeper compares this to course_budgets.max_budget on every
-- call. Used after the global cap check passes.
--
-- The schema stores spend only in aggregate slots (no per-course
-- column), so this query derives per-course totals from usage_logs filtered
-- by (instance, course_id, created_at). If Phase 6 chooses per-course slots
-- as a separate optimization, this query is replaced with a slot SUM.
--
-- Parameters (positional, bind via psycopg3 %% s placeholders below):
--   1. instance       (FQDN, e.g., 'markus.cs.toronto.edu')
--   2. course_id      (INTEGER)
--   3. period_starts  (TIMESTAMP, from active_period row)
--   4. period_ends    (TIMESTAMP, from active_period row)
--
-- Index used: usage_logs_instance_course_time_idx.

SELECT COALESCE(SUM(total_cost), 0) AS course_spend
  FROM aitg.usage_logs
 WHERE instance      = %s
   AND course_id     = %s
   AND created_at   >= %s
   AND created_at   <  %s;
