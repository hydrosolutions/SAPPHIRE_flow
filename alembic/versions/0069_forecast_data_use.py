"""Class-local forecast identity and dormant immutable consumed-input lineage.

Revision ID: 0069
Revises: 0068
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0069"
down_revision: str | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCTIONS = """
CREATE FUNCTION public.forecast_test_write_refused() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF NEW.data_use = 'expired_rating_test' THEN
        RAISE EXCEPTION
                'test forecast writes are disabled pending deployment isolation';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.forecast_data_use_immutable() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF OLD.data_use IS DISTINCT FROM NEW.data_use
       OR OLD.input_lineage IS DISTINCT FROM NEW.input_lineage
       OR (OLD.data_use = 'expired_rating_test'
            AND OLD.station_id <> NEW.station_id) THEN
        RAISE EXCEPTION 'forecast data use and input lineage are immutable';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.forecast_input_lineage_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE d jsonb; item jsonb; record jsonb; tenant uuid; sid uuid; parent uuid;
    expected text[]; field text; flag jsonb;
BEGIN
    IF NEW.data_use = 'standard' THEN RETURN NEW; END IF;
    d := NEW.input_lineage::jsonb;
    SELECT tenant_id INTO tenant FROM public.stations WHERE id = NEW.station_id;
    IF NOT (jsonb_typeof(d) = 'object'
        AND d ?& ARRAY['snapshots', 'static_attributes',
                      'provisional_discharge_fingerprints',
                      'contributor_forecast_ids', 'transformation_versions']
        AND (d - ARRAY['snapshots', 'static_attributes',
                      'provisional_discharge_fingerprints',
                      'contributor_forecast_ids', 'transformation_versions'])
            = '{}'::jsonb
        AND jsonb_typeof(d->'snapshots') = 'array'
        AND jsonb_typeof(d->'static_attributes') = 'array'
        AND jsonb_typeof(d->'provisional_discharge_fingerprints') = 'array'
        AND jsonb_typeof(d->'contributor_forecast_ids') = 'array'
        AND jsonb_typeof(d->'transformation_versions') = 'array'
        AND jsonb_array_length(d->'transformation_versions') > 0
        AND jsonb_array_length(d->'snapshots') +
            jsonb_array_length(d->'static_attributes') +
            jsonb_array_length(d->'provisional_discharge_fingerprints') +
            jsonb_array_length(d->'contributor_forecast_ids') > 0) IS TRUE THEN
        RAISE EXCEPTION 'forecast input lineage is missing or malformed';
    END IF;
    FOR item IN SELECT value FROM
        jsonb_array_elements(d->'transformation_versions') LOOP
        IF jsonb_typeof(item) <> 'string' OR length(btrim(item #>> '{}')) = 0 THEN
            RAISE EXCEPTION 'forecast input transformation version is missing';
        END IF;
    END LOOP;
    -- Include the output station to preserve its tenant identity as well.
    INSERT INTO public.forecast_input_stations VALUES (NEW.id, NEW.station_id, tenant);
    FOR item IN SELECT value FROM jsonb_array_elements(d->'static_attributes') LOOP
        sid := (item->>'station_id')::uuid;
        IF NOT (jsonb_typeof(item) = 'object'
            AND (item - ARRAY['station_id', 'source', 'version', 'values'])
                = '{}'::jsonb
            AND jsonb_typeof(item->'station_id') = 'string'
            AND jsonb_typeof(item->'source') = 'string'
            AND jsonb_typeof(item->'version') = 'string'
            AND length(btrim(item->>'source')) > 0
            AND length(btrim(item->>'version')) > 0
            AND jsonb_typeof(item->'values') = 'object'
            AND item->'values' <> '{}'::jsonb
            AND EXISTS (SELECT 1 FROM public.stations
                WHERE id = sid AND tenant_id = tenant)) IS TRUE THEN
            RAISE EXCEPTION 'forecast static attribute scope or content disagreement';
        END IF;
        IF EXISTS (SELECT 1 FROM jsonb_each(item->'values') e
            WHERE length(btrim(e.key)) = 0
                OR jsonb_typeof(e.value) NOT IN ('number', 'null')
                OR CASE WHEN jsonb_typeof(e.value) = 'number' THEN
                    abs((e.value #>> '{}')::numeric) > 1.7976931348623157e308
                    ELSE false END) THEN
            RAISE EXCEPTION
                'forecast static attribute values must be numeric or missing';
        END IF;
        INSERT INTO public.forecast_input_stations VALUES (NEW.id, sid, tenant)
            ON CONFLICT DO NOTHING;
    END LOOP;
    FOR item IN SELECT value FROM jsonb_array_elements(d->'snapshots') LOOP
        record := (item->>'content')::jsonb;
        sid := (record->>'station_id')::uuid;
        IF NOT (jsonb_typeof(item) = 'object'
            AND (item - ARRAY['kind', 'units', 'content']) = '{}'::jsonb
            AND jsonb_typeof(item->'content') = 'string'
            AND jsonb_typeof(record) = 'object'
            AND item->>'kind' IN ('observation', 'historical_forcing', 
                'weather_forecast')
            AND jsonb_typeof(item->'units') = 'string'
            AND length(btrim(item->>'units')) > 0
            AND jsonb_typeof(record->'parameter') = 'string'
            AND length(btrim(record->>'parameter')) > 0
            AND jsonb_typeof(record->'value') IN ('number', 'null')
            AND record ? 'value'
            AND NOT record ?| ARRAY['created_at', 'captured_at', 'run_id']
            AND EXISTS (SELECT 1 FROM public.stations
                WHERE id = sid AND tenant_id = tenant)) IS TRUE THEN
            RAISE EXCEPTION 'forecast input snapshot scope or content disagreement';
        END IF;
        expected := CASE item->>'kind'
            WHEN 'observation' THEN ARRAY['id', 'station_id', 'timestamp',
                'parameter', 'value', 'source', 'rating_curve_id',
                'rating_curve_correction_version', 'qc_status', 'qc_flags',
                'qc_rule_version', 'delivery_id']
            WHEN 'historical_forcing' THEN ARRAY['station_id', 'source', 'version',
                'valid_time', 'parameter', 'spatial_type', 'band_id',
                'member_id', 'value']
            ELSE ARRAY['id', 'station_id', 'nwp_source', 'cycle_time', 'valid_time',
                'parameter', 'spatial_type', 'band_id', 'member_id', 'value',
                'is_gap', 'gap_status'] END;
        IF NOT (record ?& expected AND record - expected = '{}'::jsonb
            AND jsonb_typeof(record->'station_id') = 'string'
            AND (item->>'kind' = 'historical_forcing'
                OR jsonb_typeof(record->'id') = 'string')) IS TRUE THEN
            RAISE EXCEPTION 'forecast input snapshot fields are malformed';
        END IF;
        IF jsonb_typeof(record->'value') = 'number'
            AND abs((record->>'value')::numeric) > 1.7976931348623157e308 THEN
            RAISE EXCEPTION 'forecast input snapshot value is outside finite range';
        END IF;
        FOREACH field IN ARRAY CASE WHEN item->>'kind' = 'observation'
            THEN ARRAY['timestamp'] WHEN item->>'kind' = 'weather_forecast'
            THEN ARRAY['valid_time', 'cycle_time'] ELSE ARRAY['valid_time'] END LOOP
            IF NOT (jsonb_typeof(record->field) = 'string'
                AND record->>field ~
                    '^[0-9]{4}-[0-9]{2}-[0-9]{2}T.*(Z|[+-][0-9]{2}:[0-9]{2})$')
                IS TRUE THEN
                RAISE EXCEPTION 'forecast input snapshot time is malformed';
            END IF;
            PERFORM (record->>field)::timestamptz;
        END LOOP;
        field := CASE WHEN item->>'kind' = 'weather_forecast'
            THEN 'nwp_source' ELSE 'source' END;
        IF NOT (jsonb_typeof(record->field) = 'string'
            AND length(btrim(record->>field)) > 0) IS TRUE THEN
            RAISE EXCEPTION 'forecast input snapshot source is malformed';
        END IF;
        IF item->>'kind' <> 'observation' THEN
            IF jsonb_typeof(record->'value') <> 'number' THEN
                RAISE EXCEPTION 'forecast input snapshot value must be numeric';
            END IF;
            IF NOT (record->>'spatial_type' IN
                ('point', 'basin_average', 'elevation_band', 'gridded')
                AND ((record->>'spatial_type' = 'elevation_band') =
                     (jsonb_typeof(record->'band_id') <> 'null'))) IS TRUE THEN
                RAISE EXCEPTION 'forecast input snapshot spatial band disagreement';
            END IF;
            FOREACH field IN ARRAY ARRAY['band_id', 'member_id'] LOOP
                IF NOT (jsonb_typeof(record->field) = 'null' OR
                    (jsonb_typeof(record->field) = 'number'
                     AND record->>field ~ '^-?[0-9]+$')) IS TRUE THEN
                    RAISE EXCEPTION 'forecast input snapshot integer is malformed';
                END IF;
            END LOOP;
        END IF;
        IF item->>'kind' = 'historical_forcing' AND NOT (
            jsonb_typeof(record->'version') = 'string'
            AND length(btrim(record->>'version')) > 0) IS TRUE THEN
            RAISE EXCEPTION 'forecast input snapshot version is malformed';
        END IF;
        IF item->>'kind' = 'weather_forecast' AND NOT (
            jsonb_typeof(record->'is_gap') = 'boolean'
            AND (jsonb_typeof(record->'gap_status') = 'null'
                OR record->>'gap_status' IN ('recovered', 'unrecoverable'))
            AND (record->'is_gap' = 'false'::jsonb
                OR jsonb_typeof(record->'gap_status') <> 'null')) IS TRUE THEN
            RAISE EXCEPTION 'forecast input snapshot gap metadata is malformed';
        END IF;
        IF item->>'kind' = 'observation' THEN
            IF NOT (record->>'source' IN ('measured', 'rating_curve_derived',
                'component_derived', 'manual_import')
                AND record->>'qc_status' IN ('raw', 'qc_passed', 'qc_failed',
                    'qc_suspect', 'missing', 'qc_unchecked')
                AND ((record->>'qc_status' = 'missing') =
                    (jsonb_typeof(record->'value') = 'null'))
                AND jsonb_typeof(record->'qc_flags') = 'array'
                AND jsonb_typeof(record->'rating_curve_id') IN ('null', 'string'))
                IS TRUE THEN
                RAISE EXCEPTION 'forecast input snapshot QC metadata is malformed';
            END IF;
            FOREACH field IN ARRAY ARRAY['rating_curve_correction_version',
                'qc_rule_version', 'delivery_id'] LOOP
                IF NOT (jsonb_typeof(record->field) = 'null' OR
                    (jsonb_typeof(record->field) = 'string'
                     AND length(btrim(record->>field)) > 0)) IS TRUE THEN
                    RAISE EXCEPTION 'forecast input snapshot metadata is malformed';
                END IF;
            END LOOP;
            FOR flag IN SELECT value FROM
                jsonb_array_elements(record->'qc_flags') LOOP
                IF NOT (jsonb_typeof(flag) = 'object'
                    AND flag ?& ARRAY['rule_id', 'rule_version', 'status', 'detail']
                    AND flag - ARRAY['rule_id', 'rule_version', 'status', 'detail']
                        = '{}'::jsonb
                    AND jsonb_typeof(flag->'rule_id') = 'string'
                    AND length(btrim(flag->>'rule_id')) > 0
                    AND jsonb_typeof(flag->'rule_version') = 'string'
                    AND length(btrim(flag->>'rule_version')) > 0
                    AND flag->>'status' IN ('qc_passed', 'qc_failed', 'qc_suspect',
                        'qc_unchecked')
                    AND jsonb_typeof(flag->'detail') IN ('null', 'string')) IS TRUE THEN
                    RAISE EXCEPTION 'forecast input snapshot QC flag is malformed';
                END IF;
            END LOOP;
        END IF;
        IF item->>'kind' = 'observation' THEN
            IF NOT EXISTS (SELECT 1 FROM public.observations
                WHERE id = (record->>'id')::uuid AND station_id = sid
                    AND parameter = record->>'parameter'
                    AND timestamp = (record->>'timestamp')::timestamptz
                    AND source = record->>'source') THEN
                RAISE EXCEPTION 'forecast observation input identity disagreement';
            END IF;
        ELSIF item->>'kind' = 'weather_forecast' THEN
            IF NOT EXISTS (SELECT 1 FROM public.weather_forecasts
                WHERE id = (record->>'id')::uuid AND station_id = sid
                    AND parameter = record->>'parameter'
                    AND nwp_source = record->>'nwp_source'
                    AND valid_time = (record->>'valid_time')::timestamptz
                    AND cycle_time = (record->>'cycle_time')::timestamptz
                    AND spatial_type = record->>'spatial_type'
                    AND band_id IS NOT DISTINCT FROM (record->>'band_id')::integer
                    AND member_id IS NOT DISTINCT FROM
                        (record->>'member_id')::integer) THEN
                RAISE EXCEPTION 'forecast weather input identity disagreement';
            END IF;
        END IF;
        IF record->>'rating_curve_id' IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM public.rating_curves 
                WHERE id = (record->>'rating_curve_id')::uuid
                AND station_id = sid) THEN
            RAISE EXCEPTION 'forecast input curve belongs to another source station';
        END IF;
        INSERT INTO public.forecast_input_stations VALUES (NEW.id, sid, tenant)
            ON CONFLICT DO NOTHING;
    END LOOP;
    FOR item IN SELECT value FROM
        jsonb_array_elements(d->'provisional_discharge_fingerprints') LOOP
        IF jsonb_typeof(item) <> 'string' THEN
            RAISE EXCEPTION 'forecast provisional identity must be a string';
        END IF;
        SELECT station_id INTO sid FROM public.provisional_discharges
            WHERE fingerprint = item #>> '{}' AND tenant_id = tenant;
        IF sid IS NULL THEN
            RAISE EXCEPTION
                'forecast provisional input identity or tenant disagreement';
        END IF;
        INSERT INTO public.forecast_input_stations VALUES (NEW.id, sid, tenant)
            ON CONFLICT DO NOTHING;
    END LOOP;
    FOR item IN SELECT value FROM
        jsonb_array_elements(d->'contributor_forecast_ids') LOOP
        IF jsonb_typeof(item) <> 'string' THEN
            RAISE EXCEPTION 'forecast contributor identity must be a string';
        END IF;
        parent := (item #>> '{}')::uuid;
        SELECT f.station_id INTO sid FROM public.forecasts f
            JOIN public.stations s ON s.id = f.station_id
            WHERE f.id = parent AND f.id <> NEW.id AND f.data_use = NEW.data_use
                AND s.tenant_id = tenant AND f.input_lineage IS NOT NULL
                AND EXISTS (SELECT 1 FROM public.forecast_evidence e
                WHERE e.forecast_id = f.id);
        IF sid IS NULL THEN
            RAISE EXCEPTION
                'forecast contributor identity, class or tenant disagreement';
        END IF;
        INSERT INTO public.forecast_input_stations VALUES (NEW.id, sid, tenant)
            ON CONFLICT DO NOTHING;
    END LOOP;
    RETURN NEW;
END $$;
"""


def upgrade() -> None:
    op.add_column(
        "forecasts",
        sa.Column("data_use", sa.Text, nullable=False, server_default="standard"),
    )
    op.add_column("forecasts", sa.Column("input_lineage", sa.Text, nullable=True))
    op.create_check_constraint(
        "ck_forecasts_data_use",
        "forecasts",
        "data_use IN ('standard', 'expired_rating_test')",
    )
    op.create_check_constraint(
        "ck_forecasts_input_lineage",
        "forecasts",
        "(data_use = 'standard' AND input_lineage IS NULL) OR "
        "(data_use = 'expired_rating_test' AND input_lineage IS NOT NULL)",
    )
    op.drop_index(
        "uq_forecasts_station_model_issued_param", "forecasts", if_exists=True
    )
    op.create_index(
        "uq_forecasts_station_model_issued_param",
        "forecasts",
        ["station_id", "model_id", "issued_at", "parameter", "data_use"],
        unique=True,
        postgresql_where=sa.text("status <> 'superseded'"),
        if_not_exists=True,
    )
    op.create_table(
        "forecast_input_stations",
        sa.Column(
            "forecast_id",
            UUID(as_uuid=True),
            sa.ForeignKey("forecasts.id"),
            primary_key=True,
        ),
        sa.Column("station_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["station_id", "tenant_id"], ["stations.id", "stations.tenant_id"]
        ),
    )
    op.create_index(
        "ix_forecast_input_stations_station",
        "forecast_input_stations",
        ["station_id"],
        if_not_exists=True,
    )
    op.execute(_FUNCTIONS)
    op.execute(
        "CREATE TRIGGER trg_forecast_test_write_refused BEFORE INSERT "
        "ON public.forecasts "
        "FOR EACH ROW EXECUTE FUNCTION public.forecast_test_write_refused()"
    )
    op.execute(
        "CREATE TRIGGER trg_forecast_data_use_immutable BEFORE UPDATE "
        "ON public.forecasts "
        "FOR EACH ROW EXECUTE FUNCTION public.forecast_data_use_immutable()"
    )
    op.execute(
        "CREATE TRIGGER trg_forecast_input_lineage_insert AFTER INSERT "
        "ON public.forecasts "
        "FOR EACH ROW EXECUTE FUNCTION public.forecast_input_lineage_insert()"
    )
    op.execute(
        "CREATE TRIGGER trg_forecast_input_stations_immutable "
        "BEFORE UPDATE OR DELETE OR TRUNCATE "
        "ON public.forecast_input_stations FOR EACH STATEMENT "
        "EXECUTE FUNCTION public.provisional_immutable()"
    )
    # No write capability is introduced for any runtime/operator role.
    op.execute("REVOKE ALL ON public.forecast_input_stations FROM PUBLIC")
    op.execute("""DO $$ DECLARE r text; BEGIN
        FOREACH r IN ARRAY ARRAY['sapphire_api', 'sapphire_worker', 'sapphire_operator',
                                'sapphire_publication_health'] LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
                EXECUTE format(
                    'REVOKE ALL ON public.forecast_input_stations FROM %I', r);
            END IF;
        END LOOP;
    END $$""")


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM public.forecasts "
            "WHERE data_use <> 'standard') OR EXISTS "
            "(SELECT 1 FROM public.forecast_input_stations)"
        )
    ):
        raise RuntimeError("test forecast lineage exists; downgrade refused")
    for trigger in (
        "trg_forecast_input_lineage_insert",
        "trg_forecast_data_use_immutable",
        "trg_forecast_test_write_refused",
    ):
        op.execute(f"DROP TRIGGER {trigger} ON public.forecasts")
    op.drop_table("forecast_input_stations")
    for function in (
        "forecast_input_lineage_insert",
        "forecast_data_use_immutable",
        "forecast_test_write_refused",
    ):
        op.execute(f"DROP FUNCTION public.{function}()")
    op.drop_index(
        "uq_forecasts_station_model_issued_param", "forecasts", if_exists=True
    )
    op.create_index(
        "uq_forecasts_station_model_issued_param",
        "forecasts",
        ["station_id", "model_id", "issued_at", "parameter"],
        unique=True,
        postgresql_where=sa.text("status <> 'superseded'"),
        if_not_exists=True,
    )
    op.drop_constraint("ck_forecasts_input_lineage", "forecasts")
    op.drop_constraint("ck_forecasts_data_use", "forecasts")
    op.drop_column("forecasts", "input_lineage")
    op.drop_column("forecasts", "data_use")
