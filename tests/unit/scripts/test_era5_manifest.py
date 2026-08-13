"""Plan 171 task 1b — storage layout, D5 identities and the atomic manifest
writer."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from scripts.dhm_precip.era5_errors import Era5StorageError
from scripts.dhm_precip.era5_manifest import (
    Era5ProvenanceManifest,
    OperatorProvenance,
    PackingAccounting,
    RawWindowRecord,
    TransformYearRecord,
    checksum_file,
    load_operator_provenance,
    raw_request_identity,
    raw_window_is_current,
    read_manifest,
    transform_identity,
    transform_year_is_current,
    with_raw_window,
    with_transform_year,
    write_manifest_atomic,
)

if TYPE_CHECKING:
    from pathlib import Path

_PROVENANCE = OperatorProvenance(
    cds_portal_url="https://cds.climate.copernicus.eu",
    dataset_landing_page_url="https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land",
    licence_name="Licence to use Copernicus Products",
    licence_version="1.0",
    licence_accepted_at=datetime(2026, 8, 13, tzinfo=UTC),
)


def _base_kwargs() -> dict[str, object]:
    return {
        "raw_sha256s": ["a" * 64],
        "accumulation_rule_id": "era5_land_01_00_accumulation_day_v1",
        "packing_tolerance_mm": 1e-4,
        "units_factor": 1000.0,
        "output_schema_version": "1",
        "transform_version": "1",
        "output_format": "netcdf4_h5netcdf",
        "output_dtype": "float32",
        "output_encoding": {"zlib": True, "complevel": 4},
    }


class TestRawRequestIdentity:
    def test_changes_when_bounding_box_changes(self) -> None:
        payload_a = {"area": [31, 80, 26, 89], "year": "2021"}
        payload_b = {"area": [30, 80, 26, 89], "year": "2021"}
        assert raw_request_identity(
            "reanalysis-era5-land", payload_a
        ) != raw_request_identity("reanalysis-era5-land", payload_b)

    def test_changes_when_dataset_id_changes(self) -> None:
        payload = {"area": [31, 80, 26, 89], "year": "2021"}
        assert raw_request_identity(
            "reanalysis-era5-land", payload
        ) != raw_request_identity("reanalysis-era5", payload)

    def test_stable_under_key_ordering(self) -> None:
        payload_a = {"year": "2021", "area": [31, 80, 26, 89], "month": "10"}
        payload_b = {"month": "10", "area": [31, 80, 26, 89], "year": "2021"}
        assert raw_request_identity(
            "reanalysis-era5-land", payload_a
        ) == raw_request_identity("reanalysis-era5-land", payload_b)


class TestTransformIdentity:
    def test_changes_when_output_schema_version_bumps(self) -> None:
        kwargs = _base_kwargs()
        id_a = transform_identity(**kwargs)
        kwargs["output_schema_version"] = "2"
        id_b = transform_identity(**kwargs)
        assert id_a != id_b

    def test_changes_when_transform_version_bumps(self) -> None:
        kwargs = _base_kwargs()
        id_a = transform_identity(**kwargs)
        kwargs["transform_version"] = "2"
        id_b = transform_identity(**kwargs)
        assert id_a != id_b

    def test_stable_under_encoding_key_ordering(self) -> None:
        kwargs_a = _base_kwargs()
        kwargs_b = _base_kwargs()
        kwargs_b["output_encoding"] = {"complevel": 4, "zlib": True}
        assert transform_identity(**kwargs_a) == transform_identity(**kwargs_b)

    def test_changes_when_raw_sha256s_change(self) -> None:
        kwargs = _base_kwargs()
        id_a = transform_identity(**kwargs)
        kwargs["raw_sha256s"] = ["b" * 64]
        id_b = transform_identity(**kwargs)
        assert id_a != id_b


class TestAtomicManifestWriter:
    def _manifest(self) -> Era5ProvenanceManifest:
        return Era5ProvenanceManifest(
            dataset="reanalysis-era5-land",
            client_package_version="0.7.7",
            operator_provenance=_PROVENANCE,
        )

    def test_round_trips(self, tmp_path: Path) -> None:
        manifest = self._manifest()
        path = tmp_path / "manifest.json"
        write_manifest_atomic(manifest, path)
        read_back = read_manifest(path)
        assert read_back == manifest

    def test_missing_manifest_reads_as_none(self, tmp_path: Path) -> None:
        assert read_manifest(tmp_path / "nope.json") is None

    def test_crash_during_serialisation_leaves_previous_manifest_intact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "manifest.json"
        original = self._manifest()
        write_manifest_atomic(original, path)

        record = RawWindowRecord(
            window_id="2021",
            dataset="reanalysis-era5-land",
            request_payload={"year": "2021"},
            raw_request_identity="deadbeef",
            sha256="c" * 64,
            client_package_version="0.7.7",
            downloaded_at=datetime(2026, 8, 13, tzinfo=UTC),
        )
        updated = with_raw_window(original, record)

        import scripts.dhm_precip.era5_manifest as era5_manifest_module

        def _boom(self: object, *_a: object, **_k: object) -> None:
            raise RuntimeError("simulated crash mid-serialisation")

        monkeypatch.setattr(
            era5_manifest_module._Era5ProvenanceManifestModel,
            "model_dump_json",
            _boom,
        )
        with pytest.raises(RuntimeError, match="simulated crash"):
            write_manifest_atomic(updated, path)

        # The previous manifest is untouched, still readable and parseable.
        read_back = read_manifest(path)
        assert read_back == original
        assert read_back.raw_windows == {}


class TestResumeChecks:
    def test_raw_window_is_current_requires_identity_file_and_checksum_match(
        self, tmp_path: Path
    ) -> None:
        final_path = tmp_path / "raw.nc"
        final_path.write_bytes(b"payload-bytes")
        record = RawWindowRecord(
            window_id="2021",
            dataset="reanalysis-era5-land",
            request_payload={},
            raw_request_identity="identity-a",
            sha256=checksum_file(final_path),
            client_package_version="0.7.7",
            downloaded_at=datetime(2026, 8, 13, tzinfo=UTC),
        )
        manifest = with_raw_window(
            Era5ProvenanceManifest(
                dataset="reanalysis-era5-land",
                client_package_version="0.7.7",
                operator_provenance=_PROVENANCE,
            ),
            record,
        )

        assert raw_window_is_current(
            manifest,
            window_id="2021",
            expected_identity="identity-a",
            final_path=final_path,
        )
        assert not raw_window_is_current(
            manifest,
            window_id="2021",
            expected_identity="identity-b",
            final_path=final_path,
        )
        final_path.write_bytes(b"different-bytes")
        assert not raw_window_is_current(
            manifest,
            window_id="2021",
            expected_identity="identity-a",
            final_path=final_path,
        )

    def test_transform_year_is_current(self, tmp_path: Path) -> None:
        final_path = tmp_path / "product.nc"
        final_path.write_bytes(b"product-bytes")
        record = TransformYearRecord(
            product_year=2021,
            transform_identity="identity-a",
            sha256=checksum_file(final_path),
            accumulation_convention="era5_land_01_00_accumulation_day_v1",
            units_conversion="metres_to_mm_x1000",
            packing=PackingAccounting(
                packing_corrected_cells=0, max_correction_mm=0.0, mass_adjustment_mm=0.0
            ),
            non_finite_cell_count=0,
            dropped_boundary_stamp=None,
            transformed_at=datetime(2026, 8, 13, tzinfo=UTC),
        )
        manifest = with_transform_year(
            Era5ProvenanceManifest(
                dataset="reanalysis-era5-land",
                client_package_version="0.7.7",
                operator_provenance=_PROVENANCE,
            ),
            record,
        )
        assert transform_year_is_current(
            manifest, year=2021, expected_identity="identity-a", final_path=final_path
        )
        assert not transform_year_is_current(
            manifest, year=2021, expected_identity="identity-b", final_path=final_path
        )


class TestOperatorProvenanceFile:
    def test_missing_file_raises_typed_error(self, tmp_path: Path) -> None:
        with pytest.raises(Era5StorageError, match="not found"):
            load_operator_provenance(tmp_path / "missing.json")

    def test_incomplete_file_raises_typed_error(self, tmp_path: Path) -> None:
        path = tmp_path / "provenance.json"
        path.write_text('{"cds_portal_url": "https://cds.example"}')
        with pytest.raises(Era5StorageError, match="incomplete"):
            load_operator_provenance(path)

    def test_complete_file_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / "provenance.json"
        path.write_text(
            '{"cds_portal_url": "https://cds.example", '
            '"dataset_landing_page_url": "https://cds.example/d", '
            '"licence_name": "Licence", "licence_version": "1.0", '
            '"licence_accepted_at": "2026-08-13T00:00:00Z"}'
        )
        provenance = load_operator_provenance(path)
        assert provenance.licence_name == "Licence"
