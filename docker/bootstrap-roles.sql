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

-- Plan 162 T1 — the backup identity. Deliberately INHERIT (unlike
-- sapphire_api/sapphire_worker above): its entire purpose is unconditional
-- broad SELECT via `pg_read_all_data`, and `pg_dump` never issues `SET ROLE`
-- — a NOINHERIT role here would hold the membership and still be denied at
-- dump time (D1). Kept OUT of the api/worker blanket-revoke block below;
-- converged in its OWN block further down this file.
SELECT format('ALTER ROLE sapphire_backup PASSWORD %L', :'backup_password')
WHERE EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'sapphire_backup')
UNION ALL
SELECT format(
    'CREATE ROLE sapphire_backup LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
    'INHERIT NOREPLICATION PASSWORD %L',
    :'backup_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'sapphire_backup')
\gexec

-- ── CONVERGE PRE-EXISTING ROLES TO LEAST PRIVILEGE ─────────────────────────
-- The CREATE branch above sets NOSUPERUSER/... on a FRESH role, but a role
-- that ALREADY EXISTS (an in-place upgrade on an existing volume) took the
-- ALTER-PASSWORD branch and had ONLY its password reset. Without the block
-- below it would RETAIN any SUPERUSER/CREATEDB/CREATEROLE/REPLICATION/
-- BYPASSRLS attributes, role memberships, and stale table/schema/DB grants
-- (including UPDATE/DELETE on audit_log) left by an earlier, over-broad
-- deploy — so an in-place upgrade would NOT converge to least privilege,
-- contradicting Plan 147 Slice D's "a fresh volume AND an in-place existing-
-- volume upgrade converge to the same roles/grants" requirement. These
-- statements run UNCONDITIONALLY on the SAME path for fresh and pre-existing
-- roles, so both reach the identical least-privilege state; every one is
-- idempotent (a re-run on already-correct roles is a no-op).

-- (1) Normalize attributes: strip every attribute that grants escalation. A
--     fresh role is already in this state (no-op); a pre-existing SUPERUSER
--     role is demoted here.
ALTER ROLE sapphire_api NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
ALTER ROLE sapphire_worker NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;

-- (2) Revoke every role membership either app role currently holds — a stale
--     membership in a privileged group (e.g. an owner/admin role) would
--     otherwise keep re-conferring privileges the per-table matrix never
--     grants. One row per (granted_role, app_role) held; `\gexec` runs each
--     REVOKE (no rows -> no-op when the roles hold no memberships).
SELECT format('REVOKE %I FROM %I', granted.rolname, member.rolname)
FROM pg_catalog.pg_auth_members am
JOIN pg_catalog.pg_roles granted ON granted.oid = am.roleid
JOIN pg_catalog.pg_roles member ON member.oid = am.member
WHERE member.rolname IN ('sapphire_api', 'sapphire_worker')
\gexec

-- (3) Revoke all prior object privileges before the GRANTs below re-apply the
--     intended least-privilege set, so a stale grant from an earlier over-
--     broad deploy (e.g. UPDATE/DELETE on audit_log) cannot linger past this
--     run. REVOKE of a privilege not held is a no-op, so this is safe on a
--     freshly created role too. The intended grants are re-applied by the
--     unchanged, Codex-approved per-table matrix that follows.
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM sapphire_api, sapphire_worker;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM sapphire_api, sapphire_worker;
REVOKE ALL PRIVILEGES ON SCHEMA public FROM sapphire_api, sapphire_worker;
REVOKE ALL PRIVILEGES ON DATABASE sapphire FROM sapphire_api, sapphire_worker;
REVOKE ALL PRIVILEGES ON DATABASE prefect FROM sapphire_api, sapphire_worker;

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
-- Plan 215 T7: DELETE added alongside the pre-existing INSERT — the
-- `revoke-station` verb and the `set-scope-mode ... tenant` cleanup both
-- delete now-obsolete grant rows, and ran as sapphire_api (the role the CLI
-- connects as inside the `api` container) they need it.
GRANT INSERT, DELETE ON access_token_stations TO sapphire_api;
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
-- Plan 157 T3: model_artifact_provenance is a NEW table (migration 0048) —
-- its blanket SELECT above already covers reads, but writes need this
-- explicit line (conventions.md § Service users). INSERT-only, mirroring
-- model_artifact_basin_versions: a provenance row is never UPDATEd.
GRANT INSERT ON model_artifact_provenance TO sapphire_worker;

-- sapphire_worker must NOT be able to read the auth tables. The blanket
-- `GRANT SELECT ON ALL TABLES ...` above intentionally includes
-- access_tokens/access_token_stations (a schema-wide convenience grant —
-- see the comment above that GRANT), but a Prefect worker running flows has
-- no business reading token hashes/scopes; only sapphire_api's auth path
-- needs that table. Revoke it back off for sapphire_worker specifically,
-- leaving sapphire_api's SELECT (and its INSERT/UPDATE grants above)
-- untouched. Runs unconditionally on every bootstrap (fresh volume AND
-- in-place upgrade converge here, same as the rest of this file); REVOKE of
-- a privilege not held is a no-op, so a second run is a no-op too.
-- Caught by a live docker-compose deploy rehearsal, not static review.
REVOKE SELECT ON access_tokens, access_token_stations FROM sapphire_worker;

-- ── sapphire_backup: OWN CONVERGENCE BLOCK (Plan 162 T1) ────────────────────
-- Deliberately NOT folded into the api/worker blanket-revoke block above —
-- sapphire_backup is an INHERIT role (D1) with a single, unconditional
-- membership (`pg_read_all_data`), never a per-table matrix. Runs
-- unconditionally on every bootstrap (fresh volume AND in-place upgrade
-- converge here, same as the rest of this file); every statement below is
-- idempotent — a re-run against an already-correct role is a no-op.

-- (1) Fail loudly if sapphire_backup ALREADY owns any object. Direct ACL
--     REVOKE (below) cannot strip owner-intrinsic privileges — only
--     `REASSIGN OWNED` / `DROP OWNED` can — so a role that already owns
--     something is a signal this bootstrap must not silently paper over.
--     Runs first, before any grants change, so the operator sees this
--     specific failure rather than a confusing downstream symptom.
--
--     `pg_shdepend` (a SHARED catalog, spanning every database in the
--     cluster) is the same mechanism Postgres itself uses to refuse
--     `DROP ROLE` while a role still owns something -- deptype = 'o'
--     records EVERY kind of ownership (tables, schemas, functions/
--     procedures, standalone types/domains, sequences, and DATABASES
--     themselves), including ownership in a DATABASE OTHER than the one
--     this script is connected to. A hand-enumerated per-catalog check
--     (pg_class + pg_namespace alone, as an earlier revision of this
--     block did) misses functions, types/domains, databases, and
--     anything owned in another database entirely -- this query does not.
DO $$
DECLARE
    owned_objects integer;
BEGIN
    SELECT count(*) INTO owned_objects
    FROM pg_catalog.pg_shdepend sd
    JOIN pg_catalog.pg_roles r ON r.oid = sd.refobjid
    WHERE r.rolname = 'sapphire_backup'
      AND sd.deptype = 'o';

    IF owned_objects > 0 THEN
        RAISE EXCEPTION
            'sapphire_backup owns % object(s) across the cluster (tables, '
            'schemas, functions, sequences, types/domains, or databases -- '
            'in this database or another one) -- direct ACL revocation '
            'cannot strip owner-intrinsic privileges. Resolve manually '
            '(REASSIGN OWNED BY sapphire_backup TO ... or DROP OWNED BY '
            'sapphire_backup, run in EVERY database it owns something in) '
            'before re-running the role bootstrap.',
            owned_objects;
    END IF;
END $$;

-- (2) Preflight, fail loudly: `pg_read_all_data` does NOT confer BYPASSRLS,
--     so a row-level-security-enabled table would silently hand
--     sapphire_backup a POLICY-FILTERED (i.e. partial) view instead of a
--     denial pg_dump could at least report. It also does NOT cover large
--     objects (ACL'd individually), so any large object would be silently
--     skipped by pg_dump. Neither condition holds today (measured
--     2026-08-14: both counts 0) -- fail the instant either becomes true
--     rather than let a future migration make the nightly dump quietly
--     partial.
DO $$
DECLARE
    rls_tables integer;
    large_objects integer;
BEGIN
    SELECT count(*) INTO rls_tables
    FROM pg_catalog.pg_class
    WHERE relrowsecurity;

    SELECT count(*) INTO large_objects FROM pg_catalog.pg_largeobject_metadata;

    IF rls_tables > 0 THEN
        RAISE EXCEPTION
            '% table(s) have row-level security enabled -- sapphire_backup '
            '(pg_read_all_data, NOBYPASSRLS) would see a policy-filtered, '
            'silently PARTIAL view of that data. Grant BYPASSRLS to '
            'sapphire_backup or exempt it from the policy explicitly before '
            're-running the role bootstrap.',
            rls_tables;
    END IF;

    IF large_objects > 0 THEN
        RAISE EXCEPTION
            '% large object(s) exist -- pg_read_all_data does not cover '
            'large objects, so pg_dump under sapphire_backup would silently '
            'skip them. Grant explicit large-object read access before '
            're-running the role bootstrap.',
            large_objects;
    END IF;
END $$;

-- (3) Normalize attributes -- INCLUDING `LOGIN` and connection properties,
--     not merely the escalation-capable ones. A fresh role from the CREATE
--     branch above is already in this state (no-op); a pre-existing role
--     left over from an earlier deploy might be NOLOGIN (unusable for
--     pg_dump), might carry a CONNECTION LIMIT that silently throttles it,
--     or might carry a VALID UNTIL expiry that silently locks it out on
--     some future date -- none of which a prior revision of this block
--     touched, so a pre-existing role could pass this bootstrap and still
--     be unusable or its password-reset step (above) still expire.
ALTER ROLE sapphire_backup
    LOGIN
    NOSUPERUSER NOCREATEDB NOCREATEROLE
    INHERIT
    NOREPLICATION NOBYPASSRLS
    CONNECTION LIMIT -1
    VALID UNTIL 'infinity';

-- (4) Revoke EVERY role membership sapphire_backup currently holds --
--     INCLUDING a pre-existing `pg_read_all_data` membership -- then grant
--     the intended membership back with its options EXPLICITLY normalized.
--     A prior revision of this block excluded `pg_read_all_data` from the
--     revoke sweep (on the theory the immediately-following GRANT would
--     re-apply it anyway) and then issued a bare `GRANT pg_read_all_data TO
--     sapphire_backup` with no options. PostgreSQL's GRANT-role-membership
--     statement only ever ADDS/overrides the options it names -- omitted
--     options are RETAINED from the existing membership row, they are never
--     reset to a default. So a pre-existing membership carrying `WITH ADMIN
--     TRUE` (however it got there -- a stale manual grant, a downgraded
--     admin script) survived every re-run of this bootstrap indefinitely,
--     letting sapphire_backup itself GRANT pg_read_all_data membership
--     (hence cluster-wide SELECT) to arbitrary other roles -- a privilege
--     escalation path a read-only backup identity must never have.
--     Revoking unconditionally (dropping the `<> 'pg_read_all_data'`
--     exclusion) and re-granting WITH ADMIN FALSE, INHERIT TRUE, SET FALSE
--     makes every membership option explicit on every run, so no prior
--     state can survive convergence.
SELECT format('REVOKE %I FROM sapphire_backup', granted.rolname)
FROM pg_catalog.pg_auth_members am
JOIN pg_catalog.pg_roles granted ON granted.oid = am.roleid
JOIN pg_catalog.pg_roles member ON member.oid = am.member
WHERE member.rolname = 'sapphire_backup'
\gexec

-- (5) Revoke every DIRECT privilege sapphire_backup currently holds -- as
--     opposed to one conferred via role membership, handled by (4) above --
--     BEFORE the GRANTs below re-apply the intended read-only set. Without
--     this, a pre-existing role with a directly-granted INSERT/UPDATE/
--     DELETE on a table, or EXECUTE on a function OR PROCEDURE, would keep
--     that grant forever -- `pg_read_all_data` only ADDS broad SELECT, it
--     never REMOVES an unrelated direct write grant, so this role could
--     converge to "can read everything AND can still write/execute
--     whatever it was granted before" instead of read-only.
--
--     A per-object-kind enumeration (`REVOKE ... ON ALL TABLES/SEQUENCES/
--     ROUTINES IN SCHEMA public`, as an earlier revision of this block did)
--     is fundamentally incomplete, in THREE separate ways a real
--     pre-existing role can defeat it:
--       * COLUMN-level grants (`GRANT UPDATE (some_col) ON t TO
--         sapphire_backup`) survive a table-level `REVOKE ALL PRIVILEGES ON
--         ALL TABLES` -- Postgres tracks column ACLs as a SEPARATE catalog
--         entry (`pg_attribute.attacl`) that a table-level REVOKE does not
--         touch.
--       * Anything outside schema `public` survives entirely -- the
--         enumeration is scoped to `IN SCHEMA public` by construction, so a
--         grant on an object in any OTHER schema (or a grant on a
--         standalone type/domain, which has no `ALL <kind> IN SCHEMA`
--         REVOKE form at all) is never reached.
--       * `ALTER DEFAULT PRIVILEGES ... GRANT ... TO sapphire_backup` (set
--         by some other role, for objects THAT role creates in the
--         future) is never touched by any REVOKE against existing objects
--         -- it lives in `pg_default_acl`, a template applied at object
--         CREATE time, not an ACL on any object that exists yet. Left in
--         place, the very next object a future migration or manual
--         `CREATE` creates would silently re-grant sapphire_backup a
--         privilege this bootstrap just spent five steps revoking.
--
--     `DROP OWNED BY sapphire_backup` replaces the enumeration and closes
--     all three gaps in one statement: PostgreSQL implements it via
--     `pg_shdepend` (the SAME shared-catalog mechanism preflight (1) above
--     already queries for ownership), walking every ACL-type dependency
--     (`deptype = 'a'`) that names sapphire_backup as grantee -- table,
--     column, sequence, routine, schema, type/domain, AND default-privilege
--     ACLs alike -- in the current database plus shared objects (DATABASE/
--     TABLESPACE grants), and strips the role from every one. It also
--     drops any object the role OWNS, which is exactly why this line runs
--     only after preflight (1) has already failed loudly if that count is
--     nonzero: with zero owned objects there is nothing to CASCADE, so
--     plain `DROP OWNED BY` (no CASCADE) is safe and sufficient here -- the
--     ownership branch is provably never exercised at this point in the
--     script. Idempotent: a role holding none of this is a no-op.
--
--     Residual scope note: this script connects to the `sapphire` database
--     ONLY (`bootstrap-roles.sh`), so `DROP OWNED BY` here reaches
--     `sapphire`'s own objects/schemas plus shared (DATABASE/TABLESPACE)
--     grants -- it does NOT reach schema/table/column ACLs that live
--     inside the separate `prefect` database. sapphire_backup is not
--     intended to hold privilege there at all (backing up `prefect` is a
--     named non-goal, `docs/plans/162-robust-database-backup.md`); the
--     `REVOKE ... ON DATABASE prefect` and `REVOKE CONNECT ... FROM
--     PUBLIC` lines below close the reachable (shared-object) part of that
--     boundary.
DROP OWNED BY sapphire_backup;
REVOKE ALL PRIVILEGES ON DATABASE sapphire FROM sapphire_backup;
REVOKE ALL PRIVILEGES ON DATABASE prefect FROM sapphire_backup;

GRANT pg_read_all_data TO sapphire_backup WITH ADMIN FALSE, INHERIT TRUE, SET FALSE;
GRANT CONNECT ON DATABASE sapphire TO sapphire_backup;
GRANT USAGE ON SCHEMA public TO sapphire_backup;

-- Neither PUBLIC nor sapphire_backup itself may CREATE in schema public.
-- `REVOKE CREATE ... FROM sapphire_backup` ALONE is NOT sufficient: every
-- role implicitly inherits PUBLIC's ACL, so if PUBLIC ever holds CREATE (a
-- pre-PG15 cluster, or a manual re-grant), sapphire_backup would still be
-- able to create objects through PUBLIC's grant regardless of its own
-- REVOKE. Revoke from PUBLIC too -- idempotent regardless of the cluster's
-- default -- keep the named-role revoke for documentation/defense-in-depth,
-- and assert the resulting state directly rather than trusting either
-- REVOKE alone.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM sapphire_backup;

DO $$
BEGIN
    IF has_schema_privilege('sapphire_backup', 'public', 'CREATE') THEN
        RAISE EXCEPTION
            'sapphire_backup still holds CREATE on schema public after '
            'REVOKE -- PUBLIC or some other role membership is still '
            'conferring it';
    END IF;
END $$;

-- sapphire_backup must not read the separate Prefect database either --
-- same cross-database boundary as sapphire_api/sapphire_worker above.
REVOKE CONNECT ON DATABASE prefect FROM sapphire_backup;
