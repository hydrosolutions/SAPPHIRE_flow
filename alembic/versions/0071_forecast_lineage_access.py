"""Protect raw consumed-input columns without enabling test output."""

from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
-- Protected forecast lineage: table grants override column revokes.
-- This block commits before later bootstrap preflights; migration execution is atomic.
DO $$
DECLARE t text; r text;
BEGIN
    FOREACH t IN ARRAY ARRAY['forecasts', 'rejected_forecasts'] LOOP
        FOREACH r IN ARRAY ARRAY['PUBLIC', 'sapphire_api', 'sapphire_worker',
                                'sapphire_operator', 'sapphire_publication_health'] LOOP
            IF r = 'PUBLIC' OR EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
                EXECUTE format('REVOKE SELECT ON public.%I FROM %s', t,
                    CASE WHEN r = 'PUBLIC' THEN r ELSE quote_ident(r) END);
                EXECUTE format('REVOKE SELECT (input_lineage) ON public.%I FROM %s', t,
                    CASE WHEN r = 'PUBLIC' THEN r ELSE quote_ident(r) END);
            END IF;
        END LOOP;
    END LOOP;
END $$;
    """)
    op.execute("""
-- Refuse catalog-visible indirect exposure; never mutate unrelated custom objects.
DO $$
DECLARE checked_role record;
BEGIN
    FOR checked_role IN SELECT rolname FROM pg_roles WHERE rolname IN
        ('sapphire_api', 'sapphire_worker', 'sapphire_operator',
            'sapphire_publication_health') LOOP
        IF has_column_privilege(checked_role.rolname, 'public.forecasts',
            'input_lineage', 'SELECT')
           OR has_column_privilege(checked_role.rolname, 'public.rejected_forecasts',
               'input_lineage', 'SELECT') THEN
            RAISE EXCEPTION
                'protected forecast lineage: unexpected inherited read authority';
        END IF;
    END LOOP;
    IF EXISTS (
        WITH RECURSIVE exposed(classid, objid) AS (
            SELECT 'pg_class'::regclass::oid, d.ev_class
            FROM pg_rewrite d JOIN pg_depend p
              ON p.classid = 'pg_rewrite'::regclass AND p.objid = d.oid
            JOIN pg_attribute a ON a.attrelid = p.refobjid
              AND a.attname = 'input_lineage'
            WHERE p.refclassid = 'pg_class'::regclass
              AND p.refobjid IN ('public.forecasts'::regclass,
                  'public.rejected_forecasts'::regclass)
              AND p.refobjsubid IN (0, a.attnum)
            UNION
            SELECT 'pg_class'::regclass::oid, w.ev_class
            FROM exposed e JOIN pg_depend p
              ON p.refclassid = e.classid AND p.refobjid = e.objid
            JOIN pg_rewrite w ON p.classid = 'pg_rewrite'::regclass AND p.objid = w.oid
        )
        SELECT 1 FROM exposed e JOIN pg_class c ON c.oid = e.objid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' OR EXISTS (
            SELECT 1 FROM pg_roles r CROSS JOIN pg_roles runtime_role
            WHERE runtime_role.rolname IN
                ('sapphire_api', 'sapphire_worker', 'sapphire_operator',
                    'sapphire_publication_health')
              AND pg_has_role(runtime_role.oid, r.oid, 'SET')
              AND has_any_column_privilege(r.oid, c.oid, 'SELECT')
        )
    ) THEN
        RAISE EXCEPTION
            'protected forecast lineage: review dependent views before runtime grants';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_proc f JOIN pg_depend d
          ON d.classid = 'pg_proc'::regclass AND d.objid = f.oid
        JOIN pg_attribute a ON a.attrelid = d.refobjid AND a.attname = 'input_lineage'
        WHERE d.refclassid = 'pg_class'::regclass
          AND d.refobjid IN ('public.forecasts'::regclass,
              'public.rejected_forecasts'::regclass)
          AND d.refobjsubid IN (0, a.attnum)
          AND f.prosecdef AND EXISTS (
              SELECT 1 FROM pg_roles r WHERE r.rolname IN
                ('sapphire_api', 'sapphire_worker', 'sapphire_operator',
                    'sapphire_publication_health')
                AND has_function_privilege(r.oid, f.oid, 'EXECUTE')
          )
    ) THEN
        RAISE EXCEPTION
            'protected forecast lineage: review dependent definer functions';
    END IF;
    -- NOINHERIT does not prevent SET ROLE. Check every reachable principal.
    IF EXISTS (
        SELECT 1 FROM pg_roles runtime_role CROSS JOIN pg_roles granted_role
        WHERE runtime_role.rolname IN (
            'sapphire_api', 'sapphire_worker',
            'sapphire_operator', 'sapphire_publication_health')
          AND pg_has_role(runtime_role.oid, granted_role.oid, 'SET')
          AND (has_column_privilege(granted_role.oid,
                'public.forecasts', 'input_lineage', 'SELECT')
            OR has_column_privilege(granted_role.oid,
                'public.rejected_forecasts', 'input_lineage', 'SELECT'))
    ) THEN
        RAISE EXCEPTION 'protected forecast lineage: reachable read authority';
    END IF;
    -- Body-string/dynamic SQL has no reliable pg_depend graph. Refuse unknown
    -- executable application definers rather than claiming a catalog proof.
    IF EXISTS (
        SELECT 1 FROM pg_proc f JOIN pg_namespace n ON n.oid = f.pronamespace
        WHERE f.prosecdef AND n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND f.oid NOT IN (
              'public.lock_publication_grants(uuid,uuid,uuid)'::regprocedure,
              'public.lock_publication_candidate(uuid)'::regprocedure)
          AND NOT EXISTS (SELECT 1 FROM pg_depend extension_dependency
              WHERE extension_dependency.classid = 'pg_proc'::regclass
                AND extension_dependency.objid = f.oid
                AND extension_dependency.deptype = 'e')
          AND EXISTS (SELECT 1 FROM pg_roles runtime_role
              CROSS JOIN pg_roles granted_role
              WHERE runtime_role.rolname IN (
                  'sapphire_api', 'sapphire_worker',
                  'sapphire_operator', 'sapphire_publication_health')
                AND pg_has_role(runtime_role.oid, granted_role.oid, 'SET')
                AND has_function_privilege(granted_role.oid, f.oid, 'EXECUTE'))
    ) THEN
        RAISE EXCEPTION 'protected forecast lineage: unknown definer authority';
    END IF;
END $$;
    """)
    op.execute("""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sapphire_api') THEN
GRANT SELECT (
    "data_use", "id", "station_id", "model_id", "model_artifact_id",
        "combination_strategy", "source_model_ids", "issued_at", "time_step_seconds",
        "nwp_cycle_reference_time", "nwp_cycle_source", "representation", "status",
        "version", "warm_up_source", "warm_up_state_age_hours",
        "observation_staleness_hours", "created_at", "updated_at", "parameter",
        "units", "qc_status", "qc_flags", "input_quality", "input_quality_flags",
        "rating_curve_id"
) ON public.forecasts TO sapphire_api;
GRANT SELECT (
    "data_use", "id", "attempt_id", "station_id", "model_id", "model_artifact_id",
        "group_id", "issued_at", "parameter", "units", "representation",
        "time_step_seconds", "values", "qc_status", "qc_flags", "recorded_at"
) ON public.rejected_forecasts TO sapphire_api;

    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sapphire_worker') THEN
GRANT SELECT (
    "data_use", "id", "station_id", "model_id", "model_artifact_id",
        "combination_strategy", "source_model_ids", "issued_at", "time_step_seconds",
        "nwp_cycle_reference_time", "nwp_cycle_source", "representation", "status",
        "version", "warm_up_source", "warm_up_state_age_hours",
        "observation_staleness_hours", "created_at", "updated_at", "parameter",
        "units", "qc_status", "qc_flags", "input_quality", "input_quality_flags",
        "rating_curve_id"
) ON public.forecasts TO sapphire_worker;
GRANT SELECT (
    "data_use", "id", "attempt_id", "station_id", "model_id", "model_artifact_id",
        "group_id", "issued_at", "parameter", "units", "representation",
        "time_step_seconds", "values", "qc_status", "qc_flags", "recorded_at"
) ON public.rejected_forecasts TO sapphire_worker;

    END IF;
END $$;
    """)


def downgrade() -> None:
    # ACL hardening is intentionally retained. No evidence is deleted and no
    # prior broad SELECT is restored, including on a mixed-class database.
    pass
