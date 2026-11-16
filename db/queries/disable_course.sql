-- AI feature flag, per course: turn AI off for one course.
-- The gatekeeper then rejects that course's calls with
-- "Course AI features are disabled for course_id=N (is_active = false)."
--
-- Run by an admin with psql -f. psql variables:
--   instance   MarkUs host as the autotester reports it, e.g. markus.cs.toronto.edu
--   course_id  MarkUs Course.id on that instance
--
-- Prints the changed row. "UPDATE 0" means no budget row exists for that
-- course, which already blocks it ("No course budget configured").

UPDATE aitg.course_budgets
   SET is_active = FALSE
 WHERE instance = :'instance'
   AND course_id = :course_id
RETURNING instance, course_id, is_active;
