"""Empty approved BAFU write scopes, invoker guards and safe projections."""

from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
CREATE TABLE bafu_unit_write_scopes (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    station_id uuid NOT NULL,
    network text NOT NULL CHECK (network = 'bafu'),
    gauge_code text NOT NULL CHECK (gauge_code ~ '^[0-9]{4}$'),
    lindas_kind text NOT NULL CHECK (lindas_kind IN ('river', 'lake')),
    parameter text NOT NULL DEFAULT 'discharge' CHECK (parameter = 'discharge'),
    source text NOT NULL DEFAULT 'measured' CHECK (source = 'measured'),
    starts_at timestamptz NOT NULL,
    ends_at timestamptz,
    registry_sha256 text NOT NULL CHECK (registry_sha256 ~ '^[0-9a-f]{64}$'),
    approval_sha256 text NOT NULL CHECK (approval_sha256 ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    FOREIGN KEY (station_id, tenant_id) REFERENCES stations(id, tenant_id),
    CHECK (ends_at IS NULL OR starts_at < ends_at)
);
CREATE INDEX ix_bafu_write_scope ON bafu_unit_write_scopes(station_id, starts_at);
CREATE TRIGGER bafu_scope_immutable BEFORE UPDATE OR DELETE ON bafu_unit_write_scopes
    FOR EACH ROW EXECUTE FUNCTION bafu_unit_journal_immutable();
CREATE TRIGGER bafu_scope_no_truncate BEFORE TRUNCATE ON bafu_unit_write_scopes
    FOR EACH STATEMENT EXECUTE FUNCTION bafu_unit_journal_immutable();
ALTER TABLE bafu_unit_events ADD COLUMN transaction_id text NOT NULL
    DEFAULT pg_current_xact_id()::text;
ALTER TABLE bafu_unit_batch_receipts ADD COLUMN transaction_id text NOT NULL
    DEFAULT pg_current_xact_id()::text;

-- Database proof v1 is NOT the Python document digest. Normalize timestamps and
-- float bits after document JSONB parsing; native rows use the typed overload.
-- Exclude only QC, never identity/provenance.
CREATE FUNCTION bafu_unit_db_state(state jsonb) RETURNS text
LANGUAGE sql STABLE STRICT SET search_path = pg_catalog, pg_temp AS $$
    SELECT encode(sha256(convert_to(jsonb_build_array(
        (state->>'id')::uuid, (state->>'station_id')::uuid,
        extract(epoch FROM (state->>'timestamp')::timestamptz),
        state->>'parameter', state->>'source',
        encode(float8send(CASE WHEN (state->>'value')::float8 = 0
            THEN 0::float8 ELSE (state->>'value')::float8 END), 'hex'),
        (state->>'rating_curve_id')::uuid,
        state->>'rating_curve_correction_version', state->>'delivery_id'
    )::text, 'UTF8')), 'hex')
$$;
-- Do not serialize float8 through caller-controlled JSON/text output first.
CREATE FUNCTION bafu_unit_db_state(state public.observations) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog, pg_temp AS $$
    SELECT encode(sha256(convert_to(jsonb_build_array(
        state.id, state.station_id, extract(epoch FROM state.timestamp),
        state.parameter, state.source,
        encode(float8send(CASE WHEN state.value = 0
            THEN 0::float8 ELSE state.value END), 'hex'),
        state.rating_curve_id, state.rating_curve_correction_version, state.delivery_id
    )::text, 'UTF8')), 'hex')
$$;
-- Exact existing Python document_digest contract, including ensure_ascii and
-- decimal numeric equivalence. This is distinct from bafu_unit_db_state.
CREATE FUNCTION bafu_unit_canonical_json(doc jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path = pg_catalog, pg_temp AS $$
DECLARE result text; character text; code integer;
BEGIN
    CASE jsonb_typeof(doc)
    WHEN 'object' THEN
        SELECT '{' || COALESCE(string_agg(
            public.bafu_unit_canonical_json(to_jsonb(key)) || ':' ||
            public.bafu_unit_canonical_json(value), ',' ORDER BY key COLLATE "C"), '')
            || '}' INTO result FROM jsonb_each(doc);
    WHEN 'array' THEN
        SELECT '[' || COALESCE(string_agg(public.bafu_unit_canonical_json(value),
            ',' ORDER BY ordinal), '') || ']' INTO result
            FROM jsonb_array_elements(doc) WITH ORDINALITY a(value, ordinal);
    WHEN 'number' THEN
        result := doc::text;
        IF position('.' IN result) > 0 THEN
            result := rtrim(rtrim(result, '0'), '.');
        END IF;
        IF result::numeric = 0 THEN result := '0'; END IF;
    WHEN 'string' THEN
        result := '';
        FOR character IN SELECT regexp_split_to_table(doc::text, '') LOOP
            code := ascii(character);
            IF code > 65535 THEN
                result := result || '\u'
                    || lpad(to_hex(55296 + ((code - 65536) / 1024)), 4, '0')
                    || '\u' || lpad(to_hex(56320 + ((code - 65536) % 1024)), 4, '0');
            ELSIF code > 126 THEN
                result := result || '\u' || lpad(to_hex(code), 4, '0');
            ELSE result := result || character;
            END IF;
        END LOOP;
    ELSE result := doc::text;
    END CASE;
    RETURN result;
END $$;
CREATE FUNCTION bafu_unit_capture_valid(doc jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE STRICT
SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    IF NOT COALESCE(doc->'schema_version' = '1'::jsonb
        AND jsonb_typeof(doc->'source_literals') = 'array'
        AND jsonb_typeof(doc->'measurement_literals') = 'array'
        AND jsonb_typeof(doc->'payload_sha256') = 'string'
        AND doc->>'payload_sha256' ~ '^[0-9a-f]{64}$'
        AND jsonb_typeof(doc->'received_at') = 'string'
        AND jsonb_typeof(doc->'registry_version') = 'string'
        AND jsonb_typeof(doc->'registry_sha256') = 'string'
        AND doc->>'registry_sha256' ~ '^[0-9a-f]{64}$'
        AND jsonb_typeof(doc->'scientific') = 'object', false) THEN
        RETURN false;
    END IF;
    RETURN (SELECT count(*) FROM jsonb_object_keys(doc)) = 8
        AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(doc->'source_literals')
                        WHERE jsonb_typeof(value) <> 'string')
        AND NOT EXISTS (
            SELECT 1 FROM jsonb_array_elements(doc->'measurement_literals')
            WHERE jsonb_typeof(value) <> 'string');
END $$;
CREATE VIEW bafu_unit_event_reconciliation WITH (security_barrier=true) AS
SELECT event_id, batch_id, tenant_id, station_id, measurement_time, parameter,
    source, outcome, fingerprint, observation_id, value_state_sha256, reason,
    transaction_id,
    public.bafu_unit_db_state(before_state) AS before_db_sha256,
    public.bafu_unit_db_state(after_state) AS after_db_sha256,
    capture->'scientific'->>'unit' AS scientific_unit,
    public.bafu_unit_capture_valid(capture) AS capture_valid,
    pg_column_size(e) AS evidence_bytes
FROM bafu_unit_events e;
CREATE VIEW bafu_unit_receipt_reconciliation WITH (security_barrier=true) AS
SELECT batch_id, input_digest, result_digest, transaction_id,
    jsonb_build_object('schema_version', result->'schema_version',
        'input_digest', result->'input_digest',
        'expected_captures', result->'expected_captures',
        'counts', result->'counts', 'event_ids', result->'event_ids') AS summary,
    encode(sha256(convert_to(public.bafu_unit_canonical_json(result), 'UTF8')), 'hex')
        = result_digest AS digest_valid,
    (SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'station_id', s->>'station_id', 'timestamp', s->>'timestamp',
        'parameter', s->>'parameter', 'db_sha256', public.bafu_unit_db_state(s)
    )), '[]'::jsonb) FROM jsonb_array_elements(result->'observations') s) AS states,
    pg_column_size(r) AS evidence_bytes
FROM bafu_unit_batch_receipts r;

CREATE FUNCTION bafu_unit_scope_binding() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'BAFU scope approval requires READ COMMITTED isolation'
            USING ERRCODE = 'PBU01';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'bafu-unit-binding-v1:' || NEW.station_id::text, 0));
    PERFORM 1 FROM public.stations s WHERE s.id = NEW.station_id
        AND s.tenant_id = NEW.tenant_id AND s.network = NEW.network
        AND s.code = NEW.gauge_code AND s.station_kind = NEW.lindas_kind;
    IF NOT FOUND THEN RAISE EXCEPTION 'BAFU scope station binding mismatch'
            USING ERRCODE = 'PBU01'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER bafu_scope_binding BEFORE INSERT ON bafu_unit_write_scopes
    FOR EACH ROW EXECUTE FUNCTION bafu_unit_scope_binding();
CREATE FUNCTION bafu_unit_station_binding() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    IF OLD.network <> 'bafu' THEN RETURN NEW; END IF;
    IF ROW(OLD.id, OLD.tenant_id, OLD.network, OLD.code, OLD.station_kind)
       IS NOT DISTINCT FROM
       ROW(NEW.id, NEW.tenant_id, NEW.network, NEW.code, NEW.station_kind)
       THEN RETURN NEW; END IF;
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'BAFU station binding requires READ COMMITTED isolation'
            USING ERRCODE = 'PBU01';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'bafu-unit-binding-v1:' || OLD.id::text, 0));
    IF EXISTS (SELECT 1 FROM public.bafu_unit_write_scopes
               WHERE station_id = OLD.id)
       OR EXISTS (SELECT 1 FROM public.bafu_unit_batch_admissions
                  WHERE station_id = OLD.id) THEN
        RAISE EXCEPTION 'journalled or approved BAFU station binding is immutable'
            USING ERRCODE = 'PBU01';
    END IF;
    RETURN NEW;
END $$;
-- Observe the final tuple after every BEFORE rewriter; retain UPDATE row locking.
CREATE TRIGGER bafu_station_binding AFTER UPDATE ON stations
    FOR EACH ROW EXECUTE FUNCTION bafu_unit_station_binding();

CREATE FUNCTION bafu_unit_admission_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE prior_batch uuid;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'BAFU admission requires READ COMMITTED isolation'
            USING ERRCODE = 'PBU01';
    END IF;
    -- Join Python's station serialization before allocating the ordering token.
    PERFORM pg_advisory_xact_lock(('x' || substr(encode(sha256(convert_to(
        'bafu-unit-station-v1:' || NEW.tenant_id::text || ':' || NEW.station_id::text,
        'UTF8')), 'hex'), 1, 16))::bit(64)::bigint);
    PERFORM 1 FROM public.stations s WHERE s.id = NEW.station_id
        AND s.tenant_id = NEW.tenant_id AND s.network = 'bafu'
        AND s.code = NEW.gauge_code AND s.station_kind = NEW.lindas_kind FOR SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION 'BAFU admission station binding mismatch'
            USING ERRCODE = 'PBU01'; END IF;
    -- Identity defaults run BEFORE BEFORE INSERT triggers. Allocate again under
    -- the station lock: sequence gaps are harmless, inverted START order is not.
    NEW.sequence := nextval('public.bafu_unit_batch_admissions_sequence_seq');
    IF EXISTS (SELECT 1 FROM public.bafu_unit_batch_admissions a
        WHERE a.tenant_id = NEW.tenant_id AND a.station_id = NEW.station_id
          AND a.disposition = 'admitted' AND a.received_at >= NEW.received_at) THEN
        NEW.disposition := 'rejected';
    END IF;
    -- Deferred checks may already have run via SET CONSTRAINTS. The latest
    -- admitted batch is the only eligible effects owner; do not supersede its
    -- durable transaction markers, even after checks or caller settings reset.
    IF NEW.disposition = 'admitted' THEN
        SELECT batch_id INTO prior_batch FROM public.bafu_unit_batch_admissions
            WHERE tenant_id = NEW.tenant_id AND station_id = NEW.station_id
              AND disposition = 'admitted' ORDER BY sequence DESC LIMIT 1;
        IF EXISTS (SELECT 1 FROM public.bafu_unit_event_reconciliation
                   WHERE batch_id = prior_batch
                     AND transaction_id = pg_current_xact_id()::text)
           OR EXISTS (SELECT 1 FROM public.bafu_unit_receipt_reconciliation
                      WHERE batch_id = prior_batch
                        AND transaction_id = pg_current_xact_id()::text) THEN
            RAISE EXCEPTION 'BAFU START cannot supersede current-transaction effects'
                USING ERRCODE = 'PBU01';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER bafu_admission_guard BEFORE INSERT ON bafu_unit_batch_admissions
    FOR EACH ROW EXECUTE FUNCTION bafu_unit_admission_guard();

CREATE FUNCTION bafu_unit_require_eligible(batch uuid) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'BAFU effects require READ COMMITTED isolation'
            USING ERRCODE = 'PBU01';
    END IF;
    PERFORM pg_advisory_xact_lock(('x' || substr(encode(sha256(convert_to(
        'bafu-unit-station-v1:' || a.tenant_id::text || ':' || a.station_id::text,
        'UTF8')), 'hex'), 1, 16))::bit(64)::bigint)
        FROM public.bafu_unit_batch_admissions a WHERE a.batch_id = batch;
    -- Hold binding stable through commit, without scope/maintenance privileges.
    PERFORM 1 FROM public.stations s JOIN public.bafu_unit_batch_admissions a
        ON s.id = a.station_id WHERE a.batch_id = batch FOR SHARE OF s;
    IF NOT EXISTS (SELECT 1 FROM public.bafu_unit_batch_admissions a
                   JOIN public.stations s ON s.id = a.station_id
                   WHERE a.batch_id = batch AND a.disposition = 'admitted'
                     AND s.tenant_id = a.tenant_id AND s.network = 'bafu'
                     AND s.code = a.gauge_code AND s.station_kind = a.lindas_kind
                     AND NOT EXISTS (
                         SELECT 1 FROM public.bafu_unit_batch_admissions newer
                         WHERE newer.station_id = a.station_id
                           AND newer.tenant_id = a.tenant_id
                           AND newer.disposition = 'admitted'
                           AND newer.sequence > a.sequence))
    THEN RAISE EXCEPTION 'BAFU effects require admitted bound batch'
            USING ERRCODE = 'PBU01'; END IF;
END $$;
CREATE FUNCTION bafu_unit_effect_admission() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    PERFORM public.bafu_unit_require_eligible(NEW.batch_id);
    IF TG_TABLE_NAME = 'bafu_unit_events' THEN
        IF NOT public.bafu_unit_capture_valid(NEW.capture) THEN
            RAISE EXCEPTION 'malformed BAFU capture document'
            USING ERRCODE = 'PBU01';
        END IF;
        IF NEW.fingerprint IS DISTINCT FROM encode(sha256(convert_to(
                public.bafu_unit_canonical_json(NEW.capture->'scientific'),
                'UTF8')), 'hex')
           OR NOT EXISTS (SELECT 1 FROM public.bafu_unit_batch_admissions a
               WHERE a.batch_id = NEW.batch_id
                 AND a.registry_sha256 = NEW.capture->>'registry_sha256'
                 AND a.registry_version = NEW.capture->>'registry_version'
                 AND a.gauge_code = NEW.capture->'scientific'->>'gauge'
                 AND a.lindas_kind = NEW.capture->'scientific'->>'kind')
           OR (NEW.capture->'scientific'->>'measurement_time')::timestamptz
               IS DISTINCT FROM NEW.measurement_time THEN
            RAISE EXCEPTION 'BAFU event capture/batch proof mismatch'
            USING ERRCODE = 'PBU01';
        END IF;
    END IF;
    IF NEW.batch_id::text IS DISTINCT FROM
       current_setting('sapphire.bafu_unit_batch', true) THEN
        RAISE EXCEPTION 'BAFU effects require current batch correlation'
            USING ERRCODE = 'PBU01';
    END IF;
    IF TG_TABLE_NAME = 'bafu_unit_batch_receipts' THEN
        IF NEW.result->>'input_digest' IS DISTINCT FROM NEW.input_digest
           OR NEW.result->>'schema_version' IS DISTINCT FROM '1'
           OR NEW.result_digest IS DISTINCT FROM encode(sha256(convert_to(
               public.bafu_unit_canonical_json(NEW.result), 'UTF8')), 'hex')
           OR jsonb_typeof(NEW.result->'observations') IS DISTINCT FROM 'array'
           OR jsonb_typeof(NEW.result->'event_ids') IS DISTINCT FROM 'array'
           OR jsonb_typeof(NEW.result->'counts') IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'BAFU receipt document mismatch'
            USING ERRCODE = 'PBU01';
        END IF;
        IF (NEW.result->>'expected_captures')::bigint < 1
           OR jsonb_array_length(NEW.result->'event_ids') IS DISTINCT FROM
               (NEW.result->>'expected_captures')::bigint
           OR (SELECT sum(value::text::bigint)
               FROM jsonb_each(NEW.result->'counts')) IS DISTINCT FROM
               (NEW.result->>'expected_captures')::bigint
           OR EXISTS (SELECT 1 FROM jsonb_each(NEW.result->'counts')
                      WHERE value::text::bigint < 0)
           OR (SELECT array_agg(key ORDER BY key)
               FROM jsonb_each(NEW.result->'counts')) IS DISTINCT FROM
               ARRAY['evidence_only', 'inserted', 'quarantined', 'rejected',
                     'restated', 'unchanged']::text[] THEN
            RAISE EXCEPTION 'BAFU receipt counts mismatch'
            USING ERRCODE = 'PBU01';
        END IF;
    END IF;
    NEW.transaction_id := pg_current_xact_id()::text;
    RETURN NEW;
END $$;
CREATE TRIGGER bafu_event_admission BEFORE INSERT ON bafu_unit_events
    FOR EACH ROW EXECUTE FUNCTION bafu_unit_effect_admission();
CREATE TRIGGER bafu_receipt_admission BEFORE INSERT ON bafu_unit_batch_receipts
    FOR EACH ROW EXECUTE FUNCTION bafu_unit_effect_admission();

CREATE FUNCTION bafu_unit_event_complete() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE final_hash text;
BEGIN
    PERFORM public.bafu_unit_require_eligible(NEW.batch_id);
    IF NOT EXISTS (SELECT 1 FROM public.bafu_unit_receipt_reconciliation r
        WHERE r.batch_id = NEW.batch_id AND r.transaction_id = NEW.transaction_id
          AND r.digest_valid
          AND r.summary->'event_ids' @> jsonb_build_array(NEW.event_id))
    THEN RAISE EXCEPTION 'BAFU event requires same-transaction receipt'
            USING ERRCODE = 'PBU01'; END IF;
    IF NEW.observation_id IS NOT NULL THEN
        SELECT public.bafu_unit_db_state(o) INTO final_hash
            FROM public.observations o WHERE o.id = NEW.observation_id
            AND o.station_id = NEW.station_id AND o.timestamp = NEW.measurement_time
            AND o.parameter = NEW.parameter AND o.source = NEW.source
            AND o.value = NEW.canonical_value FOR SHARE;
        IF final_hash IS NULL OR final_hash IS DISTINCT FROM
                public.bafu_unit_db_state(NEW.after_state)
           OR (NEW.after_state->>'id')::uuid IS DISTINCT FROM NEW.observation_id
           OR (NEW.capture->'scientific'->>'canonical_value')::float8
                IS DISTINCT FROM NEW.canonical_value
           OR NEW.value_state_sha256 IS DISTINCT FROM encode(sha256(convert_to(
                public.bafu_unit_canonical_json(NEW.after_state - 'qc_status'
                    - 'qc_flags' - 'qc_rule_version'), 'UTF8')), 'hex') THEN
            RAISE EXCEPTION 'BAFU event final-state proof mismatch'
            USING ERRCODE = 'PBU01';
        END IF;
        IF NEW.outcome = 'evidence_only' AND public.bafu_unit_db_state(NEW.before_state)
            IS DISTINCT FROM final_hash THEN
            RAISE EXCEPTION 'BAFU evidence-only event cannot authorize mutation'
            USING ERRCODE = 'PBU01';
        END IF;
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER bafu_event_complete AFTER INSERT ON bafu_unit_events
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION bafu_unit_event_complete();

CREATE FUNCTION bafu_unit_receipt_complete() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE a record; e record; ref jsonb; state jsonb; final_hash text;
        derived jsonb := '{"evidence_only": 0,"inserted": 0,"quarantined": 0,
            "rejected": 0,"restated": 0,"unchanged": 0}'::jsonb;
        outcome_key text;
BEGIN
    PERFORM public.bafu_unit_require_eligible(NEW.batch_id);
    SELECT * INTO STRICT a FROM public.bafu_unit_batch_admissions
        WHERE batch_id = NEW.batch_id;
    IF NEW.input_digest IS DISTINCT FROM a.input_digest OR
       (SELECT count(DISTINCT value) FROM jsonb_array_elements(NEW.result->'event_ids'))
           <> jsonb_array_length(NEW.result->'event_ids') THEN
        RAISE EXCEPTION 'BAFU receipt duplicate references or input mismatch'
            USING ERRCODE = 'PBU01';
    END IF;
    FOR ref IN SELECT value FROM jsonb_array_elements(NEW.result->'event_ids') LOOP
        SELECT * INTO e FROM public.bafu_unit_event_reconciliation
            WHERE event_id = ref::text::bigint
              AND station_id = a.station_id AND tenant_id = a.tenant_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'BAFU receipt event scope mismatch'
            USING ERRCODE = 'PBU01';
        END IF;
        IF e.batch_id = NEW.batch_id THEN
            IF e.transaction_id IS DISTINCT FROM NEW.transaction_id THEN
                RAISE EXCEPTION 'BAFU receipt event transaction mismatch'
            USING ERRCODE = 'PBU01';
            END IF;
            outcome_key := e.outcome;
        ELSE
            outcome_key := 'unchanged';
            IF EXISTS (SELECT 1 FROM public.bafu_unit_event_reconciliation newer
                WHERE newer.tenant_id = e.tenant_id AND newer.station_id = e.station_id
                  AND newer.measurement_time IS NOT DISTINCT FROM e.measurement_time
                  AND newer.event_id > e.event_id) THEN
                RAISE EXCEPTION 'BAFU receipt unchanged event is not current'
            USING ERRCODE = 'PBU01';
            END IF;
            SELECT public.bafu_unit_db_state(o) INTO final_hash
                FROM public.observations o WHERE o.station_id = e.station_id
                  AND o.timestamp = e.measurement_time
                  AND o.parameter = e.parameter AND o.source = e.source FOR SHARE;
            IF (CASE WHEN e.observation_id IS NULL THEN e.before_db_sha256
                     ELSE e.after_db_sha256 END) IS DISTINCT FROM final_hash THEN
                RAISE EXCEPTION 'BAFU receipt unchanged state mismatch'
            USING ERRCODE = 'PBU01';
            END IF;
        END IF;
        derived := jsonb_set(derived, ARRAY[outcome_key],
            to_jsonb((derived->>outcome_key)::bigint + 1));
    END LOOP;
    IF derived IS DISTINCT FROM NEW.result->'counts' THEN
        RAISE EXCEPTION 'BAFU receipt derived counts mismatch'
            USING ERRCODE = 'PBU01';
    END IF;
    FOR state IN SELECT value FROM jsonb_array_elements(NEW.result->'observations') LOOP
        SELECT public.bafu_unit_db_state(o) INTO final_hash
            FROM public.observations o WHERE o.id = (state->>'id')::uuid
              AND o.station_id = a.station_id
              AND o.parameter <> 'discharge' FOR SHARE;
        IF final_hash IS NULL OR final_hash IS DISTINCT FROM
            public.bafu_unit_db_state(state) THEN
            RAISE EXCEPTION 'BAFU receipt other-parameter final state mismatch'
            USING ERRCODE = 'PBU01';
        END IF;
    END LOOP;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER bafu_receipt_complete AFTER INSERT ON bafu_unit_batch_receipts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION bafu_unit_receipt_complete();

-- INITIALLY DEFERRED is not a commit-only promise: SET CONSTRAINTS can drain
-- checks. Seal only this transaction's receipt at every later row mutation.
CREATE FUNCTION bafu_unit_receipt_observation_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE identity_row public.observations; station_tenant uuid; latest_batch uuid;
        receipt record;
BEGIN
    IF TG_OP = 'UPDATE' AND public.bafu_unit_db_state(OLD::public.observations)
        = public.bafu_unit_db_state(NEW::public.observations) THEN
        RETURN NEW; -- QC-only and signed zero preserve the promised state.
    END IF;
    FOR identity_row IN
        SELECT OLD.* WHERE TG_OP <> 'INSERT'
        UNION ALL SELECT NEW.* WHERE TG_OP <> 'DELETE'
    LOOP
        -- Unrelated DHM/manual imports must not touch private BAFU projections.
        IF identity_row.source <> 'measured' THEN CONTINUE; END IF;
        SELECT tenant_id INTO station_tenant FROM public.stations
            WHERE id = identity_row.station_id AND network = 'bafu';
        IF NOT FOUND THEN CONTINUE; END IF;
        SELECT batch_id INTO latest_batch FROM public.bafu_unit_batch_admissions
            WHERE tenant_id = station_tenant AND station_id = identity_row.station_id
              AND disposition = 'admitted' ORDER BY sequence DESC LIMIT 1;
        SELECT states, summary INTO receipt
            FROM public.bafu_unit_receipt_reconciliation
            WHERE batch_id = latest_batch
              AND transaction_id = pg_current_xact_id()::text;
        IF NOT FOUND THEN CONTINUE; END IF;
        IF EXISTS (SELECT 1 FROM jsonb_array_elements(receipt.states) state
            WHERE (state->>'station_id')::uuid = identity_row.station_id
              AND (state->>'timestamp')::timestamptz = identity_row.timestamp
              AND state->>'parameter' = identity_row.parameter)
           OR EXISTS (
            SELECT 1 FROM jsonb_array_elements(receipt.summary->'event_ids') ref
            JOIN public.bafu_unit_event_reconciliation e
              ON e.event_id = ref::text::bigint
            WHERE e.station_id = identity_row.station_id
              AND e.measurement_time = identity_row.timestamp
              AND e.parameter = identity_row.parameter
              AND e.source = identity_row.source
        ) THEN
            RAISE EXCEPTION 'BAFU mutation invalidates current-transaction receipt'
                USING ERRCODE = 'PBU01';
        END IF;
    END LOOP;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
-- Ordinary AFTER ROW sees final NEW after all BEFORE rewrites; never deferrable.
CREATE TRIGGER bafu_receipt_observation_guard
    AFTER INSERT OR UPDATE OR DELETE ON observations FOR EACH ROW
    EXECUTE FUNCTION bafu_unit_receipt_observation_guard();

CREATE FUNCTION bafu_unit_observation_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
SET extra_float_digits = 3 AS $$
DECLARE old_protected boolean := false; new_protected boolean := false;
        old_hash text; new_hash text; final_hash text; matched bigint;
BEGIN
    -- This branch MUST precede access to all private unit objects (DHM role).
    IF TG_OP <> 'INSERT' AND OLD.parameter = 'discharge' AND OLD.source = 'measured'
       AND EXISTS (SELECT 1 FROM public.stations s
                   WHERE s.id = OLD.station_id AND s.network = 'bafu') THEN
        IF current_setting('transaction_isolation') <> 'read committed' THEN
            RAISE EXCEPTION 'BAFU candidate effects require READ COMMITTED isolation'
                USING ERRCODE = 'PBU01';
        END IF;
        SELECT EXISTS (SELECT 1 FROM public.bafu_unit_write_scopes p
            WHERE p.station_id = OLD.station_id AND p.starts_at <= OLD.timestamp
              AND (p.ends_at IS NULL OR OLD.timestamp < p.ends_at)) INTO old_protected;
    END IF;
    IF TG_OP <> 'DELETE' AND NEW.parameter = 'discharge' AND NEW.source = 'measured'
       AND EXISTS (SELECT 1 FROM public.stations s
                   WHERE s.id = NEW.station_id AND s.network = 'bafu') THEN
        IF current_setting('transaction_isolation') <> 'read committed' THEN
            RAISE EXCEPTION 'BAFU candidate effects require READ COMMITTED isolation'
                USING ERRCODE = 'PBU01';
        END IF;
        SELECT EXISTS (SELECT 1 FROM public.bafu_unit_write_scopes p
            WHERE p.station_id = NEW.station_id AND p.starts_at <= NEW.timestamp
              AND (p.ends_at IS NULL OR NEW.timestamp < p.ends_at)) INTO new_protected;
    END IF;
    IF NOT (old_protected OR new_protected) THEN RETURN NULL; END IF;
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'BAFU protected effects require READ COMMITTED isolation'
            USING ERRCODE = 'PBU01';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'BAFU protected deletion has no D1 operation'
            USING ERRCODE = 'PBU01';
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_object_keys(to_jsonb(NEW)) key
        WHERE key <> ALL(ARRAY['id', 'station_id', 'timestamp', 'parameter',
            'source', 'value', 'rating_curve_id', 'rating_curve_correction_version',
            'delivery_id', 'qc_status', 'qc_flags', 'qc_rule_version', 'created_at']))
    THEN
        RAISE EXCEPTION 'BAFU unsupported observation schema'
            USING ERRCODE = 'PBU01';
    END IF;
    new_hash := public.bafu_unit_db_state(NEW::public.observations);
    IF TG_OP = 'UPDATE' THEN
        old_hash := public.bafu_unit_db_state(OLD::public.observations);
        -- Unsupported columns cannot acquire authorization from a hash that
        -- deliberately omits them. JSONB numeric equality normalizes signed zero.
        IF (to_jsonb(OLD) - ARRAY['id', 'station_id', 'timestamp', 'parameter',
            'source', 'value', 'rating_curve_id', 'rating_curve_correction_version',
            'delivery_id', 'qc_status', 'qc_flags', 'qc_rule_version'])
           IS DISTINCT FROM
           (to_jsonb(NEW) - ARRAY['id', 'station_id', 'timestamp', 'parameter',
            'source', 'value', 'rating_curve_id', 'rating_curve_correction_version',
            'delivery_id', 'qc_status', 'qc_flags', 'qc_rule_version']) THEN
            RAISE EXCEPTION 'BAFU unsupported non-QC metadata change'
            USING ERRCODE = 'PBU01';
        END IF;
        IF old_hash = new_hash THEN RETURN NULL; END IF; -- QC-only; CAS is separate.
        IF ROW(OLD.id, OLD.station_id, OLD.timestamp, OLD.parameter, OLD.source,
               OLD.delivery_id) IS DISTINCT FROM
           ROW(NEW.id, NEW.station_id, NEW.timestamp, NEW.parameter, NEW.source,
               NEW.delivery_id) THEN
            RAISE EXCEPTION 'BAFU protected identity cannot move'
            USING ERRCODE = 'PBU01';
        END IF;
    END IF;
    SELECT public.bafu_unit_db_state(o) INTO final_hash
        FROM public.observations o WHERE o.id = NEW.id;
    IF final_hash IS DISTINCT FROM new_hash THEN
        RAISE EXCEPTION 'BAFU protected final state mismatch'
            USING ERRCODE = 'PBU01';
    END IF;
    SELECT e.event_id INTO matched
        FROM public.bafu_unit_event_reconciliation e
        JOIN public.bafu_unit_batch_admissions a USING (batch_id)
        JOIN public.bafu_unit_receipt_reconciliation r USING (batch_id)
        JOIN public.bafu_unit_write_scopes p ON p.station_id = e.station_id
        JOIN public.stations s ON s.id = e.station_id
        WHERE e.observation_id = NEW.id AND e.station_id = NEW.station_id
          AND e.measurement_time = NEW.timestamp AND e.source = NEW.source
          AND e.parameter = NEW.parameter AND e.tenant_id = s.tenant_id
          AND a.disposition = 'admitted' AND a.input_digest = r.input_digest
          AND NOT EXISTS (SELECT 1 FROM public.bafu_unit_batch_admissions newer
              WHERE newer.station_id = a.station_id AND newer.tenant_id = a.tenant_id
                AND newer.disposition = 'admitted' AND newer.sequence > a.sequence)
          AND r.digest_valid
          AND a.tenant_id = s.tenant_id AND s.network = p.network
          AND s.code = p.gauge_code AND s.station_kind = p.lindas_kind
          AND a.registry_sha256 = p.registry_sha256
          AND p.starts_at <= NEW.timestamp
          AND (p.ends_at IS NULL OR NEW.timestamp < p.ends_at)
          AND e.transaction_id = pg_current_xact_id()::text
          AND r.transaction_id = pg_current_xact_id()::text
          AND e.batch_id::text = current_setting('sapphire.bafu_unit_batch', true)
          AND e.event_id = (SELECT max(latest.event_id)
              FROM public.bafu_unit_event_reconciliation latest
              WHERE latest.station_id = e.station_id
                AND latest.measurement_time = e.measurement_time)
          AND e.capture_valid AND e.after_db_sha256 = new_hash
          AND e.before_db_sha256 IS NOT DISTINCT FROM old_hash
          AND r.summary->'event_ids' @> jsonb_build_array(e.event_id)
          AND ((TG_OP = 'INSERT' AND e.outcome = 'inserted') OR
               (TG_OP = 'UPDATE' AND e.outcome = 'restated'
                AND old_hash = (SELECT prior.after_db_sha256
                    FROM public.bafu_unit_event_reconciliation prior
                    WHERE prior.station_id = e.station_id
                      AND prior.measurement_time = e.measurement_time
                      AND prior.event_id < e.event_id
                    ORDER BY prior.event_id DESC LIMIT 1)))
        ORDER BY e.event_id DESC LIMIT 1;
    IF matched IS NULL THEN
        RAISE EXCEPTION 'BAFU protected mutation lacks current admitted proof'
            USING ERRCODE = 'PBU01';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER bafu_observation_guard
    AFTER INSERT OR UPDATE OR DELETE ON observations
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION bafu_unit_observation_guard();

CREATE OR REPLACE FUNCTION bafu_unit_hold_bounds() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE parent_start timestamptz; parent_end timestamptz;
        event_time timestamptz; event_outcome text;
BEGIN
    IF TG_TABLE_NAME = 'bafu_unit_hold_releases' THEN
        SELECT starts_at, ends_at INTO STRICT parent_start, parent_end
            FROM public.bafu_unit_read_holds WHERE id = NEW.hold_id;
        IF NEW.starts_at < parent_start OR (parent_end IS NOT NULL
           AND (NEW.ends_at IS NULL OR NEW.ends_at > parent_end)) THEN
            RAISE EXCEPTION 'release interval must lie within its named hold'
            USING ERRCODE = 'PBU01';
        END IF;
    ELSIF NEW.origin = 'ingest' THEN
        SELECT measurement_time, outcome INTO STRICT event_time, event_outcome
            FROM public.bafu_unit_event_reconciliation
            WHERE event_id = NEW.event_id
              AND transaction_id = pg_current_xact_id()::text;
        IF NOT EXISTS (
            SELECT 1 FROM public.bafu_unit_event_reconciliation e
            JOIN public.bafu_unit_batch_admissions a USING (batch_id)
            WHERE e.event_id = NEW.event_id
              AND a.registry_sha256 = NEW.registry_sha256) THEN
            RAISE EXCEPTION 'BAFU ingest hold registry mismatch'
            USING ERRCODE = 'PBU01';
        END IF;
        IF event_outcome <> 'quarantined' OR event_time IS NULL
           OR NEW.starts_at <> event_time
           OR NEW.ends_at IS DISTINCT FROM event_time + interval '1 microsecond' THEN
            RAISE EXCEPTION 'ingest hold must cover its quarantined measurement only'
            USING ERRCODE = 'PBU01';
        END IF;
    ELSIF NOT has_column_privilege(current_user,
        'public.bafu_unit_read_holds', 'approval_sha256', 'INSERT') THEN
        RAISE EXCEPTION 'ordinary worker cannot approve maintenance holds'
            USING ERRCODE = 'PBU01';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON bafu_unit_write_scopes, bafu_unit_event_reconciliation,
    bafu_unit_receipt_reconciliation FROM PUBLIC;
""")


def downgrade() -> None:
    raise RuntimeError(
        "BAFU guard downgrade is unsupported; retain guards and stop writers. "
        "Recovery requires a separately reviewed migration."
    )
