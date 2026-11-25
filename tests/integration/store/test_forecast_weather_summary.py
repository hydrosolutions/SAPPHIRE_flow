from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi import HTTPException

from sapphire_flow.api.weather_evidence import read_weather_summary
from sapphire_flow.db.metadata import forecast_weather_summaries, stations
from sapphire_flow.store.weather_summary_backfill import (
    BackfillMode,
    BackfillResult,
    backfill_weather_summary,
    check_backfill_scope,
)
from sapphire_flow.types.enums import ForecastDataUse
from sapphire_flow.types.ids import ForecastId, TenantId
from tests.integration.store.test_selected_weather_evidence import (
    selected,
)
from tests.integration.store.test_selected_weather_evidence import (
    stored_case as stored_case,
)

if TYPE_CHECKING:
    from sapphire_flow.store.forecast_store import PgForecastStore
    from sapphire_flow.types.forecast import OperationalForecast


def read(store: PgForecastStore, forecast: OperationalForecast) -> object:
    result = read_weather_summary(
        version=forecast.weather_evidence_version,
        forecast_id=forecast.id,
        station_id=forecast.station_id,
        model_id=forecast.model_id,
        issue_time=forecast.issued_at,
        data_use=forecast.data_use,
        store=store,
    )
    assert result is not None
    return result.model_dump(mode="json")


def test_projection_matches_fallback_without_replaying_weather(
    stored_case: tuple[PgForecastStore, OperationalForecast],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, base = stored_case
    forecast = selected(base)
    store.store_forecast(forecast)
    projection = store.fetch_weather_summary(forecast.id)
    assert projection is not None
    with monkeypatch.context() as patch:
        patch.setattr(store, "fetch_weather_summary", lambda fid: None)
        expected = read(store, forecast)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("projected detail must not replay weather")

    monkeypatch.setattr(
        "sapphire_flow.api.weather_evidence.restore_selected_weather", forbidden
    )
    assert read(store, forecast) == expected
    assert read(store, forecast) == expected
    assert store.store_forecast(forecast) == forecast.id
    assert store.fetch_weather_summary(forecast.id) == projection


@pytest.mark.parametrize(
    "field",
    [
        "manifest_sha256",
        "snapshot_sha256",
        "schema_version",
        "weather_evidence_version",
        "station_id",
        "issue_time",
    ],
)
def test_invalid_projection_fails_closed(
    stored_case: tuple[PgForecastStore, OperationalForecast],
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    from datetime import timedelta

    store, base = stored_case
    forecast = selected(base)
    store.store_forecast(forecast)
    projection = store.fetch_weather_summary(forecast.id)
    assert projection is not None
    if field == "station_id":
        from sapphire_flow.types.ids import StationId

        projection = replace(
            projection,
            summary=replace(projection.summary, station_id=StationId(uuid4())),
        )
    elif field == "issue_time":
        projection = replace(
            projection,
            summary=replace(
                projection.summary,
                issue_time=projection.summary.issue_time + timedelta(days=1),
            ),
        )
    else:
        projection = replace(
            projection, **{field: 99 if field.endswith("version") else "0" * 64}
        )
    monkeypatch.setattr(store, "fetch_weather_summary", lambda fid: projection)
    with pytest.raises(
        HTTPException, match="Selected weather evidence is unavailable"
    ) as error:
        read(store, forecast)
    assert error.value.status_code == 503


def test_atomic_insert_and_immutable_projection(
    db_connection: sa.Connection,
    stored_case: tuple[PgForecastStore, OperationalForecast],
) -> None:
    store, base = stored_case
    forecast = selected(base)
    store.store_forecast(forecast)
    for sql in (
        "UPDATE forecast_weather_summaries SET schema_version = 2",
        "DELETE FROM forecast_weather_summaries",
        "TRUNCATE forecast_weather_summaries",
    ):
        with (
            pytest.raises(sa.exc.DBAPIError, match="immutable|append.only"),
            db_connection.begin_nested(),
        ):
            db_connection.execute(sa.text(sql))
    assert store.fetch_weather_summary(forecast.id) is not None


def test_backfill_dry_run_apply_retry_and_scope(
    db_connection: sa.Connection,
    stored_case: tuple[PgForecastStore, OperationalForecast],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, base = stored_case
    forecast = selected(base)
    # Simulate a forecast written by a pre-projection worker.
    with monkeypatch.context() as patch:
        patch.setattr(
            "sapphire_flow.store.forecast_weather_summary.insert_weather_summary",
            lambda *args, **kwargs: None,
        )
        store.store_forecast(forecast)
    tenant = db_connection.execute(
        sa.select(stations.c.tenant_id).where(stations.c.id == forecast.station_id)
    ).scalar_one()
    kwargs = dict(tenant_id=tenant, data_use=ForecastDataUse.STANDARD)
    assert (
        backfill_weather_summary(
            db_connection, forecast.id, mode=BackfillMode.DRY_RUN, **kwargs
        )
        is BackfillResult.WOULD_CREATE
    )
    assert store.fetch_weather_summary(forecast.id) is None
    with pytest.raises(ValueError, match="tenant and purpose"):
        check_backfill_scope(
            db_connection, [forecast.id, ForecastId(uuid4())], **kwargs
        )
    with pytest.raises(ValueError, match="tenant and purpose"):
        backfill_weather_summary(
            db_connection,
            forecast.id,
            tenant_id=TenantId(uuid4()),
            data_use=ForecastDataUse.STANDARD,
            mode=BackfillMode.APPLY,
        )
    with pytest.raises(ValueError, match="tenant and purpose"):
        backfill_weather_summary(
            db_connection,
            forecast.id,
            tenant_id=tenant,
            data_use=ForecastDataUse.PROVISIONAL_RATING,
            mode=BackfillMode.APPLY,
        )
    assert store.fetch_weather_summary(forecast.id) is None
    assert (
        backfill_weather_summary(
            db_connection, forecast.id, mode=BackfillMode.APPLY, **kwargs
        )
        is BackfillResult.CREATED
    )
    assert (
        backfill_weather_summary(
            db_connection, forecast.id, mode=BackfillMode.APPLY, **kwargs
        )
        is BackfillResult.UNCHANGED
    )
    assert (
        db_connection.execute(
            sa.select(sa.func.count()).select_from(forecast_weather_summaries)
        ).scalar_one()
        == 1
    )


def test_summary_failure_rolls_back_forecast(
    db_connection: sa.Connection,
    stored_case: tuple[PgForecastStore, OperationalForecast],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, base = stored_case
    forecast = selected(base)

    def refuse(*args: object, **kwargs: object) -> None:
        raise ValueError("projection refused")

    monkeypatch.setattr(
        "sapphire_flow.store.forecast_weather_summary.insert_weather_summary", refuse
    )
    with pytest.raises(ValueError, match="projection refused"):
        store.store_forecast(forecast)
    assert store.fetch_forecast(forecast.id) is None
    assert store.fetch_weather_summary(forecast.id) is None


@pytest.mark.parametrize(
    "fault",
    [
        "manifest_sha256",
        "summary_sha256",
        "snapshot_sha256",
        "station_id",
        "issue_time",
        "weather_evidence_version",
        "parent",
    ],
)
def test_parent_trigger_rejects_mismatched_digest(
    db_connection: sa.Connection,
    stored_case: tuple[PgForecastStore, OperationalForecast],
    fault: str,
) -> None:
    store, base = stored_case
    forecast = selected(base)
    store.store_forecast(forecast)
    row = dict(
        db_connection.execute(
            sa.select(forecast_weather_summaries).where(
                forecast_weather_summaries.c.forecast_id == forecast.id
            )
        )
        .mappings()
        .one()
    )
    import json

    from sapphire_flow.store.weather_summary_codec import summary_digest

    row["schema_version"] = 2
    if fault in ("station_id", "issue_time"):
        summary = json.loads(row["summary_json"])
        summary[fault] = (
            str(uuid4()) if fault == "station_id" else "2000-01-01T00:00:00Z"
        )
        row["summary_json"] = json.dumps(summary)
        row["summary_sha256"] = summary_digest(row["summary_json"])
    elif fault == "parent":
        row["forecast_id"] = uuid4()
    elif fault == "weather_evidence_version":
        row[fault] = 2
    else:
        row[fault] = "0" * 64
    with (
        pytest.raises(
            sa.exc.DBAPIError, match="parent binding mismatch|parent missing"
        ),
        db_connection.begin_nested(),
    ):
        db_connection.execute(forecast_weather_summaries.insert().values(**row))


def test_summary_browser_and_store_preserve_purpose(
    db_connection: sa.Connection,
) -> None:
    from sapphire_flow.api.routes import tables
    from sapphire_flow.store.forecast_store import PgForecastStore
    from tests.integration.api.test_dashboard_forecasts import (
        _client,
        app_overrides_clear,
    )
    from tests.integration.store.test_forecast_store import savepoint_factory
    from tests.integration.store.test_native_rating_forecasts import native_pair

    ordinary, provisional = (selected(item) for item in native_pair(db_connection))
    for forecast in (ordinary, provisional):
        PgForecastStore(
            db_connection,
            data_use=forecast.data_use,
            transaction_factory=savepoint_factory(db_connection),
        ).store_forecast(forecast)
    standard = PgForecastStore(db_connection)
    rating = PgForecastStore(db_connection, data_use=ForecastDataUse.PROVISIONAL_RATING)
    assert standard.fetch_weather_summary(provisional.id) is None
    assert rating.fetch_weather_summary(ordinary.id) is None
    assert rating.fetch_weather_summary(provisional.id) is not None
    tables._reflected = None
    client = _client(db_connection)
    try:
        response = client.get("/tables/forecast_weather_summaries/")
        assert response.status_code == 200
        assert str(ordinary.id) in response.text
        assert str(provisional.id) not in response.text
    finally:
        client.close()
        app_overrides_clear()
        tables._reflected = None


@pytest.mark.parametrize("members", [1, 2])
def test_v2_group_projection_matches_replay(
    stored_case: tuple[PgForecastStore, OperationalForecast],
    monkeypatch: pytest.MonkeyPatch,
    members: int,
) -> None:
    import random

    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.config.operational_weather import (
        OperationalPastWeatherRoute,
        OperationalWeatherConfig,
    )
    from sapphire_flow.services.forecast_evidence import capture_group_evidence
    from sapphire_flow.types.domain import ForecastQcRuleSet
    from sapphire_flow.types.forecast_evidence import StationSourceEvidence
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.operational_weather import WeatherUnused
    from sapphire_flow.types.operational_weather_v2 import WeatherUseV2
    from tests.fakes.weather_periods import GRID
    from tests.unit.adapters.test_forecast_interface_adapter_predict import (
        _group_model_inputs,
    )

    store, base = stored_case
    ids = (base.station_id,) if members == 1 else (base.station_id, StationId(uuid4()))
    sources = tuple(
        (
            sid,
            StationSourceEvidence(
                weather_use=WeatherUseV2(
                    grid=GRID,
                    eligibility_cutoff=base.issued_at,
                    station_id=sid,
                    issue_time=base.issued_at,
                    used_at=base.issued_at,
                    attempt=None,
                    refresh_finished_at=None,
                    past=WeatherUnused(),
                    future=WeatherUnused(),
                )
            ),
        )
        for sid in ids
    )
    inputs = replace(
        _group_model_inputs(),
        station_ids=ids,
        issue_time=base.issued_at,
        time_grid=GRID,
        source_evidence=sources,
    )
    evidence = capture_group_evidence(
        inputs=inputs,
        model=object(),
        model_id=base.model_id,
        artifact_bytes=b"artifact",
        rng_state=random.Random(1).getstate(),
        config=DeploymentConfig(
            max_retention_days=1000,
            operational_weather=OperationalWeatherConfig(
                route=OperationalPastWeatherRoute.RECAP_COMBINED
            ),
        ),
        qc_rules=ForecastQcRuleSet(version="test", rules=()),
        qc_overrides=[],
        baselines_by_station={},
        water_level_datums_masl={},
    )
    forecast = replace(base, evidence=evidence, weather_evidence_version=2)
    store.store_forecast(forecast)
    actual = read(store, forecast)
    monkeypatch.setattr(store, "fetch_weather_summary", lambda fid: None)
    assert read(store, forecast) == actual


def test_legacy_identical_retry_leaves_projection_absent(
    stored_case: tuple[PgForecastStore, OperationalForecast],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, base = stored_case
    forecast = selected(base)
    with monkeypatch.context() as patch:
        patch.setattr(
            "sapphire_flow.store.forecast_weather_summary.insert_weather_summary",
            lambda *args, **kwargs: None,
        )
        store.store_forecast(forecast)
    assert store.store_forecast(forecast) == forecast.id
    assert store.fetch_weather_summary(forecast.id) is None
