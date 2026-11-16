-- AI feature flag, per course: turn AI on for one course.
-- Flips the switch only. It does not create the budget row or change the cap.
--
-- Run by an admin with psql -f. psql variables:
--   instance   MarkUs host as the autotester reports it, e.g. markus.cs.toronto.edu
--   course_id  MarkUs Course.id on that instance
--
-- Prints the changed row. "UPDATE 0" means no budget row exists for that
-- course; create one first (see docs/USER_GUIDE.md).

UPDATE aitg.course_budgets
   SET is_active = TRUE
 WHERE instance = :'instance'
   AND course_id = :course_id
RETURNING instance, course_id, max_budget, is_active;
