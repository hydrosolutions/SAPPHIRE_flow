from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Literal, cast

import sqlalchemy as sa

from sapphire_flow.db.metadata import forecasts, stations
from sapphire_flow.services.selected_weather_evidence import restore_selected_weather
from sapphire_flow.services.weather_summary import summarize_weather_use
from sapphire_flow.services.weather_summary_parent import validate_summary_parent
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.forecast_weather_summary import insert_weather_summary
from sapphire_flow.store.weather_summary_codec import bind_weather_summary

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sapphire_flow.types.enums import ForecastDataUse
    from sapphire_flow.types.ids import ForecastId, TenantId


class BackfillMode(Enum):
    DRY_RUN = "dry_run"
    APPLY = "apply"


class BackfillResult(Enum):
    NOT_APPLICABLE = "not_applicable"
    UNCHANGED = "unchanged"
    WOULD_CREATE = "would_create"
    CREATED = "created"


def check_backfill_scope(
    conn: sa.Connection,
    forecast_ids: Sequence[ForecastId],
    *,
    tenant_id: TenantId,
    data_use: ForecastDataUse,
) -> None:
    if not 1 <= len(forecast_ids) <= 100 or len(set(forecast_ids)) != len(forecast_ids):
        raise ValueError("provide 1–100 distinct forecast IDs")
    found = set(
        conn.execute(
            sa.select(forecasts.c.id)
            .join(stations, stations.c.id == forecasts.c.station_id)
            .where(
                forecasts.c.id.in_(forecast_ids),
                stations.c.tenant_id == tenant_id,
                forecasts.c.data_use == data_use.value,
            )
        ).scalars()
    )
    if found != set(forecast_ids):
        raise ValueError(
            "requested forecasts are not all in the requested tenant and purpose"
        )


def backfill_weather_summary(
    conn: sa.Connection,
    forecast_id: ForecastId,
    *,
    tenant_id: TenantId,
    data_use: ForecastDataUse,
    mode: BackfillMode,
) -> BackfillResult:
    check_backfill_scope(conn, [forecast_id], tenant_id=tenant_id, data_use=data_use)
    store = PgForecastStore(conn, data_use=data_use)
    forecast = store.fetch_forecast(forecast_id)
    if forecast is None:
        raise ValueError("requested forecast is unavailable")
    version = forecast.weather_evidence_version
    if version is None:
        return BackfillResult.NOT_APPLICABLE
    if type(version) is not int or version not in (1, 2):
        raise ValueError("unsupported weather evidence version")
    evidence = store.fetch_evidence(forecast_id)
    if evidence is None:
        raise ValueError("selected evidence missing")
    validate_summary_parent(
        evidence,
        version=version,
        forecast_id=forecast_id,
        station_id=forecast.station_id,
        model_id=forecast.model_id,
        issue_time=forecast.issued_at,
        data_use=data_use,
    )
    facts = restore_selected_weather(
        evidence, model_id=forecast.model_id, issue_time=forecast.issued_at
    )
    assert evidence.snapshot_sha256 is not None
    projection = bind_weather_summary(
        summarize_weather_use(facts[forecast.station_id]),
        manifest_json=evidence.manifest_json,
        snapshot_sha256=evidence.snapshot_sha256,
        weather_evidence_version=cast("Literal[1, 2]", version),
    )
    existing = store.fetch_weather_summary(forecast_id)
    if existing is not None:
        if existing != projection:
            raise ValueError("retained weather summary differs from validated evidence")
        return BackfillResult.UNCHANGED
    if mode is BackfillMode.DRY_RUN:
        return BackfillResult.WOULD_CREATE
    insert_weather_summary(conn, forecast_id, projection, data_use=data_use)
    return BackfillResult.CREATED
