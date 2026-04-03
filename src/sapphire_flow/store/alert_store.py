# pyright: reportUnknownMemberType=false
from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sapphire_flow.db.metadata import alerts
from sapphire_flow.store._helpers import utc_from_row, utc_or_none
from sapphire_flow.types.alert import Alert
from sapphire_flow.types.enums import AlertModelStrategy, AlertSource, AlertStatus
from sapphire_flow.types.ids import AlertId, ModelId, StationId

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.datetime import UtcDatetime


class PgAlertStore:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def upsert_alert(self, alert: Alert) -> AlertId:
        if alert.status == AlertStatus.RESOLVED:
            row = self._conn.execute(
                sa.insert(alerts).values(**_to_values(alert)).returning(alerts.c.id)
            ).scalar_one()
            return AlertId(row)

        if alert.station_id is not None:
            active_statuses = ["raised", "acknowledged"]
            stmt = (
                pg_insert(alerts)
                .values(**_to_values(alert))
                .on_conflict_do_update(
                    index_elements=[
                        alerts.c.station_id,
                        alerts.c.alert_level,
                        alerts.c.source,
                    ],
                    index_where=sa.and_(
                        alerts.c.status.in_(active_statuses),
                        alerts.c.station_id.isnot(None),
                    ),
                    set_=_mutable_fields(alert),
                )
                .returning(alerts.c.id)
            )
        else:
            active_statuses = ["raised", "acknowledged"]
            stmt = (
                pg_insert(alerts)
                .values(**_to_values(alert))
                .on_conflict_do_update(
                    index_elements=[
                        alerts.c.alert_level,
                        alerts.c.source,
                    ],
                    index_where=sa.and_(
                        alerts.c.status.in_(active_statuses),
                        alerts.c.station_id.is_(None),
                    ),
                    set_=_mutable_fields(alert),
                )
                .returning(alerts.c.id)
            )

        row = self._conn.execute(stmt).scalar_one()
        return AlertId(row)

    def fetch_active_alerts(
        self,
        station_id: StationId | None = None,
        source: AlertSource | None = None,
    ) -> list[Alert]:
        stmt = sa.select(alerts).where(alerts.c.status != AlertStatus.RESOLVED.value)
        if station_id is not None:
            stmt = stmt.where(alerts.c.station_id == station_id)
        if source is not None:
            stmt = stmt.where(alerts.c.source == source.value)
        rows = self._conn.execute(stmt).mappings().all()
        return [_row_to_domain(row) for row in rows]

    def resolve_alert(self, alert_id: AlertId) -> None:
        self._conn.execute(
            sa.update(alerts)
            .where(alerts.c.id == alert_id)
            .values(status=AlertStatus.RESOLVED.value, resolved_at=sa.func.now())
        )

    def acknowledge_alert(self, alert_id: AlertId, acknowledged_by: UUID) -> None:
        self._conn.execute(
            sa.update(alerts)
            .where(alerts.c.id == alert_id)
            .values(
                status=AlertStatus.ACKNOWLEDGED.value,
                acknowledged_at=sa.func.now(),
                acknowledged_by=acknowledged_by,
            )
        )

    def fetch_alert_history(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
        source: AlertSource | None = None,
    ) -> list[Alert]:
        stmt = (
            sa.select(alerts)
            .where(alerts.c.station_id == station_id)
            .where(alerts.c.triggered_at >= start)
            .where(alerts.c.triggered_at < end)
        )
        if source is not None:
            stmt = stmt.where(alerts.c.source == source.value)
        rows = self._conn.execute(stmt).mappings().all()
        return [_row_to_domain(row) for row in rows]


def _to_values(alert: Alert) -> dict:  # type: ignore[type-arg]
    return {
        "id": alert.id,
        "station_id": alert.station_id,
        "source": alert.source.value,
        "alert_level": alert.alert_level,
        "status": alert.status.value,
        "trigger_probability": alert.trigger_probability,
        "trigger_value": alert.trigger_value,
        "triggered_at": alert.triggered_at,
        "acknowledged_at": alert.acknowledged_at,
        "acknowledged_by": alert.acknowledged_by,
        "resolved_at": alert.resolved_at,
        "first_detected_at": alert.first_detected_at,
        "notified_at": alert.notified_at,
        "created_at": alert.created_at,
        "model_ids": [str(mid) for mid in alert.model_ids],
        "alert_model_strategy": alert.alert_model_strategy.value
        if alert.alert_model_strategy is not None
        else None,
    }


def _mutable_fields(alert: Alert) -> dict:  # type: ignore[type-arg]
    return {
        "status": alert.status.value,
        "trigger_probability": alert.trigger_probability,
        "trigger_value": alert.trigger_value,
        "triggered_at": alert.triggered_at,
        "acknowledged_at": alert.acknowledged_at,
        "acknowledged_by": alert.acknowledged_by,
        "resolved_at": alert.resolved_at,
        "first_detected_at": alert.first_detected_at,
        "notified_at": alert.notified_at,
        "model_ids": [str(mid) for mid in alert.model_ids],
        "alert_model_strategy": alert.alert_model_strategy.value
        if alert.alert_model_strategy is not None
        else None,
    }


def _row_to_domain(row: sa.engine.row.RowMapping) -> Alert:
    return Alert(
        id=AlertId(row["id"]),
        station_id=StationId(row["station_id"])
        if row["station_id"] is not None
        else None,
        source=AlertSource(row["source"]),
        alert_level=row["alert_level"],
        status=AlertStatus(row["status"]),
        trigger_probability=row["trigger_probability"],
        trigger_value=row["trigger_value"],
        triggered_at=utc_from_row(row["triggered_at"]),
        acknowledged_at=utc_or_none(row["acknowledged_at"]),
        acknowledged_by=row["acknowledged_by"],
        resolved_at=utc_or_none(row["resolved_at"]),
        first_detected_at=utc_or_none(row["first_detected_at"]),
        notified_at=utc_or_none(row["notified_at"]),
        created_at=utc_from_row(row["created_at"]),
        model_ids=tuple(ModelId(mid) for mid in (row["model_ids"] or [])),  # type: ignore[arg-type]
        alert_model_strategy=AlertModelStrategy(row["alert_model_strategy"])
        if row["alert_model_strategy"] is not None
        else None,
    )
