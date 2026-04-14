# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sapphire_flow.db.metadata import observations
from sapphire_flow.store._helpers import utc_from_row, utc_or_none
from sapphire_flow.types.domain import QcFlag
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.ids import ObservationId, RatingCurveId, StationId
from sapphire_flow.types.observation import Observation, RawObservation

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime


class PgObservationStore:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def store_observations(self, obs_list: list[Observation]) -> None:
        if not obs_list:
            return
        for obs in obs_list:
            stmt = (
                pg_insert(observations)
                .values(**_obs_to_values(obs))
                .on_conflict_do_update(
                    index_elements=[
                        observations.c.station_id,
                        observations.c.timestamp,
                        observations.c.parameter,
                        observations.c.source,
                    ],
                    set_={
                        "value": obs.value,
                        "qc_status": obs.qc_status.value,
                        "qc_flags": _serialize_flags(obs.qc_flags),
                        "qc_rule_version": obs.qc_rule_version,
                    },
                )
            )
            self._conn.execute(stmt)

    _BATCH_SIZE = 5000  # same as PgHistoricalForcingStore; 9 cols × 5000 = 45K params

    def store_raw_observations(
        self, obs_list: list[RawObservation]
    ) -> list[ObservationId]:
        if not obs_list:
            return []

        rows = [
            {
                "id": ObservationId(uuid4()),
                "station_id": raw.station_id,
                "timestamp": raw.timestamp,
                "parameter": raw.parameter,
                "value": raw.value,
                "source": raw.source.value,
                "qc_status": QcStatus.RAW.value,
                "qc_flags": None,
                "qc_rule_version": None,
            }
            for raw in obs_list
        ]

        ids: list[ObservationId] = []
        for i in range(0, len(rows), self._BATCH_SIZE):
            batch = rows[i : i + self._BATCH_SIZE]
            stmt = (
                pg_insert(observations)
                .values(batch)
                .on_conflict_do_nothing(
                    index_elements=["station_id", "timestamp", "parameter", "source"],
                )
                .returning(observations.c.id)
            )
            returned = {row[0] for row in self._conn.execute(stmt).fetchall()}
            for row in batch:
                if row["id"] in returned:
                    ids.append(row["id"])

        return ids

    def update_qc(
        self,
        observation_id: ObservationId,
        qc_status: QcStatus,
        qc_flags: list[QcFlag],
        qc_rule_version: str | None = None,
    ) -> None:
        self._conn.execute(
            sa.update(observations)
            .where(observations.c.id == observation_id)
            .values(
                qc_status=qc_status.value,
                qc_flags=_serialize_flags(qc_flags),
                qc_rule_version=qc_rule_version,
            )
        )

    def fetch_observations(
        self,
        station_id: StationId,
        parameter: str,
        start: UtcDatetime,
        end: UtcDatetime,
        qc_status: QcStatus | None = None,
        source: ObservationSource | None = None,
    ) -> list[Observation]:
        stmt = (
            sa.select(observations)
            .where(observations.c.station_id == station_id)
            .where(observations.c.parameter == parameter)
            .where(observations.c.timestamp >= start)
            .where(observations.c.timestamp < end)
        )
        if qc_status is not None:
            stmt = stmt.where(observations.c.qc_status == qc_status.value)
        if source is not None:
            stmt = stmt.where(observations.c.source == source.value)
        rows = self._conn.execute(stmt).mappings().all()
        return [_row_to_domain(row) for row in rows]

    def fetch_latest_timestamp(
        self, station_id: StationId, parameter: str
    ) -> UtcDatetime | None:
        row = self._conn.execute(
            sa.select(sa.func.max(observations.c.timestamp)).where(
                sa.and_(
                    observations.c.station_id == station_id,
                    observations.c.parameter == parameter,
                )
            )
        ).scalar_one_or_none()
        return utc_or_none(row)

    def fetch_observations_batch(
        self,
        station_ids: list[StationId],
        parameter: str,
        start: UtcDatetime,
        end: UtcDatetime,
        qc_status: QcStatus | None = None,
        source: ObservationSource | None = None,
    ) -> dict[StationId, list[Observation]]:
        if not station_ids:
            return {}
        stmt = (
            sa.select(observations)
            .where(observations.c.station_id.in_(station_ids))
            .where(observations.c.parameter == parameter)
            .where(observations.c.timestamp >= start)
            .where(observations.c.timestamp < end)
        )
        if qc_status is not None:
            stmt = stmt.where(observations.c.qc_status == qc_status.value)
        if source is not None:
            stmt = stmt.where(observations.c.source == source.value)
        rows = self._conn.execute(stmt).mappings().all()

        result: dict[StationId, list[Observation]] = {sid: [] for sid in station_ids}
        for row in rows:
            sid = StationId(row["station_id"])
            result[sid].append(_row_to_domain(row))
        return result

    def fetch_derived_observations_by_curve(
        self,
        station_id: StationId,
        rating_curve_id: RatingCurveId,
    ) -> list[Observation]:
        raise NotImplementedError("Rating curves deferred to v1")


def _serialize_flags(flags: list[QcFlag]) -> list[dict] | None:  # type: ignore[type-arg]
    if not flags:
        return None
    return [
        {
            "rule_id": f.rule_id,
            "rule_version": f.rule_version,
            "status": f.status.value,
            "detail": f.detail,
        }
        for f in flags
    ]


def _deserialize_flags(raw: list | None) -> list[QcFlag]:  # type: ignore[type-arg]
    if not raw:
        return []
    return [
        QcFlag(
            rule_id=item["rule_id"],
            rule_version=item["rule_version"],
            status=QcStatus(item["status"]),
            detail=item.get("detail"),
        )
        for item in raw
    ]


def _obs_to_values(obs: Observation) -> dict:  # type: ignore[type-arg]
    return {
        "id": obs.id,
        "station_id": obs.station_id,
        "timestamp": obs.timestamp,
        "parameter": obs.parameter,
        "value": obs.value,
        "source": obs.source.value,
        "qc_status": obs.qc_status.value,
        "qc_flags": _serialize_flags(obs.qc_flags),
        "qc_rule_version": obs.qc_rule_version,
        "created_at": obs.created_at,
    }


def _row_to_domain(row: sa.engine.row.RowMapping) -> Observation:
    return Observation(
        id=ObservationId(row["id"]),
        station_id=StationId(row["station_id"]),
        timestamp=utc_from_row(row["timestamp"]),
        parameter=row["parameter"],
        value=row["value"],
        source=ObservationSource(row["source"]),
        rating_curve_id=None,
        rating_curve_correction_version=None,
        qc_status=QcStatus(row["qc_status"]),
        qc_flags=_deserialize_flags(row["qc_flags"]),
        qc_rule_version=row["qc_rule_version"],
        created_at=utc_from_row(row["created_at"]),
    )
