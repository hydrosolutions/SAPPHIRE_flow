"""Bounded QC snapshot and transaction-scoped dependency serialization."""

from alembic import op

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
CREATE VIEW public.observation_qc_boundaries WITH (security_barrier=true) AS
SELECT h.station_id, h.parameter, h.source, b.boundary_time
FROM public.bafu_unit_read_holds h
CROSS JOIN LATERAL (
    SELECT h.starts_at AS boundary_time
    UNION SELECT r.ends_at FROM public.bafu_unit_hold_releases r
        WHERE r.hold_id = h.id AND r.ends_at IS NOT NULL
          AND (h.ends_at IS NULL OR r.ends_at < h.ends_at)
) b
WHERE NOT EXISTS (SELECT 1 FROM public.bafu_unit_hold_releases r
    WHERE r.hold_id = h.id AND r.starts_at <= b.boundary_time
      AND (r.ends_at IS NULL OR b.boundary_time < r.ends_at));
REVOKE ALL ON public.observation_qc_boundaries FROM PUBLIC;

CREATE FUNCTION public.observation_qc_window(
    station uuid, param text, starts timestamptz, ends timestamptz, src text
) RETURNS TABLE(observation_id uuid, state_sha256 text, context_sha256 text,
    qc_context_segment bigint, unit_governed boolean,
    qc_state_sha256 text, cohort_size bigint)
LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp AS $$
WITH cohort AS MATERIALIZED (
    SELECT s.observation_id AS id, s.timestamp, s.source,
        s.state_sha256, s.unit_fingerprint,
        s.held, s.evidence_required, s.canonical,
        (NOT s.evidence_required OR s.canonical)
            AND (NOT s.held OR s.canonical) AS eligible
    FROM public.bafu_unit_observation_read_state s
    WHERE s.station_id = station AND s.parameter = param
        AND s.timestamp >= starts AND s.timestamp < ends
        AND (src IS NULL OR s.source = src)
), boundaries AS MATERIALIZED (
    SELECT DISTINCT b.boundary_time
    FROM public.observation_qc_boundaries b
    WHERE b.station_id = station AND b.parameter = param
        AND (src IS NULL OR b.source = src)
        AND b.boundary_time >= starts AND b.boundary_time < ends
        AND NOT EXISTS (SELECT 1 FROM cohort c
            WHERE c.timestamp = b.boundary_time AND c.source = b.source)
), timeline AS (
    SELECT timestamp AS instant, bool_or(NOT eligible) AS boundary FROM cohort
    GROUP BY timestamp
    UNION ALL SELECT boundary_time, true FROM boundaries
), segments AS MATERIALIZED (
    SELECT instant, sum(max(boundary::int)) OVER (ORDER BY instant) AS segment
    FROM timeline GROUP BY instant
), digest AS MATERIALIZED (
    SELECT encode(sha256(convert_to(jsonb_build_array(
        station, param, extract(epoch FROM starts), extract(epoch FROM ends), src,
        (SELECT COALESCE(jsonb_agg(jsonb_build_array(id, state_sha256,
            unit_fingerprint, held, evidence_required, canonical)
            ORDER BY timestamp, source, id), '[]'::jsonb) FROM cohort),
        (SELECT COALESCE(jsonb_agg(extract(epoch FROM boundary_time)
            ORDER BY boundary_time), '[]'::jsonb) FROM boundaries)
    )::text, 'UTF8')), 'hex') AS hash
)
SELECT c.id, c.state_sha256, d.hash, s.segment::bigint,
    c.evidence_required OR c.held,
    encode(sha256(convert_to(jsonb_build_array(
        o.qc_status, o.qc_flags, o.qc_rule_version)::text, 'UTF8')), 'hex'),
    count(*) OVER ()
FROM cohort c JOIN segments s ON s.instant = c.timestamp CROSS JOIN digest d
JOIN public.observations o ON o.id = c.id
WHERE c.eligible
$$;
REVOKE ALL ON FUNCTION public.observation_qc_window
    (uuid, text, timestamptz, timestamptz, text) FROM PUBLIC;

CREATE FUNCTION public.observation_qc_lock(station uuid, param text)
RETURNS void LANGUAGE plpgsql VOLATILE
SET search_path = pg_catalog, pg_temp SET lock_timeout = '5s' AS $$
BEGIN
    IF station IS NULL OR param IS NULL OR btrim(param) = '' THEN
        RAISE EXCEPTION 'QC lock requires station and parameter'
            USING ERRCODE = '22023';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'observation-qc-window-v1:' || station::text || ':' || param, 0));
END $$;
REVOKE ALL ON FUNCTION public.observation_qc_lock(uuid, text) FROM PUBLIC;

-- Immediate AFTER triggers see the final tuple, independent of constraint drains.
-- No private reads for observation mutations (including DHM/manual neighbours).
CREATE FUNCTION public.observation_qc_mutation_lock() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp SET lock_timeout = '5s' AS $$
DECLARE s uuid; p text;
BEGIN
    IF TG_TABLE_NAME = 'bafu_unit_hold_releases' THEN
        SELECT station_id, parameter INTO s, p FROM public.bafu_unit_read_holds
            WHERE id = NEW.hold_id;
        PERFORM public.observation_qc_lock(s, p);
    ELSIF TG_OP = 'UPDATE' THEN
        FOR s, p IN
            SELECT DISTINCT station_id, parameter FROM (VALUES
                (OLD.station_id, OLD.parameter), (NEW.station_id, NEW.parameter)
            ) keys(station_id, parameter) ORDER BY station_id, parameter
        LOOP
            PERFORM public.observation_qc_lock(s, p);
        END LOOP;
    ELSIF TG_OP = 'DELETE' THEN
        PERFORM public.observation_qc_lock(OLD.station_id, OLD.parameter);
    ELSE
        PERFORM public.observation_qc_lock(NEW.station_id, NEW.parameter);
    END IF;
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION public.observation_qc_mutation_lock() FROM PUBLIC;
CREATE TRIGGER observation_qc_mutation_lock AFTER INSERT OR UPDATE OR DELETE
    ON public.observations FOR EACH ROW
    EXECUTE FUNCTION public.observation_qc_mutation_lock();
CREATE TRIGGER observation_qc_mutation_lock AFTER INSERT ON public.bafu_unit_events
    FOR EACH ROW EXECUTE FUNCTION public.observation_qc_mutation_lock();
CREATE TRIGGER observation_qc_mutation_lock AFTER INSERT ON public.bafu_unit_read_holds
    FOR EACH ROW EXECUTE FUNCTION public.observation_qc_mutation_lock();
CREATE TRIGGER observation_qc_mutation_lock AFTER INSERT
    ON public.bafu_unit_hold_releases
    FOR EACH ROW EXECUTE FUNCTION public.observation_qc_mutation_lock();
CREATE TRIGGER observation_qc_mutation_lock AFTER INSERT
    ON public.bafu_unit_write_scopes
    FOR EACH ROW EXECUTE FUNCTION public.observation_qc_mutation_lock();

CREATE FUNCTION public.observation_qc_batch_compare_and_set(
    station uuid, param text, starts timestamptz, ends timestamptz, src text,
    expected_context text, verdicts jsonb
) RETURNS boolean LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp SET lock_timeout = '5s' AS $$
DECLARE current_rows jsonb; updated_count bigint;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'conditional QC requires READ COMMITTED isolation';
    END IF;
    IF station IS NULL OR param IS NULL OR btrim(param) = ''
        OR expected_context IS NULL OR expected_context !~ '^[0-9a-f]{64}$'
        OR starts IS NULL OR ends IS NULL OR starts >= ends
        OR verdicts IS NULL OR jsonb_typeof(verdicts) <> 'array' THEN
        RAISE EXCEPTION 'invalid QC batch descriptor' USING ERRCODE = '22023';
    END IF;
    IF jsonb_array_length(verdicts) = 0 OR EXISTS (
        SELECT 1 FROM jsonb_array_elements(verdicts) v WHERE
            jsonb_typeof(v) <> 'object'
            OR NOT COALESCE(v->>'id' ~
                '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$', false)
            OR NOT COALESCE(v->>'state_sha256' ~ '^[0-9a-f]{64}$', false)
            OR NOT COALESCE(v->>'qc_state_sha256' ~ '^[0-9a-f]{64}$', false)
            OR NOT COALESCE(v->>'status' IN
                ('raw', 'qc_passed', 'qc_suspect', 'qc_failed',
                 'qc_unchecked', 'missing'), false)
            OR NOT (v ? 'flags' AND v ? 'version')
            OR jsonb_typeof(v->'flags') NOT IN ('array', 'null')
            OR jsonb_typeof(v->'version') NOT IN ('string', 'null')
    ) OR (SELECT count(DISTINCT v->>'id') FROM jsonb_array_elements(verdicts) v)
        <> jsonb_array_length(verdicts) THEN
        RAISE EXCEPTION 'invalid QC batch verdicts' USING ERRCODE = '22023';
    END IF;
    PERFORM public.observation_qc_lock(station, param);
    -- Locks survive function return through commit. No caller-resettable flag.
    PERFORM 1 FROM public.observations o
        WHERE o.id IN (SELECT (v->>'id')::uuid FROM jsonb_array_elements(verdicts) v)
        ORDER BY o.id FOR UPDATE;
    SELECT jsonb_agg(to_jsonb(w)) INTO current_rows
        FROM public.observation_qc_window(station, param, starts, ends, src) w;
    IF current_rows IS NULL OR (current_rows->0->>'context_sha256')
        IS DISTINCT FROM expected_context THEN RETURN false; END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(verdicts) v
        LEFT JOIN jsonb_array_elements(current_rows) r
            ON r->>'observation_id' = v->>'id'
            AND r->>'state_sha256' = v->>'state_sha256'
            AND r->>'qc_state_sha256' = v->>'qc_state_sha256'
        WHERE r IS NULL
    ) THEN RETURN false; END IF;
    UPDATE public.observations o SET qc_status = v->>'status',
        qc_flags = NULLIF(v->'flags', 'null'::jsonb), qc_rule_version = v->>'version'
    FROM jsonb_array_elements(verdicts) v WHERE o.id = (v->>'id')::uuid;
    GET DIAGNOSTICS updated_count = ROW_COUNT;
    IF updated_count <> jsonb_array_length(verdicts) OR EXISTS (
        SELECT 1 FROM jsonb_array_elements(verdicts) v
        LEFT JOIN public.observations o ON o.id = (v->>'id')::uuid
        WHERE o.id IS NULL OR o.qc_status IS DISTINCT FROM v->>'status'
            OR o.qc_flags IS DISTINCT FROM NULLIF(v->'flags', 'null'::jsonb)
            OR o.qc_rule_version IS DISTINCT FROM v->>'version'
            OR public.bafu_unit_db_state(o) IS DISTINCT FROM v->>'state_sha256'
    ) THEN
        RAISE EXCEPTION 'QC batch postcondition failed' USING ERRCODE = 'PQC01';
    END IF;
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION public.observation_qc_batch_compare_and_set
    (uuid, text, timestamptz, timestamptz, text, text, jsonb) FROM PUBLIC;
-- Legacy tokens have no evaluated-window descriptor and cannot authorize writes.
CREATE OR REPLACE FUNCTION public.observation_qc_compare_and_set(
    target uuid, expected_state text, expected_context text,
    verdict text, flags jsonb, version text
) RETURNS boolean LANGUAGE sql SET search_path = pg_catalog, pg_temp AS $$
    SELECT false
$$;
""")


def downgrade() -> None:
    raise NotImplementedError(
        "QC serialization downgrade requires a drained deployment"
    )
