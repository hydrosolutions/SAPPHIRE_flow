"""Plan 171 task 1b — storage layout, D5 identities and the atomic manifest
writer."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from scripts.dhm_precip.era5_errors import Era5StorageError
from scripts.dhm_precip.era5_manifest import (
    AccumulationDiagnosticRecord,
    Era5ProvenanceManifest,
    OperatorProvenance,
    PackingAccounting,
    RawWindowRecord,
    TransformYearRecord,
    checksum_file,
    load_operator_provenance,
    passing_accumulation_diagnostic,
    publish_atomic,
    raw_request_identity,
    raw_window_is_current,
    read_manifest,
    transform_identity,
    transform_year_is_current,
    with_accumulation_diagnostic,
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
        "conservation_tolerance_m": 1e-5,
        "units_factor": 1000.0,
        "output_schema_version": "1",
        "transform_version": "1",
        "output_format": "netcdf4_h5netcdf",
        "output_dtype": "float32",
        "output_encoding": {
            "precipitation": {
                "zlib": True,
                "complevel": 4,
                "chunksizes": (24, 51, 91),
                "_FillValue": float("nan"),
            },
            "valid_time": {
                "units": "hours since 1970-01-01 00:00:00",
                "dtype": "int64",
            },
        },
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
        kwargs_b["output_encoding"] = {
            "valid_time": {
                "dtype": "int64",
                "units": "hours since 1970-01-01 00:00:00",
            },
            "precipitation": {
                "_FillValue": float("nan"),
                "chunksizes": (24, 51, 91),
                "complevel": 4,
                "zlib": True,
            },
        }
        assert transform_identity(**kwargs_a) == transform_identity(**kwargs_b)

    def test_changes_when_raw_sha256s_change(self) -> None:
        kwargs = _base_kwargs()
        id_a = transform_identity(**kwargs)
        kwargs["raw_sha256s"] = ["b" * 64]
        id_b = transform_identity(**kwargs)
        assert id_a != id_b

    def test_changes_when_conservation_tolerance_changes(self) -> None:
        kwargs = _base_kwargs()
        id_a = transform_identity(**kwargs)
        kwargs["conservation_tolerance_m"] = 1e-4
        id_b = transform_identity(**kwargs)
        assert id_a != id_b

    def test_changes_when_chunksizes_change(self) -> None:
        """A review finding: the identity previously carried only
        zlib/complevel, so a changed chunking strategy could silently
        resume-serve a stale product with different on-disk chunking."""
        kwargs = _base_kwargs()
        id_a = transform_identity(**kwargs)
        encoding_b = {
            k: dict(v) if isinstance(v, dict) else v
            for k, v in kwargs["output_encoding"].items()  # type: ignore[union-attr]
        }
        encoding_b["precipitation"]["chunksizes"] = (48, 51, 91)
        kwargs["output_encoding"] = encoding_b
        id_b = transform_identity(**kwargs)
        assert id_a != id_b

    def test_changes_when_fill_value_changes(self) -> None:
        kwargs = _base_kwargs()
        id_a = transform_identity(**kwargs)
        encoding_b = {
            k: dict(v) if isinstance(v, dict) else v
            for k, v in kwargs["output_encoding"].items()  # type: ignore[union-attr]
        }
        encoding_b["precipitation"]["_FillValue"] = -9999.0
        kwargs["output_encoding"] = encoding_b
        id_b = transform_identity(**kwargs)
        assert id_a != id_b

    def test_changes_when_time_encoding_units_change(self) -> None:
        kwargs = _base_kwargs()
        id_a = transform_identity(**kwargs)
        encoding_b = {
            k: dict(v) if isinstance(v, dict) else v
            for k, v in kwargs["output_encoding"].items()  # type: ignore[union-attr]
        }
        encoding_b["valid_time"]["units"] = "hours since 2000-01-01 00:00:00"
        kwargs["output_encoding"] = encoding_b
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


class TestAccumulationDiagnosticRecord:
    """Plan 174 (M-A5) task 1c."""

    def _manifest(self) -> Era5ProvenanceManifest:
        return Era5ProvenanceManifest(
            dataset="reanalysis-era5-land",
            client_package_version="0.7.7",
            operator_provenance=_PROVENANCE,
        )

    def _record(self, **overrides: object) -> AccumulationDiagnosticRecord:
        base: dict[str, object] = {
            "window_id": "2021-10",
            "source_sha256": "a" * 64,
            "reset_hour": 1,
            "terminal_hour": 0,
            "monotone_within_day": True,
            "sample_size_days": 31,
            "recorded_at": datetime(2026, 8, 16, tzinfo=UTC),
        }
        base.update(overrides)
        return AccumulationDiagnosticRecord(**base)  # type: ignore[arg-type]

    def test_round_trips_through_the_manifest(self, tmp_path: Path) -> None:
        manifest = with_accumulation_diagnostic(self._manifest(), self._record())
        path = tmp_path / "manifest.json"
        write_manifest_atomic(manifest, path)
        read_back = read_manifest(path)
        assert read_back == manifest
        assert read_back.accumulation_diagnostics["2021-10"] == self._record()

    def test_manifest_written_before_this_change_still_loads(
        self, tmp_path: Path
    ) -> None:
        # A manifest JSON with NO "accumulation_diagnostics" key at all (the
        # exact shape of a pre-1c manifest on disk) must load, defaulting to
        # {} rather than raising.
        import json

        path = tmp_path / "manifest.json"
        write_manifest_atomic(self._manifest(), path)
        raw = json.loads(path.read_text())
        del raw["accumulation_diagnostics"]
        path.write_text(json.dumps(raw))

        read_back = read_manifest(path)
        assert read_back is not None
        assert read_back.accumulation_diagnostics == {}
        assert passing_accumulation_diagnostic(read_back, expected_reset_hour=1) is None

    def test_passing_diagnostic_is_found(self) -> None:
        manifest = with_accumulation_diagnostic(self._manifest(), self._record())
        found = passing_accumulation_diagnostic(manifest, expected_reset_hour=1)
        assert found is not None
        assert found.window_id == "2021-10"

    def test_non_monotone_diagnostic_is_not_passing(self) -> None:
        manifest = with_accumulation_diagnostic(
            self._manifest(), self._record(monotone_within_day=False)
        )
        assert passing_accumulation_diagnostic(manifest, expected_reset_hour=1) is None

    def test_wrong_reset_hour_diagnostic_is_not_passing(self) -> None:
        manifest = with_accumulation_diagnostic(
            self._manifest(), self._record(reset_hour=3, terminal_hour=2)
        )
        assert passing_accumulation_diagnostic(manifest, expected_reset_hour=1) is None

    def test_no_diagnostic_at_all_is_absent_not_raising(self) -> None:
        manifest = self._manifest()
        assert passing_accumulation_diagnostic(manifest, expected_reset_hour=1) is None


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

    def test_blank_field_raises_typed_error(self, tmp_path: Path) -> None:
        """A review finding: a 'complete' provenance file (every KEY
        present) whose value is blank/whitespace previously round-tripped
        silently — only pydantic's key-presence check ran."""
        path = tmp_path / "provenance.json"
        path.write_text(
            '{"cds_portal_url": "https://cds.example", '
            '"dataset_landing_page_url": "https://cds.example/d", '
            '"licence_name": "   ", "licence_version": "1.0", '
            '"licence_accepted_at": "2026-08-13T00:00:00Z"}'
        )
        with pytest.raises(Era5StorageError, match="incomplete"):
            load_operator_provenance(path)

    def test_non_url_portal_raises_typed_error(self, tmp_path: Path) -> None:
        path = tmp_path / "provenance.json"
        path.write_text(
            '{"cds_portal_url": "not-a-url", '
            '"dataset_landing_page_url": "https://cds.example/d", '
            '"licence_name": "Licence", "licence_version": "1.0", '
            '"licence_accepted_at": "2026-08-13T00:00:00Z"}'
        )
        with pytest.raises(Era5StorageError, match="incomplete"):
            load_operator_provenance(path)


class TestOperatorProvenanceValidation:
    """`OperatorProvenance.__post_init__` — the domain-type invariants
    (CLAUDE.md 'parse, don't validate'), checked directly against the
    frozen dataclass rather than only through the JSON boundary above."""

    def _kwargs(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "cds_portal_url": "https://cds.climate.copernicus.eu",
            "dataset_landing_page_url": (
                "https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land"
            ),
            "licence_name": "Licence to use Copernicus Products",
            "licence_version": "1.0",
            "licence_accepted_at": datetime(2026, 8, 13, tzinfo=UTC),
        }
        base.update(overrides)
        return base

    def test_valid_provenance_constructs(self) -> None:
        OperatorProvenance(**self._kwargs())  # type: ignore[arg-type]

    def test_blank_licence_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="licence_name"):
            OperatorProvenance(**self._kwargs(licence_name="   "))  # type: ignore[arg-type]

    def test_blank_licence_version_rejected(self) -> None:
        with pytest.raises(ValueError, match="licence_version"):
            OperatorProvenance(**self._kwargs(licence_version=""))  # type: ignore[arg-type]

    def test_non_url_dataset_landing_page_rejected(self) -> None:
        with pytest.raises(ValueError, match="dataset_landing_page_url"):
            OperatorProvenance(
                **self._kwargs(dataset_landing_page_url="ftp://example.com")  # type: ignore[arg-type]
            )

    def test_naive_licence_accepted_at_rejected(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            OperatorProvenance(
                **self._kwargs(licence_accepted_at=datetime(2026, 8, 13))  # type: ignore[arg-type]
            )


class TestStorageErrorWrapping:
    """A review finding: manifest reads/writes, and the checksum/replace
    primitives, only caught pydantic `ValueError` — a real filesystem
    failure (permission denied, disk full) escaped as a bare `OSError`
    traceback (exit 1 from the CLI) rather than the documented
    `Era5StorageError` (exit 5)."""

    def test_checksum_file_read_failure_wrapped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "raw.nc"
        path.write_bytes(b"data")

        from pathlib import Path as _Path

        def _boom_open(self: object, *_a: object, **_k: object) -> object:
            raise OSError("simulated permission denied")

        monkeypatch.setattr(_Path, "open", _boom_open)
        with pytest.raises(Era5StorageError, match="checksum"):
            checksum_file(path)

    def test_publish_atomic_replace_failure_wrapped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import scripts.dhm_precip.era5_manifest as era5_manifest_module

        tmp = tmp_path / "raw.nc.tmp"
        tmp.write_bytes(b"data")
        final = tmp_path / "raw.nc"

        def _boom_replace(*_a: object, **_k: object) -> None:
            raise OSError("simulated disk full")

        monkeypatch.setattr(era5_manifest_module.os, "replace", _boom_replace)
        with pytest.raises(Era5StorageError, match="publish"):
            publish_atomic(tmp, final)

    def test_write_manifest_atomic_failure_wrapped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pathlib import Path as _Path

        def _boom_write_text(self: object, *_a: object, **_k: object) -> None:
            raise OSError("simulated disk full")

        monkeypatch.setattr(_Path, "write_text", _boom_write_text)
        manifest = Era5ProvenanceManifest(
            dataset="reanalysis-era5-land",
            client_package_version="0.7.7",
            operator_provenance=_PROVENANCE,
        )
        with pytest.raises(Era5StorageError, match="write manifest"):
            write_manifest_atomic(manifest, tmp_path / "manifest.json")

    def test_read_manifest_read_failure_wrapped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "manifest.json"
        path.write_text("{}")

        from pathlib import Path as _Path

        def _boom_read_text(self: object, *_a: object, **_k: object) -> str:
            raise OSError("simulated permission denied")

        monkeypatch.setattr(_Path, "read_text", _boom_read_text)
        with pytest.raises(Era5StorageError, match="read manifest"):
            read_manifest(path)

    def test_load_operator_provenance_read_failure_wrapped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "provenance.json"
        path.write_text("{}")

        from pathlib import Path as _Path

        def _boom_read_text(self: object, *_a: object, **_k: object) -> str:
            raise OSError("simulated permission denied")

        monkeypatch.setattr(_Path, "read_text", _boom_read_text)
        with pytest.raises(Era5StorageError, match="failed to read"):
            load_operator_provenance(path)
