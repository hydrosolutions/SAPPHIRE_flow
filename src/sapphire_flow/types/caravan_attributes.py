"""Plan 155 T1 — value types for the Caravan attributes import result."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True, slots=True)
class CaravanImportProvenance:
    """Structured provenance for the additive Caravan-attributes merge
    (plan 155 "Provenance of the Swiss parquet"). Deliberately NOT a
    `PackageManifest` row (`types/basin_package.py`): this import never
    goes through the basin-PACKAGE pipeline (no geometry, no correction,
    no `material_change`) -- see `store/caravan_import.py`.

    ``source_dataset_version`` stays an honest placeholder
    ("unconfirmed@delivered-2026-08-13") until the modeller confirms the
    exact Caravan release; the plan requires asking BEFORE running a real
    import, since correcting it later means a full re-import (immutability
    reasoning would apply if this were ever persisted through the package
    pipeline).
    """

    source_dataset_name: str = "caravan"
    source_dataset_version: str = "unconfirmed@delivered-2026-08-13"
    source_dataset_purpose: str = "attributes"
    extractor_name: str = "hsol"
    extractor_version: str | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class CaravanImportResult:
    """``matched_codes`` records a successful IDENTITY join + additive
    merge only -- it says nothing about whether the merged row's values
    satisfy any particular model's declared statics (that is a downstream
    concern, checked non-null/finite per-declared-name by
    ``services/caravan_statics.py::verify_static_coverage``, since it
    depends on WHICH model's static namespace is being verified).
    ``missing_from_manifest`` is the T0a-manifest gap `matched_codes` alone
    cannot surface: a caller-supplied ``expected_codes`` manifest station
    that never appeared as a row in the parquet at all (Plan 155
    post-implementation-review finding — "cannot detect a manifest station
    missing from the file")."""

    matched_codes: frozenset[str]
    unmatched_codes: frozenset[str]
    stations_without_basin: frozenset[str]
    missing_from_manifest: frozenset[str]
    provenance: CaravanImportProvenance
