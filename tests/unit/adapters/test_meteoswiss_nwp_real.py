"""Real-data integration tests for ``_parse_grib_files``.

These tests use actual MeteoSwiss ICON-CH2-EPS fixtures (CC-BY) to exercise
cfgrib + xarray semantics that mock fakes silently mask. They run in the
default unit-test suite (not marked slow — fixtures are small, test is fast).

See ``tests/fixtures/meteoswiss_nwp/README.md`` for fixture provenance.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import xarray as xr

from sapphire_flow.adapters.meteoswiss_nwp import MeteoSwissNwpAdapter

_STAC_BASE = "https://data.geo.admin.ch/api/stac/v1"
_STAC_COLLECTION = "ch.meteoschweiz.ogd-forecasting-icon-ch2"

_FIXTURE_DIR = (
    Path(__file__).parent.parent.parent
    / "fixtures"
    / "meteoswiss_nwp"
    / "icon_ch2_eps_202604231200"
)


def _make_adapter(tmp_path: Path) -> MeteoSwissNwpAdapter:
    # Real-data tests never hit the network — dummy transport is fine.
    transport = httpx.MockTransport(lambda _req: httpx.Response(404))
    client = httpx.Client(transport=transport, base_url="https://dummy")
    return MeteoSwissNwpAdapter(
        stac_base_url=_STAC_BASE,
        stac_collection=_STAC_COLLECTION,
        scratch_path=tmp_path,
        http_client=client,
    )


@pytest.fixture
def fixture_files() -> list[Path]:
    files = sorted(_FIXTURE_DIR.glob("*.grib2"))
    if not files:
        pytest.skip(f"fixtures not found at {_FIXTURE_DIR}")
    return files


class TestParseGribFilesReal:
    def test_parses_real_meteoswiss_icon_ch2_eps(
        self, fixture_files: list[Path], tmp_path: Path
    ) -> None:
        adapter = _make_adapter(tmp_path)
        ds = adapter._parse_grib_files(fixture_files)

        assert isinstance(ds, xr.Dataset)
        # convert_raw_dataset renames `number` → `member` and replaces raw
        # vars with SAPPHIRE-canonical names.
        assert "member" in ds.dims
        assert "valid_time" in ds.dims
        # Both variables survived: precipitation (from tp, deaccumulated)
        # and temperature (from t2m, Kelvin → Celsius).
        assert "temperature" in ds.data_vars
        assert "precipitation" in ds.data_vars
        # Raw names must be gone — convert_raw_dataset drops them.
        assert "tp" not in ds.data_vars
        assert "t_2m" not in ds.data_vars
        assert "t2m" not in ds.data_vars

    def test_handles_scalar_and_vector_number_coord(
        self, fixture_files: list[Path], tmp_path: Path
    ) -> None:
        # Ctrl files carry scalar `number=0`; perturb files carry a 1-D
        # `number` of length 20 (values 1..20). After combining, we must
        # see all 21 members as a single `member` dim.
        adapter = _make_adapter(tmp_path)
        ds = adapter._parse_grib_files(fixture_files)

        assert ds.sizes["member"] == 21
        members = ds.coords["member"].values.tolist()
        assert sorted(members) == list(range(21))

    def test_multi_step_concat_along_valid_time(
        self, fixture_files: list[Path], tmp_path: Path
    ) -> None:
        # Step 0 and step 1 ctrl files exist for both variables — valid_time
        # must be monotonically increasing after combine, and carry 2 distinct
        # timestamps (2026-04-23T12:00 and 13:00).
        adapter = _make_adapter(tmp_path)
        ds = adapter._parse_grib_files(fixture_files)

        times = ds.coords["valid_time"].values
        assert ds.sizes["valid_time"] == 2
        assert (times[1:] > times[:-1]).all(), "valid_time must be sorted"

    def test_parsed_cube_is_dask_backed_lazy(
        self, fixture_files: list[Path], tmp_path: Path
    ) -> None:
        # Plan 086: the parse must open cfgrib lazily (chunks={}) and keep the
        # concat/merge/convert path lazy, so every data var is dask-backed
        # (.chunks is not None). Red on main (eager numpy → .chunks is None).
        adapter = _make_adapter(tmp_path)
        ds = adapter._parse_grib_files(fixture_files)

        assert ds.data_vars, "expected at least one data var"
        for name, var in ds.data_vars.items():
            assert var.chunks is not None, f"{name} is eager (not dask-backed)"

    def test_unstructured_grid_preserved(
        self, fixture_files: list[Path], tmp_path: Path
    ) -> None:
        # ICON uses a triangular icosahedral mesh — ecCodes exposes cells as
        # a single `values` dim (283876). The parse path must not silently
        # drop it.
        adapter = _make_adapter(tmp_path)
        ds = adapter._parse_grib_files(fixture_files)

        assert "values" in ds.dims
        assert ds.sizes["values"] == 283876

    def test_mesh_coords_attached_on_values_dim(
        self, fixture_files: list[Path], tmp_path: Path
    ) -> None:
        # Plan 087: convert_raw_dataset attaches per-cell latitude/longitude
        # (from the static package asset) on the `values` dim. The arrays span
        # the FULL ICON-CH2 model domain (NOT the narrow Swiss band): min lon is
        # NEGATIVE (~-0.77), confirming the values are already on [-180, 180].
        adapter = _make_adapter(tmp_path)
        ds = adapter._parse_grib_files(fixture_files)

        assert "latitude" in ds.coords
        assert "longitude" in ds.coords
        assert ds["latitude"].dims == ("values",)
        assert ds["longitude"].dims == ("values",)
        assert ds["latitude"].sizes["values"] == 283876
        assert ds["longitude"].sizes["values"] == 283876

        lat = ds["latitude"].values
        lon = ds["longitude"].values
        assert 42.0 < float(lat.min()) < 42.5
        assert 50.0 < float(lat.max()) < 50.5
        # min lon NEGATIVE → already on [-180, 180], normalisation is a no-op.
        assert -1.0 < float(lon.min()) < 0.0
        assert 17.0 < float(lon.max()) < 18.0
