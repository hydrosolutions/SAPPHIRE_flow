# Plan 038 — Store Write Atomicity (AUTOCOMMIT → Transactional Two-Phase Inserts)

**Status**: DRAFT — grill-me COMPLETE (2026-07-08); next = WF1 plan-review
**Phase**: Cross-cutting (store layer + flows)
**Depends on**: Plan 037 (security audit finding H-21)

## Grill-me decisions (2026-07-08)

Six forks were resolved in a grill-me held 2026-07-08:

- **D1 — Read resilience (SCOPE ADDITION)**: In addition to prevention
  (`engine.begin()`) and the one-time cleanup migration, harden the hindcast
  read path so a single orphan header does **not** crash the whole fetch. The
  orphan is skipped with a `WARNING` log **and** an observability signal —
  resilient BUT loud. Scoped to the hindcast read path (forecast and
  station-group reads already tolerate orphans via INNER JOINs). See
  `### Read-path resilience (D1)` in Design and `### Step 7` in Tasks.
- **D2 — Cleanup migration**: KEEP the destructive one-time delete (forecast +
  hindcast orphans via `NOT EXISTS`), with the backup requirement and
  paused-flows / deploy-time execution; `station_groups` stays excluded (manual
  dry-run). Rationale: a header with zero value rows is definitionally valueless,
  and the migration runs with flows paused (no concurrent writes). See Step 4.
- **D3 — Test isolation**: KEEP the `tests/integration/store/conftest.py` autouse
  `TRUNCATE … CASCADE` fixture, but PIN that it must tear down **after** the
  `db_connection` rollback fixture releases its locks (so the separate-connection
  `TRUNCATE` cannot block/deadlock on a still-open per-test transaction), and add
  a test asserting isolation actually holds. See the Step 5a refinement.
- **D4 — Core approach CONFIRMED**: `conn.engine` + per-method `engine.begin()`
  (a second short-lived pooled connection per two-phase write; the shared
  AUTOCOMMIT connection is untouched; zero constructor changes).
- **D5 — Error handling CONFIRMED**: wrap each `engine.begin()` in
  `try / except SQLAlchemyError` → log `ERROR` + raise `StoreError` (widening
  `StoreError` to write failures); reads and single-statement writes keep
  propagating raw exceptions.
- **D6 — task.map invariant**: DOCUMENT the sequential-writes invariant (rely on
  the non-picklable SQLAlchemy connection as the de facto fan-out barrier); do
  **not** add a runtime guard.

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
  does not silently degrade. **(Changed by D1 → skip-with-WARNING; see
  `### Read-path resilience (D1)` below and Step 7.)** Because this fetch feeds
  skill computation / model comparison, one orphan aborting the whole batch is
  the worst-case blast radius, and the reason D1 hardens this path.
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

### Read-path resilience (D1)

Prevention (`engine.begin()`) and the one-time cleanup migration close the
orphan-creation window and remove the historical orphans, but neither guarantees
a fetch will never meet an orphan header again (crash between deploy of the fix
and next cleanup, an orphan created by a path outside the three hardened methods,
or a future duplicate-header edge case). D1 makes the hindcast read path
**resilient BUT loud**: a single orphan header must not abort the whole fetch.

Concretely, in the `fetch_hindcasts` loop (`hindcast_store.py` ~:125 and ~:186),
before reconstructing each ensemble, the missing-value-rows condition is checked:
when a header id has zero `hindcast_values` rows, the loop logs a `WARNING` and
**skips** that header (`continue`) instead of calling `_reconstruct_ensemble` and
propagating its `ValueError`:

```python
if not rows_for_id:
    log.warning(
        "hindcast.orphan_header_skipped",
        hindcast_forecast_id=fid,
        station_id=station_id,
    )
    continue
ensemble = _reconstruct_ensemble(header, rows_for_id, station_id)
```

Equivalently, `_reconstruct_ensemble` may signal the orphan (e.g. return a
sentinel / raise a narrow, caught exception) and the caller performs the
skip-with-WARNING — either shape is acceptable as long as one orphan never
aborts the batch. The valid headers in the same fetch still return.

**Scope**: D1's code change is limited to the **hindcast read path**. Forecast
reads (`forecast_store.py`) and station-group reads use INNER JOINs, so orphan
headers are already silently excluded and no crash occurs. To keep the
no-silent-failure posture consistent, orphans must never be **silent** — the
hindcast path is where the skip is newly needed, and its `WARNING` is the visible
signal that a JOIN would otherwise hide.

**Loud**: the `hindcast.orphan_header_skipped` `WARNING` events are the
observability signal — surfaceable by the watchdog / Flow 4 (pipeline
monitoring) — consistent with the no-silent-failure posture. An orphan is
tolerated at read time but never swallowed.

Why the fetch must not crash: `fetch_hindcasts` feeds skill computation and
model comparison (batch reads across many headers). A single orphan aborting the
whole batch turns one bad header into a full-flow failure — the exact
degrade-gracefully case this hardening targets.

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

**Teardown ordering (D3, MANDATORY)**: The `_truncate_atomic_writes` fixture must
run its `TRUNCATE` **after** the `db_connection` rollback fixture has rolled back
and released its per-test locks. The `TRUNCATE` runs on a *separate* pooled
connection (`db_engine.begin()`) and takes `ACCESS EXCLUSIVE` locks; if the
per-test transaction on `db_connection` is still open, that `TRUNCATE` can block
— and, with lock ordering across the two connections, deadlock — against the
still-open transaction.

Ordering must therefore be enforced explicitly, not left to fixture-declaration
accident. Make `_truncate_atomic_writes` depend on `db_connection` (request it as
a parameter). pytest tears down fixtures in reverse setup order, so
`_truncate_atomic_writes` — which sets up **after** the `db_connection` it now
depends on — tears down **before** `db_connection` rolls back. To force the
`TRUNCATE` to run only after that rollback, drive the truncate from the
`db_connection`-dependent fixture's *finalizer* rather than its `yield`-teardown,
or move the `TRUNCATE` into a fixture that is torn down strictly after
`db_connection` (e.g. by having `db_connection` itself depend on it). The
invariant to hold: **`TRUNCATE` executes only after `db_connection`'s rollback
has completed and its locks are released.** Verify with the isolation-holds test
below (a deadlock/block would surface as a hang/timeout).

**Overhead**: ~10–20 ms per test on a local testcontainer. Acceptable.

**Isolation-holds test (D3)**: Add an explicit test proving isolation actually
holds across the committed writes. In one test, write via an affected store
method (e.g. `store_forecast` / `store_hindcast` / `store_group`) — a committed
`engine.begin()` write. In the next test (or after truncation), assert the row is
**absent** (e.g. `fetch_*` returns `None` / `[]`). This confirms the autouse
`TRUNCATE` fixture cleans committed writes and that its teardown ordering does not
block or leak state between tests.

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

### Step 7 — Harden the hindcast read against orphan headers (D1)

**File**: `hindcast_store.py`

Make the hindcast fetch resilient to orphan headers (a header id with zero
`hindcast_values` rows) — see `### Read-path resilience (D1)` in Design. In the
`fetch_hindcasts` loop (~:125 and the analogous `fetch_hindcasts_by_station`
loop ~:186), before reconstructing each ensemble, check for missing value rows.
When absent, log a `WARNING` and **skip** the header (`continue`) instead of
calling `_reconstruct_ensemble` (whose `ValueError` would otherwise abort the
whole fetch):

```python
if not rows_for_id:
    log.warning(
        "hindcast.orphan_header_skipped",
        hindcast_forecast_id=fid,
        station_id=station_id,
    )
    continue
```

Equivalently, have `_reconstruct_ensemble` signal the orphan and let the caller
skip — either shape is fine so long as one orphan never aborts the batch. Reuse
the module-level `log = structlog.get_logger(__name__)` added in Step 3. Do
**not** change the forecast or station-group read paths (their INNER JOINs
already exclude orphans without crashing).

The `hindcast.orphan_header_skipped` `WARNING` is the observability signal
(watchdog / Flow 4) — orphans are tolerated but never silent.

**Test** (addition to the hindcast store test file):
- **Orphan-header skip test**: seed two hindcast headers — one valid (with value
  rows) and one orphan (header only, no `hindcast_values`). Call `fetch_hindcasts`
  (and/or `fetch_hindcasts_by_station`) and assert: (1) it does **not** raise;
  (2) the valid header is returned; (3) the orphan is absent from the result; and
  (4) a `hindcast.orphan_header_skipped` `WARNING` is emitted (capture via the
  structlog test capture / `caplog`).
