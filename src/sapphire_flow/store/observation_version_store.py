# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sapphire_flow.db.metadata import observation_versions
from sapphire_flow.store._helpers import utc_from_row
from sapphire_flow.types.enums import ObservationSource
from sapphire_flow.types.ids import (
    ObservationId,
    ObservationVersionId,
    RatingCurveId,
    StationId,
)
from sapphire_flow.types.observation import ArchivedObservationValue

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.observation import Observation


class PgObservationVersionStore:
    # 8 cols × 5000 = 40K bind params, under the 65535 Postgres limit.
    _BATCH_SIZE = 5000

    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def archive_observation_values(
        self,
        observations: Sequence[Observation],
        superseded_by_curve_id: RatingCurveId,
    ) -> int:
        if not observations:
            return 0

        rows: list[dict[str, object]] = []
        for obs in observations:
            if (
                obs.source != ObservationSource.RATING_CURVE_DERIVED
                or obs.rating_curve_id is None
            ):
                raise ValueError(
                    "archive_observation_values accepts only rating-curve-derived "
                    "observations with a rating_curve_id; got "
                    f"source={obs.source.value}, rating_curve_id={obs.rating_curve_id} "
                    f"for observation {obs.id}"
                )
            rows.append(
                {
                    "id": ObservationVersionId(uuid4()),
                    "observation_id": obs.id,
                    "station_id": obs.station_id,
                    "timestamp": obs.timestamp,
                    "parameter": obs.parameter,
                    "value": obs.value,
                    "rating_curve_id": obs.rating_curve_id,
                    "superseded_by_curve_id": superseded_by_curve_id,
                }
            )

        inserted = 0
        for i in range(0, len(rows), self._BATCH_SIZE):
            batch = rows[i : i + self._BATCH_SIZE]
            stmt = (
                pg_insert(observation_versions)
                .values(batch)
                # Idempotent: a re-archive of the same (observation, producing curve)
                # is a no-op and is NOT counted.
                .on_conflict_do_nothing(
                    index_elements=["observation_id", "rating_curve_id"]
                )
                .returning(observation_versions.c.id)
            )
            inserted += len(self._conn.execute(stmt).scalars().all())
        return inserted

    def fetch_archived_values(
        self,
        station_id: StationId,
        parameter: str,
        start: UtcDatetime,
        end: UtcDatetime,
        rating_curve_id: RatingCurveId | None = None,
    ) -> Sequence[ArchivedObservationValue]:
        stmt = sa.select(observation_versions).where(
            observation_versions.c.station_id == station_id,
            observation_versions.c.parameter == parameter,
            observation_versions.c.timestamp >= start,
            observation_versions.c.timestamp < end,
        )
        if rating_curve_id is not None:
            stmt = stmt.where(observation_versions.c.rating_curve_id == rating_curve_id)
        stmt = stmt.order_by(observation_versions.c.timestamp)
        rows = self._conn.execute(stmt).mappings().all()
        return [_row_to_archived(row) for row in rows]


def _row_to_archived(row: sa.engine.row.RowMapping) -> ArchivedObservationValue:
    return ArchivedObservationValue(
        id=ObservationVersionId(row["id"]),
        observation_id=ObservationId(row["observation_id"]),
        station_id=StationId(row["station_id"]),
        timestamp=utc_from_row(row["timestamp"]),
        parameter=row["parameter"],
        value=row["value"],
        rating_curve_id=RatingCurveId(row["rating_curve_id"]),
        superseded_at=utc_from_row(row["superseded_at"]),
        superseded_by_curve_id=RatingCurveId(row["superseded_by_curve_id"]),
    )
