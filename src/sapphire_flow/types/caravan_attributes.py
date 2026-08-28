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

    ``source_dataset_version`` defaults to ``"initial@delivered-2026-08-13"``
    (Plan 188 T2, owner-requested rename from the original
    ``"unconfirmed@delivered-2026-08-13"`` -- "unconfirmed" read as a defect
    awaiting correction; this is simply the first delivery). This value is
    NEVER persisted -- ``CaravanImportProvenance`` is returned to the
    caller, never written to a table -- so the rename changes no stored
    record; a caller with the modeller-confirmed release string still
    passes it explicitly via ``source_dataset_version=`` (threaded through
    `run_operational_caravan_import`).

    ``content_fingerprint`` (Plan 155 fixer round, major finding: "provenance
    is ephemeral ... no fingerprint and no immutability guard") is a stable
    hash of the parsed parquet rows (`store/caravan_import.py::_fingerprint_
    rows`) -- a durable IDENTITY for the exact content that was imported, so
    two imports can be compared for "identical replay" vs. "genuinely
    different source data" even before any persisted-to-a-table lineage
    record exists. Persisting this fingerprint (and the rest of this
    dataclass) into a dedicated source-version record, plus invalidating
    trained artifacts on a genuine source revision, remains deferred (needs
    new schema -- see the plan's T1 deviation note); `merge_namespaced_
    attributes` (`store/basin_store.py`) enforces the "identical replay
    only" half of that requirement independently, at the per-key level, by
    rejecting a changed value under an already-merged key.
    """

    source_dataset_name: str = "caravan"
    source_dataset_version: str = "initial@delivered-2026-08-13"
    source_dataset_purpose: str = "attributes"
    extractor_name: str = "hsol"
    extractor_version: str | None = None
    content_fingerprint: str | None = None


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
