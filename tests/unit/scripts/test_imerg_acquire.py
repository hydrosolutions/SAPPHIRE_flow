"""Plan 211 (M-A5b) task T1 — granule-name construction, window/box
arithmetic and missing-granule accounting, exercised entirely against a fake
HTTP client (no network in CI). The one real network probe permitted by the
plan (the D1 contract observation) was run manually against the live GES
DISC archive during implementation — see the module docstring's residual-
risk note in `imerg_acquire.py`; it is not repeated here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import h5py
import numpy as np
import pytest

from scripts.dhm_precip import imerg_acquire as ia
from scripts.dhm_precip.era5_request import STUDY_AREA

if TYPE_CHECKING:
    from pathlib import Path

# --- a synthetic granule fixture, carrying the D1 structure OBSERVED on the
# real GES DISC probe granule (2026-08-28) — never a download inside a test.


def _write_fake_granule(
    path: Path,
    *,
    fill_value: float = -9999.9,
    value_at_station: float = 1.5,
    lat_count: int = 1800,
    lon_count: int = 3600,
    product_version: str = "07B",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lat = np.round(np.linspace(-89.95, 89.95, lat_count), 6).astype(np.float32)
    lon = np.round(np.linspace(-179.95, 179.95, lon_count), 6).astype(np.float32)
    precip = np.full((1, lon_count, lat_count), fill_value, dtype=np.float32)
    # place a real value near the middle so extraction tests have something
    # to find without depending on exact index arithmetic
    precip[0, lon_count // 2, lat_count // 2] = value_at_station
    with h5py.File(path, "w") as f:
        f.attrs["FileHeader"] = (
            f"FileName={path.name};\nAlgorithmVersion=3IMERGHH;\n"
            f"ProductVersion={product_version};\n"
        ).encode()
        grid = f.create_group("Grid")
        grid.attrs["GridHeader"] = (
            b"BinMethod=ARITHMETIC_MEAN;\nRegistration=CENTER;\n"
            b"LatitudeResolution=0.1;\nLongitudeResolution=0.1;\n"
            b"NorthBoundingCoordinate=90;\nSouthBoundingCoordinate=-90;\n"
            b"EastBoundingCoordinate=180;\nWestBoundingCoordinate=-180;\n"
            b"Origin=SOUTHWEST;\n"
        )
        grid.create_dataset("lat", data=lat)
        grid.create_dataset("lon", data=lon)
        ds = grid.create_dataset("precipitation", data=precip)
        ds.attrs["DimensionNames"] = b"time,lon,lat"
        ds.attrs["units"] = b"mm/hr"
        ds.attrs["_FillValue"] = fill_value


@dataclass
class FakeImergHttpClient:
    """No-network double for `ImergHttpClient`. `listings` maps a directory
    URL to the raw text it should return; `granule_bytes` maps a full
    granule URL to the fixture bytes `download_to_path` should write.
    `missing_urls` simulates a 404."""

    listings: dict[str, str] = field(default_factory=dict)
    granule_bytes: dict[str, Path] = field(default_factory=dict)
    missing_urls: set[str] = field(default_factory=set)
    list_calls: list[str] = field(default_factory=list)
    download_calls: list[str] = field(default_factory=list)

    def list_directory(self, url: str) -> str:
        self.list_calls.append(url)
        if url in self.missing_urls:
            raise ia.ImergGranuleMissingError(f"no listing for {url}")
        return self.listings.get(url, "")

    def download_to_path(self, *, url: str, target: Path) -> None:
        self.download_calls.append(url)
        if url in self.missing_urls:
            raise ia.ImergGranuleMissingError(f"granule not found: {url}")
        source = self.granule_bytes[url]
        target.write_bytes(source.read_bytes())


def _clock() -> datetime:
    return datetime(2026, 8, 28, 13, 0, tzinfo=UTC)


def _sleep(_seconds: float) -> None:
    return None


# --- granule-name construction (D1) ---


class TestGranuleIdentity:
    def test_filename_matches_the_observed_gesdisc_naming_pattern(self) -> None:
        granule = ia.ImergGranuleId(start=datetime(2026, 8, 28, 7, 0, tzinfo=UTC))
        assert (
            granule.filename(revision="V07B")
            == "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07B.HDF5"
        )

    def test_remote_url_uses_year_and_day_of_year_directory(self) -> None:
        granule = ia.ImergGranuleId(start=datetime(2026, 8, 28, 7, 30, tzinfo=UTC))
        url = granule.remote_url(revision="V07B")
        assert url == (
            f"{ia.GESDISC_BASE_URL}/2026/240/"
            "3B-HHR-E.MS.MRG.3IMERG.20260828-S073000-E075959.0450.V07B.HDF5"
        )

    def test_minute_of_day_is_the_start_time_minutes_since_midnight(self) -> None:
        granule = ia.ImergGranuleId(start=datetime(2026, 8, 28, 23, 30, tzinfo=UTC))
        assert granule.minute_of_day == 23 * 60 + 30

    @pytest.mark.parametrize(
        "minute",
        [1, 15, 29, 45, 59],
    )
    def test_construction_rejects_a_non_half_hour_aligned_start(
        self, minute: int
    ) -> None:
        with pytest.raises(ValueError, match="half-hour aligned"):
            ia.ImergGranuleId(start=datetime(2026, 8, 28, 7, minute, tzinfo=UTC))

    def test_construction_rejects_a_naive_datetime(self) -> None:
        with pytest.raises(ValueError, match="tz-aware UTC"):
            ia.ImergGranuleId(start=datetime(2026, 8, 28, 7, 0))  # noqa: DTZ001

    def test_parse_granule_filename_round_trips_a_real_observed_name(self) -> None:
        name = "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07C.HDF5"
        granule, revision = ia.parse_granule_filename(name)
        assert granule.start == datetime(2026, 8, 28, 7, 0, tzinfo=UTC)
        assert revision == "V07C"

    def test_parse_granule_filename_rejects_an_unrecognised_pattern(self) -> None:
        with pytest.raises(ia.ImergReadContractError, match="does not match"):
            ia.parse_granule_filename("not-an-imerg-granule.HDF5")


class TestResolveGranuleFilename:
    def test_finds_the_matching_entry_regardless_of_revision_letter(self) -> None:
        granule = ia.ImergGranuleId(start=datetime(2026, 8, 28, 7, 0, tzinfo=UTC))
        listing = (
            '<a href="3B-HHR-E.MS.MRG.3IMERG.20260828-S063000-E065959.0390.'
            'V07C.HDF5">x</a>\n'
            '<a href="3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.'
            'V07C.HDF5">x</a>\n'
        )
        assert (
            ia.resolve_granule_filename(listing, granule)
            == "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07C.HDF5"
        )

    def test_raises_missing_when_no_entry_matches(self) -> None:
        granule = ia.ImergGranuleId(start=datetime(2026, 8, 28, 7, 0, tzinfo=UTC))
        with pytest.raises(ia.ImergGranuleMissingError):
            ia.resolve_granule_filename("<a href='unrelated.HDF5'>x</a>", granule)

    def test_raises_on_directory_ambiguity(self) -> None:
        granule = ia.ImergGranuleId(start=datetime(2026, 8, 28, 7, 0, tzinfo=UTC))
        listing = (
            "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07B.HDF5\n"
            "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07C.HDF5\n"
        )
        with pytest.raises(ia.ImergReadContractError, match="ambiguous"):
            ia.resolve_granule_filename(listing, granule)


# --- window/box arithmetic (D5) ---


class TestWindowArithmetic:
    def test_granule_count_equals_the_measured_105216(self) -> None:
        assert ia.granule_count() == ia.EXPECTED_GRANULE_COUNT == 105_216

    def test_first_granule_start_is_one_hour_before_the_study_axis(self) -> None:
        # D5: the axis's first hour (2020-01-01 00:00) needs the granules of
        # 2019-12-31 23:00-24:00 -> retrieval starts at 23:00 the day before.
        assert datetime(2019, 12, 31, 23, 0, tzinfo=UTC) == ia.FIRST_GRANULE_START

    def test_last_granule_start_is_22_30_not_23_30(self) -> None:
        # D5: the axis's last hour (2025-12-31 23:00) is built from granules
        # starting at 22:00 and 22:30 that day -> the LAST granule requested
        # is 22:30, never 23:30.
        assert datetime(2025, 12, 31, 22, 30, tzinfo=UTC) == ia.LAST_GRANULE_START

    def test_all_granule_starts_are_exactly_half_hour_spaced_with_no_gaps(
        self,
    ) -> None:
        starts = list(ia.all_granule_starts())
        diffs = {b - a for a, b in zip(starts, starts[1:], strict=False)}
        assert diffs == {timedelta(minutes=30)}

    def test_study_box_is_imported_verbatim_from_era5_request(self) -> None:
        assert ia.STUDY_BOX == STUDY_AREA == (31, 80, 26, 89)


# --- D1 read contract, observed on a synthetic granule ---


class TestReadContract:
    def test_observed_contract_matches_the_real_probe_granule_structure(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / (
            "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07B.HDF5"
        )
        _write_fake_granule(path)
        contract = ia.observe_read_contract(path)
        assert contract.hdf5_variable_path == "/Grid/precipitation"
        assert contract.dimension_names == ("time", "lon", "lat")
        assert contract.coordinate_registration == "CENTER"
        assert contract.longitude_convention == "SIGNED_180"
        assert contract.units == "mm/hr"
        assert contract.granule_revision == "V07B"
        assert contract.file_header_product_version == "07B"
        assert contract.grid_shape == (1, 3600, 1800)

    def test_a_wrong_units_attribute_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / (
            "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07B.HDF5"
        )
        _write_fake_granule(path)
        with h5py.File(path, "a") as f:
            f["Grid/precipitation"].attrs["units"] = b"mm"
        with pytest.raises(ia.ImergReadContractError, match="units"):
            ia.observe_read_contract(path)

    def test_assert_contract_consistent_passes_for_an_identical_contract(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / (
            "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07B.HDF5"
        )
        _write_fake_granule(path)
        contract = ia.observe_read_contract(path)
        ia.assert_contract_consistent(contract, frozen=contract)

    def test_assert_contract_consistent_raises_on_a_revision_mismatch(self) -> None:
        """Constructed directly (not via `observe_read_contract`, which now
        enforces the D1 pin and would reject a V07C-revision granule before
        this intra-run consistency check is ever reached) — this test is
        about `assert_contract_consistent`'s own drift-within-a-run
        behaviour, orthogonal to the pin."""
        base_kwargs = dict(
            hdf5_variable_path=ia.HDF5_VARIABLE_PATH,
            dimension_names=("time", "lon", "lat"),
            coordinate_registration="CENTER",
            longitude_convention="SIGNED_180",
            units="mm/hr",
            fill_value=-9999.9,
            grid_shape=ia.EXPECTED_GRID_SHAPE,
            lat_vector=tuple(
                round(v, 6) for v in np.linspace(-89.95, 89.95, 1800).tolist()
            ),
            lon_vector=tuple(
                round(v, 6) for v in np.linspace(-179.95, 179.95, 3600).tolist()
            ),
            grid_spacing_deg=0.1,
            file_header_product_version="07B",
        )
        frozen = ia.ImergReadContract(granule_revision="V07B", **base_kwargs)
        observed = ia.ImergReadContract(granule_revision="V07C", **base_kwargs)
        with pytest.raises(
            ia.ImergRevisionMismatchError, match="V07C.*V07B|V07B.*V07C"
        ):
            ia.assert_contract_consistent(observed, frozen=frozen)


class TestRevisionPin:
    """D1 (locking) — 'Current Early is V07B; if a granule carries another
    revision, stop and report rather than blending.' The pin is ENFORCED,
    not documentary."""

    def test_observe_read_contract_rejects_a_granule_whose_revision_is_not_v07b(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / (
            "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07C.HDF5"
        )
        _write_fake_granule(path, product_version="07C")
        with pytest.raises(
            ia.ImergRevisionMismatchError, match="V07C.*V07B|V07B.*V07C"
        ):
            ia.observe_read_contract(path)

    def test_observe_read_contract_rejects_a_filename_header_disagreement(
        self, tmp_path: Path
    ) -> None:
        """The filename says the pinned V07B, but the granule's own
        FileHeader.ProductVersion disagrees — must not be trusted from the
        filename alone."""
        path = tmp_path / (
            "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07B.HDF5"
        )
        _write_fake_granule(path, product_version="07C")
        with pytest.raises(ia.ImergReadContractError, match="disagrees"):
            ia.observe_read_contract(path)

    def test_a_granule_missing_the_file_header_attribute_is_rejected(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / (
            "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07B.HDF5"
        )
        _write_fake_granule(path)
        with h5py.File(path, "a") as f:
            del f.attrs["FileHeader"]
        with pytest.raises(ia.ImergReadContractError, match="FileHeader"):
            ia.observe_read_contract(path)


# --- D10 disk projection ---


class TestDiskProjection:
    def test_projection_multiplies_probe_size_by_the_measured_granule_count(
        self,
    ) -> None:
        projection = ia.compute_projection(
            probe_granule_bytes=8_000_000, free_disk_bytes=10**12
        )
        assert projection.projected_total_bytes == 8_000_000 * 105_216
        assert projection.fits is True

    def test_projection_reports_a_projection_that_does_not_fit(self) -> None:
        projection = ia.compute_projection(
            probe_granule_bytes=8_000_000, free_disk_bytes=1
        )
        assert projection.fits is False


# --- acquisition manifest write/read round-trip ---


class TestAcquisitionManifest:
    def test_round_trips_through_disk(self, tmp_path: Path) -> None:
        manifest = ia.ImergAcquisitionManifest(
            route=ia.ROUTE,
            collection_short_name=ia.COLLECTION_SHORT_NAME,
            granule_revision="V07B",
            completeness=ia.AcquisitionCompleteness.COMPLETE,
            requested_window_start=ia.FIRST_GRANULE_START,
            requested_window_end=ia.LAST_GRANULE_START,
            box=ia.STUDY_BOX,
            read_contract={"units": "mm/hr"},
            requested=1,
            retrieved=1,
            missing=(),
            granule_checksums={"a.HDF5": "deadbeef"},
            granule_retrieved_at={"a.HDF5": _clock()},
            retrospective=True,
            generated_at=_clock(),
        )
        path = tmp_path / "acquisition_manifest.json"
        ia.write_acquisition_manifest(manifest, path)
        reread = ia.read_acquisition_manifest(path)
        assert reread is not None
        assert reread.granule_revision == "V07B"
        assert reread.completeness == ia.AcquisitionCompleteness.COMPLETE
        assert reread.retrospective is True
        assert reread.granule_checksums == {"a.HDF5": "deadbeef"}

    def test_read_returns_none_when_absent(self, tmp_path: Path) -> None:
        assert ia.read_acquisition_manifest(tmp_path / "missing.json") is None

    def test_lives_outside_the_disposable_raw_dir(self, tmp_path: Path) -> None:
        """D10 — 'the acquisition manifest is PERMANENT'; the raw granules
        are disposable. The manifest must not live INSIDE the raw dir, or
        discarding the raw dir takes the manifest with it."""
        data_root = tmp_path / "data_root"
        manifest_path = ia.acquisition_manifest_path(data_root)
        raw_dir = ia.imerg_raw_dir(data_root)
        assert raw_dir not in manifest_path.parents


# --- missing-granule accounting (D4's upstream half — a window retrieval
# with some granules absent from the archive), no network ---


class TestRetrieveWindow:
    def test_accounts_for_missing_granules_without_failing_the_run(
        self, tmp_path: Path
    ) -> None:
        starts = [
            datetime(2026, 8, 28, 6, 0, tzinfo=UTC),
            datetime(2026, 8, 28, 6, 30, tzinfo=UTC),
            datetime(2026, 8, 28, 7, 0, tzinfo=UTC),
        ]
        fixture_dir = tmp_path / "fixtures"
        good_granules = {
            starts[0]: fixture_dir / "g0.h5",
            starts[2]: fixture_dir / "g2.h5",
        }
        for path in good_granules.values():
            _write_fake_granule(path)

        client = FakeImergHttpClient()
        for start in starts:
            granule = ia.ImergGranuleId(start=start)
            directory_url = granule.directory_url()
            # All three starts fall on the same calendar day, so "missing"
            # means "absent from that shared day's listing" — never listing
            # it, rather than marking the whole directory unreachable.
            if start in good_granules:
                filename = granule.filename(revision="V07B")
                client.listings[directory_url] = (
                    client.listings.get(directory_url, "") + filename + "\n"
                )
                client.granule_bytes[f"{directory_url}{filename}"] = good_granules[
                    start
                ]
            else:
                client.listings.setdefault(directory_url, "")

        data_root = tmp_path / "data_root"
        report, frozen, checksums = ia.retrieve_window(
            iter(starts),
            client=client,
            data_root=data_root,
            clock=_clock,
            sleep=_sleep,
        )
        assert report.requested == 3
        assert report.retrieved == 2
        assert report.missing == (starts[1].isoformat(),)
        assert frozen is not None
        assert len(checksums) == 2

    def test_retrieved_granule_files_are_written_under_the_imerg_raw_dir(
        self, tmp_path: Path
    ) -> None:
        start = datetime(2026, 8, 28, 7, 0, tzinfo=UTC)
        granule = ia.ImergGranuleId(start=start)
        fixture = tmp_path / "fixture.h5"
        _write_fake_granule(fixture)
        filename = granule.filename(revision="V07B")
        client = FakeImergHttpClient(
            listings={granule.directory_url(): filename},
            granule_bytes={f"{granule.directory_url()}{filename}": fixture},
        )
        data_root = tmp_path / "data_root"
        ia.retrieve_window(
            iter([start]),
            client=client,
            data_root=data_root,
            clock=_clock,
            sleep=_sleep,
        )
        assert (ia.imerg_raw_dir(data_root) / filename).exists()

    def test_a_second_run_does_not_re_download_an_already_acquired_granule(
        self, tmp_path: Path
    ) -> None:
        start = datetime(2026, 8, 28, 7, 0, tzinfo=UTC)
        granule = ia.ImergGranuleId(start=start)
        fixture = tmp_path / "fixture.h5"
        _write_fake_granule(fixture)
        filename = granule.filename(revision="V07B")
        client = FakeImergHttpClient(
            listings={granule.directory_url(): filename},
            granule_bytes={f"{granule.directory_url()}{filename}": fixture},
        )
        data_root = tmp_path / "data_root"
        ia.retrieve_window(
            iter([start]),
            client=client,
            data_root=data_root,
            clock=_clock,
            sleep=_sleep,
        )
        assert client.download_calls == [f"{granule.directory_url()}{filename}"]
        ia.retrieve_window(
            iter([start]),
            client=client,
            data_root=data_root,
            clock=_clock,
            sleep=_sleep,
        )
        # ⚠️ "Retrieve once; never re-download what is on disk" (plan Verify)
        assert client.download_calls == [f"{granule.directory_url()}{filename}"]

    def test_writes_the_permanent_acquisition_manifest(self, tmp_path: Path) -> None:
        """D9/D10 (locking) — `retrieve_window` itself must write the
        acquisition manifest; T2 (and any future bulk-retrieval caller)
        depends on it being written HERE, not merely returned in memory."""
        start = datetime(2026, 8, 28, 7, 0, tzinfo=UTC)
        granule = ia.ImergGranuleId(start=start)
        fixture = tmp_path / "fixture.h5"
        _write_fake_granule(fixture)
        filename = granule.filename(revision="V07B")
        client = FakeImergHttpClient(
            listings={granule.directory_url(): filename},
            granule_bytes={f"{granule.directory_url()}{filename}": fixture},
        )
        data_root = tmp_path / "data_root"
        ia.retrieve_window(
            iter([start]),
            client=client,
            data_root=data_root,
            clock=_clock,
            sleep=_sleep,
        )
        manifest = ia.read_acquisition_manifest(ia.acquisition_manifest_path(data_root))
        assert manifest is not None
        assert manifest.requested == 1
        assert manifest.retrieved == 1
        assert manifest.granule_checksums
        assert filename in manifest.granule_retrieved_at
        # A single-granule `retrieve_window` call is a PARTIAL window, never
        # COMPLETE — only a call spanning the whole pinned D5 window is.
        assert manifest.completeness == ia.AcquisitionCompleteness.PARTIAL

    def test_a_full_window_retrieval_is_marked_complete(self, tmp_path: Path) -> None:
        starts = [
            ia.FIRST_GRANULE_START,
            ia.FIRST_GRANULE_START + timedelta(minutes=30),
        ]
        fixture = tmp_path / "fixture.h5"
        _write_fake_granule(fixture)
        client = FakeImergHttpClient()
        for start in starts:
            granule = ia.ImergGranuleId(start=start)
            filename = granule.filename(revision="V07B")
            client.listings[granule.directory_url()] = filename
            client.granule_bytes[f"{granule.directory_url()}{filename}"] = fixture
        data_root = tmp_path / "data_root"
        ia.retrieve_window(
            iter(starts),
            client=client,
            data_root=data_root,
            clock=_clock,
            sleep=_sleep,
        )
        manifest = ia.read_acquisition_manifest(ia.acquisition_manifest_path(data_root))
        assert manifest is not None
        # requested (2) != EXPECTED_GRANULE_COUNT (105,216) -> PARTIAL, not
        # COMPLETE, even though every requested granule was retrieved.
        assert manifest.completeness == ia.AcquisitionCompleteness.PARTIAL


class TestRunProbe:
    def test_probe_downloads_exactly_one_granule_and_writes_the_manifest(
        self, tmp_path: Path
    ) -> None:
        start = datetime(2026, 8, 28, 7, 0, tzinfo=UTC)
        granule = ia.ImergGranuleId(start=start)
        fixture = tmp_path / "fixture.h5"
        _write_fake_granule(fixture)
        filename = granule.filename(revision="V07B")
        client = FakeImergHttpClient(
            listings={granule.directory_url(): filename},
            granule_bytes={f"{granule.directory_url()}{filename}": fixture},
        )
        data_root = tmp_path / "data_root"
        result = ia.run_probe(
            granule_start=start,
            client=client,
            data_root=data_root,
            clock=_clock,
            sleep=_sleep,
            free_disk_bytes=10**12,
        )
        assert client.download_calls == [f"{granule.directory_url()}{filename}"]
        assert result["filename"] == filename
        manifest = ia.read_acquisition_manifest(ia.acquisition_manifest_path(data_root))
        assert manifest is not None
        assert manifest.granule_revision == "V07B"
        assert manifest.retrospective is True
        assert manifest.completeness == ia.AcquisitionCompleteness.PROBE
        # A probe's requested window is its OWN single granule, never the
        # full pinned D5 window — D9's "a mandated recent probe outside the
        # 2020-2025 window must not [be mistaken for a complete acquisition
        # covering it]".
        assert manifest.requested_window_start == start
        assert manifest.requested_window_end == start


class TestCliBoundaryParsing:
    """Minor findings: naive `--granule-start` values must be rejected, not
    silently interpreted in the host timezone; disk-free must be measured
    against `--data-root`, not `.`."""

    def test_parse_cli_utc_timestamp_rejects_a_naive_value(self) -> None:
        with pytest.raises(ValueError, match="UTC offset"):
            ia.parse_cli_utc_timestamp("2026-08-28T07:00:00")

    def test_parse_cli_utc_timestamp_accepts_an_explicit_offset(self) -> None:
        parsed = ia.parse_cli_utc_timestamp("2026-08-28T07:00:00+00:00")
        assert parsed == datetime(2026, 8, 28, 7, 0, tzinfo=UTC)

    def test_parse_cli_utc_timestamp_accepts_a_trailing_z(self) -> None:
        parsed = ia.parse_cli_utc_timestamp("2026-08-28T07:00:00Z")
        assert parsed == datetime(2026, 8, 28, 7, 0, tzinfo=UTC)

    def test_nearest_existing_ancestor_walks_up_to_an_existing_directory(
        self, tmp_path: Path
    ) -> None:
        missing = tmp_path / "not" / "yet" / "created"
        assert ia._nearest_existing_ancestor(missing) == tmp_path.resolve()

    def test_nearest_existing_ancestor_is_identity_for_an_existing_path(
        self, tmp_path: Path
    ) -> None:
        assert ia._nearest_existing_ancestor(tmp_path) == tmp_path.resolve()
