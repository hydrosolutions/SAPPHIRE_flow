from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pandas as pd
import pytest
import structlog

from sapphire_flow.adapters.recap_gateway import (
    EcmwfApiLike,
    GatewayHruName,
    GatewayPolygonName,
    GatewayPolygonRef,
    GatewayPolygonResolver,
    GatewayResolutionError,
    RecapAuthError,
    RecapClientLike,
    RecapConfigurationError,
    RecapDataUnavailableError,
    RecapGatewayForecastAdapter,
    RecapGatewayReanalysisAdapter,
    RecapSnowUnavailableError,
    SnowApiLike,
    _iter_long_rows,
    _map_recap_error,
    _metres_to_mm,
    _split_provenance,
)
from sapphire_flow.exceptions import AdapterError
from sapphire_flow.protocols.adapters import (
    WeatherForecastSource,
    WeatherReanalysisSource,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.station import StationWeatherSource
from sapphire_flow.types.weather import BasinAverageForecast

_SID = StationId(UUID("00000000-0000-0000-0000-000000000001"))
_SID_A = StationId(UUID("00000000-0000-0000-0000-00000000000a"))
_SID_B = StationId(UUID("00000000-0000-0000-0000-00000000000b"))

_HRU = "hru_dhm_west_v001"
_POLY_A = "g_15013"
_POLY_B = "g_15020"
_CYCLE = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))
_START = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))
_END = ensure_utc(datetime(2026, 1, 2, tzinfo=UTC))
_SOURCE_RUN = pd.Timestamp("2026-01-01T00:00:00Z")


def _make_ref(*, band_id: int | None = None) -> GatewayPolygonRef:
    return GatewayPolygonRef(
        hru_name=GatewayHruName("hru_dhm_west_v001"),
        polygon_name=GatewayPolygonName("g_15013"),
        station_id=_SID,
        spatial_type=SpatialRepresentation.BASIN_AVERAGE,
        band_id=band_id,
    )


class _GoodResolver:
    def resolve(self, source: object) -> GatewayPolygonRef | None:
        return _make_ref()


class _NoResolveMethod:
    def other(self) -> None: ...


class _GoodEcmwf:
    def ifs_forecast(
        self,
        *,
        variable: str,
        run_date: object,
        hru_code: str,
        ifs_type: str,
        member: str | None = None,
        **kwargs: object,
    ) -> object:
        return None

    def era5_land_reanalysis(
        self,
        *,
        variable: str,
        start_date: object,
        end_date: object | None = None,
        hru_code: str,
        **kwargs: object,
    ) -> object:
        return None


class _EcmwfMissingForecast:
    def era5_land_reanalysis(
        self,
        *,
        variable: str,
        start_date: object,
        end_date: object | None = None,
        hru_code: str,
        **kwargs: object,
    ) -> object:
        return None


class _EcmwfMissingReanalysis:
    def ifs_forecast(
        self,
        *,
        variable: str,
        run_date: object,
        hru_code: str,
        ifs_type: str,
        member: str | None = None,
        **kwargs: object,
    ) -> object:
        return None


class _GoodSnow:
    def reanalysis(
        self,
        *,
        hru_code: str,
        variable: str,
        start_date: object,
        end_date: object,
        **kwargs: object,
    ) -> object:
        return None

    def forecast(
        self,
        *,
        hru_code: str,
        variable: str,
        run_date: object,
        run_hour: int = 0,
        **kwargs: object,
    ) -> object:
        return None


class _SnowMissingReanalysis:
    def other(self) -> None: ...


class _GoodClient:
    def __init__(self) -> None:
        self.ecmwf = _GoodEcmwf()
        self.snow = _GoodSnow()


class _ClientMissingEcmwf:
    def __init__(self) -> None:
        self.snow = _GoodSnow()


class _ClientMissingSnow:
    def __init__(self) -> None:
        self.ecmwf = _GoodEcmwf()


class TestGatewayPolygonTypes:
    def test_polygon_ref_five_fields(self) -> None:
        ref = _make_ref(band_id=3)
        assert ref.hru_name == "hru_dhm_west_v001"
        assert ref.polygon_name == "g_15013"
        assert ref.station_id == _SID
        assert ref.spatial_type is SpatialRepresentation.BASIN_AVERAGE
        assert ref.band_id == 3

    def test_polygon_ref_basin_average_band_id_none(self) -> None:
        assert _make_ref().band_id is None

    def test_polygon_ref_is_frozen(self) -> None:
        ref = _make_ref()
        with pytest.raises(FrozenInstanceError):
            ref.band_id = 7  # type: ignore[misc]

    def test_resolver_positive(self) -> None:
        assert isinstance(_GoodResolver(), GatewayPolygonResolver)

    def test_resolver_negative(self) -> None:
        assert not isinstance(_NoResolveMethod(), GatewayPolygonResolver)

    def test_ecmwf_positive(self) -> None:
        assert isinstance(_GoodEcmwf(), EcmwfApiLike)

    def test_ecmwf_negative_missing_forecast(self) -> None:
        assert not isinstance(_EcmwfMissingForecast(), EcmwfApiLike)

    def test_ecmwf_negative_missing_reanalysis(self) -> None:
        assert not isinstance(_EcmwfMissingReanalysis(), EcmwfApiLike)

    def test_snow_positive(self) -> None:
        assert isinstance(_GoodSnow(), SnowApiLike)

    def test_snow_negative_missing_reanalysis(self) -> None:
        assert not isinstance(_SnowMissingReanalysis(), SnowApiLike)

    def test_client_positive(self) -> None:
        assert isinstance(_GoodClient(), RecapClientLike)

    def test_client_negative_missing_ecmwf(self) -> None:
        assert not isinstance(_ClientMissingEcmwf(), RecapClientLike)

    def test_client_negative_missing_snow(self) -> None:
        assert not isinstance(_ClientMissingSnow(), RecapClientLike)

    def test_resolution_error_subclasses_adapter_error(self) -> None:
        err = GatewayResolutionError("all unmappable", station_id=_SID)
        assert isinstance(err, AdapterError)
        assert err.station_id == _SID


# --- Phase 3 fixtures -------------------------------------------------------


def _ref(
    station_id: StationId,
    polygon: str,
    *,
    hru: str = _HRU,
    band_id: int | None = None,
    spatial: SpatialRepresentation = SpatialRepresentation.BASIN_AVERAGE,
) -> GatewayPolygonRef:
    return GatewayPolygonRef(
        hru_name=GatewayHruName(hru),
        polygon_name=GatewayPolygonName(polygon),
        station_id=station_id,
        spatial_type=spatial,
        band_id=band_id,
    )


def _ws(
    station_id: StationId,
    *,
    nwp_source: str,
    role: WeatherSourceRole,
    status: WeatherSourceStatus = WeatherSourceStatus.ACTIVE,
    extraction: SpatialRepresentation = SpatialRepresentation.BASIN_AVERAGE,
) -> StationWeatherSource:
    return StationWeatherSource(
        station_id=station_id,
        nwp_source=nwp_source,
        extraction_type=extraction,
        status=status,
        role=role,
    )


def _wide_df(
    polygons: list[str],
    *,
    value: float | dict[str, float],
    with_provenance: bool = False,
    source: str = "ifs",
    source_run: object | None = None,
) -> pd.DataFrame:
    times = [
        pd.Timestamp("2026-01-01T00:00:00Z"),
        pd.Timestamp("2026-01-01T03:00:00Z"),
    ]
    index = pd.DatetimeIndex(times, name="time")
    if isinstance(value, dict):
        data = {p: [value[p]] * len(times) for p in polygons}
    else:
        data = {p: [value] * len(times) for p in polygons}
    df = pd.DataFrame(data, index=index)
    if with_provenance:
        df["source"] = source
        df["source_run"] = source_run if source_run is not None else _SOURCE_RUN
    return df


class _MapResolver:
    def __init__(self, mapping: dict[StationId, GatewayPolygonRef | None]) -> None:
        self._mapping = mapping

    def resolve(self, source: StationWeatherSource) -> GatewayPolygonRef | None:
        return self._mapping.get(source.station_id)


class _RaisingResolver:
    def resolve(self, source: StationWeatherSource) -> GatewayPolygonRef | None:
        raise AssertionError("resolver must not be called for an excluded config")


class _ForecastEcmwf:
    def __init__(self, df: pd.DataFrame) -> None:
        self.calls: list[dict[str, object]] = []
        self._df = df

    def ifs_forecast(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        return self._df

    def era5_land_reanalysis(self, **kwargs: object) -> object:
        raise AssertionError("forecast adapter must not call era5_land_reanalysis")


# Fakes return a value keyed to the SPECIFIC requested source variable, not inferred
# from a loose predicate. A wrong-variable dispatch (e.g. temperature routed as
# precipitation) hits the wrong key -> wrong value -> the value assertions fail, so the
# fakes discriminate the adapter's variable mapping rather than rubber-stamping it.
_ERA5_VALUE_BY_VARIABLE: dict[str, float] = {
    "total_precipitation": 1.0,  # metres -> *1000 = 1000.0 mm
    "2m_temperature": 300.0,  # kelvin -> 26.85 °C
}
_SNOW_VALUE_BY_VARIABLE: dict[str, float] = {
    "hs": 5.0,
    "rof": 7.0,
    "swe": 9.0,
}


def _lookup(mapping: dict[str, float], variable: object) -> float:
    key = str(variable)
    if key not in mapping:
        raise AssertionError(f"fake received unexpected variable {key!r}")
    return mapping[key]


class _ReanalysisEcmwf:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def ifs_forecast(self, **kwargs: object) -> object:
        raise AssertionError("reanalysis adapter must not call ifs_forecast")

    def era5_land_reanalysis(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        return _wide_df(
            [_POLY_A],
            value=_lookup(_ERA5_VALUE_BY_VARIABLE, kwargs["variable"]),
            with_provenance=True,
            source="era5_land",
            source_run=_SOURCE_RUN,
        )


class _ReanalysisSnow:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def reanalysis(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        return _wide_df(
            [_POLY_A],
            value=_lookup(_SNOW_VALUE_BY_VARIABLE, kwargs["variable"]),
            with_provenance=True,
            source="jsnow_reanalysis",
            source_run=_SOURCE_RUN,
        )


class _Client:
    def __init__(self, ecmwf: object, snow: object) -> None:
        self.ecmwf = ecmwf
        self.snow = snow


def _forecast_adapter(ecmwf: object, resolver: object) -> RecapGatewayForecastAdapter:
    return RecapGatewayForecastAdapter(
        client=_Client(ecmwf, _GoodSnow()),  # type: ignore[arg-type]
        resolver=resolver,  # type: ignore[arg-type]
    )


def _reanalysis_adapter(
    ecmwf: object, snow: object, resolver: object
) -> RecapGatewayReanalysisAdapter:
    return RecapGatewayReanalysisAdapter(
        client=_Client(ecmwf, snow),  # type: ignore[arg-type]
        resolver=resolver,  # type: ignore[arg-type]
    )


class TestProtocolConformance:
    def _forecast(self) -> RecapGatewayForecastAdapter:
        return _forecast_adapter(_GoodEcmwf(), _GoodResolver())

    def _reanalysis(self) -> RecapGatewayReanalysisAdapter:
        return _reanalysis_adapter(_GoodEcmwf(), _GoodSnow(), _GoodResolver())

    def test_forecast_is_forecast_source(self) -> None:
        assert isinstance(self._forecast(), WeatherForecastSource)

    def test_forecast_not_reanalysis_source(self) -> None:
        assert not isinstance(self._forecast(), WeatherReanalysisSource)

    def test_reanalysis_is_reanalysis_source(self) -> None:
        assert isinstance(self._reanalysis(), WeatherReanalysisSource)

    def test_reanalysis_not_forecast_source(self) -> None:
        assert not isinstance(self._reanalysis(), WeatherForecastSource)


class TestForecastStorageKey:
    def test_storage_key_is_ifs_ecmwf(self) -> None:
        ecmwf = _ForecastEcmwf(_wide_df([_POLY_A], value=1.0))
        adapter = _forecast_adapter(
            ecmwf, _MapResolver({_SID_A: _ref(_SID_A, _POLY_A)})
        )
        result = adapter.fetch_forecasts(
            [_ws(_SID_A, nwp_source="ifs_ecmwf", role=WeatherSourceRole.FORECAST)],
            _CYCLE,
        )
        assert result
        for forecast in result.values():
            assert forecast.nwp_source == "ifs_ecmwf"
            assert forecast.nwp_source not in {
                "era5_land",
                "recap_era5_land_reanalysis",
                "recap_snow_reanalysis",
            }


class TestDataFrameParsing:
    def test_provenance_split_present(self) -> None:
        df = _wide_df([_POLY_A], value=1.0, with_provenance=True)
        numeric, source_run = _split_provenance(df)
        assert list(numeric.columns) == [_POLY_A]
        assert "source" not in numeric.columns
        assert "source_run" not in numeric.columns
        assert source_run == _SOURCE_RUN

    def test_provenance_split_absent(self) -> None:
        df = _wide_df([_POLY_A], value=1.0, with_provenance=False)
        numeric, source_run = _split_provenance(df)
        assert list(numeric.columns) == [_POLY_A]
        assert source_run is None

    def test_wide_to_long_maps_by_polygon_not_text(self) -> None:
        df = _wide_df([_POLY_A, _POLY_B], value={_POLY_A: 1.0, _POLY_B: 2.0})
        # Only station A is resolved; B's column is present but must be ignored,
        # proving the demux keys on resolved polygon_name, not column-text parsing.
        refs = {GatewayPolygonName(_POLY_A): _ref(_SID_A, _POLY_A)}
        rows, _ = _iter_long_rows(df, refs, None)
        assert {r[0].station_id for r in rows} == {_SID_A}
        assert {r[2] for r in rows} == {1.0}
        assert all(not isinstance(r[1], pd.Timestamp) for r in rows)
        assert all(isinstance(r[1], datetime) for r in rows)

    def test_wide_to_long_applies_conversion_once(self) -> None:
        df = _wide_df([_POLY_A], value=1.0)
        refs = {GatewayPolygonName(_POLY_A): _ref(_SID_A, _POLY_A)}
        rows, _ = _iter_long_rows(df, refs, _metres_to_mm)
        assert {r[2] for r in rows} == {1000.0}

    def test_missing_expected_polygon_column_raises(self) -> None:
        # Response carries only A, but both A and B are resolved for this HRU. B
        # must fail loud, not silently vanish from the batch.
        df = _wide_df([_POLY_A], value=1.0)
        refs = {
            GatewayPolygonName(_POLY_A): _ref(_SID_A, _POLY_A),
            GatewayPolygonName(_POLY_B): _ref(_SID_B, _POLY_B),
        }
        with pytest.raises(AdapterError, match=_POLY_B):
            _iter_long_rows(df, refs, None)

    def test_hru_batch_single_fetch_per_variable(self) -> None:
        # Two stations share one HRU under distinct polygons: exactly one fetch per
        # (hru, variable, cycle) — for forecasts a 51-call ensemble per variable,
        # NOT 51 x 2 stations. A naive per-station loop fails this.
        ecmwf = _ForecastEcmwf(
            _wide_df([_POLY_A, _POLY_B], value={_POLY_A: 1.0, _POLY_B: 2.0})
        )
        resolver = _MapResolver(
            {_SID_A: _ref(_SID_A, _POLY_A), _SID_B: _ref(_SID_B, _POLY_B)}
        )
        adapter = _forecast_adapter(ecmwf, resolver)
        result = adapter.fetch_forecasts(
            [
                _ws(_SID_A, nwp_source="ifs_ecmwf", role=WeatherSourceRole.FORECAST),
                _ws(_SID_B, nwp_source="ifs_ecmwf", role=WeatherSourceRole.FORECAST),
            ],
            _CYCLE,
        )
        tp_calls = [c for c in ecmwf.calls if c["variable"] == "tp"]
        # 51 real (1 fc + 50 pf) + 1 resolve_latest_cycle probe call (Codex
        # review Finding 1) -- the probe shares the exact fc/tp call shape,
        # so it is indistinguishable from a real call by kwargs alone.
        assert len(tp_calls) == 52
        assert set(result.keys()) == {_SID_A, _SID_B}
        # Each station must receive ITS polygon's values, not the other's — a
        # swapped/mis-mapped demux (station A gets B's column) would still pass
        # the assertions above. Filter to precipitation (converted via *1000)
        # since the fake returns the same base df for every requested variable.
        forecast_a = result[_SID_A]
        forecast_b = result[_SID_B]
        assert isinstance(forecast_a, BasinAverageForecast)
        assert isinstance(forecast_b, BasinAverageForecast)
        precip_a = forecast_a.values.filter(
            forecast_a.values["parameter"] == "precipitation"
        )
        precip_b = forecast_b.values.filter(
            forecast_b.values["parameter"] == "precipitation"
        )
        assert set(precip_a["value"].to_list()) == {1000.0}
        assert set(precip_b["value"].to_list()) == {2000.0}


class TestForecastReturnShape:
    def test_basin_average_results(self) -> None:
        ecmwf = _ForecastEcmwf(_wide_df([_POLY_A], value=1.0))
        adapter = _forecast_adapter(
            ecmwf, _MapResolver({_SID_A: _ref(_SID_A, _POLY_A)})
        )
        result = adapter.fetch_forecasts(
            [_ws(_SID_A, nwp_source="ifs_ecmwf", role=WeatherSourceRole.FORECAST)],
            _CYCLE,
        )
        assert set(result.keys()) == {_SID_A}
        forecast = result[_SID_A]
        assert isinstance(forecast, BasinAverageForecast)
        assert "band_id" not in forecast.values.columns
        assert forecast.cycle_time.utcoffset() == timedelta(0)

    def test_per_item_isolation_partial(self) -> None:
        ecmwf = _ForecastEcmwf(_wide_df([_POLY_A], value=1.0))
        resolver = _MapResolver({_SID_A: _ref(_SID_A, _POLY_A), _SID_B: None})
        adapter = _forecast_adapter(ecmwf, resolver)
        with structlog.testing.capture_logs() as captured:
            result = adapter.fetch_forecasts(
                [
                    _ws(
                        _SID_A, nwp_source="ifs_ecmwf", role=WeatherSourceRole.FORECAST
                    ),
                    _ws(
                        _SID_B, nwp_source="ifs_ecmwf", role=WeatherSourceRole.FORECAST
                    ),
                ],
                _CYCLE,
            )
        assert set(result.keys()) == {_SID_A}
        unmapped_events = [
            e for e in captured if e.get("event") == "recap.station_unmapped"
        ]
        assert any(e.get("station_id") == str(_SID_B) for e in unmapped_events)

    def test_all_unmappable_raises(self) -> None:
        ecmwf = _ForecastEcmwf(_wide_df([_POLY_A], value=1.0))
        resolver = _MapResolver({_SID_A: None, _SID_B: None})
        adapter = _forecast_adapter(ecmwf, resolver)
        with pytest.raises(GatewayResolutionError) as excinfo:
            adapter.fetch_forecasts(
                [
                    _ws(
                        _SID_A,
                        nwp_source="ifs_ecmwf",
                        role=WeatherSourceRole.FORECAST,
                    ),
                    _ws(
                        _SID_B,
                        nwp_source="ifs_ecmwf",
                        role=WeatherSourceRole.FORECAST,
                    ),
                ],
                _CYCLE,
            )
        assert excinfo.value.station_id in {_SID_A, _SID_B}

    def _assert_excluded(self, config: StationWeatherSource) -> None:
        ecmwf = _ForecastEcmwf(_wide_df([_POLY_A], value=1.0))
        adapter = _forecast_adapter(ecmwf, _RaisingResolver())
        result = adapter.fetch_forecasts([config], _CYCLE)
        assert result == {}
        assert ecmwf.calls == []

    def test_prefilter_wrong_source(self) -> None:
        self._assert_excluded(
            _ws(_SID_A, nwp_source="other", role=WeatherSourceRole.FORECAST)
        )

    def test_prefilter_inactive(self) -> None:
        self._assert_excluded(
            _ws(
                _SID_A,
                nwp_source="ifs_ecmwf",
                role=WeatherSourceRole.FORECAST,
                status=WeatherSourceStatus.INACTIVE,
            )
        )

    def test_prefilter_non_basin_average(self) -> None:
        self._assert_excluded(
            _ws(
                _SID_A,
                nwp_source="ifs_ecmwf",
                role=WeatherSourceRole.FORECAST,
                extraction=SpatialRepresentation.ELEVATION_BAND,
            )
        )


class TestForecastRunHour:
    def _calls_for_cycle(self, hour: int) -> list[dict[str, object]]:
        cycle = ensure_utc(datetime(2026, 1, 1, hour, tzinfo=UTC))
        ecmwf = _ForecastEcmwf(_wide_df([_POLY_A], value=1.0))
        adapter = _forecast_adapter(
            ecmwf, _MapResolver({_SID_A: _ref(_SID_A, _POLY_A)})
        )
        adapter.fetch_forecasts(
            [_ws(_SID_A, nwp_source="ifs_ecmwf", role=WeatherSourceRole.FORECAST)],
            cycle,
        )
        return [c for c in ecmwf.calls if c["variable"] == "tp"]

    def test_run_hour_matches_non_midnight_cycle(self) -> None:
        # Regression: without an explicit run_hour the client defaults to 0, so a
        # 12Z cycle would silently fetch the 00Z run. Both fc and pf calls must
        # carry run_hour == cycle_time.hour.
        tp_calls = self._calls_for_cycle(12)
        assert tp_calls
        assert all(c["run_hour"] == 12 for c in tp_calls)
        fc = [c for c in tp_calls if c["ifs_type"] == "fc"]
        pf = [c for c in tp_calls if c["ifs_type"] == "pf"]
        assert fc and all(c["run_hour"] == 12 for c in fc)
        assert len(pf) == 50 and all(c["run_hour"] == 12 for c in pf)

    def test_run_hour_midnight_cycle(self) -> None:
        tp_calls = self._calls_for_cycle(0)
        assert tp_calls
        assert all(c["run_hour"] == 0 for c in tp_calls)


class TestDuplicatePolygonResolution:
    def test_two_stations_same_polygon_raises(self) -> None:
        # Two distinct stations resolving to one (hru, polygon) is a 1:1-resolver
        # config error; must fail loud naming both, not silently drop one.
        ecmwf = _ForecastEcmwf(_wide_df([_POLY_A], value=1.0))
        resolver = _MapResolver(
            {_SID_A: _ref(_SID_A, _POLY_A), _SID_B: _ref(_SID_B, _POLY_A)}
        )
        adapter = _forecast_adapter(ecmwf, resolver)
        with pytest.raises(RecapConfigurationError) as excinfo:
            adapter.fetch_forecasts(
                [
                    _ws(
                        _SID_A,
                        nwp_source="ifs_ecmwf",
                        role=WeatherSourceRole.FORECAST,
                    ),
                    _ws(
                        _SID_B,
                        nwp_source="ifs_ecmwf",
                        role=WeatherSourceRole.FORECAST,
                    ),
                ],
                _CYCLE,
            )
        msg = str(excinfo.value)
        assert str(_SID_A) in msg
        assert str(_SID_B) in msg
        assert ecmwf.calls == []


class TestMissingPolygonColumnBatch:
    def test_missing_polygon_column_fails_mixed_batch(self) -> None:
        # A and B share one HRU but the Gateway response only carries A's column.
        # B must not silently disappear from the batch.
        ecmwf = _ForecastEcmwf(_wide_df([_POLY_A], value=1.0))
        resolver = _MapResolver(
            {_SID_A: _ref(_SID_A, _POLY_A), _SID_B: _ref(_SID_B, _POLY_B)}
        )
        adapter = _forecast_adapter(ecmwf, resolver)
        with pytest.raises(AdapterError) as excinfo:
            adapter.fetch_forecasts(
                [
                    _ws(
                        _SID_A,
                        nwp_source="ifs_ecmwf",
                        role=WeatherSourceRole.FORECAST,
                    ),
                    _ws(
                        _SID_B,
                        nwp_source="ifs_ecmwf",
                        role=WeatherSourceRole.FORECAST,
                    ),
                ],
                _CYCLE,
            )
        msg = str(excinfo.value)
        assert str(_SID_B) in msg
        assert _POLY_B in msg


class TestIfsEnsembleAssembly:
    def _run(self) -> tuple[_ForecastEcmwf, dict[StationId, object]]:
        ecmwf = _ForecastEcmwf(_wide_df([_POLY_A], value=1.0))
        adapter = _forecast_adapter(
            ecmwf, _MapResolver({_SID_A: _ref(_SID_A, _POLY_A)})
        )
        result = adapter.fetch_forecasts(
            [_ws(_SID_A, nwp_source="ifs_ecmwf", role=WeatherSourceRole.FORECAST)],
            _CYCLE,
        )
        return ecmwf, result

    def test_one_fc_fifty_pf_per_variable(self) -> None:
        ecmwf, _ = self._run()
        tp_calls = [c for c in ecmwf.calls if c["variable"] == "tp"]
        fc_calls = [c for c in tp_calls if c["ifs_type"] == "fc"]
        pf_calls = [c for c in tp_calls if c["ifs_type"] == "pf"]
        # 1 real fc/tp call + 1 resolve_latest_cycle probe call (Codex review
        # Finding 1) -- the probe shares the exact fc/tp call shape.
        assert len(fc_calls) == 2
        assert len(pf_calls) == 50

    def test_fc_sends_no_member(self) -> None:
        ecmwf, _ = self._run()
        fc_calls = [
            c for c in ecmwf.calls if c["variable"] == "tp" and c["ifs_type"] == "fc"
        ]
        assert "member" not in fc_calls[0]

    def test_pf_members_exactly_one_to_fifty(self) -> None:
        ecmwf, _ = self._run()
        pf_members = {
            int(str(c["member"]))
            for c in ecmwf.calls
            if c["variable"] == "tp" and c["ifs_type"] == "pf"
        }
        assert pf_members == set(range(1, 51))

    def test_assembled_member_ids_zero_to_fifty(self) -> None:
        _, result = self._run()
        forecast = result[_SID_A]
        assert isinstance(forecast, BasinAverageForecast)
        member_ids = set(forecast.values["member_id"].to_list())
        assert member_ids == set(range(0, 51))


class _FallbackFakeClientError(Exception):
    def __init__(self, message: str, **attrs: object) -> None:
        super().__init__(message)
        for key, value in attrs.items():
            setattr(self, key, value)


class _CycleFallbackEcmwf:
    """Raises ``source_data_missing`` for configured ``run_hour``s, succeeds
    otherwise. Both the ``resolve_latest_cycle`` PROBE call (``ifs_type="fc"``,
    ``variable="tp"``) and the real per-variable/per-member data calls go
    through this SAME ``ifs_forecast`` method, matching the real Gateway API
    (Codex review Finding 1)."""

    def __init__(self, *, missing_hours: set[int]) -> None:
        self.calls: list[dict[str, object]] = []
        self._missing_hours = missing_hours

    def ifs_forecast(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        run_hour = int(kwargs["run_hour"])  # type: ignore[arg-type]
        if run_hour in self._missing_hours:
            raise _FallbackFakeClientError(
                "not published yet", code="source_data_missing"
            )
        return _wide_df(
            [_POLY_A],
            value=1.0,
            with_provenance=True,
            source="ifs",
            source_run=pd.Timestamp(f"2026-01-01T{run_hour:02d}:00:00Z"),
        )


class TestCycleFallbackWiring:
    """Codex review Finding 1 (blocker): fetch_forecasts must resolve the
    newest AVAILABLE IFS cycle via resolve_latest_cycle instead of blindly
    passing the nominal cycle_time through to every Gateway call, degrading
    to runoff-only whenever the nominal cycle happens to be unpublished."""

    def test_falls_back_to_older_cycle_when_nominal_unpublished(self) -> None:
        nominal_cycle = ensure_utc(datetime(2026, 1, 1, 12, tzinfo=UTC))
        ecmwf = _CycleFallbackEcmwf(missing_hours={12})
        adapter = _forecast_adapter(
            ecmwf, _MapResolver({_SID_A: _ref(_SID_A, _POLY_A)})
        )

        result = adapter.fetch_forecasts(
            [_ws(_SID_A, nwp_source="ifs_ecmwf", role=WeatherSourceRole.FORECAST)],
            nominal_cycle,
        )

        assert _SID_A in result
        forecast = result[_SID_A]
        assert isinstance(forecast, BasinAverageForecast)
        # 12Z was unpublished; the adapter must fall back to 06Z (the next
        # older 6h-cadence candidate) and fetch the FULL forecast at that
        # resolved cycle -- not degrade to an empty/no-op result.
        assert forecast.cycle_time == ensure_utc(datetime(2026, 1, 1, 6, tzinfo=UTC))
        assert not forecast.values.is_empty()
        pf_run_hours = {c["run_hour"] for c in ecmwf.calls if c.get("ifs_type") == "pf"}
        assert pf_run_hours == {6}

    def test_degrades_when_all_candidates_within_max_age_missing(self) -> None:
        nominal_cycle = ensure_utc(datetime(2026, 1, 1, 12, tzinfo=UTC))
        ecmwf = _CycleFallbackEcmwf(missing_hours={12, 6})
        adapter = RecapGatewayForecastAdapter(
            client=_Client(ecmwf, _GoodSnow()),  # type: ignore[arg-type]
            resolver=_MapResolver(  # type: ignore[arg-type]
                {_SID_A: _ref(_SID_A, _POLY_A)}
            ),
            max_cycle_age_hours=6.0,
        )

        with pytest.raises(RecapDataUnavailableError) as excinfo:
            adapter.fetch_forecasts(
                [_ws(_SID_A, nwp_source="ifs_ecmwf", role=WeatherSourceRole.FORECAST)],
                nominal_cycle,
            )

        assert excinfo.value.code == "source_data_missing"
        # Only the two probe candidates within max_cycle_age_hours=6h were
        # tried -- the adapter must bail out BEFORE the 102-call member
        # fan-out, not attempt it first.
        assert len(ecmwf.calls) == 2


class _PfUnavailableEcmwf:
    """fc (and the resolve_latest_cycle probe) succeed; every pf member fetch
    raises a data-unavailable client error (the fc-before-pf dissemination
    window). Both go through the SAME ifs_forecast method, matching the real
    Gateway API (Plan 127 Fix 1)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def ifs_forecast(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        if kwargs.get("ifs_type") == "pf":
            raise _FallbackFakeClientError(
                "No IFS dataset found", code="source_data_missing"
            )
        return _wide_df(
            [_POLY_A],
            value=1.0,
            with_provenance=True,
            source="ifs",
            source_run=_SOURCE_RUN,
        )

    def era5_land_reanalysis(self, **kwargs: object) -> object:
        raise AssertionError("forecast adapter must not call era5_land_reanalysis")


class TestPfUnavailableControlOnly:
    """Plan 127 Fix 1: a data-unavailable pf fetch (fc-before-pf window) must not
    abort the whole NWP fetch — keep the fc (control) records, break the pf
    loop. RED against pre-fix: the pf RecapDataUnavailableError propagates out of
    fetch_forecasts and raises."""

    def test_pf_unavailable_returns_fc_only_records(self) -> None:
        ecmwf = _PfUnavailableEcmwf()
        adapter = _forecast_adapter(
            ecmwf, _MapResolver({_SID_A: _ref(_SID_A, _POLY_A)})
        )

        result = adapter.fetch_forecasts(
            [_ws(_SID_A, nwp_source="ifs_ecmwf", role=WeatherSourceRole.FORECAST)],
            _CYCLE,
        )

        assert set(result.keys()) == {_SID_A}
        forecast = result[_SID_A]
        assert isinstance(forecast, BasinAverageForecast)
        # Only the fc control member (member_id=0) survived; no pf members.
        member_ids = set(forecast.values["member_id"].to_list())
        assert member_ids == {0}
        # The pf loop broke on the FIRST missing member — one pf probe per
        # variable, not 50. Two IFS variables (tp, 2t) -> 2 pf calls.
        pf_calls = [c for c in ecmwf.calls if c.get("ifs_type") == "pf"]
        assert all(int(str(c["member"])) == 1 for c in pf_calls)


class TestReanalysisConversion:
    def _run(
        self,
        parameters: list[str],
        *,
        resolver: object | None = None,
        sources: list[StationWeatherSource] | None = None,
    ) -> tuple[list[object], _ReanalysisEcmwf, _ReanalysisSnow]:
        ecmwf = _ReanalysisEcmwf()
        snow = _ReanalysisSnow()
        resolver = resolver or _MapResolver({_SID_A: _ref(_SID_A, _POLY_A)})
        sources = sources or [
            _ws(_SID_A, nwp_source="era5_land", role=WeatherSourceRole.REANALYSIS)
        ]
        adapter = _reanalysis_adapter(ecmwf, snow, resolver)
        rows = adapter.fetch_reanalysis(sources, _START, _END, parameters)
        return list(rows), ecmwf, snow

    def test_era5_provenance_literal_and_conversion(self) -> None:
        rows, ecmwf, snow = self._run(["precipitation"])
        assert rows
        # precipitation must dispatch the era5 `total_precipitation` variable; the
        # keyed fake returns 1.0 m only for that key -> *1000 = 1000.0 mm.
        assert [c["variable"] for c in ecmwf.calls] == ["total_precipitation"]
        assert {r.source for r in rows} == {"recap_era5_land_reanalysis"}
        assert {r.value for r in rows} == {1000.0}
        assert snow.calls == []

    def test_temperature_conversion(self) -> None:
        rows, ecmwf, _ = self._run(["temperature"])
        assert rows
        # temperature must dispatch `2m_temperature`; the keyed fake returns 300.0 K
        # only for that key -> 26.85 °C.
        assert [c["variable"] for c in ecmwf.calls] == ["2m_temperature"]
        assert all(abs(r.value - 26.85) < 1e-9 for r in rows)

    def test_snow_provenance_and_passthrough(self) -> None:
        rows, ecmwf, snow = self._run(["snow_depth"])
        assert rows
        # snow_depth must dispatch the snow `hs` variable; the keyed fake returns 5.0
        # only for that key (convert is None -> passthrough).
        assert [c["variable"] for c in snow.calls] == ["hs"]
        assert {r.source for r in rows} == {"recap_snow_reanalysis"}
        assert {r.value for r in rows} == {5.0}
        assert ecmwf.calls == []

    def test_deterministic_member_none(self) -> None:
        rows, _, _ = self._run(["precipitation"])
        assert rows
        assert all(r.member_id is None for r in rows)

    def test_basin_average_invariant(self) -> None:
        rows, _, _ = self._run(["precipitation"])
        assert rows
        assert all(r.spatial_type is SpatialRepresentation.BASIN_AVERAGE for r in rows)
        assert all(r.band_id is None for r in rows)

    def test_version_is_iso_string(self) -> None:
        rows, _, _ = self._run(["precipitation"])
        assert rows
        assert all(isinstance(r.version, str) for r in rows)
        assert all(r.version.startswith("2026-01-01T00:00:00") for r in rows)

    def test_parameter_selection_precip_only(self) -> None:
        _, ecmwf, snow = self._run(["precipitation"])
        assert len(ecmwf.calls) == 1
        assert ecmwf.calls[0]["variable"] == "total_precipitation"
        assert snow.calls == []

    def test_unmapped_parameter_skipped(self) -> None:
        rows, ecmwf, snow = self._run(["temperature_min"])
        assert rows == []
        assert ecmwf.calls == []
        assert snow.calls == []

    def test_mixed_mapped_and_unmapped(self) -> None:
        rows, _, _ = self._run(["precipitation", "temperature_min"])
        assert rows
        assert {r.parameter for r in rows} == {"precipitation"}

    def _assert_excluded(self, config: StationWeatherSource) -> None:
        rows, ecmwf, snow = self._run(
            ["precipitation"], resolver=_RaisingResolver(), sources=[config]
        )
        assert rows == []
        assert ecmwf.calls == []
        assert snow.calls == []

    def test_prefilter_wrong_source(self) -> None:
        self._assert_excluded(
            _ws(_SID_A, nwp_source="other", role=WeatherSourceRole.REANALYSIS)
        )

    def test_prefilter_inactive(self) -> None:
        self._assert_excluded(
            _ws(
                _SID_A,
                nwp_source="era5_land",
                role=WeatherSourceRole.REANALYSIS,
                status=WeatherSourceStatus.INACTIVE,
            )
        )

    def test_prefilter_non_basin_average(self) -> None:
        self._assert_excluded(
            _ws(
                _SID_A,
                nwp_source="era5_land",
                role=WeatherSourceRole.REANALYSIS,
                extraction=SpatialRepresentation.ELEVATION_BAND,
            )
        )

    def test_per_item_isolation_partial(self) -> None:
        resolver = _MapResolver({_SID_A: _ref(_SID_A, _POLY_A), _SID_B: None})
        sources = [
            _ws(_SID_A, nwp_source="era5_land", role=WeatherSourceRole.REANALYSIS),
            _ws(_SID_B, nwp_source="era5_land", role=WeatherSourceRole.REANALYSIS),
        ]
        with structlog.testing.capture_logs() as captured:
            rows, _, _ = self._run(
                ["precipitation"], resolver=resolver, sources=sources
            )
        assert {r.station_id for r in rows} == {_SID_A}
        unmapped_events = [
            e for e in captured if e.get("event") == "recap.station_unmapped"
        ]
        assert any(e.get("station_id") == str(_SID_B) for e in unmapped_events)

    def test_all_unmappable_raises(self) -> None:
        resolver = _MapResolver({_SID_A: None, _SID_B: None})
        sources = [
            _ws(_SID_A, nwp_source="era5_land", role=WeatherSourceRole.REANALYSIS),
            _ws(_SID_B, nwp_source="era5_land", role=WeatherSourceRole.REANALYSIS),
        ]
        with pytest.raises(GatewayResolutionError):
            self._run(["precipitation"], resolver=resolver, sources=sources)


class _FakeClientError(Exception):
    def __init__(self, message: str, **attrs: object) -> None:
        super().__init__(message)
        for key, value in attrs.items():
            setattr(self, key, value)


class TestErrorMapping:
    def test_source_data_missing(self) -> None:
        mapped = _map_recap_error(
            _FakeClientError("missing", code="source_data_missing", field="run_date")
        )
        assert isinstance(mapped, RecapDataUnavailableError)
        assert mapped.code == "source_data_missing"

    def test_unsupported_shapefile_config(self) -> None:
        mapped = _map_recap_error(
            _FakeClientError(
                "bad hru",
                code="unsupported_shapefile",
                field="hru_code",
                supported_values=["a", "b"],
            )
        )
        assert isinstance(mapped, RecapConfigurationError)
        assert mapped.field == "hru_code"
        assert mapped.supported_values == ["a", "b"]

    def test_generic_validation_with_supported_values_is_config(self) -> None:
        # Structural: a generic validation code with supported_values maps to config
        # too, so the unverified `unsupported_shapefile` literal is never the sole path.
        mapped = _map_recap_error(
            _FakeClientError(
                "bad param",
                code="unsupported_parameter",
                field="variable",
                supported_values=["2t", "tp"],
            )
        )
        assert isinstance(mapped, RecapConfigurationError)
        assert mapped.supported_values == ["2t", "tp"]

    def test_other_validation_is_generic(self) -> None:
        mapped = _map_recap_error(
            _FakeClientError("bad", code="some_other", field="variable")
        )
        assert isinstance(mapped, AdapterError)
        assert not isinstance(
            mapped, RecapDataUnavailableError | RecapConfigurationError
        )

    def test_plain_exception_is_retriable(self) -> None:
        mapped = _map_recap_error(_FakeClientError("network down"))
        assert isinstance(mapped, AdapterError)
        assert not isinstance(
            mapped, RecapDataUnavailableError | RecapConfigurationError
        )

    def test_status_401_maps_to_auth_error(self) -> None:
        mapped = _map_recap_error(_FakeClientError("unauthorized", status_code=401))
        assert isinstance(mapped, RecapAuthError)
        assert mapped.status_code == 401

    def test_status_403_maps_to_auth_error(self) -> None:
        mapped = _map_recap_error(_FakeClientError("forbidden", status_code=403))
        assert isinstance(mapped, RecapAuthError)
        assert mapped.status_code == 403

    def test_status_500_does_not_map_to_auth_error(self) -> None:
        # Negative control: a non-auth status code must not be swept into
        # RecapAuthError — it stays the generic (retriable) AdapterError.
        mapped = _map_recap_error(_FakeClientError("server error", status_code=500))
        assert not isinstance(mapped, RecapAuthError)
        assert isinstance(mapped, AdapterError)

    def test_config_error_code_takes_priority_over_status_code(self) -> None:
        # A structured validation error with BOTH a code and a 401 status
        # must map to RecapConfigurationError (more specific), not auth.
        mapped = _map_recap_error(
            _FakeClientError(
                "bad hru",
                field="hru_code",
                supported_values=["a"],
                status_code=401,
            )
        )
        assert isinstance(mapped, RecapConfigurationError)


# --- Regression fixtures for the three Codex-fixed majors -------------------


def _reanalysis_frame(
    *,
    times: list[pd.Timestamp],
    values: list[float],
    source_runs: list[object],
) -> pd.DataFrame:
    """One-variable ERA5 frame with a hand-controlled per-row time and source_run."""
    index = pd.DatetimeIndex(times, name="time")
    df = pd.DataFrame({_POLY_A: values}, index=index)
    df["source"] = "era5_land"
    df["source_run"] = source_runs
    return df


class _FixedFrameEcmwf:
    """Reanalysis ECMWF fake returning a caller-supplied frame verbatim."""

    def __init__(self, df: pd.DataFrame) -> None:
        self.calls: list[dict[str, object]] = []
        self._df = df

    def ifs_forecast(self, **kwargs: object) -> object:
        raise AssertionError("reanalysis adapter must not call ifs_forecast")

    def era5_land_reanalysis(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        return self._df


def _era5_source(
    df: pd.DataFrame,
) -> RecapGatewayReanalysisAdapter:
    return _reanalysis_adapter(
        _FixedFrameEcmwf(df), _GoodSnow(), _MapResolver({_SID_A: _ref(_SID_A, _POLY_A)})
    )


class TestReanalysisPerRowProvenance:
    """Major 1: each emitted record's version must reflect ITS OWN row's source_run."""

    def test_distinct_source_runs_yield_distinct_versions(self) -> None:
        # Two in-window rows carrying DIFFERENT source_run timestamps. The per-row fix
        # must give each record its own version. The pre-fix collapse-to-first-non-null
        # bug applied one scalar to every row -> both versions identical -> this fails.
        df = _reanalysis_frame(
            times=[
                pd.Timestamp("2026-01-01T00:00:00Z"),
                pd.Timestamp("2026-01-01T12:00:00Z"),
            ],
            values=[0.001, 0.002],
            source_runs=[
                pd.Timestamp("2026-01-01T00:00:00Z"),
                pd.Timestamp("2026-01-01T06:00:00Z"),
            ],
        )
        adapter = _era5_source(df)
        rows = adapter.fetch_reanalysis(
            [_ws(_SID_A, nwp_source="era5_land", role=WeatherSourceRole.REANALYSIS)],
            _START,
            _END,
            ["precipitation"],
        )
        versions = [r.version for r in rows]
        assert len(versions) == 2
        assert len(set(versions)) == 2
        assert set(versions) == {
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T06:00:00+00:00",
        }


class TestReanalysisWindowFilter:
    """Major 2: rows outside [start, end) must be dropped (client only sees dates)."""

    def test_out_of_window_rows_dropped(self) -> None:
        # A non-midnight [06:00, 18:00) request. Because the client's _iso_date strips
        # the window to bare dates, the returned frame spans the whole day. Rows before
        # start, at end, and after end must be filtered out; without the filter all five
        # rows leak -> this fails.
        start = ensure_utc(datetime(2026, 1, 1, 6, tzinfo=UTC))
        end = ensure_utc(datetime(2026, 1, 1, 18, tzinfo=UTC))
        df = _reanalysis_frame(
            times=[
                pd.Timestamp("2026-01-01T00:00:00Z"),  # before start -> drop
                pd.Timestamp("2026-01-01T06:00:00Z"),  # == start -> keep
                pd.Timestamp("2026-01-01T12:00:00Z"),  # in window -> keep
                pd.Timestamp("2026-01-01T18:00:00Z"),  # == end -> drop (half-open)
                pd.Timestamp("2026-01-01T20:00:00Z"),  # after end -> drop
            ],
            values=[0.0, 0.001, 0.002, 0.003, 0.004],
            source_runs=[_SOURCE_RUN] * 5,
        )
        adapter = _era5_source(df)
        rows = adapter.fetch_reanalysis(
            [_ws(_SID_A, nwp_source="era5_land", role=WeatherSourceRole.REANALYSIS)],
            start,
            end,
            ["precipitation"],
        )
        assert {r.valid_time for r in rows} == {
            ensure_utc(datetime(2026, 1, 1, 6, tzinfo=UTC)),
            ensure_utc(datetime(2026, 1, 1, 12, tzinfo=UTC)),
        }


class TestBasinAverageOnlyLock:
    """Major 3: a non-basin-average / wrong-station resolved ref must fail loud."""

    def test_elevation_band_ref_raises_not_banded_forecast(self) -> None:
        # The config is basin-average, but the resolver hands back an ELEVATION_BAND
        # ref. The pre-fix code fetched it and emitted an ElevationBandForecast; the
        # resolved-ref lock must reject it BEFORE any fetch -> no forecast returned.
        ecmwf = _ForecastEcmwf(_wide_df([_POLY_A], value=1.0))
        resolver = _MapResolver(
            {
                _SID_A: _ref(
                    _SID_A,
                    _POLY_A,
                    spatial=SpatialRepresentation.ELEVATION_BAND,
                    band_id=2,
                )
            }
        )
        adapter = _forecast_adapter(ecmwf, resolver)
        with pytest.raises(RecapConfigurationError, match="basin-average-only"):
            adapter.fetch_forecasts(
                [_ws(_SID_A, nwp_source="ifs_ecmwf", role=WeatherSourceRole.FORECAST)],
                _CYCLE,
            )
        # Rejected at resolution time — no Gateway call was made.
        assert ecmwf.calls == []

    def test_wrong_station_ref_raises(self) -> None:
        # Resolver returns a ref whose station_id is a DIFFERENT station than the
        # config being resolved. Pre-fix this mis-attributed the fetched rows.
        ecmwf = _ForecastEcmwf(_wide_df([_POLY_A], value=1.0))
        resolver = _MapResolver({_SID_A: _ref(_SID_B, _POLY_A)})
        adapter = _forecast_adapter(ecmwf, resolver)
        with pytest.raises(RecapConfigurationError, match="station"):
            adapter.fetch_forecasts(
                [_ws(_SID_A, nwp_source="ifs_ecmwf", role=WeatherSourceRole.FORECAST)],
                _CYCLE,
            )
        assert ecmwf.calls == []


class _RecordingForecastSnow:
    """Captures every ``forecast(**kwargs)`` call; returns a fixed frame."""

    def __init__(self, df: pd.DataFrame) -> None:
        self.calls: list[dict[str, object]] = []
        self._df = df

    def reanalysis(self, **kwargs: object) -> object:
        raise AssertionError("snow-forecast test must not call reanalysis")

    def forecast(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        return self._df


class TestSnowForecastFetch:
    """Plan 082 Task 2H-snow: deterministic snow-forecast fetch, member_id=None."""

    def _client(self, snow: object) -> object:
        return _Client(_GoodEcmwf(), snow)

    def test_run_hour_is_sent(self) -> None:
        # A non-zero cycle hour (12Z) proves run_hour is threaded from
        # cycle_time.hour, not left at the client's default (0).
        cycle = ensure_utc(datetime(2026, 1, 1, 12, tzinfo=UTC))
        snow = _RecordingForecastSnow(_wide_df([_POLY_A], value=5.0))
        resolver = _GoodResolver()
        adapter = RecapGatewayForecastAdapter(
            client=self._client(snow),  # type: ignore[arg-type]
            resolver=resolver,  # type: ignore[arg-type]
        )

        adapter.fetch_snow_forecast(
            [_ws(_SID, nwp_source="ifs_ecmwf", role=WeatherSourceRole.FORECAST)],
            cycle,
        )

        assert len(snow.calls) == 3  # hs, rof, swe
        for call in snow.calls:
            assert call["run_hour"] == 12

    def test_returns_member_id_none_rows(self) -> None:
        cycle = ensure_utc(datetime(2026, 1, 1, 0, tzinfo=UTC))
        snow = _RecordingForecastSnow(_wide_df([_POLY_A], value=5.0))
        adapter = RecapGatewayForecastAdapter(
            client=self._client(snow),  # type: ignore[arg-type]
            resolver=_GoodResolver(),  # type: ignore[arg-type]
        )

        result = adapter.fetch_snow_forecast(
            [_ws(_SID, nwp_source="ifs_ecmwf", role=WeatherSourceRole.FORECAST)],
            cycle,
        )

        assert _SID in result.forecasts
        forecast = result.forecasts[_SID]
        assert isinstance(forecast, BasinAverageForecast)
        member_ids = forecast.values["member_id"].unique().to_list()
        assert member_ids == [None]
        parameters = set(forecast.values["parameter"].unique().to_list())
        assert parameters == {"snow_depth", "snowmelt", "swe"}
        assert result.unavailable == {}

    def test_no_stations_returns_empty(self) -> None:
        snow = _RecordingForecastSnow(_wide_df([_POLY_A], value=5.0))
        adapter = RecapGatewayForecastAdapter(
            client=self._client(snow),  # type: ignore[arg-type]
            resolver=_GoodResolver(),  # type: ignore[arg-type]
        )

        result = adapter.fetch_snow_forecast([], _CYCLE)

        assert result.forecasts == {}
        assert result.unavailable == {}
        assert snow.calls == []


class _CodedError(Exception):
    """Structurally mimics a recap-dg-client error carrying a ``.code`` attribute."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class _PartiallyUnavailableSnow:
    """Raises for the given variable names; returns data for every other one."""

    def __init__(
        self,
        df: pd.DataFrame,
        *,
        unavailable_variables: set[str],
        code: str = "source_data_missing",
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self._df = df
        self._unavailable = unavailable_variables
        self._code = code

    def reanalysis(self, **kwargs: object) -> object:
        raise AssertionError("snow-forecast test must not call reanalysis")

    def forecast(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        if kwargs["variable"] in self._unavailable:
            raise _CodedError(f"{kwargs['variable']} unavailable", code=self._code)
        return self._df


class _TwoHruSnow:
    """Discriminates by ``hru_code``: one HRU loses a variable, the other is healthy."""

    def __init__(self, *, unavailable_hru: str, unavailable_variable: str) -> None:
        self.calls: list[dict[str, object]] = []
        self._unavailable_hru = unavailable_hru
        self._unavailable_variable = unavailable_variable

    def reanalysis(self, **kwargs: object) -> object:
        raise AssertionError("snow-forecast test must not call reanalysis")

    def forecast(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        hru = kwargs["hru_code"]
        variable = kwargs["variable"]
        if hru == self._unavailable_hru and variable == self._unavailable_variable:
            raise _CodedError(
                f"{variable} unavailable in {hru}", code="source_data_missing"
            )
        poly = _POLY_A if hru == "hru_a" else _POLY_B
        return _wide_df([poly], value=9.0)


class TestSnowForecastFetchDegradation:
    """Plan 145 D3.2a/b/c: distinct snow error, per-(hru,variable) containment."""

    def test_swe_present_snow_depth_and_snowmelt_unavailable(self) -> None:
        cycle = ensure_utc(datetime(2026, 1, 1, 0, tzinfo=UTC))
        snow = _PartiallyUnavailableSnow(
            _wide_df([_POLY_A], value=9.0),
            unavailable_variables={"hs", "rof"},
        )
        adapter = RecapGatewayForecastAdapter(
            client=_Client(_GoodEcmwf(), snow),  # type: ignore[arg-type]
            resolver=_GoodResolver(),  # type: ignore[arg-type]
        )

        result = adapter.fetch_snow_forecast(
            [_ws(_SID, nwp_source="ifs_ecmwf", role=WeatherSourceRole.FORECAST)],
            cycle,
        )

        assert _SID in result.forecasts
        parameters = set(result.forecasts[_SID].values["parameter"].unique().to_list())
        assert parameters == {"swe"}
        assert result.unavailable == {
            GatewayHruName("hru_dhm_west_v001"): frozenset({"snow_depth", "snowmelt"})
        }

    def test_all_snow_variables_unavailable_yields_no_forecast_entry(self) -> None:
        cycle = ensure_utc(datetime(2026, 1, 1, 0, tzinfo=UTC))
        snow = _PartiallyUnavailableSnow(
            _wide_df([_POLY_A], value=9.0),
            unavailable_variables={"hs", "rof", "swe"},
        )
        adapter = RecapGatewayForecastAdapter(
            client=_Client(_GoodEcmwf(), snow),  # type: ignore[arg-type]
            resolver=_GoodResolver(),  # type: ignore[arg-type]
        )

        result = adapter.fetch_snow_forecast(
            [_ws(_SID, nwp_source="ifs_ecmwf", role=WeatherSourceRole.FORECAST)],
            cycle,
        )

        assert result.forecasts == {}
        assert result.unavailable == {
            GatewayHruName("hru_dhm_west_v001"): frozenset(
                {"snow_depth", "snowmelt", "swe"}
            )
        }

    def test_two_hru_leakage_guard(self) -> None:
        # HRU A loses "hs" (source_data_missing); HRU B is fully healthy. Only A's
        # station loses snow_depth; B's station stores+would-assemble it normally —
        # proves availability is per-hru, never a global cross-HRU set (Plan 145
        # 2c major finding).
        cycle = ensure_utc(datetime(2026, 1, 1, 0, tzinfo=UTC))
        snow = _TwoHruSnow(unavailable_hru="hru_a", unavailable_variable="hs")
        resolver = _MapResolver(
            {
                _SID_A: _ref(_SID_A, _POLY_A, hru="hru_a"),
                _SID_B: _ref(_SID_B, _POLY_B, hru="hru_b"),
            }
        )
        adapter = RecapGatewayForecastAdapter(
            client=_Client(_GoodEcmwf(), snow),  # type: ignore[arg-type]
            resolver=resolver,  # type: ignore[arg-type]
        )

        result = adapter.fetch_snow_forecast(
            [
                _ws(_SID_A, nwp_source="ifs_ecmwf", role=WeatherSourceRole.FORECAST),
                _ws(_SID_B, nwp_source="ifs_ecmwf", role=WeatherSourceRole.FORECAST),
            ],
            cycle,
        )

        assert result.unavailable == {
            GatewayHruName("hru_a"): frozenset({"snow_depth"})
        }
        a_params = set(result.forecasts[_SID_A].values["parameter"].unique().to_list())
        b_params = set(result.forecasts[_SID_B].values["parameter"].unique().to_list())
        assert a_params == {"snowmelt", "swe"}
        assert b_params == {"snow_depth", "snowmelt", "swe"}

    def test_non_snow_specific_error_propagates_uncontained(self) -> None:
        # A code NOT in the snow-unavailable set (config/unexpected bug) must NOT be
        # silently contained as an "unavailable" gap — it propagates loud, matching
        # the "IFS/reanalysis error mapping unchanged" regression (D3.2c).
        cycle = ensure_utc(datetime(2026, 1, 1, 0, tzinfo=UTC))
        snow = _PartiallyUnavailableSnow(
            _wide_df([_POLY_A], value=9.0),
            unavailable_variables={"hs"},
            code="unexpected_client_bug",
        )
        adapter = RecapGatewayForecastAdapter(
            client=_Client(_GoodEcmwf(), snow),  # type: ignore[arg-type]
            resolver=_GoodResolver(),  # type: ignore[arg-type]
        )

        with pytest.raises(AdapterError) as exc_info:
            adapter.fetch_snow_forecast(
                [_ws(_SID, nwp_source="ifs_ecmwf", role=WeatherSourceRole.FORECAST)],
                cycle,
            )
        assert not isinstance(exc_info.value, RecapSnowUnavailableError)


class _MixedSourceSnow:
    """Reanalysis snow fake returning a frame with per-row mixed provenance."""

    def __init__(self, df: pd.DataFrame) -> None:
        self.calls: list[dict[str, object]] = []
        self._df = df

    def reanalysis(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        return self._df


def _mixed_source_snow_frame() -> pd.DataFrame:
    index = pd.DatetimeIndex(
        [
            pd.Timestamp("2026-01-01T00:00:00Z"),
            pd.Timestamp("2026-01-01T12:00:00Z"),
        ],
        name="time",
    )
    df = pd.DataFrame({_POLY_A: [1.0, 2.0]}, index=index)
    # Row 0 is observed (era5-land-analogous reanalysis); row 1 is
    # forecast-fill leaking into what should be a pure reanalysis window.
    df["source"] = ["jsnow_reanalysis", "jsnow_forecast"]
    df["source_run"] = [_SOURCE_RUN, _SOURCE_RUN]
    return df


class TestReanalysisLeakageGuard:
    """Plan 082 Task 3B item 3: forecast-fill rows must never leak into
    reanalysis (training-history) admission."""

    def test_jsnow_forecast_row_dropped_jsnow_reanalysis_admitted(self) -> None:
        snow = _MixedSourceSnow(_mixed_source_snow_frame())
        adapter = _reanalysis_adapter(
            _GoodEcmwf(), snow, _MapResolver({_SID_A: _ref(_SID_A, _POLY_A)})
        )

        rows = adapter.fetch_reanalysis(
            [_ws(_SID_A, nwp_source="era5_land", role=WeatherSourceRole.REANALYSIS)],
            _START,
            _END,
            ["snow_depth"],
        )

        assert len(rows) == 1
        assert rows[0].value == 1.0

    def test_era5_land_admitted_ifs_forecast_fill_dropped(self) -> None:
        index = pd.DatetimeIndex(
            [
                pd.Timestamp("2026-01-01T00:00:00Z"),
                pd.Timestamp("2026-01-01T12:00:00Z"),
            ],
            name="time",
        )
        df = pd.DataFrame({_POLY_A: [1.0, 2.0]}, index=index)
        df["source"] = ["era5_land", "ifs"]
        df["source_run"] = [_SOURCE_RUN, _SOURCE_RUN]
        adapter = _era5_source(df)

        rows = adapter.fetch_reanalysis(
            [_ws(_SID_A, nwp_source="era5_land", role=WeatherSourceRole.REANALYSIS)],
            _START,
            _END,
            ["precipitation"],
        )

        assert len(rows) == 1
        assert rows[0].value == pytest.approx(1000.0)  # 1.0 m -> 1000 mm
