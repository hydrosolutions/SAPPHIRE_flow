"""Retain declared hindcast periods and distinct evaluation identities."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0097"
down_revision = "0096"
branch_labels = None
depends_on = None

_PERIOD_KEY = (
    "COALESCE(period_definition::text, ''), "
    "COALESCE(reference_period_definition::text, ''), "
    "COALESCE(reference_displacement_seconds, 0), "
    "COALESCE(input_availability_policy, ''), "
    "COALESCE(source_periods_sha256, '')"
)

_LEGACY_INDEXES: dict[str, str] = {
    "uq_skill_scores_natural_key": (
        "CREATE UNIQUE INDEX uq_skill_scores_natural_key ON skill_scores (station_id, "
        "model_id, COALESCE(model_artifact_id::text, ''), parameter, skill_source, "
        "COALESCE(forcing_type, ''), computation_version, lead_time_hours, "
        "COALESCE(season, ''), COALESCE(flow_regime, ''), metric, time_step_seconds, "
        "COALESCE(phase_offset_seconds, -1)) WHERE computation_version >= 2 AND "
        "generation_id IS NULL"
    ),
    "uq_skill_scores_natural_key_generation": (
        "CREATE UNIQUE INDEX uq_skill_scores_natural_key_generation ON skill_scores "
        "(station_id, model_id, COALESCE(model_artifact_id::text, ''), parameter, "
        "skill_source, COALESCE(forcing_type, ''), computation_version, "
        "lead_time_hours, COALESCE(season, ''), COALESCE(flow_regime, ''), metric, "
        "time_step_seconds, COALESCE(phase_offset_seconds, -1), generation_id) WHERE "
        "computation_version >= 2 AND generation_id IS NOT NULL"
    ),
    "uq_skill_scores_natural_key_legacy": (
        "CREATE UNIQUE INDEX uq_skill_scores_natural_key_legacy ON skill_scores "
        "(station_id, model_artifact_id, parameter, skill_source, "
        "COALESCE(forcing_type, ''), computation_version, lead_time_hours, "
        "COALESCE(season, ''), COALESCE(flow_regime, ''), metric) WHERE "
        "computation_version < 2"
    ),
    "uq_skill_diagrams_natural_key": (
        "CREATE UNIQUE INDEX uq_skill_diagrams_natural_key ON skill_diagrams "
        "(station_id, model_id, COALESCE(model_artifact_id::text, ''), parameter, "
        "skill_source, computation_version, lead_time_hours, COALESCE(season, ''), "
        "COALESCE(flow_regime, ''), diagram_type, COALESCE(threshold_level, ''), "
        "time_step_seconds, COALESCE(phase_offset_seconds, -1)) WHERE "
        "computation_version >= 2 AND generation_id IS NULL"
    ),
    "uq_skill_diagrams_natural_key_generation": (
        "CREATE UNIQUE INDEX uq_skill_diagrams_natural_key_generation "
        "ON skill_diagrams "
        "(station_id, model_id, COALESCE(model_artifact_id::text, ''), parameter, "
        "skill_source, computation_version, lead_time_hours, COALESCE(season, ''), "
        "COALESCE(flow_regime, ''), diagram_type, COALESCE(threshold_level, ''), "
        "time_step_seconds, COALESCE(phase_offset_seconds, -1), generation_id) WHERE "
        "computation_version >= 2 AND generation_id IS NOT NULL"
    ),
    "uq_skill_diagrams_natural_key_legacy": (
        "CREATE UNIQUE INDEX uq_skill_diagrams_natural_key_legacy ON skill_diagrams "
        "(station_id, model_artifact_id, parameter, skill_source, computation_version, "
        "lead_time_hours, COALESCE(season, ''), COALESCE(flow_regime, ''), "
        "diagram_type, COALESCE(threshold_level, '')) WHERE computation_version < 2"
    ),
}


def upgrade() -> None:
    op.add_column(
        "hindcast_forecasts", sa.Column("period_definition", JSONB(none_as_null=True))
    )
    op.add_column(
        "hindcast_forecasts", sa.Column("source_periods", JSONB(none_as_null=True))
    )
    op.add_column(
        "hindcast_forecasts", sa.Column("input_availability_policy", sa.Text())
    )
    op.add_column(
        "skill_generations", sa.Column("evaluation_contexts", JSONB(none_as_null=True))
    )
    for table in ("skill_scores", "skill_diagrams"):
        op.add_column(table, sa.Column("period_definition", JSONB(none_as_null=True)))
        op.add_column(
            table, sa.Column("reference_period_definition", JSONB(none_as_null=True))
        )
        op.add_column(table, sa.Column("reference_displacement_seconds", sa.Integer()))
        op.add_column(table, sa.Column("input_availability_policy", sa.Text()))
        op.add_column(table, sa.Column("source_periods", JSONB(none_as_null=True)))
        op.add_column(table, sa.Column("source_periods_sha256", sa.Text()))
    for name, statement in _LEGACY_INDEXES.items():
        op.drop_index(name)
        if name.endswith("_generation"):
            statement = statement.replace(
                ", generation_id) WHERE", f", {_PERIOD_KEY}, generation_id) WHERE"
            )
        else:
            statement = statement.replace(") WHERE", f", {_PERIOD_KEY}) WHERE")
        op.execute(statement)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM hindcast_forecasts
                       WHERE period_definition IS NOT NULL
                       OR source_periods IS NOT NULL
                       OR input_availability_policy IS NOT NULL)
               OR EXISTS (SELECT 1 FROM skill_generations
                          WHERE evaluation_contexts IS NOT NULL)
               OR EXISTS (SELECT 1 FROM skill_scores WHERE period_definition IS NOT NULL
                          OR reference_period_definition IS NOT NULL
                          OR reference_displacement_seconds IS NOT NULL
                          OR input_availability_policy IS NOT NULL
                          OR source_periods IS NOT NULL
                          OR source_periods_sha256 IS NOT NULL)
               OR EXISTS (SELECT 1 FROM skill_diagrams
                          WHERE period_definition IS NOT NULL
                          OR reference_period_definition IS NOT NULL
                          OR reference_displacement_seconds IS NOT NULL
                          OR input_availability_policy IS NOT NULL
                          OR source_periods IS NOT NULL
                          OR source_periods_sha256 IS NOT NULL)
            THEN RAISE EXCEPTION 'Cannot discard retained hindcast period evidence';
            END IF;
        END $$;
    """)
    for name, statement in _LEGACY_INDEXES.items():
        op.drop_index(name)
        op.execute(statement)
    for table in ("skill_scores", "skill_diagrams"):
        for column in (
            "period_definition",
            "reference_period_definition",
            "reference_displacement_seconds",
            "input_availability_policy",
            "source_periods",
            "source_periods_sha256",
        ):
            op.drop_column(table, column)
    op.drop_column("skill_generations", "evaluation_contexts")
    for column in ("period_definition", "source_periods", "input_availability_policy"):
        op.drop_column("hindcast_forecasts", column)
