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

import re
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import numpy as np
import pandas as pd
import polars as pl
import pytest
import xarray as xr
from sapphire_flow.adapters.meteoswiss_open_data_reanalysis import (
    MeteoSwissOpenDataReanalysisAdapter,
)
from sapphire_flow.types.forcing_schema import CANONICAL_FORCING_SCHEMA
from sapphire_flow.types.forcing_sources import ForcingSource
from shapely.geometry import box

from sapphire_flow.adapters.forecast_interface import fi_unit_to_canonical
from sapphire_flow.exceptions import AdapterError
from sapphire_flow.preprocessing.exact_extract_grid_extractor import (
    ExactExtractGridExtractor,
)
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceStatus,
)
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
_CANONICAL_PARAMETERS = {
    "precipitation",
    "temperature",
    "temperature_min",
    "temperature_max",
}

# (raw NetCDF variable name, product token, canonical parameter, ForcingSource,
#  fill value). The raw variable name is deliberately NOT canonical so the
#  adapter must apply its product -> canonical-parameter mapping.
_PRODUCTS: list[tuple[str, str, str, ForcingSource, float]] = [
    ("RprelimD", "rprelimd", "precipitation", ForcingSource.METEOSWISS_RPRELIMD, 10.0),
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


def _byte_map(*, rprelimd_fill: float = 10.0) -> dict[tuple[str, str], bytes]:
    out: dict[tuple[str, str], bytes] = {}
    for raw, token, _param, _src, fill in _PRODUCTS:
        eff_fill = rprelimd_fill if token == "rprelimd" else fill
        for day in _DAYS:
            out[(token, day)] = _netcdf_bytes(raw, day, eff_fill)
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
        # Mutating only the precipitation product's bytes must change the
        # precipitation rows' versions and leave the other products untouched
        # (content-addressed per asset).
        baseline = _version_index(_fetch(_byte_map())[1])
        mutated = _version_index(_fetch(_byte_map(rprelimd_fill=99.0))[1])

        precip_src = ForcingSource.METEOSWISS_RPRELIMD.value
        for key, version in baseline.items():
            source = key[0]
            if source == precip_src:
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
            assert canonical in {"mm", "°C"}


class TestFetchReanalysisErrorPaths:
    def test_stac_server_error_raises_adapter_error(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "upstream failure"})

        sid = StationId(uuid.uuid4())
        adapter = _make_adapter(httpx.MockTransport(handler), {sid: _make_basin(sid)})
        with pytest.raises(AdapterError):
            adapter.fetch_reanalysis(
                [_make_config(sid)],
                ensure_utc(datetime(2026, 4, 10, tzinfo=UTC)),
                ensure_utc(datetime(2026, 4, 12, tzinfo=UTC)),
                sorted(_CANONICAL_PARAMETERS),
            )


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
