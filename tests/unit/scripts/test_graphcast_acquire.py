"""Plan 240 (M-A12) T2 — the GraphCast extractor's own contract: D1 (00Z
schedule), D2 (pin asserted from the file's own attrs, never the key), D4
(units read from the file, m -> mm, no deaccumulation, negative noise
clipped), D5 (nearest-cell reuses the SAME operator as `tigge_ifs`, applied
to a regular grid rather than a point cloud), and D6 (a missing key is a
gap, never filled).

No network access — every extraction test opens a REAL in-memory HDF5 file
(`h5py.File` over `io.BytesIO`), so the gzip+shuffle filter pipeline that
would corrupt a naive `zlib.decompress`-only read is exercised for real,
never mocked away. Mirrors `tests/unit/scripts/test_tigge_ifs.py`'s
synthetic-fixture convention.
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import TYPE_CHECKING

import h5py
import numpy as np
import polars as pl
import pytest

from scripts.dhm_precip.domain_types import (
    Station,
    StationCoordinate,
    StationCoordinateTable,
)
from scripts.dhm_precip.graphcast_acquire import (
    EXPECTED_GRID_SHAPE,
    VERSION_COLUMN,
    GraphcastAcquisitionError,
    NearestCellIndex,
    _assert_pinned_identity,
    _attr_str,
    extract_box_apcp,
    forecast_key,
    nearest_cell_indices,
    probe_grid,
    season_init_times,
    season_versions,
    write_points_parquet,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestForecastKey:
    def test_matches_the_measured_bucket_naming_convention(self) -> None:
        key = forecast_key(datetime(2022, 6, 1, 0))
        assert key == "GRAP_v100_GFS/2022/0601/GRAP_v100_GFS_2022060100_f000_f240_06.nc"

    def test_zero_pads_month_day(self) -> None:
        key = forecast_key(datetime(2023, 9, 5, 0))
        assert "2023/0905/" in key
        assert key.endswith("_2023090500_f000_f240_06.nc")


class TestSeasonInitTimes:
    def test_covers_exactly_one_jjas_season_of_00z_inits(self) -> None:
        times = season_init_times(2023)
        assert len(times) == 30 + 31 + 31 + 30  # June, July, August, September
        assert times[0] == datetime(2023, 6, 1, 0)
        assert times[-1] == datetime(2023, 9, 30, 0)
        assert all(t.hour == 0 for t in times)

    def test_every_day_is_present_exactly_once(self) -> None:
        times = season_init_times(2024)  # leap year — September still has 30 days
        assert len(set(times)) == len(times)
        assert len(times) == 30 + 31 + 31 + 30


class TestAttrStr:
    def test_decodes_hdf5_bytes_attributes(self) -> None:
        assert _attr_str(b"GraphCast") == "GraphCast"

    def test_passes_through_a_plain_string(self) -> None:
        assert _attr_str("GraphCast") == "GraphCast"


class TestAssertPinnedIdentity:
    """The pin as REVISED at the 2025 boundary: `model_name` case-insensitive,
    `model_version` no longer pinned (absent in v3), `initialization_model`
    still strict, and `version` recorded rather than merely tolerated."""

    def _attrs(self, **overrides: str) -> dict[str, object]:
        base = {
            "model_name": "GraphCast",
            "model_version": "v1",
            "initialization_model": "GFS",
            "version": "1_2023-10-14",
        }
        base.update(overrides)
        return base

    def test_accepts_the_pinned_variant_and_returns_its_version(self) -> None:
        assert _assert_pinned_identity(self._attrs(), key="k") == "1_2023-10-14"

    def test_accepts_a_lowercase_model_name(self) -> None:
        """2025's files say "graphcast", 2024's say "GraphCast" — case only,
        genuinely cosmetic, and it must not reject a valid season."""
        attrs = self._attrs(model_name="graphcast", version="3_2025-02-20")
        del attrs["model_version"]  # v3 files carry no `model_version` at all
        assert _assert_pinned_identity(attrs, key="k") == "3_2025-02-20"

    def test_rejects_the_ifs_initialised_variant(self) -> None:
        with pytest.raises(GraphcastAcquisitionError, match="initialization_model"):
            _assert_pinned_identity(self._attrs(initialization_model="IFS"), key="k")

    def test_rejects_a_file_with_no_version_attribute(self) -> None:
        attrs = self._attrs()
        del attrs["version"]
        with pytest.raises(GraphcastAcquisitionError, match="version"):
            _assert_pinned_identity(attrs, key="k")


class TestSeasonVersions:
    """D2 — `version` must be CONSTANT within a season but is ALLOWED to
    differ between seasons, and the value is carried through so every row and
    every report line is labelled."""

    def _write(self, root: Path, year: int, versions: list[str]) -> None:
        out_dir = root / "points"
        out_dir.mkdir(parents=True, exist_ok=True)
        n = len(versions)
        pl.DataFrame(
            {
                "station": ["A"] * n,
                "init_time_utc": [datetime(year, 6, 1 + i, 0) for i in range(n)],
                "ending_lead_hours": [6] * n,
                "valid_time_utc": [datetime(year, 6, 1 + i, 6) for i in range(n)],
                "tigge_mm": [1.0] * n,
                VERSION_COLUMN: versions,
            }
        ).write_parquet(out_dir / f"tigge_station_series_jjas{year}.parquet")

    def test_a_version_change_within_a_season_fails(self, tmp_path: Path) -> None:
        self._write(tmp_path, 2025, ["1_2023-10-14", "3_2025-02-20"])
        with pytest.raises(GraphcastAcquisitionError, match="not constant"):
            season_versions(out_root=tmp_path, seasons=(2025,))

    def test_a_version_change_between_seasons_is_allowed_and_labelled(
        self, tmp_path: Path
    ) -> None:
        self._write(tmp_path, 2024, ["1_2023-10-14"] * 2)
        self._write(tmp_path, 2025, ["3_2025-02-20"] * 2)
        assert season_versions(out_root=tmp_path, seasons=(2024, 2025)) == {
            2024: "1_2023-10-14",
            2025: "3_2025-02-20",
        }


class TestNearestCellIndices:
    """D5 — the SAME haversine argmin operator `tigge_ifs.nearest_point_index`
    uses, applied to a regular grid's flattened mesh rather than an
    irregular point cloud."""

    def _coords(self, lat: float, lon: float) -> StationCoordinateTable:
        return StationCoordinateTable(
            by_station={
                Station("S"): StationCoordinate(
                    station=Station("S"), excel_col="A", lat=lat, lon=lon, elev_m=500.0
                )
            }
        )

    def test_picks_the_nearest_regular_grid_cell(self) -> None:
        lat = np.array([30.0, 29.75, 29.5])
        lon = np.array([80.0, 80.25, 80.5])
        coords = self._coords(lat=29.7, lon=80.3)
        result = nearest_cell_indices(coords, lat=lat, lon=lon, expected_shape=None)
        assert result[Station("S")] == NearestCellIndex(row=1, col=1)

    def test_rejects_an_unexpected_grid_shape(self) -> None:
        lat = np.array([30.0, 29.0])
        lon = np.array([80.0])
        coords = self._coords(lat=29.0, lon=80.0)
        with pytest.raises(GraphcastAcquisitionError, match="grid shape"):
            nearest_cell_indices(coords, lat=lat, lon=lon)


def _fake_forecast_bytes(
    *,
    model_name: str = "GraphCast",
    model_version: str = "v1",
    initialization_model: str = "GFS",
    version: str = "1_2023-10-14",
    units: str = "m",
    n_steps: int = 5,
    apcp_mm_per_step: float = 1.0,
) -> bytes:
    """A REAL HDF5 file (gzip + shuffle, exactly the archive's own encoding
    — measured, T1) small enough to hold in memory: `EXPECTED_GRID_SHAPE`
    lat/lon axes, `apcp` in metres. Step `i`'s value is `i * apcp_mm_per_step`
    mm at every grid cell, plus one deliberately negative cell at step 1
    (D4's clipped numerical noise)."""
    n_lat, n_lon = EXPECTED_GRID_SHAPE
    lat = np.linspace(90.0, -90.0, n_lat, dtype="f4")
    lon = np.arange(0.0, 360.0, 360.0 / n_lon, dtype="f4")
    buf = io.BytesIO()
    with h5py.File(buf, "w") as f:
        f.attrs["model_name"] = model_name
        f.attrs["model_version"] = model_version
        f.attrs["initialization_model"] = initialization_model
        f.attrs["version"] = version
        f.create_dataset("latitude", data=lat)
        f.create_dataset("longitude", data=lon)
        apcp = f.create_dataset(
            "apcp",
            shape=(n_steps, n_lat, n_lon),
            dtype="f4",
            chunks=(1, n_lat, n_lon),
            compression="gzip",
            shuffle=True,
        )
        apcp.attrs["units"] = units
        values = np.zeros((n_steps, n_lat, n_lon), dtype="f4")
        for step in range(n_steps):
            values[step, :, :] = step * (apcp_mm_per_step / 1000.0)
        values[1, 0, 0] = -1.4e-4  # D4 — measured numerical noise, must clip to 0
        apcp[:] = values
    return buf.getvalue()


class _FakeS3FileSystem:
    """A REAL byte-store keyed by path, never a `Mock` — `.open` returns a
    fresh `BytesIO` over the registered bytes so h5py can seek freely, and
    an unregistered path raises `FileNotFoundError` exactly like s3fs does
    for a missing key (D6's gap path)."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self._files = files

    def open(
        self, path: str, mode: str = "rb", cache_type: str | None = None
    ) -> io.BytesIO:
        del mode, cache_type
        if path not in self._files:
            raise FileNotFoundError(path)
        return io.BytesIO(self._files[path])


def _one_station_grid_nearest(
    *, row: int = 0, col: int = 0
) -> dict[Station, NearestCellIndex]:
    return {Station("Origin"): NearestCellIndex(row=row, col=col)}


class TestExtractBoxApcp:
    def test_valid_time_is_init_plus_lead_units_converted_to_mm(self) -> None:
        init = datetime(2022, 6, 1, 0)
        key = forecast_key(init)
        fs = _FakeS3FileSystem(
            {f"noaa-oar-mlwp-data/{key}": _fake_forecast_bytes(apcp_mm_per_step=2.0)}
        )
        # nearest cell (0, 1) — deliberately NOT (0, 0), where the fixture
        # plants its D4 negative-noise cell (covered by the test below).
        frame = extract_box_apcp(
            fs=fs,
            init_time=init,
            nearest=_one_station_grid_nearest(row=0, col=1),
            lead_hours=(6, 12),
        )
        assert frame is not None
        assert frame.height == 2
        row6 = frame.filter(frame["ending_lead_hours"] == 6).row(0, named=True)
        assert row6["valid_time_utc"] == init.replace(hour=6)
        assert row6["tigge_mm"] == pytest.approx(2.0)  # step 1 * 2.0 mm
        row12 = frame.filter(frame["ending_lead_hours"] == 12).row(0, named=True)
        assert row12["tigge_mm"] == pytest.approx(4.0)  # step 2 * 2.0 mm

    def test_every_row_is_labelled_with_the_files_own_model_version(self) -> None:
        """D2 — a v3 file's rows say v3, read off the file, never assumed."""
        init = datetime(2025, 6, 1, 0)
        fs = _FakeS3FileSystem(
            {
                f"noaa-oar-mlwp-data/{forecast_key(init)}": _fake_forecast_bytes(
                    model_name="graphcast", version="3_2025-02-20"
                )
            }
        )
        frame = extract_box_apcp(
            fs=fs, init_time=init, nearest=_one_station_grid_nearest(), lead_hours=(6,)
        )
        assert frame is not None
        assert frame[VERSION_COLUMN].to_list() == ["3_2025-02-20"]

    def test_negative_numerical_noise_is_clipped_to_zero(self) -> None:
        init = datetime(2022, 6, 1, 0)
        key = forecast_key(init)
        fs = _FakeS3FileSystem({f"noaa-oar-mlwp-data/{key}": _fake_forecast_bytes()})
        # station nearest cell is (0, 0) — exactly where the fixture plants
        # the negative noise at step 1 (lead 6h).
        frame = extract_box_apcp(
            fs=fs, init_time=init, nearest=_one_station_grid_nearest(), lead_hours=(6,)
        )
        assert frame is not None
        assert frame["tigge_mm"][0] == 0.0

    def test_missing_key_is_a_gap_not_an_error(self) -> None:
        fs = _FakeS3FileSystem({})
        frame = extract_box_apcp(
            fs=fs,
            init_time=datetime(2022, 6, 1, 0),
            nearest=_one_station_grid_nearest(),
            lead_hours=(6,),
        )
        assert frame is None

    def test_rejects_a_file_whose_attrs_do_not_match_the_pin(self) -> None:
        init = datetime(2022, 6, 1, 0)
        key = forecast_key(init)
        fs = _FakeS3FileSystem(
            {
                f"noaa-oar-mlwp-data/{key}": _fake_forecast_bytes(
                    initialization_model="IFS"
                )
            }
        )
        with pytest.raises(GraphcastAcquisitionError, match="initialization_model"):
            extract_box_apcp(
                fs=fs,
                init_time=init,
                nearest=_one_station_grid_nearest(),
                lead_hours=(6,),
            )

    def test_rejects_units_other_than_metres(self) -> None:
        init = datetime(2022, 6, 1, 0)
        key = forecast_key(init)
        fs = _FakeS3FileSystem(
            {f"noaa-oar-mlwp-data/{key}": _fake_forecast_bytes(units="kg m-2")}
        )
        with pytest.raises(GraphcastAcquisitionError, match="units"):
            extract_box_apcp(
                fs=fs,
                init_time=init,
                nearest=_one_station_grid_nearest(),
                lead_hours=(6,),
            )

    def test_rejects_noncontiguous_leads(self) -> None:
        init = datetime(2022, 6, 1, 0)
        key = forecast_key(init)
        fs = _FakeS3FileSystem({f"noaa-oar-mlwp-data/{key}": _fake_forecast_bytes()})
        with pytest.raises(GraphcastAcquisitionError, match="contiguous"):
            extract_box_apcp(
                fs=fs,
                init_time=init,
                nearest=_one_station_grid_nearest(),
                lead_hours=(6, 24),
            )


class TestWritePointsParquet:
    def test_writes_under_points_with_the_shared_naming_convention(
        self, tmp_path: Path
    ) -> None:
        frame = pl.DataFrame(
            {
                "station": ["A"],
                "init_time_utc": [datetime(2022, 6, 1, 0)],
                "ending_lead_hours": [6],
                "valid_time_utc": [datetime(2022, 6, 1, 6)],
                "tigge_mm": [1.5],
            }
        )
        out_path = write_points_parquet(frame, out_root=tmp_path, year=2022)
        assert out_path.name == "tigge_station_series_jjas2022.parquet"
        assert out_path.parent.name == "points"
        assert pl.read_parquet(out_path).height == 1


class TestProbeGrid:
    """D5/D6 — the publication grid comes from the first AVAILABLE forecast.
    ⛔ A missing key at the head of the run is a gap `retrieve_season` still
    records; it must not abort every season. The run fails only when NO
    requested init is present, because then there is no grid at all."""

    def test_reads_the_grid_from_the_first_key_when_it_is_present(self) -> None:
        inits = season_init_times(2022)[:3]
        fs = _FakeS3FileSystem(
            {
                f"noaa-oar-mlwp-data/{forecast_key(inits[0])}": _fake_forecast_bytes(),
            }
        )
        lat, lon = probe_grid(fs=fs, inits=inits)
        assert (lat.size, lon.size) == EXPECTED_GRID_SHAPE

    def test_walks_past_a_missing_first_key_instead_of_aborting(self) -> None:
        """The defect this test pins: opening `inits[0]` directly aborted the
        WHOLE multi-season retrieval when that single file was absent."""
        inits = season_init_times(2022)[:3]
        fs = _FakeS3FileSystem(
            {
                # inits[0] and inits[1] deliberately absent — gaps, not errors.
                f"noaa-oar-mlwp-data/{forecast_key(inits[2])}": _fake_forecast_bytes(),
            }
        )
        lat, lon = probe_grid(fs=fs, inits=inits)
        assert (lat.size, lon.size) == EXPECTED_GRID_SHAPE

    def test_missing_first_key_of_the_first_season_still_reaches_a_later_season(
        self,
    ) -> None:
        """The multi-season shape of the same defect — season 2022's head is
        absent, so the grid must come from 2023."""
        inits = (*season_init_times(2022)[:2], *season_init_times(2023)[:2])
        fs = _FakeS3FileSystem(
            {
                f"noaa-oar-mlwp-data/{forecast_key(inits[3])}": _fake_forecast_bytes(),
            }
        )
        lat, lon = probe_grid(fs=fs, inits=inits)
        assert (lat.size, lon.size) == EXPECTED_GRID_SHAPE

    def test_fails_when_no_requested_initialisation_is_present(self) -> None:
        inits = season_init_times(2022)[:3]
        with pytest.raises(GraphcastAcquisitionError, match="no requested"):
            probe_grid(fs=_FakeS3FileSystem({}), inits=inits)

    def test_still_asserts_the_pin_on_whichever_file_it_lands_on(self) -> None:
        """⛔ Walking forward must not weaken D2 — the file actually opened is
        still checked against the pin."""
        inits = season_init_times(2022)[:2]
        fs = _FakeS3FileSystem(
            {
                f"noaa-oar-mlwp-data/{forecast_key(inits[1])}": _fake_forecast_bytes(
                    initialization_model="IFS"
                ),
            }
        )
        with pytest.raises(GraphcastAcquisitionError, match="initialization_model"):
            probe_grid(fs=fs, inits=inits)
