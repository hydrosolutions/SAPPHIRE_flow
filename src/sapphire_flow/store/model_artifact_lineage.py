# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Plan 120 Task 2D — train-time lineage write wiring.

A standalone helper, NOT a widening of the cross-cutting
`ModelArtifactStore.store_artifact` Protocol (see plan "Task 2D — Train-time
lineage write wiring" for the full rationale). Called right after
`store_artifact`/`store_and_promote_artifact` returns, on the connection the
calling flow task already has.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sapphire_flow.db.metadata import (
    basin_versions,
    model_artifact_basin_versions,
    stations,
)

if TYPE_CHECKING:
    from collections.abc import Collection

    from sapphire_flow.types.ids import ArtifactId, StationId

log = structlog.get_logger()


def record_artifact_basin_lineage(
    conn: sa.Connection,
    artifact_id: ArtifactId,
    trained_station_ids: Collection[StationId],
) -> None:
    """Write one ``model_artifact_basin_versions`` row per basin a station-
    or group-scoped artifact actually trained on.

    Unresolvable-basin behavior is SPLIT by kind (plan "Task 2D",
    grill-me (a)):

    - ``stations.basin_id IS NULL`` -> SKIP the lineage row for that
      station, INFO-logged, no raise. With the D-UP prerequisite gate in
      ``services/training_data.py``, a model REQUIRING static features can
      never reach this helper with a NULL basin, so a NULL basin here
      provably means static features were not required.
    - A basin with NO current (``superseded_at IS NULL``) ``basin_versions``
      row (a dangling ``basin_id`` or a Task-0A-invariant violation) -> FAIL
      LOUD (raise). This is an integrity violation the Task 0A invariant is
      meant to make unrepresentable; silently swallowing it would defeat the
      Decision-B stale-basin retrain SLA.

    NON-ATOMIC and LOG-LOUD on failure, deliberately: matches the
    pre-existing store+promote relationship, which is already non-atomic
    under the AUTOCOMMIT connection flows run on in production. See plan
    "Task 2D" § "No new transaction boundary" for the full rationale.
    """
    for station_id in trained_station_ids:
        basin_id_row = conn.execute(
            sa.select(stations.c.basin_id).where(stations.c.id == station_id)
        ).one_or_none()
        if basin_id_row is None:
            raise ValueError(
                f"station {station_id} not found while recording basin "
                f"lineage for artifact {artifact_id}"
            )
        basin_id = basin_id_row[0]
        if basin_id is None:
            log.info(
                "model_artifact_lineage.station_basin_null_skip",
                station_id=str(station_id),
                artifact_id=str(artifact_id),
            )
            continue

        current_version_row = conn.execute(
            sa.select(basin_versions.c.id).where(
                sa.and_(
                    basin_versions.c.basin_id == basin_id,
                    basin_versions.c.superseded_at.is_(None),
                )
            )
        ).one_or_none()
        if current_version_row is None:
            raise ValueError(
                f"basin {basin_id} (station {station_id}) has no current "
                f"basin_versions row while recording lineage for artifact "
                f"{artifact_id} — Task 0A invariant violated"
            )
        basin_version_id = current_version_row[0]

        conn.execute(
            pg_insert(model_artifact_basin_versions)
            .values(
                model_artifact_id=artifact_id,
                basin_version_id=basin_version_id,
            )
            .on_conflict_do_nothing(
                index_elements=["model_artifact_id", "basin_version_id"]
            )
        )


class PgArtifactLineageWriter:
    """Thin flow-facing adapter around `record_artifact_basin_lineage` — the
    ``lineage_writer`` object `train_models_flow`/`onboard_model_flow` call
    right after storing an artifact. Production wiring only; tests inject a
    fake with the same `.record(...)` shape (see
    `tests.fakes.fake_stores.FakeArtifactLineageWriter`)."""

    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def record(
        self, artifact_id: ArtifactId, trained_station_ids: Collection[StationId]
    ) -> None:
        record_artifact_basin_lineage(self._conn, artifact_id, trained_station_ids)
