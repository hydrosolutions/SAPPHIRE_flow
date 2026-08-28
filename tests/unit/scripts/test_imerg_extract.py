"""Plan 211 (M-A5b) task T2 — hourly aggregation (D3/D4/D5), point
extraction reusing `era5_extract`'s operators, and the D9 publish/discover
cycle. Entirely no-network: granules are synthetic h5py fixtures carrying
the structure observed on the real GES DISC probe granule.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import h5py
import numpy as np
import polars as pl
import pytest

from scripts.dhm_precip import imerg_extract as ie
from scripts.dhm_precip.domain_types import (
    ExtractionOperator,
    Station,
    StationCoordinate,
    StationCoordinateTable,
    VerticalDatum,
)
from scripts.dhm_precip.era5_errors import ExtractionPostConditionError

if TYPE_CHECKING:
    from pathlib import Path


def _write_fake_granule(
    path: Path,
    *,
    start: datetime,
    fill_value: float = -9999.9,
    lat_count: int = 1800,
    lon_count: int = 3600,
    value: float = 1.0,
    value_at: tuple[float, float] | None = None,
) -> None:
    """The REAL global grid shape (1, 3600, 1800) — `ImergReadContract`'s
    `__post_init__` pins the exact D1 grid shape, so a downsized fixture
    would fail contract validation before the test ever reaches its own
    assertion. `value_at=(lat, lon)` sets one cell (nearest to that
    coordinate) to `value`, everything else stays at `fill_value`; omit it
    to fill the whole grid with `value`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lat = np.round(np.linspace(-89.95, 89.95, lat_count), 6).astype(np.float32)
    lon = np.round(np.linspace(-179.95, 179.95, lon_count), 6).astype(np.float32)
    if value_at is None:
        precip = np.full((1, lon_count, lat_count), value, dtype=np.float32)
    else:
        precip = np.full((1, lon_count, lat_count), fill_value, dtype=np.float32)
        target_lat, target_lon = value_at
        i = int(np.argmin(np.abs(lon - target_lon)))
        j = int(np.argmin(np.abs(lat - target_lat)))
        precip[0, i, j] = value
    with h5py.File(path, "w") as f:
        grid = f.create_group("Grid")
        grid.attrs["GridHeader"] = (
            b"BinMethod=ARITHMETIC_MEAN;\nRegistration=CENTER;\n"
            b"LatitudeResolution=0.1;\nLongitudeResolution=0.1;\n"
            b"Origin=SOUTHWEST;\n"
        )
        grid.create_dataset("lat", data=lat)
        grid.create_dataset("lon", data=lon)
        ds = grid.create_dataset("precipitation", data=precip)
        ds.attrs["DimensionNames"] = b"time,lon,lat"
        ds.attrs["units"] = b"mm/hr"
        ds.attrs["_FillValue"] = fill_value


def _granule_filename(start: datetime, *, revision: str = "V07C") -> str:
    from scripts.dhm_precip.imerg_acquire import ImergGranuleId

    return ImergGranuleId(start=start).filename(revision=revision)


def _station_table() -> StationCoordinateTable:
    return StationCoordinateTable(
        by_station={
            Station("A"): StationCoordinate(
                station=Station("A"), excel_col="A", lat=27.05, lon=85.05, elev_m=1200.0
            )
        }
    )


# --- D3/D4/D5 — half-hourly -> hourly aggregation, the acceptance-critical
# core of this task ---


class TestAggregateHalfHourlyToHourly:
    def test_two_finite_half_hours_average_to_the_hourly_rate(self) -> None:
        first_hour = datetime(2020, 1, 1, 5, 0, tzinfo=UTC)
        half_hourly = {
            first_hour - timedelta(hours=1): 2.0,
            first_hour - timedelta(minutes=30): 4.0,
        }
        valid_time, values, counts = ie.aggregate_half_hourly_to_hourly(
            half_hourly, first_hour=first_hour, last_hour=first_hour
        )
        assert values[0] == pytest.approx(3.0)
        assert counts[0] == 2  # noqa: PLR2004

    def test_an_hour_with_exactly_one_granule_is_nan_with_granule_count_1(
        self,
    ) -> None:
        """D4 (locking): 'An hour with fewer than two granules is NaN' —
        never averaged from the single value present, never treated as the
        hourly rate on its own."""
        hour = datetime(2020, 1, 1, 5, 0, tzinfo=UTC)
        half_hourly = {hour - timedelta(hours=1): 7.0}  # only the FIRST half
        _valid_time, values, counts = ie.aggregate_half_hourly_to_hourly(
            half_hourly, first_hour=hour, last_hour=hour
        )
        assert np.isnan(values[0])
        assert counts[0] == 1

    def test_an_hour_with_zero_granules_is_nan_with_granule_count_0(self) -> None:
        hour = datetime(2020, 1, 1, 5, 0, tzinfo=UTC)
        _valid_time, values, counts = ie.aggregate_half_hourly_to_hourly(
            {}, first_hour=hour, last_hour=hour
        )
        assert np.isnan(values[0])
        assert counts[0] == 0

    def test_period_ending_mapping_including_the_first_hour_of_the_axis(
        self,
    ) -> None:
        """D5 (locking): hour `t` is built from the granules whose `E`
        falls in `(t-1, t]` — i.e. starting at `t-1h` and `t-30min`. Proven
        at the STUDY AXIS's own first hour (2020-01-01 00:00), which needs
        the two granules of 2019-12-31 23:00 and 23:30 (D5's "the FIRST
        hour needs the two granules of 2019-12-31 23:00-24:00")."""
        from scripts.dhm_precip.imerg_acquire import FIRST_GRANULE_START

        first_axis_hour = datetime(2020, 1, 1, 0, 0, tzinfo=UTC)
        half_hourly = {
            FIRST_GRANULE_START: 1.0,  # 2019-12-31 23:00
            FIRST_GRANULE_START + timedelta(minutes=30): 3.0,  # 2019-12-31 23:30
        }
        valid_time, values, counts = ie.aggregate_half_hourly_to_hourly(
            half_hourly, first_hour=first_axis_hour, last_hour=first_axis_hour
        )
        assert valid_time[0] == np.datetime64("2020-01-01T00:00:00")
        assert values[0] == pytest.approx(2.0)
        assert counts[0] == 2  # noqa: PLR2004

    def test_a_granule_shifted_one_hour_earlier_does_not_contribute(self) -> None:
        """The period-ending window is exactly `(t-1, t]` — a granule that
        starts one hour too early must NOT be picked up as if it were
        in-window (guards against an off-by-one hour shift, D5's own
        concern: 'an unstated timestamp convention already cost this track
        a phase difference')."""
        hour = datetime(2020, 1, 1, 5, 0, tzinfo=UTC)
        half_hourly = {
            hour - timedelta(hours=2): 99.0,  # one hour too early
            hour - timedelta(minutes=30): 4.0,  # correct
        }
        _valid_time, values, counts = ie.aggregate_half_hourly_to_hourly(
            half_hourly, first_hour=hour, last_hour=hour
        )
        assert np.isnan(values[0])
        assert counts[0] == 1

    def test_values_are_rounded_to_9_decimal_places(self) -> None:
        hour = datetime(2020, 1, 1, 5, 0, tzinfo=UTC)
        half_hourly = {
            hour - timedelta(hours=1): 1.0 / 3.0,
            hour - timedelta(minutes=30): 1.0 / 3.0,
        }
        _valid_time, values, _counts = ie.aggregate_half_hourly_to_hourly(
            half_hourly, first_hour=hour, last_hour=hour
        )
        assert values[0] == round(1.0 / 3.0, 9)

    def test_complete_axis_has_one_row_per_hour_never_omitted(self) -> None:
        first_hour = datetime(2020, 1, 1, 0, 0, tzinfo=UTC)
        last_hour = datetime(2020, 1, 1, 3, 0, tzinfo=UTC)
        valid_time, values, counts = ie.aggregate_half_hourly_to_hourly(
            {}, first_hour=first_hour, last_hour=last_hour
        )
        assert len(valid_time) == len(values) == len(counts) == 4  # noqa: PLR2004


# --- per-granule reading, reusing era5_extract's nearest/bilinear (D2) ---


class TestReadGranule:
    def test_reshapes_into_the_layout_era5_extract_operators_expect(
        self, tmp_path: Path
    ) -> None:
        start = datetime(2026, 8, 28, 7, 0, tzinfo=UTC)
        path = tmp_path / _granule_filename(start)
        _write_fake_granule(path, start=start, value=2.5)
        ds, contract = ie.read_granule(path)
        assert set(ds.coords) == {"valid_time", "latitude", "longitude"}
        assert ds["valid_time"].values[0] == np.datetime64("2026-08-28T07:00:00")
        assert contract.granule_revision == "V07C"

    def test_fill_value_becomes_nan(self, tmp_path: Path) -> None:
        start = datetime(2026, 8, 28, 7, 0, tzinfo=UTC)
        path = tmp_path / _granule_filename(start)
        _write_fake_granule(path, start=start, value=-9999.9)
        ds, _contract = ie.read_granule(path)
        assert bool(np.isnan(ds["precipitation"].values).all())

    def test_nearest_extraction_reuses_era5_extract_verbatim(
        self, tmp_path: Path
    ) -> None:
        from scripts.dhm_precip.era5_extract import extract_nearest_series

        start = datetime(2026, 8, 28, 7, 0, tzinfo=UTC)
        path = tmp_path / _granule_filename(start)
        _write_fake_granule(path, start=start, value=3.5, value_at=(27.05, 85.05))
        ds, _contract = ie.read_granule(path)
        coord = StationCoordinate(
            station=Station("A"), excel_col="A", lat=27.05, lon=85.05, elev_m=1200.0
        )
        series = extract_nearest_series(ds, coord, variable="precipitation")
        assert series.values[0] == pytest.approx(3.5)


# --- D9 storage/identity/manifest primitives ---


class TestStorageLayout:
    def test_points_root_is_under_imerg_early_never_era5_land(
        self, tmp_path: Path
    ) -> None:
        root = ie.imerg_points_root(tmp_path)
        assert "imerg_early" in root.parts
        assert "era5_land" not in root.parts

    def test_allocate_published_dir_numbers_start_at_zero_and_increment(
        self, tmp_path: Path
    ) -> None:
        root = ie.imerg_points_root(tmp_path)
        first = ie.allocate_published_dir(root, identity="aaaa")
        second = ie.allocate_published_dir(root, identity="bbbb")
        assert first.name.startswith("0000-")
        assert second.name.startswith("0001-")

    def test_a_bundle_with_an_invalid_number_does_not_get_its_number_reused(
        self, tmp_path: Path
    ) -> None:
        root = ie.imerg_points_root(tmp_path)
        first = ie.allocate_published_dir(root, identity="aaaa")
        (first / "junk.txt").write_text("not a manifest")  # never published
        second = ie.allocate_published_dir(root, identity="bbbb")
        assert second.name.startswith("0001-")


def _write_bundle_payload(
    directory: Path, *, station_count: int = 1, hour_count: int = 2
) -> dict[str, str]:
    from scripts.dhm_precip.era5_manifest import checksum_file

    stations = [f"S{i}" for i in range(station_count)]
    timestamps = [
        str(np.datetime64("2020-01-01T00:00:00") + np.timedelta64(h, "h"))
        for h in range(hour_count)
    ]
    rows = [
        {
            "station_id": s,
            "timestamp_utc": t,
            "precip_mm_per_h": 1.0,
            "granule_count": 2,
        }
        for s in stations
        for t in timestamps
    ]
    pl.DataFrame(rows).write_csv(directory / ie._NEAREST_SERIES_FILENAME)
    pl.DataFrame(
        [
            {
                "station": s,
                "lat": 27.0,
                "lon": 85.0,
                "grid_lat": 27.05,
                "grid_lon": 85.05,
                "station_elev_m": 1000.0,
                "station_elevation_datum": "UNKNOWN",
            }
            for s in stations
        ]
    ).write_csv(directory / ie._STATION_CELL_FILENAME)
    pl.DataFrame({"scope": ["STATION"], "station": [stations[0]]}).write_csv(
        directory / ie._SENSITIVITY_FILENAME
    )
    return {name: checksum_file(directory / name) for name in ie.IMERG_PAYLOAD_FILES}


class TestPublishAndDiscover:
    def test_publish_then_discover_round_trips(self, tmp_path: Path) -> None:
        data_root = tmp_path / "data_root"
        root = ie.imerg_points_root(data_root)
        identity = "deadbeef"
        staged = ie.prepare_staging_dir(root, identity=identity)
        payload_sha256s = _write_bundle_payload(staged, station_count=1, hour_count=2)
        manifest = ie.ImergExtractionManifest(
            extraction_identity=identity,
            operator_id=str(ExtractionOperator.NEAREST),
            coordinate_table_sha256="abc",
            route="GES DISC HTTPS archive",
            collection_short_name="GPM_3IMERGHHE_07",
            granule_revision="V07C",
            window_start=datetime(2019, 12, 31, 23, 0, tzinfo=UTC),
            window_end=datetime(2025, 12, 31, 22, 30, tzinfo=UTC),
            box=(31, 80, 26, 89),
            read_contract={"units": "mm/hr"},
            retrospective=True,
            measured_acquisition_latency="4h20m-4h50m",
            payload_sha256s=payload_sha256s,
            station_accounting={
                "S0": {
                    "n_hours": 2,
                    "n_hours_complete": 2,
                    "n_hours_partial": 0,
                    "n_hours_missing_granule": 0,
                    "n_nan_hours": 0,
                }
            },
            n_stations=1,
            n_hours=2,
            generated_at=datetime(2026, 8, 28, tzinfo=UTC),
        )
        ie._write_manifest(manifest, staged / ie.manifest_filename())
        published = ie.publish_imerg_bundle(
            staged,
            data_root=data_root,
            identity=identity,
            expected_station_count=1,
            expected_hour_count=2,
        )
        assert published.exists()

        found_dir, found_manifest = ie.discover_imerg_bundle(
            data_root, expected_station_count=1, expected_hour_count=2
        )
        assert found_dir == published
        assert found_manifest.extraction_identity == identity
        assert found_manifest.retrospective is True

    def test_publish_refuses_a_bundle_not_marked_retrospective(
        self, tmp_path: Path
    ) -> None:
        data_root = tmp_path / "data_root"
        root = ie.imerg_points_root(data_root)
        identity = "cafef00d"
        staged = ie.prepare_staging_dir(root, identity=identity)
        payload_sha256s = _write_bundle_payload(staged, station_count=1, hour_count=1)
        manifest = ie.ImergExtractionManifest(
            extraction_identity=identity,
            operator_id=str(ExtractionOperator.NEAREST),
            coordinate_table_sha256="abc",
            route="GES DISC HTTPS archive",
            collection_short_name="GPM_3IMERGHHE_07",
            granule_revision="V07C",
            window_start=datetime(2019, 12, 31, 23, 0, tzinfo=UTC),
            window_end=datetime(2025, 12, 31, 22, 30, tzinfo=UTC),
            box=(31, 80, 26, 89),
            read_contract={},
            retrospective=False,  # D7 violation
            measured_acquisition_latency="4h20m-4h50m",
            payload_sha256s=payload_sha256s,
            station_accounting={"S0": {"n_hours": 1}},
            n_stations=1,
            n_hours=1,
            generated_at=datetime(2026, 8, 28, tzinfo=UTC),
        )
        ie._write_manifest(manifest, staged / ie.manifest_filename())
        with pytest.raises(ExtractionPostConditionError, match="RETROSPECTIVE"):
            ie.publish_imerg_bundle(
                staged,
                data_root=data_root,
                identity=identity,
                expected_station_count=1,
                expected_hour_count=1,
            )

    def test_discovery_finds_nothing_when_the_root_is_absent(
        self, tmp_path: Path
    ) -> None:
        from scripts.dhm_precip.era5_errors import ExtractionInputAbsentError

        with pytest.raises(ExtractionInputAbsentError):
            ie.discover_imerg_bundle(
                tmp_path / "nope", expected_station_count=1, expected_hour_count=1
            )

    def test_validate_rejects_a_granule_count_precip_inconsistency(
        self, tmp_path: Path
    ) -> None:
        """D4/D9 — a row claiming granule_count < 2 but a non-null value (or
        the reverse) must never validate — it would be invented or
        wrongly-discarded data."""
        from scripts.dhm_precip.era5_manifest import checksum_file

        directory = tmp_path / "bundle"
        directory.mkdir()
        pl.DataFrame(
            {
                "station_id": ["S0"],
                "timestamp_utc": ["2020-01-01T00:00:00"],
                "precip_mm_per_h": [1.5],
                "granule_count": [1],  # inconsistent: partial but has a value
            }
        ).write_csv(directory / ie._NEAREST_SERIES_FILENAME)
        pl.DataFrame(
            [
                {
                    "station": "S0",
                    "lat": 27.0,
                    "lon": 85.0,
                    "grid_lat": 27.05,
                    "grid_lon": 85.05,
                    "station_elev_m": 1000.0,
                    "station_elevation_datum": "UNKNOWN",
                }
            ]
        ).write_csv(directory / ie._STATION_CELL_FILENAME)
        pl.DataFrame({"scope": ["STATION"]}).write_csv(
            directory / ie._SENSITIVITY_FILENAME
        )
        payload_sha256s = {
            name: checksum_file(directory / name) for name in ie.IMERG_PAYLOAD_FILES
        }
        manifest = ie.ImergExtractionManifest(
            extraction_identity="idididid",
            operator_id=str(ExtractionOperator.NEAREST),
            coordinate_table_sha256="abc",
            route="GES DISC HTTPS archive",
            collection_short_name="GPM_3IMERGHHE_07",
            granule_revision="V07C",
            window_start=datetime(2019, 12, 31, 23, 0, tzinfo=UTC),
            window_end=datetime(2025, 12, 31, 22, 30, tzinfo=UTC),
            box=(31, 80, 26, 89),
            read_contract={},
            retrospective=True,
            measured_acquisition_latency="4h20m-4h50m",
            payload_sha256s=payload_sha256s,
            station_accounting={"S0": {"n_hours": 1}},
            n_stations=1,
            n_hours=1,
            generated_at=datetime(2026, 8, 28, tzinfo=UTC),
        )
        renamed = directory.parent / "0000-idididid"
        directory.rename(renamed)
        with pytest.raises(ExtractionPostConditionError, match="disagree"):
            ie.validate_imerg_bundle(
                renamed, manifest, expected_station_count=1, expected_hour_count=1
            )


class TestBuildStationCellTable:
    def test_records_cell_centre_and_station_elevation_no_grid_indices(
        self,
    ) -> None:
        stations = _station_table()
        rows = ie.build_station_cell_table(
            stations, nearest_by_station={Station("A"): (27.05, 85.05)}
        )
        assert len(rows) == 1
        row = rows[0]
        assert row.grid_lat == pytest.approx(27.05)
        assert row.grid_lon == pytest.approx(85.05)
        assert row.station_elev_m == pytest.approx(1200.0)
        assert row.station_elevation_datum == VerticalDatum.UNKNOWN
        assert not hasattr(row, "grid_i")
        assert not hasattr(row, "grid_j")
