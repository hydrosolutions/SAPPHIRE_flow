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
-- ── Plan 510: the operator's SAFE STATE comes FIRST ────────────────────────
-- Before any preflight or block below that can abort under ON_ERROR_STOP, the
-- operator role (if it exists) loses login, memberships and every table,
-- sequence, schema and database privilege, and its open sessions are ended.
-- Otherwise an operator-controllable object (the role may create TEMP objects
-- and large objects) could abort `init` first and leave a surviving session
-- holding its DML.
--
-- ORDER MATTERS. (1) The role's backends are terminated FIRST: an operator
-- transaction can hold locks that block the restriction itself (its own
-- `ALTER ROLE ... PASSWORD` locks the same pg_authid tuple; `LOCK TABLE` and row
-- locks block the REVOKEs), and locks live only as long as their sessions.
-- (2) The restriction is plain AUTOCOMMITTED statements under a short
-- lock_timeout with ON_ERROR_STOP off (a timeout must not abort the script),
-- retried for three unrolled rounds (psql has no loop) until a `\gset` check
-- shows the role neutralised: no login, no explicit ACL entry on public's
-- relations or on schema public — exactly the scope the REVOKEs below cover. A
-- stale grant in another schema is not a reason to abort (it is not a lock
-- problem); `DROP OWNED` further down removes it before login returns.
-- Committed before the sweep below, a reconnecting session is refused.
-- (3) If the role still cannot be neutralised the safe state could not be
-- established, and aborting `init` with a clear error is then the right thing.
-- Every bootstrap ends operator sessions: deploys stop the workers first, and an
-- in-flight import rolls back atomically. This runs when this SQL runs, i.e.
-- after the wrapper's variable/secret checks and the preceding migrations.
SELECT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'sapphire_operator'
) AS operator_exists
\gset
\set operator_neutral t
\if :operator_exists
    \set operator_neutral f
    \set ON_ERROR_STOP off
    SET lock_timeout = '2s';
    -- round 1
    \if :operator_neutral
    \else
        SELECT count(pg_catalog.pg_terminate_backend(pid)) AS terminated
        FROM pg_catalog.pg_stat_activity
        WHERE usename = 'sapphire_operator' AND pid <> pg_catalog.pg_backend_pid()
        \gset
        SELECT format('REVOKE %I FROM sapphire_operator', granted.rolname)
        FROM pg_catalog.pg_auth_members am
        JOIN pg_catalog.pg_roles granted ON granted.oid = am.roleid
        JOIN pg_catalog.pg_roles member ON member.oid = am.member
        WHERE member.rolname = 'sapphire_operator'
        \gexec
        ALTER ROLE sapphire_operator NOLOGIN;
        REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM sapphire_operator;
        REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM sapphire_operator;
        REVOKE ALL PRIVILEGES ON SCHEMA public FROM sapphire_operator;
        REVOKE ALL PRIVILEGES ON DATABASE sapphire FROM sapphire_operator;
        REVOKE ALL PRIVILEGES ON DATABASE prefect FROM sapphire_operator;
        SELECT NOT r.rolcanlogin
           AND NOT EXISTS (
                SELECT 1 FROM pg_catalog.pg_class c
                CROSS JOIN LATERAL pg_catalog.aclexplode(c.relacl) a
                WHERE a.grantee = r.oid
                  AND c.relnamespace = 'public'::regnamespace)
           AND NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_namespace n
            CROSS JOIN LATERAL pg_catalog.aclexplode(n.nspacl) a
            WHERE n.nspname = 'public' AND a.grantee = r.oid) AS operator_neutral
        FROM pg_catalog.pg_roles r WHERE r.rolname = 'sapphire_operator'
        \gset
    \endif
    -- round 2
    \if :operator_neutral
    \else
        SELECT count(pg_catalog.pg_terminate_backend(pid)) AS terminated
        FROM pg_catalog.pg_stat_activity
        WHERE usename = 'sapphire_operator' AND pid <> pg_catalog.pg_backend_pid()
        \gset
        SELECT format('REVOKE %I FROM sapphire_operator', granted.rolname)
        FROM pg_catalog.pg_auth_members am
        JOIN pg_catalog.pg_roles granted ON granted.oid = am.roleid
        JOIN pg_catalog.pg_roles member ON member.oid = am.member
        WHERE member.rolname = 'sapphire_operator'
        \gexec
        ALTER ROLE sapphire_operator NOLOGIN;
        REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM sapphire_operator;
        REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM sapphire_operator;
        REVOKE ALL PRIVILEGES ON SCHEMA public FROM sapphire_operator;
        REVOKE ALL PRIVILEGES ON DATABASE sapphire FROM sapphire_operator;
        REVOKE ALL PRIVILEGES ON DATABASE prefect FROM sapphire_operator;
        SELECT NOT r.rolcanlogin
           AND NOT EXISTS (
                SELECT 1 FROM pg_catalog.pg_class c
                CROSS JOIN LATERAL pg_catalog.aclexplode(c.relacl) a
                WHERE a.grantee = r.oid
                  AND c.relnamespace = 'public'::regnamespace)
           AND NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_namespace n
            CROSS JOIN LATERAL pg_catalog.aclexplode(n.nspacl) a
            WHERE n.nspname = 'public' AND a.grantee = r.oid) AS operator_neutral
        FROM pg_catalog.pg_roles r WHERE r.rolname = 'sapphire_operator'
        \gset
    \endif
    -- round 3
    \if :operator_neutral
    \else
        SELECT count(pg_catalog.pg_terminate_backend(pid)) AS terminated
        FROM pg_catalog.pg_stat_activity
        WHERE usename = 'sapphire_operator' AND pid <> pg_catalog.pg_backend_pid()
        \gset
        SELECT format('REVOKE %I FROM sapphire_operator', granted.rolname)
        FROM pg_catalog.pg_auth_members am
        JOIN pg_catalog.pg_roles granted ON granted.oid = am.roleid
        JOIN pg_catalog.pg_roles member ON member.oid = am.member
        WHERE member.rolname = 'sapphire_operator'
        \gexec
        ALTER ROLE sapphire_operator NOLOGIN;
        REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM sapphire_operator;
        REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM sapphire_operator;
        REVOKE ALL PRIVILEGES ON SCHEMA public FROM sapphire_operator;
        REVOKE ALL PRIVILEGES ON DATABASE sapphire FROM sapphire_operator;
        REVOKE ALL PRIVILEGES ON DATABASE prefect FROM sapphire_operator;
        SELECT NOT r.rolcanlogin
           AND NOT EXISTS (
                SELECT 1 FROM pg_catalog.pg_class c
                CROSS JOIN LATERAL pg_catalog.aclexplode(c.relacl) a
                WHERE a.grantee = r.oid
                  AND c.relnamespace = 'public'::regnamespace)
           AND NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_namespace n
            CROSS JOIN LATERAL pg_catalog.aclexplode(n.nspacl) a
            WHERE n.nspname = 'public' AND a.grantee = r.oid) AS operator_neutral
        FROM pg_catalog.pg_roles r WHERE r.rolname = 'sapphire_operator'
        \gset
    \endif
    RESET lock_timeout;
    \set ON_ERROR_STOP on
    \if :operator_neutral
    \else
        DO $$
        BEGIN
            RAISE EXCEPTION 'sapphire_operator could not be neutralised after 3 '
                'rounds (its sessions were terminated; something else holds a '
                'conflicting lock) -- refusing to continue without the safe state';
        END $$;
    \endif
    -- Sweep: terminate, wait (pg_stat_activity is snapshotted once per
    -- transaction, so the snapshot is cleared inside the loop), terminate again,
    -- wait again. A survivor only raises a WARNING: its privileges are already
    -- revoked and committed, so it cannot write, and `init` must not abort here.
    DO $$
    DECLARE
        remaining integer;
        deadline timestamptz;
        started timestamptz := pg_catalog.clock_timestamp();
    BEGIN
        FOR round IN 1..2 LOOP
            PERFORM pg_catalog.pg_terminate_backend(pid)
            FROM pg_catalog.pg_stat_activity
            WHERE usename = 'sapphire_operator'
              AND pid <> pg_catalog.pg_backend_pid();
            deadline := pg_catalog.clock_timestamp() + interval '3 seconds';
            LOOP
                PERFORM pg_catalog.pg_stat_clear_snapshot();
                SELECT count(*) INTO remaining
                FROM pg_catalog.pg_stat_activity
                WHERE usename = 'sapphire_operator'
                  AND pid <> pg_catalog.pg_backend_pid();
                EXIT WHEN remaining = 0
                    OR pg_catalog.clock_timestamp() > deadline;
                PERFORM pg_catalog.pg_sleep(0.05);
            END LOOP;
        END LOOP;
        -- Test scaffolding kept in production SQL: the sweep-timing test parses
        -- this line. It prints a duration only, never a secret.
        RAISE NOTICE 'operator sweep took % ms',
            round(extract(epoch FROM pg_catalog.clock_timestamp() - started) * 1000);
        IF remaining > 0 THEN
            RAISE WARNING 'sapphire_operator: % session(s) survived termination; '
                'their privileges are revoked and committed', remaining;
        END IF;
        -- A persistent LARGE OBJECT is creatable by any login (lo_creat is
        -- PUBLIC) and would abort the preflights below. lo_* stays PUBLIC:
        -- revoking it would change other roles.
        PERFORM pg_catalog.lo_unlink(oid)
        FROM pg_catalog.pg_largeobject_metadata
        WHERE lomowner = 'sapphire_operator'::regrole;
    END $$;
\endif

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
-- Plan 341 T1: the API container also hosts the audited hydrologist operator
-- CLI. Runtime auth only SELECTs these tables; grant/revoke writes are scoped
-- to this role and never to a worker or service token.
GRANT INSERT, UPDATE ON users TO sapphire_api;
GRANT INSERT ON user_external_identities TO sapphire_api;
GRANT INSERT, DELETE ON human_station_grants TO sapphire_api;
GRANT INSERT ON audit_log TO sapphire_api;
-- Plan 341 T2: only the API's verified-human path writes publication decisions.
GRANT INSERT, UPDATE ON forecast_publication_selections TO sapphire_api;
GRANT INSERT ON forecast_publication_decisions, forecast_publication_events TO sapphire_api;
GRANT UPDATE ON forecast_publication_sequence TO sapphire_api;
GRANT INSERT ON pipeline_health TO sapphire_api;
GRANT EXECUTE ON FUNCTION public.lock_publication_grants(uuid,uuid,uuid)
    TO sapphire_api;
GRANT EXECUTE ON FUNCTION public.lock_publication_candidate(uuid)
    TO sapphire_api;

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
GRANT INSERT ON forecast_evidence_blobs, forecast_evidence TO sapphire_worker;
GRANT INSERT ON forecast_preservation_attestations TO sapphire_worker;
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
-- Plan 235 D2c/D3: the append-only publication ledger. INSERT-only, same as
-- the two tables above — publishing (including "marking stale" via an
-- empty-count generation) never needs UPDATE.
GRANT INSERT ON skill_generations TO sapphire_worker;
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
-- Plan 399 T4 / 405: model_artifact_warm_start (migration 0060) is the fine-tune
-- provenance row `train_models_flow` writes right after storing a retrained
-- artifact. INSERT-only, like model_artifact_provenance: the store never
-- UPDATEs or DELETEs it (reads are covered by the blanket SELECT above). Without
-- this line a warm-start retrain trains, stores the artifact, then dies on
-- `permission denied for table model_artifact_warm_start`.
GRANT INSERT ON model_artifact_warm_start TO sapphire_worker;
-- Plan 404 T1: the append-only rejected-forecast record. INSERT-only — the
-- role-independent append-only trigger (migration 0065) already refuses
-- UPDATE/DELETE/TRUNCATE even for the table owner; sapphire_api's blanket
-- SELECT above covers T3's read route, and never gets a write grant here.
GRANT INSERT ON rejected_forecasts TO sapphire_worker;

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
REVOKE SELECT ON access_tokens, access_token_stations,
    users, user_external_identities, human_station_grants FROM sapphire_worker;

-- Plan 341 T2: host backup-health writer. Created without login so existing
-- staging deployments need no new credential. During activation the operator
-- enables LOGIN with a separate host-only password; bootstrap retains LOGIN
-- on subsequent deploys while re-converging the narrow grants below.
SELECT 'CREATE ROLE sapphire_publication_health NOLOGIN NOSUPERUSER NOCREATEDB '
       'NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS'
WHERE NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'sapphire_publication_health'
)
\gexec
ALTER ROLE sapphire_publication_health NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOINHERIT NOREPLICATION NOBYPASSRLS;
SELECT format('REVOKE %I FROM sapphire_publication_health', granted.rolname)
FROM pg_catalog.pg_auth_members am
JOIN pg_catalog.pg_roles granted ON granted.oid = am.roleid
JOIN pg_catalog.pg_roles member ON member.oid = am.member
WHERE member.rolname = 'sapphire_publication_health'
\gexec
-- DROP OWNED removes stale column grants and default ACLs as well as table
-- grants. Refuse to run it if this role unexpectedly owns an object.
DO $$
DECLARE
    owned_objects integer;
BEGIN
    SELECT count(*) INTO owned_objects
    FROM pg_catalog.pg_shdepend sd
    JOIN pg_catalog.pg_roles r ON r.oid = sd.refobjid
    WHERE r.rolname = 'sapphire_publication_health' AND sd.deptype = 'o';
    IF owned_objects > 0 THEN
        RAISE EXCEPTION 'sapphire_publication_health owns % object(s)', owned_objects;
    END IF;
END $$;
DROP OWNED BY sapphire_publication_health;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM sapphire_publication_health;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM sapphire_publication_health;
REVOKE ALL PRIVILEGES ON SCHEMA public FROM sapphire_publication_health;
REVOKE ALL PRIVILEGES ON DATABASE sapphire FROM sapphire_publication_health;
REVOKE ALL PRIVILEGES ON DATABASE prefect FROM sapphire_publication_health;
GRANT CONNECT ON DATABASE sapphire TO sapphire_publication_health;
GRANT USAGE ON SCHEMA public TO sapphire_publication_health;
GRANT SELECT, INSERT, UPDATE ON protected_backup_health TO sapphire_publication_health;
GRANT SELECT ON forecast_preservation_attestations,
    forecast_evidence, forecast_publication_decisions TO sapphire_publication_health;
GRANT SELECT, INSERT ON protected_backup_forecast_proofs TO sapphire_publication_health;

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
    -- Temporary relations (any session's pg_temp_N / pg_toast_temp_N) are
    -- skipped: a role allowed TEMP must not be able to abort this bootstrap
    -- with a scratch table; PERSISTENT RLS tables are still refused.
    SELECT count(*) INTO rls_tables
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relrowsecurity
      AND c.relpersistence <> 't'
      AND n.nspname !~ '^pg_(toast_)?temp_';

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

-- ── sapphire_operator: OWN CONVERGENCE BLOCK (Plan 510) ────────────────────
-- The database-limited identity for the DHM delivery import commands. Three
-- levels of database identity: the owner (deploy: migrations, this script,
-- tenants), the runtime roles above, and this one. It is ALWAYS created
-- NOLOGIN; login is enabled only when the operator overlay supplies a password
-- (`-v operator_password`, set by bootstrap-roles.sh from
-- SAPPHIRE_OPERATOR_DB_PASSWORD_FILE). A deploy without the overlay sets NOLOGIN
-- again; every bootstrap also ends the role's open sessions (first block of
-- this file); docs/standards/cicd.md has the manual equivalent for
-- when `init` itself cannot run.
--
-- Its rows are limited by the database, not by the Python: migration 0067's
-- guard triggers refuse any write outside the delivery. The grants below are
-- given ONLY while every guard trigger exists and is enabled; otherwise this
-- block grants nothing, revokes what the role holds, warns, and lets `init`
-- continue (an aborted `init` would leave the whole stack down).
--
-- Bypass preconditions this design rests on (none is granted here):
--   * DISABLE TRIGGER needs table ownership;
--   * session_replication_role needs superuser or an explicit GRANT SET;
--   * TRUNCATE needs the TRUNCATE privilege (never granted, and guarded);
--   * SET ROLE needs a role membership (all are revoked below);
--   * TEMP is granted to PUBLIC by default and is NOT prevented — the guard
--     schema-qualifies its relations and pins its search_path, so a temporary
--     table cannot influence it.
SELECT 'CREATE ROLE sapphire_operator NOLOGIN NOSUPERUSER NOCREATEDB '
       'NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS'
WHERE NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'sapphire_operator'
)
\gexec
ALTER ROLE sapphire_operator NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT -1 VALID UNTIL 'infinity';
ALTER ROLE sapphire_operator RESET ALL;
ALTER ROLE sapphire_operator IN DATABASE sapphire RESET ALL;
SELECT format('REVOKE %I FROM sapphire_operator', granted.rolname)
FROM pg_catalog.pg_auth_members am
JOIN pg_catalog.pg_roles granted ON granted.oid = am.roleid
JOIN pg_catalog.pg_roles member ON member.oid = am.member
WHERE member.rolname = 'sapphire_operator'
\gexec
-- (The revocations already ran at the top of this file.) Ownership preflight:
-- objects of ANY class in a temporary namespace (resolved generically with
-- pg_identify_object: relations, functions, types, collations, domains, ...) vanish with
-- their (already terminated) session and must never abort `init`; PERSISTENT
-- objects are still refused. pg_shdepend spans the cluster, so the object
-- lookups only apply to rows of THIS database; foreign-database ownerships and
-- shared objects stay countable.
DO $$
DECLARE
    owned_objects integer;
BEGIN
    SELECT count(*) INTO owned_objects
    FROM pg_catalog.pg_shdepend sd
    JOIN pg_catalog.pg_roles r ON r.oid = sd.refobjid
    LEFT JOIN pg_catalog.pg_database d
        ON d.datname = pg_catalog.current_database() AND d.oid = sd.dbid
    LEFT JOIN LATERAL (
        SELECT i.schema
        FROM pg_catalog.pg_identify_object(sd.classid, sd.objid, sd.objsubid) i
        WHERE d.oid IS NOT NULL
    ) o ON true
    WHERE r.rolname = 'sapphire_operator' AND sd.deptype = 'o'
      AND NOT COALESCE(o.schema ~ '^pg_(toast_)?temp_', false);
    IF owned_objects > 0 THEN
        RAISE EXCEPTION 'sapphire_operator owns % object(s)', owned_objects;
    END IF;
END $$;
-- Bounded, so a lock held by anything that survived cannot hang the deploy.
SET lock_timeout = '10s';
DROP OWNED BY sapphire_operator;
RESET lock_timeout;
GRANT CONNECT ON DATABASE sapphire TO sapphire_operator;
GRANT USAGE ON SCHEMA public TO sapphire_operator;

-- Every guard trigger of migration 0067, by table, name, function and
-- pg_trigger.tgtype (BEFORE=2, ROW=1, INSERT=4, DELETE=8, UPDATE=16,
-- TRUNCATE=32), present AND enabled ('O' origin, 'A' always; a DISABLE
-- TRIGGER leaves the row present with 'D'), and carrying EXACTLY the WHEN
-- predicate `session_user = 'sapphire_operator'` (a missing, `false` or
-- wrong-role condition is a broken guard).
SELECT count(*) = 10 AS operator_guard_ok
FROM (VALUES
    ('observations', 'trg_observations_operator_guard_insert',
        'operator_guard_delivery_row', 7),
    ('observations', 'trg_observations_operator_guard_update',
        'operator_guard_delivery_row', 19),
    ('observations', 'trg_observations_operator_guard_delete',
        'operator_guard_delivery_row', 11),
    ('rating_curves', 'trg_rating_curves_operator_guard_insert',
        'operator_guard_delivery_row', 7),
    ('rating_curves', 'trg_rating_curves_operator_guard_update',
        'operator_guard_delivery_row', 19),
    ('rating_curves', 'trg_rating_curves_operator_guard_delete',
        'operator_guard_delivery_row', 11),
    ('stations', 'trg_stations_operator_guard_insert',
        'operator_guard_station_insert', 7),
    ('observations', 'trg_observations_operator_guard_truncate',
        'operator_guard_truncate', 34),
    ('rating_curves', 'trg_rating_curves_operator_guard_truncate',
        'operator_guard_truncate', 34),
    ('stations', 'trg_stations_operator_guard_truncate',
        'operator_guard_truncate', 34)
) AS expected(rel, trigger_name, function_name, trigger_type)
JOIN pg_catalog.pg_trigger t
    ON t.tgname = expected.trigger_name
   AND t.tgrelid = to_regclass('public.' || expected.rel)
   AND NOT t.tgisinternal
   AND t.tgenabled IN ('O', 'A')
   AND pg_catalog.pg_get_expr(t.tgqual, t.tgrelid)
       = '(SESSION_USER = ''sapphire_operator''::name)'
   AND t.tgtype = expected.trigger_type
JOIN pg_catalog.pg_proc p
    ON p.oid = t.tgfoid
   AND p.pronamespace = 'public'::regnamespace
   AND p.proname = expected.function_name
   AND NOT p.prosecdef
\gset

\if :operator_guard_ok
    -- SELECT on `tenants` and `stations` is what the guard's own lookups need
    -- (it runs as the invoker); the operator's reads reach every tenant's
    -- rows — the guard limits integrity, not availability or confidentiality.
    GRANT SELECT ON tenants, stations, rating_curves, observations
        TO sapphire_operator;
    GRANT INSERT ON stations TO sapphire_operator;
    GRANT INSERT, DELETE ON rating_curves TO sapphire_operator;
    GRANT INSERT, UPDATE, DELETE ON observations TO sapphire_operator;
    -- INSERT only: no SELECT, no UPDATE/DELETE (the append-only trigger of
    -- migration 0046 applies). The sequence privilege is what the INSERT needs
    -- to draw its key; the store drops the implicit RETURNING.
    GRANT INSERT ON audit_log TO sapphire_operator;
    GRANT USAGE ON SEQUENCE audit_log_id_seq TO sapphire_operator;
\else
    DO $$
    BEGIN
        RAISE WARNING 'sapphire_operator: a guard trigger from migration 0067 is '
            'missing or not enabled — the role is granted NO table privileges';
    END $$;
\endif

\if :{?operator_password}
    ALTER ROLE sapphire_operator LOGIN PASSWORD :'operator_password';
\else
    ALTER ROLE sapphire_operator NOLOGIN;
\endif

-- Dormant provisional-discharge boundary: broad SELECT must not expose protected
-- measurements, reference proofs or conversion snapshots. No role can enable it.
REVOKE ALL ON provisional_discharge_permissions, measurement_feed_evidence,
    rating_reference_proofs, provisional_discharges
    FROM PUBLIC, sapphire_api, sapphire_worker, sapphire_operator, sapphire_publication_health;
DO $$
DECLARE t text; r text; cols text;
BEGIN
    FOREACH t IN ARRAY ARRAY['provisional_discharge_permissions', 'measurement_feed_evidence',
                            'rating_reference_proofs', 'provisional_discharges'] LOOP
        SELECT string_agg(quote_ident(attname), ', ') INTO cols FROM pg_attribute
        WHERE attrelid = to_regclass('public.' || t) AND attnum > 0 AND NOT attisdropped;
        FOREACH r IN ARRAY ARRAY['PUBLIC', 'sapphire_api', 'sapphire_worker',
                                'sapphire_operator', 'sapphire_publication_health'] LOOP
            EXECUTE format('REVOKE SELECT (%s), INSERT (%s), UPDATE (%s), REFERENCES (%s) ON public.%I FROM %s',
                           cols, cols, cols, cols, t, CASE WHEN r = 'PUBLIC' THEN r ELSE quote_ident(r) END);
        END LOOP;
    END LOOP;
END $$;
GRANT SELECT (tenant_id, state) ON provisional_discharge_permissions
    TO sapphire_api, sapphire_worker;
