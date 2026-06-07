-- Local-dev seed: one course_budgets row for the test instance and course
-- the Phase 4/5/6 smoke tests use (markus.example.edu, course_id 12). Without
-- this row every call from the smoke-test metadata fails-closed at the Phase 6
-- "missing course budget" guard.
--
-- PROD: do NOT run this. Course budgets are created per-course by an admin
-- (instructor or sysadmin) through the same channel that enters term budgets.

SET search_path TO aitg;

INSERT INTO course_budgets (instance, course_id, max_budget, alert_threshold, is_active)
VALUES ('markus.example.edu', 12, 100.00, 80.00, TRUE)
ON CONFLICT (instance, course_id) DO NOTHING;
