# Health-check queries

These files hold named SQL fragments. Each file is one query. The telemetry adapter and the gatekeeper hook run them against the database. Each query reads rows and leaves them as they are.

A comment at the top of each file lists:

- its purpose
- its parameters
- the index it relies on

| File | Purpose | Called by |
|---|---|---|
| `active_period.sql` | Find the budget period active now. Return empty if none is active. | Gatekeeper, every chat completion |
| `global_spend.sql` | Sum the 10 slot rows for a period to get total CAD spent | Gatekeeper, every chat completion |
| `course_spend.sql` | Sum `usage_logs.total_cost` for a course within the period | Gatekeeper, after the global check passes |

An admin dashboard can reuse these queries.
