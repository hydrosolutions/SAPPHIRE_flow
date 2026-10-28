"""Explicit owner-assumed-zero reference basis; no activation or grants."""

from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None

_UPGRADE_FUNCTIONS = r"""
CREATE OR REPLACE FUNCTION public.provisional_reference_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp
SET timezone = 'UTC' AS $$
DECLARE d jsonb; parent jsonb; assumed boolean := false; field text; expected text[];
    python_whitespace constant text := (
                        U&'\0009\000a\000b\000c\000d\001c\001d\001e' ||
                        U&'\001f\0020\0085\00a0\1680\2000\2001\2002' ||
                        U&'\2003\2004\2005\2006\2007\2008\2009\200a' ||
                        U&'\2028\2029\202f\205f\3000');
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'protected provisional writes require READ COMMITTED';
    END IF;
    d := NEW.content::jsonb;
    IF TG_TABLE_NAME = 'rating_reference_proofs' THEN
        IF session_user IS DISTINCT FROM (
            SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = TG_RELID
        ) THEN
            RAISE EXCEPTION 'protected reference admission requires table owner';
        END IF;
        assumed := NEW.reference_basis = 'owner_assumed_same_zero';
        IF assumed THEN
            expected := ARRAY['id','tenant_id','station_id','rating_curve_id','curve',
                'endpoint','api_station_id','level_unit','curve_unit','basis',
                'level_unit_evidence_reference','level_unit_evidence_sha256',
                'curve_unit_evidence_reference','curve_unit_evidence_sha256',
                'assumed_by','assumed_at','authority_reference','authority_sha256',
                'binding_by','binding_at','binding_reference','binding_sha256'];
            IF NOT (d->>'basis' = 'owner_assumed_same_zero') IS TRUE THEN
                RAISE EXCEPTION 'reference basis disagreement';
            END IF;
            FOREACH field IN ARRAY ARRAY['assumed_at','binding_at'] LOOP
                IF NOT (jsonb_typeof(d->field) = 'string' AND d->>field ~
                    '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{6})?\+00:00$'
                    AND d->>field = to_char((d->>field)::timestamptz,
                        'YYYY-MM-DD"T"HH24:MI:SS') ||
                        CASE WHEN extract(microseconds FROM
                            (d->>field)::timestamptz)::bigint % 1000000 = 0
                        THEN '' ELSE to_char((d->>field)::timestamptz, '.US')
                        END || '+00:00'
                    AND (d->>field)::timestamptz <= clock_timestamp()) IS TRUE THEN
                    RAISE EXCEPTION 'assumed reference requires aware nonfuture time';
                END IF;
            END LOOP;
            IF (d->>'binding_at')::timestamptz < (d->>'assumed_at')::timestamptz THEN
                RAISE EXCEPTION 'reference binding predates owner authority';
            END IF;
            FOREACH field IN ARRAY ARRAY['assumed_by','authority_reference',
                'binding_by','binding_reference','level_unit_evidence_reference',
                'curve_unit_evidence_reference'] LOOP
                IF NOT (jsonb_typeof(d->field) = 'string'
                    AND length(btrim(d->>field, python_whitespace)) > 0) IS TRUE THEN
                    RAISE EXCEPTION 'missing attributed assumption evidence';
                END IF;
            END LOOP;
            FOREACH field IN ARRAY ARRAY['authority_sha256','binding_sha256',
                'level_unit_evidence_sha256','curve_unit_evidence_sha256'] LOOP
                IF NOT (jsonb_typeof(d->field) = 'string'
                    AND d->>field ~ '^[0-9a-f]{64}$') IS TRUE THEN
                    RAISE EXCEPTION 'invalid assumption evidence digest';
                END IF;
            END LOOP;
            IF NOT (jsonb_typeof(d->'api_station_id') = 'number'
                AND (NEW.content::json)->>'api_station_id' ~ '^[1-9][0-9]*$'
                AND translate(d->>'endpoint', python_whitespace, '')
                    = d->>'endpoint') IS TRUE THEN
                RAISE EXCEPTION 'invalid assumption feed identity';
            END IF;
        ELSE
            expected := ARRAY['id','tenant_id','station_id','rating_curve_id','curve',
                'endpoint','api_station_id','level_unit','curve_unit',
                'level_reference','curve_reference','offset_m','evidence_reference',
                'verified_by','verified_at'];
            IF NEW.reference_basis IS DISTINCT FROM 'verified' THEN
                RAISE EXCEPTION 'reference basis disagreement';
            END IF;
        END IF;
        IF NOT (jsonb_typeof(d) = 'object' AND d ?& expected
            AND d - expected = '{}'::jsonb
            AND jsonb_typeof(d->'curve') = 'object'
            AND (d->'curve') - 'content' = '{}'::jsonb
            AND jsonb_typeof(d->'curve'->'content') = 'string') IS TRUE THEN
            RAISE EXCEPTION 'malformed reference basis payload';
        END IF;
    END IF;
    IF NOT (d->>'id' = NEW.id::text AND d->>'tenant_id' = NEW.tenant_id::text
        AND d->>'station_id' = NEW.station_id::text
        AND d->>'endpoint' ~ '^https://[^/?#@:]+(:[0-9]+)?(/[^?#]*)?$'
        AND (d->>'api_station_id')::integer > 0
        AND (assumed OR (length(btrim(d->>'evidence_reference')) > 0
        AND length(btrim(d->>'verified_by')) > 0
        AND (d->>'verified_at')::timestamptz IS NOT NULL))) IS TRUE THEN
        RAISE EXCEPTION 'invalid protected reference identity or evidence';
    END IF;
    IF TG_TABLE_NAME = 'measurement_feed_evidence' THEN
        SELECT to_jsonb(o) INTO parent FROM public.observations o
            WHERE id = NEW.observation_id AND station_id = NEW.station_id FOR SHARE;
        IF NOT (d->>'observation_id' = NEW.observation_id::text
            AND parent->>'source' = 'measured' AND parent->>'parameter' = 'water_level'
            AND parent->>'rating_curve_id' IS NULL
            AND parent->>'rating_curve_correction_version' IS NULL
            AND (parent->>'value')::float8 > '-Infinity'::float8
            AND (parent->>'value')::float8 < 'Infinity'::float8
            AND public.provisional_measurement_content(
                (d->'measurement'->>'content')::jsonb)
                = public.provisional_measurement_content(parent)) IS TRUE THEN
            RAISE EXCEPTION 'feed evidence differs from persisted measurement';
        END IF;
    ELSE
        SELECT to_jsonb(c) INTO parent FROM public.rating_curves c
            WHERE id = NEW.rating_curve_id AND station_id = NEW.station_id FOR SHARE;
        IF NOT (d->>'rating_curve_id' = NEW.rating_curve_id::text
            AND d->>'level_unit' = 'm' AND d->>'curve_unit' = 'm'
            AND (assumed OR (d->>'level_reference' IN ('gauge_zero', 'masl')
            AND d->>'curve_reference' IN ('gauge_zero', 'masl')
            AND (d->>'offset_m')::float8 > '-Infinity'::float8
            AND (d->>'offset_m')::float8 < 'Infinity'::float8))
            AND public.provisional_curve_content((d->'curve'->>'content')::jsonb)
                = public.provisional_curve_content(parent)) IS TRUE THEN
            RAISE EXCEPTION 'reference proof differs from persisted curve/reference';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.provisional_discharge_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp
SET timezone = 'UTC' AS $$
DECLARE d jsonb; o public.observations;
    c public.rating_curves; feed jsonb; proof jsonb; newest uuid;
    basis text; reference_time timestamptz; transform float8; conversion_version text;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'protected provisional writes require READ COMMITTED';
    END IF;
    d := NEW.content::jsonb;
    IF NEW.captured_at > clock_timestamp() THEN
        RAISE EXCEPTION 'provisional capture time is in the future';
    END IF;
    -- Serialize owner disable with append without granting UPDATE for row locks.
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'provisional-discharge:' || NEW.tenant_id::text, 0));
    IF NOT EXISTS (SELECT 1 FROM public.provisional_discharge_permissions
        WHERE tenant_id = NEW.tenant_id AND state = 'enabled') THEN
        RAISE EXCEPTION 'provisional discharge activation is disabled';
    END IF;
    -- This dormant path favours integrity over throughput: a table SHARE lock
    -- excludes concurrent new curve candidates as well as edits to existing ones.
    LOCK TABLE public.rating_curves IN SHARE MODE;
    SELECT * INTO o FROM public.observations WHERE id = NEW.observation_id FOR SHARE;
    SELECT * INTO c FROM public.rating_curves WHERE id = NEW.rating_curve_id FOR SHARE;
    SELECT id INTO newest FROM public.rating_curves WHERE station_id = NEW.station_id
        ORDER BY valid_from DESC, version DESC LIMIT 1;
    SELECT content::jsonb INTO feed FROM public.measurement_feed_evidence
        WHERE id = NEW.feed_evidence_id;
    SELECT content::jsonb, reference_basis INTO proof, basis
        FROM public.rating_reference_proofs
        WHERE id = NEW.reference_proof_id;
    IF basis = 'verified' AND NOT proof ? 'basis' THEN
        reference_time := (proof->>'verified_at')::timestamptz;
        transform := (proof->>'offset_m')::float8;
        conversion_version := 'expired-rating-linear-reference-v1';
    ELSIF basis = 'owner_assumed_same_zero'
        AND proof->>'basis' = 'owner_assumed_same_zero' THEN
        reference_time := (proof->>'binding_at')::timestamptz;
        transform := 0.0;
        conversion_version := 'expired-rating-owner-assumed-zero-v1';
    ELSE
        RAISE EXCEPTION 'reference basis disagreement';
    END IF;
    IF NOT (o.station_id = NEW.station_id AND c.station_id = NEW.station_id
        AND o.source = 'measured' AND o.parameter = 'water_level'
        AND o.qc_status = 'qc_passed' AND length(o.qc_rule_version) > 0
        AND jsonb_array_length(o.qc_flags) > 0
        AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(o.qc_flags) f
                        WHERE f->>'status' IS DISTINCT FROM 'qc_passed')
        AND o.timestamp >= c.valid_from AND o.timestamp <= NEW.captured_at
        AND c.id = newest AND c.valid_from < c.valid_to AND c.valid_to <=
            NEW.captured_at
        AND public.provisional_measurement_content(d->'measurement')
            = public.provisional_measurement_content(to_jsonb(o))
        AND public.provisional_qc_content(d->'qc') =
            public.provisional_qc_content(to_jsonb(o))
        AND public.provisional_curve_content(d->'curve') =
            public.provisional_curve_content(to_jsonb(c))
        AND d->'feed_evidence' = feed AND d->'reference_proof' = proof
        AND public.provisional_measurement_content(
            (feed->'measurement'->>'content')::jsonb)
            = public.provisional_measurement_content(d->'measurement')
        AND public.provisional_curve_content((proof->'curve'->>'content')::jsonb)
            = public.provisional_curve_content(d->'curve')
        AND (feed->>'verified_at')::timestamptz <= NEW.captured_at
        AND reference_time <= NEW.captured_at
        AND feed->>'endpoint' = proof->>'endpoint'
        AND feed->>'api_station_id' = proof->>'api_station_id'
        AND d->>'conversion_version' = conversion_version
        AND (d->>'discharge')::float8 = NEW.discharge
        AND o.value + transform >=
            (SELECT min((p->>'water_level')::float8)
             FROM jsonb_array_elements(c.points) p)
        AND o.value + transform <=
            (SELECT max((p->>'water_level')::float8)
             FROM jsonb_array_elements(c.points) p)
    ) IS TRUE THEN
        RAISE EXCEPTION 'provisional snapshot, QC, curve or reference disagreement';
    END IF;
    RETURN NEW;
END $$;
"""

_DOWNGRADE_FUNCTIONS = r"""
CREATE OR REPLACE FUNCTION public.provisional_reference_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp
SET timezone = 'UTC' AS $$
DECLARE d jsonb; parent jsonb;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'protected provisional writes require READ COMMITTED';
    END IF;
    d := NEW.content::jsonb;
    IF NOT (d->>'id' = NEW.id::text AND d->>'tenant_id' = NEW.tenant_id::text
        AND d->>'station_id' = NEW.station_id::text
        AND d->>'endpoint' ~ '^https://[^/?#@:]+(:[0-9]+)?(/[^?#]*)?$'
        AND (d->>'api_station_id')::integer > 0
        AND length(btrim(d->>'evidence_reference')) > 0
        AND length(btrim(d->>'verified_by')) > 0
        AND (d->>'verified_at')::timestamptz IS NOT NULL) IS TRUE THEN
        RAISE EXCEPTION 'invalid protected reference identity or evidence';
    END IF;
    IF TG_TABLE_NAME = 'measurement_feed_evidence' THEN
        SELECT to_jsonb(o) INTO parent FROM public.observations o
            WHERE id = NEW.observation_id AND station_id = NEW.station_id FOR SHARE;
        IF NOT (d->>'observation_id' = NEW.observation_id::text
            AND parent->>'source' = 'measured' AND parent->>'parameter' = 'water_level'
            AND parent->>'rating_curve_id' IS NULL
            AND parent->>'rating_curve_correction_version' IS NULL
            AND (parent->>'value')::float8 > '-Infinity'::float8
            AND (parent->>'value')::float8 < 'Infinity'::float8
            AND public.provisional_measurement_content(
                (d->'measurement'->>'content')::jsonb)
                = public.provisional_measurement_content(parent)) IS TRUE THEN
            RAISE EXCEPTION 'feed evidence differs from persisted measurement';
        END IF;
    ELSE
        SELECT to_jsonb(c) INTO parent FROM public.rating_curves c
            WHERE id = NEW.rating_curve_id AND station_id = NEW.station_id FOR SHARE;
        IF NOT (d->>'rating_curve_id' = NEW.rating_curve_id::text
            AND d->>'level_unit' = 'm' AND d->>'curve_unit' = 'm'
            AND d->>'level_reference' IN ('gauge_zero', 'masl')
            AND d->>'curve_reference' IN ('gauge_zero', 'masl')
            AND (d->>'offset_m')::float8 > '-Infinity'::float8
            AND (d->>'offset_m')::float8 < 'Infinity'::float8
            AND public.provisional_curve_content((d->'curve'->>'content')::jsonb)
                = public.provisional_curve_content(parent)) IS TRUE THEN
            RAISE EXCEPTION 'reference proof differs from persisted curve/reference';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.provisional_discharge_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp
SET timezone = 'UTC' AS $$
DECLARE d jsonb; o public.observations;
    c public.rating_curves; feed jsonb; proof jsonb; newest uuid;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'protected provisional writes require READ COMMITTED';
    END IF;
    d := NEW.content::jsonb;
    IF NEW.captured_at > clock_timestamp() THEN
        RAISE EXCEPTION 'provisional capture time is in the future';
    END IF;
    -- Serialize owner disable with append without granting UPDATE for row locks.
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'provisional-discharge:' || NEW.tenant_id::text, 0));
    IF NOT EXISTS (SELECT 1 FROM public.provisional_discharge_permissions
        WHERE tenant_id = NEW.tenant_id AND state = 'enabled') THEN
        RAISE EXCEPTION 'provisional discharge activation is disabled';
    END IF;
    -- This dormant path favours integrity over throughput: a table SHARE lock
    -- excludes concurrent new curve candidates as well as edits to existing ones.
    LOCK TABLE public.rating_curves IN SHARE MODE;
    SELECT * INTO o FROM public.observations WHERE id = NEW.observation_id FOR SHARE;
    SELECT * INTO c FROM public.rating_curves WHERE id = NEW.rating_curve_id FOR SHARE;
    SELECT id INTO newest FROM public.rating_curves WHERE station_id = NEW.station_id
        ORDER BY valid_from DESC, version DESC LIMIT 1;
    SELECT content::jsonb INTO feed FROM public.measurement_feed_evidence
        WHERE id = NEW.feed_evidence_id;
    SELECT content::jsonb INTO proof FROM public.rating_reference_proofs
        WHERE id = NEW.reference_proof_id;
    IF NOT (o.station_id = NEW.station_id AND c.station_id = NEW.station_id
        AND o.source = 'measured' AND o.parameter = 'water_level'
        AND o.qc_status = 'qc_passed' AND length(o.qc_rule_version) > 0
        AND jsonb_array_length(o.qc_flags) > 0
        AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(o.qc_flags) f
                        WHERE f->>'status' IS DISTINCT FROM 'qc_passed')
        AND o.timestamp >= c.valid_from AND o.timestamp <= NEW.captured_at
        AND c.id = newest AND c.valid_from < c.valid_to AND c.valid_to <=
            NEW.captured_at
        AND public.provisional_measurement_content(d->'measurement')
            = public.provisional_measurement_content(to_jsonb(o))
        AND public.provisional_qc_content(d->'qc') =
            public.provisional_qc_content(to_jsonb(o))
        AND public.provisional_curve_content(d->'curve') =
            public.provisional_curve_content(to_jsonb(c))
        AND d->'feed_evidence' = feed AND d->'reference_proof' = proof
        AND public.provisional_measurement_content(
            (feed->'measurement'->>'content')::jsonb)
            = public.provisional_measurement_content(d->'measurement')
        AND public.provisional_curve_content((proof->'curve'->>'content')::jsonb)
            = public.provisional_curve_content(d->'curve')
        AND (feed->>'verified_at')::timestamptz <= NEW.captured_at
        AND (proof->>'verified_at')::timestamptz <= NEW.captured_at
        AND feed->>'endpoint' = proof->>'endpoint'
        AND feed->>'api_station_id' = proof->>'api_station_id'
        AND d->>'conversion_version' = 'expired-rating-linear-reference-v1'
        AND (d->>'discharge')::float8 = NEW.discharge
        AND o.value + (proof->>'offset_m')::float8 >=
            (SELECT min((p->>'water_level')::float8)
             FROM jsonb_array_elements(c.points) p)
        AND o.value + (proof->>'offset_m')::float8 <=
            (SELECT max((p->>'water_level')::float8)
             FROM jsonb_array_elements(c.points) p)
    ) IS TRUE THEN
        RAISE EXCEPTION 'provisional snapshot, QC, curve or reference disagreement';
    END IF;
    RETURN NEW;
END $$;
"""


def upgrade() -> None:
    # Temporary default backfills legacy rows without issuing immutable UPDATEs.
    op.execute(
        "ALTER TABLE public.rating_reference_proofs ADD COLUMN "
        "reference_basis text NOT NULL DEFAULT 'verified'"
    )
    op.execute(
        "ALTER TABLE public.rating_reference_proofs "
        "ALTER COLUMN reference_basis DROP DEFAULT"
    )
    op.execute(
        "ALTER TABLE public.rating_reference_proofs "
        "ADD CONSTRAINT ck_rating_reference_basis "
        "CHECK (reference_basis IN ('verified', 'owner_assumed_same_zero'))"
    )
    op.execute(_UPGRADE_FUNCTIONS)


def downgrade() -> None:
    # Block both admission paths before taking the refusal query's snapshot.
    op.execute("LOCK TABLE public.provisional_discharges IN SHARE MODE")
    op.execute("LOCK TABLE public.rating_reference_proofs IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM public.rating_reference_proofs
            WHERE reference_basis = 'owner_assumed_same_zero')
        OR EXISTS (SELECT 1 FROM public.provisional_discharges
            WHERE content::jsonb->'reference_proof'->>'basis'
                = 'owner_assumed_same_zero')
        THEN RAISE EXCEPTION 'assumed reference evidence exists; downgrade refused';
        END IF;
    END $$""")
    op.execute(_DOWNGRADE_FUNCTIONS)
    op.execute("ALTER TABLE public.rating_reference_proofs DROP COLUMN reference_basis")
