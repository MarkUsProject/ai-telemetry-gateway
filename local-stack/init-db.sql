-- Runs once on first Postgres container start (mounted into
-- /docker-entrypoint-initdb.d/). Creates the application role with the
-- minimum privileges LiteLLM and the gatekeeper hook need.
--
-- LOCAL ASSUMPTION (secrets): the password for the application
-- role is read from an environment variable injected by docker-compose at
-- container start. PROD: same separation, with the role password sourced
-- from sysadmin's secret store.
--
-- Why a separate role from POSTGRES_USER:
--   POSTGRES_USER (aitg_admin)  — superuser on this database, used for
--                                 schema migrations only.
--   aitg_app                    — connects from the LiteLLM container and,
--                                 in Phase 6, from the gatekeeper hook.
--                                 CRUD-only on application tables; cannot
--                                 modify schema in steady state.
--
-- For Phase 2 we keep LiteLLM connecting as the admin role because it
-- runs `prisma migrate deploy` on startup. Phase 3 moves LiteLLM to the
-- app role once our 5 tables are migrated through a separate path.

CREATE ROLE aitg_app WITH LOGIN PASSWORD 'aitg_app_local_only_placeholder';

-- Dedicated schema for our tables. LiteLLM's Prisma client operates
-- against the `public` schema and runs a "migration diff" on every boot
-- which DROPs any unfamiliar tables. Keeping our tables in `aitg` makes
-- them invisible to Prisma and keeps both schemas independently managed.
CREATE SCHEMA aitg AUTHORIZATION aitg_admin;

-- App role connects, uses the aitg schema, and can read/write its data —
-- but cannot create or drop schema objects (schema changes go through
-- migrations run as the admin role).
GRANT CONNECT ON DATABASE aitg TO aitg_app;
GRANT USAGE ON SCHEMA aitg TO aitg_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA aitg TO aitg_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA aitg TO aitg_app;

-- Future tables created in the aitg schema inherit the same grants.
ALTER DEFAULT PRIVILEGES IN SCHEMA aitg
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO aitg_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA aitg
  GRANT USAGE, SELECT ON SEQUENCES TO aitg_app;

-- aitg_app reads/writes only in the aitg schema, so its default search_path
-- points there. aitg_admin's default stays as the Postgres standard
-- (`"$user", public`) so LiteLLM's Prisma client — which connects as
-- aitg_admin — creates LiteLLM_* tables in public and never reaches into
-- aitg. Our migrations issue an explicit `SET search_path TO aitg;` at the
-- top of each file, so they don't rely on a session default.
ALTER ROLE aitg_app IN DATABASE aitg SET search_path = aitg, public;
