# Basin/static package importer runbook

Nepal v1 operational procedures for importing an accepted basin/static
package (Plan 120). Scope: package layout, running the importer, reading the
acceptance report, and the correction/new-`package_id` procedure. Populates
the `basins`/`basin_versions`/`basin_static_packages` tables and 082's §5a
`recap_gateway_polygon_bindings` `basin_average` rows — until a package is
imported, 082's store-backed Gateway-polygon resolver returns `None` for
every station (see Plan 082 / Plan 120 "Production-gate note").

## Package layout

An accepted package is a directory containing, at minimum
(`docs/requirements/04-basin-static-artifact-contract.md` §2/§9):

```text
manifest.json              # package identity, network, checksums, extractor info
basins.gpkg                 # one 2-D MultiPolygon feature per basin, EPSG:4326
static_attributes.parquet   # one row per gauge_id, Float64 attribute columns
feature_catalog.json        # per-attribute metadata (source, aggregation, window)
validation_report.json      # per-basin validation checks + warnings/errors
bands.gpkg                  # OPTIONAL — elevation-band geometries
README.md                   # OPTIONAL — producer notes
checksums.sha256            # OPTIONAL — sidecar hash file (must agree with manifest.checksums if both present)
```

## `basin_static_packages` provenance

Every successful (or already-imported) run writes/matches exactly one
`basin_static_packages` row keyed on the manifest's `package_id` — the
producer-declared identifier, `network`, `contract_version`, the extractor
name/version, `source_datasets`, `climatology_window`, and the importer's
own computed payload checksums (retained even though the package's files
themselves are discarded after import, contract §11). This is the row every
`basins`/`basin_versions`/§5a row for this import FK-references — "which
package produced this basin's current state" is answered by joining back to
this table (see "Reading the acceptance report" below).

The extraction tool that produces this package is **adjacent** — SAP3 does
not call it (contract §12, `docs/plans/117-basin-static-artifact-
architecture.md`). Obtain the package from the extractor operator (or DHM's
regeneration path, contract §12) and place it anywhere readable — the
importer takes an explicit `--package-dir`.

## Running the importer

```bash
DATABASE_URL=postgresql+psycopg://... \
  uv run python -m sapphire_flow.cli.import_basin_package \
    --package-dir /path/to/nepal-dhm-basins
```

This is a **manual, onboarding-time** invocation for v1 — there is no
scheduled Prefect flow around it (Plan 120 Task 3A, deliberately out of
scope; every package is a deliberate operator action, not a recurring
ingest). The CLI:

1. Loads and whole-package-validates the package (contract §9 first list —
   unsupported `contract_version`, a missing mandatory file, a checksum
   mismatch, a non-EPSG:4326 geometry file, etc. all reject the WHOLE
   package before any write).
2. Joins `basins.gpkg` to `static_attributes.parquet` on `gauge_id` and
   evaluates each basin's per-basin accept / onboarding-hold decision
   (contract §9 second list). A null catalog-`required_by_models` static
   feature holds the basin only when its station has a real, ACTIVE model
   assignment (direct or via a station group) requiring that feature — the
   CLI resolves this against the live `stations`/`model_assignments`/
   `station_groups`/`group_model_assignments` tables
   (`services.basin_importer.build_assigned_model_features_resolver`); it
   never treats every basin as unassigned. An ACTIVE assignment naming a
   model that fails to discover (entry-point scan) aborts the run loudly
   (`ConfigurationError`) rather than silently under-counting requirements.
3. Runs the canonical write pipeline in ONE database transaction: package
   provenance (`basin_static_packages`) first, then each accepted basin's
   projection + `basin_versions` snapshot, then the §5a `basin_average`
   mapping rows last.
4. Prints a structured acceptance report and exits non-zero if the package
   (or an accepted decision, re-checked at the write boundary) was rejected.

The importer never synthesizes a missing attribute, edits geometry to pass
validation, or falls back to a different basin without a recorded operator
decision (contract 04:670-672) — an anticipated problem always shows up in
the report, never as a silent partial success.

## Reading the acceptance report

The report (`BasinPackageImportReport`,
`src/sapphire_flow/types/basin_package.py`) partitions every basin in the
package into exactly one of:

| Field | Meaning |
|---|---|
| `outcome` | `"imported"` (accepted basins written, or the package had zero accepted basins), `"already_imported"` (identical `package_id` + fingerprint re-run — a no-op), or `"rejected"` (a whole-package or write-boundary problem — nothing persisted). |
| `accepted` | Basins that passed every per-basin check and were (or, on a no-op, would be) persisted. Still carries visible `warnings` (e.g. a non-required static feature that happened to be null) even though the basin was accepted. |
| `onboarding_held` | Basins held in `onboarding` — never dropped or silently skipped — with `hold_reasons` (e.g. an unmatched station, a required static feature missing for an assigned model, geometry outside required coverage). Resolve the underlying data problem and re-run; a held basin is picked up automatically once its hold reason clears. |
| `imported_basins` | The Task 2A/2C persistence outcome per accepted basin — `"inserted"` (brand-new `(network, basin_code)`) or `"corrected"` (a new `package_id` over an existing one — see below). Each carries `material_change` and, for a correction, `affected_artifact_ids`. |
| `rejection_reason` | Set only when `outcome="rejected"` — the human-readable reason the whole package was refused. |

`onboarding_held` basins are **not** an error — they are the contract's
SHOULD-import-with-visible-warnings behavior (§9/§10) for a basin that isn't
ready yet. Log every held basin's `hold_reasons` before re-running; don't
treat a held basin as a failed run.

## Correction / new-`package_id` procedure

A package is **immutable once accepted** (contract §11). If basin geometry
or attributes change, the producer issues a **new package with a new
`package_id`** over the same `(network, basin_code)` — never a re-upload
under the old `package_id` (the importer rejects that as an immutability
violation, with a clear reason in the report).

1. Obtain the corrected package (same `network`/`basin_code`, new
   `package_id`).
2. Run the importer exactly as above, pointing `--package-dir` at the new
   package.
3. The importer:
   - Stamps the PRIOR current `basin_versions` row's `superseded_at`.
   - Appends a new current `basin_versions` row (`version + 1`) carrying the
     new package's geometry/attributes/§5a mapping snapshot.
   - Refreshes the `basins` projection row (including its `name`) in place.
   - Replaces the station's §5a `basin_average` binding atomically (an
     HRU/name rename never leaves two rows for one station).
   - Emits `affected_artifact_ids` — the EXACT model artifacts trained on
     the version this correction just superseded (via
     `model_artifact_basin_versions`), so the correction deterministically
     names what needs retraining.
4. **No automatic quarantine.** The station stays live on its current
   artifact through the correction — a billed operational service cannot
   default to an availability hit for the full retrain cycle. A correction
   only flags `material_change=True` and names the affected artifacts; a
   head hydrologist decides whether to retrain via the normal training flow
   and whether a genuinely material correction warrants manually
   quarantining the station in the meantime.
5. A basin present in the database but **absent** from the new package is
   left completely untouched — packages are incremental/regional (waves of
   stations), and absence never means "remove this basin."

## Idempotency

Re-running the identical package (same `package_id`, same computed
checksums) is always safe — the importer detects it via the stored
fingerprint and reports `outcome="already_imported"` without writing
anything.
