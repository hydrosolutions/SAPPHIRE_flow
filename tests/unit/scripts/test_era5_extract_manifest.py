"""Plan 174 (M-A5) task 4a — D7's `extraction_identity`, bundle publication,
and the manifest, exercised directly (cheaper than the full CLI).

M-11 (review finding): the identity test must cover EVERY declared
value-affecting input, not a subset — each parameter below is varied in
turn and must change the resulting identity.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from scripts.dhm_precip.era5_extract_manifest import (
    ExtractionManifest,
    checksum_file,
    current_pointer_path,
    extraction_identity,
    prepare_staging_dir,
    publish_bundle,
    published_dir,
    read_extraction_manifest,
    write_extraction_manifest,
)

if TYPE_CHECKING:
    from pathlib import Path

_BASE_KWARGS: dict[str, object] = {
    "operator_id": "NEAREST",
    "coordinate_table_sha256": "a" * 64,
    "source_sha256s": ("b" * 64, "c" * 64),
    "orography_identity": "d" * 64,
    "jjas_months": (6, 7, 8, 9),
    "djf_months": (12, 1, 2),
    "mam_months": (3, 4, 5),
    "on_months": (10, 11),
    "wet_threshold_mm_per_h": 0.2,
    "wet_threshold_side": ">=",
    "zero_policy": "exclude_zero",
    "quantile_definition": "linear",
    "quantile_grid": (0.5, 0.9, 0.99),
    "station_elevation_datum": "UNKNOWN",
    "orography_elevation_datum": "LOCAL_MSL",
    "output_schema_version": "1",
    "output_format": "netcdf4_h5netcdf",
    "output_dtype": "float32",
    "output_encoding": {
        "valid_time": {"units": "hours since 1970-01-01 00:00:00", "dtype": "int64"}
    },
}


class TestExtractionIdentityCoversEveryDeclaredInput:
    """M-11 — each field D7 declares as part of the identity, varied alone,
    must change the digest. A broken implementation that forgets to hash
    one of these fields would pass a narrower test; this one would not."""

    def test_base_case_is_reproducible(self) -> None:
        assert extraction_identity(**_BASE_KWARGS) == extraction_identity(
            **_BASE_KWARGS
        )

    @pytest.mark.parametrize(
        ("field", "override"),
        [
            ("operator_id", "BILINEAR"),
            ("coordinate_table_sha256", "z" * 64),
            ("source_sha256s", ("z" * 64,)),
            ("orography_identity", "z" * 64),
            ("jjas_months", (7, 8, 9)),
            ("djf_months", (1, 2)),
            ("mam_months", (4, 5)),
            ("on_months", (11,)),
            ("wet_threshold_mm_per_h", 0.5),
            ("wet_threshold_side", ">"),
            ("zero_policy", "include_zero"),
            ("quantile_definition", "nearest"),
            ("quantile_grid", (0.5,)),
            ("station_elevation_datum", "EGM2008"),
            ("orography_elevation_datum", "EGM96"),
            ("output_schema_version", "2"),
            ("output_format", "netcdf4_scipy"),
            ("output_dtype", "float64"),
            ("output_encoding", {"valid_time": {"units": "x", "dtype": "int32"}}),
            ("extraction_code_version", "2"),
        ],
    )
    def test_changing_the_field_alone_changes_the_identity(
        self, field: str, override: object
    ) -> None:
        base = extraction_identity(**_BASE_KWARGS)
        changed = extraction_identity(**{**_BASE_KWARGS, field: override})
        assert base != changed, f"{field} is not covered by extraction_identity"


class TestPublishBundleMechanics:
    def _staged_bundle(
        self, data_root: Path, *, identity: str, payload_text: str = "x"
    ) -> Path:
        staging = prepare_staging_dir(data_root, identity=identity)
        payload_path = staging / "series_nearest.nc"
        payload_path.write_text(payload_text)
        write_extraction_manifest(
            ExtractionManifest(
                orography_identity="o",
                extraction_identity=identity,
                operator_id="NEAREST",
                coordinate_table_sha256="a" * 64,
                source_sha256s=("b" * 64,),
                payload_sha256s={"series_nearest.nc": checksum_file(payload_path)},
                orography_spec={},
                orography_source_record={},
                accumulation_diagnostic={},
                generated_at=datetime(2026, 8, 16, tzinfo=UTC),
            ),
            staging / "extraction_manifest.json",
        )
        return staging

    def test_publish_sets_current_pointer(self, tmp_path: Path) -> None:
        staged = self._staged_bundle(tmp_path, identity="abc123")
        publish_bundle(
            staged,
            data_root=tmp_path,
            identity="abc123",
            clock_now=datetime(2026, 8, 16, tzinfo=UTC),
        )
        assert current_pointer_path(tmp_path).read_text().strip() == "abc123"
        assert published_dir(tmp_path, identity="abc123").exists()

    def test_republish_of_a_reconciling_identity_adopts_it(
        self, tmp_path: Path
    ) -> None:
        staged1 = self._staged_bundle(tmp_path, identity="same-id")
        publish_bundle(
            staged1,
            data_root=tmp_path,
            identity="same-id",
            clock_now=datetime(2026, 8, 16, tzinfo=UTC),
        )
        staged2 = self._staged_bundle(tmp_path, identity="same-id")
        publish_bundle(
            staged2,
            data_root=tmp_path,
            identity="same-id",
            clock_now=datetime(2026, 8, 16, tzinfo=UTC),
        )
        # Adopted, not quarantined: no ".orphan-" sibling directory exists.
        siblings = [p.name for p in (tmp_path / "era5_land" / "points").iterdir()]
        assert not any("orphan" in name for name in siblings)

    def test_republish_of_a_non_reconciling_identity_quarantines_it(
        self, tmp_path: Path
    ) -> None:
        staged1 = self._staged_bundle(tmp_path, identity="same-id")
        publish_bundle(
            staged1,
            data_root=tmp_path,
            identity="same-id",
            clock_now=datetime(2026, 8, 16, tzinfo=UTC),
        )
        # Corrupt the published payload so its manifest no longer reconciles.
        (published_dir(tmp_path, identity="same-id") / "series_nearest.nc").write_text(
            "corrupted"
        )

        staged2 = self._staged_bundle(
            tmp_path, identity="same-id", payload_text="fresh"
        )
        publish_bundle(
            staged2,
            data_root=tmp_path,
            identity="same-id",
            clock_now=datetime(2026, 8, 16, tzinfo=UTC),
        )
        siblings = [p.name for p in (tmp_path / "era5_land" / "points").iterdir()]
        assert any("orphan" in name for name in siblings)
        # The freshly-published one reconciles again.
        republished = published_dir(tmp_path, identity="same-id")
        assert (republished / "series_nearest.nc").read_text() == "fresh"

    def test_manifest_never_hashes_itself_or_the_pointer(self, tmp_path: Path) -> None:
        staged = self._staged_bundle(tmp_path, identity="abc123")
        manifest = read_extraction_manifest(staged / "extraction_manifest.json")
        assert manifest is not None
        assert "extraction_manifest.json" not in manifest.payload_sha256s
        assert "CURRENT" not in manifest.payload_sha256s
