# Plan 040 — Hindcast Deduplication Constraint

**Status**: DRAFT
**Phase**: Cross-cutting (schema + store)
**Depends on**: Plan 038 (store write atomicity)

## Context

### The problem

The `hindcast_forecasts` table has no unique constraint. Two non-unique indexes
exist (`ix_hindcast_forecasts_station_model_step` and
`ix_hindcast_forecasts_station_model_step_param`) but neither prevents
duplicate rows for the same `(station_id, model_id, hindcast_step, parameter,
hindcast_run_id)` combination.

This means:
1. A Prefect task retry after a successful commit silently inserts a duplicate
   header (with a new UUID), producing two identical hindcasts.
2. A manual re-run of the hindcast flow for the same period doubles all data.
3. `fetch_hindcasts` returns both duplicates, inflating skill computation inputs.

By contrast, the `forecasts` table has a partial unique index
(`uq_forecasts_station_model_issued_param`) that prevents this exact class of
duplicates. The asymmetry is accidental.

### How this happened

The stores were implemented in Phase 2 (Batch D, commit `2df0629`, 2026-03-24).
The commit message describes hindcast as "mirroring ForecastStore pattern."
Two days later, the deduplication constraints pass (commit `62d9924`) added
unique constraints to `observations`, `weather_forecasts`, `skill_scores`, and
`forecasts` — but skipped `hindcast_forecasts`.

The likely reasoning was that `hindcast_run_id` differentiates re-runs, so the
same `(station_id, model_id, hindcast_step, parameter)` tuple can legitimately
appear multiple times with different `hindcast_run_id` values. This is correct
— but there is no constraint preventing duplicates *within the same run*.

The gap went undetected because:
1. **No test covers same-run-id duplicates**: The `test_run_id_filter`
   integration test verifies that two hindcasts with different `run_id`s are
   stored as separate rows (by design), but never tests same-`run_id` insertion.
2. **`store_hindcast` uses plain `pg_insert` with no `ON CONFLICT` clause**:
   Unlike `store_group` (which uses `ON CONFLICT DO UPDATE`/`DO NOTHING`),
   the hindcast header insert will silently succeed on duplicate data.
3. **Sequential execution masked the issue**: The hindcast flow runs tasks
   sequentially (no `task.map()` fan-out for store calls), so concurrent
   duplicate insertion has not occurred in practice.

### Systemic lessons

Three process gaps allowed these issues:

1. **Late wiring**: Stores were written and tested with transactional test
   connections. The production `AUTOCOMMIT` connection was added three weeks
   later in Plan 036, without re-validating store methods against the new
   connection semantics. Fix: production connection mode should be tested.

2. **Table-by-table constraint pass**: The dedup constraints commit added
   unique indexes to four tables and missed one. Without a schema checklist
   ("every table with a natural key gets a unique constraint"), individual
   tables slip through. Fix: add a schema review checklist to the conventions.

3. **Copy-paste store design**: Hindcast "mirrored" forecast, but the
   constraint that makes forecast safe was added in a separate commit that
   didn't mirror back to hindcast. Fix: when adding a constraint to one
   table in a pair, check the sibling table.

### Blast radius of duplicates

- **Skill computation**: `fetch_hindcasts` returns all rows matching
  `(station_id, model_id, hindcast_step range)`. Duplicate headers mean
  duplicate ensembles in the skill metric calculation, inflating sample size
  and biasing CRPS/rank histogram results.
- **Storage**: Each duplicate header carries a full set of `hindcast_values`
  rows. At ~120 lead-time steps × 21 ensemble members = ~2520 value rows per
  hindcast, duplicates accumulate significant storage.
- **Fetch crash interaction (Plan 038)**: If Plan 038's atomicity fix is in
  place and a retry creates a duplicate, both copies are complete (no orphans).
  The problem is purely data quality, not data integrity.

### Secondary gap: missing `hindcast_values` index

`forecast_values` has `ix_forecast_values_forecast_valid_time` for efficient
lookups by `(forecast_id, valid_time)`. `hindcast_values` has **no index**
beyond the primary key. `fetch_hindcasts` queries `hindcast_values` with
`WHERE hindcast_forecast_id IN (...)`, which does a sequential scan without
an index on `hindcast_forecast_id`. At scale (1000 stations × 365 days ×
2520 rows/hindcast ≈ 920M rows), this becomes a serious performance issue.

## Design

### Unique constraint on `hindcast_forecasts`

Add a unique index on `(station_id, model_id, hindcast_step, parameter,
hindcast_run_id)`. This mirrors the forecast table's
`uq_forecasts_station_model_issued_param` but includes `hindcast_run_id`
(since different runs are legitimately distinct).

```sql
CREATE UNIQUE INDEX uq_hindcast_forecasts_station_model_step_param_run
ON hindcast_forecasts (station_id, model_id, hindcast_step, parameter, hindcast_run_id);
```

No partial-index exclusion is needed (hindcast has no `status` lifecycle like
forecast's `superseded` state).

### Upsert in `store_hindcast`

Change the header insert from plain `pg_insert` to `pg_insert(...).on_conflict_do_nothing()`.
With the unique constraint in place, a retry that attempts to re-insert the
same hindcast is silently skipped. The values insert is already inside the
`engine.begin()` transaction (Plan 038), so if the header is skipped, the
values are also skipped (the `if rows:` block still fires, but the FK
constraint on `hindcast_forecast_id` would fail since the header was not
inserted — this needs careful handling).

**Preferred approach**: Use `ON CONFLICT DO NOTHING` with a `RETURNING id`
clause. If the returned result is empty (conflict occurred, row skipped),
skip the values insert entirely and return the existing hindcast's ID:

```python
def store_hindcast(self, hindcast: HindcastForecast) -> HindcastForecastId:
    with self._engine.begin() as txn:
        result = txn.execute(
            pg_insert(hindcast_forecasts)
            .values(...)
            .on_conflict_do_nothing(
                index_elements=[
                    "station_id", "model_id", "hindcast_step",
                    "parameter", "hindcast_run_id",
                ]
            )
            .returning(hindcast_forecasts.c.id)
        )
        inserted = result.scalar_one_or_none()
        if inserted is None:
            # Duplicate — already stored in a previous attempt
            return hindcast.id

        rows = [...]
        if rows:
            txn.execute(sa.insert(hindcast_values), rows)
    return hindcast.id
```

### Index on `hindcast_values`

Add a covering index for the fetch pattern:

```sql
CREATE INDEX ix_hindcast_values_forecast_id
ON hindcast_values (hindcast_forecast_id);
```

This mirrors `ix_forecast_values_forecast_valid_time` on the forecast side.

### Existing duplicate cleanup

Before adding the unique constraint, existing duplicates must be removed
(the constraint creation will fail if duplicates exist). A data migration
identifies and deletes duplicate headers, keeping the earliest `created_at`
per `(station_id, model_id, hindcast_step, parameter, hindcast_run_id)` group:

```sql
DELETE FROM hindcast_values
WHERE hindcast_forecast_id IN (
    SELECT id FROM hindcast_forecasts hf
    WHERE EXISTS (
        SELECT 1 FROM hindcast_forecasts hf2
        WHERE hf2.station_id = hf.station_id
          AND hf2.model_id = hf.model_id
          AND hf2.hindcast_step = hf.hindcast_step
          AND hf2.parameter = hf.parameter
          AND hf2.hindcast_run_id = hf.hindcast_run_id
          AND hf2.created_at < hf.created_at
    )
);

DELETE FROM hindcast_forecasts hf
WHERE EXISTS (
    SELECT 1 FROM hindcast_forecasts hf2
    WHERE hf2.station_id = hf.station_id
      AND hf2.model_id = hf.model_id
      AND hf2.hindcast_step = hf.hindcast_step
      AND hf2.parameter = hf.parameter
      AND hf2.hindcast_run_id = hf.hindcast_run_id
      AND hf2.created_at < hf.created_at
);
```

Note: in production there may be zero duplicates (the flow has only run
sequentially so far). The migration should log how many rows were deleted.

## Tasks

### Step 1 — Alembic migration: dedup + unique constraint + index

**File**: new Alembic migration

1. Delete duplicate `hindcast_values` rows (cascading from duplicate headers)
2. Delete duplicate `hindcast_forecasts` rows (keep earliest `created_at`)
3. `CREATE UNIQUE INDEX uq_hindcast_forecasts_station_model_step_param_run`
4. `CREATE INDEX ix_hindcast_values_forecast_id ON hindcast_values (hindcast_forecast_id)`

Log row counts before and after. Migration must be idempotent (check index
existence before creating).

### Step 2 — Update `store_hindcast` to upsert

**File**: `hindcast_store.py`

Change the header insert to `pg_insert(...).on_conflict_do_nothing().returning(...)`.
Skip values insert if the header was a duplicate. This depends on Plan 038's
`engine.begin()` wrapping being in place.

### Step 3 — Update schema definition

**File**: `metadata.py`

Add the unique index and the `hindcast_values` index to the SQLAlchemy table
definitions so that `metadata.create_all()` and future Alembic autogenerate
remain in sync.

### Step 4 — Tests

1. Integration test: insert the same hindcast twice with the same `run_id` —
   verify only one header row exists and the method returns successfully
   (no `IntegrityError`).
2. Integration test: insert two hindcasts with different `run_id`s for the
   same `(station_id, model_id, hindcast_step, parameter)` — verify both
   are stored (legitimate re-runs).
3. Verify `fetch_hindcasts` returns correct results with the new index
   (no behavioral change, just performance).

### Step 5 — Schema checklist (preventive)

**File**: `docs/conventions.md` (new section)

Add a "Schema constraint checklist" to conventions:
- Every table with a natural key must have a unique constraint
- Every FK column used in WHERE/JOIN must have an index
- When adding a constraint to one table in a header+values pair, check the
  sibling table

## Open questions

1. Should the `ON CONFLICT DO NOTHING` approach also update `created_at` or
   `qc_status` on conflict (i.e., use `ON CONFLICT DO UPDATE` instead)?
   Current design says no — if the data is identical, there is nothing to
   update. If the caller needs to overwrite, they should delete first.
