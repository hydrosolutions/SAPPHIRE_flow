"""Plan 404 T1 — the append-only record for a QC-rejected member or
group-station forecast (D1/D2). Never `forecasts`; see
`docs/plans/404-store-qc-rejected-member-forecasts.md`.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.pool import NullPool

from sapphire_flow.db.metadata import rejected_forecasts
from sapphire_flow.exceptions import CaptureAbandonedError, ConfigurationError
from sapphire_flow.store._helpers import utc_from_row
from sapphire_flow.types.domain import QcFlag
from sapphire_flow.types.enums import EnsembleRepresentation, ForecastDataUse, QcStatus
from sapphire_flow.types.forecast_lineage import ForecastInputLineage
from sapphire_flow.types.ids import (
    ArtifactId,
    ModelId,
    RejectedForecastId,
    StationGroupId,
    StationId,
)
from sapphire_flow.types.rejected_forecast import (
    PersistedRejectedForecast,
    validate_rejected_assignment,
)

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable, Sequence
    from contextlib import AbstractContextManager as ContextManager

    from sqlalchemy import RowMapping

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.rejected_forecast import RejectedForecastEntry

# D6/T1 — the transaction's own inner bound. D6's 10s overall deadline is the
# only limit that holds for EVERY kind of stall (host resolution, a stall
# after connecting); these are the per-step limits inside an already-open
# transaction.
_LOCK_TIMEOUT = "2s"
_STATEMENT_TIMEOUT = "5s"


def rejected_capture_transaction_factory(
    url: sa.URL,
) -> Callable[[], ContextManager[sa.Connection]]:
    """One fresh, bounded connection per run (T1) — a dedicated `NullPool`
    engine on `url` with a short `connect_timeout`, so a dead/unreachable
    database fails fast instead of waiting psycopg's ~130s default. No
    disposal is needed: `NullPool` never retains a connection between uses.
    `make_pg_stores` is this function's only caller, passing `conn.engine.url`
    (the `URL` object, never `str(url)`, which masks the password)."""
    engine = sa.create_engine(
        url, poolclass=NullPool, connect_args={"connect_timeout": 5}
    )
    return engine.begin


def _nonfinite_encode(value: float) -> float | dict[str, str]:
    if math.isfinite(value):
        return value
    return {"nonfinite": repr(value)}


def _nonfinite_decode(raw: object) -> float:
    if isinstance(raw, dict):
        return float(raw["nonfinite"])  # type: ignore[arg-type] — 'nan'/'inf'/'-inf'
    return float(raw)  # type: ignore[arg-type]


def _encode_series(ensemble: ForecastEnsemble) -> dict[str, list[list[object]]]:
    """Plan 404's record contract: `values` keyed by member id or quantile
    level, each an array of `[valid_time, value]` pairs — each series keeps
    its OWN timestamps (`ForecastEnsemble` allows members with different
    timelines)."""
    is_members = ensemble.representation == EnsembleRepresentation.MEMBERS
    key_col = "member_id" if is_members else "quantile"
    series: dict[str, list[list[object]]] = {}
    for row in ensemble.values.sort([key_col, "valid_time"]).iter_rows(named=True):
        key = str(row[key_col])
        series.setdefault(key, []).append(
            [row["valid_time"].isoformat(), _nonfinite_encode(row["value"])]
        )
    return series


def _decode_series(
    raw: dict[str, list[list[object]]],
) -> dict[str, tuple[tuple[UtcDatetime, float], ...]]:
    result: dict[str, tuple[tuple[UtcDatetime, float], ...]] = {}
    for key, points in raw.items():
        decoded: list[tuple[UtcDatetime, float]] = []
        for point in points:
            valid_time_iso, value_raw = point[0], point[1]
            decoded.append(
                (
                    utc_from_row(datetime.fromisoformat(str(valid_time_iso))),
                    _nonfinite_decode(value_raw),
                )
            )
        result[key] = tuple(decoded)
    return result


def _qc_flags_json(flags: tuple[QcFlag, ...]) -> list[dict[str, object]]:
    return [
        {
            "rule_id": f.rule_id,
            "rule_version": f.rule_version,
            "status": f.status.value,
            "detail": f.detail,
        }
        for f in flags
    ]


def _parse_qc_flags(raw: object) -> tuple[QcFlag, ...]:
    items: list[dict[str, object]] = raw or []  # type: ignore[assignment]
    return tuple(
        QcFlag(
            rule_id=f["rule_id"],  # type: ignore[arg-type]
            rule_version=f["rule_version"],  # type: ignore[arg-type]
            status=QcStatus(f["status"]),
            detail=f.get("detail"),  # type: ignore[arg-type]
        )
        for f in items
    )


def _build_rows(entry: RejectedForecastEntry) -> list[dict[str, object]]:
    payload = entry.payload
    rows: list[dict[str, object]] = []
    for param in payload.parameters:
        ensemble = param.ensemble
        rows.append(
            {
                "id": uuid4(),
                "attempt_id": entry.attempt_id,
                "data_use": payload.data_use.value,
                "input_lineage": payload.input_lineage.content
                if payload.input_lineage
                else None,
                "station_id": payload.station_id,
                "model_id": payload.model_id,
                "model_artifact_id": payload.model_artifact_id,
                "group_id": payload.group_id,
                "issued_at": payload.issued_at,
                "parameter": ensemble.parameter,
                "units": ensemble.units,
                "representation": ensemble.representation.value,
                "time_step_seconds": int(ensemble.time_step.total_seconds()),
                "values": _encode_series(ensemble),
                "qc_status": param.qc_status.value,
                "qc_flags": _qc_flags_json(param.qc_flags),
            }
        )
    return rows


def _row_to_domain(row: RowMapping) -> PersistedRejectedForecast:
    return PersistedRejectedForecast(
        id=RejectedForecastId(row["id"]),
        attempt_id=row["attempt_id"],
        recorded_at=utc_from_row(row["recorded_at"]),
        station_id=StationId(row["station_id"]),
        model_id=ModelId(row["model_id"]),
        model_artifact_id=(
            ArtifactId(row["model_artifact_id"])
            if row["model_artifact_id"] is not None
            else None
        ),
        group_id=(
            StationGroupId(row["group_id"]) if row["group_id"] is not None else None
        ),
        issued_at=utc_from_row(row["issued_at"]),
        parameter=row["parameter"],
        representation=EnsembleRepresentation(row["representation"]),
        units=row["units"],
        time_step_seconds=row["time_step_seconds"],
        qc_status=QcStatus(row["qc_status"]),
        qc_flags=_parse_qc_flags(row["qc_flags"]),
        values=_decode_series(row["values"]),
        data_use=ForecastDataUse(row["data_use"]),
        input_lineage=ForecastInputLineage.from_content(row["input_lineage"])
        if row["input_lineage"] is not None
        else None,
    )


class PgRejectedForecastStore:
    def __init__(
        self,
        conn: sa.Connection,
        *,
        transaction_factory: Callable[[], ContextManager[sa.Connection]] | None,
        data_use: ForecastDataUse = ForecastDataUse.STANDARD,
    ) -> None:
        """`transaction_factory` has NO default (T1): every caller must
        decide explicitly. `api/deps.py` passes `None` — the API reads on
        its request connection and never writes; a write on a store built
        with `None` raises `ConfigurationError`."""
        self._conn = conn
        self._begin = transaction_factory
        if not isinstance(cast("object", data_use), ForecastDataUse):
            raise ValueError("rejected forecast store requires a typed purpose")
        self._data_use = data_use

    def write_batch(
        self,
        entries: Sequence[RejectedForecastEntry],
        *,
        abandon: threading.Event,
    ) -> None:
        if self._begin is None:
            raise ConfigurationError(
                "RejectedForecastStore.write_batch requires a transaction_factory "
                "— this store was built with none (read-only construction)"
            )
        for entry in entries:
            validate_rejected_assignment(entry.payload, self._data_use)
        rows = [row for entry in entries for row in _build_rows(entry)]
        with self._begin() as txn:
            txn.execute(sa.text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
            txn.execute(
                sa.text(f"SET LOCAL statement_timeout = '{_STATEMENT_TIMEOUT}'")
            )
            if rows:
                txn.execute(sa.insert(rejected_forecasts), rows)
            # D6 — checked INSIDE the transaction, immediately before COMMIT:
            # a save the caller gave up waiting on must never land after
            # being logged as timed out.
            if abandon.is_set():
                raise CaptureAbandonedError(
                    "rejected-forecast capture abandoned before commit"
                )

    def fetch_rejected_forecasts(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
        model_id: ModelId | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[PersistedRejectedForecast], int]:
        filters = [
            rejected_forecasts.c.data_use == self._data_use.value,
            rejected_forecasts.c.station_id == station_id,
            rejected_forecasts.c.issued_at >= start,
            rejected_forecasts.c.issued_at < end,
        ]
        if model_id is not None:
            filters.append(rejected_forecasts.c.model_id == model_id)
        where = sa.and_(*filters)

        total: int = self._conn.execute(
            sa.select(sa.func.count()).select_from(rejected_forecasts).where(where)
        ).scalar_one()

        rows = (
            self._conn.execute(
                sa.select(rejected_forecasts)
                .where(where)
                .order_by(
                    rejected_forecasts.c.issued_at.desc(),
                    rejected_forecasts.c.recorded_at.desc(),
                    rejected_forecasts.c.id.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
            .mappings()
            .all()
        )
        return [_row_to_domain(row) for row in rows], total
