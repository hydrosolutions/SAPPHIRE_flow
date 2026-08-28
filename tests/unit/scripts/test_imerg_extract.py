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
    Era5StorageError,
    ExtractionInputAbsentError,
    ExtractionPostConditionError,
)
from scripts.dhm_precip.era5_manifest import checksum_file

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


def _hours(first: datetime, last: datetime) -> tuple[datetime, ...]:
    """A short test axis. The production axis is PINNED (`ie.hourly_axis()`),
    so a sub-window is passed explicitly rather than configured."""
    hours: list[datetime] = []
    current = first
    while current <= last:
        hours.append(current)
        current += timedelta(hours=1)
    return tuple(hours)


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
            half_hourly, hours=_hours(first_hour, first_hour)
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
            half_hourly, hours=_hours(hour, hour)
        )
        assert np.isnan(values[0])
        assert counts[0] == 1
        assert nf_counts[0] == 0

    def test_an_hour_with_zero_granules_is_nan_with_granule_count_0(self) -> None:
        hour = datetime(2020, 1, 1, 5, 0, tzinfo=UTC)
        _valid_time, values, counts, nf_counts = ie.aggregate_half_hourly_to_hourly(
            {}, hours=_hours(hour, hour)
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
            half_hourly, hours=_hours(hour, hour)
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
            half_hourly, hours=_hours(first_axis_hour, first_axis_hour)
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
            half_hourly, hours=_hours(hour, hour)
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
            half_hourly, hours=_hours(hour, hour)
        )
        assert values[0] == round(1.0 / 3.0, 9)

    def test_complete_axis_has_one_row_per_hour_never_omitted(self) -> None:
        first_hour = datetime(2020, 1, 1, 0, 0, tzinfo=UTC)
        last_hour = datetime(2020, 1, 1, 3, 0, tzinfo=UTC)
        valid_time, values, counts, nf_counts = ie.aggregate_half_hourly_to_hourly(
            {}, hours=_hours(first_hour, last_hour)
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
        with pytest.raises(ia.ImergReadContractError, match="V07C.*V07B|V07B.*V07C"):
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


_AT = datetime(2026, 8, 28, tzinfo=UTC)

#: Every hand-built bundle carries the PINNED acquisition accounting: the
#: publish/discovery predicate now derives completeness itself, so a fixture
#: that claimed a two-granule window would be refused — which is the point.
_ACQUISITION_FIELDS: dict[str, Any] = {
    "route": ia.ROUTE,
    "collection_short_name": ia.COLLECTION_SHORT_NAME,
    "granule_revision": ia.PINNED_GRANULE_REVISION_PER_PLAN,
    "acquisition_window_start": ia.FIRST_GRANULE_START,
    "acquisition_window_end": ia.LAST_GRANULE_START,
    "granules_requested": ia.EXPECTED_GRANULE_COUNT,
    "granules_retrieved": ia.EXPECTED_GRANULE_COUNT,
    "granules_missing": (),
    "box": ia.STUDY_BOX,
}

_AXIS: tuple[str, ...] = tuple(
    str(np.datetime64(h.replace(tzinfo=None), "s")) for h in ie.hourly_axis()
)


@pytest.fixture(autouse=True)
def _pin_one_station(monkeypatch: pytest.MonkeyPatch) -> None:
    """`validate_imerg_bundle` PINS Plan 211's 26 stations rather than taking
    a caller's count, so a one-station fixture has to shrink the pin itself —
    deliberately and visibly. `test_a_bundle_with_fewer_stations_than_the_pin_
    is_refused` leaves the real pin in place to prove it is live."""
    monkeypatch.setattr(ie, "EXPECTED_STATION_COUNT", 1)


def _write_bundle_payload(
    directory: Path,
    *,
    station_ids: tuple[str, ...] = ("S0",),
    first_row_overrides: dict[str, Any] | None = None,
) -> dict[str, str]:
    """A payload on the COMPLETE pinned hourly axis — the only shape the
    predicate accepts, since the axis is D5's own and not a parameter.
    `first_row_overrides` breaks exactly one cell of the first row."""
    n = len(_AXIS)
    precip: list[float | None] = [1.0] * n
    granule_count: list[int | None] = [2] * n
    non_finite: list[int | None] = [0] * n
    for key, value in (first_row_overrides or {}).items():
        {
            "precip_mm_per_h": precip,
            "granule_count": granule_count,
            "non_finite_cell_count": non_finite,
        }[key][0] = value
    pl.concat(
        [
            pl.DataFrame(
                {
                    "station_id": [station] * n,
                    "timestamp_utc": list(_AXIS),
                    "precip_mm_per_h": pl.Series(values=precip, dtype=pl.Float64),
                    "granule_count": pl.Series(values=granule_count, dtype=pl.Int64),
                    "non_finite_cell_count": pl.Series(
                        values=non_finite, dtype=pl.Int64
                    ),
                    "grid_lat": [27.05] * n,
                    "grid_lon": [85.05] * n,
                    "station_elev_m": [1000.0] * n,
                    "station_elevation_datum": ["UNKNOWN"] * n,
                }
            )
            for station in station_ids
        ]
    ).write_csv(directory / ie._NEAREST_SERIES_FILENAME)
    pl.DataFrame(
        [
            {
                "station": station,
                "lat": 27.0,
                "lon": 85.0,
                "grid_lat": 27.05,
                "grid_lon": 85.05,
                "station_elev_m": 1000.0,
                "station_elevation_datum": "UNKNOWN",
            }
            for station in station_ids
        ]
    ).write_csv(directory / ie._STATION_CELL_FILENAME)
    pl.DataFrame(
        [
            {
                "scope": "STATION",
                "station": station_ids[0],
                "season": "JJAS",
                "statistic": "QUANTILE",
                "quantile": 0.5,
                "nearest_value": 1.0,
                "bilinear_value": 1.0,
                "delta_absolute": 0.0,
                "delta_unit": "MM_PER_H",
                "ratio": 1.0,
                "n_hours_common_finite": n,
                "n_hours_excluded": 0,
                "n_wet_nearest": 0,
                "n_wet_bilinear": 0,
                "sign_agreement_fraction": None,
            }
        ]
    ).write_csv(directory / ie._SENSITIVITY_FILENAME)
    return {name: checksum_file(directory / name) for name in ie.IMERG_PAYLOAD_FILES}


def _station_accounting(**overrides: Any) -> dict[str, Any]:
    """Matches `_write_bundle_payload`'s default rows (every hour complete and
    finite) — `validate_imerg_bundle` RE-DERIVES this from the published CSV,
    so it has to be the truth, not a plausible-looking dict."""
    row: dict[str, Any] = {
        "n_hours": ie.EXPECTED_HOUR_COUNT,
        "n_hours_complete": ie.EXPECTED_HOUR_COUNT,
        "n_hours_partial": 0,
        "n_hours_missing_granule": 0,
        "n_hours_non_finite_cell": 0,
        "n_hours_any_non_finite_cell": 0,
        "n_granules_non_finite_cell": 0,
        "n_nan_hours": 0,
    }
    row.update(overrides)
    return row


def _bundle_manifest(
    *,
    payload_sha256s: dict[str, str],
    station_ids: tuple[str, ...] = ("S0",),
    **overrides: Any,
) -> ie.ImergExtractionManifest:
    """ONE factory for every hand-built extraction manifest here. The identity
    is DERIVED from the finished manifest (as `run()` derives it), so it agrees
    with the recorded provenance by construction — there is no second copy of
    those inputs for a test to drift against."""
    fields: dict[str, Any] = {
        "extraction_identity": "",
        "operator_id": str(ExtractionOperator.NEAREST),
        "coordinate_table_sha256": "abc",
        "acquisition_record_sha256": "acquisition-record-digest",
        "read_contract_sha256": "read-contract-digest",
        **_ACQUISITION_FIELDS,
        "sensitivity_params": {"quantile_grid": [0.5]},
        "acquisition_generated_at": _AT,
        "output_axis_start": ie.FIRST_HOUR,
        "output_axis_end": ie.LAST_HOUR,
        "timestamp_convention": AccumulationConvention.PERIOD_ENDING,
        "period_ending_convention": ie.EXPECTED_PERIOD_ENDING_CONVENTION,
        "retrospective": True,
        "measured_acquisition_latency": "4h20m-4h50m",
        "payload_sha256s": payload_sha256s,
        "station_accounting": {
            station: _station_accounting() for station in station_ids
        },
        "n_stations": len(station_ids),
        "n_hours": ie.EXPECTED_HOUR_COUNT,
        "generated_at": _AT,
    }
    fields.update(overrides)
    manifest = ie.ImergExtractionManifest(**fields)
    if "extraction_identity" in overrides:
        return manifest
    return manifest.model_copy(
        update={"extraction_identity": ie.imerg_extraction_identity(manifest)}
    )


def _stage_bundle(
    tmp_path: Path,
    *,
    first_row_overrides: dict[str, Any] | None = None,
    **manifest_overrides: Any,
) -> tuple[Path, ie.ImergExtractionManifest]:
    """A staged bundle that validates as published, so each test can break
    exactly ONE thing about it."""
    staged = ie.prepare_staging_dir(ie.imerg_points_root(tmp_path / "data_root"))
    payload_sha256s = _write_bundle_payload(
        staged, first_row_overrides=first_row_overrides
    )
    manifest = _bundle_manifest(payload_sha256s=payload_sha256s, **manifest_overrides)
    ie._write_manifest(manifest, staged / ie.MANIFEST_FILENAME)
    return staged, manifest


class TestPublishAndDiscover:
    def test_publish_then_discover_round_trips(self, tmp_path: Path) -> None:
        data_root = tmp_path / "data_root"
        staged, manifest = _stage_bundle(tmp_path)
        identity = manifest.extraction_identity
        published = ie.publish_imerg_bundle(
            staged, data_root=data_root, identity=identity
        )
        assert published.exists()

        found_dir, found_manifest = ie.discover_imerg_bundle(data_root)
        assert found_dir == published
        assert found_manifest.extraction_identity == identity
        assert found_manifest.retrospective is True

    def test_publish_refuses_a_bundle_not_marked_retrospective(
        self, tmp_path: Path
    ) -> None:
        data_root = tmp_path / "data_root"
        staged, manifest = _stage_bundle(tmp_path, retrospective=False)  # D7 violation
        with pytest.raises(ExtractionPostConditionError, match="RETROSPECTIVE"):
            ie.publish_imerg_bundle(
                staged, data_root=data_root, identity=manifest.extraction_identity
            )

    def test_publish_refuses_when_the_identity_argument_disagrees_with_the_manifest(
        self, tmp_path: Path
    ) -> None:
        """D9 (locking) — `publish_imerg_bundle` must not publish a bundle
        under a label that disagrees with its own manifest's
        `extraction_identity` — a mismatch would move it under the WRONG
        identity and make it undiscoverable under its true one."""
        data_root = tmp_path / "data_root"
        staged, _manifest = _stage_bundle(tmp_path)
        with pytest.raises(ExtractionPostConditionError, match="publish identity"):
            ie.publish_imerg_bundle(
                staged,
                data_root=data_root,
                identity="a-different-identity",
            )

    def test_discovery_finds_nothing_when_the_root_is_absent(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(ExtractionInputAbsentError):
            ie.discover_imerg_bundle(tmp_path / "nope")

    def test_validate_rejects_a_granule_count_precip_inconsistency(
        self, tmp_path: Path
    ) -> None:
        """D4/D9 — a row claiming granule_count < 2 but a non-null value (or
        the reverse) must never validate — it would be invented or
        wrongly-discarded data."""
        staged, manifest = _stage_bundle(
            tmp_path, first_row_overrides={"granule_count": 1}
        )
        with pytest.raises(ExtractionPostConditionError, match="disagree"):
            ie.validate_imerg_bundle(staged, manifest)

    def test_validate_rejects_a_directory_name_that_merely_contains_the_identity(
        self, tmp_path: Path
    ) -> None:
        """D9 (locking) — an EXACT `NNNN-<identity>` match is required of a
        PUBLISHED bundle; a directory whose name merely CONTAINS the identity
        as a substring of something else must be rejected."""
        directory = tmp_path / "xx-deadbeef-yy"
        directory.mkdir()
        manifest = _bundle_manifest(
            payload_sha256s=_write_bundle_payload(directory),
            extraction_identity="deadbeef",
        )
        with pytest.raises(
            ExtractionPostConditionError, match="does not exactly match"
        ):
            ie.validate_imerg_bundle(directory, manifest)

    def test_validate_rejects_an_extraction_identity_that_does_not_recompute(
        self, tmp_path: Path
    ) -> None:
        """D9/P7a (locking) — a manifest whose `extraction_identity` does not
        match the digest recomputed from its own identity-bearing fields must
        be rejected: otherwise the label and the recorded provenance could
        silently drift apart."""
        staged, manifest = _stage_bundle(tmp_path)
        tampered = manifest.model_copy(update={"operator_id": "TAMPERED"})
        ie._write_manifest(tampered, staged / ie.MANIFEST_FILENAME)
        with pytest.raises(ExtractionPostConditionError, match="recomputed"):
            ie.validate_imerg_bundle(staged, tampered)

    def test_validate_rejects_a_malformed_sensitivity_schema(
        self, tmp_path: Path
    ) -> None:
        """D9/D1a (locking) — a sensitivity CSV missing the required
        columns (a bare `{scope, station}` frame) must be rejected, not
        published as a valid diagnostics artefact."""
        staged = ie.prepare_staging_dir(ie.imerg_points_root(tmp_path / "data_root"))
        _write_bundle_payload(staged)
        # Overwrite with the OLD, malformed shape.
        pl.DataFrame({"scope": ["STATION"], "station": ["S0"]}).write_csv(
            staged / ie._SENSITIVITY_FILENAME
        )
        manifest = _bundle_manifest(
            payload_sha256s={
                name: checksum_file(staged / name) for name in ie.IMERG_PAYLOAD_FILES
            }
        )
        ie._write_manifest(manifest, staged / ie.MANIFEST_FILENAME)
        with pytest.raises(ExtractionPostConditionError, match="missing column"):
            ie.validate_imerg_bundle(staged, manifest)


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
        )
        assert code == 0

        found_dir, manifest = ie.discover_imerg_bundle(
            data_root,
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

        # D6 — the selected cell's CENTRE and the station's own elevation with
        # its vertical datum; ⛔ never grid indices, never a DEM mismatch.
        cell = pl.read_csv(found_dir / ie._STATION_CELL_FILENAME)
        assert cell["station"].to_list() == ["A"]
        assert cell["grid_lat"][0] == pytest.approx(27.05)
        assert cell["grid_lon"][0] == pytest.approx(85.05)
        assert cell["station_elev_m"][0] == pytest.approx(1200.0)
        assert cell["station_elevation_datum"][0] == str(VerticalDatum.UNKNOWN)
        assert "grid_i" not in cell.columns
        assert "grid_j" not in cell.columns

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
            )

    def test_run_refuses_a_probe_manifest(self, tmp_path: Path) -> None:
        """D9 (locking) — 'a one-granule probe manifest can be mistaken for a
        complete acquisition'. There is no PROBE label to consult any more:
        T2 DERIVES that this record accounts for one granule, not 105,216."""
        from scripts.dhm_precip.imerg_acquire import FIRST_GRANULE_START

        data_root = tmp_path / "data_root"
        checksums = self._write_two_granules(data_root)
        one_name = next(iter(checksums))
        manifest = ia.ImergAcquisitionManifest(
            route="GES DISC HTTPS archive",
            collection_short_name="GPM_3IMERGHHE_07",
            granule_revision="V07B",
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
        with pytest.raises(ia.ImergRequestFailedError, match="requested 1 !="):
            ie.run(
                args,
                coords_path=coords_path,
                expected_stations=frozenset({Station("A")}),
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
        )
        assert code == 0
        found_dir, _manifest = ie.discover_imerg_bundle(
            data_root,
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
            )
            assert code == 0

        assert dir_a.exists()
        assert dir_b.exists()
        assert (dir_a / ie.MANIFEST_FILENAME).stat().st_size > 0
        for name in ie.IMERG_PAYLOAD_FILES:
            assert (dir_a / name).read_bytes() == (dir_b / name).read_bytes()

        import json as _json

        manifest_a = _json.loads((dir_a / ie.MANIFEST_FILENAME).read_text())
        manifest_b = _json.loads((dir_b / ie.MANIFEST_FILENAME).read_text())
        assert manifest_a["generated_at"] != manifest_b["generated_at"]
        del manifest_a["generated_at"]
        del manifest_b["generated_at"]
        assert manifest_a == manifest_b

    def test_an_existing_non_empty_out_is_refused_never_erased(
        self, tmp_path: Path
    ) -> None:
        """⛔ MAJOR (locking) — `--out` used to `shutil.rmtree()` any existing
        caller-supplied path before copying, so a mistyped `--out` recursively
        deleted it. Plan 211 forbids deleting anything under the ERA5 or
        `points/` trees: an existing non-empty destination is REFUSED."""
        data_root = tmp_path / "data_root"
        checksums = self._write_two_granules(data_root)
        _write_complete_acquisition_manifest(data_root, granule_checksums=checksums)
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)

        precious = tmp_path / "precious"
        (precious / "points" / "0000-abc").mkdir(parents=True)
        keeper = precious / "points" / "0000-abc" / "series.csv"
        keeper.write_text("irreplaceable")

        args = ie.build_parser().parse_args(
            ["--data-root", str(data_root), "--out", str(precious)]
        )
        with pytest.raises(Era5StorageError, match="refusing to erase"):
            ie.run(
                args,
                clock=lambda: datetime(2026, 8, 28, tzinfo=UTC),
                coords_path=coords_path,
                expected_stations=frozenset({Station("A")}),
            )
        assert keeper.read_text() == "irreplaceable"

    def test_an_empty_out_directory_is_accepted(self, tmp_path: Path) -> None:
        """The refusal above must not break D8's own two-run command: an
        empty (or absent) destination is the normal case."""
        data_root = tmp_path / "data_root"
        checksums = self._write_two_granules(data_root)
        _write_complete_acquisition_manifest(data_root, granule_checksums=checksums)
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)
        out = tmp_path / "out"
        out.mkdir()
        args = ie.build_parser().parse_args(
            ["--data-root", str(data_root), "--out", str(out)]
        )
        assert (
            ie.run(
                args,
                clock=lambda: datetime(2026, 8, 28, tzinfo=UTC),
                coords_path=coords_path,
                expected_stations=frozenset({Station("A")}),
            )
            == 0
        )
        assert (out / ie.MANIFEST_FILENAME).exists()

    def test_a_storage_failure_reading_the_acquisition_manifest_exits_5(
        self, tmp_path: Path
    ) -> None:
        """`ImergStorageError`'s docstring promises exit code 5. T2's table
        mapped EVERY `ImergAcquisitionError` to 3, so an unreadable
        acquisition manifest reported a request failure instead of a storage
        one — and ops tooling scripts against the exit code."""
        data_root = tmp_path / "data_root"
        ia.acquisition_manifest_path(data_root).mkdir(parents=True)  # not a file
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)
        code = ie.main(
            ["--data-root", str(data_root)],
            coords_path=coords_path,
            expected_stations=frozenset({Station("A")}),
        )
        assert code == 5  # noqa: PLR2004


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
        )

        assert exit_code not in (0, None)
        captured = capsys.readouterr()
        assert "imerg_extract.cli.failed" in captured.err
        assert "ImergReadContractError" in captured.err


# --- D9 (BLOCKER) — T2 DERIVES completeness; it never trusts the label ---


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
        with pytest.raises(ia.ImergRequestFailedError, match="105216|unaccounted"):
            ie.run(
                args,
                clock=lambda: datetime(2026, 8, 28, tzinfo=UTC),
                coords_path=coords_path,
                expected_stations=frozenset({Station("A")}),
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
            )


# --- D4/D9 — the primary series validator's numeric contract ---


class TestPrimarySeriesNumericContract:
    def test_a_null_granule_count_is_rejected(self, tmp_path: Path) -> None:
        """`~col.is_in([0, 1, 2])` is NULL for a null cell, and `filter`
        drops nulls — so a null count slipped through every range check."""
        staged, manifest = _stage_bundle(
            tmp_path,
            first_row_overrides={"granule_count": None, "precip_mm_per_h": None},
        )
        with pytest.raises(ExtractionPostConditionError, match="null value"):
            ie.validate_imerg_bundle(staged, manifest)

    def test_a_nan_precip_on_a_nominally_complete_hour_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """Nullness is not finiteness: a NaN written into a complete,
        cell-finite hour is not a valid rate."""
        staged, manifest = _stage_bundle(
            tmp_path, first_row_overrides={"precip_mm_per_h": float("nan")}
        )
        with pytest.raises(ExtractionPostConditionError, match="FINITE rate"):
            ie.validate_imerg_bundle(staged, manifest)

    def test_more_non_finite_cells_than_granules_is_rejected(
        self, tmp_path: Path
    ) -> None:
        staged, manifest = _stage_bundle(
            tmp_path,
            first_row_overrides={
                "granule_count": 1,
                "non_finite_cell_count": 2,
                "precip_mm_per_h": None,
            },
        )
        with pytest.raises(ExtractionPostConditionError, match="0 <= non_finite"):
            ie.validate_imerg_bundle(staged, manifest)


# --- D4/D9 — station accounting is RE-DERIVED from the payload ---


class TestStationAccountingIsRederived:
    def test_swapped_totals_that_still_sum_correctly_are_rejected(
        self, tmp_path: Path
    ) -> None:
        """⛔ The old check only asserted the four components SUM to the
        hour count — so any permutation of them passed."""
        swapped = _station_accounting(
            n_hours_complete=0, n_hours_missing_granule=ie.EXPECTED_HOUR_COUNT
        )
        staged, manifest = _stage_bundle(tmp_path, station_accounting={"S0": swapped})
        with pytest.raises(ExtractionPostConditionError, match="disagrees with the"):
            ie.validate_imerg_bundle(staged, manifest)

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
    def test_a_route_that_is_not_the_pinned_one_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """The route is a PIN (D1's measured GES DISC HTTPS archive), checked
        by the same predicate the acquisition record uses — not merely
        reconciled against a second copy of itself inside the bundle."""
        staged, manifest = _stage_bundle(tmp_path, route="a different route")
        with pytest.raises(ExtractionPostConditionError, match="pins"):
            ie.validate_imerg_bundle(staged, manifest)

    def test_an_output_axis_end_that_disagrees_with_the_payload_is_rejected(
        self, tmp_path: Path
    ) -> None:
        staged, manifest = _stage_bundle(
            tmp_path, output_axis_end=datetime(2020, 6, 1, tzinfo=UTC)
        )
        with pytest.raises(ExtractionPostConditionError, match="output_axis"):
            ie.validate_imerg_bundle(staged, manifest)

    def test_acquisition_counts_that_do_not_add_up_are_rejected(
        self, tmp_path: Path
    ) -> None:
        """D9 — the bundle carries the acquisition's counts and gaps, and they
        must reconcile with each other and with the pinned window."""
        staged, manifest = _stage_bundle(tmp_path, granules_retrieved=99)
        with pytest.raises(ExtractionPostConditionError, match="!= requested"):
            ie.validate_imerg_bundle(staged, manifest)


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
        staged, manifest = _stage_bundle(tmp_path)
        valid = ie.publish_imerg_bundle(
            staged, data_root=data_root, identity=manifest.extraction_identity
        )

        broken_json = ie.allocate_published_dir(root, identity="bbbb")
        (broken_json / ie.MANIFEST_FILENAME).write_text("{not json")
        broken_csv = ie.allocate_published_dir(root, identity="cccc")
        shutil.copytree(valid, broken_csv, dirs_exist_ok=True)
        (broken_csv / ie._NEAREST_SERIES_FILENAME).write_bytes(b"\x00\x01 not,a\ncsv")

        found, _found_manifest = ie.discover_imerg_bundle(data_root)
        assert found == valid


# --- D9 (MAJOR) — the ONE predicate owns the plan's invariants, so a bundle
# cannot reach publication (or discovery) by bypassing run() ---


class TestPinnedInvariantsCannotBeBypassed:
    def test_a_one_granule_accounting_bundle_is_refused_at_publication(
        self, tmp_path: Path
    ) -> None:
        """⛔ THE BLOCKER, at the layer that actually owns it. `run()` derived
        completeness correctly, but the shared publish/discovery predicate
        only checked `retrieved + missing == requested` — so a hand-built
        bundle claiming ONE requested granule published happily, and D9's
        "publication and discovery must call ONE validation predicate" then
        made discovery accept it too."""
        data_root = tmp_path / "data_root"
        staged, manifest = _stage_bundle(
            tmp_path, granules_requested=1, granules_retrieved=1, granules_missing=()
        )
        with pytest.raises(ExtractionPostConditionError, match="pinned D5 window"):
            ie.publish_imerg_bundle(
                staged, data_root=data_root, identity=manifest.extraction_identity
            )
        assert not list(ie.imerg_points_root(data_root).glob("0*"))

    def test_a_one_granule_accounting_bundle_is_invisible_to_discovery(
        self, tmp_path: Path
    ) -> None:
        """The same predicate, from the other side: a bundle that somehow got
        onto disk with under-complete accounting is skipped, never returned."""
        data_root = tmp_path / "data_root"
        root = ie.imerg_points_root(data_root)
        staged, manifest = _stage_bundle(
            tmp_path, granules_requested=1, granules_retrieved=1, granules_missing=()
        )
        published = ie.allocate_published_dir(
            root, identity=manifest.extraction_identity
        )
        shutil.copytree(staged, published, dirs_exist_ok=True)
        with pytest.raises(ExtractionPostConditionError, match="pinned D5 window"):
            ie.discover_imerg_bundle(data_root)

    def test_a_gap_outside_the_pinned_window_is_refused(self, tmp_path: Path) -> None:
        """D5/D9 — the gaps are reconciled against the identity's own window:
        accounting that balances but records a granule outside 2019-12-31
        23:00 .. 2025-12-31 22:30 is not describing this acquisition."""
        staged, manifest = _stage_bundle(
            tmp_path,
            granules_retrieved=ia.EXPECTED_GRANULE_COUNT - 1,
            granules_missing=(
                (ia.LAST_GRANULE_START + timedelta(minutes=30)).isoformat(),
            ),
        )
        with pytest.raises(ExtractionPostConditionError, match="inside the"):
            ie.validate_imerg_bundle(staged, manifest)

    def test_a_bundle_with_fewer_stations_than_the_pin_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The 26-station scope is a PIN, not a validator parameter — this is
        the one test that leaves it at its real value, so the autouse
        one-station fixture cannot hide a pin that stopped being enforced."""
        monkeypatch.setattr(ie, "EXPECTED_STATION_COUNT", 26)
        staged, manifest = _stage_bundle(tmp_path)
        with pytest.raises(ExtractionPostConditionError, match="expected the pinned"):
            ie.validate_imerg_bundle(staged, manifest)
