"""Native rating originals and isolated immutable derived samples."""

from alembic import op

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""

CREATE TABLE rating_delivery_files (
	id UUID NOT NULL,
	tenant_id UUID NOT NULL,
	station_id UUID NOT NULL,
	delivery_id TEXT NOT NULL,
	delivered_on DATE NOT NULL,
	sha256 TEXT NOT NULL,
	original BYTEA NOT NULL,
	admitted_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(station_id, tenant_id) REFERENCES stations (id, tenant_id),
	CONSTRAINT uq_rating_delivery_file UNIQUE (tenant_id, station_id, delivery_id),
	CONSTRAINT uq_rating_delivery_file_scope UNIQUE (id, station_id),
	CONSTRAINT ck_rating_file_digest CHECK (sha256 = encode(sha256(original), 'hex')),
	CONSTRAINT ck_rating_file_identity CHECK (length(btrim(delivery_id)) > 0)
)

;


CREATE TABLE rating_delivery_curves (
	file_id UUID NOT NULL,
	station_id UUID NOT NULL,
	rating_curve_id UUID NOT NULL,
	PRIMARY KEY (file_id, rating_curve_id),
	FOREIGN KEY(file_id, station_id) REFERENCES rating_delivery_files (id, station_id),
	FOREIGN KEY(rating_curve_id, station_id) REFERENCES rating_curves (id, station_id)
)

;


CREATE TABLE rating_derived_discharge (
	fingerprint TEXT NOT NULL,
	tenant_id UUID NOT NULL,
	station_id UUID NOT NULL,
	observation_id UUID NOT NULL,
	rating_curve_id UUID NOT NULL,
	file_id UUID NOT NULL,
	timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
	discharge FLOAT,
	rejection TEXT,
	content TEXT NOT NULL,
	captured_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (fingerprint),
	FOREIGN KEY(station_id, tenant_id) REFERENCES stations (id, tenant_id),
	FOREIGN KEY(observation_id, station_id) REFERENCES observations (id, station_id),
	FOREIGN KEY(rating_curve_id, station_id) REFERENCES rating_curves (id, station_id),
	FOREIGN KEY(file_id, station_id) REFERENCES rating_delivery_files (id, station_id),
	CONSTRAINT ck_rating_derived_digest CHECK (fingerprint =
        encode(sha256(convert_to(content, 'UTF8')), 'hex')),
	CONSTRAINT ck_rating_derived_result CHECK (
        (discharge IS NULL) <> (rejection IS NULL)),
	CONSTRAINT ck_rating_derived_finite CHECK (discharge >= 0 AND discharge <
        'Infinity'::float8)
)

;
CREATE INDEX ix_rating_derived_station_time ON rating_derived_discharge (station_id,
        timestamp);

CREATE FUNCTION public.native_rating_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    RAISE EXCEPTION 'native rating evidence is immutable';
END $$;
CREATE TRIGGER native_rating_files_immutable BEFORE UPDATE OR DELETE ON
        rating_delivery_files
    FOR EACH ROW EXECUTE FUNCTION public.native_rating_immutable();
CREATE TRIGGER native_rating_curves_immutable BEFORE UPDATE OR DELETE ON
        rating_delivery_curves
    FOR EACH ROW EXECUTE FUNCTION public.native_rating_immutable();
CREATE TRIGGER native_rating_derived_immutable BEFORE UPDATE OR DELETE ON
        rating_derived_discharge
    FOR EACH ROW EXECUTE FUNCTION public.native_rating_immutable();
CREATE FUNCTION public.native_rating_curve_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.rating_delivery_curves WHERE rating_curve_id =
        OLD.id) THEN
        RAISE EXCEPTION 'admitted native rating curve is immutable';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
REVOKE ALL ON FUNCTION public.native_rating_curve_guard() FROM PUBLIC;
CREATE TRIGGER native_rating_curve_guard BEFORE UPDATE OR DELETE ON rating_curves
    FOR EACH ROW EXECUTE FUNCTION public.native_rating_curve_guard();
REVOKE ALL ON rating_delivery_files, rating_delivery_curves,
        rating_derived_discharge FROM PUBLIC;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sapphire_worker') THEN
        GRANT SELECT, INSERT ON rating_delivery_files, rating_delivery_curves TO
        sapphire_worker;
        GRANT SELECT, INSERT ON rating_derived_discharge TO sapphire_worker;
    END IF;
END $$;

ALTER TABLE forecasts DROP CONSTRAINT ck_forecasts_data_use;
ALTER TABLE forecasts ADD CONSTRAINT ck_forecasts_data_use CHECK (data_use IN
        ('standard','expired_rating_test','provisional_rating'));
ALTER TABLE forecasts DROP CONSTRAINT ck_forecasts_input_lineage;
ALTER TABLE forecasts ADD CONSTRAINT ck_forecasts_input_lineage CHECK (
    (data_use = 'standard' AND input_lineage IS NULL) OR
    (data_use IN ('expired_rating_test','provisional_rating') AND input_lineage IS
        NOT NULL));
ALTER TABLE rejected_forecasts DROP CONSTRAINT ck_rejected_forecasts_data_use;
ALTER TABLE rejected_forecasts ADD CONSTRAINT ck_rejected_forecasts_data_use CHECK
        (data_use IN ('standard','expired_rating_test','provisional_rating'));
ALTER TABLE rejected_forecasts DROP CONSTRAINT ck_rejected_forecasts_input_lineage;
ALTER TABLE rejected_forecasts ADD CONSTRAINT ck_rejected_forecasts_input_lineage
        CHECK (
    (data_use = 'standard' AND input_lineage IS NULL) OR
    (data_use IN ('expired_rating_test','provisional_rating') AND input_lineage IS
        NOT NULL));
DROP TRIGGER trg_forecast_input_lineage_insert ON forecasts;
CREATE TRIGGER trg_forecast_input_lineage_insert AFTER INSERT ON forecasts
    FOR EACH ROW WHEN (NEW.data_use <> 'provisional_rating')
    EXECUTE FUNCTION public.forecast_input_lineage_insert();
DROP TRIGGER trg_rejected_forecast_lineage_shape ON rejected_forecasts;
CREATE TRIGGER trg_rejected_forecast_lineage_shape AFTER INSERT ON rejected_forecasts
    FOR EACH ROW WHEN (NEW.data_use <> 'provisional_rating')
    EXECUTE FUNCTION public.rejected_forecast_lineage_shape();
CREATE FUNCTION public.native_rating_lineage_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE d jsonb; day jsonb; digest text; tenant uuid;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.station_id IS DISTINCT FROM OLD.station_id OR NEW.model_id IS
        DISTINCT FROM OLD.model_id
            OR NEW.id IS DISTINCT FROM OLD.id
            OR NEW.issued_at IS DISTINCT FROM OLD.issued_at
            OR NEW.model_artifact_id IS DISTINCT FROM OLD.model_artifact_id
            OR NEW.input_lineage IS DISTINCT FROM OLD.input_lineage OR NEW.data_use
        IS DISTINCT FROM OLD.data_use THEN
            RAISE EXCEPTION 'native rating lineage and purpose are immutable';
        END IF;
        IF TG_TABLE_NAME = 'forecasts' THEN
            IF NEW.rating_curve_id IS NOT NULL THEN
                RAISE EXCEPTION 'native rating source curves belong only in lineage';
            END IF;
        END IF;
        RETURN NEW;
    END IF;
    d := NEW.input_lineage::jsonb;
    SELECT tenant_id INTO tenant FROM public.stations WHERE id = NEW.station_id;
    IF NOT (d->>'kind' = 'provisional_rating'
        AND d->'snapshots' = '[]'::jsonb AND d->'static_attributes' = '[]'::jsonb
        AND d->'provisional_discharge_fingerprints' = '[]'::jsonb
        AND d->'contributor_forecast_ids' = '[]'::jsonb
        AND jsonb_array_length(d->'rating_input'->'days') > 0
        AND (d->'rating_input'->>'issue_time')::timestamptz = NEW.issued_at
        AND (d->'rating_input'->>'artifact_id')::uuid = NEW.model_artifact_id
        AND d->'rating_input'->>'policy' =
        'utc-day-sample-first-mean-measured-precedence-v1') IS TRUE THEN
        RAISE EXCEPTION 'native rating purpose and lineage disagree';
    END IF;
    FOR day IN SELECT value FROM jsonb_array_elements(d->'rating_input'->'days') LOOP
        IF NOT (jsonb_typeof(day) = 'object'
            AND (day->>'day')::timestamptz + interval '24 hours' <= NEW.issued_at
            AND (day->>'day')::timestamptz =
                date_trunc('day', (day->>'day')::timestamptz AT TIME ZONE 'UTC')
                    AT TIME ZONE 'UTC'
            AND jsonb_typeof(day->'fingerprints') = 'array'
            AND jsonb_array_length(day->'fingerprints') > 0) IS TRUE THEN
            RAISE EXCEPTION 'native rating daily input is missing or future';
        END IF;
        FOR digest IN SELECT jsonb_array_elements_text(day->'fingerprints') LOOP
            IF NOT EXISTS (SELECT 1 FROM public.rating_derived_discharge r
                WHERE r.fingerprint = digest AND r.station_id = NEW.station_id
                    AND r.tenant_id = tenant
                    AND r.timestamp >= (day->>'day')::timestamptz
                    AND r.timestamp < (day->>'day')::timestamptz + interval '24 hours')
        THEN
                RAISE EXCEPTION 'native rating input identity or tenant disagreement';
            END IF;
        END LOOP;
    END LOOP;
    IF TG_TABLE_NAME = 'forecasts' THEN
        IF NEW.rating_curve_id IS NOT NULL THEN
            RAISE EXCEPTION 'native rating source curves belong only in lineage';
        END IF;
        INSERT INTO public.forecast_input_stations VALUES (NEW.id, NEW.station_id,
        tenant);
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.native_rating_lineage_guard() FROM PUBLIC;
CREATE TRIGGER native_rating_forecast_lineage AFTER INSERT ON forecasts
    FOR EACH ROW WHEN (NEW.data_use = 'provisional_rating') EXECUTE FUNCTION
        public.native_rating_lineage_guard();
CREATE TRIGGER native_rating_rejected_lineage AFTER INSERT ON rejected_forecasts
    FOR EACH ROW WHEN (NEW.data_use = 'provisional_rating') EXECUTE FUNCTION
        public.native_rating_lineage_guard();
CREATE TRIGGER native_rating_forecast_immutable BEFORE UPDATE ON forecasts
    FOR EACH ROW WHEN (OLD.data_use = 'provisional_rating') EXECUTE FUNCTION
        public.native_rating_lineage_guard();
CREATE TRIGGER native_rating_rejected_immutable BEFORE UPDATE ON rejected_forecasts
    FOR EACH ROW WHEN (OLD.data_use = 'provisional_rating') EXECUTE FUNCTION
        public.native_rating_lineage_guard();
CREATE FUNCTION public.native_rating_lineage(identity uuid, rejected boolean)
        RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT input_lineage FROM public.forecasts
        WHERE NOT rejected AND id = identity AND data_use = 'provisional_rating'
    UNION ALL SELECT input_lineage FROM public.rejected_forecasts
        WHERE rejected AND id = identity AND data_use = 'provisional_rating'
$$;
REVOKE ALL ON FUNCTION public.native_rating_lineage(uuid,boolean) FROM PUBLIC;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sapphire_worker') THEN
        GRANT EXECUTE ON FUNCTION public.native_rating_lineage(uuid,boolean) TO
        sapphire_worker;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sapphire_api') THEN
        GRANT EXECUTE ON FUNCTION public.native_rating_lineage(uuid,boolean) TO
        sapphire_api;
    END IF;
END $$;
        CREATE OR REPLACE FUNCTION public.reject_test_forecast_publication() RETURNS
        trigger
        LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog, public, pg_temp AS $$
        DECLARE ids uuid[];
        BEGIN
            IF TG_TABLE_NAME = 'forecast_publication_selections' THEN
                ids := ARRAY[NEW.selected_forecast_id];
            ELSE
                ids := ARRAY[NEW.forecast_id, NEW.replaced_forecast_id];
            END IF;
            IF EXISTS (SELECT 1 FROM public.forecasts
                WHERE id = ANY(ids) AND data_use <> 'standard') THEN
                RAISE EXCEPTION 'test forecast cannot enter normal publication'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
    """)


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM rating_derived_discharge)
        OR EXISTS (SELECT 1 FROM rating_delivery_files) THEN
        RAISE EXCEPTION 'cannot downgrade surviving native rating evidence';
    END IF;
END $$;

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM forecasts WHERE data_use = 'provisional_rating')
        OR EXISTS (SELECT 1 FROM rejected_forecasts WHERE data_use =
        'provisional_rating') THEN
        RAISE EXCEPTION 'cannot downgrade surviving provisional rating forecasts';
    END IF;
END $$;
DROP TRIGGER native_rating_forecast_lineage ON forecasts;
DROP TRIGGER native_rating_rejected_lineage ON rejected_forecasts;
DROP TRIGGER native_rating_forecast_immutable ON forecasts;
DROP TRIGGER native_rating_rejected_immutable ON rejected_forecasts;
DROP FUNCTION public.native_rating_lineage_guard();
DROP FUNCTION public.native_rating_lineage(uuid,boolean);
DROP TRIGGER trg_forecast_input_lineage_insert ON forecasts;
CREATE TRIGGER trg_forecast_input_lineage_insert AFTER INSERT ON forecasts
    FOR EACH ROW EXECUTE FUNCTION public.forecast_input_lineage_insert();
DROP TRIGGER trg_rejected_forecast_lineage_shape ON rejected_forecasts;
CREATE TRIGGER trg_rejected_forecast_lineage_shape AFTER INSERT ON rejected_forecasts
    FOR EACH ROW EXECUTE FUNCTION public.rejected_forecast_lineage_shape();
ALTER TABLE forecasts DROP CONSTRAINT ck_forecasts_data_use;
ALTER TABLE forecasts ADD CONSTRAINT ck_forecasts_data_use CHECK (data_use IN
        ('standard','expired_rating_test'));
ALTER TABLE forecasts DROP CONSTRAINT ck_forecasts_input_lineage;
ALTER TABLE forecasts ADD CONSTRAINT ck_forecasts_input_lineage CHECK ((data_use =
        'standard' AND input_lineage IS NULL) OR (data_use = 'expired_rating_test'
        AND input_lineage IS NOT NULL));
ALTER TABLE rejected_forecasts DROP CONSTRAINT ck_rejected_forecasts_data_use;
ALTER TABLE rejected_forecasts ADD CONSTRAINT ck_rejected_forecasts_data_use CHECK
        (data_use IN ('standard','expired_rating_test'));
ALTER TABLE rejected_forecasts DROP CONSTRAINT ck_rejected_forecasts_input_lineage;
ALTER TABLE rejected_forecasts ADD CONSTRAINT ck_rejected_forecasts_input_lineage
        CHECK ((data_use = 'standard' AND input_lineage IS NULL) OR (data_use =
        'expired_rating_test' AND input_lineage IS NOT NULL));
        CREATE OR REPLACE FUNCTION public.reject_test_forecast_publication() RETURNS
        trigger
        LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog, public, pg_temp AS $$
        DECLARE ids uuid[];
        BEGIN
            IF TG_TABLE_NAME = 'forecast_publication_selections' THEN
                ids := ARRAY[NEW.selected_forecast_id];
            ELSE
                ids := ARRAY[NEW.forecast_id, NEW.replaced_forecast_id];
            END IF;
            IF EXISTS (SELECT 1 FROM public.forecasts
                WHERE id = ANY(ids) AND data_use = 'expired_rating_test') THEN
                RAISE EXCEPTION 'test forecast cannot enter normal publication'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
DROP TRIGGER native_rating_curve_guard ON rating_curves;
DROP FUNCTION public.native_rating_curve_guard();
DROP TABLE rating_derived_discharge;
DROP TABLE rating_delivery_curves;
DROP TABLE rating_delivery_files;
DROP FUNCTION public.native_rating_immutable();
    """)
