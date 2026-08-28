"""Plan 211 (M-A5b) task T2 — hourly aggregation (D3/D4/D5), point
extraction reusing `era5_extract`'s operators, and the D9 publish/discover
cycle. Entirely no-network: granules are synthetic h5py fixtures carrying
the structure observed on the real GES DISC probe granule.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import h5py
import numpy as np
import polars as pl
import pytest

from scripts.dhm_precip import imerg_acquire as ia
from scripts.dhm_precip import imerg_extract as ie
from scripts.dhm_precip.domain_types import (
    AccumulationConvention,
    ExtractionOperator,
    Station,
    StationCoordinate,
    StationCoordinateTable,
    VerticalDatum,
)
from scripts.dhm_precip.era5_errors import (
    ExtractionInputAbsentError,
    ExtractionPostConditionError,
)
from scripts.dhm_precip.era5_manifest import checksum_file
from scripts.dhm_precip.params import DEFAULT_PARAMS

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
    product_version: str = "07B",
    file_header_filename: str | None = None,
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
        f.attrs["FileHeader"] = (
            f"FileName={file_header_filename or path.name};\n"
            "AlgorithmVersion=3IMERGHH;\n"
            f"ProductVersion={product_version};\n"
        ).encode()
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


def _granule_filename(start: datetime, *, revision: str = "V07B") -> str:
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
        _valid_time, values, counts, nf_counts = ie.aggregate_half_hourly_to_hourly(
            half_hourly, first_hour=first_hour, last_hour=first_hour
        )
        assert values[0] == pytest.approx(3.0)
        assert counts[0] == 2  # noqa: PLR2004
        assert nf_counts[0] == 0

    def test_an_hour_with_exactly_one_granule_is_nan_with_granule_count_1(
        self,
    ) -> None:
        """D4 (locking): 'An hour with fewer than two granules is NaN' —
        never averaged from the single value present, never treated as the
        hourly rate on its own."""
        hour = datetime(2020, 1, 1, 5, 0, tzinfo=UTC)
        half_hourly = {hour - timedelta(hours=1): 7.0}  # only the FIRST half
        _valid_time, values, counts, nf_counts = ie.aggregate_half_hourly_to_hourly(
            half_hourly, first_hour=hour, last_hour=hour
        )
        assert np.isnan(values[0])
        assert counts[0] == 1
        assert nf_counts[0] == 0

    def test_an_hour_with_zero_granules_is_nan_with_granule_count_0(self) -> None:
        hour = datetime(2020, 1, 1, 5, 0, tzinfo=UTC)
        _valid_time, values, counts, nf_counts = ie.aggregate_half_hourly_to_hourly(
            {}, first_hour=hour, last_hour=hour
        )
        assert np.isnan(values[0])
        assert counts[0] == 0
        assert nf_counts[0] == 0

    def test_a_granule_that_exists_but_is_non_finite_counts_as_existing_not_missing(
        self,
    ) -> None:
        """D4 (locking, BLOCKER fix): 'a granule that exists but whose
        station cell is non-finite is likewise NaN, counted SEPARATELY [from
        a missing granule]'. A granule that WAS retrieved and read, but
        whose value at this station's cell is a fill sentinel (already
        converted to NaN by `read_granule`), must still count toward
        `granule_count` — conflating 'exists but non-finite' with 'never
        retrieved' silently under-reports the acquisition's real coverage
        as if the granule were archive-missing."""
        hour = datetime(2020, 1, 1, 5, 0, tzinfo=UTC)
        half_hourly = {
            hour - timedelta(hours=1): 2.0,
            hour - timedelta(minutes=30): float("nan"),  # existing, non-finite
        }
        _valid_time, values, counts, nf_counts = ie.aggregate_half_hourly_to_hourly(
            half_hourly, first_hour=hour, last_hour=hour
        )
        assert counts[0] == 2  # BOTH granules existed  # noqa: PLR2004
        assert nf_counts[0] == 1  # exactly one was non-finite
        assert np.isnan(values[0])  # never averaged from the one finite value

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
        valid_time, values, counts, _nf_counts = ie.aggregate_half_hourly_to_hourly(
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
        _valid_time, values, counts, _nf_counts = ie.aggregate_half_hourly_to_hourly(
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
        _valid_time, values, _counts, _nf_counts = ie.aggregate_half_hourly_to_hourly(
            half_hourly, first_hour=hour, last_hour=hour
        )
        assert values[0] == round(1.0 / 3.0, 9)

    def test_complete_axis_has_one_row_per_hour_never_omitted(self) -> None:
        first_hour = datetime(2020, 1, 1, 0, 0, tzinfo=UTC)
        last_hour = datetime(2020, 1, 1, 3, 0, tzinfo=UTC)
        valid_time, values, counts, nf_counts = ie.aggregate_half_hourly_to_hourly(
            {}, first_hour=first_hour, last_hour=last_hour
        )
        assert (
            len(valid_time) == len(values) == len(counts) == len(nf_counts) == 4  # noqa: PLR2004
        )


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
        assert contract.granule_revision == "V07B"

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

    def test_rejects_a_granule_whose_revision_is_not_the_pinned_v07b(
        self, tmp_path: Path
    ) -> None:
        start = datetime(2026, 8, 28, 7, 0, tzinfo=UTC)
        path = tmp_path / _granule_filename(start, revision="V07C")
        _write_fake_granule(path, start=start, product_version="07C")
        with pytest.raises(
            ia.ImergRevisionMismatchError, match="V07C.*V07B|V07B.*V07C"
        ):
            ie.read_granule(path)


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


_VALID_IDENTITY_INPUTS_KWARGS: dict[str, object] = {
    "operator_id": str(ExtractionOperator.NEAREST),
    "route": "GES DISC HTTPS archive",
    "collection_short_name": "GPM_3IMERGHHE_07",
    "granule_revision": "V07B",
    "requested_window_start": "2019-12-31T23:00:00+00:00",
    "requested_window_end": "2025-12-31T22:30:00+00:00",
    "box": (31, 80, 26, 89),
    "read_contract": {"units": "mm/hr"},
    "jjas_months": (6, 7, 8, 9),
    "djf_months": (12, 1, 2),
    "mam_months": (3, 4, 5),
    "on_months": (10, 11),
    "wet_threshold_mm_per_h": 0.2,
    "wet_threshold_side": ">=",
    "zero_policy": "exclude_zero",
    "quantile_definition": "linear",
    "quantile_grid": (0.5, 0.9, 0.99),
}


def _valid_identity_inputs(
    *,
    coordinate_table_sha256: str = "abc",
    granule_checksums: dict[str, str] | None = None,
) -> ie.ImergIdentityInputs:
    return ie.ImergIdentityInputs(
        coordinate_table_sha256=coordinate_table_sha256,
        granule_checksums=granule_checksums or {},
        **_VALID_IDENTITY_INPUTS_KWARGS,  # type: ignore[arg-type]
    )


def _write_bundle_payload(
    directory: Path,
    *,
    station_count: int = 1,
    hour_count: int = 2,
    nearest_rows: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    stations = [f"S{i}" for i in range(station_count)]
    timestamps = [
        str(np.datetime64("2020-01-01T00:00:00") + np.timedelta64(h, "h"))
        for h in range(hour_count)
    ]
    rows = (
        nearest_rows
        if nearest_rows is not None
        else [
            {
                "station_id": s,
                "timestamp_utc": t,
                "precip_mm_per_h": 1.0,
                "granule_count": 2,
                "non_finite_cell_count": 0,
                "grid_lat": 27.05,
                "grid_lon": 85.05,
                "station_elev_m": 1000.0,
                "station_elevation_datum": "UNKNOWN",
            }
            for s in stations
            for t in timestamps
        ]
    )
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
    pl.DataFrame(
        [
            {
                "scope": "STATION",
                "station": stations[0],
                "season": "JJAS",
                "statistic": "QUANTILE",
                "quantile": 0.5,
                "nearest_value": 1.0,
                "bilinear_value": 1.0,
                "delta_absolute": 0.0,
                "delta_unit": "MM_PER_H",
                "ratio": 1.0,
                "n_hours_common_finite": hour_count,
                "n_hours_excluded": 0,
                "n_wet_nearest": 0,
                "n_wet_bilinear": 0,
                "sign_agreement_fraction": None,
            }
        ]
    ).write_csv(directory / ie._SENSITIVITY_FILENAME)
    return {name: checksum_file(directory / name) for name in ie.IMERG_PAYLOAD_FILES}


def _station_accounting(*, hour_count: int) -> dict[str, object]:
    """Matches `_write_bundle_payload`'s rows (every hour complete and
    finite) — `validate_imerg_bundle` RE-DERIVES this from the published
    CSV, so it has to be the truth, not a plausible-looking dict."""
    return {
        "n_hours": hour_count,
        "n_hours_complete": hour_count,
        "n_hours_partial": 0,
        "n_hours_missing_granule": 0,
        "n_hours_non_finite_cell": 0,
        "n_hours_any_non_finite_cell": 0,
        "n_granules_non_finite_cell": 0,
        "n_nan_hours": 0,
    }


def _bundle_manifest(
    *,
    inputs: ie.ImergIdentityInputs,
    payload_sha256s: dict[str, str],
    hour_count: int = 1,
    station_ids: tuple[str, ...] = ("S0",),
    **overrides: Any,
) -> ie.ImergExtractionManifest:
    """ONE factory for every hand-built extraction manifest here. The
    top-level provenance is DERIVED from `inputs`, so it agrees with the
    hashed identity by construction and each test overrides only the single
    field it is actually about — six near-identical 25-line literals could
    each drift from the identity independently."""
    axis_start = datetime(2020, 1, 1, 0, 0, tzinfo=UTC)
    fields: dict[str, Any] = {
        "extraction_identity": inputs.digest(),
        "operator_id": inputs.operator_id,
        "coordinate_table_sha256": inputs.coordinate_table_sha256,
        "route": inputs.route,
        "collection_short_name": inputs.collection_short_name,
        "granule_revision": inputs.granule_revision,
        "acquisition_window_start": datetime.fromisoformat(
            inputs.requested_window_start
        ),
        "acquisition_window_end": datetime.fromisoformat(inputs.requested_window_end),
        "granules_requested": len(inputs.granule_checksums),
        "granules_retrieved": len(inputs.granule_checksums),
        "granules_missing": (),
        "acquisition_generated_at": datetime(2026, 8, 28, tzinfo=UTC),
        "output_axis_start": axis_start,
        "output_axis_end": axis_start + timedelta(hours=hour_count - 1),
        "timestamp_convention": AccumulationConvention.PERIOD_ENDING,
        "period_ending_convention": ie.EXPECTED_PERIOD_ENDING_CONVENTION,
        "box": inputs.box,
        "read_contract": dict(inputs.read_contract),
        "retrospective": True,
        "measured_acquisition_latency": "4h20m-4h50m",
        "payload_sha256s": payload_sha256s,
        "station_accounting": {
            station: _station_accounting(hour_count=hour_count)
            for station in station_ids
        },
        "identity_inputs": inputs.canonical_payload(),
        "n_stations": len(station_ids),
        "n_hours": hour_count,
        "generated_at": datetime(2026, 8, 28, tzinfo=UTC),
    }
    fields.update(overrides)
    return ie.ImergExtractionManifest(**fields)


class TestPublishAndDiscover:
    def test_publish_then_discover_round_trips(self, tmp_path: Path) -> None:
        data_root = tmp_path / "data_root"
        root = ie.imerg_points_root(data_root)
        inputs = _valid_identity_inputs(granule_checksums={"g.HDF5": "deadbeef"})
        identity = inputs.digest()
        staged = ie.prepare_staging_dir(root, identity=identity)
        payload_sha256s = _write_bundle_payload(staged, station_count=1, hour_count=2)
        manifest = _bundle_manifest(
            inputs=inputs, payload_sha256s=payload_sha256s, hour_count=2
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
        inputs = _valid_identity_inputs()
        identity = inputs.digest()
        staged = ie.prepare_staging_dir(root, identity=identity)
        payload_sha256s = _write_bundle_payload(staged, station_count=1, hour_count=1)
        manifest = _bundle_manifest(
            inputs=inputs,
            payload_sha256s=payload_sha256s,
            retrospective=False,  # D7 violation
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

    def test_publish_refuses_when_the_identity_argument_disagrees_with_the_manifest(
        self, tmp_path: Path
    ) -> None:
        """D9 (locking) — `publish_imerg_bundle` must not publish a bundle
        under a label that disagrees with its own manifest's
        `extraction_identity` — a mismatch would move it under the WRONG
        identity and make it undiscoverable under its true one."""
        data_root = tmp_path / "data_root"
        root = ie.imerg_points_root(data_root)
        inputs = _valid_identity_inputs()
        real_identity = inputs.digest()
        staged = ie.prepare_staging_dir(root, identity=real_identity)
        payload_sha256s = _write_bundle_payload(staged, station_count=1, hour_count=1)
        manifest = _bundle_manifest(inputs=inputs, payload_sha256s=payload_sha256s)
        ie._write_manifest(manifest, staged / ie.manifest_filename())
        with pytest.raises(ExtractionPostConditionError, match="publish identity"):
            ie.publish_imerg_bundle(
                staged,
                data_root=data_root,
                identity="a-different-identity",
                expected_station_count=1,
                expected_hour_count=1,
            )

    def test_discovery_finds_nothing_when_the_root_is_absent(
        self, tmp_path: Path
    ) -> None:
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
        directory = tmp_path / "bundle"
        directory.mkdir()
        pl.DataFrame(
            {
                "station_id": ["S0"],
                "timestamp_utc": ["2020-01-01T00:00:00"],
                "precip_mm_per_h": [1.5],
                "granule_count": [1],  # inconsistent: partial but has a value
                "non_finite_cell_count": [0],
                "grid_lat": [27.05],
                "grid_lon": [85.05],
                "station_elev_m": [1000.0],
                "station_elevation_datum": ["UNKNOWN"],
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
        manifest = _bundle_manifest(
            inputs=_valid_identity_inputs(),
            payload_sha256s=payload_sha256s,
            extraction_identity="idididid",
        )
        renamed = directory.parent / "0000-idididid"
        directory.rename(renamed)
        with pytest.raises(ExtractionPostConditionError, match="disagree"):
            ie.validate_imerg_bundle(
                renamed, manifest, expected_station_count=1, expected_hour_count=1
            )

    def test_validate_rejects_a_directory_name_that_merely_contains_the_identity(
        self, tmp_path: Path
    ) -> None:
        """D9 (locking) — an EXACT `NNNN-<identity>` (or staged
        `<identity>--<token>`) match is required; a directory whose name
        merely CONTAINS the identity as a substring of something else must
        be rejected."""
        directory = tmp_path / "xx-deadbeef-yy"
        directory.mkdir()
        _write_bundle_payload(directory, station_count=1, hour_count=1)
        manifest = _bundle_manifest(
            inputs=_valid_identity_inputs(),
            payload_sha256s={
                name: checksum_file(directory / name) for name in ie.IMERG_PAYLOAD_FILES
            },
            extraction_identity="deadbeef",
        )
        with pytest.raises(
            ExtractionPostConditionError, match="does not exactly match"
        ):
            ie.validate_imerg_bundle(
                directory, manifest, expected_station_count=1, expected_hour_count=1
            )

    def test_validate_rejects_an_extraction_identity_that_does_not_recompute(
        self, tmp_path: Path
    ) -> None:
        """D9/P7a (locking) — a manifest whose `extraction_identity` does
        not match the digest recomputed from its own `identity_inputs` must
        be rejected: otherwise the label and the recorded provenance could
        silently drift apart."""
        data_root = tmp_path / "data_root"
        root = ie.imerg_points_root(data_root)
        inputs = _valid_identity_inputs()
        identity = inputs.digest()
        staged = ie.prepare_staging_dir(root, identity=identity)
        payload_sha256s = _write_bundle_payload(staged, station_count=1, hour_count=1)
        tampered_inputs = inputs.canonical_payload()
        tampered_value_inputs = tampered_inputs["value_inputs"]
        assert isinstance(tampered_value_inputs, dict)
        tampered_value_inputs["operator_id"] = "TAMPERED"
        manifest = _bundle_manifest(
            inputs=inputs,
            payload_sha256s=payload_sha256s,
            identity_inputs=tampered_inputs,
        )
        ie._write_manifest(manifest, staged / ie.manifest_filename())
        with pytest.raises(ExtractionPostConditionError, match="recomputed"):
            ie.validate_imerg_bundle(
                staged, manifest, expected_station_count=1, expected_hour_count=1
            )

    def test_validate_rejects_a_malformed_sensitivity_schema(
        self, tmp_path: Path
    ) -> None:
        """D9/D1a (locking) — a sensitivity CSV missing the required
        columns (a bare `{scope, station}` frame) must be rejected, not
        published as a valid diagnostics artefact."""
        data_root = tmp_path / "data_root"
        root = ie.imerg_points_root(data_root)
        inputs = _valid_identity_inputs()
        identity = inputs.digest()
        staged = ie.prepare_staging_dir(root, identity=identity)
        _write_bundle_payload(staged, station_count=1, hour_count=1)
        # Overwrite with the OLD, malformed shape.
        pl.DataFrame({"scope": ["STATION"], "station": ["S0"]}).write_csv(
            staged / ie._SENSITIVITY_FILENAME
        )
        payload_sha256s = {
            name: checksum_file(staged / name) for name in ie.IMERG_PAYLOAD_FILES
        }
        manifest = _bundle_manifest(inputs=inputs, payload_sha256s=payload_sha256s)
        ie._write_manifest(manifest, staged / ie.manifest_filename())
        with pytest.raises(ExtractionPostConditionError, match="missing column"):
            ie.validate_imerg_bundle(
                staged, manifest, expected_station_count=1, expected_hour_count=1
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


# --- T2 end-to-end through run(), reading a REAL acquisition manifest (D9)
# --- the T1->T2 handoff run() is not allowed to bypass by re-globbing.


def _write_coords_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        [{"station": "A", "excel_col": "A", "lat": 27.05, "lon": 85.05, "elev": 1200.0}]
    ).write_csv(path)


def _complete_acquisition_manifest(
    data_root: Path, *, granule_checksums: dict[str, str]
) -> ia.ImergAcquisitionManifest:
    """A manifest that is COMPLETE **by derivation**, not by label: every
    granule of the pinned D5 window is accounted for — the handful actually
    on disk as retrieved, every other one recorded MISSING.

    ⛔ The previous fixture here declared a two-granule, one-hour window and
    simply set `completeness=COMPLETE`, and the end-to-end test then proved
    a full 52,608-hour bundle could be published from it. That fixture
    rationalised the defect ("completeness is a data field T2 trusts") — but
    a consumer must never accept a field it could have computed, and T2 now
    DERIVES completeness from exactly these contents."""
    retrieved_starts = {
        ia.parse_granule_filename(name)[0].start for name in granule_checksums
    }
    probe = sorted(granule_checksums)[0]
    contract = ia.observe_read_contract(
        ia.imerg_raw_dir(data_root) / probe
    ).as_manifest_dict()
    return ia.ImergAcquisitionManifest(
        route=ia.ROUTE,
        collection_short_name=ia.COLLECTION_SHORT_NAME,
        granule_revision=ia.PINNED_GRANULE_REVISION_PER_PLAN,
        completeness=ia.AcquisitionCompleteness.COMPLETE,
        requested_window_start=ia.FIRST_GRANULE_START,
        requested_window_end=ia.LAST_GRANULE_START,
        box=ia.STUDY_BOX,
        read_contract=contract,
        requested=ia.EXPECTED_GRANULE_COUNT,
        retrieved=len(granule_checksums),
        missing=tuple(
            start.isoformat()
            for start in ia.all_granule_starts()
            if start not in retrieved_starts
        ),
        granule_checksums=granule_checksums,
        granule_retrieved_at={
            name: datetime(2026, 8, 28, tzinfo=UTC) for name in granule_checksums
        },
        retrospective=True,
        generated_at=datetime(2026, 8, 28, tzinfo=UTC),
    )


def _write_complete_acquisition_manifest(
    data_root: Path, *, granule_checksums: dict[str, str]
) -> None:
    ia.write_acquisition_manifest(
        _complete_acquisition_manifest(data_root, granule_checksums=granule_checksums),
        ia.acquisition_manifest_path(data_root),
    )


class TestRunEndToEnd:
    def _write_two_granules(self, data_root: Path) -> dict[str, str]:
        from scripts.dhm_precip.imerg_acquire import (
            FIRST_GRANULE_START,
            ImergGranuleId,
            imerg_raw_dir,
        )

        starts = [FIRST_GRANULE_START, FIRST_GRANULE_START + timedelta(minutes=30)]
        values = [2.0, 4.0]  # mean == 3.0, D5's own first-hour worked example
        checksums: dict[str, str] = {}
        for start, value in zip(starts, values, strict=True):
            granule = ImergGranuleId(start=start)
            filename = granule.filename(revision="V07B")
            path = imerg_raw_dir(data_root) / filename
            _write_fake_granule(path, start=start, value_at=(27.05, 85.05), value=value)
            checksums[filename] = checksum_file(path)
        return checksums

    def test_run_publishes_a_bundle_reading_the_real_acquisition_manifest(
        self, tmp_path: Path
    ) -> None:
        data_root = tmp_path / "data_root"
        checksums = self._write_two_granules(data_root)
        _write_complete_acquisition_manifest(data_root, granule_checksums=checksums)
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)

        args = ie.build_parser().parse_args(["--data-root", str(data_root)])
        code = ie.run(
            args,
            clock=lambda: datetime(2026, 8, 28, tzinfo=UTC),
            coords_path=coords_path,
            expected_stations=frozenset({Station("A")}),
            params=DEFAULT_PARAMS,
        )
        assert code == 0

        found_dir, manifest = ie.discover_imerg_bundle(
            data_root,
            expected_station_count=1,
            expected_hour_count=ie.EXPECTED_HOUR_COUNT,
        )
        assert manifest.retrospective is True
        assert manifest.granule_revision == "V07B"
        assert manifest.route == "GES DISC HTTPS archive"
        # D9 — the bundle manifest IS the extraction record, so it carries
        # the acquisition's counts and gaps, not just its window.
        assert manifest.granules_requested == ia.EXPECTED_GRANULE_COUNT
        assert manifest.granules_retrieved == 2  # noqa: PLR2004
        assert len(manifest.granules_missing) == ia.EXPECTED_GRANULE_COUNT - 2
        assert manifest.acquisition_generated_at == datetime(2026, 8, 28, tzinfo=UTC)

        # D4 — the one hour with two finite granules averages correctly;
        # nulls (never the string "NaN") elsewhere. Polars round-trips a
        # written `None` as an actual null, which `is_not_null()` catches —
        # unlike NumPy NaN written straight into a CSV cell.
        series = pl.read_csv(found_dir / ie._NEAREST_SERIES_FILENAME)
        first_row = series.filter(pl.col("timestamp_utc") == "2020-01-01T00:00:00")
        assert first_row["precip_mm_per_h"][0] == pytest.approx(3.0)
        assert first_row["granule_count"][0] == 2  # noqa: PLR2004
        other_row = series.filter(pl.col("timestamp_utc") == "2020-01-01T05:00:00")
        assert other_row["precip_mm_per_h"].is_null()[0]
        assert other_row["granule_count"][0] == 0

    def test_run_refuses_when_no_acquisition_manifest_exists(
        self, tmp_path: Path
    ) -> None:
        data_root = tmp_path / "data_root"
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)
        args = ie.build_parser().parse_args(["--data-root", str(data_root)])
        with pytest.raises(ExtractionInputAbsentError, match="acquisition manifest"):
            ie.run(
                args,
                coords_path=coords_path,
                expected_stations=frozenset({Station("A")}),
                params=DEFAULT_PARAMS,
            )

    def test_run_refuses_a_probe_manifest(self, tmp_path: Path) -> None:
        """D9 (locking) — 'a one-granule probe manifest can be mistaken for
        a complete acquisition'. T2 must refuse a PROBE outright."""
        from scripts.dhm_precip.imerg_acquire import FIRST_GRANULE_START

        data_root = tmp_path / "data_root"
        checksums = self._write_two_granules(data_root)
        one_name = next(iter(checksums))
        manifest = ia.ImergAcquisitionManifest(
            route="GES DISC HTTPS archive",
            collection_short_name="GPM_3IMERGHHE_07",
            granule_revision="V07B",
            completeness=ia.AcquisitionCompleteness.PROBE,
            requested_window_start=FIRST_GRANULE_START,
            requested_window_end=FIRST_GRANULE_START,
            box=(31, 80, 26, 89),
            read_contract={"units": "mm/hr"},
            requested=1,
            retrieved=1,
            missing=(),
            granule_checksums={one_name: checksums[one_name]},
            granule_retrieved_at={one_name: datetime(2026, 8, 28, tzinfo=UTC)},
            retrospective=True,
            generated_at=datetime(2026, 8, 28, tzinfo=UTC),
        )
        ia.write_acquisition_manifest(manifest, ia.acquisition_manifest_path(data_root))
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)
        args = ie.build_parser().parse_args(["--data-root", str(data_root)])
        with pytest.raises(ia.ImergAcquisitionIncompleteError, match="PROBE"):
            ie.run(
                args,
                coords_path=coords_path,
                expected_stations=frozenset({Station("A")}),
                params=DEFAULT_PARAMS,
            )

    def test_run_refuses_a_granule_whose_checksum_disagrees_with_the_manifest(
        self, tmp_path: Path
    ) -> None:
        """D9 (locking) — T2 must not silently extract a file that was
        modified after T1 acquired it; it cross-checks every granule's
        checksum against the acquisition manifest rather than trusting
        whatever bytes happen to be on disk."""
        data_root = tmp_path / "data_root"
        checksums = self._write_two_granules(data_root)
        _write_complete_acquisition_manifest(data_root, granule_checksums=checksums)
        from scripts.dhm_precip.imerg_acquire import imerg_raw_dir

        tampered_name = next(iter(checksums))
        (imerg_raw_dir(data_root) / tampered_name).write_bytes(b"corrupted")
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)
        args = ie.build_parser().parse_args(["--data-root", str(data_root)])
        with pytest.raises(ExtractionPostConditionError, match="checksum"):
            ie.run(
                args,
                coords_path=coords_path,
                expected_stations=frozenset({Station("A")}),
                params=DEFAULT_PARAMS,
            )

    def test_run_never_globs_the_raw_directory_independently(
        self, tmp_path: Path
    ) -> None:
        """D9 (locking) — an EXTRA raw file that was never part of the
        acquisition (e.g. left over from a different run) must not silently
        enter the extraction just because it happens to sit in the raw
        directory; only files the acquisition manifest actually lists are
        read."""
        from scripts.dhm_precip.imerg_acquire import (
            FIRST_GRANULE_START,
            ImergGranuleId,
            imerg_raw_dir,
        )

        data_root = tmp_path / "data_root"
        checksums = self._write_two_granules(data_root)
        _write_complete_acquisition_manifest(data_root, granule_checksums=checksums)
        # An UNLISTED third granule physically present under raw/.
        extra_start = FIRST_GRANULE_START + timedelta(hours=6)
        extra_path = imerg_raw_dir(data_root) / ImergGranuleId(
            start=extra_start
        ).filename(revision="V07B")
        _write_fake_granule(extra_path, start=extra_start, value=999.0)

        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)
        args = ie.build_parser().parse_args(["--data-root", str(data_root)])
        code = ie.run(
            args,
            clock=lambda: datetime(2026, 8, 28, tzinfo=UTC),
            coords_path=coords_path,
            expected_stations=frozenset({Station("A")}),
            params=DEFAULT_PARAMS,
        )
        assert code == 0
        found_dir, _manifest = ie.discover_imerg_bundle(
            data_root,
            expected_station_count=1,
            expected_hour_count=ie.EXPECTED_HOUR_COUNT,
        )
        # Only the TWO manifest-listed granules were read — the extra
        # unlisted one never contributed a value at hour 06:00.
        series = pl.read_csv(found_dir / ie._NEAREST_SERIES_FILENAME)
        row = series.filter(pl.col("timestamp_utc") == "2020-01-01T06:00:00")
        assert row["granule_count"][0] == 0
        assert row["precip_mm_per_h"].is_null()[0]

    def test_out_copies_the_published_bundle_and_is_deterministic_across_runs(
        self, tmp_path: Path
    ) -> None:
        """D8/plan Verify (locking) — `--out <dir>` must be the FULL bundle
        (manifest + payload CSVs), and two runs over the same inputs must be
        byte-identical except the manifest's own `generated_at`."""
        data_root = tmp_path / "data_root"
        checksums = self._write_two_granules(data_root)
        _write_complete_acquisition_manifest(data_root, granule_checksums=checksums)
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)

        dir_a = tmp_path / "out_a"
        dir_b = tmp_path / "out_b"
        for out_dir, clock_value in (
            (dir_a, datetime(2026, 8, 28, 1, tzinfo=UTC)),
            (dir_b, datetime(2026, 8, 28, 2, tzinfo=UTC)),
        ):
            args = ie.build_parser().parse_args(
                ["--data-root", str(data_root), "--out", str(out_dir)]
            )
            code = ie.run(
                args,
                clock=lambda cv=clock_value: cv,
                coords_path=coords_path,
                expected_stations=frozenset({Station("A")}),
                params=DEFAULT_PARAMS,
            )
            assert code == 0

        assert dir_a.exists()
        assert dir_b.exists()
        assert (dir_a / ie.manifest_filename()).stat().st_size > 0
        for name in ie.IMERG_PAYLOAD_FILES:
            assert (dir_a / name).read_bytes() == (dir_b / name).read_bytes()

        import json as _json

        manifest_a = _json.loads((dir_a / ie.manifest_filename()).read_text())
        manifest_b = _json.loads((dir_b / ie.manifest_filename()).read_text())
        assert manifest_a["generated_at"] != manifest_b["generated_at"]
        del manifest_a["generated_at"]
        del manifest_b["generated_at"]
        assert manifest_a == manifest_b


# --- main() must catch a mid-extraction D1 read-contract/revision
# violation, not let it escape as a raw traceback (fixer round 2) ---


class TestMainCatchesMidExtractionD1Violation:
    def test_a_second_granule_header_revision_mismatch_is_reported_not_raised(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A granule whose PATH carries the D1-pinned revision (V07B) but
        whose own embedded `FileHeader.FileName` says V07C — a renamed
        file — raises `ImergReadContractError` out of `read_granule()`
        while T2 reads the SECOND granule, after the first has already
        frozen the read contract. `main()` must catch this (via the broad
        `ImergAcquisitionError`) and report it as a structured
        `imerg_extract.cli.failed` log line with a defined exit code —
        never as an unhandled traceback.

        ⛔ NOT `FileHeader.ProductVersion`: the 2026-08-28 probe measured
        `ProductVersion=07B` on a granule whose filename read V07C, so the
        two are different axes and equating them would reject legitimate
        granules. `FileName` is the same axis as the path, so a
        disagreement there is a real defect."""
        from scripts.dhm_precip.imerg_acquire import (
            FIRST_GRANULE_START,
            ImergGranuleId,
            imerg_raw_dir,
        )

        data_root = tmp_path / "data_root"
        starts = [FIRST_GRANULE_START, FIRST_GRANULE_START + timedelta(minutes=30)]
        # The SECOND granule's embedded FileHeader.FileName disagrees with
        # its own (still D1-pinned) path revision.
        checksums: dict[str, str] = {}
        for index, start in enumerate(starts):
            filename = ImergGranuleId(start=start).filename(revision="V07B")
            path = imerg_raw_dir(data_root) / filename
            _write_fake_granule(
                path,
                start=start,
                value_at=(27.05, 85.05),
                value=2.0,
                file_header_filename=(
                    ImergGranuleId(start=start).filename(revision="V07C")
                    if index == 1
                    else None
                ),
            )
            checksums[filename] = checksum_file(path)
        _write_complete_acquisition_manifest(data_root, granule_checksums=checksums)
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)

        exit_code = ie.main(
            ["--data-root", str(data_root)],
            clock=lambda: datetime(2026, 8, 28, tzinfo=UTC),
            coords_path=coords_path,
            expected_stations=frozenset({Station("A")}),
            params=DEFAULT_PARAMS,
        )

        assert exit_code not in (0, None)
        captured = capsys.readouterr()
        assert "imerg_extract.cli.failed" in captured.err
        assert "ImergReadContractError" in captured.err


# --- D9 (BLOCKER) — T2 DERIVES completeness; it never trusts the label ---


def _nearest_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "station_id": "S0",
        "timestamp_utc": "2020-01-01T00:00:00",
        "precip_mm_per_h": 1.0,
        "granule_count": 2,
        "non_finite_cell_count": 0,
        "grid_lat": 27.05,
        "grid_lon": 85.05,
        "station_elev_m": 1000.0,
        "station_elevation_datum": "UNKNOWN",
    }
    row.update(overrides)
    return row


def _stage_bundle(
    tmp_path: Path,
    *,
    hour_count: int = 1,
    nearest_rows: list[dict[str, Any]] | None = None,
    **manifest_overrides: Any,
) -> tuple[Path, ie.ImergExtractionManifest]:
    """A staged bundle that validates as published, so each test can break
    exactly ONE thing about it."""
    root = ie.imerg_points_root(tmp_path / "data_root")
    inputs = _valid_identity_inputs(granule_checksums={"g.HDF5": "deadbeef"})
    staged = ie.prepare_staging_dir(root, identity=inputs.digest())
    payload_sha256s = _write_bundle_payload(
        staged, station_count=1, hour_count=hour_count, nearest_rows=nearest_rows
    )
    manifest = _bundle_manifest(
        inputs=inputs,
        payload_sha256s=payload_sha256s,
        hour_count=hour_count,
        **manifest_overrides,
    )
    ie._write_manifest(manifest, staged / ie.manifest_filename())
    return staged, manifest


class TestT2DerivesAcquisitionCompleteness:
    def test_a_two_granule_manifest_labelled_complete_publishes_nothing(
        self, tmp_path: Path
    ) -> None:
        """⛔ THE BLOCKER, locked. The previous fixture built exactly this —
        a two-granule, one-hour acquisition manifest with
        `completeness=COMPLETE` — and the end-to-end test proved it
        published a full 52,608-hour bundle. T2 must now reject it BEFORE
        opening any granule file, and publish nothing."""
        from scripts.dhm_precip.imerg_acquire import (
            FIRST_GRANULE_START,
            ImergGranuleId,
            imerg_raw_dir,
        )

        data_root = tmp_path / "data_root"
        starts = [FIRST_GRANULE_START, FIRST_GRANULE_START + timedelta(minutes=30)]
        checksums: dict[str, str] = {}
        for start, value in zip(starts, [2.0, 4.0], strict=True):
            filename = ImergGranuleId(start=start).filename(revision="V07B")
            path = imerg_raw_dir(data_root) / filename
            _write_fake_granule(path, start=start, value_at=(27.05, 85.05), value=value)
            checksums[filename] = checksum_file(path)
        honest = _complete_acquisition_manifest(data_root, granule_checksums=checksums)
        # The SAME granules, but the window is claimed to be just those two.
        claimed = honest.model_copy(
            update={
                "requested_window_end": starts[-1],
                "requested": 2,
                "retrieved": 2,
                "missing": (),
            }
        )
        ia.write_acquisition_manifest(claimed, ia.acquisition_manifest_path(data_root))

        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)
        args = ie.build_parser().parse_args(["--data-root", str(data_root)])
        with pytest.raises(
            ia.ImergAcquisitionIncompleteError, match="105216|unaccounted"
        ):
            ie.run(
                args,
                clock=lambda: datetime(2026, 8, 28, tzinfo=UTC),
                coords_path=coords_path,
                expected_stations=frozenset({Station("A")}),
                params=DEFAULT_PARAMS,
            )
        assert not ie.imerg_points_root(data_root).exists()

    def test_a_read_contract_that_drifted_since_acquisition_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """D1/D9 — the granule T2 reads must carry the WHOLE contract T1
        recorded, not merely a matching revision letter."""
        from scripts.dhm_precip.imerg_acquire import (
            FIRST_GRANULE_START,
            ImergGranuleId,
            imerg_raw_dir,
        )

        data_root = tmp_path / "data_root"
        starts = [FIRST_GRANULE_START, FIRST_GRANULE_START + timedelta(minutes=30)]
        checksums: dict[str, str] = {}
        for start in starts:
            filename = ImergGranuleId(start=start).filename(revision="V07B")
            path = imerg_raw_dir(data_root) / filename
            _write_fake_granule(path, start=start, value_at=(27.05, 85.05), value=2.0)
            checksums[filename] = checksum_file(path)
        manifest = _complete_acquisition_manifest(
            data_root, granule_checksums=checksums
        )
        drifted = dict(manifest.read_contract)
        lat = drifted["lat_vector"]
        assert isinstance(lat, tuple | list)
        drifted["lat_vector"] = [float(lat[0]) + 1e-9, *[float(v) for v in lat[1:]]]
        ia.write_acquisition_manifest(
            manifest.model_copy(update={"read_contract": drifted}),
            ia.acquisition_manifest_path(data_root),
        )
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)
        args = ie.build_parser().parse_args(["--data-root", str(data_root)])
        with pytest.raises(ExtractionPostConditionError, match="read contract"):
            ie.run(
                args,
                clock=lambda: datetime(2026, 8, 28, tzinfo=UTC),
                coords_path=coords_path,
                expected_stations=frozenset({Station("A")}),
                params=DEFAULT_PARAMS,
            )


# --- D4/D9 — the primary series validator's numeric contract ---


class TestPrimarySeriesNumericContract:
    def test_a_null_granule_count_is_rejected(self, tmp_path: Path) -> None:
        """`~col.is_in([0, 1, 2])` is NULL for a null cell, and `filter`
        drops nulls — so a null count slipped through every range check."""
        staged, manifest = _stage_bundle(
            tmp_path,
            nearest_rows=[_nearest_row(granule_count=None, precip_mm_per_h=None)],
        )
        with pytest.raises(ExtractionPostConditionError, match="null value"):
            ie.validate_imerg_bundle(
                staged, manifest, expected_station_count=1, expected_hour_count=1
            )

    def test_a_nan_precip_on_a_nominally_complete_hour_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """Nullness is not finiteness: a NaN written into a complete,
        cell-finite hour is not a valid rate."""
        staged, manifest = _stage_bundle(
            tmp_path, nearest_rows=[_nearest_row(precip_mm_per_h=float("nan"))]
        )
        with pytest.raises(ExtractionPostConditionError, match="FINITE rate"):
            ie.validate_imerg_bundle(
                staged, manifest, expected_station_count=1, expected_hour_count=1
            )

    def test_more_non_finite_cells_than_granules_is_rejected(
        self, tmp_path: Path
    ) -> None:
        staged, manifest = _stage_bundle(
            tmp_path,
            nearest_rows=[
                _nearest_row(
                    granule_count=1, non_finite_cell_count=2, precip_mm_per_h=None
                )
            ],
        )
        with pytest.raises(ExtractionPostConditionError, match="0 <= non_finite"):
            ie.validate_imerg_bundle(
                staged, manifest, expected_station_count=1, expected_hour_count=1
            )


# --- D4/D9 — station accounting is RE-DERIVED from the payload ---


class TestStationAccountingIsRederived:
    def test_swapped_totals_that_still_sum_correctly_are_rejected(
        self, tmp_path: Path
    ) -> None:
        """⛔ The old check only asserted the four components SUM to the
        hour count — so any permutation of them passed."""
        swapped = _station_accounting(hour_count=2)
        swapped["n_hours_complete"] = 0
        swapped["n_hours_missing_granule"] = 2
        staged, manifest = _stage_bundle(
            tmp_path, hour_count=2, station_accounting={"S0": swapped}
        )
        with pytest.raises(ExtractionPostConditionError, match="disagrees with the"):
            ie.validate_imerg_bundle(
                staged, manifest, expected_station_count=1, expected_hour_count=2
            )

    def test_a_one_granule_non_finite_hour_reaches_the_non_exclusive_total(
        self,
    ) -> None:
        """⛔ The exclusive `n_hours_non_finite_cell` bucket only counts
        hours where BOTH granules exist, so an hour with a single existing,
        non-finite granule was classified as merely `partial` and vanished
        from every non-finite total."""
        row = ie._station_accounting_row(
            granule_count=np.array([1]), non_finite_cell_count=np.array([1])
        )
        assert row["n_hours_partial"] == 1
        assert row["n_hours_non_finite_cell"] == 0  # exclusive bucket, correctly
        assert row["n_hours_any_non_finite_cell"] == 1
        assert row["n_granules_non_finite_cell"] == 1


# --- D9/P7a — provenance may never contradict the hashed identity ---


class TestManifestProvenanceReconciliation:
    def test_a_top_level_route_that_contradicts_identity_inputs_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """The digest still recomputes — only `identity_inputs` feeds it —
        so nothing else catches a top-level field edited to say anything at
        all."""
        staged, manifest = _stage_bundle(tmp_path, route="a different route")
        with pytest.raises(ExtractionPostConditionError, match="contradicts"):
            ie.validate_imerg_bundle(
                staged, manifest, expected_station_count=1, expected_hour_count=1
            )

    def test_an_output_axis_end_that_disagrees_with_the_payload_is_rejected(
        self, tmp_path: Path
    ) -> None:
        staged, manifest = _stage_bundle(
            tmp_path,
            hour_count=2,
            output_axis_end=datetime(2020, 6, 1, tzinfo=UTC),
        )
        with pytest.raises(ExtractionPostConditionError, match="output_axis"):
            ie.validate_imerg_bundle(
                staged, manifest, expected_station_count=1, expected_hour_count=2
            )

    def test_acquisition_counts_that_do_not_add_up_are_rejected(
        self, tmp_path: Path
    ) -> None:
        """D9 — the bundle carries the acquisition's counts and gaps, and
        they must reconcile with each other and with the granule set the
        identity was hashed over."""
        staged, manifest = _stage_bundle(tmp_path, granules_requested=99)
        with pytest.raises(ExtractionPostConditionError, match="granules_requested"):
            ie.validate_imerg_bundle(
                staged, manifest, expected_station_count=1, expected_hour_count=1
            )


# --- D9 — discovery must SKIP an invalid higher bundle, never abort ---


class TestDiscoverySkipsInvalidBundles:
    def test_a_malformed_higher_bundle_falls_back_to_the_lower_valid_one(
        self, tmp_path: Path
    ) -> None:
        """`_read_manifest` raises `Era5StorageError` on malformed JSON and
        polars raises its own type on a malformed CSV — neither was caught,
        so discovery aborted at the highest NNNN instead of falling back."""
        data_root = tmp_path / "data_root"
        root = ie.imerg_points_root(data_root)
        inputs = _valid_identity_inputs(granule_checksums={"g.HDF5": "deadbeef"})
        staged = ie.prepare_staging_dir(root, identity=inputs.digest())
        payload_sha256s = _write_bundle_payload(staged, station_count=1, hour_count=1)
        manifest = _bundle_manifest(inputs=inputs, payload_sha256s=payload_sha256s)
        ie._write_manifest(manifest, staged / ie.manifest_filename())
        valid = ie.publish_imerg_bundle(
            staged,
            data_root=data_root,
            identity=inputs.digest(),
            expected_station_count=1,
            expected_hour_count=1,
        )

        broken_json = ie.allocate_published_dir(root, identity="bbbb")
        (broken_json / ie.manifest_filename()).write_text("{not json")
        broken_csv = ie.allocate_published_dir(root, identity="cccc")
        shutil.copytree(valid, broken_csv, dirs_exist_ok=True)
        (broken_csv / ie._NEAREST_SERIES_FILENAME).write_bytes(b"\x00\x01 not,a\ncsv")

        found, _found_manifest = ie.discover_imerg_bundle(
            data_root, expected_station_count=1, expected_hour_count=1
        )
        assert found == valid
