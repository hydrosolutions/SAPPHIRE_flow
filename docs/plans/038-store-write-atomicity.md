# Plan 038 — Store Write Atomicity (AUTOCOMMIT → Transactional Two-Phase Inserts)

**Status**: DRAFT
**Phase**: Cross-cutting (store layer + flows)
**Depends on**: Plan 037 (security audit finding H-21)

## Context

### The problem

Three store methods issue two-phase inserts (header row + value rows) with no
transactional wrapper. All operational flows share a single `sa.Connection` in
`AUTOCOMMIT` mode (`_db.py:68`). Each `conn.execute()` auto-commits immediately
at the DBAPI level. If the process crashes between the header insert and the
values insert, the header row persists with no corresponding values — an orphan
record that silently degrades downstream queries.

### Affected methods

| Store | Method | Phase 1 | Phase 2 |
|-------|--------|---------|---------|
| `PgForecastStore` | `store_forecast` (line 36–80) | `INSERT INTO forecasts` | `INSERT INTO forecast_values` |
| `PgHindcastStore` | `store_hindcast` (line 29–79) | `INSERT INTO hindcast_forecasts` | `INSERT INTO hindcast_values` |
| `PgStationGroupStore` | `store_group` (line 23–48) | `INSERT INTO station_groups` | `INSERT INTO station_group_members` |

Between Phase 1 and Phase 2 in each method, only pure Python runs (row
construction from the domain object). No I/O, no network calls. The window for
a crash is small but real under container restarts, OOM kills, or DB connection
loss.

### Blast radius of an orphan record

- **Forecast**: `fetch_forecast(fid)` JOINs on `forecast_values`. An orphan header
  returns `None` — the caller sees "no forecast" even though the ID exists. The
  next cycle inserts a new forecast (new UUID), leaving the orphan permanently.
- **Hindcast**: Same JOIN pattern. Orphan headers silently inflate hindcast counts
  but contribute zero data to skill computation.
- **Station group**: Group header exists but has no members. Downstream
  `fetch_groups_for_station` returns a group with `station_ids=()`, which may
  cause empty model assignments.

### Why `conn.begin()` on AUTOCOMMIT doesn't work

The security audit (Plan 037, H-21) initially proposed wrapping two-phase inserts
in `with self._conn.begin():`. Investigation revealed this is **not safe**:

1. **Zero atomicity**: On an `AUTOCOMMIT` connection, `conn.begin()` calls
   `dialect.do_begin()` which is a no-op for psycopg in autocommit mode. No
   `BEGIN` reaches PostgreSQL. Each statement still auto-commits immediately.
   Rollback on exception is also a no-op.

2. **InvalidRequestError**: SQLAlchemy's autobegin fires on the first `execute()`
   call, setting `self._transaction`. A subsequent `conn.begin()` inside a store
   method raises `InvalidRequestError("This connection has already initialized a
   SQLAlchemy Transaction()")` because the shared connection was already used by
   prior store calls in the same flow.

3. **Savepoints require a transaction**: `conn.begin_nested()` (SAVEPOINT) only
   works inside an active transaction. In `AUTOCOMMIT` mode there is no outer
   transaction to nest within.

### Design options

**Option A — Per-method fresh connection** (targeted, minimal blast radius):
Each two-phase write method opens its own short-lived connection from the engine
(without AUTOCOMMIT), runs both inserts inside a single transaction, then closes.
The shared AUTOCOMMIT connection continues to serve all other store methods.

Pros: Surgical fix. No changes to the 50+ single-statement store methods.
Cons: Requires the engine to be accessible from store instances (currently they
only hold `self._conn`). Adds a second connection per two-phase write.

**Option B — Remove global AUTOCOMMIT, add explicit commits** (architectural):
Change `setup_production_stores` to use default transaction isolation. Add
`conn.commit()` after each logical unit of work in every flow. Wrap two-phase
writes in `with conn.begin():` (now effective since the connection is transactional).

Pros: Correct by default. All multi-statement writes are atomic.
Cons: Every flow and every single-statement store method now requires explicit
commit management. Missing a `commit()` means data silently rolls back on
connection close. High risk of regression across ~60 store methods.

**Option C — Engine injection + per-method transaction** (recommended):
Pass the `sa.Engine` (not just `sa.Connection`) to the three affected stores.
In `store_forecast`, `store_hindcast`, and `store_group`, open a short-lived
transactional connection from the engine:

```python
def store_forecast(self, forecast: OperationalForecast) -> ForecastId:
    with self._engine.begin() as txn:
        txn.execute(sa.insert(forecasts).values(...))
        rows = _build_value_rows(forecast)
        if rows:
            txn.execute(sa.insert(forecast_values), rows)
    return forecast.id
```

`engine.begin()` opens a new connection, issues `BEGIN`, auto-commits on
success, auto-rolls-back on exception, and closes the connection. The shared
AUTOCOMMIT connection used by all other stores is unaffected.

Pros: Targeted fix. Only 3 methods change. No regression risk for other stores.
Cons: Three stores hold both `self._conn` (for reads/single writes) and
`self._engine` (for atomic multi-writes). Slight API change in constructors.

## Tasks

TBD — pending user review of the design options.

## Open questions

1. Should `store_group` use the same engine-injection pattern, or is the
   `on_conflict_do_update` on the header sufficient to make orphan members
   a non-issue (re-running `store_group` with the same group ID would re-upsert
   the header and then insert members)?

2. Should we add an orphan-cleanup query (e.g., `DELETE FROM forecasts WHERE id
   NOT IN (SELECT DISTINCT forecast_id FROM forecast_values)`) as a periodic
   maintenance task to handle any existing orphans?

3. Does the `PgModelArtifactStore.store_artifact` method (filesystem write +
   DB insert) also need transactional treatment? A crash between
   `artifact_path.write_bytes()` and the `INSERT` leaves an orphan file.
