"""Dormant protected provisional discharge and reference/feed evidence.

Revision ID: 0068
Revises: 0067
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0068"
down_revision: str | None = "0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

metadata = sa.MetaData()
# Foreign-key targets are existing relations; these are not created here.
sa.Table("tenants", metadata, sa.Column("id", UUID, primary_key=True))
sa.Table("stations", metadata, sa.Column("id", UUID), sa.Column("tenant_id", UUID))
sa.Table("observations", metadata, sa.Column("id", UUID), sa.Column("station_id", UUID))
sa.Table(
    "rating_curves", metadata, sa.Column("id", UUID), sa.Column("station_id", UUID)
)

# Protected, dormant provisional-discharge storage. No ordinary observation joins.
provisional_discharge_permissions = sa.Table(
    "provisional_discharge_permissions",
    metadata,
    sa.Column(
        "tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), primary_key=True
    ),
    sa.Column("state", sa.Text, nullable=False, server_default="disabled"),
    sa.Column("permission_reference", sa.Text, nullable=True),
    sa.Column("inventory_digest", sa.Text, nullable=True),
    sa.CheckConstraint(
        "state IN ('disabled', 'enabled')", name="ck_provisional_permission_state"
    ),
    sa.CheckConstraint(
        "state = 'disabled' OR (length(permission_reference) > 0 "
        "AND inventory_digest ~ '^[0-9a-f]{64}$' "
        "AND permission_reference IS NOT NULL AND inventory_digest IS NOT NULL)",
        name="ck_provisional_permission_evidence",
    ),
)

measurement_feed_evidence = sa.Table(
    "measurement_feed_evidence",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("station_id", UUID(as_uuid=True), nullable=False),
    sa.Column("observation_id", UUID(as_uuid=True), nullable=False),
    sa.Column("content", sa.Text, nullable=False),
    sa.Column("fingerprint", sa.Text, nullable=False, unique=True),
    sa.ForeignKeyConstraint(
        ["station_id", "tenant_id"], ["stations.id", "stations.tenant_id"]
    ),
    sa.ForeignKeyConstraint(
        ["observation_id", "station_id"], ["observations.id", "observations.station_id"]
    ),
    sa.UniqueConstraint(
        "id", "tenant_id", "station_id", "observation_id", name="uq_feed_evidence_scope"
    ),
    sa.CheckConstraint(
        "fingerprint = encode(sha256(convert_to(content, 'UTF8')), 'hex')",
        name="ck_feed_evidence_digest",
    ),
)
sa.Index("ix_feed_evidence_station", measurement_feed_evidence.c.station_id)
sa.Index("ix_feed_evidence_observation", measurement_feed_evidence.c.observation_id)

rating_reference_proofs = sa.Table(
    "rating_reference_proofs",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("station_id", UUID(as_uuid=True), nullable=False),
    sa.Column("rating_curve_id", UUID(as_uuid=True), nullable=False),
    sa.Column("content", sa.Text, nullable=False),
    sa.Column("fingerprint", sa.Text, nullable=False, unique=True),
    sa.ForeignKeyConstraint(
        ["station_id", "tenant_id"], ["stations.id", "stations.tenant_id"]
    ),
    sa.ForeignKeyConstraint(
        ["rating_curve_id", "station_id"],
        ["rating_curves.id", "rating_curves.station_id"],
    ),
    sa.UniqueConstraint(
        "id",
        "tenant_id",
        "station_id",
        "rating_curve_id",
        name="uq_rating_reference_scope",
    ),
    sa.CheckConstraint(
        "fingerprint = encode(sha256(convert_to(content, 'UTF8')), 'hex')",
        name="ck_rating_reference_digest",
    ),
)
sa.Index("ix_rating_reference_station", rating_reference_proofs.c.station_id)
sa.Index("ix_rating_reference_curve", rating_reference_proofs.c.rating_curve_id)

provisional_discharges = sa.Table(
    "provisional_discharges",
    metadata,
    sa.Column("fingerprint", sa.Text, primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("station_id", UUID(as_uuid=True), nullable=False),
    sa.Column("observation_id", UUID(as_uuid=True), nullable=False),
    sa.Column("rating_curve_id", UUID(as_uuid=True), nullable=False),
    sa.Column("feed_evidence_id", UUID(as_uuid=True), nullable=False),
    sa.Column("reference_proof_id", UUID(as_uuid=True), nullable=False),
    sa.Column("discharge", sa.Float, nullable=False),
    sa.Column("content", sa.Text, nullable=False),
    sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(
        ["station_id", "tenant_id"], ["stations.id", "stations.tenant_id"]
    ),
    sa.ForeignKeyConstraint(
        ["observation_id", "station_id"], ["observations.id", "observations.station_id"]
    ),
    sa.ForeignKeyConstraint(
        ["rating_curve_id", "station_id"],
        ["rating_curves.id", "rating_curves.station_id"],
    ),
    sa.ForeignKeyConstraint(
        ["feed_evidence_id", "tenant_id", "station_id", "observation_id"],
        [
            "measurement_feed_evidence.id",
            "measurement_feed_evidence.tenant_id",
            "measurement_feed_evidence.station_id",
            "measurement_feed_evidence.observation_id",
        ],
    ),
    sa.ForeignKeyConstraint(
        ["reference_proof_id", "tenant_id", "station_id", "rating_curve_id"],
        [
            "rating_reference_proofs.id",
            "rating_reference_proofs.tenant_id",
            "rating_reference_proofs.station_id",
            "rating_reference_proofs.rating_curve_id",
        ],
    ),
    sa.CheckConstraint(
        "fingerprint = encode(sha256(convert_to(content, 'UTF8')), 'hex')",
        name="ck_provisional_discharge_digest",
    ),
    sa.CheckConstraint(
        "discharge > '-Infinity'::float8 AND discharge < 'Infinity'::float8",
        name="ck_provisional_discharge_finite",
    ),
)
sa.Index("ix_provisional_discharge_station", provisional_discharges.c.station_id)
sa.Index(
    "ix_provisional_discharge_observation", provisional_discharges.c.observation_id
)
sa.Index("ix_provisional_discharge_curve", provisional_discharges.c.rating_curve_id)
sa.Index("ix_provisional_discharge_feed", provisional_discharges.c.feed_evidence_id)
sa.Index("ix_provisional_discharge_proof", provisional_discharges.c.reference_proof_id)

_FUNCTIONS = """
CREATE FUNCTION public.provisional_immutable() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    RAISE EXCEPTION 'protected provisional evidence is immutable';
END $$;

CREATE FUNCTION public.provisional_permission_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'protected provisional writes require READ COMMITTED';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.provisional_permission_disable() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'protected provisional writes require READ COMMITTED';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'provisional-discharge:' || NEW.tenant_id::text, 0));
    IF session_user IS DISTINCT FROM (
        SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = TG_RELID
    ) OR OLD.state <> 'enabled' OR NEW.state <> 'disabled'
      OR (to_jsonb(OLD) - 'state') IS DISTINCT FROM (to_jsonb(NEW) - 'state') THEN
        RAISE EXCEPTION 'protected permission is immutable except owner disable';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.provisional_curve_content(data jsonb) RETURNS jsonb
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp
SET timezone = 'UTC' AS $$
    SELECT to_jsonb(r) || jsonb_build_object('points',
        (SELECT jsonb_agg(p ORDER BY (p->>'water_level')::float8, p::text)
         FROM jsonb_array_elements(r.points) p))
    FROM jsonb_populate_record(NULL::public.rating_curves, data) r
$$;

CREATE FUNCTION public.provisional_measurement_content(data jsonb) RETURNS jsonb
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp
SET timezone = 'UTC' AS $$
    SELECT to_jsonb(r) - ARRAY['qc_status', 'qc_flags', 'qc_rule_version']
    FROM jsonb_populate_record(NULL::public.observations, data) r
$$;

CREATE FUNCTION public.provisional_qc_content(data jsonb) RETURNS jsonb
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp AS $$
    SELECT jsonb_build_object('qc_status', data->'qc_status',
        'qc_rule_version', data->'qc_rule_version',
        'qc_flags', (SELECT jsonb_agg(f ORDER BY f::text)
            FROM jsonb_array_elements(COALESCE(NULLIF(data->'qc_flags', 'null'::jsonb),
            '[]'::jsonb)) f))
$$;

CREATE FUNCTION public.provisional_reference_insert() RETURNS trigger
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

CREATE FUNCTION public.provisional_discharge_insert() RETURNS trigger
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

_TABLES = (
    "provisional_discharge_permissions",
    "measurement_feed_evidence",
    "rating_reference_proofs",
    "provisional_discharges",
)


def upgrade() -> None:
    for name in _TABLES:
        table = metadata.tables[name]
        op.execute(sa.schema.CreateTable(table))
        for index in sorted(table.indexes, key=lambda item: str(item.name)):
            op.execute(sa.schema.CreateIndex(index, if_not_exists=True))
    op.execute(_FUNCTIONS)
    for name in _TABLES:
        events = (
            "DELETE OR TRUNCATE"
            if name == "provisional_discharge_permissions"
            else "UPDATE OR DELETE OR TRUNCATE"
        )
        op.execute(
            f"CREATE TRIGGER trg_{name}_immutable BEFORE {events} "
            f"ON public.{name} FOR EACH STATEMENT "
            "EXECUTE FUNCTION public.provisional_immutable()"
        )
    op.execute(
        "CREATE TRIGGER trg_provisional_permission_insert BEFORE INSERT "
        "ON public.provisional_discharge_permissions FOR EACH ROW "
        "EXECUTE FUNCTION public.provisional_permission_insert()"
    )
    op.execute(
        "CREATE TRIGGER trg_provisional_permission_disable BEFORE UPDATE "
        "ON public.provisional_discharge_permissions FOR EACH ROW "
        "EXECUTE FUNCTION public.provisional_permission_disable()"
    )
    for name in ("measurement_feed_evidence", "rating_reference_proofs"):
        op.execute(
            f"CREATE TRIGGER trg_{name}_insert BEFORE INSERT ON public.{name} "
            "FOR EACH ROW EXECUTE FUNCTION public.provisional_reference_insert()"
        )
    op.execute(
        "CREATE TRIGGER trg_provisional_discharges_insert BEFORE INSERT "
        "ON public.provisional_discharges FOR EACH ROW "
        "EXECUTE FUNCTION public.provisional_discharge_insert()"
    )
    # Migration-only upgrade is safe even with pre-existing default ACL grants.
    op.execute(
        "REVOKE ALL ON " + ", ".join("public." + n for n in _TABLES) + " FROM PUBLIC"
    )
    op.execute("""DO $$ DECLARE r text; BEGIN
        FOREACH r IN ARRAY ARRAY['sapphire_api', 'sapphire_worker',
                                'sapphire_operator', 'sapphire_publication_health'] LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
                EXECUTE format('REVOKE ALL ON public.provisional_discharge_permissions,
                    public.measurement_feed_evidence, public.rating_reference_proofs,
                    public.provisional_discharges FROM %I', r);
            END IF;
        END LOOP;
    END $$""")


def downgrade() -> None:
    for name in _TABLES:
        if op.get_bind().scalar(
            sa.text(f"SELECT EXISTS (SELECT 1 FROM public.{name})")
        ):
            raise RuntimeError(
                "protected provisional evidence exists; downgrade refused"
            )
    for name in reversed(_TABLES):
        op.drop_table(name)
    for name in (
        "provisional_discharge_insert()",
        "provisional_reference_insert()",
        "provisional_qc_content(jsonb)",
        "provisional_curve_content(jsonb)",
        "provisional_measurement_content(jsonb)",
        "provisional_immutable()",
        "provisional_permission_disable()",
        "provisional_permission_insert()",
    ):
        op.execute(f"DROP FUNCTION public.{name}")
