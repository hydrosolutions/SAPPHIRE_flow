from __future__ import annotations

from typing import TYPE_CHECKING, Literal, cast

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sapphire_flow.db.metadata import forecast_weather_summaries, forecasts
from sapphire_flow.store.weather_summary_codec import (
    SUMMARY_SCHEMA_VERSION,
    decode_weather_summary,
    encode_weather_summary,
    summary_digest,
)
from sapphire_flow.types.weather_summary import PersistedWeatherSummary

if TYPE_CHECKING:
    from sapphire_flow.types.enums import ForecastDataUse
    from sapphire_flow.types.ids import ForecastId


def fetch_weather_summary(
    conn: sa.Connection,
    forecast_id: ForecastId,
    *,
    data_use: ForecastDataUse,
) -> PersistedWeatherSummary | None:
    row = (
        conn.execute(
            sa.select(forecast_weather_summaries)
            .join(
                forecasts,
                forecasts.c.id == forecast_weather_summaries.c.forecast_id,
            )
            .where(
                forecasts.c.id == forecast_id,
                forecasts.c.data_use == data_use.value,
                forecast_weather_summaries.c.schema_version == SUMMARY_SCHEMA_VERSION,
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    if row["weather_evidence_version"] not in (1, 2):
        raise ValueError("unsupported weather summary evidence version")
    return PersistedWeatherSummary(
        schema_version=row["schema_version"],
        weather_evidence_version=cast("Literal[1, 2]", row["weather_evidence_version"]),
        manifest_sha256=row["manifest_sha256"],
        snapshot_sha256=row["snapshot_sha256"],
        summary=decode_weather_summary(row["summary_json"], row["summary_sha256"]),
    )


def insert_weather_summary(
    conn: sa.Connection,
    forecast_id: ForecastId,
    projection: PersistedWeatherSummary,
    *,
    data_use: ForecastDataUse,
) -> None:
    payload = encode_weather_summary(projection.summary)
    conn.execute(
        pg_insert(forecast_weather_summaries)
        .values(
            forecast_id=forecast_id,
            schema_version=projection.schema_version,
            weather_evidence_version=projection.weather_evidence_version,
            manifest_sha256=projection.manifest_sha256,
            snapshot_sha256=projection.snapshot_sha256,
            summary_json=payload,
            summary_sha256=summary_digest(payload),
        )
        .on_conflict_do_nothing(index_elements=["forecast_id", "schema_version"])
    )
    if fetch_weather_summary(conn, forecast_id, data_use=data_use) != projection:
        raise ValueError("retained weather summary differs from validated evidence")
