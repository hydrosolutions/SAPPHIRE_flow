import json
from datetime import UTC, datetime

import numpy as np
import pytest
import xarray as xr

from sapphire_flow.exceptions import StoreError
from sapphire_flow.protocols.stores import NwpGridStore
from sapphire_flow.store.zarr_nwp_grid_store import ZarrNwpGridStore
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.weather import GriddedForecast


def _make_forecast(cycle_time: UtcDatetime) -> GriddedForecast:
    """Create a small synthetic GriddedForecast."""
    ds = xr.Dataset(
        {
            "precipitation": xr.DataArray(
                np.random.rand(3, 5, 4, 4).astype(np.float32),
                dims=["member", "valid_time", "latitude", "longitude"],
            ),
            "temperature": xr.DataArray(
                np.random.rand(3, 5, 4, 4).astype(np.float32),
                dims=["member", "valid_time", "latitude", "longitude"],
            ),
        }
    )
    return GriddedForecast(nwp_source="icon_ch2_eps", cycle_time=cycle_time, values=ds)


def _make_dask_forecast_real_dim_order(cycle_time: UtcDatetime) -> GriddedForecast:
    """A dask-backed forecast with the REAL adapter dim order/chunking.

    Dims ``(valid_time, member, values)`` chunked ``(1, 1, N)`` — exactly what
    the lazy cfgrib parse produces (one GRIB message per file → one member, one
    step). Feeding this naive-lazy source to archive's ``(1, member, values)``
    encoding raises the dask-chunk-overlap ``ValueError`` unless archive rechunks
    it first (Plan 086, the BLOCKER).
    """
    ds = xr.Dataset(
        {
            "precipitation": xr.DataArray(
                np.arange(2 * 3 * 8, dtype=np.float32).reshape(2, 3, 8),
                dims=["valid_time", "member", "values"],
            ),
            "temperature": xr.DataArray(
                np.arange(2 * 3 * 8, dtype=np.float32).reshape(2, 3, 8) + 100.0,
                dims=["valid_time", "member", "values"],
            ),
        }
    ).chunk({"valid_time": 1, "member": 1, "values": -1})
    return GriddedForecast(nwp_source="icon_ch2_eps", cycle_time=cycle_time, values=ds)


class TestZarrNwpGridStore:
    def test_archive_round_trip_dask_real_dim_order(self, tmp_path: object) -> None:
        """A lazy (1,1,N) source in real dim order archives and round-trips.

        Red on main: archive feeds the (1,1,N) dask source straight into the
        (1, member, values) encoding → ValueError "would overlap multiple Dask
        chunks". Green after the archive rechunk to (1, *shape[1:]).
        """
        store = ZarrNwpGridStore()
        ct = ensure_utc(datetime(2026, 4, 1, 6, tzinfo=UTC))
        forecast = _make_dask_forecast_real_dim_order(ct)

        path = store.archive(forecast, tmp_path)  # type: ignore[arg-type]
        assert path.exists()

        loaded = store.load(tmp_path, "icon_ch2_eps", ct)  # type: ignore[arg-type]
        loaded.values.load()
        xr.testing.assert_equal(loaded.values, forecast.values.compute())

    def test_round_trip(self, tmp_path: object) -> None:
        """Archive then load produces identical Dataset."""
        store = ZarrNwpGridStore()
        ct = ensure_utc(datetime(2026, 4, 1, 6, tzinfo=UTC))
        forecast = _make_forecast(ct)

        path = store.archive(forecast, tmp_path)  # type: ignore[arg-type]
        assert path.exists()

        loaded = store.load(tmp_path, "icon_ch2_eps", ct)  # type: ignore[arg-type]
        assert loaded.nwp_source == "icon_ch2_eps"
        assert loaded.cycle_time == ct
        xr.testing.assert_equal(loaded.values, forecast.values)

    def test_zarr_uses_zstd_compression(self, tmp_path: object) -> None:
        """Archived Zarr uses zstd compression."""
        store = ZarrNwpGridStore()
        ct = ensure_utc(datetime(2026, 4, 1, 6, tzinfo=UTC))
        forecast = _make_forecast(ct)
        path = store.archive(forecast, tmp_path)  # type: ignore[arg-type]
        zarray_path = path / "precipitation" / ".zarray"
        meta = json.loads(zarray_path.read_text())
        assert meta["compressor"]["id"] == "zstd"

    def test_chunks_per_member(self, tmp_path: object) -> None:
        """Chunks follow (1, *shape[1:]) strategy."""
        store = ZarrNwpGridStore()
        ct = ensure_utc(datetime(2026, 4, 1, 6, tzinfo=UTC))
        forecast = _make_forecast(ct)
        path = store.archive(forecast, tmp_path)  # type: ignore[arg-type]
        zarray_path = path / "precipitation" / ".zarray"
        meta = json.loads(zarray_path.read_text())
        assert meta["chunks"][0] == 1

    def test_path_convention(self, tmp_path: object) -> None:
        """Path follows {base}/{nwp_source}/{cycle:%Y%m%dT%H}.zarr."""
        store = ZarrNwpGridStore()
        ct = ensure_utc(datetime(2026, 4, 1, 6, tzinfo=UTC))
        forecast = _make_forecast(ct)
        path = store.archive(forecast, tmp_path)  # type: ignore[arg-type]
        assert path == tmp_path / "icon_ch2_eps" / "20260401T06.zarr"  # type: ignore[operator]

    def test_no_temp_dirs_after_archive(self, tmp_path: object) -> None:
        """No .zarr.tmp or .zarr.old left after successful archive."""
        store = ZarrNwpGridStore()
        ct = ensure_utc(datetime(2026, 4, 1, 6, tzinfo=UTC))
        forecast = _make_forecast(ct)
        store.archive(forecast, tmp_path)  # type: ignore[arg-type]
        for p in tmp_path.rglob("*"):  # type: ignore[attr-defined]
            assert ".zarr.tmp" not in str(p)
            assert ".zarr.old" not in str(p)

    def test_load_nonexistent_raises_store_error(self, tmp_path: object) -> None:
        """Loading a nonexistent archive raises StoreError."""
        store = ZarrNwpGridStore()
        ct = ensure_utc(datetime(2026, 4, 1, 6, tzinfo=UTC))
        with pytest.raises(StoreError, match="NWP archive not found"):
            store.load(tmp_path, "icon_ch2_eps", ct)  # type: ignore[arg-type]

    def test_protocol_conformance(self) -> None:
        """ZarrNwpGridStore satisfies NwpGridStore Protocol."""
        assert isinstance(ZarrNwpGridStore(), NwpGridStore)

    def test_overwrite_existing_archive(self, tmp_path: object) -> None:
        """Archiving over existing archive replaces it atomically."""
        store = ZarrNwpGridStore()
        ct = ensure_utc(datetime(2026, 4, 1, 6, tzinfo=UTC))
        forecast1 = _make_forecast(ct)
        store.archive(forecast1, tmp_path)  # type: ignore[arg-type]

        forecast2 = _make_forecast(ct)
        store.archive(forecast2, tmp_path)  # type: ignore[arg-type]

        loaded = store.load(tmp_path, "icon_ch2_eps", ct)  # type: ignore[arg-type]
        xr.testing.assert_equal(loaded.values, forecast2.values)

    def test_archive_is_zarr_format_v2(self, tmp_path: object) -> None:
        """Archive is written in zarr v2 on-disk format (zarr_format=2)."""
        store = ZarrNwpGridStore()
        ct = ensure_utc(datetime(2026, 4, 1, 6, tzinfo=UTC))
        forecast = _make_forecast(ct)
        path = store.archive(forecast, tmp_path)  # type: ignore[arg-type]
        assert (path / ".zgroup").exists(), "v2 format marker missing"
        assert not (path / "zarr.json").exists(), "v3 format marker should not appear"
        zarray = json.loads((path / "precipitation" / ".zarray").read_text())
        assert zarray["zarr_format"] == 2
        assert zarray["compressor"]["id"] == "zstd"
