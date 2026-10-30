"""Safe current unit-read disposition; no source payloads or historical values."""

from alembic import op

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE VIEW public.bafu_unit_observation_read_state WITH (security_barrier=true) AS
SELECT o.id AS observation_id,
    EXISTS (SELECT 1 FROM public.bafu_unit_read_holds h
        WHERE h.tenant_id = s.tenant_id AND h.station_id = o.station_id
          AND h.parameter = o.parameter AND h.source = o.source
          AND h.starts_at <= o.timestamp
          AND (h.ends_at IS NULL OR o.timestamp < h.ends_at)
          AND NOT EXISTS (SELECT 1 FROM public.bafu_unit_hold_releases r
              WHERE r.hold_id = h.id AND r.starts_at <= o.timestamp
                AND (r.ends_at IS NULL OR o.timestamp < r.ends_at))) AS held,
    (e.event_id IS NOT NULL OR EXISTS (
        SELECT 1 FROM public.bafu_unit_write_scopes p
        WHERE p.tenant_id = s.tenant_id AND p.station_id = o.station_id
          AND p.parameter = o.parameter AND p.source = o.source
          AND p.starts_at <= o.timestamp
          AND (p.ends_at IS NULL OR o.timestamp < p.ends_at))) AS evidence_required,
    COALESCE(e.observation_id = o.id
        AND e.after_db_sha256 = public.bafu_unit_db_state(o)
        AND e.capture_valid
        AND a.tenant_id = s.tenant_id AND s.network = 'bafu'
        AND a.gauge_code = s.code AND a.lindas_kind = s.station_kind
        AND EXISTS (SELECT 1 FROM public.bafu_unit_write_scopes p
            WHERE p.tenant_id = s.tenant_id AND p.station_id = o.station_id
              AND p.parameter = o.parameter AND p.source = o.source
              AND p.starts_at <= o.timestamp
              AND (p.ends_at IS NULL OR o.timestamp < p.ends_at)
              AND p.registry_sha256 = a.registry_sha256)
        AND NOT EXISTS (SELECT 1 FROM public.bafu_unit_write_scopes p
            WHERE p.tenant_id = s.tenant_id AND p.station_id = o.station_id
              AND p.parameter = o.parameter AND p.source = o.source
              AND p.starts_at <= o.timestamp
              AND (p.ends_at IS NULL OR o.timestamp < p.ends_at)
              AND p.registry_sha256 <> a.registry_sha256), false) AS canonical,
    e.fingerprint AS unit_fingerprint,
    public.bafu_unit_db_state(o) AS state_sha256,
    o.station_id, o.parameter, o.timestamp, o.source
FROM public.observations o
JOIN public.stations s ON s.id = o.station_id
LEFT JOIN LATERAL (
    SELECT latest.* FROM public.bafu_unit_event_reconciliation latest
    WHERE latest.tenant_id = s.tenant_id AND latest.station_id = o.station_id
      AND latest.measurement_time = o.timestamp
      AND latest.parameter = o.parameter AND latest.source = o.source
    ORDER BY latest.event_id DESC LIMIT 1
) e ON true
LEFT JOIN public.bafu_unit_batch_admissions a ON a.batch_id = e.batch_id;
REVOKE ALL ON public.bafu_unit_observation_read_state FROM PUBLIC;
-- Invoker rights. Lock before the fresh READ COMMITTED token lookup so an
-- evidence-only zero conversion cannot race a verdict. No implicit commit.
CREATE FUNCTION public.observation_qc_compare_and_set(
    target uuid, expected_state text, expected_context text,
    verdict text, flags jsonb, version text
) RETURNS boolean LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    -- No legacy token can describe evaluated neighbours. 0078 provides bounded CAS.
    RETURN false;
END $$;
REVOKE ALL ON FUNCTION public.observation_qc_compare_and_set
    (uuid, text, text, text, jsonb, text) FROM PUBLIC;
""")


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION observation_qc_compare_and_set"
        "(uuid, text, text, text, jsonb, text)"
    )
    op.execute("DROP VIEW bafu_unit_observation_read_state")
