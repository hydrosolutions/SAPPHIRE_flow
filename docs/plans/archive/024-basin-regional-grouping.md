---
status: DONE
created: 2026-04-10
scope: Add regional_basin column to basins table, enforce 1:1 station-basin relationship
depends_on: []
---

# 024 — Basin Regional Grouping and 1:1 Constraint

## Problem

The `basins` table stores per-station catchment polygons — the drainage area
upstream of each gauge. In hydrology, "basin" typically refers to a larger
geographic unit (e.g., "Karnali basin") containing many gauging stations. This
terminology overlap causes confusion in external-facing documents and
discussions with deployment partners (DHM).

Two concrete issues:

1. **Missing regional grouping**: `architecture-context.md` (line 2633)
   documents a `regional_basin: TEXT NULL` column for display grouping (e.g.,
   "Karnali", "Gandaki"), but this column was never implemented in the schema,
   dataclass, or store. `v0-scope.md` §C says `basins — as designed`, so the
   omission is an implementation gap, not a deliberate deferral —
   `band_geometries` (also documented only in the v1 north-star architecture)
   was already implemented under "as designed", so the same interpretation
   applies to `regional_basin`. Nepal deployment needs this for organising
   stations in the dashboard and API responses by river system. For Swiss v0
   the column will be `NULL` for most stations — the primary motivation is
   Nepal v1 readiness, but the column is cheap and "as designed" supports
   adding it now.

2. **No 1:1 enforcement**: Each river station should have its own catchment
   polygon. The schema allows multiple stations to reference the same `basins`
   row (`stations.basin_id` FK with no UNIQUE constraint), but this is never
   correct in practice — even nested catchments on the same river (e.g., two
   gauges on the Koshi, one upstream of the other) have distinct catchment
   geometries and therefore distinct `basins` rows. A UNIQUE constraint
   prevents accidental data corruption during onboarding. Note: this is a
   **new invariant** not previously documented in architecture-context.md or
   v0-scope.md — the plan formalises an implicit assumption.

## Design

### Add `regional_basin` column

A `TEXT NULL` column on the `basins` table. Populated during station
onboarding — either from the data source (CAMELS-CH, DHM metadata) or
manually. Used for display and filtering only; does not affect modelling.

Examples:
- Switzerland: `"Aare"`, `"Rhein"`, `"Rhône"`, `NULL` (optional)
- Nepal: `"Karnali"`, `"Gandaki"`, `"Koshi"`, `"Bagmati"`, etc.

Not an FK to a separate table — a simple text label. If structured basin
hierarchies are needed in v1+, a `regions` table can be introduced and
`regional_basin` migrated to an FK. For now, a text column is sufficient and
avoids over-engineering.

Values should use official authority naming where possible (Swiss Federal
Office nomenclature for v0; DHM's official basin names for Nepal v1) to
support future interoperability with WMO-49 network reporting.

**Distinction from `station_groups`**: `regional_basin` is a geographic
display label on the catchment — one value per basin row, does not affect
modelling. `station_groups` are named sets of stations grouped for ML model
training (e.g., `swiss_alpine`) — many-to-many, directly affects which
stations contribute to group-scoped model training. A station in
`regional_basin = "Koshi"` may belong to station group `nepal_terai` for
ML purposes — the two concepts are orthogonal.

### Enforce 1:1 station-to-basin relationship

Add a partial UNIQUE constraint on `stations.basin_id WHERE basin_id IS NOT
NULL`. Weather stations have `basin_id = NULL` (no catchment) — the constraint
only applies to stations that reference a basin.

This matches the existing data model intention: each river station has its own
catchment polygon. The constraint catches bugs where onboarding accidentally
assigns two stations to the same basin row.

### What does NOT change

- **Table name**: `basins` stays as `basins`. No rename to `catchments`.
- **`basin_average` spatial type**: This is a data extraction concept, not an
  entity name. Unchanged.
- **`attributes` JSONB**: Continues to store static catchment attributes. No
  structural change.
- **`BasinId`, `Basin`, `BasinStore` naming**: Internal code keeps current
  names. The team understands that "basin" means "catchment" internally.
- **Relationship direction**: `stations.basin_id` FK to `basins.id` stays as
  is. The UNIQUE constraint enforces 1:1 without changing the FK direction.

## Scope

Three steps. No dependencies on other plans.

### Step 1: Alembic migration

**Create**:
- `alembic/versions/0023_add_regional_basin_and_unique_constraint.py`
  - `op.add_column("basins", sa.Column("regional_basin", sa.Text, nullable=True))`
  - `op.create_index("uq_stations_basin_id", "stations", ["basin_id"], unique=True, postgresql_where=sa.text("basin_id IS NOT NULL"))`

The nullable column is unconditionally additive. The partial UNIQUE index is
additive only if the data precondition holds (no duplicate non-NULL `basin_id`
values) — the mandatory pre-migration check guards this. Both changes are
backwards-compatible per `cicd.md` migration conventions.

**Downgrade**:
  - `op.drop_index("uq_stations_basin_id", "stations")`
  - `op.drop_column("basins", "regional_basin")`

**Implementation note**: Use `op.create_index()` with `postgresql_where`, not
raw DDL — consistent with existing partial indexes in the codebase (e.g.,
`uq_forecasts_station_model_issued` in migration 0008).

**Pre-migration check** (mandatory): The upgrade function must include a
pre-flight assertion — an `op.execute()` query that selects duplicate
non-NULL `basin_id` values from `stations` and raises with a clear error
message before attempting the index creation. A bare `CREATE UNIQUE INDEX`
that fails on a live DB is not backwards-safe per `cicd.md` migration
conventions.

**Verification**: `uv run alembic upgrade head` on a fresh and existing DB.
Requires `DATABASE_URL_DIRECT` (per `cicd.md` — migrations bypass PgBouncer).

### Step 2: Type, metadata, and store updates

**Modify**:
- `src/sapphire_flow/db/metadata.py` — add
  `sa.Column("regional_basin", sa.Text, nullable=True)` to `basins` table
- `src/sapphire_flow/types/basin.py` — add `regional_basin: str | None = None`
  to `Basin` dataclass. **The `= None` default is mandatory** — without it,
  all 6 existing `Basin(...)` call sites break with `TypeError`. The default
  ensures backward compatibility: existing code that omits `regional_basin`
  gets `None` silently.
- `src/sapphire_flow/store/basin_store.py` — add
  `regional_basin=row["regional_basin"]` in `_row_to_domain()` and
  `regional_basin=basin.regional_basin` in `store_basin()` values dict.
  Pre-migration rows return `None` for the new column, which is safe.
- `tests/integration/store/test_basin_store.py` — test round-trip of
  `regional_basin` field; test that inserting two stations with the same
  non-NULL `basin_id` raises `IntegrityError`

**No changes needed**:
- `tests/fakes/fake_stores.py` — `FakeBasinStore` stores `Basin` objects
  as-is, does not construct them. No change required.
- `src/sapphire_flow/adapters/camelsch_adapter.py` — constructs `Basin(...)`
  at line 229 but omits `regional_basin`. With `= None` default, this is safe
  — Swiss v0 basins get `regional_basin=None` automatically.

**Verification**: `uv run pytest tests/integration/store/test_basin_store.py tests/unit/ -v`

### Step 3: Doc updates

**Modify**:
- `docs/spec/database-schema.md` — add `regional_basin` column to both v0 and
  v1 `basins` entity in the Mermaid ER diagrams
- `docs/spec/types-and-protocols.md` — add `regional_basin: str | None = None`
  to `Basin` type definition
- `docs/handover/data-model.md` — add `regional_basin` to Station domain ER
  diagram; update the domain description to explain the grouping purpose
- `docs/architecture-context.md` — add the UNIQUE partial index
  `uq_stations_basin_id` to the `stations` table indexes paragraph
  (line ~2577); note `regional_basin` as a basin attribute populated during
  onboarding in the Flow 5 step 5.2 context (basin creation), not step 5.1
  (station minimum fields). The `regional_basin` column definition (line 2633)
  is already correct and needs no change.

**Verification**: Visual review.

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1",
      "tasks": ["1"],
      "parallel": false
    },
    {
      "id": "phase-2",
      "tasks": ["2", "3"],
      "parallel": true,
      "depends_on": ["phase-1"]
    }
  ]
}
```

Step 1 (migration) runs first — the DB column must exist before the store
can map it. Steps 2 (code) and 3 (docs) are independent and can run in
parallel after the migration.

## What this plan does NOT cover

- **Renaming `basins` to `catchments`**: Decided against — internal naming
  stays as is. External docs use "catchment" where appropriate.
- **CAMELS-CH adapter changes**: The adapter can populate `regional_basin`
  from CAMELS-CH metadata if a mapping exists, but this is optional and can
  be done separately. For Swiss v0, `regional_basin` will be `NULL` for most
  stations unless populated from config.
- **Nepal onboarding adapter**: DHM will provide regional basin names as part
  of station metadata. The onboarding flow already passes through all `Basin`
  fields — no flow-level changes needed.
- **API filtering by region**: Phase 9 concern. The column is ready for
  filtering when the API is built.
- **`regions` reference table**: If structured hierarchies are needed later
  (sub-basins, basin areas, etc.), a dedicated table can be introduced and
  `regional_basin` migrated to an FK. Not needed for v0 or v1.
