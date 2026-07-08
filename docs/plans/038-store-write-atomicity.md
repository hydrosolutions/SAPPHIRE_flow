# Plan 038 — Store Write Atomicity (AUTOCOMMIT → Transactional Two-Phase Inserts)

**Status**: DRAFT — grill-me COMPLETE (2026-07-08); WF1 plan-review applied (2026-07-08); next = owner READY
**Phase**: Cross-cutting (store layer + flows)
**Depends on**: Plan 037 (security audit finding H-21)

## Grill-me decisions (2026-07-08)

Six forks were resolved in a grill-me held 2026-07-08. A subsequent WF1
plan-review + two owner decisions (2026-07-08) **reversed D5** (do NOT wrap
writes in `StoreError`) and **reworked the test-isolation strategy** (D3/D5b →
committed session-scoped FK-parent seeds + broad per-test truncation). The
reversal is recorded inline below; entries D5a and D5c are **superseded** by the
D5 reversal (there is no `StoreError` wrap left to preserve fault-escalation
across, and no store-level `ERROR` to double-log against the caller `WARNING`).

- **D1 — Read resilience (SCOPE ADDITION)**: In addition to prevention
  (`engine.begin()`) and the one-time cleanup migration, harden the hindcast
  read path so a single orphan header does **not** crash the whole fetch. The
  orphan is skipped with a `WARNING` log **and** an observability signal —
  resilient BUT loud. Scoped to the hindcast read path (forecast and
  station-group reads already tolerate orphans via INNER JOINs). Both hindcast
  read methods that call `_reconstruct_ensemble` are covered:
  `fetch_hindcasts` (`hindcast_store.py:~125`) and `fetch_hindcasts_by_station`
  (`hindcast_store.py:~186`). The `compute_skills.py:194` caller of
  `fetch_hindcasts_by_station` therefore inherits the skip-not-crash behaviour.
  See `### Read-path resilience (D1)` in Design and `### Step 7` in Tasks.
- **D2 — Cleanup migration**: KEEP the destructive one-time delete (forecast +
  hindcast orphans via `NOT EXISTS`), with the backup requirement and
  paused-flows / deploy-time execution; `station_groups` stays excluded (manual
  dry-run). Rationale: a header with zero value rows is definitionally valueless,
  and the migration runs with flows paused (no concurrent writes). See Step 4.
- **D3 — Test isolation (REWORKED, WF1 + owner, 2026-07-08)**: KEEP the autouse
  `TRUNCATE … CASCADE` fixture, but its scope and the seed strategy are reworked
  (see D5b below). Two owner-confirmed pins:
  (1) **Broad truncation over write tables only.** Per-test the fixture truncates
  ONLY the tables the three affected methods commit
  (`forecasts`, `forecast_values`, `hindcast_forecasts`, `hindcast_values`,
  `station_groups`, `station_group_members`, `group_model_assignments`,
  `model_artifacts`) — **NOT** `stations`/`models`, which are stable
  committed session seeds (truncating them would drop the FK parents the
  separate `engine.begin()` connection needs).
  (2) **Teardown ordering pinned (D3 pin retained).** The separate-connection
  `TRUNCATE` must run **after** the `db_connection` rollback releases its locks;
  otherwise the `ACCESS EXCLUSIVE` truncate deadlocks against the `ROW EXCLUSIVE`
  locks still held by the open per-test transaction.
  Add a test asserting isolation actually holds across committed writes. See the
  Step 5a rework.
- **D5b — FK-parent visibility across connections (test strategy, MANDATORY —
  REWORKED)**: Because the three affected store methods now write on a *separate*
  pooled connection (`engine.begin()`), their FK parents (**stations, models**)
  must be **committed and visible** before the store method is called. The
  existing tests seed parents via the uncommitted `db_connection` rollback
  transaction, so after this plan those parents are invisible at READ COMMITTED
  to the store's new connection → FK `IntegrityError`. **Chosen approach
  (owner-confirmed)**: seed the FK parents once via a **committed,
  session/module-scoped** fixture (visible to every pool connection) using
  deterministic codes (e.g. `TEST-001`), namespaced so they do not collide with
  per-test rollback-seeded rows. The per-test truncation (D3) does **not** touch
  `stations`/`models`, so these committed seeds survive across tests. See the
  Step 5a rework.
- **D4 — Core approach CONFIRMED**: `conn.engine` + per-method `engine.begin()`
  (a second short-lived pooled connection per two-phase write; the shared
  AUTOCOMMIT connection is untouched; zero constructor changes).
- **D5 — REVERSED (WF1, 2026-07-08)**: Do **NOT** catch/wrap writes in
  `StoreError`. Let the raw SQLAlchemy exceptions propagate — matching every
  other SQL-backed store, none of which catch or wrap write exceptions.
  `engine.begin()` already provides the atomicity (it rolls back the whole
  two-phase write on any exception and closes the connection); a
  `try / except SQLAlchemyError` → raise `StoreError` wrapper is unnecessary and
  **harmful**. Two reasons the wrap is harmful:
  (1) **It would break `StoreError`'s meaning.** `StoreError` is raised today
  ONLY when a failure is *connection-fatal* (via `is_connection_fatal`,
  `services/hindcast.py:70`; the raises at `:382–396` and `:679–688` guard on
  it). Widening it to "any write failure" collapses the fatal/transient
  distinction the hindcast service relies on.
  (2) **It would regress the GROUP forecast path.** The operational GROUP loop in
  `run_forecast_cycle.py:1770–1782` has an `except StoreError: raise` at `:1774`
  (and an outer `except StoreError: raise` at `:1814`) that intentionally aborts
  the whole group cycle on a connection-fatal store failure, while a plain
  `except Exception` (`:1776`) logs a `WARNING` and *continues* on a transient
  one. If `store_forecast` started raising `StoreError` on **every** write
  failure, a single *transient* write error would abort the entire group cycle
  instead of being logged-and-skipped — a regression. `engine.begin()`'s
  atomic rollback already handles the transient case correctly without a wrap.
  This reversal **supersedes D5a and D5c** (no wrap ⇒ nothing to preserve
  fault-escalation across, and no store-level `ERROR` to double-log against the
  caller `WARNING`). The station-path callers
  (`run_forecast_cycle.py:1461,1533,1572`, plain `except Exception`) are likewise
  unaffected — raw SQLAlchemy exceptions continue to propagate as before.
- **D5a — SUPERSEDED by the D5 reversal**: there is no `StoreError` write-wrap,
  so there is no fault-escalation to preserve across it and no
  `is_connection_fatal` `__cause__`-unwrap change. `is_connection_fatal` is
  untouched by this plan.
- **D5c — SUPERSEDED by the D5 reversal**: with no store-level `ERROR` on write
  failure, there is no double-log against the caller `WARNING`; the caller
  events are unchanged.
- **D6 — task.map invariant**: DOCUMENT the sequential-writes invariant (rely on
  the non-picklable SQLAlchemy connection as the de facto fan-out barrier — see
  `flows/train_models.py:420–422`); do **not** add a runtime guard.

## Context

### The problem

Three store methods issue two-phase inserts (header row + value rows) with no
transactional wrapper. All operational flows share a single `sa.Connection` in
`AUTOCOMMIT` mode (`flows/_db.py:72` — the
`.execution_options(isolation_level="AUTOCOMMIT")` call). Each `conn.execute()` auto-commits immediately
at the DBAPI level. If the process crashes between the header insert and the
values insert, the header row persists with no corresponding values — an orphan
record that breaks downstream queries.

### Affected methods

| Store | Method | Phase 1 | Phase 2 |
|-------|--------|---------|---------|
| `PgForecastStore` | `store_forecast` (`forecast_store.py:40–85`; `__init__` :37) | `INSERT INTO forecasts` | `INSERT INTO forecast_values` |
| `PgHindcastStore` | `store_hindcast` (`hindcast_store.py:28–79`; `__init__` :25) | `INSERT INTO hindcast_forecasts` | `INSERT INTO hindcast_values` |
| `PgStationGroupStore` | `store_group` (`station_group_store.py:22–49`; `__init__` :19) | `UPSERT INTO station_groups` | `INSERT INTO station_group_members` |

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
  sees "no forecast" even though the ID exists. The value-hydrating list queries
  (`fetch_forecasts_in_range`, `_fetch_by_ids`) also use INNER JOINs, so orphan
  headers are excluded there. **Exception**: `fetch_forecast_summaries`
  (`forecast_store.py:180–219`) queries the `forecasts` table directly with **no
  join** to `forecast_values`, so an orphan header *would* surface as a summary
  row (no values, but visible in API pagination — this method backs the list
  endpoint at `api/routes/api_stations.py:266`). The claim that orphans are
  "silently excluded everywhere" is therefore false for summaries. This does not
  change the plan's approach: prevention via `engine.begin()` closes the
  creation window, so no D1-style read-path skip hardening is required for the
  forecast summary path. The next cycle inserts a new forecast (new UUID),
  leaving any historical orphan permanent until the Step 4 cleanup migration.
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
  retry is not guaranteed.

  **No production caller today (verified 2026-07-08)**: `grep -rn "store_group("
  src/` shows the only definitions/uses of `store_group` are the Protocol
  (`protocols/stores.py:536`) and the implementation
  (`station_group_store.py:22`); every *call* site is in `tests/` or the fake
  (`tests/fakes/fake_stores.py`). Neither `flows/onboard.py` nor
  `services/onboarding.py` / `services/model_onboarding.py` calls it — groups are
  created at bootstrap (TOML import / scripts), not through a runtime flow. The
  two-phase risk for `store_group` is therefore currently exercised only by test
  code. We still harden it (D4) for two reasons: (a) it is a public store method
  that a future onboarding path is expected to call, and hardening it now avoids a
  second atomicity pass later; (b) the hardening is mechanically identical to the
  forecast/hindcast fix (`engine.begin()` wrapper), so the marginal cost is
  near-zero. **Open item for the implementer / owner**: confirm whether the
  absence of a runtime `store_group` call is intentional (bootstrap-only) or a
  genuine gap in Flow 5 onboarding. If it is a gap, that is a *separate* plan — do
  not add the call in this plan (scope discipline); note the finding and move on.
  **Engine injection remains warranted** regardless.

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
        # NOTE: pg_insert here has NO on_conflict clause (== sa.insert); keep or
        # simplify, but do NOT add ON CONFLICT (no unique constraint; see Plan 040).
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

**Note (corrected — dialect usage per method)**: only `store_group` genuinely
uses `ON CONFLICT`. Verified against the current code:

- `store_forecast` (`forecast_store.py`): uses generic `sa.insert` throughout
  (header + values). No conflict handling.
- `store_hindcast` (`hindcast_store.py:29–53`): uses `pg_insert(hindcast_forecasts)`
  for the header **but with NO `.on_conflict_do_*()` clause** — it is
  semantically identical to `sa.insert` here. There is no unique constraint on
  `hindcast_forecasts` for a conflict to target (duplicate-header protection is
  deferred to **Plan 040**). The `pg_insert` there is vestigial: it may be left
  as-is (harmless) or simplified to `sa.insert` (equally correct). The values
  insert uses generic `sa.insert`. **Do NOT add an `ON CONFLICT` clause to
  `store_hindcast`** — the schema does not support it and it is out of scope.
- `store_group` (`station_group_store.py`): uses `pg_insert` **with** ON CONFLICT
  — `on_conflict_do_update` on the header and `on_conflict_do_nothing` on the
  members insert. **Those clauses MUST be preserved verbatim** inside the txn
  block.

The earlier phrasing ("`store_hindcast` and `store_group` use `pg_insert` … for
statements with `ON CONFLICT` clauses") was factually wrong for `store_hindcast`
and is corrected above.

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
SQLAlchemy connections that are not pickle-serialisable
(`flows/train_models.py:420–422`),
which independently prevents naive `task.map()` fan-out of store calls
(confirmed by `docs/standards/orchestration.md`).

**API path**: `api/deps.py`'s `get_stores` depends on `get_connection`
(`engine.connect()`, default transactional mode, not AUTOCOMMIT). The
`engine.begin()` call inside a store opens a separate pooled connection — correct
but uses an extra connection unnecessarily in that context. **However, none of
the three affected store methods (`store_forecast`, `store_hindcast`,
`store_group`) is called from any API route** — so the "extra connection"
concern is moot for the current API surface. Moreover, the API's one write
endpoint (alert acknowledge) already uses `engine.begin()` via
`get_connection_rw` and instantiates `PgAlertStore(conn_rw)` directly, bypassing
`get_stores` entirely (`api/routes/api_alerts.py:112`). Do **not** over-apply the
`engine.begin()` pattern to API-path stores that do not need it. Since any future
API write volume is negligible (review/publish operations only), the extra
connection is acceptable if it ever arises.

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

Two hindcast read methods call `_reconstruct_ensemble` and must **both** be
hardened: `fetch_hindcasts` (`hindcast_store.py:~125`) and
`fetch_hindcasts_by_station` (`hindcast_store.py:~186`). In each loop, before
reconstructing an ensemble, the missing-value-rows condition is checked: when a
header id has zero `hindcast_values` rows, the loop logs a `WARNING` and
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

**Scope**: D1's code change is limited to the **hindcast read path** — and to
**both** of its `_reconstruct_ensemble` callers (`fetch_hindcasts:~125`,
`fetch_hindcasts_by_station:~186`). The `compute_skills.py:194` caller of
`fetch_hindcasts_by_station` benefits directly from the skip-not-crash behaviour
(one orphan header no longer aborts a skill-computation batch). Forecast reads
(`forecast_store.py`) and station-group reads use INNER JOINs, so orphan headers
are already silently excluded and no crash occurs. To keep the no-silent-failure
posture consistent, orphans must never be **silent** — the hindcast path is where
the skip is newly needed, and its `WARNING` is the visible signal that a JOIN
would otherwise hide.

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
- **Write failures are NOT caught or logged at the store** (D5 reversed): the
  raw SQLAlchemy exception propagates, `engine.begin()` has already rolled the
  transaction back, and the existing caller-side handlers log it
  (`run_forecast_cycle.py` `forecast_cycle.store_forecast_failed` WARNING;
  `services/hindcast.py` `hindcast.step_failed`). No new `{entity}.store_failed`
  ERROR event is added.
- The one new log event this plan adds is the **D1 read-path**
  `hindcast.orphan_header_skipped` `WARNING` (Step 7) — the reason a
  module-level logger is added to `hindcast_store.py`.

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
a `with self._engine.begin() as txn:` block, switching the two `self._conn.execute()`
calls to `txn.execute(...)`. Preserve all existing logic verbatim — row
construction, the `if rows:` / `if group.station_ids:` conditionals, and the
per-method dialect usage (`sa.insert` for `store_forecast`; `pg_insert`
*without* an `ON CONFLICT` clause for the `store_hindcast` header; `pg_insert`
**with** `on_conflict_do_update` / `on_conflict_do_nothing` for `store_group`,
preserved verbatim).

**Do NOT catch or wrap exceptions (D5 reversed).** Let any SQLAlchemy exception
propagate raw out of the `with` block — this matches every other SQL-backed
store, none of which catch or wrap write exceptions. `engine.begin()` already
rolls the whole two-phase write back atomically and closes the connection on
exception, so no wrapper is needed. Wrapping in `StoreError` would (a) collapse
`StoreError`'s connection-fatal-only meaning and (b) regress the GROUP path,
whose `except StoreError: raise` (`run_forecast_cycle.py:1774`, outer `:1814`)
would abort the whole group cycle on a *transient* write failure. See the D5
reversal in Grill-me decisions. `is_connection_fatal` (`services/hindcast.py:70`)
is left untouched.

### Step 3 — Add module logger for the D1 orphan-skip WARNING

**File**: `hindcast_store.py`

Add `log = structlog.get_logger(__name__)` at module level (matching existing
pattern in `zarr_nwp_grid_store.py` and `weather_forecast_store.py`). This logger
exists **only** to emit the D1 read-path `hindcast.orphan_header_skipped`
`WARNING` from Step 7 — the write paths are **not** logged at the store level
(D5 reversed; write failures propagate raw and are logged by the callers).
`forecast_store.py` and `station_group_store.py` need no module logger under this
plan.

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

#### 5a — Test isolation v2: committed session seeds + broad truncation (MUST be done first)

**File**: `tests/integration/conftest.py` (hoisted so coverage reaches every
affected test, not just `tests/integration/store/`)

Two distinct problems must be solved together, in order, before Step 2 is
implemented — implementing Step 2 without both will immediately break the
existing integration tests that call the three affected store methods.

**Problem 1 — committed writes leak across tests.** The existing `db_connection`
fixture wraps each test in a transaction that rolls back at teardown. After this
plan, `engine.begin()` opens a **separate pooled connection** that commits
outside that rollback scope. Data written by the three affected store methods
persists across tests — breaking isolation for every affected write call in the
suite.

**Problem 2 — FK parents seeded via `db_connection` are invisible to the store's
new connection (D5b).** The affected-store tests currently seed prerequisites
(stations, models) via the uncommitted `db_connection` transaction, then call the
store method. After the change, the store writes on a *second* connection from
`engine.begin()`; at READ COMMITTED the uncommitted parents are invisible to it →
every write raises `IntegrityError` (FK violation). The truncate fixture below
does **nothing** for this problem.

**Fix for Problem 2 (D5b) — committed session/module-scoped FK-parent seeds.**
Seed the FK parents the atomicity tests need — **stations and models** — via a
**committed, session/module-scoped** fixture (`db_engine.begin()`), so they are
visible to every pool connection, including the store's separate `engine.begin()`
connection. Seed them **once** so deterministic codes (e.g. `TEST-001`) do not
collide per-test. Namespace these committed seed codes so they do not collide
with any per-test rollback-seeded `stations`/`models` rows a test may still add
on `db_connection`. Because the per-test truncation (below) does **not** touch
`stations`/`models`, these committed seeds survive across the whole session.

```python
with db_engine.begin() as seed:
    sid = _seed_station(seed, code="TEST-001")   # committed, session-scoped
    mid = _seed_model(seed, name="test-model")   # committed, session-scoped
store = PgForecastStore(db_connection)           # store constructor unchanged
```

**Fix for Problem 1 — per-test TRUNCATE over the write tables only (D3).** Add an
`autouse` function-scoped fixture that truncates ONLY the tables the three
affected methods commit — **NOT** `stations`/`models` (they are the committed
session seeds; truncating them would drop the FK parents the separate
`engine.begin()` connection needs):

```python
from __future__ import annotations
import pytest
import sqlalchemy as sa

# Write tables committed by store_forecast / store_hindcast / store_group only.
# stations/models are DELIBERATELY EXCLUDED: they are committed session seeds
# (D5b) and truncating them would drop the FK parents the separate
# engine.begin() connection depends on.
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
def _truncate_atomic_writes(db_connection, db_engine: sa.Engine):
    yield
    # Teardown-ordering pin (D3): the separate-connection TRUNCATE must run
    # AFTER db_connection's per-test transaction has rolled back and released
    # its ROW EXCLUSIVE locks — otherwise this ACCESS EXCLUSIVE truncate
    # deadlocks against them. Depending on db_connection orders this teardown
    # to fire after db_connection's rollback.
    with db_engine.begin() as conn:
        conn.execute(
            sa.text(f"TRUNCATE {', '.join(_ATOMIC_WRITE_TABLES)} CASCADE")
        )
```

**Teardown ordering (D3 pin retained)**: the truncate runs in teardown, and the
fixture depends on `db_connection` so its teardown fires *after* `db_connection`
rolls back and drops its locks. Truncating while the per-test transaction still
holds `ROW EXCLUSIVE` locks would deadlock the `ACCESS EXCLUSIVE` truncate. The
first test in the session starts against empty write tables (migrations leave
them empty).

**Coverage — widen beyond `store/` (owner-confirmed).** The autouse truncation
must apply to **every** test that calls one of the three affected store methods,
because each commits on a separate connection whose rows would otherwise leak.
The affected files are:
- `tests/integration/store/test_forecast_store.py`
- `tests/integration/store/test_hindcast_store.py`
- `tests/integration/store/test_station_group_store.py`
- `tests/integration/store/test_forecast_summary.py`
- `tests/integration/test_model_onboarding_integration.py` (outside `store/`)
- `tests/integration/test_e2e_pipeline.py` (outside `store/`)

**Chosen approach**: place the autouse `_truncate_atomic_writes` fixture (and the
committed FK-parent seed fixture) in **`tests/integration/conftest.py`** so it is
in scope for both the `store/` subpackage and the two top-level integration
files, rather than in `tests/integration/store/conftest.py` (which would miss
the two outside `store/`).

**FK cascade note**: `model_artifacts.group_id` and
`group_model_assignments.group_id` both reference `station_groups.id`.
`TRUNCATE station_groups CASCADE` would cascade into those tables. Including
them explicitly in the truncation list makes the dependency visible and
prevents silent data loss if future tests write to those tables via
`engine.begin()`.

**Overhead**: ~10–20 ms per test on a local testcontainer. Acceptable.

**Isolation-holds test (D3)**: Add an explicit test proving isolation actually
holds across the committed writes. In one test, write via an affected store
method (e.g. `store_forecast` / `store_hindcast` / `store_group`) — a committed
`engine.begin()` write against the committed session FK parents. In a
*subsequent* test, assert the row is **absent** (e.g. `fetch_*` returns `None` /
`[]`). This confirms the autouse `TRUNCATE` fixture cleans committed writes left
by the prior test and that no state leaks between tests, while the committed
`stations`/`models` seeds correctly survive.

#### 5b — New tests

**Files**: additions to existing store test files

All tests below rely on the committed session/module-scoped FK-parent seeds
(stations, models) from Step 5a so the store's internal `engine.begin()`
connection can see them.

1. **Atomicity rollback test** (one per store): Monkeypatch `txn.execute` to
   raise `sqlalchemy.exc.OperationalError` on the second call (values insert).
   Assert that (a) the raw SQLAlchemy exception propagates (it is **not** wrapped
   in `StoreError` — D5 reversed) and (b) the header row is absent from the DB
   (`engine.begin()` rolled the whole two-phase write back atomically).
2. **Atomicity success test** (one per store): Call the store method, then
   verify both header and values are committed and fetchable.
3. **`conn.engine` smoke test**: Assert `db_connection.engine is db_engine` in
   the test fixture to confirm the engine extraction works with testcontainers.

### Step 6 — Update docs

- **`StoreError` is NOT widened (D5 reversed).** Leave the `exceptions.py`
  docstring ("Store data retrieval failure …") and the `docs/conventions.md`
  exception-table row for `StoreError` **unchanged** — write failures now
  propagate raw SQLAlchemy exceptions, so `StoreError`'s scope and handling are
  the same as before this plan.
- Update `docs/spec/types-and-protocols.md` if store Protocol docstrings
  mention transaction behavior (currently they do not — verify and skip if clean).
- **No non-store production code change.** With D5 reversed there is no
  `is_connection_fatal` edit; the only production files touched are the three
  stores (Steps 1–3, 7). Note in the PR description that Step 7 adds a read-path
  `WARNING` in `hindcast_store.py` so reviewers expect the behaviour change.
- Archive this plan.

### Step 7 — Harden the hindcast read against orphan headers (D1)

**File**: `hindcast_store.py`

Make the hindcast fetch resilient to orphan headers (a header id with zero
`hindcast_values` rows) — see `### Read-path resilience (D1)` in Design. **Both**
hindcast read methods that call `_reconstruct_ensemble` must be hardened:
`fetch_hindcasts` (loop at `hindcast_store.py:~125`) **and**
`fetch_hindcasts_by_station` (loop at `~:186`). In each loop, before
reconstructing each ensemble, check for missing value rows. When absent, log a
`WARNING` and **skip** the header (`continue`) instead of calling
`_reconstruct_ensemble` (whose `ValueError` would otherwise abort the whole
fetch):

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
skip — either shape is fine so long as one orphan never aborts the batch, and it
must be applied consistently to **both** loops. Reuse the module-level
`log = structlog.get_logger(__name__)` added in Step 3. Do **not** change the
forecast or station-group read paths (their INNER JOINs already exclude orphans
without crashing).

The `hindcast.orphan_header_skipped` `WARNING` is the observability signal
(watchdog / Flow 4) — orphans are tolerated but never silent. The
`compute_skills.py:194` caller of `fetch_hindcasts_by_station` inherits this
skip-not-crash behaviour directly (a single orphan no longer aborts a
skill-computation batch), which is why hardening `fetch_hindcasts_by_station` as
well as `fetch_hindcasts` matters.

**Test** (addition to the hindcast store test file):
- **Orphan-header skip test**: seed two hindcast headers — one valid (with value
  rows) and one orphan (header only, no `hindcast_values`). Call `fetch_hindcasts`
  (and/or `fetch_hindcasts_by_station`) and assert: (1) it does **not** raise;
  (2) the valid header is returned; (3) the orphan is absent from the result; and
  (4) a `hindcast.orphan_header_skipped` `WARNING` is emitted (capture via the
  structlog test capture / `caplog`).
