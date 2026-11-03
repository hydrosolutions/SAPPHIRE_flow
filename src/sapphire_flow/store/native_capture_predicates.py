from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from sapphire_flow.db.metadata import native_measurement_captures, observations


def current_capture_matches_observation() -> sa.ColumnElement[bool]:
    measurement = sa.cast(native_measurement_captures.c.source_content, JSONB)[
        "measurement"
    ]
    columns = (
        observations.c.id,
        observations.c.station_id,
        observations.c.timestamp,
        observations.c.parameter,
        observations.c.value,
        observations.c.source,
        observations.c.created_at,
        observations.c.delivery_id,
        observations.c.rating_curve_id,
        observations.c.rating_curve_correction_version,
    )
    return sa.and_(
        native_measurement_captures.c.observation_id == observations.c.id,
        native_measurement_captures.c.station_id == observations.c.station_id,
        *(
            measurement[column.name]
            .astext.cast(column.type)
            .is_not_distinct_from(column)
            for column in columns
        ),
    )
