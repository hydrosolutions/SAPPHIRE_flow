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
from typing import TYPE_CHECKING, Any

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
    file_header_filename: str | None = None,
) -> None:
    """`file_header_filename` overrides the name embedded in the granule's
    own `FileHeader.FileName` — the field D1's revision cross-check compares
    against the PATH (same axis). Fixture files are written under a scratch
    name and then copied to their real granule name by the fake client, so
    every fixture that will be served as a granule must declare that real
    name here."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lat = np.round(np.linspace(-89.95, 89.95, lat_count), 6).astype(np.float32)
    lon = np.round(np.linspace(-179.95, 179.95, lon_count), 6).astype(np.float32)
    precip = np.full((1, lon_count, lat_count), fill_value, dtype=np.float32)
    # place a real value near the middle so extraction tests have something
    # to find without depending on exact index arithmetic
    precip[0, lon_count // 2, lat_count // 2] = value_at_station
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


def _contract_kwargs() -> dict[str, Any]:
    """Every `ImergReadContract` field except `granule_revision`, so a test
    can vary exactly one of them."""
    return {
        "hdf5_variable_path": ia.HDF5_VARIABLE_PATH,
        "dimension_names": ("time", "lon", "lat"),
        "coordinate_registration": "CENTER",
        "longitude_convention": "SIGNED_180",
        "units": "mm/hr",
        "fill_value": -9999.9,
        "grid_shape": ia.EXPECTED_GRID_SHAPE,
        "lat_vector": tuple(np.linspace(-89.95, 89.95, 1800).tolist()),
        "lon_vector": tuple(np.linspace(-179.95, 179.95, 3600).tolist()),
        "grid_spacing_deg": 0.1,
        "file_header_product_version": "07B",
    }


_READ_CONTRACT_KEYS_STUB: dict[str, Any] = dict.fromkeys(
    ia.ImergReadContract.__dataclass_fields__, "recorded"
)


def _derivation_complete_manifest(
    *, granule_checksums: dict[str, str] | None = None, **overrides: Any
) -> ia.ImergAcquisitionManifest:
    """A manifest that DERIVES as COMPLETE: every half-hour start of the
    pinned D5 window is accounted for, retrieved or missing. Each test
    overrides the one field it is about."""
    checksums = granule_checksums or {}
    retrieved_starts = {ia.parse_granule_filename(name)[0].start for name in checksums}
    fields: dict[str, Any] = {
        "route": ia.ROUTE,
        "collection_short_name": ia.COLLECTION_SHORT_NAME,
        "granule_revision": ia.PINNED_GRANULE_REVISION_PER_PLAN,
        "requested_window_start": ia.FIRST_GRANULE_START,
        "requested_window_end": ia.LAST_GRANULE_START,
        "box": ia.STUDY_BOX,
        "read_contract": _READ_CONTRACT_KEYS_STUB,
        "requested": ia.EXPECTED_GRANULE_COUNT,
        "retrieved": len(checksums),
        "missing": tuple(
            start.isoformat()
            for start in ia.all_granule_starts()
            if start not in retrieved_starts
        ),
        "granule_checksums": checksums,
        "granule_retrieved_at": {name: _clock() for name in checksums},
        "retrospective": True,
        "generated_at": _clock(),
    }
    fields.update(overrides)
    return ia.ImergAcquisitionManifest(**fields)


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
        # DERIVED from the pinned window's arithmetic, never restated as a
        # bare literal (D10) — and it still has to equal the measured 105,216.
        starts = list(ia.all_granule_starts())
        assert ia.EXPECTED_GRANULE_COUNT == len(starts) == 105_216

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
        base_kwargs = _contract_kwargs()
        frozen = ia.ImergReadContract(granule_revision="V07B", **base_kwargs)
        observed = ia.ImergReadContract(granule_revision="V07C", **base_kwargs)
        with pytest.raises(ia.ImergReadContractError, match="V07C.*V07B|V07B.*V07C"):
            ia.assert_contract_consistent(observed, frozen=frozen)

    def test_a_lat_vector_perturbed_below_six_decimals_is_rejected(self) -> None:
        """D1 (locking) — the stated REASON the vectors are frozen: "two
        conforming granules ... can map the same station to different cells
        ... verifying only the field name is not enough". Every other field
        is identical here; only one latitude moves, by 1e-9. ⛔ That is
        BELOW the six decimals the vectors used to be rounded to, so the
        rounding itself defeated the exact-vector requirement."""
        base_kwargs = _contract_kwargs()
        frozen = ia.ImergReadContract(granule_revision="V07B", **base_kwargs)
        perturbed = dict(base_kwargs)
        lat = base_kwargs["lat_vector"]
        assert isinstance(lat, tuple)
        perturbed["lat_vector"] = (lat[0] + 1e-9, *lat[1:])
        observed = ia.ImergReadContract(granule_revision="V07B", **perturbed)
        with pytest.raises(ia.ImergReadContractError, match="other than revision"):
            ia.assert_contract_consistent(observed, frozen=frozen)

    def test_a_lon_vector_shifted_by_one_cell_is_rejected(self) -> None:
        base_kwargs = _contract_kwargs()
        frozen = ia.ImergReadContract(granule_revision="V07B", **base_kwargs)
        shifted = dict(base_kwargs)
        lon = base_kwargs["lon_vector"]
        assert isinstance(lon, tuple)
        shifted["lon_vector"] = tuple(v + 0.1 for v in lon)
        observed = ia.ImergReadContract(granule_revision="V07B", **shifted)
        with pytest.raises(ia.ImergReadContractError, match="other than revision"):
            ia.assert_contract_consistent(observed, frozen=frozen)

    def test_observed_vectors_are_exact_never_rounded_to_six_decimals(
        self, tmp_path: Path
    ) -> None:
        """D1 (locking) — the vectors are recorded EXACTLY. The fixture's
        own float32 -89.95 reads back as -89.94999694824219; a six-decimal
        round would record -89.95 and silently absorb any drift smaller
        than 1e-6."""
        path = tmp_path / (
            "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07B.HDF5"
        )
        _write_fake_granule(path)
        contract = ia.observe_read_contract(path)
        assert contract.lat_vector[0] == float(np.float32(-89.95))
        assert contract.lat_vector[0] != round(float(np.float32(-89.95)), 6)


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
        with pytest.raises(ia.ImergReadContractError, match="V07C.*V07B|V07B.*V07C"):
            ia.observe_read_contract(path)

    def test_observe_read_contract_rejects_a_path_embedded_name_disagreement(
        self, tmp_path: Path
    ) -> None:
        """The PATH says the pinned V07B, but the granule's own embedded
        `FileHeader.FileName` says V07C — a renamed file. The revision must
        never be inferred from the path alone."""
        path = tmp_path / (
            "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07B.HDF5"
        )
        _write_fake_granule(
            path,
            file_header_filename=(
                "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07C.HDF5"
            ),
        )
        with pytest.raises(ia.ImergReadContractError, match="disagrees"):
            ia.observe_read_contract(path)

    def test_a_product_version_that_differs_from_the_filename_is_not_a_violation(
        self, tmp_path: Path
    ) -> None:
        """⛔ The 2026-08-28 probe MEASURED a granule whose filename (and
        embedded `FileHeader.FileName`) read V07C while its
        `FileHeader.ProductVersion` read `07B`: the reprocessing-generation
        letter and the ATBD product version are different axes. Equating
        them would reject legitimate granules, so `ProductVersion` is
        RECORDED as its own frozen contract field and never compared with
        the filename revision."""
        path = tmp_path / (
            "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07B.HDF5"
        )
        _write_fake_granule(path, product_version="07A")
        contract = ia.observe_read_contract(path)
        assert contract.granule_revision == "V07B"
        assert contract.file_header_product_version == "07A"

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
        for start, path in good_granules.items():
            _write_fake_granule(
                path,
                file_header_filename=ia.ImergGranuleId(start=start).filename(
                    revision="V07B"
                ),
            )

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
        filename = granule.filename(revision="V07B")
        fixture = tmp_path / "fixture.h5"
        _write_fake_granule(fixture, file_header_filename=filename)
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
        filename = granule.filename(revision="V07B")
        fixture = tmp_path / "fixture.h5"
        _write_fake_granule(fixture, file_header_filename=filename)
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
        filename = granule.filename(revision="V07B")
        fixture = tmp_path / "fixture.h5"
        _write_fake_granule(fixture, file_header_filename=filename)
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
        # A single-granule `retrieve_window` call does not account for the
        # pinned window, so it never DERIVES as complete.
        assert ia.is_complete_acquisition(manifest) is False

    def test_a_two_granule_retrieval_starting_at_the_window_edge_is_partial(
        self, tmp_path: Path
    ) -> None:
        """⛔ Starting at the pinned window's own first granule is NOT enough
        to be COMPLETE: completeness is DERIVED from the record, and two
        granules do not account for the window's 105,216 half-hour starts."""
        starts = [
            ia.FIRST_GRANULE_START,
            ia.FIRST_GRANULE_START + timedelta(minutes=30),
        ]
        client = FakeImergHttpClient()
        for index, start in enumerate(starts):
            granule = ia.ImergGranuleId(start=start)
            filename = granule.filename(revision="V07B")
            fixture = tmp_path / f"fixture-{index}.h5"
            _write_fake_granule(fixture, file_header_filename=filename)
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
        # requested (2) != EXPECTED_GRANULE_COUNT (105,216) -> not complete,
        # even though every requested granule was retrieved.
        assert ia.is_complete_acquisition(manifest) is False


class TestRunProbe:
    def test_probe_downloads_exactly_one_granule_and_writes_the_manifest(
        self, tmp_path: Path
    ) -> None:
        start = datetime(2026, 8, 28, 7, 0, tzinfo=UTC)
        granule = ia.ImergGranuleId(start=start)
        filename = granule.filename(revision="V07B")
        fixture = tmp_path / "fixture.h5"
        _write_fake_granule(fixture, file_header_filename=filename)
        client = FakeImergHttpClient(
            listings={granule.directory_url(): filename},
            granule_bytes={f"{granule.directory_url()}{filename}": fixture},
        )
        data_root = tmp_path / "data_root"
        projection = ia.run_probe(
            granule_start=start,
            client=client,
            data_root=data_root,
            clock=_clock,
            sleep=_sleep,
            free_disk_bytes=10**12,
        )
        assert client.download_calls == [f"{granule.directory_url()}{filename}"]
        # D10 — the projection is MEASURED from this one granule, never
        # restated: probe size x the pinned window's granule count.
        assert projection.projected_granule_count == ia.EXPECTED_GRANULE_COUNT
        assert projection.projected_total_bytes == (
            projection.probe_granule_bytes * ia.EXPECTED_GRANULE_COUNT
        )
        manifest = ia.read_acquisition_manifest(ia.acquisition_manifest_path(data_root))
        assert manifest is not None
        assert manifest.granule_revision == "V07B"
        assert manifest.retrospective is True
        # A one-granule probe never derives as a complete acquisition, so T2
        # cannot mistake it for one (D9).
        assert ia.is_complete_acquisition(manifest) is False
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


# --- D9/D5 — completeness is DERIVED, never trusted (the blocker) ---


class TestAcquisitionCompletenessIsDerived:
    """⛔ The defect class this track has spent nine review rounds
    eliminating: a value published under an identity SUPPLIED alongside it
    rather than DERIVED from it. A manifest may CLAIM `COMPLETE`; the claim
    is worth nothing unless the record itself accounts for every granule of
    the pinned D5 window."""

    def test_a_fully_accounted_window_derives_complete(self) -> None:
        manifest = _derivation_complete_manifest()
        assert ia.acquisition_completeness_violations(manifest) == ()
        assert ia.is_complete_acquisition(manifest) is True
        ia.assert_acquisition_manifest_complete(manifest)

    def test_a_two_granule_one_hour_manifest_labelled_complete_is_rejected(
        self,
    ) -> None:
        """The EXACT shape the previous locking fixture built: a two-granule,
        one-hour window with `completeness=COMPLETE` — from which the
        end-to-end test then published a full 52,608-hour bundle."""
        starts = [
            ia.FIRST_GRANULE_START,
            ia.FIRST_GRANULE_START + timedelta(minutes=30),
        ]
        names = [ia.ImergGranuleId(start=s).filename(revision="V07B") for s in starts]
        manifest = _derivation_complete_manifest(
            granule_checksums={name: "deadbeef" for name in names},
            requested_window_end=starts[-1],
            requested=2,
            retrieved=2,
            missing=(),
        )
        with pytest.raises(ia.ImergRequestFailedError, match="105216|unaccounted"):
            ia.assert_acquisition_manifest_complete(manifest)

    def test_a_duplicate_plus_missing_sequence_is_rejected(self) -> None:
        """⛔ The counts all add up — retrieved 2 + missing 105,214 ==
        requested 105,216 — but one start is recorded BOTH retrieved and
        missing while another is recorded nowhere. Only comparing the
        accounted-for SET against the pinned window catches that."""
        all_starts = list(ia.all_granule_starts())
        retrieved = all_starts[:2]
        names = {
            ia.ImergGranuleId(start=s).filename(revision="V07B"): "deadbeef"
            for s in retrieved
        }
        manifest = _derivation_complete_manifest(
            granule_checksums=names,
            granule_retrieved_at={name: _clock() for name in names},
            missing=tuple(
                s.isoformat()
                for s in all_starts
                if s not in (all_starts[0], all_starts[2])
            ),
        )
        with pytest.raises(
            ia.ImergRequestFailedError, match="BOTH retrieved and missing"
        ):
            ia.assert_acquisition_manifest_complete(manifest)

    def test_a_shifted_window_start_is_rejected(self) -> None:
        """D5 — the first hour needs the two granules of 2019-12-31
        23:00-24:00; a window starting an hour later silently shifts the
        whole series."""
        manifest = _derivation_complete_manifest(
            requested_window_start=ia.FIRST_GRANULE_START + timedelta(hours=1)
        )
        with pytest.raises(ia.ImergRequestFailedError, match="window start"):
            ia.assert_acquisition_manifest_complete(manifest)

    def test_a_checksum_retrieval_time_key_mismatch_is_rejected(self) -> None:
        name = ia.ImergGranuleId(start=ia.FIRST_GRANULE_START).filename(revision="V07B")
        manifest = _derivation_complete_manifest(
            granule_checksums={name: "deadbeef"}, granule_retrieved_at={}
        )
        with pytest.raises(ia.ImergRequestFailedError, match="same granule filenames"):
            ia.assert_acquisition_manifest_complete(manifest)

    def test_a_gap_outside_the_pinned_window_is_rejected(self) -> None:
        """⛔ The counts still add up, but one recorded gap sits outside the
        pinned D5 window — so the accounting cannot be describing that
        window, however well its numbers balance."""
        all_starts = list(ia.all_granule_starts())
        manifest = _derivation_complete_manifest(
            missing=(
                *(s.isoformat() for s in all_starts[:-1]),
                (ia.LAST_GRANULE_START + timedelta(minutes=30)).isoformat(),
            )
        )
        with pytest.raises(ia.ImergRequestFailedError, match="inside the"):
            ia.assert_acquisition_manifest_complete(manifest)


# --- D10 — the PERMANENT record is not destructively replaceable ---


class TestAcquisitionManifestIsPermanent:
    def test_a_one_granule_record_may_not_overwrite_a_complete_one(
        self, tmp_path: Path
    ) -> None:
        """D10 (locking) — discarding the raw granules is only safe because
        the acquisition record survives. A later probe run writing the same
        fixed path would otherwise replace a 105,216-checksum record with a
        one-granule one, and nothing could ever confirm which bytes produced
        the bundle.

        ⛔ The writer must DERIVE the incoming record's completeness. The
        previous version only refused a record whose stored `completeness`
        LABEL said PROBE/PARTIAL — so this same one-granule record, labelled
        COMPLETE, replaced the permanent one. There is no label any more, and
        the derivation is what refuses it."""
        path = ia.acquisition_manifest_path(tmp_path)
        ia.write_acquisition_manifest(_derivation_complete_manifest(), path)
        name = ia.ImergGranuleId(start=ia.FIRST_GRANULE_START).filename(revision="V07B")
        probe = _derivation_complete_manifest(
            granule_checksums={name: "deadbeef"},
            requested_window_end=ia.FIRST_GRANULE_START,
            requested=1,
            retrieved=1,
            missing=(),
        )
        with pytest.raises(ia.ImergStorageError, match="never be downgraded"):
            ia.write_acquisition_manifest(probe, path)
        retained = ia.read_acquisition_manifest(path)
        assert retained is not None
        assert ia.is_complete_acquisition(retained) is True
        assert retained.requested == ia.EXPECTED_GRANULE_COUNT

    def test_a_redownload_whose_checksums_disagree_is_refused(
        self, tmp_path: Path
    ) -> None:
        """D10 — the retained checksums do not PREVENT a GES DISC archive
        revision; they make it DETECTABLE. That only holds if the old
        checksums are still there when the new ones arrive."""
        path = ia.acquisition_manifest_path(tmp_path)
        name = ia.ImergGranuleId(start=ia.FIRST_GRANULE_START).filename(revision="V07B")
        ia.write_acquisition_manifest(
            _derivation_complete_manifest(granule_checksums={name: "originalsha"}),
            path,
        )
        with pytest.raises(ia.ImergStorageError, match="revised the archive"):
            ia.write_acquisition_manifest(
                _derivation_complete_manifest(granule_checksums={name: "revisedsha"}),
                path,
            )
        retained = ia.read_acquisition_manifest(path)
        assert retained is not None
        assert retained.granule_checksums[name] == "originalsha"

    def test_an_identical_complete_rewrite_is_allowed(self, tmp_path: Path) -> None:
        path = ia.acquisition_manifest_path(tmp_path)
        name = ia.ImergGranuleId(start=ia.FIRST_GRANULE_START).filename(revision="V07B")
        for _ in range(2):
            ia.write_acquisition_manifest(
                _derivation_complete_manifest(granule_checksums={name: "samesha"}),
                path,
            )
        retained = ia.read_acquisition_manifest(path)
        assert retained is not None
        assert retained.granule_checksums == {name: "samesha"}


# --- D5 — the filename's period END is the field that maps an interval ---


class TestPeriodEndIsChecked:
    def test_parse_rejects_a_mislabeled_period_end(self) -> None:
        with pytest.raises(ia.ImergReadContractError, match="period end"):
            ia.parse_granule_filename(
                "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E075959.0420.V07B.HDF5"
            )

    def test_resolve_ignores_a_listing_entry_with_a_mislabeled_end(self) -> None:
        granule = ia.ImergGranuleId(start=datetime(2026, 8, 28, 7, 0, tzinfo=UTC))
        listing = "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E075959.0420.V07B.HDF5\n"
        with pytest.raises(ia.ImergGranuleMissingError):
            ia.resolve_granule_filename(listing, granule)


# --- the CLI itself (exit codes, and the OSError-unsafe paths) ---


class TestMain:
    def _client_for(self, granule: ia.ImergGranuleId, fixture: Path) -> object:
        filename = granule.filename(revision="V07B")
        _write_fake_granule(fixture, file_header_filename=filename)
        return FakeImergHttpClient(
            listings={granule.directory_url(): filename},
            granule_bytes={f"{granule.directory_url()}{filename}": fixture},
        )

    def test_a_successful_probe_exits_zero_and_records_the_acquisition(
        self, tmp_path: Path
    ) -> None:
        start = datetime(2026, 8, 28, 7, 0, tzinfo=UTC)
        client = self._client_for(ia.ImergGranuleId(start=start), tmp_path / "f.h5")
        data_root = tmp_path / "data_root"
        code = ia.main(
            [
                "--data-root",
                str(data_root),
                "--granule-start",
                start.isoformat(),
            ],
            client=client,
            clock=_clock,
            sleep=_sleep,
        )
        assert code == 0
        assert ia.acquisition_manifest_path(data_root).exists()

    def test_a_missing_granule_exits_3(self, tmp_path: Path) -> None:
        start = datetime(2026, 8, 28, 7, 0, tzinfo=UTC)
        code = ia.main(
            [
                "--data-root",
                str(tmp_path / "data_root"),
                "--granule-start",
                start.isoformat(),
            ],
            client=FakeImergHttpClient(),  # empty listing -> 404-equivalent
            clock=_clock,
            sleep=_sleep,
        )
        assert code == 3  # noqa: PLR2004

    def test_a_credentials_failure_exits_2_not_3(self, tmp_path: Path) -> None:
        """⛔ `ImergCredentialsError`'s docstring promises exit code 2 and
        `ImergStorageError`'s promises 5; `main()` used to return 3 for
        every `ImergAcquisitionError`, so both promises were fiction. Ops
        tooling can script against an exit code; it cannot script against a
        log field."""

        class RejectingClient:
            def list_directory(self, url: str) -> str:
                raise ia.ImergCredentialsError(f"Earthdata rejected {url}")

            def download_to_path(self, *, url: str, target: Path) -> None:
                raise AssertionError("never reached")

        code = ia.main(
            [
                "--data-root",
                str(tmp_path / "data_root"),
                "--granule-start",
                "2026-08-28T07:00:00+00:00",
            ],
            client=RejectingClient(),
            clock=_clock,
            sleep=_sleep,
        )
        assert code == 2  # noqa: PLR2004

    def test_an_unwritable_data_root_is_reported_not_raised(
        self, tmp_path: Path
    ) -> None:
        """A storage failure anywhere under the probe — here the acquisition
        manifest's own directory blocked by a FILE — must surface as a typed,
        exit-coded CLI failure, never a raw unhandled traceback."""
        start = datetime(2026, 8, 28, 7, 0, tzinfo=UTC)
        client = self._client_for(ia.ImergGranuleId(start=start), tmp_path / "f.h5")
        data_root = tmp_path / "data_root"
        ia.imerg_raw_dir(data_root).mkdir(parents=True)
        ia.acquisition_manifest_path(data_root).mkdir()  # a DIRECTORY, not a file
        code = ia.main(
            [
                "--data-root",
                str(data_root),
                "--granule-start",
                start.isoformat(),
            ],
            client=client,
            clock=_clock,
            sleep=_sleep,
        )
        assert code == 5  # noqa: PLR2004
