from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import UUID

import pandas as pd
import pytest

from sapphire_flow.adapters.recap_gateway import (
    EcmwfApiLike,
    GatewayHruName,
    GatewayPolygonName,
    GatewayPolygonRef,
    RecapConfigurationError,
    RecapPayloadIntegrityError,
)
from sapphire_flow.adapters.recap_operational import (
    RecapGatewayOperationalAdapter,
    parse_operational_samples,
)
from sapphire_flow.config.operational_weather import OperationalWeatherConfig
from sapphire_flow.preprocessing.recap_operational_weather import reduce_daily_window
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.operational_weather import WeatherProductIdentity
from sapphire_flow.types.station import StationWeatherSource

if TYPE_CHECKING:
    from recap_client.http import ParquetHttpClient


FIXTURES = Path(__file__).parents[2] / "fixtures" / "recap" / "operational"
START = ensure_utc(datetime(2026, 10, 7, tzinfo=UTC))
ISSUE = ensure_utc(datetime(2026, 11, 6, tzinfo=UTC))
END = ensure_utc(ISSUE + timedelta(days=10))
LIMIT = 64 * 1024 * 1024


@pytest.fixture
def precipitation() -> pd.DataFrame:
    return pd.read_parquet(FIXTURES / "precipitation.parquet")


class TestQualifiedOperationalReduction:
    def test_missing_numeric_value_does_not_bypass_source_grid_validation(
        self, precipitation: pd.DataFrame
    ) -> None:
        row = precipitation.iloc[:1].copy()
        row.index = row.index + pd.Timedelta(minutes=30)
        row.loc[:, "g_225"] = float("nan")
        with pytest.raises(RecapPayloadIntegrityError, match="numeric/day"):
            parse_operational_samples(
                row,
                polygon="g_225",
                parameter="precipitation",
                start=START,
                end=END,
                max_response_bytes=LIMIT,
            )

    def test_actual_memberless_payload_all_21_stations_40_days(
        self, precipitation: pd.DataFrame
    ) -> None:
        temperature = pd.read_parquet(FIXTURES / "temperature.parquet")
        polygons = [
            str(column)
            for column in precipitation.columns
            if str(column).startswith("g_")
        ]
        assert len(polygons) == 21
        for number, polygon in enumerate(polygons, start=1):
            p_samples = parse_operational_samples(
                precipitation,
                polygon=polygon,
                parameter="precipitation",
                start=START,
                end=END,
                max_response_bytes=LIMIT,
            )
            t_samples = parse_operational_samples(
                temperature,
                polygon=polygon,
                parameter="temperature",
                start=START,
                end=END,
                max_response_bytes=LIMIT,
            )
            window = reduce_daily_window(
                station_id=StationId(UUID(int=number)),
                binding_fingerprint="a" * 64,
                issue_time=ISSUE,
                start=START,
                end=END,
                samples=p_samples + t_samples,
            )
            assert len(window.days) == 40
            assert len(p_samples) == 688
            assert all(day.precipitation_mm >= 0 for day in window.days)
            assert p_samples[-1].interval_end == END
            assert p_samples[-1].product is WeatherProductIdentity.CONTROL

    def test_conflicting_optional_member_rejected(
        self, precipitation: pd.DataFrame
    ) -> None:
        precipitation.loc[precipitation.index[0], "source_meta"] = '{"member":"1"}'
        with pytest.raises(RecapPayloadIntegrityError, match="invalid operational"):
            parse_operational_samples(
                precipitation,
                polygon="g_225",
                parameter="precipitation",
                start=START,
                end=END,
                max_response_bytes=LIMIT,
            )

    def test_oversize_response_rejected(self, precipitation: pd.DataFrame) -> None:
        with pytest.raises(RecapPayloadIntegrityError, match="byte limit"):
            parse_operational_samples(
                precipitation,
                polygon="g_225",
                parameter="precipitation",
                start=START,
                end=END,
                max_response_bytes=1,
            )

    def test_missing_required_row_is_not_filled(
        self, precipitation: pd.DataFrame
    ) -> None:
        temperature = pd.read_parquet(FIXTURES / "temperature.parquet")
        rows = parse_operational_samples(
            precipitation.iloc[1:],
            polygon="g_225",
            parameter="precipitation",
            start=START,
            end=END,
            max_response_bytes=LIMIT,
        ) + parse_operational_samples(
            temperature,
            polygon="g_225",
            parameter="temperature",
            start=START,
            end=END,
            max_response_bytes=LIMIT,
        )
        with pytest.raises(ValueError, match="gap"):
            reduce_daily_window(
                station_id=StationId(UUID(int=1)),
                binding_fingerprint="a" * 64,
                issue_time=ISSUE,
                start=START,
                end=END,
                samples=rows,
            )


class RecordingApi:
    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames
        self.calls: list[dict[str, object]] = []

    def operational(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self.frames[str(kwargs["ifs_variable_name"])]


class PolygonResolver:
    def resolve(self, source: StationWeatherSource) -> GatewayPolygonRef:
        return GatewayPolygonRef(
            hru_name=GatewayHruName("nepal21_20261012"),
            polygon_name=GatewayPolygonName("g_225"),
            station_id=source.station_id,
            spatial_type=SpatialRepresentation.BASIN_AVERAGE,
            band_id=None,
        )


def make_binding() -> StationWeatherSource:
    return StationWeatherSource(
        station_id=StationId(UUID(int=1)),
        nwp_source="recap_ecmwf_operational",
        role=WeatherSourceRole.OPERATIONAL,
        status=WeatherSourceStatus.ACTIVE,
        extraction_type=SpatialRepresentation.BASIN_AVERAGE,
    )


class TestRecapGatewayOperationalAdapter:
    def test_retains_complete_prefix_when_later_tail_day_is_incomplete(self) -> None:
        temperature = pd.read_parquet(FIXTURES / "temperature.parquet")
        temperature.loc[ISSUE + timedelta(days=2, hours=6), "g_225"] = float("nan")
        adapter = RecapGatewayOperationalAdapter(
            api=RecordingApi(
                {
                    "tp": pd.read_parquet(FIXTURES / "precipitation.parquet"),
                    "2t": temperature,
                }
            ),
            resolver=PolygonResolver(),
            config=OperationalWeatherConfig(),
        )
        source = make_binding()
        window = adapter.fetch_windows(
            sources=[source], issue_time=ISSUE, start=START, end=ISSUE
        )[source.station_id]
        assert window.end == ISSUE + timedelta(days=2)
        assert all(
            day.precipitation_mm is not None and day.temperature_c is not None
            for day in window.days
        )

    @pytest.mark.parametrize("metadata", ["{}", '{"grid_resolution":0.25}'])
    def test_unknown_or_contradictory_tail_grid_is_not_a_recoverable_gap(
        self, metadata: str
    ) -> None:
        temperature = pd.read_parquet(FIXTURES / "temperature.parquet")
        temperature.loc[ISSUE, "source_meta"] = metadata
        adapter = RecapGatewayOperationalAdapter(
            api=RecordingApi(
                {
                    "tp": pd.read_parquet(FIXTURES / "precipitation.parquet"),
                    "2t": temperature,
                }
            ),
            resolver=PolygonResolver(),
            config=OperationalWeatherConfig(),
        )
        with pytest.raises(
            RecapPayloadIntegrityError, match="invalid operational weather sample"
        ):
            adapter.fetch_windows(
                sources=[make_binding()], issue_time=ISSUE, start=START, end=ISSUE
            )

    def test_short_model_window_uses_minimum_provider_history_and_buffer(self) -> None:
        api = RecordingApi(
            {
                "tp": pd.read_parquet(FIXTURES / "precipitation.parquet"),
                "2t": pd.read_parquet(FIXTURES / "temperature.parquet"),
            }
        )
        adapter = RecapGatewayOperationalAdapter(
            api=cast("EcmwfApiLike", api),
            resolver=PolygonResolver(),
            config=OperationalWeatherConfig(),
        )
        source = make_binding()
        start = ensure_utc(ISSUE - timedelta(days=2))
        result = adapter.fetch_windows(
            sources=[source], issue_time=ISSUE, start=start, end=ISSUE
        )
        assert len(api.calls) == 2
        assert all(
            call["start_date"] == (ISSUE - timedelta(days=14)).date()
            for call in api.calls
        )
        assert all(call["subdaily_resolution"] == 6 for call in api.calls)
        assert all(
            "value_agg_fn" not in call and "unit_conversion_fn" not in call
            for call in api.calls
        )
        assert result[source.station_id].start == start
        assert result[source.station_id].end == ISSUE + timedelta(days=15)

    def test_incomplete_optional_buffer_does_not_invalidate_required_past(self) -> None:
        frames = {
            "tp": pd.read_parquet(FIXTURES / "precipitation.parquet"),
            "2t": pd.read_parquet(FIXTURES / "temperature.parquet"),
        }
        api = RecordingApi(
            {key: frame.loc[frame.index < ISSUE] for key, frame in frames.items()}
        )
        adapter = RecapGatewayOperationalAdapter(
            api=cast("EcmwfApiLike", api),
            resolver=PolygonResolver(),
            config=OperationalWeatherConfig(),
        )
        source = make_binding()
        result = adapter.fetch_windows(
            sources=[source], issue_time=ISSUE, start=START, end=ISSUE
        )
        assert result[source.station_id].end == ISSUE

    def test_invalid_binding_is_rejected_before_any_call(self) -> None:
        api = RecordingApi({})
        adapter = RecapGatewayOperationalAdapter(
            api=cast("EcmwfApiLike", api),
            resolver=PolygonResolver(),
            config=OperationalWeatherConfig(),
        )
        with pytest.raises(RecapConfigurationError, match="binding"):
            adapter.fetch_windows(
                sources=[replace(make_binding(), role=WeatherSourceRole.REANALYSIS)],
                issue_time=ISSUE,
                start=START,
                end=ISSUE,
            )
        assert api.calls == []


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get_parquet_df(self, path: str, params: dict[str, object]) -> pd.DataFrame:
        self.calls.append((path, params))
        return pd.DataFrame()


class TestPinnedSdkRequestContract:
    def test_independent_future_endpoint_preserves_run_and_untransformed_values(
        self,
    ) -> None:
        from recap_client.ecmwf import EcmwfApi

        transport = RecordingTransport()
        api = EcmwfApi(cast("ParquetHttpClient", transport))
        api.ifs_forecast(
            variable="tp",
            run_date=ISSUE,
            run_hour=6,
            hru_code="nepal21_20261012",
            ifs_type="fc",
            member="fc",
            include_provenance=True,
            horizon_days=15,
        )
        assert transport.calls == [
            (
                "exports/ifs/forecast",
                {
                    "variable": "tp",
                    "run_date": "2026-11-06",
                    "run_hour": 6,
                    "hru_code": "nepal21_20261012",
                    "ifs_type": "fc",
                    "member": "fc",
                    "level_type": "sfc",
                    "horizon_days": 15,
                    "include_provenance": True,
                },
            )
        ]

    def test_combined_endpoint_uses_fill_cycles_without_member_or_transforms(
        self,
    ) -> None:
        from recap_client.ecmwf import EcmwfApi

        transport = RecordingTransport()
        api = EcmwfApi(cast("ParquetHttpClient", transport))
        api.operational(
            hru_code="nepal21_20261012",
            start_date=START,
            era5_variable_name="total_precipitation",
            ifs_variable_name="tp",
            subdaily_resolution=6,
            include_provenance=True,
        )
        assert transport.calls == [
            (
                "exports/ecmwf/operational",
                {
                    "hru_code": "nepal21_20261012",
                    "start_date": "2026-10-07",
                    "era5_variable_name": "total_precipitation",
                    "ifs_variable_name": "tp",
                    "subdaily_resolution": 6,
                    "era5_dataset_name": "reanalysis-era5-land",
                    "include_provenance": True,
                },
            )
        ]


class TestOptionalBufferIntegrity:
    @pytest.mark.parametrize("value", [float("inf"), -1.0])
    @pytest.mark.parametrize("scope", ["required", "optional"])
    def test_numeric_failure_is_confined_to_optional_scope(
        self,
        value: float,
        scope: str,
    ) -> None:
        frames = {
            "tp": pd.read_parquet(FIXTURES / "precipitation.parquet"),
            "2t": pd.read_parquet(FIXTURES / "temperature.parquet"),
        }
        timestamp = ISSUE if scope == "optional" else START
        frames["tp"].loc[timestamp, "g_225"] = value
        adapter = RecapGatewayOperationalAdapter(
            api=cast("EcmwfApiLike", RecordingApi(frames)),
            resolver=PolygonResolver(),
            config=OperationalWeatherConfig(),
        )
        if scope == "required":
            with pytest.raises(RecapPayloadIntegrityError, match="numeric/day"):
                adapter.fetch_windows(
                    sources=[make_binding()], issue_time=ISSUE, start=START, end=ISSUE
                )
        else:
            result = adapter.fetch_windows(
                sources=[make_binding()], issue_time=ISSUE, start=START, end=ISSUE
            )
            assert result[make_binding().station_id].end == ISSUE

    @pytest.mark.parametrize("scope", ["required", "optional"])
    def test_missing_sample_preserves_partial_support_as_a_gap(
        self, scope: str
    ) -> None:
        frames = {
            "tp": pd.read_parquet(FIXTURES / "precipitation.parquet"),
            "2t": pd.read_parquet(FIXTURES / "temperature.parquet"),
        }
        timestamp = ISSUE if scope == "optional" else START
        frames["tp"].loc[timestamp, "g_225"] = float("nan")
        adapter = RecapGatewayOperationalAdapter(
            api=cast("EcmwfApiLike", RecordingApi(frames)),
            resolver=PolygonResolver(),
            config=OperationalWeatherConfig(),
        )
        result = adapter.fetch_windows(
            sources=[make_binding()], issue_time=ISSUE, start=START, end=ISSUE
        )
        if scope == "optional":
            assert result[make_binding().station_id].end == ISSUE
            return
        missing = next(
            day
            for day in result[make_binding().station_id].days
            if day.day == timestamp
        )
        assert missing.precipitation_mm is None
        assert missing.temperature_c is not None
        assert missing.samples

    def test_invalid_optional_value_does_not_hide_other_leg_identity(self) -> None:
        frames = {
            "tp": pd.read_parquet(FIXTURES / "precipitation.parquet"),
            "2t": pd.read_parquet(FIXTURES / "temperature.parquet"),
        }
        frames["tp"].loc[ISSUE, "g_225"] = float("nan")
        frames["2t"].loc[ISSUE, "source_meta"] = '{"member":"1"}'
        adapter = RecapGatewayOperationalAdapter(
            api=cast("EcmwfApiLike", RecordingApi(frames)),
            resolver=PolygonResolver(),
            config=OperationalWeatherConfig(),
        )
        with pytest.raises(RecapPayloadIntegrityError, match="invalid operational"):
            adapter.fetch_windows(
                sources=[make_binding()], issue_time=ISSUE, start=START, end=ISSUE
            )

    def test_invalid_optional_time_grid_omits_only_buffer(self) -> None:
        frames = {
            "tp": pd.read_parquet(FIXTURES / "precipitation.parquet"),
            "2t": pd.read_parquet(FIXTURES / "temperature.parquet"),
        }
        frames["tp"] = frames["tp"].rename(index={ISSUE: ISSUE + timedelta(minutes=30)})
        adapter = RecapGatewayOperationalAdapter(
            api=cast("EcmwfApiLike", RecordingApi(frames)),
            resolver=PolygonResolver(),
            config=OperationalWeatherConfig(),
        )
        result = adapter.fetch_windows(
            sources=[make_binding()], issue_time=ISSUE, start=START, end=ISSUE
        )
        assert result[make_binding().station_id].end == ISSUE


class TestNetRadiation:
    def test_four_channel_request_converts_once_and_preserves_legacy_default(
        self,
    ) -> None:
        from sapphire_flow.types.operational_weather import (
            WEATHER_PARAMETERS,
            period_weather_value,
        )
        from sapphire_flow.types.recap_intervals import (
            GatewayIntervalContract,
            GatewayIntervalQualification,
        )

        precipitation = pd.read_parquet(FIXTURES / "precipitation.parquet")
        radiation = precipitation.copy()
        # Valid normalized fixture: per-interval energy for 100 W/m².
        for stamp, row in radiation.iterrows():
            run = pd.Timestamp(row["source_run"])
            run = run.tz_localize("UTC") if run.tzinfo is None else run
            lead = (stamp - run).total_seconds() / 3600
            hours = 1 if row["source"] == "era5_land" else 3 if lead < 144 else 6
            radiation.loc[stamp, "g_225"] = 100 * hours * 3600
        api = RecordingApi(
            {
                "tp": precipitation,
                "2t": pd.read_parquet(FIXTURES / "temperature.parquet"),
                "ssr": radiation,
                "str": radiation,
            }
        )
        qualification = GatewayIntervalQualification(
            contract=GatewayIntervalContract.NORMALIZED_START_V1,
            revision="test-normalized-v1",
        )
        adapter = RecapGatewayOperationalAdapter(
            api=api,
            resolver=PolygonResolver(),
            config=OperationalWeatherConfig(),
            interval_qualification=qualification,
        )
        source = make_binding()
        legacy = adapter.fetch_windows(
            sources=[source], issue_time=ISSUE, start=START, end=ISSUE
        )[source.station_id]
        assert {call["ifs_variable_name"] for call in api.calls} == {"tp", "2t"}
        api.calls.clear()
        window = adapter.fetch_windows(
            sources=[source],
            issue_time=ISSUE,
            start=START,
            end=ISSUE,
            parameters_by_station={source.station_id: WEATHER_PARAMETERS},
        )[source.station_id]
        assert {call["ifs_variable_name"] for call in api.calls} == {
            "tp",
            "2t",
            "ssr",
            "str",
        }
        assert window.binding_fingerprint != legacy.binding_fingerprint
        assert all(
            period_weather_value(day, "solar_net_radiation") == 100
            for day in window.days
        )
        assert [day.temperature_c for day in window.days] == [
            day.temperature_c for day in legacy.days
        ]

    def test_unqualified_radiation_refuses_before_provider_access(self) -> None:
        from sapphire_flow.types.operational_weather import WEATHER_PARAMETERS

        api = RecordingApi({})
        source = make_binding()
        adapter = RecapGatewayOperationalAdapter(
            api=api, resolver=PolygonResolver(), config=OperationalWeatherConfig()
        )
        with pytest.raises(ValueError, match="requires qualified"):
            adapter.fetch_windows(
                sources=[source],
                issue_time=ISSUE,
                start=START,
                end=ISSUE,
                parameters_by_station={source.station_id: WEATHER_PARAMETERS},
            )
        assert api.calls == []

    def test_absent_radiation_keeps_native_legacy_support(self) -> None:
        from sapphire_flow.adapters.recap_gateway import RecapDataUnavailableError
        from sapphire_flow.types.operational_weather import (
            WEATHER_PARAMETERS,
            period_weather_value,
        )
        from sapphire_flow.types.recap_intervals import (
            GatewayIntervalContract,
            GatewayIntervalQualification,
        )

        class MissingRadiationApi(RecordingApi):
            def operational(self, **kwargs: object) -> object:
                if kwargs["ifs_variable_name"] in ("ssr", "str"):
                    raise RecapDataUnavailableError(
                        "absent", code="source_data_missing"
                    )
                return super().operational(**kwargs)

        api = MissingRadiationApi(
            {
                "tp": pd.read_parquet(FIXTURES / "precipitation.parquet"),
                "2t": pd.read_parquet(FIXTURES / "temperature.parquet"),
            }
        )
        source = make_binding()
        adapter = RecapGatewayOperationalAdapter(
            api=api,
            resolver=PolygonResolver(),
            config=OperationalWeatherConfig(),
            interval_qualification=GatewayIntervalQualification(
                contract=GatewayIntervalContract.NORMALIZED_START_V1,
                revision="test-normalized-v1",
            ),
        )
        window = adapter.fetch_windows(
            sources=[source],
            issue_time=ISSUE,
            start=START,
            end=ISSUE,
            parameters_by_station={source.station_id: WEATHER_PARAMETERS},
        )[source.station_id]
        assert window.end == ISSUE
        assert all(
            day.precipitation_mm is not None and day.temperature_c is not None
            for day in window.days
        )
        assert all(
            period_weather_value(day, "solar_net_radiation") is None
            for day in window.days
        )


class TestRadiationFingerprints:
    def test_legacy_binding_fingerprints_remain_exact(self) -> None:
        from sapphire_flow.adapters.recap_operational import (
            future_binding_fingerprint,
            future_binding_fingerprint_v2,
            operational_binding_fingerprint,
            operational_binding_fingerprint_v2,
        )
        from tests.fakes.weather_periods import GRID

        source = make_binding()
        ref = PolygonResolver().resolve(source)
        assert (
            operational_binding_fingerprint(source, ref)
            == "c664d992dd1eb5a79d02533b527e58ba11db993832791766a800c47492fd460d"
        )
        assert (
            future_binding_fingerprint(source, ref, (0,))
            == "14fc73505ab25c203717231802cd5915a9fdcabe4dd2e94d1cd28a0157ca92fb"
        )
        assert (
            operational_binding_fingerprint_v2(source, ref, GRID)
            == "cb1c0d700dc5e31db07de0738962053830fee50cb175e7385b8a526f30254b1b"
        )
        assert (
            future_binding_fingerprint_v2(source, ref, (0,), GRID)
            == "343a1c902a7613351fa7f55e64ea3f08e1323e8a330ffd3127f736b6e606353e"
        )

    def test_channel_order_is_canonical_and_revision_binds_new_cache(self) -> None:
        from dataclasses import replace

        from sapphire_flow.adapters.recap_operational import (
            operational_binding_fingerprint,
        )
        from sapphire_flow.types.operational_weather import WEATHER_PARAMETERS
        from sapphire_flow.types.recap_intervals import (
            GatewayIntervalContract,
            GatewayIntervalQualification,
        )

        source = make_binding()
        ref = PolygonResolver().resolve(source)
        qualification = GatewayIntervalQualification(
            contract=GatewayIntervalContract.NORMALIZED_START_V1,
            revision="corrected-v1",
        )
        identity = operational_binding_fingerprint(
            source,
            ref,
            parameters=WEATHER_PARAMETERS,
            interval_qualification=qualification,
        )
        assert identity == operational_binding_fingerprint(
            source,
            ref,
            parameters=tuple(reversed(WEATHER_PARAMETERS)),
            interval_qualification=qualification,
        )
        assert identity != operational_binding_fingerprint(
            source,
            ref,
            parameters=WEATHER_PARAMETERS,
            interval_qualification=replace(qualification, revision="corrected-v2"),
        )


class TestMixedRadiationRefresh:
    def test_shared_hru_batches_only_requested_channels(self) -> None:
        from dataclasses import replace

        from sapphire_flow.types.operational_weather import (
            LEGACY_WEATHER_PARAMETERS,
            WEATHER_PARAMETERS,
        )
        from sapphire_flow.types.recap_intervals import (
            GatewayIntervalContract,
            GatewayIntervalQualification,
        )

        precipitation = pd.read_parquet(FIXTURES / "precipitation.parquet")
        other = next(
            str(name)
            for name in precipitation.columns
            if str(name).startswith("g_") and name != "g_225"
        )
        radiation_source = make_binding()
        legacy_source = replace(radiation_source, station_id=StationId(UUID(int=2)))

        class Resolver(PolygonResolver):
            def resolve(self, source: StationWeatherSource) -> GatewayPolygonRef:
                ref = super().resolve(source)
                return (
                    replace(ref, polygon_name=GatewayPolygonName(other))
                    if source.station_id == legacy_source.station_id
                    else ref
                )

        api = RecordingApi(
            {
                "tp": precipitation,
                "2t": pd.read_parquet(FIXTURES / "temperature.parquet"),
                "ssr": precipitation,
                "str": precipitation,
            }
        )
        adapter = RecapGatewayOperationalAdapter(
            api=api,
            resolver=Resolver(),
            config=OperationalWeatherConfig(),
            interval_qualification=GatewayIntervalQualification(
                contract=GatewayIntervalContract.NORMALIZED_START_V1,
                revision="test-normalized-v1",
            ),
        )
        windows = adapter.fetch_windows(
            sources=[radiation_source, legacy_source],
            issue_time=ISSUE,
            start=START,
            end=ISSUE,
            parameters_by_station={
                radiation_source.station_id: WEATHER_PARAMETERS,
                legacy_source.station_id: LEGACY_WEATHER_PARAMETERS,
            },
        )
        calls = [call["ifs_variable_name"] for call in api.calls]
        assert calls.count("ssr") == calls.count("str") == 1
        assert calls.count("tp") == calls.count("2t") == 2
        assert {
            sample.parameter
            for day in windows[legacy_source.station_id].days
            for sample in day.samples
        } == set(LEGACY_WEATHER_PARAMETERS)
        assert {
            sample.parameter
            for day in windows[radiation_source.station_id].days
            for sample in day.samples
        } == set(WEATHER_PARAMETERS)


class TestNativeUtcDailyContract:
    @pytest.mark.parametrize(
        "parameter,raw_pulse,expected",
        [
            ("precipitation", 0.024, 24.0),
            ("solar_net_radiation", 86400.0, 1.0),
            ("thermal_net_radiation", -86400.0, -1.0),
            ("temperature", 297.15, 1.0),
        ],
    )
    def test_midnight_membership_matches_era5_land_daily_product(
        self, parameter: str, raw_pulse: float, expected: float
    ) -> None:
        from sapphire_flow.types.operational_weather import (
            WeatherParameter,
            daily_weather_value,
        )
        from sapphire_flow.types.recap_intervals import (
            GatewayIntervalContract,
            GatewayIntervalQualification,
        )

        day = ensure_utc(datetime(2026, 12, 31, tzinfo=UTC))
        next_day = ensure_utc(day + timedelta(days=1))
        index = pd.date_range(day, periods=48, freq="h")
        baseline = 273.15 if parameter == "temperature" else 0.0
        values = [baseline] * 48
        values[23] = raw_pulse
        values[24] = raw_pulse
        response = pd.DataFrame(
            {
                "g_225": values,
                "source": "era5_land",
                "source_run": index.floor("D"),
                "source_meta": "{}",
            },
            index=index,
        )
        samples = parse_operational_samples(
            response,
            polygon="g_225",
            parameter=cast("WeatherParameter", parameter),
            start=day,
            end=ensure_utc(day + timedelta(days=2)),
            max_response_bytes=LIMIT,
            interval_qualification=GatewayIntervalQualification(
                contract=GatewayIntervalContract.GATEWAY_NATIVE_START_V1,
                revision="gateway-docs:" + "a" * 40,
            ),
        )
        # D23 ends at D+1 midnight and belongs to D; D+1 00 belongs to D+1.
        assert samples[23].valid_time == day + timedelta(hours=23)
        assert samples[23].source_run == day
        assert samples[24].valid_time == next_day
        assert samples[24].source_run == next_day
        assert samples[23].interval_end == (
            None if parameter == "temperature" else next_day
        )
        assert daily_weather_value(
            day, samples[:24], cast("WeatherParameter", parameter)
        ) == pytest.approx(expected)
        assert daily_weather_value(
            next_day, samples[24:], cast("WeatherParameter", parameter)
        ) == pytest.approx(expected)
        assert (
            daily_weather_value(day, samples[:23], cast("WeatherParameter", parameter))
            is None
        )

    def test_native_and_legacy_radiation_acquisitions_have_distinct_keys(self) -> None:
        from sapphire_flow.adapters.recap_operational import (
            future_binding_fingerprint,
            operational_binding_fingerprint,
        )
        from sapphire_flow.types.operational_weather import WEATHER_PARAMETERS
        from sapphire_flow.types.recap_intervals import (
            GatewayIntervalContract,
            GatewayIntervalQualification,
        )

        source = make_binding()
        ref = PolygonResolver().resolve(source)
        revision = "gateway-docs:" + "a" * 40
        legacy = GatewayIntervalQualification(
            contract=GatewayIntervalContract.NORMALIZED_START_V1, revision=revision
        )
        native = GatewayIntervalQualification(
            contract=GatewayIntervalContract.GATEWAY_NATIVE_START_V1, revision=revision
        )
        assert operational_binding_fingerprint(
            source, ref, parameters=WEATHER_PARAMETERS, interval_qualification=native
        ) != operational_binding_fingerprint(
            source, ref, parameters=WEATHER_PARAMETERS, interval_qualification=legacy
        )
        assert future_binding_fingerprint(
            source,
            ref,
            (0,),
            parameters=WEATHER_PARAMETERS,
            interval_qualification=native,
        ) != future_binding_fingerprint(
            source,
            ref,
            (0,),
            parameters=WEATHER_PARAMETERS,
            interval_qualification=legacy,
        )
