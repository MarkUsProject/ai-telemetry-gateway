-- Returns the active global_budget_periods row for the current timestamp,
-- or zero rows if no period is active (fail-closed trigger).
--
-- Phase 6 gatekeeper runs this on every chat completion. The empty-result
-- case is load-bearing: when no row matches, the gatekeeper halts the call.
--
-- Index used: gbp_active_window_idx (partial, WHERE is_active).

SELECT id, period_code, display_name, max_budget, starts_at, ends_at
  FROM aitg.global_budget_periods
 WHERE NOW() BETWEEN starts_at AND ends_at
   AND is_active
 ORDER BY starts_at DESC
 LIMIT 1;
