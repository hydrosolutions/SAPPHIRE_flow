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
    inner_raw json; inner_data jsonb; point_raw json; point_data jsonb;
    ordered_points jsonb; parent_point jsonb; point_index integer;
    token text; parent_token text; number_value float8; parent_value float8;
    previous_stage float8; stamp timestamptz;
    raw_nodes json[]; source_nodes jsonb[]; node_depths integer[];
    core_nodes boolean[]; stack_top integer; node_raw json; node_source jsonb;
    node_depth integer; node_core boolean; node_type text; child record;
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
        IF assumed THEN
            BEGIN
                inner_raw := (d->'curve'->>'content')::json;
                inner_data := inner_raw::jsonb;
                expected := ARRAY['id','station_id','version','valid_from',
                    'valid_to','points','interpolation','uploaded_by','created_at',
                    'delivery_id','rating_type_label'];
                IF NOT (json_typeof(inner_raw) = 'object'
                    AND inner_data ?& expected AND inner_data - expected = '{}'
                    AND (SELECT count(*) = count(DISTINCT key)
                         FROM json_object_keys(inner_raw) key)) IS TRUE THEN
                    RAISE EXCEPTION 'invalid assumed curve snapshot fields';
                END IF;
                FOREACH field IN ARRAY ARRAY['id','station_id','interpolation',
                    'uploaded_by','delivery_id','rating_type_label'] LOOP
                    IF NOT (inner_data->field = parent->field
                        AND jsonb_typeof(inner_data->field) =
                            jsonb_typeof(parent->field)) IS TRUE THEN
                        RAISE EXCEPTION 'invalid assumed curve snapshot provenance';
                    END IF;
                END LOOP;
                IF NOT (json_typeof(inner_raw->'version') = 'number'
                    AND inner_raw->>'version' ~ '^[1-9][0-9]*$'
                    AND inner_data->'version' = parent->'version') IS TRUE THEN
                    RAISE EXCEPTION 'invalid assumed curve snapshot version';
                END IF;
                FOREACH field IN ARRAY ARRAY['valid_from','valid_to','created_at'] LOOP
                    IF field = 'valid_to' AND parent->field = 'null'::jsonb THEN
                        IF inner_data->field IS DISTINCT FROM 'null'::jsonb THEN
                            RAISE EXCEPTION 'invalid assumed curve snapshot timestamp';
                        END IF;
                        CONTINUE;
                    END IF;
                    stamp := (parent->>field)::timestamptz;
                    IF NOT (isfinite(stamp)
                        AND extract(year FROM stamp) BETWEEN 1 AND 9999
                        AND jsonb_typeof(inner_data->field) = 'string'
                        AND inner_data->>field =
                            to_char(stamp, 'YYYY-MM-DD"T"HH24:MI:SS') ||
                            CASE WHEN extract(microseconds FROM stamp)::bigint
                                % 1000000 = 0 THEN ''
                            ELSE to_char(stamp, '.US') END || '+00:00') IS TRUE THEN
                        RAISE EXCEPTION 'invalid assumed curve snapshot timestamp';
                    END IF;
                END LOOP;
                IF NOT (json_typeof(inner_raw->'points') = 'array'
                    AND json_array_length(inner_raw->'points') > 0
                    AND json_array_length(inner_raw->'points') =
                        jsonb_array_length(parent->'points')) IS TRUE THEN
                    RAISE EXCEPTION 'invalid assumed curve snapshot points';
                END IF;
                SELECT jsonb_agg(p ORDER BY (p->>'water_level')::float8)
                    INTO ordered_points FROM jsonb_array_elements(parent->'points') p;
                previous_stage := '-Infinity'::float8;
                point_index := 0;
                FOR point_raw IN SELECT value
                    FROM json_array_elements(inner_raw->'points') LOOP
                    point_data := point_raw::jsonb;
                    parent_point := ordered_points->point_index;
                    IF NOT (json_typeof(point_raw) = 'object'
                        AND point_data ?& ARRAY['water_level','discharge']) IS TRUE THEN
                        RAISE EXCEPTION 'invalid assumed curve snapshot point fields';
                    END IF;
                    raw_nodes := ARRAY[point_raw];
                    source_nodes := ARRAY[parent_point];
                    node_depths := ARRAY[0]; core_nodes := ARRAY[false];
                    stack_top := 1;
                    WHILE stack_top > 0 LOOP
                        node_raw := raw_nodes[stack_top];
                        node_source := source_nodes[stack_top];
                        node_depth := node_depths[stack_top];
                        node_core := core_nodes[stack_top];
                        stack_top := stack_top - 1;
                        node_type := json_typeof(node_raw);
                        IF node_core AND node_type IS DISTINCT FROM 'number' THEN
                            RAISE EXCEPTION 'assumed curve snapshot core numeric type';
                        END IF;
                        IF node_type IS DISTINCT FROM jsonb_typeof(node_source) THEN
                            RAISE EXCEPTION 'invalid assumed curve snapshot node type';
                        END IF;
                        IF node_type IN ('object','array') AND node_depth > 128 THEN
                            RAISE EXCEPTION
                                'assumed curve snapshot metadata depth limit';
                        END IF;
                        IF node_type = 'object' THEN
                            IF NOT (SELECT count(*) = count(DISTINCT key)
                                FROM json_object_keys(node_raw) key) THEN
                                RAISE EXCEPTION 'assumed curve snapshot duplicate key';
                            END IF;
                            IF (SELECT array_agg(key ORDER BY key)
                                FROM json_object_keys(node_raw) key) IS DISTINCT FROM
                                (SELECT array_agg(key ORDER BY key)
                                FROM jsonb_object_keys(node_source) key) THEN
                                RAISE EXCEPTION
                                    'assumed curve snapshot metadata fields';
                            END IF;
                            FOR child IN SELECT * FROM json_each(node_raw) LOOP
                                stack_top := stack_top + 1;
                                raw_nodes[stack_top] := child.value;
                                source_nodes[stack_top] := node_source->child.key;
                                node_depths[stack_top] := node_depth + 1;
                                core_nodes[stack_top] := node_depth = 0 AND
                                    child.key IN ('water_level','discharge');
                            END LOOP;
                        ELSIF node_type = 'array' THEN
                            IF json_array_length(node_raw) <>
                                jsonb_array_length(node_source) THEN
                                RAISE EXCEPTION 'assumed curve snapshot metadata array';
                            END IF;
                            FOR child IN SELECT value, ordinality
                                FROM json_array_elements(node_raw)
                                WITH ORDINALITY LOOP
                                stack_top := stack_top + 1;
                                raw_nodes[stack_top] := child.value;
                                source_nodes[stack_top] :=
                                    node_source->(child.ordinality::integer - 1);
                                node_depths[stack_top] := node_depth + 1;
                                core_nodes[stack_top] := false;
                            END LOOP;
                        ELSIF node_type = 'number' THEN
                            token := node_raw::text;
                            parent_token := node_source::text;
                            IF (token ~ '[.eE]') <> (parent_token ~ '[.eE]') THEN
                                RAISE EXCEPTION
                                    'invalid assumed curve snapshot numeric type';
                            END IF;
                            IF token ~ '[.eE]' THEN
                                number_value := token::float8;
                                parent_value := parent_token::float8;
                                IF NOT (number_value > '-Infinity'::float8
                                    AND number_value < 'Infinity'::float8
                                    AND number_value = parent_value) THEN
                                    RAISE EXCEPTION
                                        'invalid assumed curve snapshot numeric value';
                                END IF;
                            ELSE
                                IF length(ltrim(token, '-')) > 4300 THEN
                                    RAISE EXCEPTION
                                        'assumed curve snapshot integer digit limit';
                                END IF;
                                IF token::numeric <> parent_token::numeric THEN
                                    RAISE EXCEPTION
                                        'invalid assumed curve snapshot numeric value';
                                END IF;
                            END IF;
                            IF left(token, 1) = '-' AND token::numeric = 0 THEN
                                RAISE EXCEPTION 'assumed curve snapshot negative zero';
                            END IF;
                            IF node_core THEN
                                number_value := token::float8;
                                IF NOT (number_value > '-Infinity'::float8
                                    AND number_value < 'Infinity'::float8) THEN
                                    RAISE EXCEPTION
                                        'assumed curve snapshot core number range';
                                END IF;
                            END IF;
                        ELSIF node_core THEN
                            RAISE EXCEPTION 'assumed curve snapshot core numeric type';
                        ELSIF (node_raw #>> '{}') IS DISTINCT FROM
                              (node_source #>> '{}') THEN
                            RAISE EXCEPTION 'assumed curve snapshot metadata value';
                        END IF;
                    END LOOP;
                    IF (point_raw->>'water_level')::float8 <= previous_stage THEN
                        RAISE EXCEPTION 'invalid assumed curve snapshot stage order';
                    END IF;
                    previous_stage := (point_raw->>'water_level')::float8;
                    point_index := point_index + 1;
                END LOOP;
            EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range
                OR datetime_field_overflow OR invalid_parameter_value
                OR untranslatable_character OR program_limit_exceeded THEN
                RAISE EXCEPTION 'invalid assumed curve snapshot encoding or range';
            END;
        END IF;
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
