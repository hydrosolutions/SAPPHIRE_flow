from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import structlog
from sqlalchemy.exc import SQLAlchemyError

from sapphire_flow.adapters.bafu_thresholds import fetch_threshold_page
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.threshold_import import (
    ThresholdImportMode,
    bafu_thresholds,
    import_station_thresholds,
)
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.auth import AuditEntry
from sapphire_flow.types.enums import AuditEventType
from sapphire_flow.types.threshold_import import ThresholdImportStatus

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import sqlalchemy as sa

    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import TenantId
    from sapphire_flow.types.station import StationConfig

log = structlog.get_logger(__name__)


def import_one(
    *,
    station: StationConfig,
    tenant_id: TenantId,
    config: DeploymentConfig,
    engine: sa.Engine,
    client: httpx.Client,
    mode: ThresholdImportMode,
    report_dir: Path,
    clock: Callable[[], UtcDatetime],
    operator: str | None = None,
) -> dict[str, object]:
    if station.tenant_id != tenant_id:
        raise ConfigurationError("threshold station is outside the requested tenant")
    if mode is ThresholdImportMode.APPLY and config.enable_forecast_alerts:
        raise ConfigurationError(
            "threshold import requires review of enabled forecast alerts"
        )
    snapshot = fetch_threshold_page(
        client, station_code=station.code, retrieved_at=clock()
    )
    values = bafu_thresholds(station, snapshot, config.get_danger_level_definitions())
    report_dir.mkdir(parents=True, exist_ok=True)
    name = f"{station.code}-{snapshot.sha256}"
    (report_dir / f"{name}.html").write_text(snapshot.html)
    report: dict[str, object] = {
        "station_id": str(station.id),
        "station_code": station.code,
        "source": "authority",
        "url": snapshot.url,
        "retrieved_at": snapshot.retrieved_at.isoformat(),
        "sha256": snapshot.sha256,
        "bounds_m3s": snapshot.bounds,
        "levels": [t.danger_level for t in values],
        "temporal_basis": "current published bands; historical effective dates unknown",
        "status": "pending",
        "observation_alerts_enabled": config.enable_observation_alerts,
    }
    report_path = report_dir / f"{name}-{mode.value}.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    with engine.begin() as conn:
        store = PgStationStore(conn)
        current = store.fetch_station(station.id)
        if current is None or current.tenant_id != tenant_id:
            raise ConfigurationError(
                "threshold station no longer belongs to requested tenant"
            )
        status = import_station_thresholds(
            station=current,
            tenant_id=tenant_id,
            thresholds=values,
            store=store,
            mode=mode,
        )
        if status is ThresholdImportStatus.IMPORTED:
            PgAuditLogStore(conn).append_entry(
                AuditEntry.system(  # pyright: ignore[reportUnknownMemberType]
                    event_type=AuditEventType.THRESHOLDS_IMPORTED,
                    target_type="station",
                    target_id=str(station.id),
                    detail={**report, "status": status.value, "operator": operator},
                    ip_address=None,
                    created_at=clock(),
                )
            )
    report["status"] = status.value
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    return report


def make_onboarding_threshold_importer(
    *,
    engine: sa.Engine,
    config: DeploymentConfig,
    tenant_id: TenantId,
    report_dir: Path,
    clock: Callable[[], UtcDatetime],
    operator: str | None,
) -> Callable[[StationConfig], None]:
    def import_thresholds(station: StationConfig) -> None:
        if station.network != "bafu" or "discharge" not in station.measured_parameters:
            return
        try:
            with httpx.Client() as client:
                result = import_one(
                    station=station,
                    tenant_id=tenant_id,
                    config=config,
                    engine=engine,
                    client=client,
                    mode=ThresholdImportMode.APPLY,
                    report_dir=report_dir,
                    clock=clock,
                    operator=operator,
                )
            log.info("onboarding.threshold_import", **result)
        except (
            httpx.HTTPError,
            ValueError,
            ConfigurationError,
            SQLAlchemyError,
            OSError,
        ) as exc:
            log.warning(
                "onboarding.threshold_import_unavailable",
                station_code=station.code,
                reason=type(exc).__name__,
            )

    return import_thresholds
