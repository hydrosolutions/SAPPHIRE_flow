# Plan 040 — Hindcast Deduplication Constraint

**Status**: DRAFT — grill-me COMPLETE (2026-07-10): conflict action = **ON CONFLICT DO UPDATE, full-replace** (header fields + value rows; return the existing header id). Depends-on Plan 038 is now MERGED (#71). Next: WF1 plan-review → build.
**Phase**: Cross-cutting (schema + store)
**Depends on**: Plan 038 (store write atomicity) — MERGED (#71)

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

### Upsert in `store_hindcast` — ON CONFLICT DO UPDATE, full-replace (grill-me 2026-07-10)

**Decision (owner):** a duplicate (same natural key) **overwrites** the existing
hindcast — header fields AND the value-row payload — so the latest run's data
fully wins (no stale values linger). This is the resolved answer to the former
open question (§ Open questions).

**Note (post-038):** Plan 038 reworked the store to an injectable transaction —
use `self._begin()` (NOT `self._engine.begin()`). Both inserts + the value delete
run inside the one `self._begin()` transaction, so the replace is atomic.

**Header — `on_conflict_do_update` + `RETURNING id`.** The RETURNING id is the id
**of the row actually in the DB**: the freshly-inserted `hindcast.id` on a clean
insert, or the **EXISTING row's id** on a conflict/update (which DIFFERS from the
new `hindcast.id`). Use that id for the values and return it.

**Values — full replace keyed to the returned id.** Because on a conflict the
header id is the existing id (not the new one), and a plain values INSERT would
leave the prior run's value rows in place, the values are REPLACED: `DELETE FROM
hindcast_values WHERE hindcast_forecast_id = <returned id>`, then INSERT the new
rows keyed to `<returned id>`. On a clean insert the DELETE is a harmless no-op.

```python
def store_hindcast(self, hindcast: HindcastForecast) -> HindcastForecastId:
    with self._begin() as txn:                       # Plan 038 injectable txn
        header_id = txn.execute(
            pg_insert(hindcast_forecasts)
            .values(id=hindcast.id, ...)
            .on_conflict_do_update(
                index_elements=[
                    "station_id", "model_id", "hindcast_step",
                    "parameter", "hindcast_run_id",
                ],
                set_={  # every mutable NON-key header field; NOT the key or id
                    "model_artifact_id": ...,
                    "units": ...,
                    "representation": ...,
                    "forcing_type": ...,
                    "created_at": ...,
                },
            )
            .returning(hindcast_forecasts.c.id)
        ).scalar_one()
        # Full-replace the payload keyed to the row actually in the DB.
        txn.execute(
            sa.delete(hindcast_values).where(
                hindcast_values.c.hindcast_forecast_id == header_id
            )
        )
        rows = [{..., "hindcast_forecast_id": header_id, ...} for ...]
        if rows:
            txn.execute(sa.insert(hindcast_values), rows)
    return header_id
```

**`set_` columns:** every mutable non-key header field (`model_artifact_id`,
`units`, `representation`, `forcing_type`, `created_at`) is refreshed to the new
run's values; the natural-key columns and `id` are NOT updated (`id` stays the
existing row's — hence returning `header_id`, not `hindcast.id`). The new
`ix_hindcast_values_forecast_id` index (below) also makes the per-header value
DELETE efficient.

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

### Step 2 — Update `store_hindcast` to upsert (DO UPDATE full-replace)

**File**: `hindcast_store.py`

Change the header insert to `pg_insert(...).on_conflict_do_update(index_elements=
[natural key], set_={mutable non-key fields}).returning(id)`, take the RETURNING
id (existing row's id on conflict, new id on insert), then DELETE the existing
`hindcast_values` for that id and INSERT the new rows keyed to it, and return that
id. Uses Plan 038's injectable `self._begin()` (already merged) — the header
upsert, values DELETE, and values INSERT are all inside the one transaction, so
the replace is atomic. See the design section for the code shape.

### Step 3 — Update schema definition

**File**: `metadata.py`

Add the unique index and the `hindcast_values` index to the SQLAlchemy table
definitions so that `metadata.create_all()` and future Alembic autogenerate
remain in sync.

### Step 4 — Tests

1. **Dedup (same-run idempotent retry):** insert the SAME hindcast twice with the
   same `run_id` and identical data — verify exactly one header row exists, no
   `IntegrityError`, and the method returns the SAME id both times.
2. **DO UPDATE full-replace (the load-bearing test):** insert a hindcast, then
   re-insert with the SAME natural key but DIFFERENT payload (different value
   rows AND a changed mutable header field, e.g. `model_artifact_id`). Verify:
   (a) still exactly one header row; (b) the header's mutable fields now reflect
   the SECOND write; (c) the value rows are the SECOND write's (the first write's
   values are GONE — no stale rows, count matches the new payload); (d) the method
   returns the EXISTING header's id (not the second call's `hindcast.id`).
3. **Distinct runs preserved:** insert two hindcasts with different `run_id`s for
   the same `(station_id, model_id, hindcast_step, parameter)` — verify BOTH are
   stored (legitimate re-runs, no conflict).
4. Verify `fetch_hindcasts` returns correct results with the new index (no
   behavioral change, just performance) — and de-duplicated (one row per key).

### Step 5 — Schema checklist (preventive)

**File**: `docs/conventions.md` (new section)

Add a "Schema constraint checklist" to conventions:
- Every table with a natural key must have a unique constraint
- Every FK column used in WHERE/JOIN must have an index
- When adding a constraint to one table in a header+values pair, check the
  sibling table

## Open questions — RESOLVED (grill-me 2026-07-10)

1. ~~DO NOTHING vs DO UPDATE on conflict?~~ **RESOLVED: ON CONFLICT DO UPDATE,
   full-replace.** A same-natural-key re-insert overwrites the existing hindcast —
   the header's mutable non-key fields are refreshed AND the value-row payload is
   replaced (DELETE old + INSERT new, keyed to the existing header id), so the
   latest write fully wins and no stale value rows linger. The method returns the
   EXISTING header id. Rationale: the owner wants a same-run re-insert to be a
   true refresh, not a silent skip. (An idempotent retry with identical data
   converges to the same state; a re-insert with corrected data overwrites.) See
   the design § "Upsert in store_hindcast".
