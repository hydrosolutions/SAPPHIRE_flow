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
mapping table (082 Task 2D, `082:297`). Plan 120 owns the **write** side:
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
**retained (superseded, not deleted)**, and every model artifact records **the exact
basin version(s) it trained on** via a lineage join table — so `04` §11 bullet 4 ("which
artifacts trained on the OLD data") is answerable by construction, and a correction can
deterministically name the artifacts to retrain. Concrete mechanism, the group-artifact
lineage design, and the correction→retrain payoff are in **Versioned basin state**,
below.

---

## Multi-tenant / professional-service posture

This importer is built for **professional service provision to multiple hydromets** over
the coming years, so auditability and cross-tenant automated retraining are first-class,
not afterthoughts. Three design choices carry that intent: **network-scoped import**
(`04:181` — `manifest.network` scopes every basin, so tenants never collide, Decision A);
**complete per-basin version lineage** (`basin_versions` retains every superseded
snapshot, and the artifact↔version join table records exactly which basin versions each
artifact trained on); and **correction→retrain automation** (Task 2C emits the affected-
artifact set for Flow 9). Together these mean a single tenant's basin correction is fully
auditable and deterministically identifies the exact models to retrain — the
differentiators for billing this as a service rather than a one-off deployment.

---

## Ownership (schema split)

| Concern | Owner |
|---|---|
| §5a mapping table **base** schema (`station_id, basin_id, gateway_hru_name, name, spatial_type, band_id`) + the resolver that reads it (`db/metadata.py:198-238`) | **082** Task 2D (`082:297`) |
| §5a **provenance columns** (`package_id`, `imported_at`), additive on 082's base table + extending `GatewayPolygonBindingRow`/`store_binding` to write them | **120** (Task 0A schema, Task 2B writer) |
| `basin_static_packages` provenance table; `basin_versions` history table (+ one-current-per-basin partial index + legacy backfill); `basins.package_id`; `model_artifact_basin_versions` lineage join table | **120** (Task 0A) |
| Additive `store_artifact` `trained_station_ids` arg + train-time write of the lineage join rows | **120** (Task 2D — reaches into the training artifact-creation path) |
| Populating all of the above from an accepted package | **120** (Phases 1–3) |

Each schema **object** has one owner: 082 owns the §5a base table + resolver; 120 owns
the additive provenance layer, the provenance table, and the basin-versioning schema.
Basin versioning (`basin_versions` + `model_artifact_basin_versions`) is a larger change
than 082's additive §5a columns, and it lives entirely in 120 — there is **no
co-ownership of one object**. 120 layers additive migrations onto 082's already-settled
base table (082 ships the store-backed resolver + §5a table, fixture-tested,
`082:297`); it does not redefine 082's six base columns. The 120↔082 relationship
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
`basins.id`, plus a `model_artifact_basin_versions` lineage join table.** Not a version
column on `basins`, and not a singular FK on `model_artifacts`.

Why a table, not a version column on `basins` (the `historical_forcing` pattern):
`historical_forcing` (`db/metadata.py:418-471`) versions rows in place — a `version`
column (`:425`), `clock_timestamp()` `created_at` default (`:447`), version-in-natural-
key (`:467`) — precisely because **nothing holds an FK to an individual
`historical_forcing` row.** `basins` is different: `stations.basin_id`
(`db/metadata.py:84`) and the §5a `basin_id` both FK to `basins.id`. Adding a
version column and a new `basins` row per version would either strand those FKs on a
stale version or force repointing them on every correction. So `basins.id` stays the
**stable logical identity** (inbound FKs untouched), and versions live in a child
table. Only the `clock_timestamp()` deterministic-ordering + versioned-natural-key
precedent is reused from `historical_forcing`. The `superseded_at` marker is **new to
`basin_versions`** — `historical_forcing` has no such column (it identifies the current
row as the max `version`, `:467`); `basin_versions` adds `superseded_at IS NULL` as the
explicit current-version marker (backed by a partial unique index, below).

Schema (all additive; Task 0A):

- **`basin_versions`** — append-only. `id` (PK), `basin_id` (FK → `basins.id`),
  `package_id` (FK → `basin_static_packages`, **nullable** — legacy rows have no
  package, see Legacy backfill below), `version` (int), `geometry`, `attributes`
  (JSONB), `area_km2`, `band_geometries` (JSONB), `gateway_mapping` (JSONB — snapshot of
  this version's §5a rows), `superseded_at` (nullable), `created_at`
  (`clock_timestamp()` default, `historical_forcing:447` precedent). Natural key
  `uq(basin_id, version)`. **Plus a partial unique index
  `uq_basin_versions_one_current_per_basin` on `(basin_id) WHERE superseded_at IS NULL`
  (major review finding)** — `uq(basin_id, version)` alone would permit two
  `superseded_at IS NULL` rows for one basin, making "the current basin version"
  ambiguous. The partial index makes exactly-one-current an invariant the DB enforces;
  the correction transaction (below) therefore MUST stamp the prior current row's
  `superseded_at` **before** inserting the new current row. Current version = the row
  with `superseded_at IS NULL`. Rows are never updated except to stamp `superseded_at`,
  and never deleted.
- **`basins`** keeps holding the **current** version's `geometry`/`attributes`/
  `area_km2`/`regional_basin`/`band_geometries` (existing readers, e.g. `PgBasinStore`,
  are untouched — the projection columns are unchanged), plus an additive nullable
  **`basins.package_id`** FK naming the package that produced the current state.
- **Legacy backfill (blocker review finding).** Existing `basins` rows (Swiss v0
  CAMELS-CH; `db/metadata.py:42-65`) predate this schema — they have **no**
  `basin_versions` row and no `package_id`. Because Task 2D's lineage write resolves a
  station's basin to its **current `basin_versions` row**, a training run against a
  legacy basin would find no version and either fail or silently write no lineage.
  Task 0A therefore **backfills one `basin_versions` row per existing `basins` row**
  (`version=1`, `superseded_at IS NULL`, `package_id=NULL` — the legacy/pre-120
  sentinel, projecting the basin's current `geometry`/`attributes`/`area_km2`/
  `band_geometries` and an empty-or-current `gateway_mapping`). `basins.package_id`
  stays `NULL` for these. This is a one-time data migration (contra the earlier "no
  data migration" claim, which was wrong for the lineage path). Regression: a
  Swiss-style pre-120 basin can still train and write a lineage row pointing at its
  legacy `version=1` `basin_versions` row (Task 0A verification).
- **`model_artifact_basin_versions`** (lineage join table, SETTLED — replaces a singular
  FK). Columns `model_artifact_id` (FK → `model_artifacts.id`), `basin_version_id`
  (FK → `basin_versions.id`); PK the pair. A **join table, not a singular
  `model_artifacts.basin_version_id` FK**, because ML models train per **station GROUP**
  (`GroupForecastModel`; `model_artifacts.group_id` `db/metadata.py:512`,
  `models.artifact_scope='group'` `db/metadata.py:486-488`), and a group artifact spans many
  stations → many basins → many `basin_versions`. A singular FK could record only a
  station-scoped artifact's single basin; the join table records full §11 lineage for
  **both** station- and group-scoped artifacts. `04` §11 bullet 4 becomes a join query:
  `SELECT DISTINCT model_artifact_id FROM model_artifact_basin_versions JOIN
  basin_versions … WHERE basin_versions.superseded_at IS NOT NULL` — the artifacts
  trained on now-old data. `model_artifacts` itself gains **no** new column.

On a correction (Task 2C), in this order inside the one-package transaction (the order
is load-bearing for the partial unique index above): **(1)** stamp the prior current
version's `superseded_at`; **(2)** append the new `basin_versions` row (new `package_id`,
`version+1`, `superseded_at NULL`) — now the only current row; **(3)** refresh the
`basins` current-state projection + its `package_id`; **(4)** upsert-REPLACE the current
§5a rows to the new package (delete-then-insert per station, see Task 2B/2C). Doing (2)
before (1) would momentarily leave two `superseded_at IS NULL` rows and violate
`uq_basin_versions_one_current_per_basin`. The prior geometry/attributes/§5a mapping
survive verbatim in the superseded `basin_versions` snapshot (its `gateway_mapping`
JSONB).

> **Trade-off — SETTLED (owner, 2026-07-16): keep projection-with-history.** The `basins`
> *projection* row's geometry/attributes ARE updated in place on a correction, but the
> prior version's full snapshot is written to `basin_versions` **within the same
> one-package transaction, before** the projection is refreshed — so the version history
> is **audit-complete either way**, and "prior version retained, superseded, not deleted"
> plus "artifacts answerable by construction" both hold. The strict-no-mutation
> alternative (`basins` as a thin identity row, geometry/attributes SoT only in
> `basin_versions`) forces a `PgBasinStore` read-path refactor (every basin read becomes
> a join to the current `basin_versions` row) for **no audit benefit**, so it is
> rejected. Note: the legacy backfill (one `basin_versions` row per existing basin,
> above) is required by **both** designs — Task 2D's lineage write needs a current
> version row for legacy basins regardless — but under projection-with-history it is a
> lightweight one-row-per-basin insert, not a read-path rewrite. Fork resolved.

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
2. `basin_versions` history table (columns above), including `package_id` **nullable**
   (legacy rows carry NULL) and the partial unique index
   `uq_basin_versions_one_current_per_basin` on `(basin_id) WHERE superseded_at IS NULL`
   (major finding — otherwise two current versions per basin are representable).
3. Additive nullable `basins.package_id` FK → `basin_static_packages` (additive on
   `db/metadata.py:42-65`; no change to existing columns or `uq_basins_network_code`
   `:64`).
4. `model_artifact_basin_versions` lineage join table: `model_artifact_id` (FK →
   `model_artifacts.id`), `basin_version_id` (FK → `basin_versions.id`), PK the pair.
   New table only — **no** column added to `model_artifacts` (`db/metadata.py:507-559`
   unchanged, `ck_model_artifacts_scope_xor` intact).
5. Additive nullable `package_id` (FK) + `imported_at` on 082's §5a base table
   (`db/metadata.py:198-238`); no redefinition of its six base columns.
6. **Legacy backfill (blocker finding).** A one-time data migration inserting one
   `basin_versions` row (`version=1`, `superseded_at IS NULL`, `package_id=NULL`) for
   **every** pre-existing `basins` row, projecting that basin's current geometry/
   attributes/`area_km2`/`band_geometries` (and its current §5a rows, if any, into
   `gateway_mapping`). Without this, a legacy (Swiss/CAMELS-CH) basin has no current
   `basin_versions` row, so Task 2D's lineage write finds nothing to point at and
   training breaks or writes no lineage.

**Scope out:** No change to 082's base §5a columns; no change to `model_artifacts`
columns; no per-attribute (sub-basin) provenance table; no removal of any `basins`
column.

**Verification** — structural introspection (not a substring scan): `basin_static_packages`,
`basin_versions`, and `model_artifact_basin_versions` exist with the stated
PKs/FKs/`uq(basin_id, version)` **and the partial unique index on `(basin_id) WHERE
superseded_at IS NULL`** (join-table PK = the `(model_artifact_id, basin_version_id)`
pair); `basins` gains a nullable `package_id` FK while every pre-existing `basins` column
and `uq_basins_network_code` are unchanged; `model_artifacts` is structurally unchanged
(`ck_model_artifacts_scope_xor` intact); the §5a table gains nullable
`package_id`/`imported_at` with its six base columns intact. **Legacy-backfill
regression:** seed a pre-120-style `basins` row with no `basin_versions`/`package_id`,
run the migration, and assert it gains exactly one `version=1`, `superseded_at IS NULL`,
`package_id IS NULL` `basin_versions` row — and that a station on that basin can train
and write a `model_artifact_basin_versions` row (cross-checks Task 2D's resolution
against a legacy basin, not just a freshly-imported one).

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
- **Feature-catalog validation (`04` §7, `:499-548`), made explicit and COMPLETE (major
  finding — the earlier list omitted required fields):** each catalog `name` matches a
  Parquet column (`04:508`); every Parquet attribute column has a catalog entry
  (`04:638`); `source_dataset` references a `manifest.source_datasets` entry (`04:511`);
  and **every** per-feature required field is present and well-typed — `type` ∈
  {float,integer}, `unit` present (`04:508-509`), **`aggregation`** describing the
  derivation (`04:512`), **`description`** (`04:513`), **`climatology_window`** present
  (object) for a forcing-derived index and **`null`** (present key, null value) for a
  geometry-derived one — and when present it MUST equal `manifest.climatology_window`
  (`04:514`), and **`required_by_models`** as an array (`04:515`, SHOULD — a missing
  entry is a warning, not a package reject).
- **Fixed Parquet shape/dtypes (`04:319-335`), validated explicitly (major finding):**
  `static_attributes.parquet` is one row per station, `gauge_id` as `Utf8`, and **every**
  attribute column is `Float64` (`04:324`) — a non-`Float64` attribute column, a
  long/multi-index layout, or a missing/duplicate `gauge_id` is a whole-package reject
  (`04:335`).
- **`validation_report.json` required fields (`04` §8, `:550-600`), validated (major
  finding):** top-level `summary` (with `passed`/`failed`/`warnings` counts) and `basins`
  (one entry per `basins.gpkg` feature) MUST be present; each per-basin entry MUST carry
  `network`, `basin_code`, `station_code`, `gateway_hru_name`, `name`, `status` ∈
  {passed,warning,failed}, and a `checks` object with the minimum checks — a malformed or
  field-missing report is a whole-package reject.
- **`bands.gpkg`: absent vs present-invalid are different (BLOCKER).** *Absent* optional
  `bands.gpkg` → fine; only basin-level rows are produced downstream, no station is
  stranded. *Present but invalid* (unreadable, wrong CRS, non-2-D, schema-nonconforming)
  → treated as an invalid geometry file, **NOT as absent**: it rejects the package (or
  holds the affected basins per §9/§10), never silently tolerated.

**Scope out:** No per-basin accept decisions (Task 1B); no writes.

**Verification** — discriminating negative fixtures: a well-formed package parses; each
whole-package reject rule raises its specific rejection; a Parquet-column-without-catalog
and a catalog-`source_dataset`-not-in-manifest and a `climatology_window` mismatch each
reject; **a catalog entry missing `aggregation`/`description` rejects; a forcing-derived
catalog entry missing the required `climatology_window` (vs a geometry-derived one whose
`climatology_window` is `null`) rejects; a non-`Float64` attribute column rejects; a
duplicate or missing `gauge_id` rejects; a `validation_report.json` missing a required
top-level or per-basin field rejects;** a file mutated vs a present producer checksum
rejects; an **absent** `bands.gpkg` parses clean while a **present malformed**
`bands.gpkg` rejects (distinct outcomes).

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

- **§5a provenance-column write path (major finding).** 082's `GatewayPolygonBindingRow`
  (`types/station.py:87-99`) and `RecapGatewayPolygonStore.store_binding`
  (`recap_gateway_polygon_store.py:38-58`) carry/write only the **six base** columns —
  neither knows about `package_id`/`imported_at` (added in Task 0A). This task
  **extends the binding type with optional `package_id`/`imported_at` fields and
  `store_binding` to write them** (including in the `on_conflict_do_update` `set_` at
  `recap_gateway_polygon_store.py:49-56`, so a re-population refreshes provenance).
  Keeping the writer on 082's store (rather than a separate 120-owned writer) preserves
  single-object ownership: 082 owns the table + type + writer; 120 owns only the additive
  columns and the population, consistent with the Ownership split above. The new fields
  are optional/nullable so 082's own fixture callers that omit them still compile.
- **Bands are persistence-only (BLOCKER resolution).** When `bands.gpkg` is present,
  120 persists it — write `basins.band_geometries` (`04:425`, column
  `db/metadata.py:56`) and emit **band-level** §5a rows (`spatial_type='elevation_band'`,
  populated `band_id`, matching the `elevation_band` value used at
  `station_weather_sources.extraction_type` `db/metadata.py:172-178` and
  `weather_forecasts.spatial_type`/`band_id` `db/metadata.py:362,369`) alongside the
  basin-level rows (`spatial_type='basin_average'`, `band_id=NULL`). These band rows are
  **stored for future use only.** 120 does **not** require 082's resolver to read them
  and does **not** undefer banding: Recap v1 is basin-average-only
  (`recap_gateway.py:493` prefilter, `:517` lock; `081:213` DECISION). When `bands.gpkg`
  is absent, only basin-level rows are written.
- **basin_average §5a rows are DELETE-then-INSERT per station (major finding).** The §5a
  table carries a **partial unique index**
  `uq_recap_gateway_polygon_bindings_one_basin_average_per_station` on `(station_id)
  WHERE spatial_type='basin_average'` (`db/metadata.py:233-238`), and the code comment at
  `db/metadata.py:225-232` mandates that the 120 importer **upsert-REPLACE** the
  basin-average binding, never accumulate. `store_binding`'s current
  `on_conflict_do_update` keys on `(station_id, gateway_hru_name, name)` — the **full PK**
  — so a correction that changes `gateway_hru_name` or `name` for the same station's
  basin-average binding would be a NEW key and a bare INSERT alongside the still-present
  old row, which violates the partial unique index and raises `IntegrityError`. This task
  therefore **DELETEs the existing `basin_average` row for `station_id` (a
  `DELETE … WHERE station_id=:sid AND spatial_type='basin_average'`) before inserting the
  new one**, so exactly one basin-average row survives even when the HRU/name changed.
  (Band rows, keyed by `station_id + band_id`, keep the PK-conflict upsert.)
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
a package without `bands.gpkg` → basin-level rows only; **the §5a rows carry the
import's `package_id`/`imported_at` (provenance columns written, not NULL);** a non-null
`band_geometries` round-trips through `store_basin`→`fetch_basin` as a **list** (not a
JSON string) — this fails against the current `json.dumps` path; 082's store-backed
resolver reads the seeded **basin-average** row back and returns the expected
`GatewayPolygonRef` (basin-average-only cross-check; band rows are not resolved).
**Correction/HRU-rename replace:** re-populating a station's basin-average binding with a
**different** `gateway_hru_name`/`name` (new package) leaves **exactly one**
basin_average row for that station — not two, and not an `IntegrityError` against
`uq_recap_gateway_polygon_bindings_one_basin_average_per_station`.

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
- **New `package_id` for an existing `(network, code)`** → a **correction (Decision B)**,
  in the exact order the partial unique index requires (see Versioned basin state): **(1)**
  stamp the prior current `basin_versions` row's `superseded_at`; **(2)** append the new
  `basin_versions` row (`version+1`, new `package_id`, `superseded_at NULL`); **(3)**
  refresh the `basins` projection + `basins.package_id`; **(4)** refresh the current §5a
  rows via the **DELETE-then-INSERT basin_average replace** (Task 2B) — never a bare
  INSERT, so an HRU/name change does not violate the per-station basin_average partial
  unique index; then add a `basin_static_packages` row and set a **material-change flag**
  in the report. The insert-only store cannot do this today — this task adds an
  upsert/`update_basin_from_package` path keyed on `(network, code)`.
- **Correction → affected-artifact set (professional-service payoff).** On superseding a
  basin version, query `model_artifact_basin_versions` for the artifacts whose lineage
  includes the now-superseded `basin_version_id`, and **emit that exact affected-artifact
  set** to the retraining path (Flow 9 / the `04` §11 "material data change → retrain"
  behavior). This is what makes a single basin correction deterministically name the
  models to retrain — auditable and complete for both station- and group-scoped
  artifacts. The set is returned in the acceptance report; the retrain itself is Flow 9.
- **New `(network, code)`** → delegates to Task 2A insert (`version=1`).
- **Material-change cascade (`04:688-695` steps 2–5: re-extract forcing, recompute
  static attributes, retrain, recompute skill)** is **operator/Flow-9-triggered and OUT
  OF SCOPE** — the importer records the correction + provenance + material-change flag
  **and emits the affected-artifact set** (above); it does not itself re-extract or
  retrain.

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

**Affected-artifact gate (discriminating).** Seed basin `B`: two artifacts trained on
`B` version `v1` and one trained on `v2` (each via `model_artifact_basin_versions` rows,
mixing a station- and a group-scoped artifact). Correct `B` (→ `v3`, superseding `v2`):
the emitted affected set MUST be **exactly the artifacts trained on the version current
at their train time that is now superseded** — not all three, not none. (The fixture
pins which artifacts each version carried, so "return everything" and "return nothing"
both fail.)

```bash
uv run pytest tests/integration/store/test_basin_importer_idempotency.py::TestReimportAndCorrections tests/integration/store/test_basin_importer_idempotency.py::TestCorrectionAffectedArtifacts
```

#### Task 2D — Train-time lineage write wiring — 120 OWNS this (SETTLED)

**Scope in:** An unpopulated lineage table is worthless for a billed service, so 120
**reaches into the training artifact-creation path** and wires it to write the
`model_artifact_basin_versions` rows for every basin a station- OR group-scoped artifact
**actually trained on**. `store_artifact` (`protocols/stores.py:396-408`,
`store/model_artifact_store.py:44-55`) is still the single chokepoint every
artifact-creation path funnels through, so the lineage write is wired THERE — but it
**cannot derive the trained basin set from `station_id`/`group_id` alone**, for two
reasons the review surfaced:

1. **`TrainingUnit` never reaches the store.** `store_artifact` receives only `model_id`,
   `artifact_bytes`, timestamps, and `station_id`/`group_id` — no `TrainingUnit`, no
   `station_ids`. `TrainingUnit` (`types/training.py:13-30`) is a caller-side domain type
   built in the training/onboarding flows; threading it into the store layer would be a
   layering violation. **The earlier draft's "resolve via `TrainingUnit.station_ids`
   inside `store_artifact`" was wrong** and is dropped.
2. **A group's trained set ≠ its full membership.** Resolving `group_id` →
   `station_group_members` (`db/metadata.py:254-273`) inside the store would record
   **every** member — but group training **skips members with no usable data**: the
   trained subset is `GroupTrainingData.station_ids` (`services/training_data.py:333,346`,
   built from `valid_station_ids` which excludes `data is None` members). Recording
   skipped members would over-claim lineage and mis-target the correction→retrain set.

**Resolution — an explicit `trained_station_ids` argument (blocker finding).** Add a
keyword-only `trained_station_ids: frozenset[StationId] | None = None` parameter to
`store_artifact` (protocol + `PgModelArtifactStore` + every fake). Callers populate it:
`{station_id}` for a station-scoped artifact, and **`GroupTrainingData.station_ids`** (the
actually-trained subset) for a group-scoped one. Inside `store_artifact`, each
`trained_station_ids` member resolves `stations.basin_id` (`db/metadata.py:84`) → the
**current** `basin_versions` row (`superseded_at IS NULL`) → one
`model_artifact_basin_versions` row. Legacy basins resolve to their backfilled `version=1`
row (Task 0A). If `trained_station_ids` is `None` (e.g. a pre-existing fake not yet
updated), no lineage rows are written and a warning is logged — the parameter is
optional so 082/other callers still compile, but every real training/onboarding caller
MUST pass it.

**ALL artifact-creation callers this must update (verified — each must now pass
`trained_station_ids`):** onboarding — `services/model_onboarding.py:1270`,
`flows/onboard_model.py:364`; **and the training/retraining flow** —
`flows/train_models.py:171,384` (`_store_artifact_task`) → `services/training.py:82,94`
(`store_and_promote_artifact` → `store_artifact`). Flow 6 retraining artifacts are the
ones MOST likely to be regenerated after a correction, so missing them would break the
correction→retrain payoff for exactly the wrong artifacts. Each of these paths has the
trained station set in scope (the `TrainingUnit`/`GroupTrainingData` it just trained
from), so passing it is a local change.

**Scope out:** No change to how artifacts are trained or promoted; no change to
`model_artifacts` columns (the join table carries the lineage). No change to 082's
fixture callers of `store_artifact` beyond accepting the new optional parameter.

**Verification (discriminating):** (a) a **station-scoped** artifact via `store_artifact`
(`trained_station_ids={station_id}`) writes exactly one `model_artifact_basin_versions`
row (its basin's current version); (b) a **group-scoped** artifact whose group has N
members but where **one member was skipped** (no usable data → absent from
`GroupTrainingData.station_ids`, `trained_station_ids` has N−1) writes exactly **N−1**
rows, NOT N — proving lineage tracks the trained subset, not full `station_group_members`
membership; (c) an artifact created through the **training/retraining flow**
(`store_and_promote_artifact`, not just onboarding) ALSO writes its lineage rows — a test
that drives the Flow-6 path and asserts the join rows exist, so a wiring that only covered
onboarding would FAIL this case; (d) a station on a **legacy** (pre-120, backfilled) basin
writes a lineage row pointing at that basin's `version=1` row.

```bash
uv run pytest tests/integration/store/test_model_artifact_lineage.py::TestLineageWriteOnStore
```

### Phase 3 — Import entrypoint + docs

#### Task 3A — Importer orchestration + acceptance report

**Scope in:** The top-level import function/CLI wiring that composes 1A→1B→2A/2C→2B in
one transaction per package, returning a structured **acceptance report** (accepted
basins, `onboarding`-held basins with reasons, package-level rejections, warnings,
material-change flags, and — for corrections — the emitted affected-artifact set from
Task 2C) so the §9 "warnings MUST remain visible in onboarding reports" requirement
(`04:653-655`) is met. The importer MUST NOT synthesize missing
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
  `model_artifact_basin_versions` lineage, §5a `package_id`/`imported_at`) and to note
  that a correction emits the affected-artifact set (§11 bullet 4 + the retrain cascade),
  replacing the "no first-class field yet / left to the implementing plan" language.
- Update `docs/spec/database-schema.md` (`:42`) and `docs/architecture-context.md`
  (`:2650`) — both still describe `basins` without the new provenance/version — to add
  `basin_static_packages`, `basin_versions`, `basins.package_id`, and the
  `model_artifact_basin_versions` lineage join table.
- **Update the authoritative Protocol spec (major finding).** Task 2D changes
  `ModelArtifactStore.store_artifact`, whose signature is documented at
  `docs/spec/types-and-protocols.md:2306-2318`. Add the keyword-only
  `trained_station_ids: frozenset[StationId] | None = None` parameter there and document
  its lineage semantics (station-scoped → `{station_id}`; group-scoped →
  `GroupTrainingData.station_ids`, the trained subset) so the spec stays authoritative for
  the store contract.
- Add an importer runbook (`docs/operations/basin-static-importer-runbook.md`) covering
  package placement, running the importer, reading the acceptance report, and the
  correction/new-`package_id` procedure.

**Scope out:** No changes to the extraction-tool brief (adjacent, `04:697-712`).

**Verification** — a lean doc test asserts: the runbook has the operator anchors (package
layout, `basin_static_packages`, acceptance report, correction procedure) as sections;
`database-schema.md` and `architecture-context.md` mention `basin_versions` +
`basins.package_id`; **`types-and-protocols.md`'s `store_artifact` carries
`trained_station_ids`;** and `04` §5a/§6.2a/§11 no longer describe the persistence target
as an open gap. Anchor checks, not full-text assertions.

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

## Settled owner decisions (2026-07-16 — recorded, not open)

- **`basins` mutation model → projection-with-history** (not strict-no-mutation). See the
  Versioned-basin-state trade-off blockquote.
- **Group-artifact provenance → `model_artifact_basin_versions` join table** (not a
  singular FK).
- **Stamp site → 120 owns the join-table schema AND the train-time write wiring**
  (Task 2D), including the additive keyword-only `store_artifact` `trained_station_ids`
  parameter (caller-supplied trained subset — NOT store-side `TrainingUnit`/
  `station_group_members` resolution, which would either violate layering or over-record
  skipped group members).

## Open questions

None blocking. The two former residuals are settled below; the only remaining gate on a
real production run is external (an accepted basin/static package to import), not a design
question.

### Settled (2026-07-16, owner)

- **Correction UX = emit + flag + KEEP SERVING; no automatic quarantine.** On a correction,
  Task 2C flags the material change and emits the affected-artifact set to Flow 9 (retrain
  automation) — but the station **stays live on its current artifact** until the operator
  promotes the retrained one. Rationale (professional-service posture): auto-quarantining a
  station on every correction would take it dark for the full retrain cycle (potentially
  days) — an availability hit a billed service can't default to. Transparency instead of
  darkness: the pending-retrain state is surfaced (the material-change flag + "forecast from
  a superseded basin version" indicator). A head hydrologist **may** quarantine a
  station for a genuinely material correction — an operator/policy decision, not automatic.
- **Coverage check source = REUSE 082's coverage manifest** (082 Task 3A/3B), not a
  standalone check — 082 is already a `depends_on`, so "basin outside required coverage"
  reads the same manifest. No duplicate coverage machinery.

## References

- `docs/requirements/04-basin-static-artifact-contract.md` (§2 `:39-62`, §5 `:283-289`,
  §5a `:291-310`, §6.2a `:415-448`, §7 `:499-548`, §9 `:628-655`, §10 `:657-672`,
  §11 `:674-695`)
- `docs/plans/117-basin-static-artifact-architecture.md` (docs-only contract alignment)
- `docs/plans/082-recap-gateway-operational-readiness.md` (Task 2D `:297` — base §5a
  table + resolver this plan populates/extends)
- `docs/plans/081-recap-dg-client-integration.md:213` (basin-average-only DECISION)
- `src/sapphire_flow/types/basin.py:11-22`; `src/sapphire_flow/db/metadata.py:42-65`
  (`basins`), `:172-178`/`:362,369` (`elevation_band` usage), `:198-238` (§5a base
  table + one-basin_average partial unique index + upsert-REPLACE comment), `:417-471`
  (`historical_forcing` version + `clock_timestamp()` precedent; NO `superseded_at`),
  `:486-488`/`:512` (`group` artifact scope + `group_id`), `:500-565` (`model_artifacts`,
  `ck_model_artifacts_scope_xor` at `:542`, no basin lineage today),
  `:254-273` (`station_group_members`, full group membership);
  `src/sapphire_flow/store/basin_store.py:43-59`/`:71` (insert-only, `json.dumps` JSONB
  bug); `src/sapphire_flow/store/recap_gateway_polygon_store.py:38-58` (§5a writer, six
  base cols only today); `src/sapphire_flow/types/station.py:87-99`
  (`GatewayPolygonBindingRow`, six base fields); `src/sapphire_flow/adapters/recap_gateway.py:493`/`:517`
  (basin-average-only prefilter/lock)
- Train-time lineage-write sites (Task 2D): `src/sapphire_flow/protocols/stores.py:396-408`
  (`store_artifact` — no basin-version/`trained_station_ids` arg today);
  `src/sapphire_flow/store/model_artifact_store.py:44-55` (concrete store);
  `src/sapphire_flow/services/training_data.py:333,346`
  (`GroupTrainingData.station_ids` = trained subset, skips no-data members);
  `src/sapphire_flow/types/training.py:13-30` (`TrainingUnit` — caller-side, never reaches
  the store); call sites `src/sapphire_flow/services/model_onboarding.py:1270`,
  `src/sapphire_flow/flows/onboard_model.py:364`, `src/sapphire_flow/services/training.py:82,94`,
  `src/sapphire_flow/flows/train_models.py:171,384`
- `docs/spec/types-and-protocols.md:2306-2318` (`store_artifact` Protocol spec — Task 3B
  update target)
- `docs/spec/database-schema.md:42`, `docs/architecture-context.md:2650` (doc-update
  targets)

## Change log for adjacent docs (flag, do not edit here beyond noting)

- **`docs/plans/README.md`** — no longer needs a 120 ownership correction. The former
  `:85-88` line now describes **Plan 124** (unrelated), and the Plan 120 index entry
  (now at `README:117`) already states the correct split (120 owns package
  import/validation + §5a-row population + the provenance layer; 082 owns the §5a base
  table + resolver). The earlier "README correction task" was a stale finding and is
  dropped.
- **082 / `04`** should carry an "incremental/regional, versioned" package-completeness
  clarification (Decision A) — flagged for those docs' owners, not edited here.

**Follow-up plans to file (out of 120 build scope):**
- **Legacy basin provenance backfill** (grill-me (c), 2026-07-22) — attribute real extraction
  provenance to pre-120 Swiss/CAMELS-CH basins currently stamped `package_id=NULL, version=1`. Not
  deployment-critical; needed for audit uniformity + if Swiss ever becomes a billed tenant. Draft as
  a small stub after 120 lands.

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
      "tasks": ["2A", "2B", "2C", "2D"],
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
    "2D": ["0A"],
    "2C": ["2A", "2B", "2D"],
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

## Escalation — `plan` workflow (2026-07-19). NOT-READY; do NOT treat as settled.

The `plan` workflow ESCALATED (R1 2 blocker/7 major → R2 2 blocker/8 major, stalled). The doc
above reads settled but has **2 blockers + 8 majors unresolved** (real, code-grounded — this is a
genuinely complex multi-subsystem plan, not planner over-scoping). Categorised:

**Blockers (design holes):**
1. **Non-package basin inserts get no `basin_versions` row.** Onboarding still inserts basins via
   `basin_store.store_basin` (`store/basin_store.py:43`), which writes no version history; Task 2D
   lineage then needs `stations.basin_id` → a current `basin_versions` row. Decide the invariant:
   `store_basin` creates a `version=1, package_id=NULL` current row, OR non-package basin writes are
   guarded/retired. + regression for a post-migration onboarding-inserted basin.
2. **Package-provenance FK inserted too late.** `basin_versions.package_id` / `basins.package_id` /
   §5a `package_id` are immediate FKs to `basin_static_packages`, but the plan writes FK-referencing
   rows before inserting that row. Insert `basin_static_packages` FIRST inside the package txn.

**Majors — clear fixes (reviewer gave solutions):**
- Band §5a rows: table PK is `(station_id, gateway_hru_name, name)`, NOT station+band_id → a band
  rename accumulates stale rows. Delete existing `elevation_band` rows for the station/basin before
  inserting the package's band set (or a real partial unique key) + a rename test.
- Package schema validation incomplete: validate ALL required gpkg/report fields (warnings/errors
  arrays, display_name, outlet coords, delineation_method, gauge_id, lat/lon, band bounds) + negative
  fixtures (`04:563,177,251`).
- Station matching must be **(network, code)-scoped** (stations unique by network+code;
  `station_store.py:79`) — not code alone.
- Flow-6 lineage must thread the **trained subset** (`GroupTrainingData.station_ids` after skips —
  `training_data.py:312,346`), NOT `TrainingUnit.station_ids` (full group membership).
- **Task 2D simplification:** do NOT widen the cross-cutting `ModelArtifactStore.store_artifact`
  Protocol (3 impls) with a `trained_station_ids` kwarg + basin resolution. Instead a standalone
  `record_artifact_basin_lineage(conn, artifact_id, trained_station_ids)` called immediately after
  each `store_artifact()` return (same conn, atomic) gives the identical "every caller wires it"
  guarantee without teaching `PgModelArtifactStore` to resolve `stations`/`basin_versions`.
- (minor) `store_artifact` returns `tuple[ArtifactId, str]`, not `ArtifactId` — fix the spec in 3B.

**Owner decisions (grill-me) — RESOLVED 2026-07-22 (owner):**
- (a) **Trained station with an unresolvable basin at lineage-write time → SPLIT by kind.**
  **`basin_id IS NULL` → skip the lineage row + log at INFO** (no WARNING — this is a legitimate,
  common state: a model declaring no static features can train on a basin-less station, and it is
  already safe by construction because `assemble_station_training_data` fails-loud UPSTREAM
  (`training_data.py:216-234`) whenever a model *requires* static features but the basin/attributes
  are absent; so a NULL basin reaching Task 2D provably means static features were not required and
  there is simply no basin version to reference). **A DANGLING `basin_id`, OR a basin that exists but
  has NO current `basin_versions` row → FAIL-LOUD.** These are integrity violations that blocker #1's
  fix (every `store_basin` write creates a `version=1, package_id=NULL` current row) + the `basin_id`
  FK are meant to make unrepresentable; if one still appears, raise rather than silently emit an
  artifact with no basin lineage (which would defeat the decision-b stale-basin SLA). Parse-don't-
  validate / invalid-states-unrepresentable posture (CLAUDE.md). Regression: (i) a no-static-feature
  model on a NULL-basin station trains + skips lineage (no raise); (ii) a dangling/no-current-version
  basin raises with a clear message.
- (b) **RATIFIED — keep serving, no auto-quarantine** (already recorded settled at the "Correction
  UX" bullet under §Open questions → Settled). Continuity wins for a billed operational service;
  the pending-retrain state is surfaced (material-change flag + "forecast from a superseded basin
  version" indicator); a head hydrologist MAY manually quarantine a genuinely material correction.
- (c) **NULL-provenance sentinel used SHORT-TERM for pre-120 legacy basins, AND a backfill follow-up
  is FILED** (owner chose the follow-up, not accept-forever). 120 still stamps legacy Swiss/CAMELS-CH
  basins `package_id=NULL, version=1` now (it must, to not block), but a new follow-up plan/stub tracks
  attributing real extraction provenance to them (audit uniformity + Swiss-as-billed-tenant readiness).
  Added to §Change log / follow-ups below; NOT in 120's build scope.

**All three owner decisions are now resolved.** The remaining escalation items — **2 blockers + 8
majors, all with reviewer-supplied fixes** (above) — are folded by the 2026-07-22 `plan`-workflow
re-run (they were design/code fixes the planner can apply, not owner calls). The extractor's full
package has **landed and its output was tested (HRU 12300, 2026-07-22)**, so 120's real-package run is
no longer gated — the plan is cleared to drive to READY.
