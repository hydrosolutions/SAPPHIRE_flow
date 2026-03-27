---
status: DRAFT
created: 2026-03-27
scope: types + DB schema + store + config + Flow 0 step
depends_on: []  # Independent — can land whenever parameter extensibility is needed
---

# 009 — Parameter Extensibility

## Problem

Parameters are hardcoded: 10 canonical names seeded in migration 0001, `ParameterDomain` enum
limited to `RIVER | WEATHER`, `ParameterStore` is read-only, and `metadata.py` has a CHECK
constraint restricting `parameter_domain` to `('river', 'weather')`. Adding a new parameter
(e.g. `water_temperature` for water quality monitoring, `groundwater_level` for boreholes)
requires code changes in 3+ places.

The architecture doc (§`parameters` table, §Flow 0 step 0.6) and spec (`ParameterDomain`,
`ParameterStore.register()`) already describe the target design. This plan implements it.

---

## Changes

### Phase 1 — Type + Protocol Changes

#### 1A. Extend `ParameterDomain` enum

`src/sapphire_flow/types/enums.py`: Add `WATER_QUALITY`, `GROUNDWATER`, `SOIL` per spec.

#### 1B. Add `ParameterStore.register()` to protocol

`src/sapphire_flow/protocols/stores.py`: Add `register(self, definition: ParameterDefinition) -> None`
per spec. Idempotent upsert semantics.

### Phase 2 — DB Schema

#### 2A. Migration: drop CHECK constraint on `parameter_domain`

Remove `parameter_domain IN ('river', 'weather')` CHECK constraint from the `parameters` table.
The column remains `TEXT NOT NULL` — validation moves to application layer (structured warning
for unknown domains, not rejection).

#### 2B. Update `metadata.py`

Remove the `sa.CheckConstraint(...)` on the `parameter_domain` column.

### Phase 3 — Store Implementation

#### 3A. `PgParameterStore.register()`

Implement as `INSERT ... ON CONFLICT (name) DO UPDATE SET display_name = ..., unit = ...,
aggregation_method = ..., parameter_domain = ...`. Does not update `created_at`.

#### 3B. `FakeParameterStore.register()`

In-memory equivalent: upsert into the internal dict/list.

### Phase 4 — Config Loading + Flow 0

#### 4A. Config schema for `[[parameters]]`

Add a `ParameterConfig` Pydantic model (boundary type) and parse `[[parameters]]` sections
from deployment config TOML. Fields: `name`, `display_name`, `unit`, `parameter_domain`,
`aggregation_method`.

#### 4B. Flow 0 step 0.6

Implement the parameter registration step: read `[[parameters]]` from config, validate
each entry, call `ParameterStore.register()`. Log structured warning (`known_domain=false`)
for parameter domains not in `ParameterDomain` enum.

### Phase 5 — Tests

- Unit test: `ParameterDomain` enum has all 5 values
- Unit test: `PgParameterStore.register()` upsert semantics (insert new, update existing)
- Unit test: `FakeParameterStore.register()` mirrors Pg behavior
- Unit test: config parsing accepts `[[parameters]]` sections
- Unit test: Flow 0 step 0.6 registers parameters and warns on unknown domains
- Integration test: round-trip register → fetch_by_name

### Phase 6 — Deferred (noted, not implemented)

These are downstream changes enabled by parameter extensibility but requiring their own plans:

- **Widen `StationThreshold.parameter`** from `Literal["discharge", "water_level"]` to `str`
  (also `ExceedanceResult.parameter`) — prerequisite for water quality thresholds
- **Extend `StationKind`** for boreholes, lysimeters — prerequisite for groundwater/soil stations
- **New forcing sources** for non-river models (soil moisture, recharge)

---

## File-Level Change Summary

| File | Change type | Phase |
|---|---|---|
| `src/sapphire_flow/types/enums.py` | Add 3 `ParameterDomain` values | 1A |
| `src/sapphire_flow/protocols/stores.py` | Add `ParameterStore.register()` | 1B |
| `alembic/versions/NNNN_drop_parameter_domain_check.py` | Drop CHECK constraint | 2A |
| `src/sapphire_flow/db/metadata.py` | Remove CHECK constraint | 2B |
| `src/sapphire_flow/store/parameter_store.py` | Implement `register()` | 3A |
| `tests/fakes/fake_stores.py` | `FakeParameterStore.register()` | 3B |
| `src/sapphire_flow/config/deployment.py` | `ParameterConfig` model, parse `[[parameters]]` | 4A |
| `src/sapphire_flow/flows/deploy.py` (or equivalent Flow 0) | Step 0.6 implementation | 4B |
| `tests/` | New tests per Phase 5 | 5 |

---

## Open Items

1. **API endpoint for parameter registration** — The architecture doc notes this may be needed
   eventually. Not in this plan — config-driven is sufficient for now. When needed, add
   `POST /api/v1/parameters` that calls `ParameterStore.register()` with the same validation.

2. **Migration of existing CHECK constraint** — If production DBs already have data, the
   migration must `DROP CONSTRAINT` without data loss. This is safe (removing a constraint
   never fails on existing data).
