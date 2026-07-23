---
status: READY
created: 2026-07-16
plan: 120
title: Basin/static package importer + §5a persistence + versioned basin state
scope: Import an accepted basin/static package (incremental/regional, versioned), persist basin geometry/attributes, the §5a Gateway polygon-reference mapping, package provenance, and a versioned basin-state history so model artifacts reference the basin version they trained on; Nepal v1.
depends_on:
  - 082-recap-gateway-operational-readiness
  - 115a-weather-source-identity-schema
---

# Plan 120 — Basin/static package importer + §5a persistence + versioned basin state

**Status**: READY
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
- Import is **network-scoped** (`04:181` — `manifest.network` scopes every basin, and the
  DB station key is `(network, code)`, `db/metadata.py:131`), so two tenants importing
  the same numeric `basin_code` never collide.

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

## Ownership (schema split)

| Concern | Owner |
|---|---|
| §5a mapping table **base** schema (`station_id, basin_id, gateway_hru_name, name, spatial_type, band_id`) + the resolver that reads it (`db/metadata.py:198-238`) | **082** Task 2D (`082:297`) |
| §5a **provenance columns** (`package_id`, `imported_at`), additive on 082's base table + extending `GatewayPolygonBindingRow`/`store_binding` to write them | **120** (Task 0A schema, Task 2B writer) |
| `basin_static_packages` provenance table; `basin_versions` history table (+ one-current-per-basin partial index + legacy backfill); `basins.package_id`; `model_artifact_basin_versions` lineage join table | **120** (Task 0A) |
| Standalone `record_artifact_basin_lineage(...)` helper + wiring it at each artifact-creation call site (`store_artifact` Protocol left UNTOUCHED) | **120** (Task 2D — wires a post-`store_artifact` helper into the training/onboarding paths) |
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
`historical_forcing` (`db/metadata.py:612-669`) versions rows in place — a `version`
column (`:620`), `clock_timestamp()` `created_at` default (`:642`), version-in-natural-
key (`:662`) — precisely because **nothing holds an FK to an individual
`historical_forcing` row.** `basins` is different: `stations.basin_id`
(`db/metadata.py:84`) and the §5a `basin_id` both FK to `basins.id`. Adding a
version column and a new `basins` row per version would either strand those FKs on a
stale version or force repointing them on every correction. So `basins.id` stays the
**stable logical identity** (inbound FKs untouched), and versions live in a child
table. Only the `clock_timestamp()` deterministic-ordering + versioned-natural-key
precedent is reused from `historical_forcing`. The `superseded_at` marker is **new to
`basin_versions`** — `historical_forcing` has no such column (it identifies the current
row as the max `version`, `:662`); `basin_versions` adds `superseded_at IS NULL` as the
explicit current-version marker (backed by a partial unique index, below).

Schema (all additive; Task 0A):

- **`basin_versions`** — append-only. `id` (PK), `basin_id` (FK → `basins.id`),
  `package_id` (FK → `basin_static_packages`, **nullable** — legacy rows have no
  package, see Legacy backfill below), `version` (int), `geometry`, `attributes`
  (JSONB), `area_km2`, `band_geometries` (JSONB), `gateway_mapping` (JSONB — snapshot of
  this version's §5a mapping; **source of truth defined below**), `superseded_at`
  (nullable), `created_at`
  (`clock_timestamp()` default, `historical_forcing:642` precedent). Natural key
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
- **Ongoing non-package basin inserts (blocker review finding).** The one-time backfill
  fixes only basins that exist *at migration time*. Station onboarding still creates
  basins AFTER 120 lands: `services/onboarding.py:256` calls
  `basin_store.store_basin(basin)`, and `PgBasinStore.store_basin`
  (`store/basin_store.py:43-59`) inserts **only** a `basins` row — no `basin_versions`
  row. Such a basin would have no current version, so Task 2D's lineage write would hit
  the fail-loud "no current `basin_versions` row" branch (grill-me (a)) for any station on
  it. **Invariant (Task 0A, code change to the store):** every `store_basin` insert MUST also
  create a `version=1, superseded_at IS NULL, package_id=NULL` `basin_versions` row projecting
  the just-written geometry/attributes/`area_km2`/`band_geometries` (identical shape to the
  legacy-backfill row), written **atomically with the basins row via one data-modifying CTE**
  (D-0A) so the pair cannot be split even under the AUTOCOMMIT production connection. This makes
  "every basin has exactly one current `basin_versions` row" a store-enforced invariant for both
  the package path (Task 2A, which calls `store_basin` with `package_id` set) and the non-package
  onboarding path (`package_id=None`). Regression: a basin onboarded via
  `services/onboarding.py`'s `store_basin` path AFTER the migration gains its `version=1` current
  row, and a station on it writes a lineage row without raising.
- **`model_artifact_basin_versions`** (lineage join table, SETTLED — replaces a singular
  FK). Columns `model_artifact_id` (FK → `model_artifacts.id`), `basin_version_id`
  (FK → `basin_versions.id`); PK the pair. A **join table, not a singular
  `model_artifacts.basin_version_id` FK**, because ML models train per **station GROUP**
  (`GroupForecastModel`; `model_artifacts.group_id` `db/metadata.py:706-711`,
  `models.artifact_scope='group'` `db/metadata.py:681-683`), and a group artifact spans many
  stations → many basins → many `basin_versions`. A singular FK could record only a
  station-scoped artifact's single basin; the join table records full §11 lineage for
  **both** station- and group-scoped artifacts. `04` §11 bullet 4 becomes a join query:
  `SELECT DISTINCT model_artifact_id FROM model_artifact_basin_versions JOIN
  basin_versions … WHERE basin_versions.superseded_at IS NOT NULL` — the artifacts
  trained on now-old data. `model_artifacts` itself gains **no** new column.

**Canonical write pipeline (SINGLE source of truth — Task 2A and Task 2C both POINT HERE;
neither re-derives the FK/partial-index reasoning).** Every import runs inside one package
transaction in exactly this order — load-bearing for **both** the immediate FK to
`basin_static_packages` and the `uq_basin_versions_one_current_per_basin` partial index:

1. **Branch on idempotency / correction FIRST** (before any write): decide no-op (same
   `package_id`, identical computed checksums), reject (same `package_id`, differing
   checksums — immutability violation), correction (new `package_id` over an existing
   `(network, code)`), or new insert (unseen `(network, code)`). See Task 2C.
2. **Write package provenance next:** INSERT the `basin_static_packages` row FIRST, before
   ANY row that FK-references it. `basins.package_id`, `basin_versions.package_id`, and the
   §5a `package_id` are all **immediate** (non-`DEFERRABLE`) FKs, so inserting any of them
   before the package row raises a live `ForeignKeyViolation`.
3. **Write the new/corrected basin projection + its version:**
   - **New `(network, code)`** → call `store_basin(basin, package_id=<step-2 package>)`,
     which atomically writes the `basins` projection row **and** its paired
     `version=1, superseded_at NULL` `basin_versions` row in one data-modifying CTE
     (Task 0A). Task 2A does **not** insert basins/versions through its own separate code.
   - **Correction** → (a) stamp the prior current version's `superseded_at`; (b) append the
     new `basin_versions` row (`version+1`, `superseded_at NULL`, the step-2 `package_id`) —
     now the only current row; (c) refresh the `basins` projection + its `package_id`.
     Stamping (a) **before** appending (b) is required: appending first would momentarily
     leave two `superseded_at IS NULL` rows and violate the partial unique index.
4. **Call the §5a replace writer LAST** (Task 2B): delete-then-insert the current
   `basin_average` §5a rows per affected station, so an HRU/name rename leaves exactly one
   row and never violates `uq_recap_gateway_polygon_bindings_one_basin_average_per_station`
   (band §5a rows are out of v1 scope, so there is no band replace).

The `basin_versions` `gateway_mapping` JSONB written at step 3 is built from the in-memory
validated package structure (Task 1B output), NOT read back from the DB §5a rows written at
step 4 — see "`gateway_mapping` source of truth" below. The prior geometry/attributes/§5a
mapping survive verbatim in the superseded `basin_versions` snapshot (its `gateway_mapping`
JSONB).

**`gateway_mapping` source of truth — built from the in-memory validated package, NOT read
back from the DB (major finding — resolves a write-order contradiction).** The
`gateway_mapping` JSONB is derived from the **already-validated in-memory package structure
produced by Task 1B** (the same per-station/per-basin mapping data Task 2B later writes into
the §5a rows), NOT by reading the §5a rows back from the DB. This removes any insert-order
dependency and keeps the append-only invariant intact: at the moment the `basin_versions`
row is inserted (canonical-pipeline step 3 for a new basin; correction sub-step 3(b) for a
re-import), the DB §5a rows for this basin/version have **not yet been written** (they are
the step-4 §5a replace writer, Task 2B, which runs after), so a DB round-trip would capture
the WRONG (empty on insert, or stale on correction) mapping — and the natural "have Task 2B
UPDATE the row afterward" fix is forbidden
by the "never updated except to stamp `superseded_at`" rule. Sourcing both `gateway_mapping`
(2A) and the §5a rows (2B) from the one in-memory Task 1B structure sidesteps all three
constraints. **Duplication risk (noted):** 2A and 2B then independently derive the same
per-row shape (e.g. `spatial_type`/`band_id` assignment) from that structure; keep the
row-shaping logic in one shared function that both call so they cannot drift.

> **Trade-off — SETTLED (owner, 2026-07-16) and RE-RATIFIED (owner, 2026-07-22): keep
> projection-with-history.** Confirmed on the 2026-07-22 rework — readers use the in-place
> current `basins` row; `basin_versions` holds the audit history. Not re-opened. The `basins`
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

## Implementation status (fixer pass, 2026-07-22 — keep current, do not let this drift)

**NOT production-ready. No package loader/importer exists yet.** Landed so far, across
three passes:

- **Task 0A — DONE** (`cbe3a1c`): provenance/versioning schema (`basin_static_packages`,
  `basin_versions`, `model_artifact_basin_versions`, additive `package_id` columns) +
  `PgBasinStore.store_basin` as the single atomic basin+`version=1` creation CTE. The
  `band_geometries` `json.dumps` JSONB bug was fixed as part of this same rewrite (see the
  corrected Task 2B bullet below — it does NOT need to fix this again).
- **Task 2B — PARTIAL** (fixer pass, hardened in the second fixer pass): the §5a
  store-layer provenance write path (`GatewayPolygonBindingRow.package_id`/`imported_at`,
  `store_binding` write/upsert) is DONE, and the `basin_average` correction-replace path is
  a single atomic `INSERT ... ON CONFLICT (station_id) WHERE spatial_type='basin_average'
  DO UPDATE` (not a two-statement DELETE-then-INSERT — that shipped in round 1 and was
  SUPERSEDED in round 2 for a silent-drop-on-partial-failure bug; see the SUPERSEDED note
  below). The package-driven population itself (something that actually calls
  `store_binding` from an accepted package's dissolved geometries) is NOT built — it
  depends on Task 1A/1B/2A below.
- **Task 2D — DONE, including the service-level onboarding path (third fixer pass)**:
  `record_artifact_basin_lineage` helper (`store/model_artifact_lineage.py`), wired into
  `train_models_flow`, `onboard_model_flow`, AND `services/model_onboarding.onboard_model`
  (the latter called from `services/onboarding.py`'s station-onboarding path, e.g.
  `onboard_from_camelsch` / `flows/onboard.py::onboard_stations_flow`) right after artifact
  storage, with the NULL-skip/dangling-raise split and the D-UP upstream static-features
  gate in `services/training_data.py`. The first two fixer passes wired only the two
  Prefect-flow call sites and missed the service-level one — a station onboarded via
  `onboard_stations_flow` got no lineage row.
- **Phase 1 (Task 1A/1B — package loader, checksums, feature-catalog/per-basin acceptance),
  Task 2A (dissolve into `basins` + version snapshot), Task 2C (incremental upsert +
  versioned corrections + idempotency + affected-artifact set), and Phase 3 (Task 3A
  importer entrypoint, Task 3B docs) are NOT implemented.** Until Task 1A/1B/2A/2C/3A land,
  there is no way to actually import a basin/static package — 082's store-backed resolver
  keeps returning `None` for every station (Production-gate note, below), and the lineage
  table has no package-sourced basins to reference yet (legacy/onboarding-created basins
  still resolve correctly, per Task 2D's `version=1` fallback).

Do not treat this plan as "landed" for Nepal production purposes on the strength of Task
0A/2B/2D alone — Phase 1/2A/2C/3A are the blocking remainder.

---

## Incremental build sequence (slicing)

This plan is large and lands in **slices**, each its own `/implement` run → hold-at-PR, so every
PR stays reviewable:

1. **Foundation** (Task 0A + 2D + the 2B store-layer write/replace path) — **DONE, merged in PR #124.**
2. **Phase 1 — Tasks 1A + 1B** (package loader + checksums + feature-catalog + whole-package and
   per-basin acceptance validation) — **DONE, merged in PR #126.**
3. **Phase 2 — Tasks 2A + 2C + the 2B package-driven population** (dissolve accepted package into
   `basins` + `version=1` snapshot + `basin_static_packages` provenance; the package-driven §5a
   `basin_average` population via 082's `store_binding`; incremental upsert + versioned corrections +
   idempotency + the correction→affected-artifact set) — **THIS SLICE** (branch `feat/plan-120-phase2-persistence`).
4. **Phase 3 — Tasks 3A + 3B** (importer entrypoint/CLI + acceptance report; docs/runbook) — final slice.

**Scope rule for an `/implement` run: build ONLY the current slice's phase and STOP.** For THIS run,
implement the **write side — Task 2A, the Task 2B PACKAGE-DRIVEN §5a population, and Task 2C** — wiring the
merged Phase-1 loader output into DB persistence. Do NOT build Task 3A/3B (CLI entrypoint + docs) in this
run; they are the final slice with their own PR. Already on `main` — CONSUME, do not re-implement: Task
0A/2D + the 2B store-LAYER write/replace path (#124); Tasks 1A/1B the package loader/validation (#126). Task
2A/2C go through `store_basin` (0A) and 082's `store_binding` (0A/2B) — the atomic single-object write paths —
never their own basin/version/§5a SQL. Live-Postgres integration tests (per the plan's Verification blocks).

---

## Scope

### Phase 0 — Provenance + versioning schema

#### Task 0A — Provenance/versioning tables + additive columns (BLOCKER-gate) — DONE (`cbe3a1c`)

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
   New table only — **no** column added to `model_artifacts` (`db/metadata.py:695-739`
   unchanged, `ck_model_artifacts_scope_xor` at `:737` intact).
5. Additive nullable `package_id` (FK) + `imported_at` on 082's §5a base table
   (`db/metadata.py:198-238`); no redefinition of its six base columns.
6. **Legacy backfill (blocker finding).** A one-time data migration inserting one
   `basin_versions` row (`version=1`, `superseded_at IS NULL`, `package_id=NULL`) for
   **every** pre-existing `basins` row, projecting that basin's current geometry/
   attributes/`area_km2`/`band_geometries` (and its current §5a rows, if any, into
   `gateway_mapping`). Without this, a legacy (Swiss/CAMELS-CH) basin has no current
   `basin_versions` row, so Task 2D's lineage write finds nothing to point at and
   training breaks or writes no lineage.
7. **`store_basin` is the SINGLE atomic basin-creation path (blocker + major finding).**
   Change `PgBasinStore.store_basin` (`store/basin_store.py:43-59`) so a single insert
   creates the `basins` row **and** its paired `version=1, superseded_at IS NULL`
   `basin_versions` row atomically **using one data-modifying CTE** —
   `WITH b AS (INSERT INTO basins (...) RETURNING id) INSERT INTO basin_versions (...) SELECT
   ... FROM b` — so the pair is atomic **even under the production AUTOCOMMIT connection**
   (`flows/_db.py:78`); two separate statements would self-commit independently and could
   leave a committed `basins` row with no current version if the second failed. The version
   row projects the just-inserted geometry/attributes/`area_km2`/`band_geometries` (and, for
   the package path, the in-memory `gateway_mapping`; empty otherwise). Add an optional
   `package_id: PackageId | None = None` to both `Basin` (`types/basin.py:11-22`) and
   `store_basin` (a new `PackageId` NewType in `types/ids.py`, wrapping the
   `basin_static_packages` PK), so `store_basin` is the ONE creation path called by **both**
   station onboarding (`services/onboarding.py:256`, `package_id=None` → the version row's
   `package_id` is NULL, the legacy/non-package sentinel) **and** Task 2A's package import
   (`package_id=<the package row>`). Task 2A therefore MUST NOT insert basins/versions
   through its own separate code — the paired-version insert lives inside this one function.
   This makes "exactly one current `basin_versions` row per basin" a store-enforced invariant
   across both creation paths, and removes the Task-2D fail-loud "no current version" branch
   for any freshly created basin.

**Scope out:** No change to 082's base §5a columns; no change to `model_artifacts`
columns; no per-attribute (sub-basin) provenance table; no removal of any `basins`
column.

**Verification — split by tier (major finding: migration DATA behavior belongs in
DB-backed integration tests, per repo precedent `tests/unit/db/test_alembic_head_release_b.py`
for cheap head/metadata checks vs `tests/integration/db/test_migration_0033_camels_retire.py`
for behavior).**

*Cheap metadata/head checks (unit, `tests/unit/db`):* structural introspection (not a
substring scan) that `basin_static_packages`, `basin_versions`, and
`model_artifact_basin_versions` exist with the stated PKs/FKs/`uq(basin_id, version)` **and
the partial unique index on `(basin_id) WHERE superseded_at IS NULL`** (join-table PK =
the `(model_artifact_id, basin_version_id)` pair); `basins` gains a nullable `package_id`
FK while every pre-existing `basins` column and `uq_basins_network_code` are unchanged;
`model_artifacts` is structurally unchanged (`ck_model_artifacts_scope_xor` intact); the
§5a table gains nullable `package_id`/`imported_at` with its six base columns intact; the
Alembic head advances by exactly this one revision.

*Migration/backfill behavior (integration, DB-backed —
`tests/integration/db/test_migration_00xx_basin_static_provenance.py`), because it runs a
real Alembic data migration and asserts FK enforcement, the partial unique index, and
PostGIS geometry projection:* **Legacy-backfill regression** — seed a pre-120-style
`basins` row (with real geometry) and no `basin_versions`/`package_id`, run the migration,
and assert it gains exactly one `version=1`, `superseded_at IS NULL`, `package_id IS NULL`
`basin_versions` row (geometry projected), and that a station on that basin can train and
write a `model_artifact_basin_versions` row (cross-checks Task 2D's resolution against a
legacy basin, not just a freshly-imported one). **Non-package insert regression** — create
a basin via `PgBasinStore.store_basin` (the onboarding path) AFTER the migration and assert
it gains exactly one `version=1`, `superseded_at IS NULL`, `package_id IS NULL` current
`basin_versions` row, and that Task 2D's lineage write for a station on it succeeds (does
not hit the fail-loud no-current-version branch). **Atomic-pair regression — TWO tests
(fixer pass, 2026-07-22):** (1) a structural proxy — exactly one `conn.execute()` call
writes both rows (`TestAtomicPairRegression::test_exactly_one_execute_call_writes_both_rows`,
`tests/integration/db/test_migration_0039_basin_static_provenance.py:280-343`); AND (2) the
failure-injection variant this bullet originally called for — a temporary `CHECK` constraint
on `basin_versions` rejects one poisoned `area_km2` value, forcing the version leg of the
SAME CTE statement to fail, and the test asserts **no committed `basins` row is ever left
without a current `basin_versions` row**
(`TestAtomicPairRegression::test_version_leg_failure_leaves_no_orphaned_basins_row`,
`:344-408`) — Postgres statement-level atomicity rolls back the whole CTE even under
AUTOCOMMIT; a two-statement implementation fails BOTH tests (verified: reverting
`store_basin` to two separate `INSERT`s makes both go RED).

```bash
uv run pytest \
  tests/unit/db/test_basin_static_provenance_schema.py::TestBasinStaticPackagesTable \
  tests/unit/db/test_basin_static_provenance_schema.py::TestBasinVersionsTable \
  tests/integration/db/test_migration_0039_basin_static_provenance.py::TestLegacyBackfillRegression \
  tests/integration/db/test_migration_0039_basin_static_provenance.py::TestNonPackageInsertRegression
```

### Phase 1 — Package read + validation (§9 acceptance rules)

#### Task 1A — Package loader, checksums, feature-catalog + whole-package acceptance

**Scope in:** A Pydantic-boundary loader for the mandatory file set (`manifest.json`,
`basins.gpkg`, `static_attributes.parquet`, `feature_catalog.json`,
`validation_report.json` — `04:61-62`) plus the optional `bands.gpkg` (`04:53-56`).
Parse-don't-validate: raw external data → Pydantic model → frozen domain type.

- **Canonical checksums — source + file set defined explicitly (major finding).** The
  producer declares hashes in two possible places that are the SAME set: `manifest.checksums`
  (`04:83`, SHOULD) and/or the optional `checksums.sha256` sidecar (`04:58`). The **canonical
  payload file set** SAP3 hashes = **exactly the files the producer declared** (the
  `manifest.checksums` keys — equivalently the `checksums.sha256` entries), which by
  construction are the payload files and **exclude the self-referential/hash-bearing files**
  (`manifest.json` and `checksums.sha256` themselves — see the checked-in fixture, whose
  `manifest.checksums` covers `basins.gpkg`/`static_attributes.parquet`/`feature_catalog.json`/
  `validation_report.json`/`README.md` but NOT `manifest.json`:
  `tests/fixtures/basin_static/nepal-dhm-basins/manifest.json:40-46`). "Every present package
  file" is therefore REPLACED by "every producer-declared payload file"; hashing a file that
  hashes itself is not attempted. Rules: (1) the importer computes SHA-256 over that payload
  set (values carry the fixture's `sha256:` algorithm prefix); (2) each computed hash is
  **verified** against the producer's declared value and a mismatch — or a declared file that
  is absent — rejects the package (`04:634`); (3) if both `manifest.checksums` and a
  `checksums.sha256` sidecar are present they MUST agree; (4) the computed hashes for the
  payload set are what land in `basin_static_packages.checksums`. If the producer declared
  NO hashes at all, SAP3 still computes-and-stores the payload-set hashes (the payload set is
  then `manifest.files` ∪ any present optional payload file, still excluding
  `manifest.json`/`checksums.sha256`), with nothing to verify against.
- **Whole-package reject rules (`04:628-639`):** unsupported `contract_version`; a
  missing mandatory file; a producer-checksum mismatch; empty/conflicting `network`;
  any geometry file not EPSG:4326; package-level ID duplication; `feature_catalog.json`
  omitting a Parquet attribute column.
- **Full contract-conformance validation — authority = the contract; the exhaustive
  per-field list is pinned in the negative-fixture test names, NOT re-transcribed here
  (major findings folded; redundant per-field prose collapsed per the field-list-authority
  note).** Task 1A validates **every** required field / dtype / reject rule the contract
  marks required across `feature_catalog.json` (`04` §7 `:499-548`),
  `static_attributes.parquet` (`04:319-335`), the `basins.gpkg` required columns
  (`04:177-203`), and `validation_report.json` (`04` §8 `:556-579`), and rejects (or holds
  per §9/§10) on any violation. Rather than mirror `04` field-by-field (which rots), the
  enumerated cases live in the negative-fixture **test names/docstrings** (Verification). The
  fields reviewers flagged as previously under-validated — each its own reject with its own
  fixture — are: catalog `aggregation`/`description`/`climatology_window` (an object for a
  forcing-derived index, `null` for a geometry-derived one, and == `manifest.climatology_window`
  when present), every catalog `name` ↔ a Parquet column and every Parquet attribute column
  ↔ a catalog entry, `source_dataset` ∈ `manifest.source_datasets`; the Parquet
  `gauge_id: Utf8` one-row-per-station shape with **every** attribute column `Float64`; the
  `basins.gpkg` `display_name`/`outlet_lon`/`outlet_lat`/`delineation_method` plus the
  extractor-toolchain `gauge_id`/`latitude`/`longitude` with `latitude == outlet_lat` and
  `longitude == outlet_lon` (`04:199-203`, `gauge_id` also the Task 1B join key); and
  `validation_report.json`'s top-level `summary`/`basins` plus each per-basin `warnings` and
  `errors` array (`04:574-575`). (`required_by_models` missing = warning, not reject, `04:515`.)
- **`bands.gpkg` when present — validate FULLY, defer only the §5a writer (owner decision,
  2026-07-22).** When `bands.gpkg` is present, 120 persists only its **geometry** into
  `basins.band_geometries` JSONB (Task 2B) and writes **no** band-level §5a rows in v1 (the
  `elevation_band` §5a writer is deferred — see Task 2B). **Contract validation is NOT
  deferrable, though (owner decision, 2026-07-22):** when the file is present, Task 1A
  validates **all** required `bands.gpkg` columns, types, and parent-references per the
  contract (`04:253-271`) — `network` == `manifest.network`, `basin_code` referencing a
  `basins.gpkg` basin, `station_code` matching the parent basin, `band_id` unique within
  `network+basin_code`, `gateway_hru_name` declared in `manifest.gateway_hru_names`, `name`
  lowercase/GeoPackage-unique/not-digit-leading, `display_name`, `min_elevation_m` /
  `max_elevation_m` with `max > min`, `area_km2` positive, and 2-D valid `Polygon`/
  `MultiPolygon` in EPSG:4326 — and rejects (or holds the affected basins per §9/§10) on any
  violation. Only the **§5a row writer** for bands is deferred, not the file's validation;
  validating a present file guards `basins.band_geometries` and lets the future band-undefer
  plan re-import without re-validating.
- **`bands.gpkg`: absent vs present-invalid are different (BLOCKER).** *Absent* optional
  `bands.gpkg` → fine; only basin-level rows are produced downstream, no station is
  stranded. *Present but invalid* (unreadable, wrong CRS, non-2-D, schema-nonconforming)
  → treated as an invalid geometry file, **NOT as absent**: it rejects the package (or
  holds the affected basins per §9/§10), never silently tolerated.

> **Note (minor finding — field-list authority).** The consolidated conformance bullet above
> names only the fields reviewers flagged as previously under-validated (`gauge_id`/lat/lon,
> `warnings`/`errors`, `aggregation`/`description`/`climatology_window`), kept as a compact
> reminder so they cannot silently rot back out — the four verbose per-field bullets they
> replaced are gone. The **authoritative** field list remains the contract — `04` §4
> (`:177-203`), §5 (`:253-271`), §7 (`:499-548`), §8 (`:556-579`), §9 (`:628-655`); Task 1A
> validates **every** required-field/type/reject rule there, and the exhaustive per-field
> enumeration is pinned in the negative-fixture **test names/docstrings** (Verification
> below), where drift is caught by a failing test rather than rotting in prose. If the plan
> prose and `04` ever disagree, `04` wins.

**Scope out:** No per-basin accept decisions (Task 1B); no writes.

**Verification** — discriminating negative fixtures: a well-formed package parses; each
whole-package reject rule raises its specific rejection; a Parquet-column-without-catalog
and a catalog-`source_dataset`-not-in-manifest and a `climatology_window` mismatch each
reject; **a catalog entry missing `aggregation`/`description` rejects; a forcing-derived
catalog entry missing the required `climatology_window` (vs a geometry-derived one whose
`climatology_window` is `null`) rejects; a non-`Float64` attribute column rejects; a
duplicate or missing `gauge_id` rejects; a `basins.gpkg` missing any of `display_name`/
`outlet_lon`/`outlet_lat`/`delineation_method`/`gauge_id`/`latitude`/`longitude` rejects,
and a `basins.gpkg` whose `latitude ≠ outlet_lat` (or `longitude ≠ outlet_lon`) rejects;
a `validation_report.json` missing a required top-level or per-basin field — including the
`warnings` or `errors` array — rejects;** a file mutated vs a present producer checksum
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
non-positive; the basin's **`(network, station_code)` unmatched to a SAP3 station**;
required static features for an assigned model missing/null; Gateway feature `name`
missing/duplicated/naming-rule-violating; Gateway HRU name missing/undeclared-in-manifest;
basin outside required coverage. SHOULD-allow import with **visible** per-basin warnings
when the basin is not yet assigned to a model needing the missing feature (`04:653-655`).

- **Station identity is network-scoped (major finding).** SAP3 station identity is
  `(network, code)` — `PgStationStore.fetch_station_by_code(code, network)`
  (`store/station_store.py:79`) and the DB constraint `uq_stations_network_code`
  (`db/metadata.py:131`). Match each basin's station by its **`(network, station_code)`
  pair** (the `network` is the basin row's `network`, which already MUST equal
  `manifest.network`, `04:181`), never by `station_code` alone. A basin whose row/manifest
  `network` disagrees with the station it would otherwise match, or whose
  `(network, station_code)` matches no station, is unmatched — held in `onboarding` or
  rejected per the §9 rule, not silently bound to a same-code station in another network.

**Scope out:** No writes; the material-change cascade (§11 steps 2–5) is Task 2C's note.

**Verification:** matched `gauge_id` sets → clean join; a `gauge_id` in only one file →
raises (no partial import); each per-basin rule → the right outcome (reject-package vs
hold-`onboarding` vs accept-with-warning), with the warning surfaced in the returned
acceptance report, not swallowed; **a basin whose `station_code` exists only under a
DIFFERENT `network` is treated as unmatched (held/rejected), NOT bound to that
other-network station — proving `(network, station_code)` matching, not code-alone.**

```bash
uv run pytest tests/unit/services/test_basin_package_loader.py::TestGaugeIdJoin tests/unit/services/test_basin_package_loader.py::TestPerBasinAcceptance
```

### Phase 2 — Persistence (write side)

#### Task 2A — Dissolve accepted package into `basins` + version snapshot + provenance

**Scope in:** One DB transaction per package (all-or-nothing at the package level; per-basin
`onboarding` holds from 1B are recorded, not silent skips). This task implements the
**new-basin branch of the canonical write pipeline** defined ONCE under "Versioned basin
state" — Task 2A does **not** re-derive the FK-order / partial-index reasoning; it POINTS
BACK to that paragraph (as Task 2C does). Concretely, inside the one-package transaction:

1. **Insert the `basin_static_packages` provenance row FIRST** (canonical step 2; computed
   `checksums` retained even though package files are discarded, `04:429-430`), so its
   `package_id` exists before anything FK-references it.
2. For each **new** `(network, basin_code)` accepted basin, call
   `store_basin(basin, package_id=<the step-1 package>, gateway_mapping=<the in-memory §5a
   snapshot>)` (canonical step 3, new-basin branch). `store_basin` (Task 0A) atomically
   writes the `basins` projection row — `geometry` (2-D `MultiPolygon`, EPSG:4326),
   `attributes` JSONB (`{name: value}` over every `Float64` column), `area_km2`/
   `regional_basin`, `band_geometries`, and `basins.package_id` (`04:415-434`) — **and** the
   paired `version=1, superseded_at NULL` `basin_versions` row in one data-modifying CTE.
   Task 2A does **NOT** insert basins/versions through its own separate SQL; it goes through
   `store_basin` so the pair is atomic and the package + non-package onboarding paths share
   one invariant-enforcing function.

The `basin_versions` `gateway_mapping` JSONB is built from the **in-memory validated package
structure (Task 1B output)**, NOT read back from the §5a table — see "`gateway_mapping` source
of truth" under Versioned basin state; this is why it is populated here (passed into
`store_basin`) even though the DB §5a rows are written later (Task 2B, canonical step 4). Both
2A's `gateway_mapping` and 2B's §5a rows are shaped from the one Task 1B structure via a shared
row-shaping helper so they cannot drift.

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
`attributes IS NULL` and not `0`. **FK-order negative test:** a fixture that attempts the
old order (write `basins`/`basin_versions` before the `basin_static_packages` row) surfaces
a live `ForeignKeyViolation`, proving the package row must be inserted first — and the
implemented importer, inserting the package first, completes without it.

```bash
uv run pytest tests/integration/store/test_basin_importer_persistence.py::TestDissolveIntoBasins
```

#### Task 2B — §5a mapping population + band persistence + store JSONB fix — PARTIAL (store-layer write/replace path done, fixer pass; package-driven population still open)

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
- **Bands persist ONLY as `basins.band_geometries` JSONB — no §5a band rows in v1 (major
  finding: don't build unused complexity).** When `bands.gpkg` is present, 120 persists its
  geometries into the existing `basins.band_geometries` JSONB column (`04:425`, column
  `db/metadata.py:56`) and **stops there.** It does **NOT** emit band-level §5a rows
  (`spatial_type='elevation_band'`) — nothing in 120, 082, or 081 reads a band-level §5a row
  in v1: Recap v1 is basin-average-only (`recap_gateway.py:493` prefilter, `:517` lock;
  `081:213` DECISION), so a band §5a writer (with its delete-then-insert idempotency and
  rename-safety logic) would be write-path complexity with **zero consumers**. Contract §12
  (`04:697-712`) guarantees a durable regeneration path, so deferring band §5a rows is not
  lossy — the future plan that actually undefers banding in 082's resolver re-imports the
  package and populates them then. Only **basin-level** §5a rows
  (`spatial_type='basin_average'`, `band_id=NULL`) are written by 120. **Deferred to that
  future plan (out of 120 scope):** the `elevation_band` §5a row writer, its per-station
  delete-then-insert idempotency, and its band-rename regression. (The `elevation_band`
  value and `db/metadata.py:172-178`/`:362,369` usages remain the future writer's target.)
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
  **SUPERSEDED (fixer round, 2026-07-22, major finding):** two-statement DELETE-then-INSERT
  on an AUTOCOMMIT connection (`setup_production_stores`) is NOT atomic — a failure on the
  INSERT half (e.g. an invalid `package_id` FK) left the DELETE already committed, silently
  dropping the station's §5a binding. `store_binding` now does the replace in a **single**
  `INSERT ... ON CONFLICT (station_id) WHERE spatial_type='basin_average' DO UPDATE`
  statement, targeting the same partial unique index directly, so Postgres commits the
  whole replace or none of it. See `src/sapphire_flow/store/recap_gateway_polygon_store.py:39`
  and the failure-injection regression
  `TestBasinAverageUniquenessConstraint::test_store_binding_replace_leaves_old_row_intact_on_insert_failure`.
- **Store JSONB fix — `band_geometries` ONLY — ALREADY LANDED IN TASK 0A (was
  major; RESOLVED, doc corrected post-Task-0A-review).** The original
  concern was that `PgBasinStore.store_basin` wrapped `band_geometries` in
  `json.dumps(...)` before the JSONB column, storing a JSON **string**
  scalar instead of a JSON array. Task 0A's rewrite of `store_basin` (the
  single atomic basin+`version=1` CTE path, `basin_store.py:47-114`) already
  passes `basin.band_geometries` straight through as a Python list (both in
  the `basins` insert, `:78`, and the paired `basin_versions` row, `:93`) —
  no `json.dumps` call remains anywhere in the file. Verified empirically: a
  non-null `band_geometries` round-trips through `store_basin`→`fetch_basin`
  as a **list**, today, on top of Task 0A alone. **`attributes` was never
  affected** — it was already passed straight to its JSONB column
  (`attributes=basin.attributes`), with no `json.dumps`. Task 2B therefore
  does **NOT** need to touch either field; this bullet is retained only so
  Task 2B's own JSONB-shaped work (`gateway_mapping`, the §5a provenance
  columns) has the prior finding's context on record.

**Scope out:** No Gateway-side HRU registration/upload (manual, 082 runbook Task 4A); no
forcing fetch (082 adapters); resolver behavior unchanged (082-owned).

**Verification:** a package with `bands.gpkg` → its geometries land in
`basins.band_geometries` (JSONB) and **exactly one basin-level §5a row**
(`spatial_type='basin_average'`, `band_id IS NULL`) — and **NO `elevation_band` §5a rows**
(band §5a writing is deferred, above); a package without `bands.gpkg` → the same one
basin-level row, `band_geometries` NULL/empty; **the basin-level §5a row carries the
import's `package_id`/`imported_at` (provenance columns written, not NULL) — this IS a
genuine Task 2B red-first case, unlike the `band_geometries` round-trip below;** a
non-null `band_geometries` round-trips through `store_basin`→`fetch_basin` as a **list**
(not a JSON string) — **already GREEN as of Task 0A** (see the corrected bullet above); a
Task 2B implementer should assert this as a regression-guard, not author it as a red-first
case, since the `json.dumps` bug it originally targeted is already fixed; 082's
store-backed resolver reads the seeded **basin-average** row back and returns the expected
`GatewayPolygonRef`. **Correction/HRU-rename replace:** re-populating a station's
basin-average binding with a **different** `gateway_hru_name`/`name` (new package) leaves
**exactly one** basin_average row for that station — not two, and not an `IntegrityError`
against `uq_recap_gateway_polygon_bindings_one_basin_average_per_station`. **IMPLEMENTED
(fixer pass, 2026-07-22):** the §5a provenance write path (optional
`package_id`/`imported_at` on `GatewayPolygonBindingRow` + `store_binding` write/upsert)
and the basin_average replace path both landed —
`src/sapphire_flow/store/recap_gateway_polygon_store.py`,
`tests/integration/store/test_recap_gateway_polygon_store.py`
(`TestProvenanceWritePath`, `TestBasinAverageUniquenessConstraint`). **Note (second fixer
round, 2026-07-22):** the replace path shipped as two-statement DELETE-then-INSERT and was
then hardened to a single atomic `INSERT ... ON CONFLICT DO UPDATE` statement — see the
SUPERSEDED note above. The package-driven population itself (Task 1A/1B loader → this
store) is still open.

```bash
# Store-layer provenance/replace path (fixer pass, DONE):
uv run pytest tests/integration/store/test_recap_gateway_polygon_store.py::TestProvenanceWritePath tests/integration/store/test_recap_gateway_polygon_store.py::TestBasinAverageUniquenessConstraint
# Package-driven population (Task 1A/1B/2A dissolve -> this store, still open):
uv run pytest tests/integration/store/test_basin_importer_persistence.py::TestFiveAMappingPopulation
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
- **New `package_id` for an existing `(network, code)`** → a **correction (Decision B)**.
  Apply the **correction branch of the canonical write pipeline defined ONCE in Versioned
  basin state** — do NOT re-derive the FK/partial-index rationale here (minor finding: one
  copy so the two cannot drift). That branch is: canonical step 2 insert the new
  `basin_static_packages` row FIRST → canonical step 3 correction sub-steps (a) stamp the
  prior current `basin_versions.superseded_at`, (b) append the new `version+1`
  `basin_versions` row, (c) refresh the `basins` projection + `basins.package_id` →
  canonical step 4 refresh the current §5a rows. Two correction-specific notes: step 4's §5a
  refresh is the **atomic basin_average replace** (Task 2B; a single
  `INSERT ... ON CONFLICT DO UPDATE` statement as of the second fixer round — see the
  SUPERSEDED note in Task 2B above) — never a bare
  INSERT, so an HRU/name rename does not leave a stale row or violate
  `uq_recap_gateway_polygon_bindings_one_basin_average_per_station` (band §5a rows are out of
  v1 scope, so there is no band replace); and after step 4 set a **material-change flag** in
  the report. The insert-only `store_basin` path cannot express a correction (it is the
  new-basin creation path) — this task adds a separate upsert/`update_basin_from_package`
  path keyed on `(network, code)` for the correction sub-steps.
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

#### Task 2D — Train-time lineage write wiring — 120 OWNS this (SETTLED) — DONE (fixer pass, 2026-07-22)

**Scope in:** An unpopulated lineage table is worthless for a billed service, so 120 wires
the training/onboarding paths to write the `model_artifact_basin_versions` rows for every
basin a station- OR group-scoped artifact **actually trained on**. The lineage write is a
**standalone helper, NOT a widening of the cross-cutting `ModelArtifactStore.store_artifact`
Protocol** — this is the review-settled design (the earlier "add a `trained_station_ids`
kwarg to `store_artifact`" draft is DROPPED; rationale below).

**Prerequisite subtask — close the upstream static-features gate (D-UP, major finding —
backs grill-me (a)) — DONE (fixer pass, 2026-07-22).** The NULL-vs-dangling split below
relies on "static requirements fail loud UPSTREAM", but that was only true, pre-fix, for
`basin_id IS None`.
`assemble_station_training_data` (`services/training_data.py:213,228`) checks missing static
features **only** inside `if basin is not None and basin.attributes:` (`:215-216`) — so when
static features are REQUIRED but the basin **row is absent** (dangling `basin_id`) or its
`attributes` are **absent/empty**, the function falls through with `static_attributes=None`
instead of failing. Task 2D therefore FIRST changes `assemble_station_training_data` to
**return `None` (fail-loud, log a warning) whenever `model.data_requirements.static_features`
is non-empty but the basin row OR its `attributes` are absent/empty** — not only the
`basin_id IS None` branch. Add station- and group-level tests (a required-static model on a
station whose basin is missing, and whose `attributes` are empty, each returns `None`; the
existing `basin_id IS None` case still returns `None`). This is what makes grill-me (a)'s
"skip on NULL basin / fail-loud on dangling" sound in **Flow-6 retraining**, not just
onboarding: a required-static model can never reach the lineage helper with a NULL/empty
basin, so a NULL basin at helper time provably means static features were not required.

**Why NOT widen `store_artifact` (major finding — simpler, smaller blast radius).**
`store_artifact` (`protocols/stores.py:402-413`, `store/model_artifact_store.py:44-55`,
plus the fakes) is a foundational store contract with 3 implementations and an
authoritative spec entry. Teaching it about basin lineage would (a) force a signature
change across the Protocol + every implementation + the spec, and (b) require
`PgModelArtifactStore` to resolve `stations`/`basin_versions` — table knowledge that
doesn't belong in an artifact-bytes store. Two facts the review surfaced make the kwarg
both unnecessary and wrong:

1. **`TrainingUnit` never reaches the store.** `store_artifact` receives only `model_id`,
   `artifact_bytes`, timestamps, and `station_id`/`group_id` — no `TrainingUnit`, no
   `station_ids`. `TrainingUnit` (`types/training.py:13-30`) is a caller-side domain type;
   threading it into the store layer would be a layering violation.
2. **A group's trained set ≠ its full membership.** Resolving `group_id` →
   `station_group_members` (`db/metadata.py:254-273`) inside the store would record
   **every** member — but group training **skips members with no usable data**: the trained
   subset is `GroupTrainingData.station_ids` (`services/training_data.py:333,348`, built
   from `valid_station_ids` which excludes `data is None` members). Recording skipped
   members would over-claim lineage and mis-target the correction→retrain set.

**Resolution — a standalone `record_artifact_basin_lineage(...)` helper, called right after
`store_artifact` returns.** Add `record_artifact_basin_lineage(store_or_conn, artifact_id:
ArtifactId, trained_station_ids: Collection[StationId])` (a new module, e.g.
`store/model_artifact_lineage.py`). For each `trained_station_ids` member it resolves
`stations.basin_id` (`db/metadata.py:84`) → the **current** `basin_versions` row
(`superseded_at IS NULL`) → one `model_artifact_basin_versions` row (legacy/onboarding basins
resolve to their `version=1` current row from Task 0A). `store_artifact`, the Protocol,
`PgModelArtifactStore`, and the fakes are **left untouched**. (`Collection`, not `frozenset` —
the trained-subset call sites pass a `tuple`, `GroupTrainingData.station_ids` being
`tuple[StationId, ...]` at `types/model.py:116`; a widened parameter type avoids a forced
`frozenset(...)` at every caller — minor finding.)

`store_or_conn` is whatever the calling flow task already has to reach the DB — in production
the **same AUTOCOMMIT connection** the flow stores run on (obtained from the store setup, NOT
by reaching into `PgModelArtifactStore._conn`), and in the many store-agnostic flow tests a
lineage-recording fake. The helper writes its join rows **directly, right after
`store_artifact` returns** (and, in the training path, after the promotion) — **NON-ATOMIC and
LOG-LOUD on failure**, exactly matching the pre-existing store+promote relationship, which is
ALSO non-atomic under AUTOCOMMIT today. There is deliberately no new transaction boundary.

**No new transaction boundary — the lineage write is NON-ATOMIC, deliberately (owner +
orchestrator, 2026-07-22 — resolves the R2/R3 stall).** An earlier draft tried to make the
lineage write atomic with the artifact INSERT + promotion by opening a fresh
`engine.begin()` transaction in the store-artifact tasks. **That is CUT.** It fights the
codebase and over-invests:

- Production flow stores run on a single **AUTOCOMMIT** connection (`flows/_db.py:78`), so
  there is no enclosing transaction to join and store + promote are ALREADY non-atomic today
  — a pre-existing, accepted property.
- The two store-artifact flow tasks are **store-agnostic** — `_store_artifact_task`
  (`flows/train_models.py:174`) and `_store_onboarding_artifact_task`
  (`flows/onboard_model.py:382`) both type their store as `object` and are driven by
  `FakeModelArtifactStore` in **dozens** of tests (`tests/unit/flows/test_train_models.py`,
  `tests/unit/flows/test_onboard_model_flow.py`, `tests/unit/services/test_model_onboarding.py`)
  with **no engine / `DATABASE_URL` in scope**. Threading an engine + `engine.begin()` into
  those tasks would break the store-agnostic contract and every fake-driven test.

So the helper is simply **called right after `store_artifact` returns**, writing the join
rows on the connection/store the task already has, **NON-ATOMIC, LOG-LOUD on failure** —
matching the pre-existing (already non-atomic under AUTOCOMMIT) store+promote relationship.
The `store_artifact` Protocol/signature, `PgModelArtifactStore`, and the fakes stay
**UNCHANGED**; only the two flow tasks gain the post-store helper call. A lineage-write
failure is logged loudly (and surfaces in the acceptance/onboarding report), not swallowed.

> **YAGNI note.** The `model_artifact_basin_versions` table has **zero consumers today** —
> Flow 9 (the hard correction→retrain SLA that would want an atomic
> artifact+lineage guarantee) is **out of scope** for 120. Upgrade to a real transaction
> only **if/when** Flow 9 (or another consumer) actually needs a hard correction→retrain
> SLA; until then a non-atomic, log-loud lineage write is proportionate and keeps the
> store-agnostic flow tasks intact.

**Unresolvable-basin behavior — SPLIT by kind (grill-me (a), owner-resolved 2026-07-22).**
The helper does NOT treat every missing basin the same:

- **`stations.basin_id IS NULL` → SKIP the lineage row for that station + log at INFO (no
  raise, no WARNING).** This is a legitimate, common state: a model declaring no static
  features can train on a basin-less station, and it is safe by construction — with the D-UP
  prerequisite subtask above, `assemble_station_training_data` fails loud UPSTREAM
  (`services/training_data.py:213-234`) whenever a model *requires* static features but the
  basin row OR its attributes are absent/empty, so a NULL basin reaching the helper provably
  means static features were not required and there is no basin version to reference.
- **A DANGLING `basin_id` (no `basins` row), OR a `basins` row with NO current
  `basin_versions` row → FAIL LOUD (raise with a clear message).** These are integrity
  violations that the Task 0A invariant (every `store_basin` insert creates a `version=1,
  package_id=NULL` current row) plus the `basin_id` FK are meant to make unrepresentable; if
  one still appears, raise rather than silently emit an artifact with no basin lineage
  (which would defeat the Decision-B stale-basin retrain SLA). Parse-don't-validate /
  invalid-states-unrepresentable posture (CLAUDE.md).

**Wiring — SPLIT by path; call the helper right after `store_artifact` returns, on the
connection/store the task already has (verified sites). The training and onboarding paths
differ in where the artifact is promoted, so the helper lands at a different point in each:**

- **Training/retraining flow — lineage AFTER store + promote.** `flows/train_models.py:413`
  calls `_store_artifact_task` (`:174-180`) → `services/training.py:82,94`
  (`store_and_promote_artifact`, which does `store_artifact` **then** `promote_artifact`
  together). Call the helper immediately **after `store_and_promote_artifact` returns** the
  `artifact_id`. Crucially, `_store_artifact_task` receives only `TrainingUnit` (`:175`),
  which for a group carries the FULL membership — **NOT** the trained subset. The trained
  subset `GroupTrainingData.station_ids` lives in `data` (`flows/train_models.py:358`, in
  scope at the `:413` call site). So thread the trained set through from `data`:
  `{unit.station_id}` for a station-scoped unit, and **`data.station_ids`** for a group-scoped
  one (the post-skip subset, a `tuple` — passed straight to the `Collection` param). Flow-6
  artifacts are the ones MOST likely to be regenerated after a correction, so getting their
  trained subset right is what makes the correction→retrain payoff target the correct
  artifacts.
- **Onboarding — lineage AFTER store ONLY; promotion is UNTOUCHED.** Onboarding stores the
  artifact in `TRAINING` status and does **NOT** promote it here — promotion happens later,
  after the skill gate (`services/model_onboarding.py:1268` "do NOT promote yet";
  `flows/onboard_model.py:814` store call, task defined at `:382`, `:364` is NOT the store
  call — minor finding). So call the helper **right after the store returns** its
  `artifact_id`, with `{station_id}` (station-scoped) or the assembled group's trained subset.
  **Do NOT move or touch the post-skill-gate promotion** — the lineage row is written at store
  time regardless of whether the artifact is later promoted or rejected (an artifact that
  fails the skill gate still records what it trained on, which is fine — lineage answers "what
  data did this artifact see", independent of promotion).

**Scope out:** No change to `store_artifact`'s signature, the `ModelArtifactStore`
Protocol, `PgModelArtifactStore`, or the fakes; no change to how artifacts are trained or
promoted; no change to `model_artifacts` columns (the join table carries the lineage).

**Verification (discriminating):** (a) a **station-scoped** artifact wired via the helper
(`trained_station_ids={station_id}`) writes exactly one `model_artifact_basin_versions` row
(its basin's current version); (b) a **group-scoped** artifact whose group has N members but
where **one member was skipped** (no usable data → absent from `GroupTrainingData.station_ids`,
so the helper receives N−1) writes exactly **N−1** rows, NOT N — proving lineage tracks the
trained subset, not full `station_group_members` membership; (c) an artifact created through
the **training/retraining flow** (`store_and_promote_artifact`, not just onboarding) ALSO
writes its lineage rows — a test that drives the Flow-6 path and asserts the join rows exist,
so a wiring that only covered onboarding FAILS this case; (d) a station on a **legacy**
(pre-120, backfilled) basin writes a lineage row pointing at that basin's `version=1` row;
**(e) a no-static-feature model on a station with `basin_id IS NULL` trains and the helper
SKIPS the lineage row WITHOUT raising (INFO-logged); (f) a station whose `basin_id` is
dangling or whose basin has no current `basin_versions` row makes the helper RAISE with a
clear message** — proving the NULL-vs-dangling split, not a blanket skip or a blanket raise;
**(g) D-UP upstream gate: a required-static model on a station whose basin row is absent, or
whose `attributes` are empty, makes `assemble_station_training_data` return `None` (fail-loud)
BEFORE the helper is reached — so the helper never sees a required-static NULL/empty basin (a
station- and a group-level case).**

```bash
uv run pytest tests/integration/store/test_model_artifact_lineage.py::TestLineageWriteHelper
```

### Phase 3 — Import entrypoint + docs

#### Task 3A — Importer orchestration + acceptance report

**Scope in:** The top-level import function/CLI wiring that runs, in one transaction per
package, the **canonical write pipeline** (defined once under "Versioned basin state"):
validate (Task 1A → 1B) → branch idempotency/correction (Task 2C) → write package provenance
(Task 2A step 1, `basin_static_packages` FIRST) → write the new/corrected basin projection +
version (Task 2A `store_basin` for a new basin, or Task 2C's correction sub-steps) → call the
§5a replace writer LAST (Task 2B). It returns a structured **acceptance report** (accepted
basins, `onboarding`-held basins with reasons, package-level rejections, warnings,
material-change flags, any lineage-write failures, and — for corrections — the emitted
affected-artifact set from Task 2C) so the §9 "warnings MUST remain visible in onboarding
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
  `model_artifact_basin_versions` lineage, §5a `package_id`/`imported_at`) and to note
  that a correction emits the affected-artifact set (§11 bullet 4 + the retrain cascade),
  replacing the "no first-class field yet / left to the implementing plan" language.
- Update `docs/spec/database-schema.md` (`:42`) and `docs/architecture-context.md`
  (the `basins` table section at `:2681`, NOT `:2650` which is
  `calculated_station_formulas` — minor finding) — both still describe `basins` without the
  new provenance/version — to add `basin_static_packages`, `basin_versions`,
  `basins.package_id`, and the `model_artifact_basin_versions` lineage join table.
- **Document the standalone lineage helper — do NOT edit the `store_artifact` signature
  (revised per the Task 2D design).** Task 2D leaves `ModelArtifactStore.store_artifact`
  UNCHANGED, so the spec's `store_artifact` entry (the `ModelArtifactStore` section starts
  at `docs/spec/types-and-protocols.md:2383`, NOT `:2306-2318` which is `WeatherForecastStore`
  — minor finding) needs **no** `trained_station_ids` parameter. Instead, document the new
  `record_artifact_basin_lineage(store_or_conn, artifact_id, trained_station_ids)` helper and
  its lineage semantics (called **right after `store_artifact` returns**, NON-ATOMIC/log-loud;
  station-scoped → `{station_id}`; group-scoped → `GroupTrainingData.station_ids`, the trained
  subset; NULL-basin → skip; dangling/no-current → raise) where the store/lineage contract is
  described. **While there, fix the spec's
  stale `store_artifact` return type** (minor finding): the spec shows `-> ArtifactId` at
  `docs/spec/types-and-protocols.md:2398`, but the code returns `tuple[ArtifactId, str]`
  (`protocols/stores.py:413`, `store/model_artifact_store.py:44-55`) — correct it to
  `tuple[ArtifactId, str]`.
- Add an importer runbook (`docs/operations/basin-static-importer-runbook.md`) covering
  package placement, running the importer, reading the acceptance report, and the
  correction/new-`package_id` procedure.
- **Refresh the plan status in `docs/plans/README.md`** (small status-only edit): Plan 120 is
  **no longer paused/gated on the extractor** — the extractor package **landed and its output
  was tested (HRU 12300, 2026-07-22)**, so 120's real-package run is unblocked (the remaining
  production gate is only that an accepted package be imported, per the Production-gate note).
  Status-line only; do not re-open the ownership split (already correct).

**Scope out:** No changes to the extraction-tool brief (adjacent, `04:697-712`).

**Verification** — a lean doc test asserts: the runbook has the operator anchors (package
layout, `basin_static_packages`, acceptance report, correction procedure) as sections;
`database-schema.md` and `architecture-context.md` mention `basin_versions` +
`basins.package_id`; **`types-and-protocols.md` documents `record_artifact_basin_lineage`
and its `store_artifact` return type reads `tuple[ArtifactId, str]` (not `ArtifactId`);**
and `04` §5a/§6.2a/§11 no longer describe the persistence target as an open gap. Anchor
checks, not full-text assertions.

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
- Banding in the resolver / operational fetch — band **geometries** are persisted into
  `basins.band_geometries` (Task 2B) but band-level **§5a rows** are NOT written in v1
  (deferred to the future band-undefer plan); Recap v1 stays basin-average-only (`081:213`).

## Settled owner decisions (2026-07-16 — recorded, not open)

- **`basins` mutation model → projection-with-history** (not strict-no-mutation). See the
  Versioned-basin-state trade-off blockquote.
- **Group-artifact provenance → `model_artifact_basin_versions` join table** (not a
  singular FK).
- **Stamp site → 120 owns the join-table schema AND the train-time write wiring**
  (Task 2D), via a **standalone `record_artifact_basin_lineage(...)` helper called after
  each `store_artifact()` return** — NOT a widening of the `store_artifact` Protocol
  (revised per the 2026-07-22 review: the kwarg design is dropped as unnecessary
  blast-radius; see Task 2D "Why NOT widen `store_artifact`"). The helper takes the
  caller-supplied trained subset (`{station_id}` or `GroupTrainingData.station_ids`) — NOT
  store-side `TrainingUnit`/`station_group_members` resolution, which would either violate
  layering or over-record skipped group members.

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
  table + one-basin_average partial unique index + upsert-REPLACE comment), `:612-669`
  (`historical_forcing` version + `clock_timestamp()` precedent; NO `superseded_at`),
  `:681-683` (`models.artifact_scope`) / `:706-711` (`model_artifacts.group_id`),
  `:695-739` (`model_artifacts`, `ck_model_artifacts_scope_xor` at `:735-738`, no basin
  lineage today), `:254-273` (`station_group_members`, full group membership);
  `:131` (`uq_stations_network_code` — station identity is `(network, code)`);
  `src/sapphire_flow/store/basin_store.py:43-59`/`:71` (`store_basin` today inserts only a
  `basins` row — Task 0A makes it the single atomic basin+`version=1` CTE creation path;
  `attributes` already passed directly to JSONB (no bug), `band_geometries` `json.dumps`
  JSONB bug ALREADY FIXED in Task 0A's rewrite — not deferred to Task 2B, see the
  corrected Task 2B bullet above); `src/sapphire_flow/store/station_store.py:79`
  (`fetch_station_by_code(code, network)` — network-scoped station match);
  `src/sapphire_flow/services/onboarding.py:256` (`store_basin` non-package insert path);
  `src/sapphire_flow/store/recap_gateway_polygon_store.py:38-58` (§5a writer, six
  base cols only today); `src/sapphire_flow/db/metadata.py:222` +
  `alembic/versions/0032_recap_gateway_polygon_bindings.py:78` (§5a PK =
  `(station_id, gateway_hru_name, name)` — grounds the deferred band-writer's
  delete-then-insert requirement, out of v1 scope);
  `src/sapphire_flow/types/station.py:87-99`
  (`GatewayPolygonBindingRow`, six base fields); `src/sapphire_flow/adapters/recap_gateway.py:493`/`:517`
  (basin-average-only prefilter/lock)
- Lineage-write design (Task 2D — NON-ATOMIC, no new transaction): `src/sapphire_flow/flows/_db.py:78`
  (production connection opened `isolation_level="AUTOCOMMIT"` — store + promote are already
  non-atomic today, so a new `engine.begin()` for lineage is CUT); the store-artifact flow
  tasks are store-agnostic (`_store_artifact_task` `flows/train_models.py:174`,
  `_store_onboarding_artifact_task` `flows/onboard_model.py:382`, both typed `object`, driven by
  `FakeModelArtifactStore` in `tests/unit/flows/test_train_models.py`,
  `tests/unit/flows/test_onboard_model_flow.py`, `tests/unit/services/test_model_onboarding.py`);
  `src/sapphire_flow/store/model_artifact_store.py:23` (`_conn` is private — helper must NOT
  reach into it); `src/sapphire_flow/types/model.py:116`
  (`GroupTrainingData.station_ids: tuple[StationId, ...]` — helper param is `Collection`)
- Train-time lineage-write sites (Task 2D — standalone helper, `store_artifact` UNCHANGED):
  `src/sapphire_flow/protocols/stores.py:402-413`
  (`store_artifact` Protocol — returns `tuple[ArtifactId, str]`, left untouched);
  `src/sapphire_flow/store/model_artifact_store.py:44-55` (concrete store — untouched);
  `src/sapphire_flow/services/training_data.py:333,348`
  (`GroupTrainingData.station_ids` = trained subset, skips no-data members);
  `:213,228` (`assemble_station_training_data` today gates static features only inside
  `basin is not None and basin.attributes` — Task 2D's D-UP prerequisite extends it to
  fail-loud when static features are required but the basin row OR attributes are absent/empty,
  grounding the NULL-basin skip);
  `src/sapphire_flow/types/training.py:13-30` (`TrainingUnit` — caller-side, never reaches
  the store; group units carry FULL membership, not the trained subset);
  helper call sites `src/sapphire_flow/services/model_onboarding.py:1268`,
  `src/sapphire_flow/flows/onboard_model.py:814` (store call; task def `:382`, NOT `:364`),
  `src/sapphire_flow/services/training.py:82,94`,
  `src/sapphire_flow/flows/train_models.py:174,413` (`_store_artifact_task` receives only
  `TrainingUnit`; `data` with `GroupTrainingData.station_ids` is in scope at the `:413`
  call site — `:358`)
- `docs/spec/types-and-protocols.md:2383` (`ModelArtifactStore` Protocol spec, with the
  stale `-> ArtifactId` return at `:2398` — Task 3B fixes to `tuple[ArtifactId, str]`);
  `:2306-2318` is `WeatherForecastStore`, NOT `store_artifact`
- `docs/spec/database-schema.md:42`, `docs/architecture-context.md:2681` (`basins` table
  section; `:2650` is `calculated_station_formulas`) — doc-update targets

## Change log for adjacent docs (flag, do not edit here beyond noting)

- **`docs/plans/README.md`** — the 120 index entry (now at `README:117`) already states the
  correct ownership split (120 owns package import/validation + §5a-row population + the
  provenance layer; 082 owns the §5a base table + resolver), so no ownership correction is
  needed. Task 3B DOES make a small **status-only** README edit: 120 is no longer paused/gated
  on the extractor — the extractor package landed + was tested (HRU 12300, 2026-07-22).
- **082 / `04`** should carry an "incremental/regional, versioned" package-completeness
  clarification (Decision A) — flagged for those docs' owners, not edited here.

**Follow-up plans to file (out of 120 build scope):**
- **Band §5a undefer** (D-BAND, 2026-07-22) — when a deployment/model actually needs
  elevation-band forcing, add the `elevation_band` §5a row writer (its per-station
  delete-then-insert idempotency, and its band-rename regression) to 082's resolver path and
  re-import the package. 120 already validates `bands.gpkg` FULLY when present and persists band
  **geometries** to `basins.band_geometries`, and contract §12 (`04:697-712`) guarantees a
  lossless re-import, so nothing is lost by deferring the §5a rows. **Tracked here so the
  deferral is not forgotten.**
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
    "2C": ["2A", "2D"],
    "3A": ["2B", "2C"],
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

## Escalation — `plan` workflow, RESOLVED in the 2026-07-22 owner+orchestrator rework (this doc)

The `plan` workflow ESCALATED twice. First (2026-07-19): R1 2 blocker/7 major → R2 2 blocker/8
major, because the Escalation notes recorded reviewer fixes but **the task bodies were never
edited to match**. A re-run then STALLED again (R2 = 1 blocker/5 majors → **R3 REGRESSED to 3
blockers/5 majors**) because the autonomous planner kept trying to make Task 2D's lineage write
**ATOMIC via a new `engine.begin()` transaction** — which fights the store-agnostic,
AUTOCOMMIT-driven, `FakeModelArtifactStore`-tested flow tasks and over-invests for a lineage
table with **zero consumers today**. **The owner + orchestrator have now RESOLVED every residual
item (2026-07-22)** via seven directives folded directly into the task bodies below:

- **D-2D** — CUT Task 2D's transactional rearchitecture: the lineage helper is called
  NON-ATOMIC, log-loud, right after `store_artifact` returns (training after store+promote;
  onboarding after store only, promotion untouched). `engine.begin()` plumbing + rollback case
  (g) deleted. YAGNI: upgrade to a real transaction only if Flow 9 (out of scope) needs a hard
  SLA. (Resolves 2 blockers + 1 over-engineering major.)
- **D-0A** — `store_basin` is the single atomic creation path (basin + `version=1` in one
  data-modifying CTE, atomic under AUTOCOMMIT), gains optional `package_id`/`Basin.package_id`,
  called by both onboarding and Task 2A; + a failure-in-second-write regression. (1 blocker + 1
  major.)
- **D-UP** — add the real upstream static-features gate in `assemble_station_training_data`
  (fail-loud when static features are required but the basin row OR attributes are absent/empty,
  not only `basin_id IS None`). (1 major; backs grill-me (a).)
- **D-ORD** — one canonical write pipeline (idempotency branch → package provenance FIRST →
  basin projection+version via `store_basin` → §5a replace LAST); Task 2A/2C point back to it;
  `2C depends_on 2B` removed. (1 blocker.)
- **D-BAND** — DEFER the band §5a writer, but still VALIDATE `bands.gpkg` FULLY when present;
  band-undefer follow-up filed. (1 major.)
- **D-PROJ** — projection-with-history RATIFIED (not re-opened).
- **D-HK** — refreshed stale `model_artifacts` citations (`:695-739`), scoped the JSONB fix to
  `band_geometries` only, folded a README status refresh into Task 3B, collapsed the
  contract-mirroring per-field prose in Task 1A.

The historical resolution log below is retained; entries superseded by the 2026-07-22 rework are
annotated inline. (Genuinely complex multi-subsystem plan, not planner over-scoping.)

**Blockers (design holes) — RESOLVED:**
1. **Non-package basin inserts get no `basin_versions` row.** Onboarding still inserts basins via
   `basin_store.store_basin` (`store/basin_store.py:43`), which writes no version history; Task 2D
   lineage then needs `stations.basin_id` → a current `basin_versions` row.
   **RESOLVED →** Task 0A scope item 7 + the "Ongoing non-package basin inserts" bullet under Legacy
   backfill: `store_basin` now creates a `version=1, package_id=NULL` current row on every insert; +
   a post-migration onboarding-insert regression in Task 0A Verification.
2. **Package-provenance FK inserted too late.** Immediate FKs to `basin_static_packages` written before
   that row exists.
   **RESOLVED →** the canonical write pipeline (Versioned basin state) makes package provenance
   canonical step 2 (INSERT `basin_static_packages` FIRST, before any FK-referencing row); Task 2A
   and Task 2C both point back to it; Task 2A adds an FK-order negative test.

**Majors — clear fixes — RESOLVED:**
- Band §5a rows: table PK is `(station_id, gateway_hru_name, name)`, NOT station+band_id → a band
  rename would accumulate stale rows.
  **RESOLVED (2026-07-22, 2nd Codex pass) by DEFERRAL, not by building the writer →** band §5a
  rows have **zero v1 consumers** (Recap v1 is basin-average-only, `081:213`), so 120 persists
  `bands.gpkg` only as `basins.band_geometries` JSONB and defers the `elevation_band` §5a writer +
  its delete-then-insert idempotency + rename regression to the future plan that undefers banding
  in 082's resolver (contract §12 `04:697-712` guarantees a lossless re-import). **Updated by
  D-BAND (2026-07-22):** only the §5a WRITER is deferred — Task 1A still validates `bands.gpkg`
  FULLY (all required columns/types/parent-refs per `04:253-271`) when the file is present, since
  contract validation is not deferrable. The basin_average §5a delete-then-insert replace (its own
  major finding) stays.
- Package schema validation incomplete: validate ALL required gpkg/report fields (warnings/errors
  arrays, display_name, outlet coords, delineation_method, gauge_id, lat/lon, band bounds).
  **RESOLVED →** Task 1A "Required GeoPackage columns" bullet + the extended `validation_report.json`
  bullet (`warnings`/`errors` at `04:574-575`) + negative fixtures in Task 1A Verification.
- Station matching must be **(network, code)-scoped** (`station_store.py:79`) — not code alone.
  **RESOLVED →** Task 1B "Station identity is network-scoped" bullet + a cross-network negative test in
  Task 1B Verification.
- Flow-6 lineage must thread the **trained subset** (`GroupTrainingData.station_ids` after skips —
  `training_data.py:333,348`), NOT `TrainingUnit.station_ids` (full group membership).
  **RESOLVED →** Task 2D wiring bullet: the helper is called from `flows/train_models.py:413` using
  `data.station_ids` (in scope at `:358`), NOT the `TrainingUnit` the `_store_artifact_task` receives.
- **Task 2D simplification:** do NOT widen the cross-cutting `ModelArtifactStore.store_artifact`
  Protocol (3 impls) with a `trained_station_ids` kwarg + basin resolution. Instead a standalone
  `record_artifact_basin_lineage(store_or_conn, artifact_id, trained_station_ids)` helper.
  **RESOLVED →** Task 2D fully rewritten to the standalone helper (`store_artifact`/Protocol/store/fakes
  left untouched); Task 3B, the Ownership table, "Settled owner decisions", and References all updated
  to drop the kwarg. **(Called NON-ATOMIC right after `store_artifact` returns — see D-2D below; NOT
  inside a new transaction.)**
- **(2nd Codex pass, BLOCKER) The "same connection, commits atomically" claim was false** —
  production flow stores run on an AUTOCOMMIT connection (`flows/_db.py:78`) and
  `store_and_promote_artifact` receives a `ModelArtifactStore`, not a `sa.Connection`
  (`services/training.py:82-94`); the store's `_conn` is private (`model_artifact_store.py:23`).
  **SUPERSEDED by D-2D (2026-07-22).** The R2→R3 re-run tried to fix this by opening a dedicated
  `engine.begin()` transaction wrapping artifact insert + promotion + lineage — and that ATOMIC
  rearchitecture is what caused the R3 regression (3 blockers), because it breaks the
  store-agnostic, `FakeModelArtifactStore`-driven flow tasks (no engine in scope). **The
  owner+orchestrator resolution CUTS the transaction:** the helper is called NON-ATOMIC, log-loud,
  right after `store_artifact` returns — matching the pre-existing (already non-atomic under
  AUTOCOMMIT) store+promote relationship — with a YAGNI note to add a real transaction only if
  Flow 9 (out of scope) later needs a hard SLA. The rollback regression (former case (g)) is
  DELETED. Helper param stays `Collection[StationId]` (call sites pass the `tuple`
  `GroupTrainingData.station_ids`).
- **(2nd Codex pass, majors) RESOLVED inline:** (i) Task 1A `basins.gpkg` required columns now
  include `gauge_id`/`latitude`/`longitude` + the `latitude==outlet_lat`/`longitude==outlet_lon`
  equality checks (`04:199-203`); (ii) checksum source/file-set defined canonically (producer-declared
  payload set = `manifest.checksums` keys, excluding self-referential `manifest.json`/`checksums.sha256`;
  fixture `…/manifest.json:40-46`); (iii) migration/backfill DATA gate moved to
  `tests/integration/db/` (cheap metadata/head checks stay in `tests/unit/db`); (iv) `gateway_mapping`
  now explicitly sourced from the in-memory Task 1B structure (not a DB read-back), resolving the
  append-only-vs-insert-order contradiction; (v) stale citations refreshed (`historical_forcing`
  `:612-669`, `onboard_model.py:814` store call, `04:574-575` warnings/errors).
- (minor) `store_artifact` returns `tuple[ArtifactId, str]`, not `ArtifactId`.
  **RESOLVED →** Task 3B now fixes the stale spec return type at `types-and-protocols.md:2398`; the
  spec-section citation corrected to `:2383` (`:2306-2318` was `WeatherForecastStore`).

**Owner decisions (grill-me) — RESOLVED 2026-07-22 (owner):**
- (a) **Trained station with an unresolvable basin at lineage-write time → SPLIT by kind.**
  **`basin_id IS NULL` → skip the lineage row + log at INFO** (no WARNING — this is a legitimate,
  common state: a model declaring no static features can train on a basin-less station, and it is
  safe by construction because `assemble_station_training_data` fails-loud UPSTREAM
  (`training_data.py:213,228`, extended by D-UP's prerequisite subtask to also fail when the basin
  row/attributes are absent/empty, not only `basin_id IS None`) whenever a model *requires* static
  features but the basin/attributes are absent; so a NULL basin reaching Task 2D provably means
  static features were not required and
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

**All three owner decisions are now resolved, AND every blocker/major is now folded into the task
bodies (this 2026-07-22 rework — not merely recorded in this section).** Grill-me (a)'s NULL-vs-dangling
split is written into Task 2D Scope + Verification cases (e)/(f); (b) is at the Correction-UX bullet;
(c)'s follow-up is filed under §Change log. The extractor's full package has **landed and its output was
tested (HRU 12300, 2026-07-22)**, so 120's real-package run is no longer gated. The plan remains
**Status: DRAFT** pending the owner's READY call; the design holes the escalation named are closed in
the prose an implementer builds from.
