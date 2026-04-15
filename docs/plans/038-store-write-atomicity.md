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
record that breaks downstream queries.

### Affected methods

| Store | Method | Phase 1 | Phase 2 |
|-------|--------|---------|---------|
| `PgForecastStore` | `store_forecast` (line 35–80) | `INSERT INTO forecasts` | `INSERT INTO forecast_values` |
| `PgHindcastStore` | `store_hindcast` (line 28–79) | `INSERT INTO hindcast_forecasts` | `INSERT INTO hindcast_values` |
| `PgStationGroupStore` | `store_group` (line 23–50) | `UPSERT INTO station_groups` | `INSERT INTO station_group_members` |

Between Phase 1 and Phase 2 in each method, only pure Python runs (row
construction from the domain object). No I/O, no network calls. The window for
a crash is small but real under container restarts, OOM kills, or DB connection
loss.

**Scope expansion from H-21**: Plan 037's H-21 identified two affected stores
(forecast, hindcast). This plan adds `PgStationGroupStore.store_group` as a
third — its header upsert + member insert is the same two-phase pattern.

### Blast radius of an orphan record

- **Forecast**: `fetch_forecast(fid)` uses an INNER JOIN on `forecast_values`.
  An orphan header returns zero rows → the method returns `None`. The caller
  sees "no forecast" even though the ID exists. All list queries
  (`fetch_forecasts_in_range`, `_fetch_by_ids`) also use INNER JOINs, so orphan
  headers are silently excluded everywhere. The next cycle inserts a new forecast
  (new UUID), leaving the orphan permanently.
- **Hindcast**: `fetch_hindcasts` does **not** use a JOIN — it fetches headers
  first, then values separately. An orphan header triggers
  `_reconstruct_ensemble`, which raises `ValueError("No hindcast_values rows
  for hindcast_forecast_id=...")`. **An orphan header crashes the fetch**, it
  does not silently degrade.
- **Station group**: `fetch_groups_for_station` uses an INNER JOIN on
  `station_group_members`, so an orphan group header (no members) is **invisible**
  to this query. Only a direct `fetch_group(group_id)` call reveals the orphan,
  returning a group with `station_ids=frozenset()`. The `on_conflict_do_update`
  on the header makes re-running `store_group` safe (it re-upserts the header
  and inserts members with `DO NOTHING` on duplicates), but this only helps if
  the caller retries. Station onboarding (Flow 5) is on-demand, so automatic
  retry is not guaranteed. **Engine injection is still warranted.**

### Deliberately excluded multi-statement patterns

Three other store methods use batch loops with multiple `execute()` calls:

| Store | Method | Pattern | Why excluded |
|-------|--------|---------|-------------|
| `PgStationStore` | `store_thresholds` | Loop of individual upserts | `ON CONFLICT DO UPDATE` — partial write is self-healing on retry |
| `PgObservationStore` | `store_raw_observations` | Batched loop (5000/batch) | `ON CONFLICT DO NOTHING` — next ingest fills gaps |
| `PgHistoricalForcingStore` | `store_forcing` | Batched loop (5000/batch) | `ON CONFLICT DO NOTHING` — next ingest fills gaps |

These are homogeneous batch inserts with upsert semantics. A partial write is
recoverable on the next run without intervention. The header+values pattern in
the three in-scope methods is qualitatively different: the header has no
corresponding values, and no automatic retry will fix that without the caller
explicitly re-inserting the same domain object (which generates a new UUID).

### Why `conn.begin()` on AUTOCOMMIT doesn't work

The security audit (Plan 037, H-21) initially proposed wrapping two-phase inserts
in `with self._conn.begin():` / `conn.begin_nested()`. Investigation revealed
this is **not safe**:

1. **Zero atomicity**: On an `AUTOCOMMIT` connection, `conn.begin()` is a
   Python-only state change — no `BEGIN` is emitted to PostgreSQL.
   `DefaultDialect.do_begin` is a no-op (`pass`). psycopg2's
   `ISOLATION_LEVEL_AUTOCOMMIT` suppresses the implicit `BEGIN` that would
   normally precede each statement, so every `execute()` still auto-commits
   immediately. Rollback on exception calls `dbapi_connection.rollback()`,
   which is a no-op when no transaction block is open.

2. **Savepoints require a transaction**: `conn.begin_nested()` (SAVEPOINT)
   issues a `SAVEPOINT` SQL statement, but PostgreSQL rejects it with
   `ProgrammingError: SAVEPOINT can only be used in transaction blocks`.
   In `AUTOCOMMIT` mode there is no outer transaction to nest within.

### Root cause

The stores were written in Phase 2 (2026-03-24, commit `2df0629`) with
integration tests that use transactional connections rolling back after each
test. No store method calls `conn.commit()`. Three weeks later, Plan 036
(commit `6442ef5`, 2026-04-13) added `setup_production_stores()` with
`AUTOCOMMIT` to prevent writes from silently rolling back on connection close.
This solved the immediate problem but introduced the atomicity gap for
two-phase writes. The test fixtures masked the issue because they never exercise
`AUTOCOMMIT` mode.

## Design

### Chosen approach: `conn.engine` + per-method transaction (Option C simplified)

SQLAlchemy 2.0.48 (our pinned version) exposes `conn.engine` as an instance
attribute that returns the originating `Engine`. The three affected stores
use this in `__init__` to obtain an engine reference:

```python
class PgForecastStore:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn
        self._engine = conn.engine
```

This requires **zero changes** to `make_pg_stores`, `setup_production_stores`,
or `api/deps.py`. The constructor signature is unchanged.

In `store_forecast`, `store_hindcast`, and `store_group`, the two-phase write
uses a short-lived transactional connection from the engine:

```python
def store_forecast(self, forecast: OperationalForecast) -> ForecastId:
    with self._engine.begin() as txn:
        txn.execute(sa.insert(forecasts).values(...))
        rows = _build_value_rows(forecast)
        if rows:
            txn.execute(sa.insert(forecast_values), rows)
    return forecast.id

def store_hindcast(self, hindcast: HindcastForecast) -> HindcastForecastId:
    with self._engine.begin() as txn:
        txn.execute(pg_insert(hindcast_forecasts).values(...))
        rows = [...]
        if rows:
            txn.execute(sa.insert(hindcast_values), rows)
    return hindcast.id

def store_group(self, group: StationGroup) -> None:
    with self._engine.begin() as txn:
        txn.execute(pg_insert(station_groups).values(...).on_conflict_do_update(...))
        if group.station_ids:
            txn.execute(pg_insert(station_group_members).values([...]).on_conflict_do_nothing())
```

`engine.begin()` opens a new connection, issues `BEGIN`, auto-commits on
success, auto-rolls-back on exception, and closes the connection. The shared
AUTOCOMMIT connection used by all other stores is unaffected.

**Note**: `store_hindcast` and `store_group` use `pg_insert` (from
`sqlalchemy.dialects.postgresql`) for statements with `ON CONFLICT` clauses.
The values inserts in `store_forecast` and `store_hindcast` use generic
`sa.insert` (no conflict handling needed). Implementations must preserve
`pg_insert` where `ON CONFLICT` is used and `sa.insert` elsewhere.

### Why this is safe

**Connection pool**: SQLAlchemy defaults to `pool_size=5, max_overflow=10`
(15 connections). Both `store_forecast` (forecast cycle) and `store_hindcast`
(hindcast flow) are called **sequentially** — no `task.map()` fan-out. Maximum
concurrent store connections = 1 + 1 (shared AUTOCOMMIT + one transactional).
The pool is never stressed.

**Invariant — sequential store writes**: The pool safety argument above depends
on store writes remaining outside `task.map()` fan-out blocks.
`docs/architecture-context.md` describes Phase B of Flow 1 as "parallel across
units" via `task.map()`, but the current implementation (`run_forecast_cycle.py`)
uses a serial `for` loop over stations. If a future refactor moves
`store_forecast` or `store_hindcast` inside `task.map()` (e.g. for
>1000-station parallelism), each concurrent task would hold 2 pool slots
(AUTOCOMMIT + transactional), potentially exhausting the pool. This must be
revisited if store writes are ever parallelised. Note also that stores hold
SQLAlchemy connections that are not pickle-serialisable (`train_models.py:316`),
which independently prevents naive `task.map()` fan-out of store calls
(confirmed by `docs/standards/orchestration.md`).

**API path**: `api/deps.py` uses `engine.connect()` (default transactional
mode, not AUTOCOMMIT). The `engine.begin()` call inside a store opens a
separate pooled connection — correct but uses an extra connection unnecessarily
in the API context. Since API write volume is negligible (review/publish
operations only), this is acceptable.

**PgBouncer (v1)**: `engine.begin()` opens a self-contained transaction, which
is fully compatible with PgBouncer in transaction-pooling mode. Option C is
forward-compatible with the v1 connection architecture.

**COPY target (v0-scope §D2)**: The scope document specifies PostgreSQL `COPY`
for `forecast_values` bulk writes. Option C's per-call transactional connection
does not block a future switch to `COPY` — the `engine.begin()` block can host
a `COPY` command via SQLAlchemy Core `text("COPY …")` or Polars
`write_database()`. Note: §D2 also mentions asyncpg `copy_to_table()`, which
uses its own connection pool incompatible with SQLAlchemy Core. The current
store layer uses synchronous SQLAlchemy (psycopg2), not asyncpg — this is a
pre-existing driver choice gap unrelated to this plan. If the future COPY
migration follows the asyncpg path, the `engine.begin()` wrapper would need
replacement, not just extension.

### Logging

Per `docs/standards/logging.md`:
- Transaction rollback on failure: `ERROR` level, event name
  `{entity}.store_failed` (e.g. `forecast.store_failed`)
- SQL timing instrumentation around `engine.begin()`: `DEBUG` level
- Raise `StoreError` on DB failures per `docs/conventions.md` exception table

### What this plan does NOT address

- **`PgModelArtifactStore.store_artifact`**: This is a file+DB two-resource
  atomicity problem (filesystem write precedes DB insert). A DB transaction
  cannot undo a filesystem write. The current write order (file-first, then DB)
  means the only failure mode is an orphan file with no DB record — harmless
  and unreachable by the application. A startup sweep to clean orphan files is
  a nice-to-have for a future maintenance plan, not urgent.
- **Hindcast duplicate protection**: `hindcast_forecasts` lacks a unique
  constraint, so Prefect retries can produce duplicate headers. This is a
  separate schema issue tracked in **Plan 040**.
- **Connection pool sizing**: The engine has no explicit `pool_size`/
  `max_overflow` (security finding M-15). Not made worse by this plan since
  store writes are sequential, but should be addressed separately.

## Tasks

### Step 1 — Add `self._engine` to three stores

**Files**: `forecast_store.py`, `hindcast_store.py`, `station_group_store.py`

In each store's `__init__`, add `self._engine = conn.engine` after the existing
`self._conn = conn` line.

### Step 2 — Wrap two-phase writes in `engine.begin()`

**Files**: same three stores

Replace the two sequential `self._conn.execute()` calls in each method with
a `with self._engine.begin() as txn:` block. Preserve all existing logic
(row construction, conditionals, `pg_insert` dialect usage).

Wrap the `engine.begin()` block in `try / except sqlalchemy.exc.SQLAlchemyError`
(never bare `except`). On failure, log the error (Step 3) and raise `StoreError`
with context (entity ID, station ID, model ID as applicable). This extends
`StoreError`'s scope from retrieval-only to general store failures — Step 6
includes the required `conventions.md` and docstring update.

**Note — new pattern for SQL stores**: No SQL-backed store currently catches
exceptions or wraps them in `StoreError` (only `ZarrNwpGridStore` does, for
file I/O). This plan establishes the pattern for write methods that require
atomicity guarantees. Read methods and single-statement writes continue to let
SQLAlchemy exceptions propagate, consistent with existing store code.

### Step 3 — Add error logging

**Files**: same three stores

Add `log = structlog.get_logger(__name__)` at module level (matching existing
pattern in `zarr_nwp_grid_store.py` and `weather_forecast_store.py`).

Log on transaction rollback per `docs/standards/logging.md`:
- `log.error("forecast.store_failed", forecast_id=..., station_id=..., model_id=...)`
- `log.error("hindcast.store_failed", hindcast_forecast_id=..., station_id=..., model_id=..., error=...)`
- `log.error("station_group.store_failed", group_id=..., error=...)`

Levels: `ERROR` for rollback (matches logging standard: "Database write failure.
Requires human attention."), `DEBUG` for SQL timing around `engine.begin()`
(matches: "SQL query timing. Off in production by default.").

### Step 4 — One-time orphan cleanup migration

**File**: new Alembic migration

**Pre-migration requirement**: This is a destructive data-only migration
(irreversible DELETEs). Per `docs/standards/cicd.md`, the rollback path for
destructive migrations is "restore from backup + redeploy previous image tag."
A database backup **must** be taken before running this migration in production.
Note: the CI/CD standard's "additive only" rule addresses schema migrations for
rolling-deployment compatibility; it is silent on data-only DELETEs. This
migration deletes orphan rows with no schema change and is treated as safe
under the backup-and-redeploy rollback path.

Add a data migration that deletes orphan records created before the fix. Use
`NOT EXISTS` (correlated subquery) instead of `NOT IN` to leverage FK indexes
and avoid materialising the full child-table set:
- `DELETE FROM forecasts f WHERE NOT EXISTS (SELECT 1 FROM forecast_values fv WHERE fv.forecast_id = f.id)`
- `DELETE FROM hindcast_forecasts hf WHERE NOT EXISTS (SELECT 1 FROM hindcast_values hv WHERE hv.hindcast_forecast_id = hf.id)`

**Station groups — excluded from migration**: Empty station groups may be
intentional (created between `store_group(empty)` and `add_station_to_group()`).
The `station_groups` cleanup is **not** included in the Alembic migration.
Instead, before running the migration, execute the following dry-run query
manually and review the output:
```sql
SELECT sg.id, sg.name, sg.created_at
FROM station_groups sg
WHERE NOT EXISTS (
    SELECT 1 FROM station_group_members sgm WHERE sgm.group_id = sg.id
);
```
If the results confirm all empty groups are orphans (not intentionally empty),
a follow-up migration or manual DELETE can be issued. Do not gate conditional
logic inside an Alembic migration — it either runs or it does not.

### Step 5 — Tests

#### 5a — Test isolation fixture (MUST be done first)

**File**: `tests/integration/store/conftest.py` (new file)

The existing `db_connection` fixture wraps each test in a transaction that rolls
back at teardown. After this plan, `engine.begin()` opens a **separate pooled
connection** that commits outside that rollback scope. Data written by the three
affected store methods will persist across tests — breaking isolation for all
51+ write calls in the existing test suite.

Add an `autouse` function-scoped fixture that truncates the six affected tables
after each test:

```python
from __future__ import annotations
import pytest
import sqlalchemy as sa

_ATOMIC_WRITE_TABLES = (
    "forecast_values",
    "forecasts",
    "hindcast_values",
    "hindcast_forecasts",
    "group_model_assignments",
    "model_artifacts",
    "station_group_members",
    "station_groups",
)

@pytest.fixture(autouse=True)
def _truncate_atomic_writes(db_engine: sa.Engine) -> None:
    yield
    with db_engine.begin() as conn:
        conn.execute(
            sa.text(f"TRUNCATE {', '.join(_ATOMIC_WRITE_TABLES)} CASCADE")
        )
```

**FK cascade note**: `model_artifacts.group_id` and
`group_model_assignments.group_id` both reference `station_groups.id`.
`TRUNCATE station_groups CASCADE` would cascade into those tables. Including
them explicitly in the truncation list makes the dependency visible and
prevents silent data loss if future tests write to those tables via
`engine.begin()`.

This requires **no changes** to `tests/integration/conftest.py`, no changes to
store constructors beyond what Step 1 already proposes, and no changes to any
existing test function signature. The `db_connection` rollback fixture continues
to handle seed-data isolation (stations, models, artifacts inserted via
`conn.execute()`) for all other stores. The truncation is belt-and-suspenders
for the new committed writes.

**Overhead**: ~10–20 ms per test on a local testcontainer. Acceptable.

#### 5b — New tests

**Files**: additions to existing store test files

1. **Atomicity rollback test** (one per store): Monkeypatch `txn.execute` to
   raise `sqlalchemy.exc.OperationalError` on the second call (values insert).
   Assert that the header row is absent from the DB and `StoreError` is raised.
2. **Atomicity success test** (one per store): Call the store method, then
   verify both header and values are committed and fetchable.
3. **`conn.engine` smoke test**: Assert `db_connection.engine is db_engine` in
   the test fixture to confirm the engine extraction works with testcontainers.

### Step 6 — Update docs

- Widen `StoreError` docstring in `exceptions.py` from "Store data retrieval
  failure" to "Store operation failure (write atomicity violation, archive not
  found, corrupt data)".
- Update `docs/conventions.md` exception table: change `StoreError` description
  to match the widened scope, and update the `Handling` column from "Log, raise
  to caller" to "Log at store level (write failures), raise to caller" to
  reflect that write-path `StoreError` is now logged at the store before
  raising.
- Update `docs/spec/types-and-protocols.md` if store Protocol docstrings
  mention transaction behavior (currently they do not — verify and skip if clean).
- Archive this plan.
