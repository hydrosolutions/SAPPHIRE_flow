from __future__ import annotations

import hashlib
import json
import random
import zlib
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, Literal, cast
from uuid import UUID, uuid4

import pandas as pd
import polars as pl
import pytest

from sapphire_flow.adapters.recap_gateway import (
    RecapDataUnavailableError,
    RecapPayloadIntegrityError,
    StoreBackedGatewayPolygonResolver,
)
from sapphire_flow.adapters.recap_operational import (
    RecapGatewayOperationalAdapter,
    operational_binding_fingerprint,
)
from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.config.forecast_qc_rules import ForecastQcRuleSet
from sapphire_flow.config.operational_weather import (
    OperationalPastWeatherRoute,
    OperationalWeatherConfig,
)
from sapphire_flow.services.forecast_evidence import restore_snapshot
from sapphire_flow.services.operational_weather_proof import WeatherProofError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    AlertEligibility,
    ForecastCycleHealth,
    ForecastDataUse,
    InputQualityCategory,
    InputQualityLevel,
    ModelArtifactStatus,
    ModelAssignmentStatus,
    QcStatus,
    SpatialRepresentation,
    WeatherSourceRole,
)
from sapphire_flow.types.forcing_track import (
    ForcingResolutionPolicy,
    RawFetchOutcome,
    RawFetchStatus,
)
from sapphire_flow.types.forecast_evidence import ForecastEvidence
from sapphire_flow.types.ids import BasinId, ModelId, StationGroupId, StationId
from sapphire_flow.types.operational_weather import WeatherAttemptState, WeatherCacheKey
from sapphire_flow.types.station import (
    GatewayPolygonBindingRow,
    GroupModelAssignment,
    ModelAssignment,
    StationGroup,
)
from sapphire_flow.types.weather import BasinAverageForecast
from sapphire_flow.types.write_principal import WritePrincipal
from tests.conftest import make_observations, make_station_config
from tests.fakes.fake_models import FakeGroupForecastModel, FakeStationForecastModel
from tests.fakes.fake_stores import FakeStationGroupStore
from tests.fakes.operational_weather import FakeOperationalWeatherCache
from tests.unit.adapters.test_recap_operational import (
    FIXTURES,
    ISSUE,
    make_binding,
)
from tests.unit.flows.test_run_forecast_cycle import (
    _block_real_httpx_network,  # noqa: F401 -- imported autouse network guard
    _FakeCandidateAwareSource,
    _make_full_stores,
    _run_cycle_with_stores,
)
from tests.unit.services.test_recap_daily_weather import qualified_rows

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.adapters.recap_gateway import EcmwfApiLike
    from sapphire_flow.flows.run_forecast_cycle import ForecastCycleResult
    from sapphire_flow.protocols.forecast_model import ForecastModel
    from sapphire_flow.protocols.stores import OperationalWeatherCacheStore
    from sapphire_flow.services.selected_weather_evidence import (
        SelectedWeatherIssuanceRefusal,
    )
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.forecast import OperationalForecast
    from sapphire_flow.types.forecast_evidence import StationSourceEvidence
    from sapphire_flow.types.weather import WeatherForecastRecord


_DEFAULT_STATION_ID = StationId(UUID(int=1))
_DEFAULT_MODEL_ID = ModelId("selected_weather_fixture")


class FixtureOperationalApi:
    def __init__(self, before_fetch: Callable[[], None]) -> None:
        self.calls: list[dict[str, object]] = []
        self.before_fetch = before_fetch

    def operational(self, **kwargs: object) -> object:
        self.before_fetch()
        self.calls.append(kwargs)
        name = "precipitation" if kwargs["ifs_variable_name"] == "tp" else "temperature"
        return pd.read_parquet(FIXTURES / f"{name}.parquet")


class PolygonStore:
    def __init__(self, rows: list[GatewayPolygonBindingRow]) -> None:
        self.rows = rows

    def fetch_bindings_for_station(
        self, station_id: StationId
    ) -> list[GatewayPolygonBindingRow]:
        return [row for row in self.rows if station_id == row.station_id]


@pytest.mark.parametrize(
    "capture_fault", ["none", "missing", "hash", "changed_fact", "oversize"]
)
@pytest.mark.parametrize("group_run", [False, True])
def test_selected_native_cycle_uses_committed_weather_and_consumed_members(
    monkeypatch: pytest.MonkeyPatch,
    group_run: bool,
    capture_fault: Literal["none", "missing", "hash", "changed_fact", "oversize"],
) -> None:
    _run_selected_cycle(monkeypatch, group_run=group_run, capture_fault=capture_fault)


def test_selected_native_21_station_group_snapshot_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _run_selected_cycle(
        monkeypatch,
        group_run=True,
        capture_fault="none",
        station_count=21,
        lookback_days=30,
    )


def _run_selected_cycle(
    monkeypatch: pytest.MonkeyPatch,
    *,
    group_run: bool,
    capture_fault: Literal["none", "missing", "hash", "changed_fact", "oversize"],
    station_count: int = 1,
    first_station_id: StationId = _DEFAULT_STATION_ID,
    model_id: ModelId = _DEFAULT_MODEL_ID,
    configure_cycle: Callable[
        [dict[str, object], FakeStationGroupStore | None, DeploymentConfig, ModelId],
        dict[str, object],
    ]
    | None = None,
    configure_refresh: Callable[[dict[str, object]], dict[str, object]] | None = None,
    verify_cycle: Callable[[ForecastCycleResult, dict[str, object]], None]
    | None = None,
    execution_runs: int = 1,
    past_refresh_error: Exception | None = None,
    lookback_days: int = 2,
    model_override: ForecastModel | None = None,
    additional_models: dict[ModelId, ForecastModel] | None = None,
    expected_health: ForecastCycleHealth | None = None,
    omitted_observation_days: frozenset[int] = frozenset(),
    missing_observation_days: frozenset[int] = frozenset(),
    expected_forecasts: int | None = None,
    consumed_future_count: int = 128,
    future_support_days: int | None = None,
    data_use: ForecastDataUse = ForecastDataUse.STANDARD,
    weather_legs: Literal["both", "past", "future", "unused"] = "both",
    refresh_case: Literal[
        "none",
        "past_unavailable",
        "future_unavailable",
        "past_fatal",
        "future_fatal",
        "expired",
        "expired_success",
        "retained_subset",
        "retained_corrupt",
        "rollover",
        "mixed_cycles",
    ] = "none",
) -> list[OperationalForecast] | None:
    stores = _make_full_stores()
    if data_use is ForecastDataUse.PROVISIONAL_RATING:
        from tests.fakes.fake_stores import FakeForecastStore

        stores["forecast_store"] = FakeForecastStore(data_use=data_use)
        omitted_observation_days = omitted_observation_days | {lookback_days - 1}
    sources = [
        replace(
            make_binding(), station_id=StationId(UUID(int=first_station_id.int + index))
        )
        for index in range(station_count)
    ]
    station_store = stores["station_store"]
    stations = [
        make_station_config(
            station_id=source.station_id,
            basin_id=BasinId(uuid4()),
            measured_parameters=frozenset({"discharge"}),
            forecast_targets=frozenset({"discharge"}),
        )
        for source in sources
    ]
    for station, source in zip(stations, sources, strict=True):
        station_store.store_station(station)
        station_store.store_weather_source(source)
        station_store.store_weather_source(
            replace(source, nwp_source="ifs_ecmwf", role=WeatherSourceRole.FORECAST)
        )
    station, source = stations[0], sources[0]
    sid = station.id
    mid = model_id
    step = timedelta(days=1)
    group_store = FakeStationGroupStore() if group_run else None
    if model_override is None:
        model = FakeGroupForecastModel() if group_run else FakeStationForecastModel()
        model.alert_eligibility = AlertEligibility.SKILL_FORECAST
        model.data_requirements = replace(
            model.data_requirements,
            supported_time_steps=frozenset({step}),
            lookback_steps=lookback_days,
            forecast_horizon_steps=10,
            past_dynamic_features=frozenset({"precipitation", "temperature"})
            if weather_legs in ("both", "past")
            else frozenset(),
            future_dynamic_features=frozenset({"precipitation", "temperature"})
            if weather_legs in ("both", "future")
            else frozenset(),
            static_features=frozenset(),
            spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
        )
    else:
        model = model_override
    artifact_scope = {}
    if group_store is None:
        station_store.store_model_assignment(
            ModelAssignment(
                station_id=sid,
                model_id=mid,
                time_step=step,
                status=ModelAssignmentStatus.ACTIVE,
                priority=1,
                created_at=ISSUE,
            )
        )
        artifact_scope["station_id"] = sid
    else:
        group_id = StationGroupId(uuid4())
        group_store.store_group(
            StationGroup(
                id=group_id,
                name="weather fixture",
                station_ids=frozenset(station.id for station in stations),
                created_at=ISSUE,
            )
        )
        group_store.store_group_model_assignment(
            GroupModelAssignment(
                group_id=group_id,
                model_id=mid,
                time_step=step,
                status=ModelAssignmentStatus.ACTIVE,
                priority=1,
                created_at=ISSUE,
            )
        )
        artifact_scope["group_id"] = group_id
    stores["artifact_store"].store_artifact(
        model_id=mid,
        artifact_bytes=b"fake_artifact",
        training_period_start=ensure_utc(ISSUE - timedelta(days=100)),
        training_period_end=ensure_utc(ISSUE - timedelta(days=10)),
        trained_at=ISSUE,
        status=ModelArtifactStatus.ACTIVE,
        **artifact_scope,
    )
    for station in stations:
        stores["obs_store"].store_observations(
            [
                replace(row, value=None, qc_status=QcStatus.MISSING)
                if (
                    row.timestamp.date()
                    - (ISSUE - timedelta(days=lookback_days)).date()
                ).days
                in missing_observation_days
                else row
                for row in make_observations(
                    n=lookback_days * 24,
                    rng=random.Random(station.id.int),
                    station_id=station.id,
                    parameter="discharge",
                    start=ensure_utc(ISSUE - timedelta(days=lookback_days)),
                    interval=timedelta(hours=1),
                )
                if (
                    row.timestamp.date()
                    - (ISSUE - timedelta(days=lookback_days)).date()
                ).days
                not in omitted_observation_days
            ]
        )
    polygons = [
        name
        for name in pd.read_parquet(FIXTURES / "precipitation.parquet").columns
        if name.startswith("g_")
    ]
    polygon_store = PolygonStore(
        [
            GatewayPolygonBindingRow(
                station_id=station.id,
                basin_id=station.basin_id,
                gateway_hru_name="nepal21_20261012",
                name=name,
                spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                band_id=None,
            )
            for station, name in zip(stations, polygons[:station_count], strict=True)
        ]
    )
    resolver = StoreBackedGatewayPolygonResolver(polygon_store)
    keys = []
    for source in sources:
        ref = resolver.resolve(source)
        assert ref is not None
        keys.append(
            WeatherCacheKey(
                station_id=source.station_id,
                binding_fingerprint=operational_binding_fingerprint(source, ref),
            )
        )
    cache: OperationalWeatherCacheStore = FakeOperationalWeatherCache(
        station_store=station_store
    )

    def require_pending() -> None:
        for key in keys:
            entry = cache.fetch(key)
            assert entry is not None and entry.state is WeatherAttemptState.PENDING

    api = FixtureOperationalApi(require_pending)
    config = OperationalWeatherConfig(route=OperationalPastWeatherRoute.RECAP_COMBINED)
    past = RecapGatewayOperationalAdapter(
        api=cast("EcmwfApiLike", api), resolver=resolver, config=config
    )
    first_cycle = (
        ensure_utc(ISSUE - timedelta(days=1))
        if refresh_case == "mixed_cycles"
        else ISSUE
    )
    rows_by_station = {
        station.id: [
            replace(row, station_id=station.id, id=uuid4())
            for row in qualified_rows(first_cycle)
            if future_support_days is None
            or row.valid_time < ISSUE + timedelta(days=future_support_days)
        ]
        for station in stations
    }
    # Prior ensemble rows must not enter control-only evidence.
    for rows in rows_by_station.values():
        stores["nwp_store"].store_weather_forecasts(
            [
                replace(row, id=uuid4(), member_id=member)
                for member in range(1, 51)
                for row in rows
            ]
        )
    future_results = {
        first_cycle: {
            station_id: BasinAverageForecast(
                nwp_source="ifs_ecmwf",
                cycle_time=first_cycle,
                values=pl.DataFrame(
                    [
                        {
                            "valid_time": row.valid_time,
                            "parameter": row.parameter,
                            "member_id": row.member_id,
                            "value": row.value,
                        }
                        for row in rows
                    ]
                ),
            )
            for station_id, rows in rows_by_station.items()
        }
    }
    future = _FakeCandidateAwareSource(results_by_cycle=future_results)
    observed = []
    method = "predict_batch" if group_run else "predict"
    original = getattr(model, method)

    def checked_predict(*args: object, **kwargs: object) -> object:
        if refresh_case == "mixed_cycles" and execution_time > ISSUE:
            pytest.fail("mixed-cycle group reached prediction")
        for key in keys:
            entry = cache.fetch(key)
            if weather_legs == "unused":
                assert entry is None
            else:
                expected_state = (
                    WeatherAttemptState.FAILED
                    if execution_time > ISSUE
                    and refresh_case not in ("none", "rollover")
                    else WeatherAttemptState.COMPLETE
                )
                assert entry is not None and entry.state is expected_state
            observed.append(entry)
        return original(*args, **kwargs)

    monkeypatch.setattr(model, method, checked_predict)
    if capture_fault != "none":
        import importlib

        runner = importlib.import_module(
            "sapphire_flow.services.run_group_forecast"
            if group_run
            else "sapphire_flow.services.run_station_forecast"
        )
        capture_name = (
            "capture_group_evidence" if group_run else "capture_station_evidence"
        )
        capture = getattr(runner, capture_name)

        def corrupt_capture(**kwargs: object) -> object:
            captured = capture(**kwargs)
            if capture_fault == "missing":
                return replace(captured, snapshot=None, snapshot_sha256=None)
            if capture_fault == "hash":
                return replace(captured, snapshot_sha256="0" * 64)
            if capture_fault == "oversize":
                payload = zlib.compress(b" " * (config.max_evidence_bytes + 1))
            else:
                decoded = restore_snapshot(captured.snapshot)
                records = (
                    decoded["source_records"][0][1]
                    if group_run
                    else decoded["source_records"]
                )
                records["weather_use"]["past"]["freshness"] = "stale"
                payload = zlib.compress(json.dumps(decoded).encode())
            digest = hashlib.sha256(payload).hexdigest()
            manifest = json.loads(captured.manifest_json)
            manifest["snapshot_sha256"] = digest
            return replace(
                captured,
                snapshot=payload,
                snapshot_sha256=digest,
                manifest_json=json.dumps(manifest),
            )

        monkeypatch.setattr(runner, capture_name, corrupt_capture)
    rating_inputs = None
    opt_ins = ()
    if data_use is ForecastDataUse.PROVISIONAL_RATING:
        from tests.unit.flows.test_selected_weather_native_rating import rating_fixture

        rating_inputs, opt_ins = rating_fixture(stations[0], mid, lookback_days)
    execution_time = ISSUE
    deployment_config = DeploymentConfig(
        max_retention_days=3650, operational_weather=config, provisional_ratings=opt_ins
    )
    cycle_options: dict[str, object] = {
        "config": deployment_config,
        "data_use": data_use,
        "rating_inputs": rating_inputs,
        "qc_rules": ForecastQcRuleSet(version="fixture", rules=()),
        "clock": lambda: execution_time,
        "cycle_time": ISSUE.isoformat(),
        "gateway_polygon_store": polygon_store,
        "group_store": group_store,
        "operational_weather_cache": cache,
        "operational_weather_adapter": past,
        "operational_weather_principal": WritePrincipal(
            id=None, tenant_id=station.tenant_id
        ),
    }
    if refresh_case == "mixed_cycles":
        cycle_options["config"] = DeploymentConfig.model_validate(
            {
                **deployment_config.model_dump(),
                "nwp_max_fallback_age_hours": 48,
                "nwp_grid_retention_days": 3,
            }
        )
        cycle_options["forcing_resolution_policy"] = ForcingResolutionPolicy(
            cycle_cadence_hours=6, max_cycle_age_hours=48, max_retries=0
        )
    if configure_cycle is not None:
        cycle_options.update(
            configure_cycle(stores, group_store, deployment_config, mid)
        )
        cache = cast(
            "OperationalWeatherCacheStore", cycle_options["operational_weather_cache"]
        )

    def run_cycle() -> ForecastCycleResult:
        return _run_cycle_with_stores(
            stores,
            adapter=future,
            models={mid: model, **(additional_models or {})},
            **cycle_options,
        )

    counts: list[int] = []
    for run_index in range(execution_runs):
        execution_time = ensure_utc(ISSUE + timedelta(hours=6 * run_index))
        result = run_cycle()
        counts.append(result.forecasts_stored)
    if verify_cycle is not None:
        verify_cycle(result, stores)
        return [
            forecast
            for forecast in stores["forecast_store"].fetch_forecasts_for_cycle(ISSUE)
            if forecast.station_id in {station.id for station in stations}
            and forecast.model_id == mid
        ]
    if refresh_case != "none":
        from tests.fakes.fake_stores import FakeForecastStore

        assert result.forecasts_stored == station_count, result
        first_entries = [cache.fetch(key) for key in keys]
        assert all(entry is not None for entry in first_entries)
        # Isolate the second capture from immutable forecast retry identity; the
        # cache and actual archive remain the same. Retry persistence has its own
        # PostgreSQL tests.
        stores["forecast_store"] = FakeForecastStore(data_use=data_use)
        if configure_refresh is not None:
            cycle_options.update(configure_refresh(stores))
        observed.clear()
        archive = stores["nwp_store"]
        write_archive = archive.store_weather_forecasts

        def preserve_identical_archive_rows(
            records: list[WeatherForecastRecord],
        ) -> None:
            # The general fake appends; real PostgreSQL has a natural-key upsert.
            # Keep the old IDs/timestamps on an identical refresh, as that store does.
            fresh = []
            for row in records:
                matches = [
                    old
                    for old in archive.fetch_weather_forecasts(
                        row.station_id, row.nwp_source, row.cycle_time, [row.parameter]
                    )
                    if (old.valid_time, old.member_id, old.band_id, old.spatial_type)
                    == (row.valid_time, row.member_id, row.band_id, row.spatial_type)
                ]
                if matches:
                    assert len(matches) == 1
                    assert (
                        replace(row, id=matches[0].id, created_at=matches[0].created_at)
                        == matches[0]
                    )
                else:
                    fresh.append(row)
            write_archive(fresh)

        monkeypatch.setattr(
            archive, "store_weather_forecasts", preserve_identical_archive_rows
        )
        if refresh_case in ("retained_subset", "retained_corrupt"):
            model.data_requirements = replace(
                model.data_requirements, forecast_horizon_steps=5
            )
            consumed_future_count = 80
            if refresh_case == "retained_corrupt":
                read_archive = archive.fetch_weather_forecasts

                def changed_unused_tail(
                    *args: object, **kwargs: object
                ) -> list[WeatherForecastRecord]:
                    return [
                        replace(row, value=row.value + 1)
                        if row.member_id == 0
                        and row.valid_time >= ISSUE + timedelta(days=9)
                        else row
                        for row in read_archive(*args, **kwargs)
                    ]

                monkeypatch.setattr(
                    archive, "fetch_weather_forecasts", changed_unused_tail
                )
        api.calls.clear()
        future.fetch_calls.clear()
        execution_time = ensure_utc(
            ISSUE + timedelta(hours=49 if refresh_case.startswith("expired") else 1)
        )
        if refresh_case.startswith("past_"):

            def fail_past(**kwargs: object) -> object:
                require_pending()
                api.calls.append(kwargs)
                if refresh_case == "past_fatal":
                    raise RecapPayloadIntegrityError("synthetic invalid paired source")
                if past_refresh_error is not None:
                    raise past_refresh_error
                raise RecapDataUnavailableError(
                    "synthetic source unavailable", code="NO_DATA"
                )

            monkeypatch.setattr(api, "operational", fail_past)
        elif refresh_case == "rollover":

            def complete_reanalysis(**kwargs: object) -> object:
                require_pending()
                api.calls.append(kwargs)
                index = pd.date_range(
                    start=pd.Timestamp(kwargs["start_date"], tz="UTC"),
                    end=ISSUE,
                    inclusive="left",
                    freq="h",
                )
                raw_value = 0.001 if kwargs["ifs_variable_name"] == "tp" else 284.15
                return pd.DataFrame(
                    {
                        **{name: raw_value for name in polygons},
                        "source": "era5_land",
                        "source_run": index.normalize(),
                        "source_meta": '{"grid_resolution":0.25}',
                    },
                    index=index,
                )

            monkeypatch.setattr(api, "operational", complete_reanalysis)
        elif refresh_case == "mixed_cycles":
            future_results.clear()
            future_results[ISSUE] = {
                sid: BasinAverageForecast(
                    nwp_source="ifs_ecmwf",
                    cycle_time=ISSUE,
                    values=pl.DataFrame(
                        [
                            {
                                "valid_time": row.valid_time,
                                "parameter": row.parameter,
                                "member_id": row.member_id,
                                "value": row.value,
                            }
                            for row in qualified_rows()
                        ]
                    ),
                )
            }
        elif refresh_case != "expired_success":

            def fail_future(*args: object, **kwargs: object) -> object:
                require_pending()
                future.fetch_calls.append(ISSUE)
                if refresh_case == "future_fatal":
                    raise RecapPayloadIntegrityError("synthetic invalid future source")
                return RawFetchOutcome(
                    status=RawFetchStatus.ABSENT_AT_CYCLE, cycle=ISSUE, stations={}
                )

            monkeypatch.setattr(future, "fetch_requirement", fail_future)
        if refresh_case in ("past_fatal", "future_fatal"):
            with pytest.raises(RecapPayloadIntegrityError, match="synthetic invalid"):
                run_cycle()
            assert not observed
            assert stores["forecast_store"].fetch_forecasts_for_cycle(ISSUE) == []
            assert all(
                cache.fetch(key).state is WeatherAttemptState.FAILED for key in keys
            )
            return []
        if refresh_case == "retained_corrupt":
            with pytest.raises(
                WeatherProofError, match="no longer matches its complete receipt"
            ):
                run_cycle()
            assert not observed
            assert stores["forecast_store"].fetch_forecasts_for_cycle(ISSUE) == []
            return []
        result = run_cycle()
        if refresh_case == "mixed_cycles":
            assert result.forecasts_stored == 0, result
            assert not observed
            assert stores["forecast_store"].fetch_forecasts_for_cycle(ISSUE) == []
            assert any("cannot mix source cycles" in error for error in result.errors)
            return []
        if refresh_case.startswith("expired") or refresh_case == "retained_corrupt":
            if refresh_case.startswith("expired"):
                assert future.fetch_calls == []
                assert all(
                    cache.fetch(key).state is WeatherAttemptState.FAILED
                    and cache.fetch(key).future_receipts == ()
                    for key in keys
                )
            assert result.forecasts_stored == 0, result
            assert not observed
            assert stores["forecast_store"].fetch_forecasts_for_cycle(ISSUE) == []
            return []
        for index, key in enumerate(keys):
            entry = cache.fetch(key)
            expected_state = (
                WeatherAttemptState.COMPLETE
                if refresh_case == "rollover"
                else WeatherAttemptState.FAILED
            )
            assert entry is not None and entry.state is expected_state
            assert entry.attempt != first_entries[index].attempt
            if refresh_case == "rollover":
                old_snapshot = first_entries[index].past_snapshot
                assert old_snapshot is not None and entry.past_snapshot is not None
                assert any(
                    sample.source.value == "ifs"
                    for day in old_snapshot.window.days
                    for sample in day.samples
                )
                assert entry.past_snapshot.id != old_snapshot.id
                assert all(
                    sample.source.value == "era5_land"
                    for day in entry.past_snapshot.window.days
                    for sample in day.samples
                )
                assert entry.past_snapshot.window.days[-1].precipitation_mm == 24.0
    if expected_health is not None:
        assert result.health is expected_health, result
    if capture_fault != "none":
        assert result.health is ForecastCycleHealth.FAILED
        assert result.errors
        assert result.forecasts_stored == 0
        assert not observed
        assert stores["forecast_store"].fetch_forecasts_for_cycle(ISSUE) == []
        return
    if expected_forecasts == 0:
        assert result.forecasts_stored == 0, result
        assert not observed
        if future_support_days is not None:
            assert any(
                "full requested future window" in error for error in result.errors
            )
        return []
    assert (
        sum(counts) if execution_runs > 1 else result.forecasts_stored
    ) == station_count, result
    assert observed
    assert len(api.calls) == (
        1
        if refresh_case == "past_unavailable"
        else 2 * execution_runs
        if weather_legs in ("both", "past")
        else 0
    )
    if refresh_case in ("future_unavailable", "retained_subset"):
        assert future.fetch_calls
    else:
        assert future.fetch_calls == (
            [ISSUE] * execution_runs if weather_legs in ("both", "future") else []
        )
    forecasts = [
        forecast
        for forecast in stores["forecast_store"].fetch_forecasts_for_cycle(ISSUE)
        if forecast.station_id in {station.id for station in stations}
        and forecast.model_id == mid
    ]
    assert len(forecasts) == station_count
    hydrated = []
    for forecast in forecasts:
        if forecast.evidence is None:
            persisted = stores["forecast_store"].fetch_evidence(forecast.id)
            assert persisted is not None
            forecast = replace(
                forecast,
                evidence=ForecastEvidence(
                    status=persisted.status,
                    manifest_json=persisted.manifest_json,
                    snapshot=persisted.snapshot,
                    snapshot_sha256=persisted.snapshot_sha256,
                    artifact=persisted.artifact,
                    artifact_sha256=persisted.artifact_sha256,
                    reason=persisted.reason,
                    weather_evidence_version=forecast.weather_evidence_version,
                ),
            )
        hydrated.append(forecast)
    forecasts = hydrated
    evidence = forecasts[0].evidence
    assert (
        evidence is not None
        and evidence.weather_evidence_version == 1
        and evidence.snapshot is not None
    )
    decoded_size = len(zlib.decompress(evidence.snapshot))
    assert decoded_size <= config.max_evidence_bytes
    snapshot = restore_snapshot(evidence.snapshot)
    if station_count == 21:
        assert len(snapshot["source_records"]) == 21
        assert (
            sum(
                len(records["future_weather"])
                for _, records in snapshot["source_records"]
            )
            == 21 * 128
        )
    source_records = (
        snapshot["source_records"][0][1] if group_run else snapshot["source_records"]
    )
    assert source_records["historical_forcing"] == []
    assert source_records["tail_weather"] == []
    expected_future_count = (
        consumed_future_count if weather_legs in ("both", "future") else 0
    )
    assert len(source_records["future_weather"]) == expected_future_count
    assert {row["member_id"] for row in source_records["future_weather"]} == (
        {0} if expected_future_count else set()
    )
    for leg, consumed in (
        ("past", weather_legs in ("both", "past")),
        ("future", weather_legs in ("both", "future")),
    ):
        fact = source_records["weather_use"][leg]
        expected_freshness = (
            "stale"
            if refresh_case == f"{leg}_unavailable"
            or (leg == "future" and refresh_case == "retained_subset")
            else "current"
        )
        assert fact["freshness"] == expected_freshness if consumed else fact == {}
        category = (
            InputQualityCategory.FORCING if leg == "past" else InputQualityCategory.NWP
        )
        for forecast in forecasts:
            stale_flags = [
                flag
                for flag in forecast.input_quality_flags
                if flag.category is category and "weather STALE" in flag.detail
            ]
            assert bool(stale_flags) == (consumed and expected_freshness == "stale")
            assert all(flag.level is InputQualityLevel.DEGRADED for flag in stale_flags)
            if stale_flags:
                assert forecast.input_quality is InputQualityLevel.DEGRADED

    return forecasts


@pytest.mark.parametrize("weather_legs", ["past", "future", "unused"])
def test_selected_native_unused_legs_do_not_fetch(
    monkeypatch: pytest.MonkeyPatch,
    weather_legs: Literal["past", "future", "unused"],
) -> None:
    _run_selected_cycle(
        monkeypatch, group_run=True, capture_fault="none", weather_legs=weather_legs
    )


@pytest.mark.parametrize(
    "refresh_case",
    [
        "past_unavailable",
        "future_unavailable",
        "past_fatal",
        "future_fatal",
        "expired",
        "expired_success",
        "retained_subset",
        "retained_corrupt",
        "rollover",
        "mixed_cycles",
    ],
)
def test_selected_native_refresh_lifecycle_preserves_fail_closed_cache_policy(
    monkeypatch: pytest.MonkeyPatch,
    refresh_case: Literal[
        "past_unavailable",
        "future_unavailable",
        "past_fatal",
        "future_fatal",
        "expired",
        "expired_success",
        "retained_subset",
        "retained_corrupt",
        "rollover",
        "mixed_cycles",
    ],
) -> None:
    _run_selected_cycle(
        monkeypatch,
        group_run=True,
        capture_fault="none",
        refresh_case=refresh_case,
        station_count=2 if refresh_case == "mixed_cycles" else 1,
    )


@pytest.mark.parametrize("refresh_case", ["past_unavailable", "future_unavailable"])
def test_station_stale_leg_projects_persisted_quality(
    monkeypatch: pytest.MonkeyPatch,
    refresh_case: Literal["past_unavailable", "future_unavailable"],
) -> None:
    _run_selected_cycle(
        monkeypatch, group_run=False, capture_fault="none", refresh_case=refresh_case
    )


class TestSelectedWeatherIssuance:
    @pytest.mark.parametrize("group_run", [False, True])
    @pytest.mark.parametrize("stage", ["deserialize", "predict"])
    @pytest.mark.parametrize("reason", ["weather_expired", "issue_day_mismatch"])
    def test_boundary_crossing_refuses_actual_flow_write(
        self,
        monkeypatch: pytest.MonkeyPatch,
        group_run: bool,
        stage: Literal["deserialize", "predict"],
        reason: Literal["weather_expired", "issue_day_mismatch"],
    ) -> None:
        from zoneinfo import ZoneInfo

        now = ISSUE
        calls = 0
        model_type = FakeGroupForecastModel if group_run else FakeStationForecastModel
        predict_name = "predict_batch" if group_run else "predict"
        deserialize = model_type.deserialize_artifact
        predict = getattr(model_type, predict_name)
        crossing = ensure_utc(
            ISSUE
            + (
                timedelta(hours=36, microseconds=1)
                if reason == "weather_expired"
                else timedelta(hours=18, minutes=15)
            )
        )

        def deserialize_then_advance(self: object, raw: bytes) -> object:
            nonlocal now
            result = deserialize(self, raw)
            if stage == "deserialize":
                now = crossing
            return result

        def predict_then_advance(
            self: object, *args: object, **kwargs: object
        ) -> object:
            nonlocal now, calls
            calls += 1
            result = predict(self, *args, **kwargs)
            if stage == "predict":
                now = crossing
            return result

        monkeypatch.setattr(
            model_type, "deserialize_artifact", deserialize_then_advance
        )
        monkeypatch.setattr(model_type, predict_name, predict_then_advance)

        def configure(
            stores: dict[str, object],
            groups: object,
            config: DeploymentConfig,
            mid: ModelId,
        ) -> dict[str, object]:
            return {
                "config": config.model_copy(
                    update={
                        "nwp_max_fallback_age_hours": 36.0,
                        "operational_weather": config.operational_weather.model_copy(
                            update={
                                "automatic_issue_timezone": ZoneInfo("Asia/Kathmandu")
                                if reason == "issue_day_mismatch"
                                else None,
                            }
                        ),
                    }
                ),
                "cycle_time": None,
                "clock": lambda: now,
            }

        def verify(result: ForecastCycleResult, stores: dict[str, object]) -> None:
            assert result.forecasts_stored == 0
            assert stores["forecast_store"].fetch_forecasts_for_cycle(ISSUE) == []
            assert any(reason in error for error in result.errors), result.errors
            assert calls == (0 if stage == "deserialize" else 1)

        _run_selected_cycle(
            monkeypatch,
            group_run=group_run,
            capture_fault="none",
            station_count=3 if group_run else 1,
            configure_cycle=configure,
            verify_cycle=verify,
        )

    @pytest.mark.parametrize("group_run", [False, True])
    def test_automatic_tick_uses_midnight_and_same_actual_created_at(
        self,
        monkeypatch: pytest.MonkeyPatch,
        group_run: bool,
    ) -> None:
        from zoneinfo import ZoneInfo

        at = ensure_utc(ISSUE + timedelta(minutes=15))

        def configure(
            stores: dict[str, object],
            groups: object,
            config: DeploymentConfig,
            mid: ModelId,
        ) -> dict[str, object]:
            return {
                "config": config.model_copy(
                    update={
                        "operational_weather": config.operational_weather.model_copy(
                            update={
                                "automatic_issue_timezone": ZoneInfo("Asia/Kathmandu"),
                            }
                        ),
                    }
                ),
                "cycle_time": None,
                "clock": lambda: at,
            }

        def verify(result: ForecastCycleResult, stores: dict[str, object]) -> None:
            forecasts = stores["forecast_store"].fetch_forecasts_for_cycle(ISSUE)
            assert result.forecasts_stored == 1, result.errors
            assert len(forecasts) == 1
            assert forecasts[0].ensemble.issued_at == ISSUE
            assert forecasts[0].created_at == at
            assert forecasts[0].nwp_cycle_reference_time == ISSUE
            assert forecasts[0].weather_evidence_version == 1

        _run_selected_cycle(
            monkeypatch,
            group_run=group_run,
            capture_fault="none",
            configure_cycle=configure,
            verify_cycle=verify,
        )

    def test_group_preserves_success_and_qc_rejections_across_creation_expiry(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from sapphire_flow.services.forecast_qc import ForecastOutputQualityChecker
        from sapphire_flow.types.domain import ForecastQcRuleParams
        from tests.unit.flows.test_run_forecast_cycle_rejected_capture import _StubStore

        now = ISSUE
        boundary = ensure_utc(ISSUE + timedelta(hours=36))
        expired = ensure_utc(boundary + timedelta(microseconds=1))
        original_predict = FakeGroupForecastModel.predict_batch
        original_check = ForecastOutputQualityChecker.check
        rejected = _StubStore()

        def predict(
            self: FakeGroupForecastModel, *args: object, **kwargs: object
        ) -> object:
            nonlocal now
            batch = original_predict(self, *args, **kwargs)
            for sid in (StationId(UUID(int=2)), StationId(UUID(int=4))):
                ensembles, state = batch[sid]
                ensemble = ensembles["discharge"]
                batch[sid] = (
                    {
                        "discharge": replace(
                            ensemble,
                            values=ensemble.values.with_columns(
                                pl.lit(-1.0).alias("value")
                            ),
                        )
                    },
                    state,
                )
            now = boundary
            return batch

        def check(
            self: ForecastOutputQualityChecker,
            ensemble: object,
            *args: object,
            **kwargs: object,
        ) -> object:
            nonlocal now
            flags = original_check(self, ensemble, *args, **kwargs)
            if ensemble.station_id == StationId(UUID(int=2)):
                now = expired
            return flags

        monkeypatch.setattr(FakeGroupForecastModel, "predict_batch", predict)
        monkeypatch.setattr(ForecastOutputQualityChecker, "check", check)

        def configure(
            stores: dict[str, object],
            groups: object,
            config: DeploymentConfig,
            mid: ModelId,
        ) -> dict[str, object]:
            return {
                "config": config.model_copy(
                    update={"nwp_max_fallback_age_hours": 36.0}
                ),
                "clock": lambda: now,
                "rejected_forecast_store": rejected,
                "qc_rules": ForecastQcRuleSet(
                    version="negative",
                    rules=(
                        ForecastQcRuleParams(
                            rule_id="negative_value",
                            rule_version="1",
                            parameter="discharge",
                            time_step=timedelta(days=1),
                            thresholds={"value_min": 0.0},
                        ),
                    ),
                ),
            }

        def verify(result: ForecastCycleResult, stores: dict[str, object]) -> None:
            forecasts = stores["forecast_store"].fetch_forecasts_for_cycle(ISSUE)
            assert result.forecasts_stored == 1, result.errors
            assert [
                (f.station_id, f.created_at, f.ensemble.issued_at) for f in forecasts
            ] == [(StationId(UUID(int=1)), boundary, ISSUE)]
            assert forecasts[0].updated_at == boundary
            assert forecasts[0].nwp_cycle_reference_time == ISSUE
            assert any(
                str(UUID(int=3)) in error and "weather_expired" in error
                for error in result.errors
            )
            assert rejected.committed
            entries = [entry for call in rejected.calls for entry in call]
            assert {entry.payload.station_id for entry in entries} == {
                StationId(UUID(int=2)),
                StationId(UUID(int=4)),
            }
            assert all(
                parameter.qc_status is QcStatus.QC_FAILED
                for entry in entries
                for parameter in entry.payload.parameters
            )

        _run_selected_cycle(
            monkeypatch,
            group_run=True,
            capture_fault="none",
            station_count=4,
            configure_cycle=configure,
            verify_cycle=verify,
        )

    def test_nepal_day_mismatch_refuses_before_provider_fetch(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from zoneinfo import ZoneInfo

        from sapphire_flow.exceptions import ConfigurationError

        def forbidden(*args: object, **kwargs: object) -> None:
            pytest.fail("day mismatch reached provider fetch")

        monkeypatch.setattr(FixtureOperationalApi, "operational", forbidden)
        monkeypatch.setattr(_FakeCandidateAwareSource, "fetch_requirement", forbidden)

        def configure(
            stores: dict[str, object],
            groups: object,
            config: DeploymentConfig,
            mid: ModelId,
        ) -> dict[str, object]:
            return {
                "config": config.model_copy(
                    update={
                        "operational_weather": config.operational_weather.model_copy(
                            update={
                                "automatic_issue_timezone": ZoneInfo("Asia/Kathmandu"),
                            }
                        ),
                    }
                ),
                "cycle_time": None,
                "clock": lambda: ensure_utc(ISSUE + timedelta(hours=18, minutes=15)),
            }

        with pytest.raises(ConfigurationError, match="issue day differs"):
            _run_selected_cycle(
                monkeypatch,
                group_run=True,
                capture_fault="none",
                configure_cycle=configure,
            )

    def test_explicit_manual_issue_bypasses_only_calendar(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from zoneinfo import ZoneInfo

        at = ensure_utc(ISSUE + timedelta(days=1))

        def configure(
            stores: dict[str, object],
            groups: object,
            config: DeploymentConfig,
            mid: ModelId,
        ) -> dict[str, object]:
            return {
                "config": config.model_copy(
                    update={
                        "nwp_max_fallback_age_hours": 36.0,
                        "operational_weather": config.operational_weather.model_copy(
                            update={
                                "automatic_issue_timezone": ZoneInfo("Asia/Kathmandu"),
                            }
                        ),
                    }
                ),
                "forcing_resolution_policy": ForcingResolutionPolicy(
                    cycle_cadence_hours=6, max_cycle_age_hours=36, max_retries=0
                ),
                "cycle_time": ISSUE.isoformat(),
                "clock": lambda: at,
            }

        def verify(result: ForecastCycleResult, stores: dict[str, object]) -> None:
            forecasts = stores["forecast_store"].fetch_forecasts_for_cycle(ISSUE)
            assert result.forecasts_stored == 1, result.errors
            assert len(forecasts) == 1
            assert forecasts[0].ensemble.issued_at == ISSUE
            assert forecasts[0].created_at == at
            assert forecasts[0].nwp_cycle_reference_time == ISSUE

        _run_selected_cycle(
            monkeypatch,
            group_run=True,
            capture_fault="none",
            configure_cycle=configure,
            verify_cycle=verify,
        )

    @pytest.mark.parametrize("purpose", ["standard", "provisional", "replay"])
    def test_entry_refusal_uses_existing_fatal_freshness_boundary(
        self,
        monkeypatch: pytest.MonkeyPatch,
        purpose: Literal["standard", "provisional", "replay"],
    ) -> None:
        from zoneinfo import ZoneInfo

        from sapphire_flow.exceptions import ConfigurationError
        from sapphire_flow.flows import run_forecast_cycle as flow
        from sapphire_flow.types.enums import PipelineCheckType, PipelineHealthStatus
        from tests.fakes.fake_stores import FakePipelineHealthStore

        health = FakePipelineHealthStore()
        failures: list[BaseException] = []
        original = flow.emit_freshness_on_fatal_exit
        at = ensure_utc(ISSUE + timedelta(hours=18, minutes=15))
        monkeypatch.delenv("SAPPHIRE_CONFIG", raising=False)

        def emit(*args: object, **kwargs: object) -> object:
            exc = kwargs["exc"]
            assert isinstance(exc, BaseException)
            failures.append(exc)
            return original(*args, **kwargs)

        def forbidden(*args: object, **kwargs: object) -> None:
            pytest.fail("entry failure reached provider")

        monkeypatch.setattr(flow, "emit_freshness_on_fatal_exit", emit)
        monkeypatch.setattr(FixtureOperationalApi, "operational", forbidden)
        monkeypatch.setattr(_FakeCandidateAwareSource, "fetch_requirement", forbidden)

        def configure(
            stores: dict[str, object],
            groups: object,
            config: DeploymentConfig,
            mid: ModelId,
        ) -> dict[str, object]:
            options = {
                "config": config.model_copy(
                    update={
                        "operational_weather": config.operational_weather.model_copy(
                            update={
                                "automatic_issue_timezone": ZoneInfo("Asia/Kathmandu")
                            }
                        )
                    }
                ),
                "cycle_time": ISSUE.isoformat() if purpose == "replay" else None,
                "clock": lambda: at,
                "pipeline_health_store": health,
            }
            if purpose == "replay":
                options["operational_weather_adapter"] = None
            return options

        with pytest.raises(
            ConfigurationError,
            match="provider configuration"
            if purpose == "replay"
            else "issue day differs",
        ) as raised:
            _run_selected_cycle(
                monkeypatch,
                group_run=True,
                capture_fault="none",
                configure_cycle=configure,
                data_use=ForecastDataUse.PROVISIONAL_RATING
                if purpose == "provisional"
                else ForecastDataUse.STANDARD,
            )
        assert failures == [raised.value]
        assert failures[0] is raised.value
        assert raised.value.__cause__ is None
        records = health.fetch_recent(check_type=PipelineCheckType.FORECAST_FRESHNESS)
        if purpose == "standard":
            assert len(records) == 1
            assert records[0].status is PipelineHealthStatus.CRITICAL
            assert records[0].detail["forecasts_stored"] == 0
            assert records[0].checked_at == at
        else:
            assert records == []

    @pytest.mark.parametrize("group_run", [False, True])
    def test_ticking_clock_stamps_all_parameters_with_checked_time(
        self,
        monkeypatch: pytest.MonkeyPatch,
        group_run: bool,
    ) -> None:
        import importlib
        from zoneinfo import ZoneInfo

        from sapphire_flow.services.selected_weather_evidence import (
            selected_weather_issuance_refusal,
        )

        checked: list[UtcDatetime] = []
        ticks = 0
        model_type = FakeGroupForecastModel if group_run else FakeStationForecastModel
        method = "predict_batch" if group_run else "predict"
        original_predict = getattr(model_type, method)
        monkeypatch.setattr(
            model_type,
            "data_requirements",
            replace(
                model_type.data_requirements,
                target_parameters=frozenset({"discharge", "water_level"}),
                required_past_targets=frozenset({"discharge"}),
            ),
        )

        def two_parameters(
            ensembles: dict[str, ForecastEnsemble], state: bytes | None
        ) -> tuple[dict[str, ForecastEnsemble], bytes | None]:
            discharge = ensembles["discharge"]
            return {
                **ensembles,
                "water_level": replace(discharge, parameter="water_level", units="m"),
            }, state

        def predict(self: object, *args: object, **kwargs: object) -> object:
            result = original_predict(self, *args, **kwargs)
            return (
                {sid: two_parameters(*value) for sid, value in result.items()}
                if group_run
                else two_parameters(*result)
            )

        def guard(
            source: StationSourceEvidence | None,
            *,
            issue_time: UtcDatetime,
            at: UtcDatetime,
            max_age_hours: float,
            automatic_issue_timezone: ZoneInfo | None = None,
        ) -> SelectedWeatherIssuanceRefusal | None:
            checked.append(at)
            return selected_weather_issuance_refusal(
                source,
                issue_time=issue_time,
                at=at,
                max_age_hours=max_age_hours,
                automatic_issue_timezone=automatic_issue_timezone,
            )

        def clock() -> UtcDatetime:
            nonlocal ticks
            ticks += 1
            return ensure_utc(ISSUE + timedelta(minutes=15, microseconds=ticks))

        runner = importlib.import_module(
            "sapphire_flow.services.run_group_forecast"
            if group_run
            else "sapphire_flow.services.run_station_forecast"
        )
        monkeypatch.setattr(model_type, method, predict)
        monkeypatch.setattr(runner, "selected_weather_issuance_refusal", guard)

        def configure(
            stores: dict[str, object],
            groups: object,
            config: DeploymentConfig,
            mid: ModelId,
        ) -> dict[str, object]:
            return {
                "config": config.model_copy(
                    update={
                        "operational_weather": config.operational_weather.model_copy(
                            update={
                                "automatic_issue_timezone": ZoneInfo("Asia/Kathmandu")
                            }
                        )
                    }
                ),
                "cycle_time": None,
                "clock": clock,
            }

        def verify(result: ForecastCycleResult, stores: dict[str, object]) -> None:
            forecasts = stores["forecast_store"].fetch_forecasts_for_cycle(ISSUE)
            assert result.forecasts_stored == 2, result.errors
            assert {f.ensemble.parameter for f in forecasts} == {
                "discharge",
                "water_level",
            }
            assert len(checked) == 2 and checked[0] < checked[1]
            assert {f.created_at for f in forecasts} == {checked[-1]}
            assert {f.updated_at for f in forecasts} == {checked[-1]}
            assert all(f.issued_at == ISSUE for f in forecasts)

        _run_selected_cycle(
            monkeypatch,
            group_run=group_run,
            capture_fault="none",
            configure_cycle=configure,
            verify_cycle=verify,
        )
