"""Explicit operational-weather role, separate from history and future binding."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0081"
down_revision: str | None = "0080"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_station_weather_sources_role", "station_weather_sources", type_="check"
    )
    op.create_check_constraint(
        "ck_station_weather_sources_role",
        "station_weather_sources",
        "role IS NULL OR role IN ('forecast', 'reanalysis', 'operational')",
    )

    op.create_table(
        "operational_weather_cache",
        sa.Column(
            "station_id",
            UUID(as_uuid=True),
            sa.ForeignKey("stations.id"),
            nullable=False,
        ),
        sa.Column("binding_fingerprint", sa.Text, nullable=False),
        sa.Column(
            "payload_version", sa.SmallInteger, nullable=False, server_default="1"
        ),
        sa.Column("attempt_generation", sa.BigInteger, nullable=False),
        sa.Column("attempt_id", UUID(as_uuid=True), nullable=False),
        sa.Column("issue_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("past_outcome", JSONB, nullable=False),
        sa.Column("future_attempts", JSONB, nullable=False),
        sa.Column("past_snapshot", JSONB(none_as_null=True)),
        sa.Column("future_receipts", JSONB, nullable=False),
        sa.Column("completion_sha256", sa.Text),
        sa.PrimaryKeyConstraint("station_id", "binding_fingerprint"),
        sa.CheckConstraint(
            "binding_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_operational_weather_key"
        ),
        sa.CheckConstraint(
            "payload_version = 1 AND attempt_generation > 0",
            name="ck_operational_weather_version",
        ),
        sa.CheckConstraint(
            "outcome IN ('pending', 'complete', 'failed')",
            name="ck_operational_weather_outcome",
        ),
        sa.CheckConstraint(
            "(outcome = 'pending' "
            "AND finished_at IS NULL AND completion_sha256 IS NULL) OR "
            "(outcome <> 'pending' "
            "AND finished_at IS NOT NULL AND completion_sha256 IS NOT NULL "
            "AND finished_at >= started_at AND completion_sha256 ~ '^[0-9a-f]{64}$')",
            name="ck_operational_weather_completion",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(past_outcome) = 'object' "
            "AND jsonb_typeof(future_attempts) = 'array' "
            "AND jsonb_typeof(future_receipts) = 'array' "
            "AND jsonb_array_length(future_receipts) <= 32 "
            "AND (past_snapshot IS NULL OR jsonb_typeof(past_snapshot) = 'object')",
            name="ck_operational_weather_json",
        ),
    )

    op.execute("""
        REVOKE ALL ON operational_weather_cache FROM PUBLIC;
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sapphire_api') THEN
                REVOKE ALL ON operational_weather_cache FROM sapphire_api;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sapphire_worker') THEN
                REVOKE ALL ON operational_weather_cache FROM sapphire_worker;
                GRANT SELECT, INSERT, UPDATE ON operational_weather_cache
                    TO sapphire_worker;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM operational_weather_cache LIMIT 1"))
        .first()
    ):
        raise RuntimeError(
            "operational weather cache requires explicit operator-approved removal"
        )
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM station_weather_sources "
                "WHERE role = 'operational' LIMIT 1"
            )
        )
        .first()
    ):
        raise RuntimeError(
            "operational weather bindings require explicit operator-approved removal"
        )
    op.drop_table("operational_weather_cache")
    op.drop_constraint(
        "ck_station_weather_sources_role", "station_weather_sources", type_="check"
    )
    op.create_check_constraint(
        "ck_station_weather_sources_role",
        "station_weather_sources",
        "role IS NULL OR role IN ('forecast', 'reanalysis')",
    )
