# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import sqlalchemy as sa
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sapphire_flow.db.metadata import observations as observations_table
from sapphire_flow.store._helpers import utc_from_row, utc_or_none
from sapphire_flow.types.domain import QcFlag
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.ids import ObservationId, RatingCurveId, StationId
from sapphire_flow.types.observation import Observation, RawObservation

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime

log = structlog.get_logger(__name__)

_RawObservationKey = tuple[object, object, object, object]
_OBSERVATION_NATURAL_KEY_COLUMNS = (
    observations_table.c.station_id,
    observations_table.c.timestamp,
    observations_table.c.parameter,
    observations_table.c.source,
)


class PgObservationStore:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def store_observations(self, observations: list[Observation]) -> None:
        if not observations:
            return
        for obs in observations:
            stmt = (
                pg_insert(observations_table)
                .values(**_obs_to_values(obs))
                .on_conflict_do_update(
                    index_elements=_OBSERVATION_NATURAL_KEY_COLUMNS,
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
        self, observations: list[RawObservation]
    ) -> list[ObservationId]:
        if not observations:
            return []

        deduped = _dedupe_raw_observations(observations)
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
            for raw in deduped
        ]

        ids: list[ObservationId] = []
        inserted_count = 0
        updated_count = 0
        for i in range(0, len(rows), self._BATCH_SIZE):
            batch = rows[i : i + self._BATCH_SIZE]
            proposed_ids = {cast("ObservationId", row["id"]) for row in batch}
            insert_stmt = pg_insert(observations_table).values(batch)
            stmt = insert_stmt.on_conflict_do_update(
                index_elements=_OBSERVATION_NATURAL_KEY_COLUMNS,
                set_={
                    "value": insert_stmt.excluded.value,
                    "qc_status": QcStatus.RAW.value,
                    "qc_flags": None,
                    "qc_rule_version": None,
                },
                where=observations_table.c.value.is_distinct_from(
                    insert_stmt.excluded.value
                ),
            ).returning(observations_table.c.id)
            written_ids = [
                cast("ObservationId", obs_id)
                for obs_id in self._conn.execute(stmt).scalars()
            ]
            written_id_set = set(written_ids)
            ids.extend(written_ids)
            inserted_count += len(written_id_set & proposed_ids)
            updated_count += len(written_id_set - proposed_ids)

        log.info(
            "observation.raw_upsert",
            inserted=inserted_count,
            updated=updated_count,
            skipped=len(observations) - inserted_count - updated_count,
            input_count=len(observations),
            deduped_count=len(rows),
        )

        return ids

    def update_qc(
        self,
        observation_id: ObservationId,
        qc_status: QcStatus,
        qc_flags: list[QcFlag],
        qc_rule_version: str | None = None,
    ) -> None:
        self._conn.execute(
            sa.update(observations_table)
            .where(observations_table.c.id == observation_id)
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
            sa.select(observations_table)
            .where(observations_table.c.station_id == station_id)
            .where(observations_table.c.parameter == parameter)
            .where(observations_table.c.timestamp >= start)
            .where(observations_table.c.timestamp < end)
        )
        if qc_status is not None:
            stmt = stmt.where(observations_table.c.qc_status == qc_status.value)
        if source is not None:
            stmt = stmt.where(observations_table.c.source == source.value)
        rows = self._conn.execute(stmt).mappings().all()
        return [_row_to_domain(row) for row in rows]

    def fetch_latest_timestamp(
        self, station_id: StationId, parameter: str
    ) -> UtcDatetime | None:
        row = self._conn.execute(
            sa.select(sa.func.max(observations_table.c.timestamp)).where(
                sa.and_(
                    observations_table.c.station_id == station_id,
                    observations_table.c.parameter == parameter,
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
            sa.select(observations_table)
            .where(observations_table.c.station_id.in_(station_ids))
            .where(observations_table.c.parameter == parameter)
            .where(observations_table.c.timestamp >= start)
            .where(observations_table.c.timestamp < end)
        )
        if qc_status is not None:
            stmt = stmt.where(observations_table.c.qc_status == qc_status.value)
        if source is not None:
            stmt = stmt.where(observations_table.c.source == source.value)
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


def _serialize_flags(flags: list[QcFlag]) -> list[dict[str, str | None]] | None:
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


def _dedupe_raw_observations(
    observations: list[RawObservation],
) -> list[RawObservation]:
    deduped: dict[_RawObservationKey, RawObservation] = {}
    for raw in observations:
        _validate_raw_observation(raw)
        key = (raw.station_id, raw.timestamp, raw.parameter, raw.source)
        deduped[key] = raw
    return list(deduped.values())


def _validate_raw_observation(raw: RawObservation) -> None:
    if not raw.parameter.strip():
        raise ValueError("RawObservation.parameter must not be empty")
    if not math.isfinite(raw.value):
        raise ValueError("RawObservation.value must be finite")


def _deserialize_flags(raw: list[dict[str, Any]] | None) -> list[QcFlag]:
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


def _obs_to_values(obs: Observation) -> dict[str, object]:
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
