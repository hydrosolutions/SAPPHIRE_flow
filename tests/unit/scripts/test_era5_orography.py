"""Plan 174 (M-A5) tasks 1a/1b — the frozen `OrographySpec` and the fetch /
convert / aggregate / validate pipeline, against synthetic sources only
(constraint 1: no real DEM/geopotential raster is ever fetched here)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import numpy as np
import pytest

from scripts.dhm_precip.domain_types import OrographySource, VerticalDatum
from scripts.dhm_precip.era5_errors import Era5OrographyError
from scripts.dhm_precip.era5_orography import (
    aggregate_to_grid,
    assert_grid_matches,
    convert_field,
    fetch_orography_source,
    materialise_orography,
    orography_raster_path,
    orography_route_identity,
    read_orography_source_record,
    verify_orography_materialisation,
)
from scripts.dhm_precip.era5_orography_spec import (
    AGGREGATION_RULE_ID,
    G0_M_PER_S2,
    OBSERVED_OROGRAPHY_SPEC,
    OrographyConversionRule,
    OrographySpec,
)

if TYPE_CHECKING:
    from pathlib import Path


def _clock() -> datetime:
    return datetime(2026, 8, 16, tzinfo=UTC)


_CLOCK = _clock


def _spec(**overrides: object) -> OrographySpec:
    base = dict(
        source=OrographySource.MODEL_OROGRAPHY,
        product_id="reanalysis-era5-land:geopotential",
        product_version="v1",
        download_url="https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land",
        licence_name="Licence to use Copernicus Products",
        licence_version="1.0",
        licence_url="https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land?tab=licence",
        source_crs="WGS84 (EPSG:4326)",
        vertical_reference=VerticalDatum.LOCAL_MSL,
        units="m**2 s**-2",
        no_data_sentinel="NaN",
        aggregation_rule_id=AGGREGATION_RULE_ID,
        conversion_rule=OrographyConversionRule.GEOPOTENTIAL_G0,
        probe_date=date(2026, 8, 16),
    )
    base.update(overrides)
    return OrographySpec(**base)  # type: ignore[arg-type]


# --- 1a: the frozen spec ---


class TestOrographySpec:
    def test_frozen_spec_instance_constructs(self) -> None:
        spec = _spec()
        assert spec.source is OrographySource.MODEL_OROGRAPHY
        assert spec.conversion_rule is OrographyConversionRule.GEOPOTENTIAL_G0

    def test_observed_spec_constructs(self) -> None:
        # The actual committed 1a deliverable.
        assert OBSERVED_OROGRAPHY_SPEC.source is OrographySource.MODEL_OROGRAPHY
        assert OBSERVED_OROGRAPHY_SPEC.download_url.startswith("https://")
        assert OBSERVED_OROGRAPHY_SPEC.rejected_candidates == ()

    def test_rejects_blank_product_id(self) -> None:
        with pytest.raises(ValueError, match="product_id"):
            _spec(product_id="  ")

    def test_rejects_non_https_download_url(self) -> None:
        with pytest.raises(ValueError, match="https"):
            _spec(download_url="http://example.com/data.tif")

    def test_rejects_non_https_licence_url(self) -> None:
        with pytest.raises(ValueError, match="https"):
            _spec(licence_url="ftp://example.com/licence")

    def test_unrecognised_vertical_datum_rejected_by_the_enum(self) -> None:
        with pytest.raises(ValueError):
            VerticalDatum("NOT_A_DATUM")

    def test_conversion_rule_other_than_the_two_declared_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            OrographyConversionRule("scale_by_1000")

    def test_orography_source_members(self) -> None:
        assert set(OrographySource) == {
            OrographySource.MODEL_OROGRAPHY,
            OrographySource.DEM_PROXY,
        }

    def test_vertical_datum_members(self) -> None:
        assert set(VerticalDatum) == {
            VerticalDatum.EGM96,
            VerticalDatum.EGM2008,
            VerticalDatum.WGS84_ELLIPSOID,
            VerticalDatum.LOCAL_MSL,
            VerticalDatum.UNKNOWN,
        }


# --- 1b: identity ---


class TestOrographyRouteIdentity:
    def test_identity_changes_when_any_field_changes(self) -> None:
        base = _spec()
        base_id = orography_route_identity(base)
        for changed in (
            replace(base, product_id="different"),
            replace(base, licence_version="2.0"),
            replace(base, conversion_rule=OrographyConversionRule.IDENTITY),
        ):
            assert orography_route_identity(changed) != base_id

    def test_identity_deterministic(self) -> None:
        spec = _spec()
        assert orography_route_identity(spec) == orography_route_identity(spec)


class TestAggregationRuleIdDoesNotClaimWeighting:
    """Fix #8 — the aggregation is an UNWEIGHTED mean of the source cells
    contained by each target cell. `cos(lat)` varies ~0.17 % inside one 0.1
    deg cell, negligible against intra-cell relief, so the plan's accepted
    resolution is to CORRECT THE CLAIM rather than implement weighting. The
    rule id is written into the raster's attrs and into the manifest, so it
    must not assert a method that was not used."""

    def test_rule_id_says_mean_of_contained_cells(self) -> None:
        assert AGGREGATION_RULE_ID == "era5_land_orography_mean_of_contained_cells_v1"
        assert "weighted" not in AGGREGATION_RULE_ID

    def test_observed_spec_carries_the_corrected_rule_id(self) -> None:
        assert OBSERVED_OROGRAPHY_SPEC.aggregation_rule_id == AGGREGATION_RULE_ID


# --- 1b: convert_field (D3a magnitude sanity check) ---


class TestConvertField:
    def test_geopotential_converts_to_metres_at_g0(self) -> None:
        spec = _spec(conversion_rule=OrographyConversionRule.GEOPOTENTIAL_G0)
        phi = np.array([19613.3, 29419.95])  # 2000 m, 3000 m
        out = convert_field(phi, spec=spec)
        assert out == pytest.approx([2000.0, 3000.0])

    def test_metres_declared_as_geopotential_raises(self) -> None:
        # A metres-valued field (tens..thousands) is wildly outside the
        # geopotential plausible range (~1e4-1e5) — must raise, not silently
        # divide by g0 again.
        spec = _spec(conversion_rule=OrographyConversionRule.GEOPOTENTIAL_G0)
        already_metres = np.array([500.0, 3200.0])
        with pytest.raises(Era5OrographyError, match="magnitude"):
            convert_field(already_metres, spec=spec)

    def test_identity_passes_through_metres(self) -> None:
        spec = _spec(conversion_rule=OrographyConversionRule.IDENTITY)
        metres = np.array([500.0, 3200.0])
        out = convert_field(metres, spec=spec)
        assert out is metres or np.array_equal(out, metres)

    def test_geopotential_valued_field_declared_identity_raises(self) -> None:
        spec = _spec(conversion_rule=OrographyConversionRule.IDENTITY)
        geopotential_valued = np.array([9806.65, 19613.3, 88000.0])
        with pytest.raises(Era5OrographyError, match="classif|magnitude"):
            convert_field(geopotential_valued, spec=spec)

    def test_real_nepal_box_geopotential_including_the_terai_converts(self) -> None:
        """Blocker B1 (corrected D3a) — the acquired box 26-31 N contains the
        Terai lowlands at ~60 m, i.e. ~588 m2 s-2. A band that bounded the
        field MINIMUM at 1e4 raised on EVERY real field. The discriminator is
        the MAXIMUM: Everest's ~86,779 m2 s-2 is unambiguous under either
        unit, the minimum carries no signal at all."""
        spec = _spec(conversion_rule=OrographyConversionRule.GEOPOTENTIAL_G0)
        phi = np.array([60.0, 1364.0, 3700.0, 8849.0]) * G0_M_PER_S2
        out = convert_field(phi, spec=spec)
        assert out == pytest.approx([60.0, 1364.0, 3700.0, 8849.0])

    def test_real_nepal_box_metres_field_including_the_terai_passes_identity(
        self,
    ) -> None:
        spec = _spec(conversion_rule=OrographyConversionRule.IDENTITY)
        metres = np.array([60.0, 1364.0, 3700.0, 8849.0])
        assert convert_field(metres, spec=spec) == pytest.approx(metres)

    def test_sea_level_zero_minimum_is_not_rejected(self) -> None:
        """A coastal/lowland cell at exactly 0 m must not trip the sanity
        bound — only the PHYSICALLY IMPOSSIBLE is rejected (corrected D3a)."""
        spec = _spec(conversion_rule=OrographyConversionRule.GEOPOTENTIAL_G0)
        phi = np.array([0.0, 8849.0 * G0_M_PER_S2])
        assert convert_field(phi, spec=spec)[0] == pytest.approx(0.0)

    def test_unmasked_no_data_sentinel_in_a_geopotential_field_raises(self) -> None:
        """The one thing the lower bound still exists for: an unmasked
        -32768 sentinel that survived into the field."""
        spec = _spec(conversion_rule=OrographyConversionRule.GEOPOTENTIAL_G0)
        with pytest.raises(Era5OrographyError, match="physically impossible"):
            convert_field(
                np.array([-32768.0, 8849.0 * G0_M_PER_S2]),
                spec=spec,
            )

    def test_unmasked_no_data_sentinel_in_a_metres_field_raises(self) -> None:
        spec = _spec(conversion_rule=OrographyConversionRule.IDENTITY)
        with pytest.raises(Era5OrographyError, match="physically impossible"):
            convert_field(np.array([-32768.0, 3700.0]), spec=spec)


# --- 1b: aggregate_to_grid (D3a, red-first: hand-computed) ---


class TestAggregateToGrid:
    def _linear_ramp_source(
        self, *, n_per_target: int = 10
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """A 3x3 target grid at 0.1 deg, each cell subdivided into
        `n_per_target` x `n_per_target` source cells at 0.01 deg — a linear
        ramp is exactly hand-computable: the area-weighted mean of a
        symmetric subgrid equals the value at the target cell centre."""
        target_lat = np.array([26.0, 26.1, 26.2])
        target_lon = np.array([85.0, 85.1, 85.2])
        spacing = 0.1
        sub = spacing / n_per_target
        src_lat = np.concatenate(
            [
                t - spacing / 2 + sub / 2 + np.arange(n_per_target) * sub
                for t in target_lat
            ]
        )
        src_lon = np.concatenate(
            [
                t - spacing / 2 + sub / 2 + np.arange(n_per_target) * sub
                for t in target_lon
            ]
        )
        lat_grid, lon_grid = np.meshgrid(src_lat, src_lon, indexing="ij")
        values = 2.0 * lat_grid + 3.0 * lon_grid + 100.0
        return values, src_lat, src_lon, target_lat, target_lon, spacing

    def test_hand_computed_linear_ramp(self) -> None:
        values, src_lat, src_lon, target_lat, target_lon, spacing = (
            self._linear_ramp_source()
        )
        result = aggregate_to_grid(
            values,
            source_lat=src_lat,
            source_lon=src_lon,
            target_lat=target_lat,
            target_lon=target_lon,
            target_spacing_deg=spacing,
        )
        expected = 2.0 * target_lat[:, None] + 3.0 * target_lon[None, :] + 100.0
        np.testing.assert_allclose(result.values, expected, atol=1e-8)
        assert not result.flagged.any()
        assert (result.no_data_fraction == 0.0).all()

    def test_two_percent_no_data_aggregates_over_remainder_and_flags(self) -> None:
        values, src_lat, src_lon, target_lat, target_lon, spacing = (
            self._linear_ramp_source(n_per_target=100)
        )
        # NaN out 2 of the 10,000 cells belonging to target cell (0, 0) —
        # 0.02% < 5%, well inside "2%" but nonzero.
        expected_full = 2.0 * target_lat[0] + 3.0 * target_lon[0] + 100.0
        flat = values[:100, :100]
        flat[0, 0] = np.nan
        flat[0, 1] = np.nan
        result = aggregate_to_grid(
            values,
            source_lat=src_lat,
            source_lon=src_lon,
            target_lat=target_lat,
            target_lon=target_lon,
            target_spacing_deg=spacing,
        )
        assert result.flagged[0, 0]
        assert not np.isnan(result.values[0, 0])
        assert result.values[0, 0] == pytest.approx(expected_full, abs=0.5)

    def test_ten_percent_no_data_is_nan_and_flagged(self) -> None:
        n = 10
        values, src_lat, src_lon, target_lat, target_lon, spacing = (
            self._linear_ramp_source(n_per_target=n)
        )
        # NaN out 10 of the 100 source cells feeding target cell (0, 0).
        block = values[:n, :n]
        block[0, :] = np.nan
        result = aggregate_to_grid(
            values,
            source_lat=src_lat,
            source_lon=src_lon,
            target_lat=target_lat,
            target_lon=target_lon,
            target_spacing_deg=spacing,
        )
        assert result.flagged[0, 0]
        assert np.isnan(result.values[0, 0])
        # Untouched cells are unaffected.
        assert not result.flagged[1, 1]

    def test_zero_valid_source_cells_is_nan(self) -> None:
        n = 4
        values, src_lat, src_lon, target_lat, target_lon, spacing = (
            self._linear_ramp_source(n_per_target=n)
        )
        values[:n, :n] = np.nan
        result = aggregate_to_grid(
            values,
            source_lat=src_lat,
            source_lon=src_lon,
            target_lat=target_lat,
            target_lon=target_lon,
            target_spacing_deg=spacing,
        )
        assert np.isnan(result.values[0, 0])
        assert result.flagged[0, 0]


# --- 1b: assert_grid_matches ---


class TestAssertGridMatches:
    def test_matching_grids_pass(self) -> None:
        lat = np.array([26.0, 26.1, 26.2])
        lon = np.array([85.0, 85.1])
        assert_grid_matches(lat, lon, expected_lat=lat.copy(), expected_lon=lon.copy())

    def test_last_element_offset_by_1e6_deg_raises(self) -> None:
        lat = np.array([26.0, 26.1, 26.2])
        lon = np.array([85.0, 85.1])
        shifted_lat = lat.copy()
        shifted_lat[-1] += 1e-6
        with pytest.raises(Era5OrographyError, match="latitude"):
            assert_grid_matches(
                shifted_lat, lon, expected_lat=lat, expected_lon=lon.copy()
            )


# --- 1b: fetch / re-fetch verification ---


class _FakeDownloader:
    def __init__(self, *, payloads: dict[str, bytes]) -> None:
        self._payloads = payloads

    def download(self, *, spec: OrographySpec, dest_dir: Path) -> tuple[Path, ...]:
        paths = []
        for name, payload in self._payloads.items():
            path = dest_dir / name
            path.write_bytes(payload)
            paths.append(path)
        return tuple(paths)


class TestFetchOrographySource:
    def test_first_fetch_succeeds_and_writes_one_record(self, tmp_path: Path) -> None:
        spec = _spec()
        downloader = _FakeDownloader(payloads={"raw.nc": b"hello"})
        record = fetch_orography_source(
            spec, downloader=downloader, data_root=tmp_path, clock=_CLOCK
        )
        assert len(record.downloaded_files) == 1
        assert (
            read_orography_source_record(
                tmp_path / "era5_land" / "orography" / "orography_source_record.json"
            )
            == record
        )

    def test_rerun_with_matching_hash_succeeds(self, tmp_path: Path) -> None:
        spec = _spec()
        downloader = _FakeDownloader(payloads={"raw.nc": b"hello"})
        first = fetch_orography_source(
            spec, downloader=downloader, data_root=tmp_path, clock=_CLOCK
        )
        second = fetch_orography_source(
            spec, downloader=downloader, data_root=tmp_path, clock=_CLOCK
        )
        assert first.downloaded_files == second.downloaded_files

    def test_rerun_with_disagreeing_hash_raises_before_reading_as_a_raster(
        self, tmp_path: Path
    ) -> None:
        spec = _spec()
        fetch_orography_source(
            spec,
            downloader=_FakeDownloader(payloads={"raw.nc": b"hello"}),
            data_root=tmp_path,
            clock=_CLOCK,
        )
        with pytest.raises(Era5OrographyError, match="disagrees"):
            fetch_orography_source(
                spec,
                downloader=_FakeDownloader(payloads={"raw.nc": b"different bytes"}),
                data_root=tmp_path,
                clock=_CLOCK,
            )


# --- 1b: end-to-end materialise (happy path reopens + revalidates) ---


_TARGET_LAT = np.array([26.0, 26.1, 26.2])
_TARGET_LON = np.array([85.0, 85.1, 85.2])
_SPACING = 0.1


def _synthetic_source(*, elevation_offset_m: float = 2000.0):
    n = 5
    sub = _SPACING / n
    src_lat = np.concatenate(
        [t - _SPACING / 2 + sub / 2 + np.arange(n) * sub for t in _TARGET_LAT]
    )
    src_lon = np.concatenate(
        [t - _SPACING / 2 + sub / 2 + np.arange(n) * sub for t in _TARGET_LON]
    )
    lat_grid, lon_grid = np.meshgrid(src_lat, src_lon, indexing="ij")
    phi = G0_M_PER_S2 * (2.0 * lat_grid + 3.0 * lon_grid + elevation_offset_m)
    return phi, src_lat, src_lon


def _materialise(
    tmp_path: Path,
    *,
    payload: bytes = b"placeholder",
    elevation_offset_m: float = 2000.0,
):
    phi, src_lat, src_lon = _synthetic_source(elevation_offset_m=elevation_offset_m)

    def raw_reader(_path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        return phi, src_lat, src_lon

    return materialise_orography(
        _spec(conversion_rule=OrographyConversionRule.GEOPOTENTIAL_G0),
        downloader=_FakeDownloader(payloads={"raw.nc": payload}),
        raw_reader=raw_reader,
        data_root=tmp_path,
        expected_lat=_TARGET_LAT,
        expected_lon=_TARGET_LON,
        target_spacing_deg=_SPACING,
        clock=_CLOCK,
    )


class TestMaterialiseOrography:
    def test_happy_path_reopens_and_revalidates(self, tmp_path: Path) -> None:
        record = _materialise(tmp_path)
        assert record.raster_sha256
        assert record.orography_route_identity == orography_route_identity(
            _spec(conversion_rule=OrographyConversionRule.GEOPOTENTIAL_G0)
        )
        assert record.orography_identity != record.orography_route_identity


class TestOrographyIdentityCoversTheMaterialisedBytes:
    """Blocker B2 (corrected D7) — `orography_identity` must cover the ROUTE
    *plus* every downloaded file's sha256, the derived raster's own sha256
    and the frozen raster schema. A route-only identity means a source file
    whose bytes change at the same URL keeps the same identity and the stale
    raster is silently reused."""

    def test_changed_source_bytes_change_the_identity(self, tmp_path: Path) -> None:
        first = _materialise(tmp_path / "a", payload=b"source-bytes-v1")
        second = _materialise(tmp_path / "b", payload=b"source-bytes-v2")
        # Same route, same derived raster — only the SOURCE BYTES differ.
        assert first.orography_route_identity == second.orography_route_identity
        assert first.raster_sha256 == second.raster_sha256
        assert first.orography_identity != second.orography_identity

    def test_changed_derived_raster_changes_the_identity(self, tmp_path: Path) -> None:
        first = _materialise(tmp_path / "a", elevation_offset_m=2000.0)
        second = _materialise(tmp_path / "b", elevation_offset_m=2500.0)
        assert first.orography_route_identity == second.orography_route_identity
        assert first.raster_sha256 != second.raster_sha256
        assert first.orography_identity != second.orography_identity

    def test_identical_inputs_reproduce_the_identity(self, tmp_path: Path) -> None:
        first = _materialise(tmp_path / "a")
        second = _materialise(tmp_path / "b")
        assert first.orography_identity == second.orography_identity


class TestVerifyOrographyMaterialisation:
    """D7 — "a materialised raster is never trusted because a file of the
    right name exists". Every run re-verifies the source hashes and the
    raster's own sha256; a mismatch is a TYPED failure, never silent reuse."""

    def test_untouched_materialisation_verifies(self, tmp_path: Path) -> None:
        record = _materialise(tmp_path)
        verify_orography_materialisation(record, data_root=tmp_path)

    def test_tampered_raster_raises(self, tmp_path: Path) -> None:
        record = _materialise(tmp_path)
        raster = orography_raster_path(
            tmp_path, route_identity=record.orography_route_identity
        )
        raster.write_bytes(raster.read_bytes() + b"tamper")
        with pytest.raises(Era5OrographyError, match="raster"):
            verify_orography_materialisation(record, data_root=tmp_path)

    def test_tampered_source_file_raises(self, tmp_path: Path) -> None:
        record = _materialise(tmp_path)
        raw = tmp_path / record.downloaded_files[0].path
        raw.write_bytes(b"tampered source bytes")
        with pytest.raises(Era5OrographyError, match="source file"):
            verify_orography_materialisation(record, data_root=tmp_path)

    def test_record_whose_identity_does_not_recompute_raises(
        self, tmp_path: Path
    ) -> None:
        record = _materialise(tmp_path)
        forged = replace(record, orography_identity="0" * 64)
        with pytest.raises(Era5OrographyError, match="identity"):
            verify_orography_materialisation(forged, data_root=tmp_path)


class TestPlanSelfRecord:
    """Task 5a — the plan doc's "## Observed orography route" section is
    present and matches the frozen spec, not left claiming the probe never
    happened."""

    def test_plan_records_the_observed_route_matching_the_frozen_spec(self) -> None:
        from pathlib import Path

        plan_path = (
            Path(__file__).resolve().parents[3]
            / "docs"
            / "plans"
            / "174-era5-land-point-extraction.md"
        )
        text = plan_path.read_text()
        assert "## Observed orography route" in text
        assert OBSERVED_OROGRAPHY_SPEC.product_id in text
        assert str(OBSERVED_OROGRAPHY_SPEC.source) in text
        assert str(OBSERVED_OROGRAPHY_SPEC.vertical_reference) in text
