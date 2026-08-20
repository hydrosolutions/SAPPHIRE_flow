"""Plan 191 task T4 — narrow ERA5-Land t2m point extraction.

D8 seam tests (never decision tests): every artefact asserts on what is
PHYSICALLY ON DISK — the written series file's data variable name and
`units` attr, the manifest's `referenced_precipitation_bundle_identity`
key, and station/stamp counts read back from a REOPENED file, never from
the in-memory object that wrote it. #194's fifth bug passed 18 tests
against an inert mechanism precisely because nothing checked the disk.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import numpy as np
import polars as pl
import pytest
import xarray as xr

from scripts.dhm_precip import extract_era5_t2m
from scripts.dhm_precip.domain_types import (
    ExtractionOperator,
    Station,
    StationCoordinate,
)
from scripts.dhm_precip.era5_errors import (
    Era5StorageError,
    ExtractionInputAbsentError,
    ExtractionPostConditionError,
    NonFiniteExtractionError,
)
from scripts.dhm_precip.era5_extract import ExtractedSeries, extract_nearest_series
from scripts.dhm_precip.era5_extract_manifest import (
    ExtractionManifest,
    write_extraction_manifest,
)
from scripts.dhm_precip.era5_manifest import (
    Era5ProvenanceManifest,
    OperatorProvenance,
    TransformYearRecord,
    checksum_file,
    manifest_path_for,
    product_artifact_path,
    with_transform_year,
    write_manifest_atomic,
)
from scripts.dhm_precip.era5_request import STUDY_YEARS, expected_grid_shape
from scripts.dhm_precip.params import DEFAULT_PARAMS

if TYPE_CHECKING:
    from pathlib import Path

_AREA = (26.2, 85.0, 26.0, 85.2)  # north, west, south, east -> 3x3 grid
_HOUR = np.timedelta64(1, "h")
_CLOCK = lambda: datetime(2026, 8, 20, tzinfo=UTC)  # noqa: E731
_STATIONS: tuple[tuple[str, float, float], ...] = (
    ("A", 26.05, 85.05),
    ("B", 26.15, 85.15),
)
_TEST_PARAMS = replace(DEFAULT_PARAMS, expected_station_count=len(_STATIONS))


def _full_year_valid_time(year: int) -> np.ndarray:
    start = np.datetime64(f"{year:04d}-01-01T00:00:00")
    end = np.datetime64(f"{year:04d}-12-31T23:00:00")
    return np.arange(start, end + _HOUR, _HOUR)


def _t2m_product_ds(
    *, year: int, value_degc: float = 10.0, non_finite_index: int | None = None
) -> xr.Dataset:
    lat_count, lon_count = expected_grid_shape(_AREA)
    north, west, south, east = _AREA
    lat = np.round(np.linspace(south, north, lat_count), 10)
    lon = np.round(np.linspace(west, east, lon_count), 10)
    valid_time = _full_year_valid_time(year)
    values = np.full(
        (valid_time.size, lat_count, lon_count), value_degc, dtype=np.float32
    )
    if non_finite_index is not None:
        values[non_finite_index, 1, 1] = np.nan
    ds = xr.Dataset(
        {"temperature": (["valid_time", "latitude", "longitude"], values)},
        coords={"valid_time": valid_time, "latitude": lat, "longitude": lon},
    )
    ds["temperature"].attrs["units"] = "degC"
    ds.attrs.update(
        {
            "transform_version": "1",
            "output_schema_version": "1",
            "source_dataset": "reanalysis-era5-land",
            "units_conversion": "kelvin_to_celsius_subtract_273.15",
        }
    )
    return ds


def _write_t2m_product(path: Path, ds: xr.Dataset) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(
        path,
        engine="h5netcdf",
        encoding={
            "temperature": {"dtype": "float32"},
            "valid_time": {
                "units": "hours since 1970-01-01 00:00:00",
                "dtype": "int64",
            },
        },
    )


def _write_coords_csv(path: Path) -> None:
    pl.DataFrame(
        {
            "station": [s[0] for s in _STATIONS],
            "excel_col": [f"{s[0]} (mm)" for s in _STATIONS],
            "lat": [s[1] for s in _STATIONS],
            "lon": [s[2] for s in _STATIONS],
            "elev": [1500.0, 1600.0],
        }
    ).write_csv(path)


def _build_t2m_root(tmp_path: Path, *, non_finite_year: int | None = None) -> Path:
    data_root = tmp_path / "t2m"
    provenance = OperatorProvenance(
        cds_portal_url="https://example.org/portal",
        dataset_landing_page_url="https://example.org/dataset",
        licence_name="Licence",
        licence_version="1.0",
        licence_accepted_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    manifest = Era5ProvenanceManifest(
        dataset="reanalysis-era5-land",
        client_package_version="0.7.7",
        operator_provenance=provenance,
        variable="2m_temperature",
    )
    for year in STUDY_YEARS:
        ds = _t2m_product_ds(
            year=year, non_finite_index=0 if year == non_finite_year else None
        )
        path = product_artifact_path(
            year,
            data_root,
            variable_code="t2m",
            product_dir_name="degc",
            unit_label="degc",
        )
        _write_t2m_product(path, ds)
        sha = checksum_file(path)
        manifest = with_transform_year(
            manifest,
            TransformYearRecord(
                product_year=year,
                transform_identity="x",
                sha256=sha,
                accumulation_convention="instantaneous",
                units_conversion="kelvin_to_celsius_subtract_273.15",
                packing=None,
                non_finite_cell_count=0,
                dropped_boundary_stamp=None,
                transformed_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        )
    write_manifest_atomic(manifest, manifest_path_for(data_root))
    return data_root


def _station_coord(name: str, lat: float, lon: float) -> StationCoordinate:
    return StationCoordinate(
        station=Station(name), excel_col=f"{name} (mm)", lat=lat, lon=lon, elev_m=1500.0
    )


def _true_grid() -> dict[str, ExtractedSeries]:
    """The grid cell every station ACTUALLY resolves to on `_AREA`'s
    3x3 grid, computed the same way `extract_era5_t2m.run` does — so the
    fake precipitation bundle below can assert genuine D6 agreement."""
    ds = _t2m_product_ds(year=STUDY_YEARS[0])
    return {
        name: extract_nearest_series(
            ds, _station_coord(name, lat, lon), variable="temperature"
        )
        for name, lat, lon in _STATIONS
    }


def _build_precip_bundle(
    precip_root: Path,
    *,
    corrupt_station: str | None = None,
    extraction_identity: str = "precip-identity-abc123",
    omit_elevation_csv: bool = False,
    tamper_elevation_csv_after_hashing: bool = False,
) -> Path:
    bundle_dir = precip_root / "era5_land" / "points" / "0000-fake"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    payload_sha256s: dict[str, str] = {}
    if not omit_elevation_csv:
        rows: list[dict[str, object]] = []
        for name, series in _true_grid().items():
            grid_i = series.grid_i + (1 if name == corrupt_station else 0)
            rows.append(
                {
                    "station": name,
                    "grid_i": grid_i,
                    "grid_j": series.grid_j,
                    "grid_lat": series.grid_lat,
                    "grid_lon": series.grid_lon,
                }
            )
        elevation_csv_path = bundle_dir / "station_grid_elevation.csv"
        pl.DataFrame(rows).write_csv(elevation_csv_path)
        # Finding 2: the manifest must record the checksum of the bytes as
        # actually written, so a real run reconciles cleanly.
        payload_sha256s["station_grid_elevation.csv"] = checksum_file(
            elevation_csv_path
        )
        if tamper_elevation_csv_after_hashing:
            # Simulate a bundle corrupted AFTER its manifest was published:
            # the recorded checksum still describes the ORIGINAL bytes, but
            # the file on disk has since changed underneath it.
            elevation_csv_path.write_text(
                elevation_csv_path.read_text() + "\n# tampered after hashing"
            )

    manifest = ExtractionManifest(
        orography_identity="oro-x",
        extraction_identity=extraction_identity,
        operator_id=str(ExtractionOperator.NEAREST),
        coordinate_table_sha256="0" * 64,
        source_sha256s_by_year={"2020": "0" * 64},
        payload_sha256s=payload_sha256s,
        orography_spec={},
        orography_source_record={},
        accumulation_diagnostic={},
        generated_at=_CLOCK(),
    )
    write_extraction_manifest(manifest, bundle_dir / "extraction_manifest.json")
    return bundle_dir


class TestGridAgreementD6:
    """D6 — never assumed, always verified. A red-first requirement: this
    must fail loudly against a corrupted precipitation elevation table."""

    def test_matching_grid_passes_silently(self, tmp_path: Path) -> None:
        bundle_dir = _build_precip_bundle(tmp_path)
        extract_era5_t2m.assert_grid_matches_precipitation_bundle(
            _true_grid(), bundle_dir / "station_grid_elevation.csv"
        )

    def test_mismatched_grid_i_is_falsified_and_raises(self, tmp_path: Path) -> None:
        bundle_dir = _build_precip_bundle(tmp_path, corrupt_station="A")
        with pytest.raises(ExtractionPostConditionError, match="FALSIFIED"):
            extract_era5_t2m.assert_grid_matches_precipitation_bundle(
                _true_grid(), bundle_dir / "station_grid_elevation.csv"
            )

    def test_missing_elevation_csv_raises_input_absent(self, tmp_path: Path) -> None:
        with pytest.raises(ExtractionInputAbsentError):
            extract_era5_t2m.assert_grid_matches_precipitation_bundle(
                _true_grid(), tmp_path / "does_not_exist.csv"
            )

    def test_station_absent_from_elevation_table_raises(self, tmp_path: Path) -> None:
        bundle_dir = tmp_path / "era5_land" / "points" / "0000-fake"
        bundle_dir.mkdir(parents=True)
        pl.DataFrame(
            {
                "station": ["OTHER"],
                "grid_i": [0],
                "grid_j": [0],
                "grid_lat": [26.0],
                "grid_lon": [85.0],
            }
        ).write_csv(bundle_dir / "station_grid_elevation.csv")
        with pytest.raises(ExtractionPostConditionError, match="absent"):
            extract_era5_t2m.assert_grid_matches_precipitation_bundle(
                _true_grid(), bundle_dir / "station_grid_elevation.csv"
            )


class TestDiscoverPrecipBundleReconcilesElevationChecksum:
    """Finding 2 — D6 rests on "elevations are REUSED, never re-derived",
    which is only safe if the reused `station_grid_elevation.csv` is
    actually the file the referenced manifest's identity names.
    `_discover_precip_bundle` used to accept any syntactically readable
    manifest without reconciling the CSV's bytes against the manifest's own
    `payload_sha256s` — a corrupted table would silently pass through and
    be trusted for the rest of the run."""

    def test_matching_checksum_discovers_the_bundle(self, tmp_path: Path) -> None:
        bundle_dir = _build_precip_bundle(tmp_path)
        discovered_dir, identity = extract_era5_t2m._discover_precip_bundle(tmp_path)
        assert discovered_dir == bundle_dir
        assert identity == "precip-identity-abc123"

    def test_tampered_elevation_csv_is_rejected_not_trusted(
        self, tmp_path: Path
    ) -> None:
        _build_precip_bundle(tmp_path, tamper_elevation_csv_after_hashing=True)
        with pytest.raises(ExtractionPostConditionError, match="sha256"):
            extract_era5_t2m._discover_precip_bundle(tmp_path)

    def test_tampered_bundle_stops_the_full_run(self, tmp_path: Path) -> None:
        """The typed failure must actually reach the CLI's exit-code
        mapping (exit 4) — never a warning, never a silent pass-through."""
        data_root = _build_t2m_root(tmp_path)
        precip_root = tmp_path / "precip"
        _build_precip_bundle(precip_root, tamper_elevation_csv_after_hashing=True)
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)

        exit_code = extract_era5_t2m.main(
            [
                "--data-root",
                str(data_root),
                "--precip-data-root",
                str(precip_root),
            ],
            clock=_CLOCK,
            coords_path=coords_path,
            expected_stations=frozenset({Station(s[0]) for s in _STATIONS}),
            params=_TEST_PARAMS,
            request_area=_AREA,
        )
        assert exit_code == 4
        with pytest.raises(ExtractionInputAbsentError):
            extract_era5_t2m.discover_t2m_bundle(data_root)


class TestExtractionIdentity:
    def test_identity_is_deterministic(self) -> None:
        kwargs = dict(
            operator_id="NEAREST",
            coordinate_table_sha256="a" * 64,
            source_sha256s_by_year={"2020": "b" * 64},
            referenced_precipitation_bundle_identity="precip-x",
            output_schema_version="1",
            output_dtype="float32",
        )
        assert extract_era5_t2m.t2m_extraction_identity(
            **kwargs
        ) == extract_era5_t2m.t2m_extraction_identity(**kwargs)

    def test_changing_the_referenced_bundle_changes_the_identity(self) -> None:
        base = dict(
            operator_id="NEAREST",
            coordinate_table_sha256="a" * 64,
            source_sha256s_by_year={"2020": "b" * 64},
            output_schema_version="1",
            output_dtype="float32",
        )
        first = extract_era5_t2m.t2m_extraction_identity(
            referenced_precipitation_bundle_identity="precip-x", **base
        )
        second = extract_era5_t2m.t2m_extraction_identity(
            referenced_precipitation_bundle_identity="precip-y", **base
        )
        assert first != second


class TestExitCodeDispatch:
    def test_every_subclass_precedes_its_base_in_the_dispatch_table(self) -> None:
        table = extract_era5_t2m._EXIT_BY_ERROR
        for i, (exc_type, _code) in enumerate(table):
            for later_type, _later_code in table[i + 1 :]:
                assert not (
                    issubclass(later_type, exc_type) and later_type is not exc_type
                ), (
                    f"{later_type.__name__} is a SUBCLASS of {exc_type.__name__}, "
                    "listed before it — the first match wins"
                )

    def test_storage_error_maps_to_5(self) -> None:
        assert extract_era5_t2m._exit_code_for(Era5StorageError("x")) == 5

    def test_non_finite_extraction_error_maps_to_4_via_the_catchall(self) -> None:
        # NonFiniteExtractionError is NOT ExtractionPostConditionError — it
        # only reaches exit code 4 through the Era5AcquisitionError catchall.
        assert extract_era5_t2m._exit_code_for(NonFiniteExtractionError("x")) == 4


class TestRealRunSeamArtifacts:
    """D8: the published series/manifest are validated by REOPENING them
    from disk, never by inspecting the in-memory objects that wrote them."""

    def test_happy_path_publishes_a_series_and_manifest_on_disk(
        self, tmp_path: Path
    ) -> None:
        data_root = _build_t2m_root(tmp_path)
        precip_root = tmp_path / "precip"
        _build_precip_bundle(precip_root)
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)

        exit_code = extract_era5_t2m.main(
            [
                "--data-root",
                str(data_root),
                "--precip-data-root",
                str(precip_root),
            ],
            clock=_CLOCK,
            coords_path=coords_path,
            expected_stations=frozenset({Station(s[0]) for s in _STATIONS}),
            params=_TEST_PARAMS,
            request_area=_AREA,
        )
        assert exit_code == 0

        bundle_dir, _discovered_manifest = extract_era5_t2m.discover_t2m_bundle(
            data_root
        )
        series_path = bundle_dir / "series_t2m_degc.nc"
        manifest_path = bundle_dir / "extraction_manifest.json"
        assert series_path.exists()
        assert manifest_path.exists()
        assert re.fullmatch(r"0000-[0-9a-f]{64}", bundle_dir.name)

        # Seam: reopen the WRITTEN file — never trust the in-memory object.
        with xr.open_dataset(series_path, engine="h5netcdf") as reopened:
            loaded = reopened.load()
            assert extract_era5_t2m.T2M_DATA_VARIABLE in loaded
            assert loaded[extract_era5_t2m.T2M_DATA_VARIABLE].attrs["units"] == "degC"
            assert loaded["valid_time"].attrs["timezone"] == "UTC"
            assert loaded.sizes["station"] == len(_STATIONS)
            assert loaded.sizes["valid_time"] == 52608
            assert bool(
                np.isfinite(loaded[extract_era5_t2m.T2M_DATA_VARIABLE].values).all()
            )

        manifest_on_disk = json.loads(manifest_path.read_text())
        assert (
            manifest_on_disk["referenced_precipitation_bundle_identity"]
            == "precip-identity-abc123"
        )
        assert manifest_on_disk["referenced_precipitation_bundle_identity"] != ""
        assert manifest_on_disk["station_count"] == len(_STATIONS)
        assert manifest_on_disk["stamp_count"] == 52608
        assert manifest_on_disk["extraction_identity"]

    def test_manifest_records_identity_only_never_a_run_numbered_path(
        self, tmp_path: Path
    ) -> None:
        """The reviewer's fix: `allocate_published_dir` hands out a fresh
        `<NNNN>-<identity>` directory on every gate-suite run (P1a — no
        adoption, no dedup), so a run-numbered PATH recorded today can point
        at a directory pruned tomorrow. The manifest must carry the
        precipitation bundle's IDENTITY only — a real 64-char sha256 hex
        digest, physically on disk — and no `points/<NNNN>-` path fragment
        anywhere in the JSON text."""
        real_identity = hashlib.sha256(b"a real precipitation bundle").hexdigest()
        assert re.fullmatch(r"[0-9a-f]{64}", real_identity)

        data_root = _build_t2m_root(tmp_path)
        precip_root = tmp_path / "precip"
        _build_precip_bundle(precip_root, extraction_identity=real_identity)
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)

        exit_code = extract_era5_t2m.main(
            [
                "--data-root",
                str(data_root),
                "--precip-data-root",
                str(precip_root),
            ],
            clock=_CLOCK,
            coords_path=coords_path,
            expected_stations=frozenset({Station(s[0]) for s in _STATIONS}),
            params=_TEST_PARAMS,
            request_area=_AREA,
        )
        assert exit_code == 0

        bundle_dir, _discovered_manifest = extract_era5_t2m.discover_t2m_bundle(
            data_root
        )
        manifest_path = bundle_dir / "extraction_manifest.json"
        manifest_text = manifest_path.read_text()

        # No run-number-prefixed path fragment anywhere in the JSON text.
        assert re.search(r"points/\d{4}-", manifest_text) is None
        assert "referenced_precipitation_bundle_path" not in manifest_text

        manifest_on_disk = json.loads(manifest_text)
        assert manifest_on_disk["referenced_precipitation_bundle_identity"] == (
            real_identity
        )
        assert re.fullmatch(
            r"[0-9a-f]{64}",
            manifest_on_disk["referenced_precipitation_bundle_identity"],
        )

    def test_d6_mismatch_stops_the_run(self, tmp_path: Path) -> None:
        data_root = _build_t2m_root(tmp_path)
        precip_root = tmp_path / "precip"
        _build_precip_bundle(precip_root, corrupt_station="A")
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)

        exit_code = extract_era5_t2m.main(
            [
                "--data-root",
                str(data_root),
                "--precip-data-root",
                str(precip_root),
            ],
            clock=_CLOCK,
            coords_path=coords_path,
            expected_stations=frozenset({Station(s[0]) for s in _STATIONS}),
            params=_TEST_PARAMS,
            request_area=_AREA,
        )
        assert exit_code == 4
        with pytest.raises(ExtractionInputAbsentError):
            extract_era5_t2m.discover_t2m_bundle(data_root)

    def test_missing_precip_bundle_exits_2(self, tmp_path: Path) -> None:
        data_root = _build_t2m_root(tmp_path)
        precip_root = tmp_path / "precip"  # never built
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)

        exit_code = extract_era5_t2m.main(
            [
                "--data-root",
                str(data_root),
                "--precip-data-root",
                str(precip_root),
            ],
            clock=_CLOCK,
            coords_path=coords_path,
            expected_stations=frozenset({Station(s[0]) for s in _STATIONS}),
            params=_TEST_PARAMS,
            request_area=_AREA,
        )
        assert exit_code == 2

    def test_non_finite_t2m_value_exits_4(self, tmp_path: Path) -> None:
        data_root = _build_t2m_root(tmp_path, non_finite_year=STUDY_YEARS[0])
        precip_root = tmp_path / "precip"
        _build_precip_bundle(precip_root)
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)

        exit_code = extract_era5_t2m.main(
            [
                "--data-root",
                str(data_root),
                "--precip-data-root",
                str(precip_root),
            ],
            clock=_CLOCK,
            coords_path=coords_path,
            expected_stations=frozenset({Station(s[0]) for s in _STATIONS}),
            params=_TEST_PARAMS,
            request_area=_AREA,
        )
        assert exit_code == 4


class TestNumberedPublishOfT2mBundle:
    """Finding 4 — the t2m bundle now publishes to a per-run-unique numbered
    directory (`era5_extract_manifest.allocate_published_dir`), never a
    single evolving `points/` path swapped through a shared `.points.prev`
    backup. The old scheme's two defects: a crash between the two renames
    left the canonical `points` path ABSENT (recoverable only on a LATER
    publish), and two concurrent publishers sharing one `.points.prev` name
    could deadlock each other, stranding both staging dirs. The numbered
    scheme removes both possibilities — `os.replace` never faces a
    non-empty target, so there is no window and no shared name to collide
    on."""

    def _run(self, tmp_path: Path, *, data_root: Path, precip_root: Path) -> int:
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)
        return extract_era5_t2m.main(
            [
                "--data-root",
                str(data_root),
                "--precip-data-root",
                str(precip_root),
            ],
            clock=_CLOCK,
            coords_path=coords_path,
            expected_stations=frozenset({Station(s[0]) for s in _STATIONS}),
            params=_TEST_PARAMS,
            request_area=_AREA,
        )

    def test_manifest_write_failure_publishes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failure before the final `os.replace` — staging is never even
        visible under the points root — must leave no bundle behind."""
        data_root = _build_t2m_root(tmp_path)
        precip_root = tmp_path / "precip"
        _build_precip_bundle(precip_root)

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise OSError("simulated manifest write failure")

        monkeypatch.setattr(extract_era5_t2m, "_write_t2m_manifest", _boom)

        exit_code = self._run(tmp_path, data_root=data_root, precip_root=precip_root)
        assert exit_code == 5
        with pytest.raises(ExtractionInputAbsentError):
            extract_era5_t2m.discover_t2m_bundle(data_root)

    def test_manifest_write_failure_leaves_a_previously_published_bundle_byte_identical(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_root = _build_t2m_root(tmp_path)
        precip_root = tmp_path / "precip"
        _build_precip_bundle(precip_root)

        first_exit = self._run(tmp_path, data_root=data_root, precip_root=precip_root)
        assert first_exit == 0
        bundle_dir, _manifest = extract_era5_t2m.discover_t2m_bundle(data_root)
        series_path = bundle_dir / "series_t2m_degc.nc"
        manifest_path = bundle_dir / "extraction_manifest.json"
        original_series_sha256 = checksum_file(series_path)
        original_manifest_text = manifest_path.read_text()

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise OSError("simulated manifest write failure")

        monkeypatch.setattr(extract_era5_t2m, "_write_t2m_manifest", _boom)

        second_exit = self._run(tmp_path, data_root=data_root, precip_root=precip_root)
        assert second_exit == 5

        # The previously published bundle survives byte-for-byte, at the
        # SAME numbered directory — the failed run never allocated a new
        # one, because allocation only happens after staging succeeds.
        rediscovered_dir, _manifest2 = extract_era5_t2m.discover_t2m_bundle(data_root)
        assert rediscovered_dir == bundle_dir
        assert checksum_file(series_path) == original_series_sha256
        assert manifest_path.read_text() == original_manifest_text

    def test_two_publishes_same_identity_get_distinct_run_numbers_both_survive(
        self, tmp_path: Path
    ) -> None:
        """The concurrency property the OLD `.points.prev` scheme lacked:
        two publishes (here, two successive runs with byte-identical
        inputs, so they resolve to the SAME `extraction_identity`) must
        never interfere. Each gets its own numbered directory; the first
        one published is left completely untouched by the second."""
        data_root = _build_t2m_root(tmp_path)
        precip_root = tmp_path / "precip"
        _build_precip_bundle(precip_root)

        first_exit = self._run(tmp_path, data_root=data_root, precip_root=precip_root)
        assert first_exit == 0
        first_dir, first_manifest = extract_era5_t2m.discover_t2m_bundle(data_root)
        first_series_sha256 = checksum_file(first_dir / "series_t2m_degc.nc")

        second_exit = self._run(tmp_path, data_root=data_root, precip_root=precip_root)
        assert second_exit == 0
        second_dir, second_manifest = extract_era5_t2m.discover_t2m_bundle(data_root)

        assert first_manifest.extraction_identity == second_manifest.extraction_identity
        assert first_dir != second_dir
        assert first_dir.exists()
        assert second_dir.exists()
        assert (first_dir / "series_t2m_degc.nc").exists()
        assert (second_dir / "series_t2m_degc.nc").exists()
        # The first bundle was left completely untouched by the second
        # publish — no in-place mutation, no shared backup name to race on.
        assert checksum_file(first_dir / "series_t2m_degc.nc") == first_series_sha256


def _write_fake_t2m_bundle(
    points_root: Path,
    *,
    run_number: int,
    identity: str = "t2m-identity-abc123",
    write_manifest: bool = True,
    write_series: bool = True,
    manifest_extraction_identity: str | None = None,
    series_bytes: bytes = b"fake-series-payload",
    payload_sha256s: dict[str, str] | None = None,
    corrupt_series_after_hashing: bool = False,
) -> Path:
    """A minimal, hand-built t2m bundle directory for discovery tests —
    deliberately bypassing `run()`/`_publish_t2m_bundle` so a discovery test
    can construct an INVALID bundle (missing series, unreadable manifest,
    mismatched identity, mismatched payload checksum) without a full CLI
    run. By default the manifest's `payload_sha256s` is computed over the
    bytes actually written (a real, reconciling bundle); pass
    `corrupt_series_after_hashing=True` to simulate a bundle whose bytes
    changed AFTER its manifest was published (finding 1, round-3 review,
    2026-08-20) — the manifest still records the ORIGINAL hash, but the
    on-disk file no longer matches it."""
    bundle_dir = points_root / f"{run_number:04d}-{identity}"
    bundle_dir.mkdir(parents=True)
    series_path = bundle_dir / "series_t2m_degc.nc"
    if write_series:
        series_path.write_bytes(series_bytes)
    if write_manifest:
        resolved_payload_sha256s = (
            payload_sha256s
            if payload_sha256s is not None
            else (
                {"series_t2m_degc.nc": hashlib.sha256(series_bytes).hexdigest()}
                if write_series
                else {}
            )
        )
        manifest = extract_era5_t2m.T2mExtractionManifest(
            extraction_identity=manifest_extraction_identity or identity,
            operator_id=str(ExtractionOperator.NEAREST),
            coordinate_table_sha256="0" * 64,
            source_sha256s_by_year={"2020": "0" * 64},
            referenced_precipitation_bundle_identity="precip-x",
            payload_sha256s=resolved_payload_sha256s,
            station_count=2,
            stamp_count=52608,
            output_schema_version="1",
            output_dtype="float32",
            generated_at=_CLOCK(),
        )
        (bundle_dir / "extraction_manifest.json").write_text(
            manifest.model_dump_json(indent=2)
        )
    if write_series and corrupt_series_after_hashing:
        series_path.write_bytes(series_bytes + b"-corrupted-after-hashing")
    return bundle_dir


class TestDiscoverT2mBundle:
    """Finding 4 — t2m discovery is P2/P6's convention, exactly like the
    precipitation side (`_discover_precip_bundle`): the highest `NNNN`
    whose manifest validates, never a fixed path."""

    def test_selects_the_highest_valid_run_number(self, tmp_path: Path) -> None:
        points_root = extract_era5_t2m.t2m_points_dir(tmp_path)
        _write_fake_t2m_bundle(points_root, run_number=0, identity="aaa")
        expected_dir = _write_fake_t2m_bundle(points_root, run_number=1, identity="bbb")

        discovered_dir, manifest = extract_era5_t2m.discover_t2m_bundle(tmp_path)
        assert discovered_dir == expected_dir
        assert manifest.extraction_identity == "bbb"

    def test_skips_a_bundle_missing_its_series_file(self, tmp_path: Path) -> None:
        points_root = extract_era5_t2m.t2m_points_dir(tmp_path)
        expected_dir = _write_fake_t2m_bundle(points_root, run_number=0, identity="aaa")
        _write_fake_t2m_bundle(
            points_root, run_number=1, identity="bbb", write_series=False
        )

        discovered_dir, manifest = extract_era5_t2m.discover_t2m_bundle(tmp_path)
        assert discovered_dir == expected_dir
        assert manifest.extraction_identity == "aaa"

    def test_skips_a_bundle_whose_manifest_identity_does_not_match_its_directory(
        self, tmp_path: Path
    ) -> None:
        points_root = extract_era5_t2m.t2m_points_dir(tmp_path)
        expected_dir = _write_fake_t2m_bundle(points_root, run_number=0, identity="aaa")
        _write_fake_t2m_bundle(
            points_root,
            run_number=1,
            identity="bbb",
            manifest_extraction_identity="mismatched",
        )

        discovered_dir, manifest = extract_era5_t2m.discover_t2m_bundle(tmp_path)
        assert discovered_dir == expected_dir
        assert manifest.extraction_identity == "aaa"

    def test_missing_points_root_raises_input_absent(self, tmp_path: Path) -> None:
        with pytest.raises(ExtractionInputAbsentError):
            extract_era5_t2m.discover_t2m_bundle(tmp_path)

    def test_empty_points_root_raises_input_absent(self, tmp_path: Path) -> None:
        extract_era5_t2m.t2m_points_dir(tmp_path).mkdir(parents=True)
        with pytest.raises(ExtractionInputAbsentError):
            extract_era5_t2m.discover_t2m_bundle(tmp_path)


class TestDiscoverT2mBundleReconcilesPayloadChecksum:
    """Round-3 review (2026-08-20), finding 1 — the t2m bundle used to
    carry NO payload checksum at all: `discover_t2m_bundle` accepted the
    highest `NNNN` whose manifest merely PARSED and whose series file
    merely EXISTED, so a truncated or wrong-schema latest bundle could
    shadow a valid older one. `payload_sha256s` now closes that gap the
    same way `_discover_precip_bundle` already does for the bundle t2m only
    reads — except a t2m checksum failure SKIPS that one candidate (falls
    back to an older valid bundle) rather than stopping discovery
    outright."""

    def test_matching_checksum_is_discovered(self, tmp_path: Path) -> None:
        points_root = extract_era5_t2m.t2m_points_dir(tmp_path)
        expected_dir = _write_fake_t2m_bundle(points_root, run_number=0, identity="aaa")

        discovered_dir, manifest = extract_era5_t2m.discover_t2m_bundle(tmp_path)
        assert discovered_dir == expected_dir
        assert manifest.extraction_identity == "aaa"

    def test_corrupted_newest_bundle_is_skipped_in_favour_of_an_older_valid_one(
        self, tmp_path: Path
    ) -> None:
        """The red-first proof: publish a VALID older bundle, then a NEWER
        bundle whose series bytes were corrupted after its manifest was
        published. Discovery must fall back to the older, still-valid
        bundle — never return the corrupt one."""
        points_root = extract_era5_t2m.t2m_points_dir(tmp_path)
        valid_dir = _write_fake_t2m_bundle(points_root, run_number=0, identity="aaa")
        _write_fake_t2m_bundle(
            points_root,
            run_number=1,
            identity="bbb",
            corrupt_series_after_hashing=True,
        )

        discovered_dir, manifest = extract_era5_t2m.discover_t2m_bundle(tmp_path)
        assert discovered_dir == valid_dir
        assert manifest.extraction_identity == "aaa"

    def test_missing_hash_is_also_skipped_in_favour_of_an_older_valid_one(
        self, tmp_path: Path
    ) -> None:
        """A manifest published before this field existed parses with
        `payload_sha256s == {}` (the pydantic default) — no recorded hash
        for the series file is the SAME typed, skippable failure as a
        mismatched one, never an unrelated parse error."""
        points_root = extract_era5_t2m.t2m_points_dir(tmp_path)
        valid_dir = _write_fake_t2m_bundle(points_root, run_number=0, identity="aaa")
        _write_fake_t2m_bundle(
            points_root, run_number=1, identity="bbb", payload_sha256s={}
        )

        discovered_dir, manifest = extract_era5_t2m.discover_t2m_bundle(tmp_path)
        assert discovered_dir == valid_dir
        assert manifest.extraction_identity == "aaa"

    def test_all_candidates_corrupted_raises_input_absent(self, tmp_path: Path) -> None:
        points_root = extract_era5_t2m.t2m_points_dir(tmp_path)
        _write_fake_t2m_bundle(
            points_root,
            run_number=0,
            identity="aaa",
            corrupt_series_after_hashing=True,
        )

        with pytest.raises(ExtractionInputAbsentError):
            extract_era5_t2m.discover_t2m_bundle(tmp_path)

    def test_a_direct_read_of_a_named_corrupt_bundle_still_raises(
        self, tmp_path: Path
    ) -> None:
        """The skip behaviour is local to `discover_t2m_bundle`'s loop, not
        a property of the underlying predicate: a caller that reads a
        NAMED bundle directly (bypassing discovery) must still get a raised
        exception, never a silent skip."""
        points_root = extract_era5_t2m.t2m_points_dir(tmp_path)
        corrupt_dir = _write_fake_t2m_bundle(
            points_root,
            run_number=0,
            identity="aaa",
            corrupt_series_after_hashing=True,
        )
        manifest = extract_era5_t2m._read_t2m_manifest(
            corrupt_dir / extract_era5_t2m.manifest_filename()
        )
        assert manifest is not None

        with pytest.raises(ExtractionPostConditionError, match="sha256"):
            extract_era5_t2m.assert_payload_checksum_matches(
                corrupt_dir, manifest, extract_era5_t2m._T2M_SERIES_FILENAME
            )

    def test_real_run_publishes_a_payload_checksum_that_reconciles(
        self, tmp_path: Path
    ) -> None:
        """The real writer, not a hand-built fixture: `run()` must record a
        `payload_sha256s` entry for the series file that actually
        reconciles against the bytes it wrote, and `discover_t2m_bundle`
        must find that bundle via the checksum-reconciling path (not merely
        because it is the only candidate)."""
        data_root = _build_t2m_root(tmp_path)
        precip_root = tmp_path / "precip"
        _build_precip_bundle(precip_root)
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)

        exit_code = extract_era5_t2m.main(
            [
                "--data-root",
                str(data_root),
                "--precip-data-root",
                str(precip_root),
            ],
            clock=_CLOCK,
            coords_path=coords_path,
            expected_stations=frozenset({Station(s[0]) for s in _STATIONS}),
            params=_TEST_PARAMS,
            request_area=_AREA,
        )
        assert exit_code == 0

        bundle_dir, manifest = extract_era5_t2m.discover_t2m_bundle(data_root)
        series_path = bundle_dir / "series_t2m_degc.nc"
        assert manifest.payload_sha256s == {
            "series_t2m_degc.nc": checksum_file(series_path)
        }

        # Corrupt the published series in place; discovery must now refuse
        # this bundle (there is no older one to fall back to).
        series_path.write_bytes(series_path.read_bytes() + b"-corrupted")
        with pytest.raises(ExtractionInputAbsentError):
            extract_era5_t2m.discover_t2m_bundle(data_root)


class TestPublishFailureCleansUpStaging:
    """Round-3 review (2026-08-20), finding 2 — a handled publish failure
    used to strand its staging directory: `_publish_t2m_bundle` sat OUTSIDE
    the `try`/cleanup that only covered the two writers. Publication now
    runs inside the same block, covered by a `finally` that always removes
    the staging directory."""

    def test_publish_failure_removes_the_staging_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_root = _build_t2m_root(tmp_path)
        precip_root = tmp_path / "precip"
        _build_precip_bundle(precip_root)
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)

        def _boom(staged_dir: Path, *, data_root: Path, identity: str) -> Path:
            raise Era5StorageError("simulated publish failure")

        monkeypatch.setattr(extract_era5_t2m, "_publish_t2m_bundle", _boom)

        exit_code = extract_era5_t2m.main(
            [
                "--data-root",
                str(data_root),
                "--precip-data-root",
                str(precip_root),
            ],
            clock=_CLOCK,
            coords_path=coords_path,
            expected_stations=frozenset({Station(s[0]) for s in _STATIONS}),
            params=_TEST_PARAMS,
            request_area=_AREA,
        )
        assert exit_code == 5

        staging_root = extract_era5_t2m.t2m_points_dir(data_root) / ".staging"
        remaining = list(staging_root.iterdir()) if staging_root.exists() else []
        assert remaining == []

    def test_publish_success_leaves_no_staging_directory_either(
        self, tmp_path: Path
    ) -> None:
        data_root = _build_t2m_root(tmp_path)
        precip_root = tmp_path / "precip"
        _build_precip_bundle(precip_root)
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)

        exit_code = extract_era5_t2m.main(
            [
                "--data-root",
                str(data_root),
                "--precip-data-root",
                str(precip_root),
            ],
            clock=_CLOCK,
            coords_path=coords_path,
            expected_stations=frozenset({Station(s[0]) for s in _STATIONS}),
            params=_TEST_PARAMS,
            request_area=_AREA,
        )
        assert exit_code == 0

        staging_root = extract_era5_t2m.t2m_points_dir(data_root) / ".staging"
        remaining = list(staging_root.iterdir()) if staging_root.exists() else []
        assert remaining == []
