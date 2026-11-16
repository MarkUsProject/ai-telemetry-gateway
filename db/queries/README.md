# Named queries

These files hold named SQL fragments. Each file is one query. There are two kinds:

- **Health-check queries.** The telemetry adapter and the gatekeeper hook run them against the database. Each one reads rows and leaves them as they are.
- **Feature-flag queries.** An admin runs them by hand to turn AI on or off. Each one changes a single switch and prints the row it changed.

A comment at the top of each file lists:

- its purpose
- its parameters
- the index it relies on, for a health-check query

| File | Purpose | Called by |
|---|---|---|
| `active_period.sql` | Find the budget period active now. Return empty if none is active. | Gatekeeper, every chat completion |
| `global_spend.sql` | Sum the 10 slot rows for a period to get total CAD spent | Gatekeeper, every chat completion |
| `course_spend.sql` | Sum `usage_logs.total_cost` for a course within the period | Gatekeeper, after the global check passes |
| `disable_global.sql` | AI feature flag: turn AI off for every course, in every term | Admin, by hand |
| `enable_global.sql` | AI feature flag: turn AI on | Admin, by hand |
| `disable_course.sql` | AI feature flag: turn AI off for one course | Admin, by hand |
| `enable_course.sql` | AI feature flag: turn AI on for one course | Admin, by hand |

An admin dashboard can reuse the health-check queries.

## Running a feature-flag query

Use `psql -f`, as the admin role (`aitg_admin`). The course queries take two variables:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/queries/disable_global.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -v instance='markus.example.edu' -v course_id=12 -f db/queries/disable_course.sql
```

`psql` fills the variables only when it reads a file, so `-c` does not work here. See "AI feature flag" in `docs/USER_GUIDE.md`.
