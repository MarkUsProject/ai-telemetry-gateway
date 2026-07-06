# Read-only health-check queries

Named SQL fragments that the telemetry adapter and the gatekeeper hook issue
against the database. Each file is a single query with its purpose,
parameters, and the index it relies on documented at the top.

| File | Purpose | Called by |
|---|---|---|
| `active_period.sql` | Find the currently-active budget period; return empty if none | Gatekeeper, every chat completion |
| `global_spend.sql` | Sum the 10 slot rows for a period to get total CAD spent | Gatekeeper, every chat completion |
| `course_spend.sql` | Sum `usage_logs.total_cost` for a course within the period | Gatekeeper, after the global check passes |

An admin dashboard would reuse these same queries.
