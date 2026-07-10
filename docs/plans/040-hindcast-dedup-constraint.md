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

### Why this went undetected (implementation-relevant only)

The one gap that shapes the test plan (Step 4): **no test covers same-`run_id`
duplicates**. The `test_run_id_filter` integration test verifies that two
hindcasts with *different* `run_id`s are stored as separate rows (by design) but
never tests same-`run_id` insertion, and `store_hindcast` uses a plain
`pg_insert` with no `ON CONFLICT` clause, so duplicate data silently succeeds.
(The broader post-mortem archaeology — commit history, the AUTOCOMMIT-vs-txn
timeline, the table-by-table constraint pass — was removed from this living plan;
its one durable outcome is the schema checklist in Step 5.)

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
hindcast_run_id, forcing_type)`. This mirrors the forecast table's
`uq_forecasts_station_model_issued_param` but includes `hindcast_run_id`
(since different runs are legitimately distinct) **and `forcing_type`**.

```sql
CREATE UNIQUE INDEX uq_hindcast_forecasts_station_model_step_param_run
ON hindcast_forecasts (station_id, model_id, hindcast_step, parameter, hindcast_run_id, forcing_type);
```

**Why `forcing_type` is a KEY column, not a `set_` column (reviewer blocker,
2026-07-10):** `forcing_type` is a legitimate part of the natural key, not a
mutable header field. The same run can produce two hindcasts that differ only
by forcing (`NWP_ARCHIVE` vs `REANALYSIS`) and both must persist as distinct
rows. This is a schema-supported, tested distinction:
`TestFetchWithForcingTypeFilter`
(`tests/integration/store/test_hindcast_store.py:225-281`) stores two hindcasts
with the SAME `(station_id, model_id, hindcast_step, parameter,
hindcast_run_id)` and DIFFERENT `forcing_type`, then asserts `len(all_results)
== 2`. A five-column key (without `forcing_type`) would treat these as a
conflict and upsert the second over the first — that test would fail and any
future run storing both forcings for the same step/run would lose one hindcast.
Therefore `forcing_type` is in the unique index and is **removed** from the
upsert `set_` (it can never change on a genuine conflict, since it is a key
column). The `forcing_type` CHECK constraint stays as-is
(`metadata.py:733-737`).

No partial-index exclusion is needed (hindcast has no `status` lifecycle like
forecast's `superseded` state).

### Upsert in `store_hindcast` — ON CONFLICT DO UPDATE, full-replace (grill-me 2026-07-10)

**Decision (owner):** a duplicate (same natural key — the SIX columns
`station_id, model_id, hindcast_step, parameter, hindcast_run_id,
forcing_type`) **overwrites** the existing hindcast — the mutable non-key
header fields (including `qc_status`/`qc_flags`) AND the value-row payload — so
the latest run's data fully wins (no stale values or stale QC linger). This is
the resolved answer to the former open question (§ Open questions).

**Note (post-038):** Plan 038 reworked the store to an injectable transaction —
use `self._begin()` (NOT `self._conn.execute()` or `self._conn.engine.begin()`)
— the real footgun is writing directly on `self._conn`, bypassing the injected
txn. Both inserts + the value delete run inside the one `self._begin()`
transaction, so the replace is atomic.

**Header — `on_conflict_do_update` + `RETURNING id`.** The RETURNING id is the id
**of the row actually in the DB**: the freshly-inserted `hindcast.id` on a clean
insert, or the **EXISTING row's id** on a conflict/update (which DIFFERS from the
new `hindcast.id`). Use that id for the values and return it.

**Values — full replace keyed to the returned id.** Because on a conflict the
header id is the existing id (not the new one), and a plain values INSERT would
leave the prior run's value rows in place, the values are REPLACED: `DELETE FROM
hindcast_values WHERE hindcast_forecast_id = <returned id>`, then INSERT the new
rows keyed to `<returned id>`. On a clean insert the DELETE is a harmless no-op.

**Structural requirement (reviewer blocker, 2026-07-10):** the `rows` list MUST
be built INSIDE the `with self._begin()` block, AFTER `header_id` is returned,
so each value row is keyed to `header_id` — NOT to `hindcast.id`. The current
code (`hindcast_store.py:50-64`) pre-builds `rows` with
`"hindcast_forecast_id": hindcast.id` before the transaction; on a conflict the
RETURNING id is the EXISTING row's id (differs from `hindcast.id`), so value
rows keyed to `hindcast.id` would violate the FK (no header with that id) or
orphan under the wrong header. The `rows` construction must therefore move
inside the txn and use `header_id`.

```python
def store_hindcast(self, hindcast: HindcastForecast) -> HindcastForecastId:
    with self._begin() as txn:                       # Plan 038 injectable txn
        header_id = txn.execute(
            pg_insert(hindcast_forecasts)
            .values(id=hindcast.id, ...)
            .on_conflict_do_update(
                index_elements=[  # the full natural key (includes forcing_type)
                    "station_id", "model_id", "hindcast_step",
                    "parameter", "hindcast_run_id", "forcing_type",
                ],
                set_={  # every mutable NON-key header field; NOT the key or id
                    "model_artifact_id": ...,
                    "units": ...,
                    "representation": ...,
                    "created_at": ...,
                    "qc_status": ...,   # mutable — a re-run may carry new QC verdict
                    "qc_flags": ...,    # mutable — refresh alongside qc_status
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
        # Build rows HERE, inside the txn, keyed to header_id (NOT hindcast.id).
        rows = [{..., "hindcast_forecast_id": header_id, ...} for ...]
        if rows:
            txn.execute(sa.insert(hindcast_values), rows)
    return header_id
```

**`set_` columns:** every mutable non-key header field is refreshed to the new
run's values. That is: `model_artifact_id`, `units`, `representation`,
`created_at`, **`qc_status`, and `qc_flags`**. The natural-key columns
(`station_id`, `model_id`, `hindcast_step`, `parameter`, `hindcast_run_id`,
`forcing_type`) and `id` are NOT updated — `forcing_type` is now a KEY column
(see above) and so is deliberately absent from `set_`; `id` stays the existing
row's (hence returning `header_id`, not `hindcast.id`).

`qc_status` and `qc_flags` are non-nullable header columns that
`store_hindcast` writes on every insert (`hindcast_store.py:80-89`;
`metadata.py:753-759`) and are mutable per-run
(`types/forecast.py:91-92`). They MUST be in `set_`: a re-run after a QC-rule
fix carries a corrected verdict, and the plan's "latest run fully wins"
guarantee requires those fields to be overwritten too — omitting them would
silently preserve stale QC state. The new `ix_hindcast_values_forecast_id`
index (below) also makes the per-header value DELETE efficient.

**Why `representation` is in `set_` (reviewer minor, 2026-07-10):**
`representation` (`MEMBERS` vs `QUANTILES`) is a mutable non-key field and is
kept in `set_` under the "latest run fully wins" full-replace semantics: if a
model is reconfigured between runs to emit a different representation for the
same `(model, parameter)`, the re-insert overwrites the header value AND the
value-row DELETE+INSERT re-materialises the payload consistently with the new
representation — so the `ck_hindcast_values_representation_xor` XOR CHECK
(`member_id` XOR `quantile`; `metadata.py:793-796`) holds because the old value
rows are gone before the new ones land. (In practice `representation` is fixed
per `(model, parameter)` and a conflict with a differing value is not expected;
keeping it in `set_` is a safe full-replace, not a key column. It is NOT part of
the natural key — see the six-column key above.)

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
(tie-broken by lowest `id`) per full-natural-key group `(station_id,
model_id, hindcast_step, parameter, hindcast_run_id, forcing_type)`.

**Retention-policy asymmetry (intentional — reviewer note, 2026-07-10):** this
one-time cleanup keeps the OLDEST row per group, whereas the ongoing upsert
(§ "Upsert in store_hindcast") keeps the NEWEST. This is deliberate, not a
contradiction: for pre-existing historical duplicates no version is
canonically "correct", so the migration conservatively preserves the
first-written row and drops the accidental copies; going forward, a re-run is
assumed to carry newer/corrected data, so the upsert lets the latest write win.
An implementer must NOT invert either direction to "match" the other.

**Tiebreaker (reviewer blocker, 2026-07-10):** the predicate uses a strict
total order `(created_at, id)`, not `created_at` alone. If two duplicate rows
share an identical `created_at` (possible with a frozen/deterministic clock in
tests, or two inserts landing in the same tick), a bare `hf2.created_at <
hf.created_at` matches neither row, both survive, and the subsequent `CREATE
UNIQUE INDEX` aborts with a duplicate-key error. Adding the `id` tiebreaker
(`id` is a UUID and unique) guarantees exactly one survivor per group.
`forcing_type` is now part of the natural key (see above), so it is added to the
group predicate — otherwise a legitimate two-forcing pair would be wrongly
collapsed by the cleanup.

```sql
DELETE FROM hindcast_values
WHERE hindcast_forecast_id IN (
    SELECT hf.id FROM hindcast_forecasts hf
    WHERE EXISTS (
        SELECT 1 FROM hindcast_forecasts hf2
        WHERE hf2.station_id = hf.station_id
          AND hf2.model_id = hf.model_id
          AND hf2.hindcast_step = hf.hindcast_step
          AND hf2.parameter = hf.parameter
          AND hf2.hindcast_run_id = hf.hindcast_run_id
          AND hf2.forcing_type = hf.forcing_type
          AND (hf2.created_at < hf.created_at
               OR (hf2.created_at = hf.created_at AND hf2.id < hf.id))
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
      AND hf2.forcing_type = hf.forcing_type
      AND (hf2.created_at < hf.created_at
           OR (hf2.created_at = hf.created_at AND hf2.id < hf.id))
);
```

Note: in production there may be zero duplicates (the flow has only run
sequentially so far). The operator sees the blast radius via a **Python-level
count query printed to Alembic's console** (see Step 1) — NOT via SQL `RAISE
NOTICE`, which psycopg3 drops when no notice handler is registered (confirmed:
`.venv/lib/python3.12/site-packages/psycopg/_connection_base.py:341-353`,
`_notice_handler` returns immediately with no handlers; `alembic/env.py`
registers none).

## Tasks

### Step 1 — Alembic migration: dedup + unique constraint + index

**File**: new Alembic migration (revision `0029`, `down_revision = "0028"` — the
current head; see `alembic/versions/0028_orphan_header_cleanup.py:38-39`)

`upgrade()` steps:

1. Delete duplicate `hindcast_values` rows (cascading from duplicate headers) —
   the first DELETE in § "Existing duplicate cleanup".
2. Delete duplicate `hindcast_forecasts` rows (keep earliest `created_at`,
   tie-broken by `id`) — the second DELETE, grouped on the full natural key
   INCLUDING `forcing_type`.
3. `CREATE UNIQUE INDEX ... uq_hindcast_forecasts_station_model_step_param_run`
   on `(station_id, model_id, hindcast_step, parameter, hindcast_run_id,
   forcing_type)` — use `IF NOT EXISTS` for idempotency.
4. `CREATE INDEX IF NOT EXISTS ix_hindcast_values_forecast_id ON hindcast_values
   (hindcast_forecast_id)`.
5. `DROP INDEX IF EXISTS ix_hindcast_forecasts_station_model_step` and
   `DROP INDEX IF EXISTS ix_hindcast_forecasts_station_model_step_param`. Both
   are strict prefixes of the new six-column unique index
   (`station_id, model_id, hindcast_step[, parameter, ...]`): the query planner
   can satisfy those shapes via the unique index, so keeping the two non-unique
   indexes only adds write cost on every INSERT/UPDATE. The `IF EXISTS` guard
   makes the drop idempotent (safe on a fresh DB that never had them).

**Logging (reviewer majors, 2026-07-10 — RAISE NOTICE is silently dropped by
psycopg3):** the original plan prescribed a SQL-level `RAISE NOTICE` in a `DO
$$ ... $$` block to emit the runtime deleted count. This does NOT work on this
project's driver: psycopg3 routes PostgreSQL NOTICE messages to registered
`add_notice_handler` callbacks only, and with none registered (Alembic's
default; `alembic/env.py` wires none) the notice is discarded — the operator
sees nothing (confirmed:
`.venv/lib/python3.12/site-packages/psycopg/_connection_base.py:341-353`).
The `DO $$ ... $$` / `RAISE NOTICE` block is therefore **removed entirely**.

Two mechanisms replace it, both of which actually reach the operator:

1. **Docstring dry-run `SELECT count(*)`** — follow the `0028` pattern: a
   dry-run count query in the migration docstring header the operator runs
   manually before the backup (`0028` uses a docstring dry-run,
   `alembic/versions/0028_orphan_header_cleanup.py:14-31,44-60`).
2. **Python-level count printed before the DELETE** — in `upgrade()`, run the
   count through the bound connection using `op.get_bind().execute(sa.text(...))`
   (precedent: `alembic/versions/0023_add_regional_basin_and_unique_constraint.py:24-36`)
   and print it to Alembic's console (visible on every driver, unlike
   `RAISE NOTICE`):

```python
n = op.get_bind().execute(
    sa.text(
        "SELECT count(*) FROM hindcast_forecasts hf WHERE EXISTS ("
        "  SELECT 1 FROM hindcast_forecasts hf2 WHERE "
        "  hf2.station_id = hf.station_id AND hf2.model_id = hf.model_id "
        "  AND hf2.hindcast_step = hf.hindcast_step AND hf2.parameter = hf.parameter "
        "  AND hf2.hindcast_run_id = hf.hindcast_run_id "
        "  AND hf2.forcing_type = hf.forcing_type "
        "  AND (hf2.created_at < hf.created_at "
        "       OR (hf2.created_at = hf.created_at AND hf2.id < hf.id)))"
    )
).scalar_one()
print(f"plan-040: {n} duplicate hindcast_forecasts rows will be deleted")
```

Migration must be idempotent (the `IF NOT EXISTS` on both indexes; the DELETEs
are naturally no-ops on a clean DB, so `n == 0` prints on a clean run).

`downgrade()` (reviewer minor, 2026-07-10 — prior migrations that add indexes
provide a reversible downgrade: `0008_add_constraints_indexes_columns.py:166`,
`0015_hindcast_parameter_index.py:17`, `0017_widen_forecast_unique_index.py:21`):
drop the two new indexes (`DROP INDEX IF EXISTS
uq_hindcast_forecasts_station_model_step_param_run` and `DROP INDEX IF EXISTS
ix_hindcast_values_forecast_id`) and recreate the two non-unique indexes that
`upgrade()` dropped (`ix_hindcast_forecasts_station_model_step` on
`(station_id, model_id, hindcast_step)` and
`ix_hindcast_forecasts_station_model_step_param` on `(station_id, model_id,
hindcast_step, parameter)`). The dedup DELETEs are irreversible by design (as
in `0028`, `alembic/versions/0028_orphan_header_cleanup.py:63-67`); document
that recovery from the deletes is via DB restore, not Alembic.

### Step 2 — Update `store_hindcast` to upsert (DO UPDATE full-replace)

**File**: `hindcast_store.py`

Change the header insert to `pg_insert(...).on_conflict_do_update(index_elements=
[full natural key], set_={mutable non-key fields}).returning(id)`, take the
RETURNING id (existing row's id on conflict, new id on insert), then DELETE the
existing `hindcast_values` for that id and INSERT the new rows keyed to it, and
return that id. Uses Plan 038's injectable `self._begin()` (already merged) — the
header upsert, values DELETE, and values INSERT are all inside the one
transaction, so the replace is atomic. See the design section for the code shape.

Two structural points the implementer MUST NOT skip:

- **`index_elements` = the FULL natural key including `forcing_type`:**
  `["station_id", "model_id", "hindcast_step", "parameter", "hindcast_run_id",
  "forcing_type"]`. `forcing_type` is a key column, not a `set_` column (see
  Design — otherwise `TestFetchWithForcingTypeFilter` breaks).
- **`set_` = the mutable non-key fields, INCLUDING `qc_status` and `qc_flags`:**
  `{model_artifact_id, units, representation, created_at, qc_status, qc_flags}`.
  Do NOT copy the current insert's column list verbatim — `qc_status`/`qc_flags`
  are written by the current code (`hindcast_store.py:80-89`) and must be in
  `set_` so a re-run's QC verdict overwrites the stale one.
- **Move the `rows` list construction INSIDE the `with self._begin()` block**,
  after `header_id` is resolved, and key each row to `header_id` (NOT
  `hindcast.id`). The current code builds `rows` before the txn with
  `"hindcast_forecast_id": hindcast.id` (`hindcast_store.py:50-64`); on a
  conflict `header_id != hindcast.id`, so leaving `rows` outside the txn would
  key the new values to a non-existent header (FK violation) or the wrong
  header. This is a required refactor of the existing pre-txn `rows` block, not
  just an added DELETE.

### Step 3 — Update schema definition

**File**: `metadata.py`

Add the unique index (`uq_hindcast_forecasts_station_model_step_param_run` on
the SIX-column key `station_id, model_id, hindcast_step, parameter,
hindcast_run_id, forcing_type`) and the `hindcast_values` index
(`ix_hindcast_values_forecast_id`) to the SQLAlchemy table definitions so that
`metadata.create_all()` and future Alembic autogenerate remain in sync.
**Remove** the two now-redundant non-unique `sa.Index` declarations
(`ix_hindcast_forecasts_station_model_step` at `metadata.py:763-768` and
`ix_hindcast_forecasts_station_model_step_param` at `metadata.py:769-775`) —
both are strict prefixes of the new six-column unique index and are dropped by
the migration; keeping them in the table definition would cause autogenerate to
try to recreate them. `forcing_type` stays a required column with its CHECK
constraint (`metadata.py:733-737`).

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
3. **QC fields overwritten on conflict:** insert a hindcast with
   `qc_status=RAW`/empty `qc_flags`, then re-insert the SAME natural key with a
   DIFFERENT `qc_status` and a non-empty `qc_flags` — verify the stored header
   now reflects the SECOND write's `qc_status` and `qc_flags` (not the stale
   first values). Guards the `set_` inclusion of `qc_status`/`qc_flags`.
4. **`forcing_type` distinguishes rows (regression guard):**
   `TestFetchWithForcingTypeFilter`
   (`tests/integration/store/test_hindcast_store.py:225-281`) already stores two
   hindcasts with the same `(station_id, model_id, hindcast_step, parameter,
   run_id)` and different `forcing_type` and asserts two rows survive. Confirm it
   STILL PASSES under the new six-column unique index (it must — `forcing_type`
   is a key column). No edit to that test is expected; if it fails, the key is
   wrong, not the test.
5. **Distinct runs preserved:** insert two hindcasts with different `run_id`s for
   the same `(station_id, model_id, hindcast_step, parameter, forcing_type)` —
   verify BOTH are stored (legitimate re-runs, no conflict).
6. Verify `fetch_hindcasts` returns correct results with the new index (no
   behavioral change, just performance) — and de-duplicated (one row per key).
7. **Strengthen the Plan 038 atomicity spy test** (reviewer minor, 2026-07-10):
   `TestStoreHindcastAtomicitySuccess`
   (`tests/integration/store/test_hindcast_store.py:500-547`) currently asserts
   only that a statement touching table `hindcast_values` appears in
   `spy.executed` — but the new DELETE also touches `hindcast_values`, so that
   presence check now passes even if the INSERT is skipped, weakening the locked
   guarantee. Update it to assert BOTH an `Insert` and a `Delete` against
   `hindcast_values` (by statement type, e.g. `sa.sql.dml.Insert`/`Delete`), and
   that `len(spy.executed) >= 3` (header upsert + values delete + values insert).
   Add a companion assertion on a conflict re-insert that the `Delete` is
   recorded. Note this is an intentional edit to a Plan-038-locked test to keep
   its atomicity intent intact under the new DELETE, NOT a weakening.
8. **Rollback test must exercise the CONFLICT path (reviewer minor,
   2026-07-10):** `TestStoreHindcastAtomicityRollback`
   (`tests/integration/store/test_hindcast_store.py:441-497`) forces the
   `IntegrityError` on the `hindcast_values` INSERT and currently asserts only
   that `hit_values_insert` fired. On a **fresh insert** the in-txn DELETE is a
   no-op, so asserting a `hit_delete_fired` flag on a fresh-insert rollback only
   confirms routing — it cannot prove the conflict/replace path rolls back
   atomically.

   Replace (or augment) the fresh-insert scenario with a **conflict-scenario
   rollback test**: (a) seed a prior header row AND its value rows into the DB;
   (b) attempt a re-insert with the SAME natural key (triggering the real DELETE
   of the old value rows) but force the subsequent value INSERT to raise
   (inject an `IntegrityError` via the spy txn); (c) after rollback, assert
   that the ORIGINAL header row is STILL PRESENT and that the ORIGINAL value
   rows are STILL PRESENT (the full DELETE+INSERT rolled back atomically — no
   data was lost and no partial state survived). This proves atomicity of the
   conflict/replace path, not just insert routing.
9. **Align `FakeHindcastStore` with the upsert contract (reviewer major,
   2026-07-10):** `FakeHindcastStore.store_hindcast`
   (`tests/fakes/fake_stores.py:332-334`) currently keys on `hindcast.id` and
   always returns `hindcast.id`, which diverges from the real implementation
   after this plan (real returns the EXISTING row's id on a same-natural-key
   conflict). Update the fake using option (a) — keep the id-keyed dict intact,
   add a parallel natural-key map:

   - **KEEP** `self._hindcasts: dict[HindcastForecastId, HindcastForecast]`
     **UNCHANGED** (no type-annotation change, no pyright break). All existing
     external accesses — `.values()`, `len(...)`, id lookups across
     `test_hindcast.py`, `test_run_hindcast.py`, `test_train_models.py`,
     `test_training_pipeline.py`, `test_hindcast_ensemble_mode.py` — continue
     to work without modification.
   - **ADD** a parallel `self._natural_key_to_id: dict[tuple[StationId,
     ModelId, UtcDatetime, str, UUID, ForcingType], HindcastForecastId]` (the
     six-column natural key: `station_id, model_id, hindcast_step, parameter,
     hindcast_run_id, forcing_type`), initialized to `{}` in `__init__`.
   - **`store_hindcast` logic:** compute the natural-key tuple from the
     incoming `hindcast`. If the key is already in `_natural_key_to_id`
     (conflict): look up the existing `existing_id`, fully replace
     `_hindcasts[existing_id]` with the incoming `hindcast` (mirroring the real
     store's DO UPDATE full-replace — header fields AND value payload), and
     return `existing_id`. Otherwise (clean insert): store `hindcast` under
     `hindcast.id`, register `_natural_key_to_id[nk] = hindcast.id`, and
     return `hindcast.id`.

   Because `_hindcasts` remains id-keyed, existing `len(_hindcasts) == 2 *
   n_steps`-style count assertions (which store DISTINCT natural keys) are
   unaffected. The fake now correctly dedupes only genuine same-natural-key
   conflicts — not distinct-key inserts that happen to share a generated UUID.
   Without this fix, any unit test that exercises a conflict path via the fake
   and asserts on the returned id silently passes with the wrong value.

### Step 5 — Schema checklist (preventive)

**File**: `docs/conventions.md` (new section)

Add a "Schema constraint checklist" to conventions:
- Every table with a natural key must have a unique constraint
- Every FK column used in WHERE/JOIN must have an index
- When adding a constraint to one table in a header+values pair, check the
  sibling table

### Step 6 — Update the authoritative spec (reviewer major, 2026-07-10)

**File**: `docs/spec/types-and-protocols.md`

The `HindcastStore.store_hindcast` signature
(`docs/spec/types-and-protocols.md:1936`) is silent on the return value under a
conflict. Per CLAUDE.md the spec is authoritative, so its silence is a contract
gap: after this plan the returned id is the EXISTING row's id on a same-natural-key
conflict (differs from `hindcast.id`), and a reader inferring `returned_id ==
hindcast.id` would be wrong. Add an inline comment documenting the upsert
contract, e.g.:

```python
def store_hindcast(self, hindcast: HindcastForecast) -> HindcastForecastId: ...
    # Upsert on the natural key (station_id, model_id, hindcast_step, parameter,
    # hindcast_run_id, forcing_type): on conflict, updates mutable header fields +
    # replaces value rows; returns the EXISTING row's id (may differ from hindcast.id).
```

## Open questions — RESOLVED (grill-me 2026-07-10)

1. ~~DO NOTHING vs DO UPDATE on conflict?~~ **RESOLVED: ON CONFLICT DO UPDATE,
   full-replace.** A same-natural-key re-insert overwrites the existing hindcast —
   the header's mutable non-key fields (including `qc_status`/`qc_flags`) are
   refreshed AND the value-row payload is replaced (DELETE old + INSERT new,
   keyed to the existing header id), so the latest write fully wins and no stale
   value rows or stale QC linger. The method returns the EXISTING header id.
   Rationale: the owner wants a same-run re-insert to be a true refresh, not a
   silent skip. (An idempotent retry with identical data converges to the same
   state; a re-insert with corrected data overwrites.) See the design § "Upsert
   in store_hindcast".
2. ~~Is `forcing_type` part of the natural key?~~ **RESOLVED (reviewer blocker,
   2026-07-10): YES — it is a KEY column, not a `set_` column.** The same run can
   emit both `NWP_ARCHIVE` and `REANALYSIS` hindcasts for one step and both must
   persist (`TestFetchWithForcingTypeFilter`,
   `tests/integration/store/test_hindcast_store.py:225-281`). The unique index
   and the cleanup group predicate therefore include `forcing_type`, and it is
   excluded from the upsert `set_`.
