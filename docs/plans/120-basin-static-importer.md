---
status: DRAFT
created: 2026-07-16
plan: 120
title: Basin/static package importer + §5a persistence + versioned basin state
scope: Import an accepted basin/static package (incremental/regional, versioned), persist basin geometry/attributes, the §5a Gateway polygon-reference mapping, package provenance, and a versioned basin-state history so model artifacts reference the basin version they trained on; Nepal v1.
depends_on:
  - 082-recap-gateway-operational-readiness
  - 115a-weather-source-identity-schema
---

# Plan 120 — Basin/static package importer + §5a persistence + versioned basin state

**Status**: DRAFT
**Phase**: v1
**Depends on**: Plan 082 (owns the §5a mapping-table BASE schema this plan populates
and extends additively — see Ownership); 115a (weather-source role model); and, at
production time only, an accepted basin/static package (Plan 117 contract).

---

## Why this exists

`docs/requirements/04-basin-static-artifact-contract.md` §5a (`:305-310`) states that
"the implementation plan for this artifact contract MUST add an equivalent persistence
target before Nepal production enablement." **Plan 117 is docs-only and builds no
importer** (`docs/plans/117-basin-static-artifact-architecture.md`) — it records the
boundary and the §5a/provenance gaps but leaves import + persistence to a separate
plan. This is that plan.

Plan 082 ships a thin *store-backed* `GatewayPolygonResolver` that **reads** the §5a
mapping table (082 Task 2D, `082:275-293`). Plan 120 owns the **write** side:
validating/importing an accepted package, dissolving it into `basins`, populating the
§5a mapping, recording provenance, and versioning basin state. Until 120 lands and a
package is imported, 082's resolver returns `None` for every station — so **082's
production run gates on 120**, though 082's build/tests do not (they use a fixture).

---

## Two owner decisions this rework encodes

**Decision A — packages are INCREMENTAL / REGIONAL and VERSIONED, not full-network
snapshots.** DHM stations arrive in waves (dummy Nepal-AOI polygons for tool dev → real
EAST Nepal → real WEST Nepal); each package brings a disjoint set of NEW stations (or
corrected versions of existing ones) over shared underlying static datasets. Therefore:

- Import semantics = **additive UPSERT keyed by `(network, basin_code)`** into the
  basin's current version. A basin present in the DB but **absent** from a new package
  is **NOT dropped, quarantined, or flagged** — it belongs to another wave/region.
  There is no full-snapshot "drop/flag absent basin" step anywhere in this plan.
- Station *removal* is a separate station-lifecycle concern (deactivation), **never
  inferred from package absence**.
- A re-extraction driven by a shared-dataset or geometry change produces **fresh
  versioned packages** (new `package_id`) per basin/region — handled by
  upsert-to-a-new-version (Decision B), not deletion.
- `04` is silent on package completeness. **Flag (do not edit `04` here):** 082/`04`
  should carry an "incremental/regional, versioned" clarification note so no downstream
  reader assumes a package is the full network snapshot.

**Decision B — basin state is VERSIONED by `package_id`, not overwritten and lost.** A
correction (new `package_id` for an existing `(network, code)`) creates a **new version
snapshot** of the basin's geometry + attributes + §5a mapping; the prior version is
**retained (superseded, not deleted)**, and model artifacts reference the basin version
they trained on — so `04` §11 bullet 4 ("which artifacts trained on the OLD data") is
answerable by construction. Concrete mechanism and its one honest trade-off are in
**Versioned basin state**, below.

---

## Ownership (schema split)

| Concern | Owner |
|---|---|
| §5a mapping table **base** schema (`station_id, basin_id, gateway_hru_name, name, spatial_type, band_id`) + the resolver that reads it | **082** Task 2D (`082:279-283`) |
| §5a **provenance columns** (`package_id`, `imported_at`), additive on 082's base table | **120** (Task 0A) |
| `basin_static_packages` provenance table; `basin_versions` history table; `basins.package_id`; `model_artifacts.basin_version_id` | **120** (Task 0A) |
| Populating all of the above from an accepted package | **120** (Phases 1–3) |

Each schema **object** has one owner: 082 owns the §5a base table + resolver; 120 owns
the additive provenance layer, the provenance table, and the basin-versioning schema.
Basin versioning (`basin_versions` + `model_artifacts.basin_version_id`) is a larger
change than 082's additive §5a columns, and it lives entirely in 120 — there is **no
co-ownership of one object**. 120 layers additive migrations onto 082's already-settled
base table (082 ships the store-backed resolver + §5a table, fixture-tested,
`082:275-293`); it does not redefine 082's six base columns. The 120↔082 relationship
is a **runtime production gate** (082's resolver returns non-`None` only after 120 has
imported a package), stated in prose, not a build-graph `blocks` edge.

**Keeping the §5a table single-owned across versioning.** The §5a mapping table holds
**current rows only** (082's `station_id + gateway_hru_name + name` uniqueness stays
intact, so 082's resolver reads it unchanged and always sees the current polygon set).
The *superseded* §5a mapping for a prior basin version is retained inside that version's
`basin_versions` snapshot (below), not by widening 082's key. This is why versioning
does **not** force 082's resolver to learn about supersession.

---

## Versioned basin state (Decision B — concrete mechanism)

**Chosen mechanism: an additive `basin_versions` history table, keyed to the stable
`basins.id`, plus `model_artifacts.basin_version_id`.** Not a version column on
`basins`.

Why a table, not a version column on `basins` (the `historical_forcing` pattern):
`historical_forcing` (`db/metadata.py:372-429`) versions rows in place — a `version`
column (`:380`), `clock_timestamp()` supersession default (`:402`), version-in-natural-
key (`:419-428`) — precisely because **nothing holds an FK to an individual
`historical_forcing` row.** `basins` is different: `stations.basin_id`
(`db/metadata.py:84`) and the §5a `basin_id` both FK to `basins.id`. Adding a
version column and a new `basins` row per version would either strand those FKs on a
stale version or force repointing them on every correction. So `basins.id` stays the
**stable logical identity** (inbound FKs untouched), and versions live in a child
table. The supersession *mechanism* (a `superseded_at` marker set on the prior version;
`clock_timestamp()` insertion ordering) **is** reused from `historical_forcing`.

Schema (all additive; Task 0A):

- **`basin_versions`** — append-only. `id` (PK), `basin_id` (FK → `basins.id`),
  `package_id` (FK → `basin_static_packages`), `version` (int), `geometry`,
  `attributes` (JSONB), `area_km2`, `band_geometries` (JSONB), `gateway_mapping`
  (JSONB — snapshot of this version's §5a rows), `superseded_at` (nullable),
  `created_at` (`clock_timestamp()` default, `historical_forcing:402` precedent).
  Natural key `uq(basin_id, version)`. Current version = the row with
  `superseded_at IS NULL`. Rows are never updated except to stamp `superseded_at`, and
  never deleted.
- **`basins`** keeps holding the **current** version's `geometry`/`attributes`/
  `area_km2`/`regional_basin`/`band_geometries` (existing readers and the Swiss v0
  CAMELS-CH basins are untouched — no data migration), plus an additive nullable
  **`basins.package_id`** FK naming the package that produced the current state.
- **`model_artifacts` → basin-version provenance** — stamped at artifact-creation time.
  `04` §11 bullet 4 becomes a query: artifacts trained on a now-`superseded_at IS NOT NULL`
  version.
  > **OWNER DECISION NEEDED — group-scoped artifacts (independent Codex review, 2026-07-16).**
  > A singular `model_artifacts.basin_version_id` FK works only for **station-scoped**
  > artifacts. But an artifact may be **`group_id`-scoped** (`db/metadata.py:461,467,495`),
  > and a station group holds many stations → many basins → many `basin_versions`, so one FK
  > cannot record what a group artifact trained on. Two options: **(a)** an artifact↔basin-version
  > **join table** (`model_artifact_id, basin_version_id`) capturing every basin a station- OR
  > group-scoped artifact used — full §11 fidelity; **(b)** scope §11 lineage to station-scoped
  > artifacts only, and for group artifacts fall back to the group's member stations at correction
  > time (coarser, documented gap). This couples with the stamp-site fork (where the stamp is
  > wired: `stores.py:393` has no basin-version arg; training passes only station/group scope,
  > `onboard_model.py:363`, `model_onboarding.py:1261`).

On a correction (Task 2C): append a new `basin_versions` row (new `package_id`,
`version+1`, `superseded_at NULL`); set the prior version's `superseded_at`; refresh the
`basins` current-state projection + its `package_id` + the current §5a rows to the new
package. The prior geometry/attributes/§5a mapping survive verbatim in the superseded
`basin_versions` snapshot.

> **Honest trade-off (stated, not hidden).** The `basins` *projection* row's
> geometry/attributes ARE updated in place on a correction. Nothing is lost, because the
> prior version's full snapshot is written to `basin_versions` **before** the projection
> is refreshed — so "prior version retained, superseded, not deleted" and "artifacts
> answerable by construction" both hold, which is Decision B's stated intent. The
> alternative — `basins` as a thin identity row with geometry/attributes living ONLY in
> `basin_versions` (zero in-place mutation) — costs a data migration of every existing
> Swiss basin and a read-path refactor of `PgBasinStore`, for no gain against §11. That
> alternative is left as a residual **OWNER DECISION** (Open questions) rather than
> silently adopted.

---

## Scope

### Phase 0 — Provenance + versioning schema

#### Task 0A — Provenance/versioning tables + additive columns (BLOCKER-gate)

**Scope in:**
1. `basin_static_packages` table: `package_id` (PK), `network`, `contract_version`,
   `checksums` (JSONB — filename→computed sha256, retained per `04:429-430`),
   `imported_at`, and only the manifest metadata `04` §11 needs to answer "which
   package produced this" (`extractor` name/version, `source_datasets`,
   `climatology_window`). No wider manifest mirror.
2. `basin_versions` history table (columns above).
3. Additive nullable `basins.package_id` FK → `basin_static_packages` (additive on
   `db/metadata.py:42-65`; no change to existing columns or `uq_basins_network_code`
   `:64`).
4. Additive nullable `model_artifacts.basin_version_id` FK → `basin_versions.id`
   (additive on `db/metadata.py:455-499`; no change to `ck_model_artifacts_scope_xor`).
5. Additive nullable `package_id` (FK) + `imported_at` on 082's §5a base table
   (`082:279-283`); no redefinition of its six base columns.

**Scope out:** No change to 082's base §5a columns; no per-attribute (sub-basin)
provenance table; no removal of any `basins` column.

**Verification** — structural introspection (not a substring scan): `basin_static_packages`
and `basin_versions` exist with the stated PKs/FKs/`uq(basin_id, version)`; `basins`
gains a nullable `package_id` FK while every pre-existing `basins` column and
`uq_basins_network_code` are unchanged; `model_artifacts` gains a nullable
`basin_version_id` FK with `ck_model_artifacts_scope_xor` intact; the §5a table gains
nullable `package_id`/`imported_at` with its six base columns intact.

```bash
uv run pytest tests/unit/db/test_basin_static_provenance_schema.py::TestProvenanceSchema
```

### Phase 1 — Package read + validation (§9 acceptance rules)

#### Task 1A — Package loader, checksums, feature-catalog + whole-package acceptance

**Scope in:** A Pydantic-boundary loader for the mandatory file set (`manifest.json`,
`basins.gpkg`, `static_attributes.parquet`, `feature_catalog.json`,
`validation_report.json` — `04:61-62`) plus the optional `bands.gpkg` (`04:53-56`).
Parse-don't-validate: raw external data → Pydantic model → frozen domain type.

- **Canonical checksums (BLOCKER).** The importer **always computes** a canonical
  SHA-256 over **every present package file**, whether or not the producer supplied
  `checksums.sha256` (`04:83`, SHOULD). The computed hashes are what land in
  `basin_static_packages.checksums`. If a producer `checksums.sha256` is present, each
  computed hash is **verified** against it and a mismatch rejects the package (`04:634`).
- **Whole-package reject rules (`04:628-639`):** unsupported `contract_version`; a
  missing mandatory file; a producer-checksum mismatch; empty/conflicting `network`;
  any geometry file not EPSG:4326; package-level ID duplication; `feature_catalog.json`
  omitting a Parquet attribute column.
- **Feature-catalog validation (`04` §7), made explicit:** each catalog `name` matches a
  Parquet column (`04:508`); every Parquet attribute column has a catalog entry
  (`04:638`); `source_dataset` references a `manifest.source_datasets` entry (`04:511`);
  a present `climatology_window` equals `manifest.climatology_window` (`04:514`);
  required per-feature fields (`type` ∈ {float,integer}, `unit` present) exist.
- **`bands.gpkg`: absent vs present-invalid are different (BLOCKER).** *Absent* optional
  `bands.gpkg` → fine; only basin-level rows are produced downstream, no station is
  stranded. *Present but invalid* (unreadable, wrong CRS, non-2-D, schema-nonconforming)
  → treated as an invalid geometry file, **NOT as absent**: it rejects the package (or
  holds the affected basins per §9/§10), never silently tolerated.

**Scope out:** No per-basin accept decisions (Task 1B); no writes.

**Verification** — discriminating negative fixtures: a well-formed package parses; each
whole-package reject rule raises its specific rejection; a Parquet-column-without-catalog
and a catalog-`source_dataset`-not-in-manifest and a `climatology_window` mismatch each
reject; a file mutated vs a present producer checksum rejects; an **absent** `bands.gpkg`
parses clean while a **present malformed** `bands.gpkg` rejects (distinct outcomes).

```bash
uv run pytest tests/unit/services/test_basin_package_loader.py::TestWholePackageAcceptance
```

#### Task 1B — `gauge_id` join + per-basin acceptance (§9 per-basin rules)

**Scope in:** Join `basins.gpkg` ↔ `static_attributes.parquet` on `gauge_id`, **failing
loudly** on any `gauge_id` present in one file but not the other — no partial import
(`04:378-393`). Then the per-basin accept / hold-in-`onboarding` / reject decisions
(`04:641-655`): geometry missing/empty/invalid/not-2-D-`MultiPolygon`; `area_km2`
non-positive; `station_code` unmatched to a SAP3 station; required static features for an
assigned model missing/null; Gateway feature `name` missing/duplicated/naming-rule-
violating; Gateway HRU name missing/undeclared-in-manifest; basin outside required
coverage. SHOULD-allow import with **visible** per-basin warnings when the basin is not
yet assigned to a model needing the missing feature (`04:653-655`).

**Scope out:** No writes; the material-change cascade (§11 steps 2–5) is Task 2C's note.

**Verification:** matched `gauge_id` sets → clean join; a `gauge_id` in only one file →
raises (no partial import); each per-basin rule → the right outcome (reject-package vs
hold-`onboarding` vs accept-with-warning), with the warning surfaced in the returned
acceptance report, not swallowed.

```bash
uv run pytest tests/unit/services/test_basin_package_loader.py::TestGaugeIdJoin tests/unit/services/test_basin_package_loader.py::TestPerBasinAcceptance
```

### Phase 2 — Persistence (write side)

#### Task 2A — Dissolve accepted package into `basins` + version snapshot + provenance

**Scope in:** For each **new** `(network, basin_code)` accepted basin, write per
`04:415-434`: `basins.geometry` (2-D `MultiPolygon`, EPSG:4326), `basins.attributes`
JSONB (every `Float64` column, `{name: value}`), `area_km2`/`regional_basin` scalars,
and stamp `basins.package_id`. Insert the initial `basin_versions` row (`version=1`,
`superseded_at NULL`, snapshotting geometry/attributes/`area_km2`/`band_geometries` +
its §5a `gateway_mapping`). Insert the `basin_static_packages` provenance row (computed
`checksums` retained even though package files are discarded, `04:429-430`). One DB
transaction per package (all-or-nothing at the package level; per-basin `onboarding`
holds from 1B are recorded, not silent skips).

- **Null attribute round-trip (major).** An unavailable static attribute is stored as a
  **JSON `null` inside the `basins.attributes` JSONB dict** — `{"foo": null}` — per
  `04:352-354`/`04:422`. It is **NOT** `attributes IS NULL` and **NOT** `0`/a sentinel.

**Scope out:** No §5a-table rows here (Task 2B); no correction/upsert of an existing
basin (Task 2C); no forcing/attribute back-extraction (package is self-contained,
`04:450-459`).

**Verification:** a seeded accepted package writes N `basins` + N `basin_versions`
(`version=1`) rows with populated `attributes`/`geometry`/`package_id` and one
`basin_static_packages` row whose `checksums` equal the importer-computed hashes; a null
attribute round-trips as `attributes->'foo' = JSON null` (present key, null value), not
`attributes IS NULL` and not `0`.

```bash
uv run pytest tests/integration/store/test_basin_importer_persistence.py::TestDissolveIntoBasins
```

#### Task 2B — §5a mapping population + band persistence + store JSONB fix

**Scope in:** Populate the §5a mapping table (`station_id, basin_id, gateway_hru_name,
name, spatial_type, band_id, package_id, imported_at`) from the accepted package.

- **Bands are persistence-only (BLOCKER resolution).** When `bands.gpkg` is present,
  120 persists it — write `basins.band_geometries` (`04:425`, column
  `db/metadata.py:56`) and emit **band-level** §5a rows (`spatial_type='elevation_band'`,
  populated `band_id`, matching the `elevation_band` value used at
  `station_weather_sources.extraction_type` `db/metadata.py:172-178` and
  `weather_forecasts.spatial_type`/`band_id` `db/metadata.py:319-324`) alongside the
  basin-level rows (`spatial_type='basin_average'`, `band_id=NULL`). These band rows are
  **stored for future use only.** 120 does **not** require 082's resolver to read them
  and does **not** undefer banding: Recap v1 is basin-average-only
  (`recap_gateway.py:327` prefilter, `:366` lock; `081:213` DECISION). When `bands.gpkg`
  is absent, only basin-level rows are written.
- **Store JSONB fix (major).** `PgBasinStore.store_basin` currently does
  `json.dumps(basin.band_geometries)` before the JSONB column (`basin_store.py:53-55`),
  which stores a JSON **string** scalar, not a JSON array, and `fetch` returns it raw
  (`:71`). Fix: pass the Python list/dict **directly** to the JSONB column (SQLAlchemy
  serializes it) for both `band_geometries` and `attributes`, so a non-null
  `band_geometries` round-trips as a JSON array.

**Scope out:** No Gateway-side HRU registration/upload (manual, 082 runbook Task 4A); no
forcing fetch (082 adapters); resolver behavior unchanged (082-owned).

**Verification:** a package with `bands.gpkg` → one basin-level §5a row
(`spatial_type='basin_average'`, `band_id IS NULL`) plus one band-level row per band
(`spatial_type='elevation_band'`, `band_id` set), all carrying the import's `package_id`;
a package without `bands.gpkg` → basin-level rows only; a non-null `band_geometries`
round-trips through `store_basin`→`fetch_basin` as a **list** (not a JSON string) —
this fails against the current `json.dumps` path; 082's store-backed resolver reads the
seeded **basin-average** row back and returns the expected `GatewayPolygonRef`
(basin-average-only cross-check; band rows are not resolved).

```bash
uv run pytest tests/integration/store/test_basin_importer_persistence.py::TestFiveAMappingPopulation tests/integration/store/test_basin_store_jsonb.py::TestBandGeometriesRoundTrip
```

#### Task 2C — Incremental upsert + versioned corrections + idempotency — BLOCKER

**Scope in:** The current basin write is insert-only `store_basin`
(`basin_store.py:43-59`) against `uq_basins_network_code` (`db/metadata.py:64`), so a
naive re-import raises `IntegrityError`. Define exact behavior grounded in `04:674-677`
(package immutable once accepted; corrections require a **new** `package_id`) and
Decision A/B:

- **Absent basin (Decision A).** A basin already in `basins` but **not present** in the
  incoming package is **left untouched** — not dropped, not quarantined, not flagged.
  Packages are incremental/regional; absence carries no signal. (Station deactivation is
  a separate lifecycle, out of scope.)
- **Same `package_id` already imported, computed checksums identical** → idempotent
  **no-op** (skip; return "already imported"). Detected via the `package_id` PK + the
  retained computed checksums.
- **Same `package_id`, computed checksums differ** (a file mutated under an unchanged
  id) → **reject** (immutability violation; `04:676` requires a new `package_id` for any
  content change). Do not overwrite.
- **New `package_id` for an existing `(network, code)`** → a **correction (Decision B)**:
  append a `basin_versions` row (`version+1`, new `package_id`, `superseded_at NULL`),
  set the prior version's `superseded_at`, refresh the `basins` projection +
  `basins.package_id` + the current §5a rows, add a `basin_static_packages` row, and set
  a **material-change flag** in the report. The insert-only store cannot do this today —
  this task adds an upsert/`update_basin_from_package` path keyed on `(network, code)`.
- **New `(network, code)`** → delegates to Task 2A insert (`version=1`).
- **Material-change cascade (`04:688-695` steps 2–5: re-extract forcing, recompute
  static attributes, retrain, recompute skill)** is **operator-triggered and OUT OF
  SCOPE** — the importer records the correction + provenance + material-change flag; it
  does not auto-retrain.

**Scope out:** No automated retrain/hindcast cascade; no station deactivation.

**Verification (must FAIL against the current insert-only path):** re-running the same
package (identical computed checksums) → single `basins` row, no `IntegrityError`,
"already imported"; same `package_id` with a mutated file (differing computed checksum) →
raises the immutability rejection; a new `package_id` over an existing `(network, code)`
→ `basins` projection updated (new geometry/attributes/`package_id`), a second
`basin_versions` row exists with the prior version's `superseded_at` set and its snapshot
intact, and the material-change flag is set; a basin already in the DB but absent from
the package → unchanged (no delete, no flag). The re-run and correction tests fail today
because `store_basin` (`basin_store.py:43`) only inserts.

```bash
uv run pytest tests/integration/store/test_basin_importer_idempotency.py::TestReimportAndCorrections
```

### Phase 3 — Import entrypoint + docs

#### Task 3A — Importer orchestration + acceptance report

**Scope in:** The top-level import function/CLI wiring that composes 1A→1B→2A/2C→2B in
one transaction per package, returning a structured **acceptance report** (accepted
basins, `onboarding`-held basins with reasons, package-level rejections, warnings,
material-change flags, and — where an artifact-creation site exists — the
`basin_version_id` stamped) so the §9 "warnings MUST remain visible in onboarding
reports" requirement (`04:653-655`) is met. The importer MUST NOT synthesize missing
attributes, edit geometry to pass validation, or fall back to another basin without a
recorded operator decision (`04:670-672`).

**Scope out:** No scheduling/Prefect flow (manual/onboarding-time invocation for v1); no
Gateway upload automation; no model-training changes.

**Verification:** an end-to-end fixture package produces a report with the exact
accepted/held/rejected partition and populated provenance; a package that would require
synthesizing a missing attribute is rejected/held, never silently completed.

```bash
uv run pytest tests/integration/services/test_basin_importer.py::TestImporterAcceptanceReport
```

#### Task 3B — Docs: contract provenance homes, schema docs, runbook

**Scope in:**
- Update `04-basin-static-artifact-contract.md` §5a (`:305-310`), §6.2a (`:442-448`),
  and §11 (`:679-686`) to point at the realized persistence targets
  (`basin_static_packages`, `basin_versions`, `basins.package_id`,
  `model_artifacts.basin_version_id`, §5a `package_id`/`imported_at`), replacing the "no
  first-class field yet / left to the implementing plan" language.
- Update `docs/spec/database-schema.md` (`:42`) and `docs/architecture-context.md`
  (`:2650`) — both still describe `basins` without the new provenance/version — to add
  `basin_static_packages`, `basin_versions`, `basins.package_id`, and
  `model_artifacts.basin_version_id`.
- Add an importer runbook (`docs/operations/basin-static-importer-runbook.md`) covering
  package placement, running the importer, reading the acceptance report, and the
  correction/new-`package_id` procedure.
- Correct `docs/plans/README.md:85-88` (see the plan-index note in the change log below).

**Scope out:** No changes to the extraction-tool brief (adjacent, `04:697-712`).

**Verification** — a lean doc test asserts: the runbook has the operator anchors (package
layout, `basin_static_packages`, acceptance report, correction procedure) as sections;
`database-schema.md` and `architecture-context.md` mention `basin_versions` +
`basins.package_id`; and `04` §5a/§6.2a/§11 no longer describe the persistence target as
an open gap. Anchor checks, not full-text assertions.

```bash
uv run pytest tests/unit/docs/test_basin_importer_docs.py::TestImporterDocs
```

## Not in scope

- The extraction tool itself (adjacent; `04:697-712` — SAP3 does not call it).
- The static feature schema / `feature_catalog.json` semantics (modeller-owned,
  `04:312-413`) — 120 validates the catalog against the Parquet/manifest (Task 1A) but
  does not define feature meanings.
- Gateway operational fetch / watchdog / coverage (Plan 082).
- Gateway-side HRU registration / gpkg upload (manual; 082 runbook Task 4A).
- The material-data-change cascade (`04:688-695`): operator-triggered, flagged by the
  importer, not automated here.
- Per-attribute (sub-basin-granularity) provenance — basin-version granularity satisfies
  §11 (`:679-686`).
- Station deactivation / removal (a station-lifecycle concern; not inferred from package
  absence, per Decision A).
- Banding in the resolver / operational fetch — bands are persisted (Task 2B) but Recap
  v1 stays basin-average-only (`081:213`).

## Open questions (residual owner decisions — resolve before READY)

- **`basins` mutation model (Decision B residual).** Chosen design keeps `basins` as a
  mutable current-state projection with an append-only `basin_versions` history. The
  strict-no-mutation alternative (`basins` as thin identity; geometry/attributes SoT in
  `basin_versions`) is heavier (Swiss-basin data migration + `PgBasinStore` read-path
  refactor) for no §11 gain. **Owner: confirm the projection design or accept the
  migration cost.**
- **`model_artifacts.basin_version_id` stamping site.** 120 adds the FK column and the
  acceptance-report plumbing; the actual stamp happens at artifact creation in the
  model-training/onboarding path. Is wiring that stamp in-scope for 120, or a thin
  follow-on owned by the training flow? (Column-only without a stamp leaves §11 bullet 4
  answerable-by-construction only once training populates it.)
- **Correction UX.** On a correction that supersedes basins with existing trained
  artifacts/forecasts, does the importer merely *flag* the material change (current
  design) or additionally *quarantine* the affected station until the operator runs the
  §11 cascade? Current design: flag only.
- **Coverage check source (`04:651`).** "Basin outside required coverage" reuses 082's
  coverage manifest (082 Task 3A/3B) vs a standalone check. Prefer reuse; 082 is already
  a `depends_on`.

## References

- `docs/requirements/04-basin-static-artifact-contract.md` (§2 `:39-62`, §5 `:283-289`,
  §5a `:291-310`, §6.2a `:415-448`, §7 `:499-548`, §9 `:628-655`, §10 `:657-672`,
  §11 `:674-695`)
- `docs/plans/117-basin-static-artifact-architecture.md` (docs-only contract alignment)
- `docs/plans/082-recap-gateway-operational-readiness.md` (Task 2D `:275-293` — base §5a
  table + resolver this plan populates/extends)
- `docs/plans/081-recap-dg-client-integration.md:213` (basin-average-only DECISION)
- `src/sapphire_flow/types/basin.py:11-22`; `src/sapphire_flow/db/metadata.py:42-65`
  (`basins`), `:172-178`/`:319-324` (`elevation_band` usage), `:372-429`
  (`historical_forcing` version+supersession precedent), `:455-499` (`model_artifacts`);
  `src/sapphire_flow/store/basin_store.py:43-59`/`:71` (insert-only, `json.dumps` JSONB
  bug); `src/sapphire_flow/adapters/recap_gateway.py:327`/`:366` (basin-average-only)
- `docs/spec/database-schema.md:42`, `docs/architecture-context.md:2650` (doc-update
  targets)

## Change log for adjacent docs (flag, do not edit here beyond noting)

- **`docs/plans/README.md:85-88`** currently says 120 "Owns … the §5a mapping table,"
  which contradicts the split above. Corrected (with this rework) to: 120 owns package
  import/validation, the §5a-row **population**, the provenance layer
  (`basin_static_packages` + additive `package_id`/`imported_at`), and **basin-state
  versioning** (`basin_versions` + `model_artifacts.basin_version_id`); 082 owns the §5a
  **base** table + resolver.
- **082 / `04`** should carry an "incremental/regional, versioned" package-completeness
  clarification (Decision A) — flagged for those docs' owners, not edited here.

## Dependency Graph

```json
{
  "phases": [
    {
      "id": "phase-0",
      "name": "Provenance + versioning schema",
      "tasks": ["0A"],
      "parallel": false,
      "depends_on": ["plan-082", "plan-115a"]
    },
    {
      "id": "phase-1",
      "name": "Package read + validation (§9 acceptance)",
      "tasks": ["1A", "1B"],
      "parallel": false,
      "depends_on": ["phase-0"]
    },
    {
      "id": "phase-2",
      "name": "Persistence (write side)",
      "tasks": ["2A", "2B", "2C"],
      "parallel": false,
      "depends_on": ["phase-1"]
    },
    {
      "id": "phase-3",
      "name": "Import entrypoint + docs",
      "tasks": ["3A", "3B"],
      "parallel": false,
      "depends_on": ["phase-2"]
    }
  ],
  "task_dependencies": {
    "1A": ["0A"],
    "1B": ["1A"],
    "2A": ["0A", "1B"],
    "2B": ["2A"],
    "2C": ["2A", "2B"],
    "3A": ["2C"],
    "3B": ["3A"]
  }
}
```

## Whole-Plan Exit Gates

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
uv run pytest
```

## Production-gate note (not a build edge)

082's store-backed resolver returns `None` for every station until Plan 120 has imported
an accepted package. That is a **runtime** gate on 082's *production* run, not a build
dependency of 082 on 120 (082 builds and fixture-tests without 120). Do not promote
either plan to production-enabled until 120 has landed **and** an accepted package has
been imported (`082:516` risk row already records this).
