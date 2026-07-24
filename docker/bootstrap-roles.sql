-- Plan 147 Slice D — idempotent least-privilege DB role bootstrap.
--
-- Run as the DB OWNER (the Postgres bootstrap superuser, `${DB_USER:-sapphire}`)
-- from the `init` service, AFTER `alembic upgrade head` so grants cover every
-- migrated table, on EVERY deploy (fresh volume AND in-place upgrade both
-- converge here — docs/standards/cicd.md § DB role bootstrap).
--
-- Idempotent by construction:
--   * role creation is CREATE-IF-NOT-EXISTS, else ALTER ROLE ... PASSWORD
--     (so a password-secret rotation + re-run picks up the new password);
--   * every GRANT/REVOKE is a no-op when already in the target state.
--
-- Scope (conventions.md § Service users): `sapphire_api` and `sapphire_worker`
-- only. `sapphire_prefect` is UNCHANGED by this slice — the prefect-server
-- container keeps using the owner credential against the separate `prefect`
-- database (docker/init-db.sh), which is a documented residual, not an
-- omission (Plan 147 §Slice D).
--
-- psql client-side variables `:'api_password'` / `:'worker_password'` are
-- substituted (and SQL-literal-quoted) by psql BEFORE the query is sent.
-- NOTE: this substitution does NOT happen inside a dollar-quoted ($$...$$)
-- string, so role create/alter is generated with `format(..., %L)` + `\gexec`
-- below instead of a `DO $$ ... $$` block (which silently passed the raw
-- `:'var'` token through to the server — caught by this slice's own
-- integration test before it ever reached a real deploy).
--
-- Postgres has no `CREATE ROLE IF NOT EXISTS`; each pair of SELECTs below
-- produces exactly one row (the ALTER branch when the role exists, the
-- CREATE branch when it does not), and `\gexec` executes whatever row(s)
-- the preceding query returned.
SELECT format('ALTER ROLE sapphire_api PASSWORD %L', :'api_password')
WHERE EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'sapphire_api')
UNION ALL
SELECT format(
    'CREATE ROLE sapphire_api LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
    'NOINHERIT NOREPLICATION PASSWORD %L',
    :'api_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'sapphire_api')
\gexec

SELECT format('ALTER ROLE sapphire_worker PASSWORD %L', :'worker_password')
WHERE EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'sapphire_worker')
UNION ALL
SELECT format(
    'CREATE ROLE sapphire_worker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
    'NOINHERIT NOREPLICATION PASSWORD %L',
    :'worker_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'sapphire_worker')
\gexec

-- Neither app role may create objects in `public` (PG16 already denies
-- CREATE on `public` to PUBLIC by default since PG15 — explicit here so the
-- invariant holds regardless of the cluster's default, and is documented).
REVOKE CREATE ON SCHEMA public FROM sapphire_api, sapphire_worker;
GRANT USAGE ON SCHEMA public TO sapphire_api, sapphire_worker;
GRANT CONNECT ON DATABASE sapphire TO sapphire_api, sapphire_worker;

-- Neither app role may read the separate Prefect database (F3(b): "read
-- another DB"). Both connect to `sapphire` only. Revoking CONNECT from a
-- named role alone is not enough — every role implicitly inherits PUBLIC's
-- ACL, so PUBLIC's default CONNECT grant must be revoked too, else
-- sapphire_api/sapphire_worker would still connect via PUBLIC (caught by
-- this slice's own integration test). The owner/`sapphire_prefect` path is
-- unaffected: the bootstrap superuser bypasses ACL checks entirely, and
-- prefect-server connects as the owner (unchanged by this slice).
REVOKE CONNECT ON DATABASE prefect FROM PUBLIC;

-- Broad SELECT — both roles are read-heavy across the domain schema; the
-- least-privilege boundary this slice enforces is per-table
-- INSERT/UPDATE/DELETE below (F3(b): "not blanket UPDATE/DELETE"), not SELECT
-- breadth. Re-running this line after a later migration adds a new table
-- extends SELECT to it automatically; a NEW table's write grants still need
-- an explicit line below (documented in conventions.md § Service users).
GRANT SELECT ON ALL TABLES IN SCHEMA public TO sapphire_api, sapphire_worker;

-- Both roles INSERT into BIGSERIAL-keyed tables (audit_log, pipeline_health);
-- USAGE (+SELECT, for currval()) on sequences is required for that INSERT to
-- succeed. Sequences carry no data of their own — broad grant is low-risk.
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO sapphire_api, sapphire_worker;

-- sapphire_api (conventions.md § Service users): the access-token lifecycle
-- (CLI create/revoke via `docker compose exec api ...`, `last_used_at` write
-- on every successful auth) plus append-only `audit_log` INSERT. NEVER
-- UPDATE/DELETE on audit_log — defense-in-depth atop the role-independent
-- append-only trigger (migration 0046), not the primary guarantee.
GRANT INSERT, UPDATE ON access_tokens TO sapphire_api;
GRANT INSERT ON access_token_stations TO sapphire_api;
GRANT INSERT ON audit_log TO sapphire_api;

-- sapphire_worker (conventions.md § Service users): the flow/CLI write paths
-- (onboarding, ingest, training, promotion, assignment) plus append-only
-- `audit_log` INSERT (Slice E's write-isolation rejection events run as the
-- worker too). NEVER UPDATE/DELETE on audit_log.
GRANT INSERT, UPDATE ON stations TO sapphire_worker;
GRANT INSERT, UPDATE ON station_groups TO sapphire_worker;
GRANT INSERT, DELETE ON station_group_members TO sapphire_worker;
GRANT INSERT, UPDATE ON station_thresholds TO sapphire_worker;
GRANT INSERT, UPDATE ON station_weather_sources TO sapphire_worker;
GRANT INSERT, UPDATE ON model_assignments TO sapphire_worker;
GRANT INSERT, UPDATE ON group_model_assignments TO sapphire_worker;
GRANT INSERT, UPDATE ON observations TO sapphire_worker;
GRANT INSERT ON observation_versions TO sapphire_worker;
GRANT INSERT, UPDATE ON forecasts TO sapphire_worker;
GRANT INSERT ON forecast_values TO sapphire_worker;
GRANT INSERT, UPDATE ON alerts TO sapphire_worker;
GRANT INSERT ON weather_forecasts TO sapphire_worker;
GRANT INSERT, UPDATE ON model_artifacts TO sapphire_worker;
GRANT INSERT ON model_artifact_basin_versions TO sapphire_worker;
GRANT INSERT ON model_states TO sapphire_worker;
GRANT INSERT ON models TO sapphire_worker;
GRANT INSERT, UPDATE ON hindcast_forecasts TO sapphire_worker;
GRANT INSERT, DELETE ON hindcast_values TO sapphire_worker;
GRANT INSERT ON skill_scores TO sapphire_worker;
GRANT INSERT ON skill_diagrams TO sapphire_worker;
GRANT INSERT ON pipeline_health TO sapphire_worker;
GRANT INSERT, UPDATE ON basins TO sapphire_worker;
GRANT INSERT, UPDATE ON basin_versions TO sapphire_worker;
GRANT INSERT ON basin_static_packages TO sapphire_worker;
GRANT INSERT, UPDATE ON rating_curves TO sapphire_worker;
GRANT INSERT ON historical_forcing TO sapphire_worker;
GRANT INSERT, UPDATE, DELETE ON clim_baselines TO sapphire_worker;
GRANT INSERT ON flow_regime_configs TO sapphire_worker;
GRANT INSERT, UPDATE ON recap_gateway_polygon_bindings TO sapphire_worker;
GRANT INSERT, UPDATE ON calculated_station_formulas TO sapphire_worker;
GRANT INSERT ON audit_log TO sapphire_worker;
