"""Acceptance tests for the MeteoSwiss open-data daily reanalysis adapter.

Milestone 071-reanalysis-core:
- criterion 1: ``fetch_reanalysis`` over a deterministic replay fixture
  returns ``list[RawHistoricalForcing]`` with canonical parameter names, the
  correct per-product ``ForcingSource`` tag, and deterministic content-hash
  ``version`` values (identical bytes -> identical version; different bytes ->
  different version).
- criterion 2 (adapter half): basin-averaged rows are one-per-logical-key and
  reanalysis rows carry ``member_id=None``. The real ``ExactExtractGridExtractor``
  grid-math half is in ``TestReanalysisBasinAveraging`` below.
- criterion 3 (conformance half): every emitted row conforms to the shared
  canonical forcing-schema contract.

LOCKED acceptance tests authored ahead of implementation. The replay is wired
through ``httpx.MockTransport`` (the house pattern, mirroring
``test_meteoswiss_nwp.py``). A faithful echo ``GridExtractor`` double stands in
for ``exactextract`` so the adapter's STAC -> download -> content-hash ->
canonicalisation -> ``RawHistoricalForcing`` assembly is what is under test, not
projection arithmetic. Do not weaken these to make implementation easier.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import numpy as np
import pandas as pd
import polars as pl
import pytest
import xarray as xr
from shapely.geometry import box

from sapphire_flow.adapters.forecast_interface import fi_unit_to_canonical
from sapphire_flow.adapters.meteoswiss_open_data_reanalysis import (
    _PRODUCT_REGISTRY,  # pyright: ignore[reportPrivateUsage]
    MeteoSwissOpenDataReanalysisAdapter,
)
from sapphire_flow.exceptions import AdapterError, ConfigurationError
from sapphire_flow.preprocessing.exact_extract_grid_extractor import (
    ExactExtractGridExtractor,
)
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.forcing_schema import CANONICAL_FORCING_SCHEMA
from sapphire_flow.types.forcing_sources import ForcingSource
from sapphire_flow.types.historical_forcing import RawHistoricalForcing
from sapphire_flow.types.ids import BasinId, StationId
from sapphire_flow.types.station import StationWeatherSource
from sapphire_flow.types.weather import BasinAverageForecast

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.types.datetime import UtcDatetime

_STAC_BASE = "https://data.geo.admin.ch/api/stac/v1"
_STAC_COLLECTION = "ch.meteoschweiz.ogd-surface-derived-grid"

_DAYS: tuple[str, ...] = ("2026-04-10", "2026-04-11")
# NOTE (Plan 115b1 §1F): "precipitation" is deliberately EXCLUDED from this
# fixture set — once RhiresD is registered alongside RprelimD, precipitation
# maps to TWO products and the parameter-keyed fetch_reanalysis path used by
# this whole fixture family fails closed (see TestFailClosedPrecipitation
# below). relative_sunshine_duration (SrelD, single product) takes its slot
# so the "four canonical parameters over fetch_reanalysis" shape is preserved.
_CANONICAL_PARAMETERS = {
    "relative_sunshine_duration",
    "temperature",
    "temperature_min",
    "temperature_max",
}

# (raw NetCDF variable name, product token, canonical parameter, ForcingSource,
#  fill value). The raw variable name is deliberately NOT canonical so the
#  adapter must apply its product -> canonical-parameter mapping.
_PRODUCTS: list[tuple[str, str, str, ForcingSource, float]] = [
    (
        "SrelD",
        "sreld",
        "relative_sunshine_duration",
        ForcingSource.METEOSWISS_SRELD,
        10.0,
    ),
    ("TabsD", "tabsd", "temperature", ForcingSource.METEOSWISS_TABSD, 12.0),
    ("TminD", "tmind", "temperature_min", ForcingSource.METEOSWISS_TMIND, 5.0),
    ("TmaxD", "tmaxd", "temperature_max", ForcingSource.METEOSWISS_TMAXD, 18.0),
]


def _netcdf_bytes(raw_name: str, day: str, fill: float) -> bytes:
    ds = xr.Dataset(
        {
            raw_name: xr.DataArray(
                np.full((1, 6, 6), fill, dtype="float32"),
                dims=["valid_time", "latitude", "longitude"],
                coords={
                    "valid_time": [np.datetime64(f"{day}T00:00:00")],
                    "latitude": np.linspace(45.0, 49.0, 6),
                    "longitude": np.linspace(5.0, 11.0, 6),
                },
            )
        }
    )
    with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as fh:
        path = Path(fh.name)
    try:
        ds.to_netcdf(path)
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)


def _byte_map(*, sreld_fill: float = 10.0) -> dict[tuple[str, str], bytes]:
    out: dict[tuple[str, str], bytes] = {}
    for raw, token, _param, _src, fill in _PRODUCTS:
        eff_fill = sreld_fill if token == "sreld" else fill
        for day in _DAYS:
            out[(token, day)] = _netcdf_bytes(raw, day, eff_fill)
    return out


def _netcdf_bytes_with_ancillary(raw_name: str, day: str, fill: float) -> bytes:
    """Like ``_netcdf_bytes`` but additionally carries CF ancillary data
    variables (a grid-mapping/CRS scalar and a cell-boundary ``lat_bnds`` var)
    alongside the product var — exactly what real MeteoSwiss NetCDFs contain."""
    ds = xr.Dataset(
        {
            raw_name: xr.DataArray(
                np.full((1, 6, 6), fill, dtype="float32"),
                dims=["valid_time", "latitude", "longitude"],
                coords={
                    "valid_time": [np.datetime64(f"{day}T00:00:00")],
                    "latitude": np.linspace(45.0, 49.0, 6),
                    "longitude": np.linspace(5.0, 11.0, 6),
                },
            ),
            "swiss_lv95_coordinates": xr.DataArray(np.int32(0)),
            "lat_bnds": xr.DataArray(
                np.zeros((6, 2), dtype="float32"),
                dims=["latitude", "bnds"],
            ),
        }
    )
    with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as fh:
        path = Path(fh.name)
    try:
        ds.to_netcdf(path)
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)


def _byte_map_with_ancillary() -> dict[tuple[str, str], bytes]:
    out: dict[tuple[str, str], bytes] = {}
    for raw, token, _param, _src, fill in _PRODUCTS:
        for day in _DAYS:
            out[(token, day)] = _netcdf_bytes_with_ancillary(raw, day, fill)
    return out


def _feature(day: str) -> dict[str, object]:
    assets = {
        f"{raw}_ch.swiss.lv95_{day}": {
            "href": f"https://dummy/assets/{token}_{day}.swiss.lv95.nc",
            "type": "application/x-netcdf",
        }
        for raw, token, _p, _s, _f in _PRODUCTS
    }
    return {
        "id": f"{day.replace('-', '')}-ch",
        "properties": {
            "datetime": f"{day}T00:00:00Z",
            "updated": f"{day}T05:30:00Z",
            "expires": "2026-12-31T00:00:00Z",
        },
        "assets": assets,
        "links": [],
    }


def _make_handler(
    byte_map: dict[tuple[str, str], bytes],
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/assets/" in url:
            for (token, day), payload in byte_map.items():
                if f"{token}_{day}" in url:
                    return httpx.Response(
                        200,
                        content=payload,
                        headers={"content-type": "application/x-netcdf"},
                    )
            return httpx.Response(404, json={"error": "not found"})
        if "/collections/" in url:
            found = sorted(set(re.findall(r"\d{4}-\d{2}-\d{2}", url)))
            days = [d for d in (found or _DAYS) if d in _DAYS]
            return httpx.Response(
                200, json={"features": [_feature(d) for d in days], "links": []}
            )
        return httpx.Response(200, json={"features": [], "links": []})

    return handler


class _EchoExtractor:
    """A faithful ``GridExtractor`` double: echoes the grid's data variables and
    valid_times as basin-averaged rows. It performs no projection arithmetic, so
    the adapter's own canonicalisation/assembly is what gets exercised. It emits
    ``member_id=0`` (as the real extractor does for a no-member grid) so the
    adapter is forced to convert it to ``None`` for reanalysis output.
    """

    def extract(
        self,
        grid: xr.Dataset,
        configs: list[StationWeatherSource],
        basins: dict[StationId, Basin],
        cycle_time: UtcDatetime,
        nwp_source: str,
    ) -> dict[StationId, BasinAverageForecast]:
        params = list(grid.data_vars)
        valid_times: list[datetime] = []
        for raw_vt in grid["valid_time"].values:
            ts = pd.Timestamp(raw_vt)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            valid_times.append(ensure_utc(ts.to_pydatetime()))
        out: dict[StationId, BasinAverageForecast] = {}
        for cfg in configs:
            rows = [
                {"valid_time": vt, "parameter": p, "member_id": 0, "value": 1.0}
                for p in params
                for vt in valid_times
            ]
            df = pl.DataFrame(
                rows,
                schema={
                    "valid_time": pl.Datetime("us", "UTC"),
                    "parameter": pl.Utf8,
                    "member_id": pl.Int64,
                    "value": pl.Float64,
                },
            )
            out[cfg.station_id] = BasinAverageForecast(
                nwp_source=nwp_source, cycle_time=cycle_time, values=df
            )
        return out


def _make_config(station_id: StationId) -> StationWeatherSource:
    return StationWeatherSource(
        station_id=station_id,
        nwp_source="meteoswiss_open_data_reanalysis",
        extraction_type=SpatialRepresentation.BASIN_AVERAGE,
        status=WeatherSourceStatus.ACTIVE,
        role=WeatherSourceRole.REANALYSIS,
    )


def _make_basin(station_id: StationId) -> Basin:
    return Basin(
        id=BasinId(uuid.uuid4()),
        code="test_basin",
        name="Test Basin",
        geometry=box(6.0, 46.0, 10.0, 48.0),
        area_km2=100.0,
        attributes=None,
        band_geometries=None,
        created_at=ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)),
        network="test",
    )


def _clock() -> UtcDatetime:
    return ensure_utc(datetime(2026, 4, 12, 6, 0, tzinfo=UTC))


def _make_adapter(
    transport: httpx.MockTransport,
    basins: dict[StationId, Basin],
) -> MeteoSwissOpenDataReanalysisAdapter:
    client = httpx.Client(transport=transport, base_url="https://dummy")
    return MeteoSwissOpenDataReanalysisAdapter(
        stac_base_url=_STAC_BASE,
        stac_collection=_STAC_COLLECTION,
        http_client=client,
        extractor=_EchoExtractor(),
        basins=basins,
        clock=_clock,
    )


def _fetch(
    byte_map: dict[tuple[str, str], bytes],
) -> tuple[StationId, list[RawHistoricalForcing]]:
    sid = StationId(uuid.uuid4())
    adapter = _make_adapter(
        httpx.MockTransport(_make_handler(byte_map)), {sid: _make_basin(sid)}
    )
    rows = adapter.fetch_reanalysis(
        [_make_config(sid)],
        ensure_utc(datetime(2026, 4, 10, tzinfo=UTC)),
        ensure_utc(datetime(2026, 4, 12, tzinfo=UTC)),
        sorted(_CANONICAL_PARAMETERS),
    )
    return sid, rows


def _version_index(rows: list[RawHistoricalForcing]) -> dict[tuple[str, str, str], str]:
    return {(r.source, r.parameter, r.valid_time.isoformat()): r.version for r in rows}


class TestFetchReanalysisOutputContract:
    def test_returns_raw_historical_forcing_rows(self) -> None:
        _sid, rows = _fetch(_byte_map())
        assert rows
        assert all(isinstance(r, RawHistoricalForcing) for r in rows)

    def test_emits_only_canonical_parameter_names(self) -> None:
        _sid, rows = _fetch(_byte_map())
        assert {r.parameter for r in rows} == _CANONICAL_PARAMETERS

    def test_per_product_source_tag_mapping(self) -> None:
        _sid, rows = _fetch(_byte_map())
        expected = {(src.value, param) for _raw, _tok, param, src, _fill in _PRODUCTS}
        assert {(r.source, r.parameter) for r in rows} == expected

    def test_reanalysis_rows_have_no_member_id(self) -> None:
        _sid, rows = _fetch(_byte_map())
        assert all(r.member_id is None for r in rows)

    def test_rows_are_basin_average(self) -> None:
        _sid, rows = _fetch(_byte_map())
        assert all(r.spatial_type == SpatialRepresentation.BASIN_AVERAGE for r in rows)


class TestContentHashVersionDeterminism:
    def test_identical_bytes_yield_identical_versions(self) -> None:
        # Two independent runs over the *same* fixture bytes must produce the
        # same version for every logical row (content-hash, not wall-clock).
        _s1, rows1 = _fetch(_byte_map())
        _s2, rows2 = _fetch(_byte_map())
        idx1 = _version_index(rows1)
        idx2 = _version_index(rows2)
        assert idx1.keys() == idx2.keys()
        assert idx1 == idx2

    def test_version_is_nonempty_string(self) -> None:
        _sid, rows = _fetch(_byte_map())
        assert all(isinstance(r.version, str) and r.version for r in rows)

    def test_changed_bytes_change_only_that_products_version(self) -> None:
        # Mutating only the sunshine product's bytes must change the sunshine
        # rows' versions and leave the other products untouched
        # (content-addressed per asset).
        baseline = _version_index(_fetch(_byte_map())[1])
        mutated = _version_index(_fetch(_byte_map(sreld_fill=99.0))[1])

        sreld_src = ForcingSource.METEOSWISS_SRELD.value
        for key, version in baseline.items():
            source = key[0]
            if source == sreld_src:
                assert mutated[key] != version
            else:
                assert mutated[key] == version


class TestBasinAveragedRowUniqueness:
    def test_exactly_one_value_per_station_validtime_parameter(self) -> None:
        sid, rows = _fetch(_byte_map())
        keys = [(r.station_id, r.valid_time, r.parameter) for r in rows]
        assert len(keys) == len(set(keys))
        # 2 daily valid_times x 4 canonical parameters, single station.
        assert len(rows) == len(_DAYS) * len(_CANONICAL_PARAMETERS)
        assert all(r.station_id == sid for r in rows)

    def test_valid_times_are_distinct_days(self) -> None:
        _sid, rows = _fetch(_byte_map())
        days = {r.valid_time.date() for r in rows}
        assert len(days) == len(_DAYS)


class TestSchemaConformance:
    def test_every_row_conforms_to_canonical_schema(self) -> None:
        _sid, rows = _fetch(_byte_map())
        for r in rows:
            assert r.parameter in CANONICAL_FORCING_SCHEMA.parameters
            assert r.spatial_type == CANONICAL_FORCING_SCHEMA.spatial_representation

    def test_emitted_parameters_carry_canonical_units(self) -> None:
        _sid, rows = _fetch(_byte_map())
        present = {r.parameter for r in rows}
        for param in present:
            canonical = fi_unit_to_canonical(CANONICAL_FORCING_SCHEMA.units[param])
            assert canonical in {"mm", "°C", "%"}


class TestFetchReanalysisErrorPaths:
    def _fetch_with_handler(
        self, handler: Callable[[httpx.Request], httpx.Response]
    ) -> list[RawHistoricalForcing]:
        sid = StationId(uuid.uuid4())
        adapter = _make_adapter(httpx.MockTransport(handler), {sid: _make_basin(sid)})
        return adapter.fetch_reanalysis(
            [_make_config(sid)],
            ensure_utc(datetime(2026, 4, 10, tzinfo=UTC)),
            ensure_utc(datetime(2026, 4, 12, tzinfo=UTC)),
            sorted(_CANONICAL_PARAMETERS),
        )

    def test_stac_server_error_raises_adapter_error(self) -> None:
        # STAC search endpoint 5xx: the whole pipeline must fail loudly.
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "upstream failure"})

        with pytest.raises(AdapterError):
            self._fetch_with_handler(handler)

    def test_partial_asset_download_failure_raises_adapter_error(self) -> None:
        # STAC search succeeds and three of four product assets download, but
        # the sunshine asset 404s. The adapter must raise rather than
        # silently emit a forcing stream missing the sunshine rows.
        partial = {k: v for k, v in _byte_map().items() if k[0] != "sreld"}
        with pytest.raises(AdapterError):
            self._fetch_with_handler(_make_handler(partial))

    def test_asset_server_error_raises_adapter_error(self) -> None:
        # An asset download 5xx (transient upstream fault) must also surface as
        # AdapterError, not a partial result.
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "/assets/" in url:
                return httpx.Response(500, json={"error": "asset upstream failure"})
            if "/collections/" in url:
                days = [d for d in _DAYS]
                return httpx.Response(
                    200, json={"features": [_feature(d) for d in days], "links": []}
                )
            return httpx.Response(200, json={"features": [], "links": []})

        with pytest.raises(AdapterError):
            self._fetch_with_handler(handler)

    def test_empty_stac_result_yields_no_rows(self) -> None:
        # STAC returns HTTP 200 with no features for the requested range (a gap
        # in the archive). The documented contract is an empty forcing stream —
        # no rows, no exception, no fabricated data.
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"features": [], "links": []})

        assert self._fetch_with_handler(handler) == []


class TestNonMidnightRangeDaySelection:
    """A range whose endpoints are NOT midnight must select exactly the days
    whose midnight ``valid_time`` falls inside the half-open ``[start, end)``
    window. Daily MeteoSwiss rows are valid at midnight, so the boundary days'
    midnights (before ``start`` / before ``end``) must be excluded/included by
    the actual valid instant — not by ``start.date()``/``end.date()``.

    Regression for the day-loop bug: ``[04-10T06:00, 04-12T06:00)`` must yield
    valid_times ``{04-11T00:00, 04-12T00:00}`` (NOT ``{04-10T00:00, ...}``).
    The pre-fix loop ``day < end.date()`` returned ``{04-10, 04-11}``.
    """

    _DAYS_FIXTURE: tuple[str, ...] = ("2026-04-10", "2026-04-11", "2026-04-12")

    def _handler(self) -> Callable[[httpx.Request], httpx.Response]:
        # temperature-only fixture across three candidate days (a single-
        # product parameter — unaffected by the precipitation fail-closed
        # guard, Plan 115b1 §1F).
        byte_map = {
            day: _netcdf_bytes("TabsD", day, 10.0) for day in self._DAYS_FIXTURE
        }

        def _feature_one(day: str) -> dict[str, object]:
            return {
                "id": f"{day.replace('-', '')}-ch",
                "properties": {
                    "datetime": f"{day}T00:00:00Z",
                    "updated": f"{day}T05:30:00Z",
                },
                "assets": {
                    f"TabsD_ch.swiss.lv95_{day}": {
                        "href": f"https://dummy/assets/tabsd_{day}.swiss.lv95.nc",
                        "type": "application/x-netcdf",
                    }
                },
                "links": [],
            }

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "/assets/" in url:
                for day, payload in byte_map.items():
                    if f"tabsd_{day}" in url:
                        return httpx.Response(
                            200,
                            content=payload,
                            headers={"content-type": "application/x-netcdf"},
                        )
                return httpx.Response(404, json={"error": "not found"})
            if "/collections/" in url:
                found = sorted(set(re.findall(r"\d{4}-\d{2}-\d{2}", url)))
                days = [d for d in found if d in byte_map]
                return httpx.Response(
                    200,
                    json={"features": [_feature_one(d) for d in days], "links": []},
                )
            return httpx.Response(200, json={"features": [], "links": []})

        return handler

    def test_non_midnight_range_selects_interior_midnights(self) -> None:
        sid = StationId(uuid.uuid4())
        adapter = _make_adapter(
            httpx.MockTransport(self._handler()), {sid: _make_basin(sid)}
        )
        rows = adapter.fetch_reanalysis(
            [_make_config(sid)],
            ensure_utc(datetime(2026, 4, 10, 6, 0, tzinfo=UTC)),
            ensure_utc(datetime(2026, 4, 12, 6, 0, tzinfo=UTC)),
            ["temperature"],
        )

        valid_times = {r.valid_time for r in rows}
        assert valid_times == {
            datetime(2026, 4, 11, tzinfo=UTC),
            datetime(2026, 4, 12, tzinfo=UTC),
        }
        # The pre-start day's midnight must NOT leak in.
        assert datetime(2026, 4, 10, tzinfo=UTC) not in valid_times


class TestReanalysisBasinAveraging:
    """Criterion 2, grid-math half: the real ``ExactExtractGridExtractor`` over a
    regular-lat/lon basin fixture with no ensemble dimension (reanalysis) yields
    exactly one value per (station_id, valid_time, parameter)."""

    def _grid(self) -> xr.Dataset:
        valid_times = [datetime(2026, 4, d, tzinfo=UTC) for d in (10, 11)]
        coords = {
            "valid_time": valid_times,
            "latitude": np.linspace(46.0, 48.0, 5),
            "longitude": np.linspace(6.0, 10.0, 5),
        }
        return xr.Dataset(
            {
                "precipitation": xr.DataArray(
                    np.full((2, 5, 5), 10.0, dtype="float32"),
                    dims=["valid_time", "latitude", "longitude"],
                    coords=coords,
                ),
                "temperature": xr.DataArray(
                    np.full((2, 5, 5), 20.0, dtype="float32"),
                    dims=["valid_time", "latitude", "longitude"],
                    coords=coords,
                ),
            }
        )

    def test_one_value_per_validtime_parameter(self) -> None:
        sid = StationId(uuid.uuid4())
        result = ExactExtractGridExtractor().extract(
            self._grid(),
            [_make_config(sid)],
            {sid: _make_basin(sid)},
            ensure_utc(datetime(2026, 4, 10, tzinfo=UTC)),
            "meteoswiss_open_data_reanalysis",
        )

        df = result[sid].values
        keys = list(
            zip(
                df["valid_time"].to_list(),
                df["parameter"].to_list(),
                strict=True,
            )
        )
        assert len(keys) == len(set(keys))
        # 2 daily valid_times x 2 parameters, no ensemble fan-out.
        assert len(keys) == 4
        assert df["member_id"].n_unique() == 1

        # DAILY resolution (criterion 2): the extractor preserves exactly the
        # two input-grid daily timestamps — 24 h apart, no hourly sub-sampling.
        # A sub-daily extractor that deduplicated to 4 unique rows would still
        # pass the counts above but would fail this exact-timestamp pin.
        valid_times = sorted(set(df["valid_time"].to_list()))
        assert valid_times == [
            datetime(2026, 4, 10, tzinfo=UTC),
            datetime(2026, 4, 11, tzinfo=UTC),
        ]


class TestFetchReanalysisConfigFiltering:
    def test_only_matching_active_configs_yield_rows(self) -> None:
        # Service callers pass through ALL station weather-source configs.
        # Only the reanalysis-active station must yield rows — not a station
        # bound to a different source (icon), nor an inactive reanalysis one.
        match_sid = StationId(uuid.uuid4())
        other_source_sid = StationId(uuid.uuid4())
        inactive_sid = StationId(uuid.uuid4())
        basins = {
            match_sid: _make_basin(match_sid),
            other_source_sid: _make_basin(other_source_sid),
            inactive_sid: _make_basin(inactive_sid),
        }
        adapter = _make_adapter(httpx.MockTransport(_make_handler(_byte_map())), basins)
        configs = [
            _make_config(match_sid),
            StationWeatherSource(
                station_id=other_source_sid,
                nwp_source="icon_ch2_eps",
                extraction_type=SpatialRepresentation.BASIN_AVERAGE,
                status=WeatherSourceStatus.ACTIVE,
                role=WeatherSourceRole.FORECAST,
            ),
            StationWeatherSource(
                station_id=inactive_sid,
                nwp_source="meteoswiss_open_data_reanalysis",
                extraction_type=SpatialRepresentation.BASIN_AVERAGE,
                status=WeatherSourceStatus.INACTIVE,
                role=WeatherSourceRole.REANALYSIS,
            ),
        ]
        rows = adapter.fetch_reanalysis(
            configs,
            ensure_utc(datetime(2026, 4, 10, tzinfo=UTC)),
            ensure_utc(datetime(2026, 4, 12, tzinfo=UTC)),
            sorted(_CANONICAL_PARAMETERS),
        )
        assert rows
        assert {r.station_id for r in rows} == {match_sid}

    def test_excludes_forecast_binding_sharing_the_reanalysis_source_name(
        self,
    ) -> None:
        # Defense-in-depth (§7): a binding whose nwp_source string equals THIS
        # adapter's NWP_SOURCE, is ACTIVE, and requests BASIN_AVERAGE — every
        # other filter matches — must still be excluded when its role is
        # FORECAST. Proves the guard is role-based, not name/status/
        # extraction_type alone. Soundness: fails against a `matching` filter
        # that omits the `c.role is WeatherSourceRole.REANALYSIS` clause.
        sid = StationId(uuid.uuid4())

        def _no_call_handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("must not download when no config matches")

        adapter = _make_adapter(
            httpx.MockTransport(_no_call_handler), {sid: _make_basin(sid)}
        )
        configs = [
            StationWeatherSource(
                station_id=sid,
                nwp_source="meteoswiss_open_data_reanalysis",
                extraction_type=SpatialRepresentation.BASIN_AVERAGE,
                status=WeatherSourceStatus.ACTIVE,
                role=WeatherSourceRole.FORECAST,
            ),
        ]
        rows = adapter.fetch_reanalysis(
            configs,
            ensure_utc(datetime(2026, 4, 10, tzinfo=UTC)),
            ensure_utc(datetime(2026, 4, 12, tzinfo=UTC)),
            sorted(_CANONICAL_PARAMETERS),
        )
        assert rows == []

    def test_no_matching_configs_returns_empty_without_download(self) -> None:
        sid = StationId(uuid.uuid4())

        def _no_call_handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("must not download when no config matches")

        adapter = _make_adapter(
            httpx.MockTransport(_no_call_handler), {sid: _make_basin(sid)}
        )
        configs = [
            StationWeatherSource(
                station_id=sid,
                nwp_source="icon_ch2_eps",
                extraction_type=SpatialRepresentation.BASIN_AVERAGE,
                status=WeatherSourceStatus.ACTIVE,
                role=WeatherSourceRole.FORECAST,
            ),
        ]
        rows = adapter.fetch_reanalysis(
            configs,
            ensure_utc(datetime(2026, 4, 10, tzinfo=UTC)),
            ensure_utc(datetime(2026, 4, 12, tzinfo=UTC)),
            sorted(_CANONICAL_PARAMETERS),
        )
        assert rows == []


class TestAncillaryVariableDropping:
    """Real MeteoSwiss NetCDFs carry CF ancillary data variables (grid-mapping
    scalar, ``lat_bnds``, ...) alongside the product var. The adapter must reduce
    the dataset to the single product variable before extraction so the extractor
    never emits ancillary-attributed rows."""

    def test_ancillary_vars_not_emitted_as_forcing(self) -> None:
        _sid, rows = _fetch(_byte_map_with_ancillary())
        # Only canonical product parameters — no rows for swiss_lv95_coordinates
        # or lat_bnds (which the echo extractor would otherwise emit).
        assert {r.parameter for r in rows} == _CANONICAL_PARAMETERS
        # Row count unchanged from the clean-NetCDF case.
        assert len(rows) == len(_DAYS) * len(_CANONICAL_PARAMETERS)

    def test_missing_product_var_raises(self) -> None:
        # A NetCDF that lacks the expected raw product variable entirely must
        # fail loudly rather than silently emit nothing (or mislabel a stray var).
        bad = {
            (token, day): _netcdf_bytes("WrongVar", day, 1.0)
            for _raw, token, _param, _src, _fill in _PRODUCTS
            for day in _DAYS
        }
        sid = StationId(uuid.uuid4())
        adapter = _make_adapter(
            httpx.MockTransport(_make_handler(bad)), {sid: _make_basin(sid)}
        )
        with pytest.raises(AdapterError):
            adapter.fetch_reanalysis(
                [_make_config(sid)],
                ensure_utc(datetime(2026, 4, 10, tzinfo=UTC)),
                ensure_utc(datetime(2026, 4, 12, tzinfo=UTC)),
                sorted(_CANONICAL_PARAMETERS),
            )


class TestStacPaginationFollowsNext:
    """The per-day STAC items query must follow ``links[].rel == "next"`` and
    accumulate features across pages before selecting the latest-``updated`` one.
    """

    def test_selects_item_from_second_page(self) -> None:
        day = "2026-04-10"
        page1_bytes = _netcdf_bytes("TabsD", day, 1.0)
        page2_bytes = _netcdf_bytes("TabsD", day, 2.0)
        page2_url = "https://dummy/stac/collections/page2-items"

        def _feat(href_token: str, updated: str) -> dict[str, object]:
            return {
                "id": href_token,
                "properties": {
                    "datetime": f"{day}T00:00:00Z",
                    "updated": updated,
                },
                "assets": {
                    f"TabsD_ch.swiss.lv95_{day}": {
                        "href": f"https://dummy/assets/{href_token}.nc",
                        "type": "application/x-netcdf",
                    }
                },
                "links": [],
            }

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "/assets/" in url:
                if "page1tok" in url:
                    return httpx.Response(
                        200,
                        content=page1_bytes,
                        headers={"content-type": "application/x-netcdf"},
                    )
                if "page2tok" in url:
                    return httpx.Response(
                        200,
                        content=page2_bytes,
                        headers={"content-type": "application/x-netcdf"},
                    )
                return httpx.Response(404, json={"error": "not found"})
            if "page2" in url:
                # Page 2: the newer item, no further next link.
                return httpx.Response(
                    200,
                    json={
                        "features": [_feat("page2tok", f"{day}T09:00:00Z")],
                        "links": [],
                    },
                )
            if "/collections/" in url:
                # Page 1: an older item plus a next link to page 2.
                return httpx.Response(
                    200,
                    json={
                        "features": [_feat("page1tok", f"{day}T05:00:00Z")],
                        "links": [{"rel": "next", "href": page2_url}],
                    },
                )
            return httpx.Response(200, json={"features": [], "links": []})

        sid = StationId(uuid.uuid4())
        adapter = _make_adapter(httpx.MockTransport(handler), {sid: _make_basin(sid)})
        rows = adapter.fetch_reanalysis(
            [_make_config(sid)],
            ensure_utc(datetime(2026, 4, 10, tzinfo=UTC)),
            ensure_utc(datetime(2026, 4, 11, tzinfo=UTC)),
            ["temperature"],
        )

        assert rows
        # The selected feature's asset is page 2's — proving pagination was
        # followed (page 2's item is newer than page 1's).
        expected_version = hashlib.sha256(page2_bytes).hexdigest()[:16]
        assert all(r.version == expected_version for r in rows)


class TestFetchReanalysisRequestGuards:
    def test_non_basin_extraction_config_excluded(self) -> None:
        # An active reanalysis config that requests POINT (not basin-average)
        # must not get basin-average rows — and must not trigger a download.
        sid = StationId(uuid.uuid4())

        def _no_call(request: httpx.Request) -> httpx.Response:
            raise AssertionError("must not download for a non-basin config")

        adapter = _make_adapter(httpx.MockTransport(_no_call), {sid: _make_basin(sid)})
        rows = adapter.fetch_reanalysis(
            [
                StationWeatherSource(
                    station_id=sid,
                    nwp_source="meteoswiss_open_data_reanalysis",
                    extraction_type=SpatialRepresentation.POINT,
                    status=WeatherSourceStatus.ACTIVE,
                    role=WeatherSourceRole.REANALYSIS,
                )
            ],
            ensure_utc(datetime(2026, 4, 10, tzinfo=UTC)),
            ensure_utc(datetime(2026, 4, 12, tzinfo=UTC)),
            sorted(_CANONICAL_PARAMETERS),
        )
        assert rows == []

    def test_unsupported_parameters_return_empty_without_download(self) -> None:
        sid = StationId(uuid.uuid4())

        def _no_call(request: httpx.Request) -> httpx.Response:
            raise AssertionError("must not download when no product matches")

        adapter = _make_adapter(httpx.MockTransport(_no_call), {sid: _make_basin(sid)})
        rows = adapter.fetch_reanalysis(
            [_make_config(sid)],
            ensure_utc(datetime(2026, 4, 10, tzinfo=UTC)),
            ensure_utc(datetime(2026, 4, 12, tzinfo=UTC)),
            ["unsupported_param"],
        )
        assert rows == []


# ---------------------------------------------------------------------------
# Plan 115b1 §1F — fail-closed precipitation on the parameter-keyed path
# ---------------------------------------------------------------------------


class TestFailClosedPrecipitation:
    """Once RhiresD is registered alongside RprelimD, "precipitation" maps to
    TWO products — the parameter-keyed ``fetch_reanalysis`` path cannot
    disambiguate which one the caller wants, and must fail closed rather than
    silently pick one. Precipitation is served ONLY via ``fetch_products``.
    """

    def test_precipitation_alone_raises_configuration_error(self) -> None:
        sid = StationId(uuid.uuid4())

        def _no_call(request: httpx.Request) -> httpx.Response:
            raise AssertionError("must not download once the guard raises")

        adapter = _make_adapter(httpx.MockTransport(_no_call), {sid: _make_basin(sid)})
        with pytest.raises(ConfigurationError, match="precipitation"):
            adapter.fetch_reanalysis(
                [_make_config(sid)],
                ensure_utc(datetime(2026, 4, 10, tzinfo=UTC)),
                ensure_utc(datetime(2026, 4, 12, tzinfo=UTC)),
                ["precipitation"],
            )

    def test_precipitation_mixed_with_safe_parameters_still_raises(self) -> None:
        # The guard must trip even when precipitation is only ONE of several
        # requested parameters — a partial silent success would be worse.
        sid = StationId(uuid.uuid4())

        def _no_call(request: httpx.Request) -> httpx.Response:
            raise AssertionError("must not download once the guard raises")

        adapter = _make_adapter(httpx.MockTransport(_no_call), {sid: _make_basin(sid)})
        with pytest.raises(ConfigurationError, match="precipitation"):
            adapter.fetch_reanalysis(
                [_make_config(sid)],
                ensure_utc(datetime(2026, 4, 10, tzinfo=UTC)),
                ensure_utc(datetime(2026, 4, 12, tzinfo=UTC)),
                ["precipitation", "temperature"],
            )

    def test_other_single_product_parameters_unaffected(self) -> None:
        # The guard is scoped to genuinely-ambiguous parameters only — a
        # single-product parameter (temperature) still resolves normally.
        sid = StationId(uuid.uuid4())
        byte_map = {("tabsd", d): _netcdf_bytes("TabsD", d, 7.0) for d in _DAYS}
        adapter = _make_adapter(
            httpx.MockTransport(_make_handler(byte_map)), {sid: _make_basin(sid)}
        )
        rows = adapter.fetch_reanalysis(
            [_make_config(sid)],
            ensure_utc(datetime(2026, 4, 10, tzinfo=UTC)),
            ensure_utc(datetime(2026, 4, 12, tzinfo=UTC)),
            ["temperature"],
        )
        assert rows
        assert all(r.source == ForcingSource.METEOSWISS_TABSD.value for r in rows)


# ---------------------------------------------------------------------------
# Plan 115b1 §1A — adapter product/parameter registry pin (4 -> 5 parameters)
# ---------------------------------------------------------------------------


class TestAdapterProductRegistryPin:
    """Locked contract for the adapter's product registry (Plan 115b1 §1A
    migrated the canonical set 4 -> 5): the adapter serves EXACTLY SIX
    MeteoSwiss products across FIVE canonical parameters — the four
    single-product parameters PLUS precipitation, which is served by TWO
    products (RhiresD + RprelimD, §0a) reachable only via the product-scoped
    ``fetch_products`` path. Soundness: fails RED if a product/source mapping is
    dropped or precipitation is (re)excluded.
    """

    def test_five_canonical_parameters(self) -> None:
        assert {p.parameter for p in _PRODUCT_REGISTRY} == {
            "precipitation",
            "temperature",
            "temperature_min",
            "temperature_max",
            "relative_sunshine_duration",
        }

    def test_all_six_product_source_mappings(self) -> None:
        assert {
            (p.token, p.raw_var, p.parameter, p.source) for p in _PRODUCT_REGISTRY
        } == {
            (
                "rprelimd",
                "RprelimD",
                "precipitation",
                ForcingSource.METEOSWISS_RPRELIMD,
            ),
            ("rhiresd", "RhiresD", "precipitation", ForcingSource.METEOSWISS_RHIRESD),
            ("tabsd", "TabsD", "temperature", ForcingSource.METEOSWISS_TABSD),
            ("tmind", "TminD", "temperature_min", ForcingSource.METEOSWISS_TMIND),
            ("tmaxd", "TmaxD", "temperature_max", ForcingSource.METEOSWISS_TMAXD),
            (
                "sreld",
                "SrelD",
                "relative_sunshine_duration",
                ForcingSource.METEOSWISS_SRELD,
            ),
        }

    def test_precipitation_is_the_only_multi_product_parameter(self) -> None:
        counts = Counter(p.parameter for p in _PRODUCT_REGISTRY)
        assert counts["precipitation"] == 2
        assert all(c == 1 for param, c in counts.items() if param != "precipitation")


# ---------------------------------------------------------------------------
# Plan 115b1 §1F/§0a — writer-side product-scoped fetch (fetch_products)
# ---------------------------------------------------------------------------


def _archive_item(assets: dict[str, dict[str, object]]) -> dict[str, object]:
    return {"id": "archive-ch", "properties": {}, "assets": assets, "links": []}


def _archive_asset(
    token: str, grid: str, year: int, href: str
) -> dict[str, dict[str, object]]:
    """A yearly ARCHIVE-family asset for any product — the filename embeds the
    product token, grid family (ch01h / ch01r), and the full-year date span."""
    key = (
        f"ogd-surface-derived-grid-archive.{token}_{grid}.swiss.lv95_"
        f"{year}0101000000_{year}1231000000.nc"
    )
    return {key: {"href": href, "type": "application/x-netcdf"}}


def _rhiresd_archive_asset(year: int, href: str) -> dict[str, dict[str, object]]:
    return _archive_asset("rhiresd", "ch01h", year, href)


class TestFetchProductsWriterSideScopedFetch:
    """A RHIRESD-scoped ``fetch_products`` call returns ONLY RhiresD rows,
    never RprelimD (and vice versa) — Plan 115b1 §0a/§1F round-5 blocker 2.

    Soundness: fails against the old parameter-only ``fetch_reanalysis`` path
    (which cannot even be asked for "precipitation" post-guard, and which —
    were the guard absent — would nondeterministically return whichever
    product happens first in the registry, not the caller-selected one).
    """

    def test_rhiresd_scoped_call_returns_only_rhiresd_rows(self) -> None:
        sid = StationId(uuid.uuid4())
        year = 2020
        asset_url = "https://dummy/assets/rhiresd_2020.nc"
        rhiresd_bytes = _netcdf_bytes("RhiresD", f"{year}-01-01", 4.0)

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url == asset_url:
                return httpx.Response(
                    200,
                    content=rhiresd_bytes,
                    headers={"content-type": "application/x-netcdf"},
                )
            if url.endswith("/items/archive-ch"):
                return httpx.Response(
                    200,
                    json=_archive_item(_rhiresd_archive_asset(year, asset_url)),
                )
            raise AssertionError(f"unexpected request: {url}")

        adapter = _make_adapter(httpx.MockTransport(handler), {sid: _make_basin(sid)})
        rows = adapter.fetch_products(
            [ForcingSource.METEOSWISS_RHIRESD],
            [_make_config(sid)],
            ensure_utc(datetime(year, 1, 1, tzinfo=UTC)),
            ensure_utc(datetime(year, 1, 2, tzinfo=UTC)),
            ["precipitation"],
        )

        assert rows
        assert {r.source for r in rows} == {ForcingSource.METEOSWISS_RHIRESD.value}
        assert all(r.parameter == "precipitation" for r in rows)

    def test_rprelimd_scoped_call_returns_only_rprelimd_rows(self) -> None:
        # RprelimD is no longer a member of the module-level ``_PRODUCTS``
        # fixture set (§1F note above), so this uses its own day-item handler
        # (mirrors TestNonMidnightRangeDaySelection's pattern) rather than the
        # shared ``_make_handler``/``_feature`` helpers.
        sid = StationId(uuid.uuid4())
        byte_map = {d: _netcdf_bytes("RprelimD", d, 3.0) for d in _DAYS}

        def _feature_rprelimd(day: str) -> dict[str, object]:
            return {
                "id": f"{day.replace('-', '')}-ch",
                "properties": {
                    "datetime": f"{day}T00:00:00Z",
                    "updated": f"{day}T05:30:00Z",
                },
                "assets": {
                    f"RprelimD_ch.swiss.lv95_{day}": {
                        "href": f"https://dummy/assets/rprelimd_{day}.swiss.lv95.nc",
                        "type": "application/x-netcdf",
                    }
                },
                "links": [],
            }

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "/assets/" in url:
                for day, payload in byte_map.items():
                    if f"rprelimd_{day}" in url:
                        return httpx.Response(
                            200,
                            content=payload,
                            headers={"content-type": "application/x-netcdf"},
                        )
                return httpx.Response(404, json={"error": "not found"})
            if "/collections/" in url:
                found = sorted(set(re.findall(r"\d{4}-\d{2}-\d{2}", url)))
                days = [d for d in found if d in byte_map]
                return httpx.Response(
                    200,
                    json={
                        "features": [_feature_rprelimd(d) for d in days],
                        "links": [],
                    },
                )
            return httpx.Response(200, json={"features": [], "links": []})

        adapter = _make_adapter(httpx.MockTransport(handler), {sid: _make_basin(sid)})
        rows = adapter.fetch_products(
            [ForcingSource.METEOSWISS_RPRELIMD],
            [_make_config(sid)],
            ensure_utc(datetime(2026, 4, 10, tzinfo=UTC)),
            ensure_utc(datetime(2026, 4, 12, tzinfo=UTC)),
            ["precipitation"],
        )

        assert rows
        assert {r.source for r in rows} == {ForcingSource.METEOSWISS_RPRELIMD.value}

    def test_unrequested_product_returns_empty_without_download(self) -> None:
        sid = StationId(uuid.uuid4())

        def _no_call(request: httpx.Request) -> httpx.Response:
            raise AssertionError("must not download for a product not requested")

        adapter = _make_adapter(httpx.MockTransport(_no_call), {sid: _make_basin(sid)})
        rows = adapter.fetch_products(
            [ForcingSource.METEOSWISS_RHIRESD],
            [_make_config(sid)],
            ensure_utc(datetime(2026, 4, 10, tzinfo=UTC)),
            ensure_utc(datetime(2026, 4, 12, tzinfo=UTC)),
            # parameters excludes "precipitation" -> RhiresD does not match.
            ["temperature"],
        )
        assert rows == []


# ---------------------------------------------------------------------------
# Plan 115b1 §1B — archive asset selection (by year, not "first match")
# ---------------------------------------------------------------------------


class TestArchiveAssetSelection:
    def test_fetch_archive_year_selects_the_requested_year_not_the_first(
        self,
    ) -> None:
        sid = StationId(uuid.uuid4())
        url_2019 = "https://dummy/assets/rhiresd_2019.nc"
        url_2020 = "https://dummy/assets/rhiresd_2020.nc"
        bytes_2019 = _netcdf_bytes("RhiresD", "2019-01-01", 1.0)
        bytes_2020 = _netcdf_bytes("RhiresD", "2020-01-01", 2.0)
        assets = {
            **_rhiresd_archive_asset(2019, url_2019),
            **_rhiresd_archive_asset(2020, url_2020),
        }

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url == url_2019:
                return httpx.Response(
                    200,
                    content=bytes_2019,
                    headers={"content-type": "application/x-netcdf"},
                )
            if url == url_2020:
                return httpx.Response(
                    200,
                    content=bytes_2020,
                    headers={"content-type": "application/x-netcdf"},
                )
            if url.endswith("/items/archive-ch"):
                return httpx.Response(200, json=_archive_item(assets))
            raise AssertionError(f"unexpected request: {url}")

        adapter = _make_adapter(httpx.MockTransport(handler), {sid: _make_basin(sid)})
        rows = adapter.fetch_archive_year(
            ForcingSource.METEOSWISS_RHIRESD, 2020, [_make_config(sid)]
        )

        assert rows
        expected_version = hashlib.sha256(bytes_2020).hexdigest()[:16]
        assert all(r.version == expected_version for r in rows)
        assert all(r.source == ForcingSource.METEOSWISS_RHIRESD.value for r in rows)

    def test_missing_year_returns_empty(self) -> None:
        sid = StationId(uuid.uuid4())
        assets = _rhiresd_archive_asset(2020, "https://dummy/assets/rhiresd_2020.nc")

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.endswith("/items/archive-ch"):
                return httpx.Response(200, json=_archive_item(assets))
            raise AssertionError(f"unexpected request (year gap must not fetch): {url}")

        adapter = _make_adapter(httpx.MockTransport(handler), {sid: _make_basin(sid)})
        rows = adapter.fetch_archive_year(
            ForcingSource.METEOSWISS_RHIRESD, 1999, [_make_config(sid)]
        )
        assert rows == []


# ---------------------------------------------------------------------------
# Plan 115b1 §1D — R discovery (latest published RhiresD date)
# ---------------------------------------------------------------------------


class TestDiscoverRhiresdBoundary:
    def test_returns_latest_end_date_across_archive_and_last_family(self) -> None:
        sid = StationId(uuid.uuid4())
        assets = {
            **_rhiresd_archive_asset(2024, "https://dummy/a2024.nc"),
            **_rhiresd_archive_asset(2025, "https://dummy/a2025.nc"),
        }
        last_month_key = (
            "ogd-surface-derived-grid-last.rhiresd_ch01h.swiss.lv95_"
            "20260501000000_20260531000000.nc"
        )
        assets[last_month_key] = {
            "href": "https://dummy/last-may.nc",
            "type": "application/x-netcdf",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "/items?limit=100" in url:
                return httpx.Response(
                    200,
                    json={
                        "features": [_archive_item(assets)],
                        "links": [],
                    },
                )
            raise AssertionError(f"unexpected request: {url}")

        adapter = _make_adapter(httpx.MockTransport(handler), {sid: _make_basin(sid)})
        boundary = adapter.discover_rhiresd_boundary()

        assert boundary is not None
        assert boundary == ensure_utc(datetime(2026, 5, 31, tzinfo=UTC))

    def test_follows_pagination(self) -> None:
        sid = StationId(uuid.uuid4())
        page2_url = "https://dummy/stac/items-page2"
        page1_assets = _rhiresd_archive_asset(2024, "https://dummy/a2024.nc")
        page2_assets = _rhiresd_archive_asset(2025, "https://dummy/a2025.nc")

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url == page2_url:
                return httpx.Response(
                    200,
                    json={
                        "features": [_archive_item(page2_assets)],
                        "links": [],
                    },
                )
            if "/items?limit=100" in url:
                return httpx.Response(
                    200,
                    json={
                        "features": [_archive_item(page1_assets)],
                        "links": [{"rel": "next", "href": page2_url}],
                    },
                )
            raise AssertionError(f"unexpected request: {url}")

        adapter = _make_adapter(httpx.MockTransport(handler), {sid: _make_basin(sid)})
        boundary = adapter.discover_rhiresd_boundary()

        assert boundary == ensure_utc(datetime(2025, 12, 31, tzinfo=UTC))

    def test_empty_collection_returns_none(self) -> None:
        sid = StationId(uuid.uuid4())

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"features": [], "links": []})

        adapter = _make_adapter(httpx.MockTransport(handler), {sid: _make_basin(sid)})
        assert adapter.discover_rhiresd_boundary() is None

    def test_discovers_boundary_from_href_when_key_is_opaque(self) -> None:
        # Real STAC assets may key on an opaque token ("data") while the date
        # span lives ONLY in the href filename. R discovery must parse the href,
        # not just the key — otherwise R is None and Flow 6 silently serves
        # RprelimD for the whole window (Plan 115b1 §1D). Soundness: fails RED
        # against a key-only parse.
        sid = StationId(uuid.uuid4())
        assets: dict[str, dict[str, object]] = {
            "data": {
                "href": (
                    "https://dummy/ogd-surface-derived-grid-archive."
                    "rhiresd_ch01h.swiss.lv95_20250101000000_20251231000000.nc"
                ),
                "type": "application/x-netcdf",
            }
        }

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "/items?limit=100" in url:
                return httpx.Response(
                    200, json={"features": [_archive_item(assets)], "links": []}
                )
            raise AssertionError(f"unexpected request: {url}")

        adapter = _make_adapter(httpx.MockTransport(handler), {sid: _make_basin(sid)})
        assert adapter.discover_rhiresd_boundary() == ensure_utc(
            datetime(2025, 12, 31, tzinfo=UTC)
        )

    def test_ignores_non_rhiresd_distractor_asset(self) -> None:
        # A TabsD (ch01r) archive asset with a LATER end date must NOT be
        # mistaken for the RhiresD boundary — only rhiresd_ch01h assets define
        # R. Soundness: fails RED against a regex too loose to be
        # product-specific (which would return the 2026 distractor end).
        sid = StationId(uuid.uuid4())
        assets = dict(_rhiresd_archive_asset(2024, "https://dummy/a2024.nc"))
        assets[
            "ogd-surface-derived-grid-archive.tabsd_ch01r.swiss.lv95_"
            "20260101000000_20261231000000.nc"
        ] = {"href": "https://dummy/tabsd2026.nc", "type": "application/x-netcdf"}

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "/items?limit=100" in url:
                return httpx.Response(
                    200, json={"features": [_archive_item(assets)], "links": []}
                )
            raise AssertionError(f"unexpected request: {url}")

        adapter = _make_adapter(httpx.MockTransport(handler), {sid: _make_basin(sid)})
        assert adapter.discover_rhiresd_boundary() == ensure_utc(
            datetime(2024, 12, 31, tzinfo=UTC)
        )


# ---------------------------------------------------------------------------
# Plan 115b1 §1B/§1C — real (faithfully-shaped) LV95 archive fixture
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).parents[2] / "fixtures" / "meteoswiss_reanalysis"

# The real fixtures were cropped from genuine MeteoSwiss archive/day downloads
# (live STAC probe, 2026-07-16) around Bern — LV95 E:[2.59e6,2.612e6],
# N:[1.19e6,1.212e6] — which reprojects to approximately this WGS84 window.
_REAL_FIXTURE_BASIN = box(7.35, 46.90, 7.55, 47.00)


def _real_fixture_bytes(name: str) -> bytes:
    return (_FIXTURE_DIR / name).read_bytes()


def _real_fixture_basin() -> Basin:
    return Basin(
        id=BasinId(uuid.uuid4()),
        code="bern",
        name="Bern",
        geometry=_REAL_FIXTURE_BASIN,
        area_km2=100.0,
        attributes=None,
        band_geometries=None,
        created_at=ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)),
        network="test",
    )


def _real_fixture_adapter(
    sid: StationId, handler: Callable[[httpx.Request], httpx.Response]
) -> MeteoSwissOpenDataReanalysisAdapter:
    """A real-extractor adapter (NOT the echo double) over a mock transport."""
    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://dummy"
    )
    return MeteoSwissOpenDataReanalysisAdapter(
        stac_base_url=_STAC_BASE,
        stac_collection=_STAC_COLLECTION,
        http_client=client,
        extractor=ExactExtractGridExtractor(),
        basins={sid: _real_fixture_basin()},
        clock=_clock,
    )


# Basin means computed from the REAL fixture bytes through the adapter's own
# _open_grid -> reproject -> ExactExtractGridExtractor path (live probe,
# 2026-07-16). These are EXACT expected values — the test FAILS if asset
# selection routes to the wrong file, the CRS/dim normalisation regresses, or
# the extraction math changes. (RhiresD is mm of daily precip near Bern in
# early Jan 2020; SrelD is % of the astronomical sunshine maximum.)
_RHIRESD_EXPECTED: dict[datetime, float] = {
    datetime(2020, 1, 1, tzinfo=UTC): 2.919777691320872e-05,
    datetime(2020, 1, 2, tzinfo=UTC): 7.208270440754337e-05,
}
_SRELD_EXPECTED: dict[datetime, float] = {
    datetime(2026, 7, 9, tzinfo=UTC): 99.70962845719922,
    datetime(2026, 7, 10, tzinfo=UTC): 91.68713106471749,
}


class TestRealShapeFixtureExtraction:
    """Proves asset selection, variable names, dims, CRS normalisation, and
    ``exactextract`` compatibility against REAL (not synthetic) MeteoSwiss
    NetCDF bytes — for a ch01h (RhiresD) and a ch01r (SrelD) file, BOTH via the
    yearly ARCHIVE family (Plan 115b1 §1B/§1C).

    The synthetic fixtures elsewhere in this module use lat/lon dims directly
    and prove nothing about real LV95 (N/E dims + 2D curvilinear lon/lat
    auxiliary coordinates) files — this class is what actually exercises that
    path, using the REAL extractor (not the echo double). Each test pins the
    EXACT valid_times and basin means, so it fails RED against wrong archive
    selection (ch01h vs ch01r) or broken extraction math — not merely a
    finite/in-range check that a NaN-or-anything result would slip through.
    """

    def test_rhiresd_ch01h_archive_fixture_extracts_known_basin_means(self) -> None:
        sid = StationId(uuid.uuid4())
        asset_url = "https://dummy/assets/rhiresd_real.nc"
        data = _real_fixture_bytes("rhiresd_ch01h_real_shape.nc")
        assets = _archive_asset("rhiresd", "ch01h", 2020, asset_url)

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url == asset_url:
                return httpx.Response(
                    200, content=data, headers={"content-type": "application/x-netcdf"}
                )
            if url.endswith("/items/archive-ch"):
                return httpx.Response(200, json=_archive_item(assets))
            raise AssertionError(f"unexpected request: {url}")

        adapter = _real_fixture_adapter(sid, handler)
        rows = adapter.fetch_archive_year(
            ForcingSource.METEOSWISS_RHIRESD, 2020, [_make_config(sid)]
        )

        assert rows
        assert all(r.parameter == "precipitation" for r in rows)
        assert all(r.source == ForcingSource.METEOSWISS_RHIRESD.value for r in rows)
        means = {r.valid_time: r.value for r in rows}
        assert set(means) == set(_RHIRESD_EXPECTED)
        for valid_time, expected in _RHIRESD_EXPECTED.items():
            assert means[valid_time] == pytest.approx(expected, rel=1e-9)

    def test_sreld_ch01r_archive_fixture_extracts_known_basin_means(self) -> None:
        # The ch01r SrelD case goes through the WRITER product-scoped
        # ``fetch_products`` path, which routes archive-backed products through
        # the yearly archive family — the same code path the 115b2 backfill
        # uses. Proves ch01r archive asset selection AND extraction math (not a
        # daily fetch, which never touches archive addressing).
        sid = StationId(uuid.uuid4())
        asset_url = "https://dummy/assets/sreld_real.nc"
        data = _real_fixture_bytes("sreld_ch01r_real_shape.nc")
        assets = _archive_asset("sreld", "ch01r", 2026, asset_url)

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url == asset_url:
                return httpx.Response(
                    200, content=data, headers={"content-type": "application/x-netcdf"}
                )
            if url.endswith("/items/archive-ch"):
                return httpx.Response(200, json=_archive_item(assets))
            # A daily-item lookup here would mean archive routing regressed —
            # fail loudly rather than silently gap out.
            raise AssertionError(f"unexpected request (must use archive): {url}")

        adapter = _real_fixture_adapter(sid, handler)
        rows = adapter.fetch_products(
            [ForcingSource.METEOSWISS_SRELD],
            [_make_config(sid)],
            ensure_utc(datetime(2026, 7, 9, tzinfo=UTC)),
            ensure_utc(datetime(2026, 7, 11, tzinfo=UTC)),
            ["relative_sunshine_duration"],
        )

        assert rows
        assert all(r.parameter == "relative_sunshine_duration" for r in rows)
        assert all(r.source == ForcingSource.METEOSWISS_SRELD.value for r in rows)
        means = {r.valid_time: r.value for r in rows}
        assert set(means) == set(_SRELD_EXPECTED)
        for valid_time, expected in _SRELD_EXPECTED.items():
            assert means[valid_time] == pytest.approx(expected, rel=1e-9)
