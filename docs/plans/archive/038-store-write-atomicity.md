# Plan 038 — Store Write Atomicity (AUTOCOMMIT → Transactional Two-Phase Inserts)

**Status**: READY (2026-07-08) — grill-me COMPLETE + **3 WF1 plan-review passes** (design converged: D5 reversed, test-isolation reworked to injectable-transaction DI; the residual test-migration mechanics are WF2's to prove by running the tests, with guidance pinned in Steps 5a/5b). Next = **WF2 (vision-build) → hold-at-PR**.
**Phase**: Cross-cutting (store layer + flows)
**Depends on**: Plan 037 (security audit finding H-21)

## Grill-me decisions (2026-07-08)

Six forks were resolved in a grill-me held 2026-07-08. A subsequent WF1
plan-review + two owner decisions (2026-07-08) **reversed D5** (do NOT wrap
writes in `StoreError`). A later owner decision (2026-07-08) then **replaced the
whole test-isolation approach**: the committed session-scoped FK-parent seeds +
broad per-test `TRUNCATE` scheme is **abandoned** in favour of an **injectable
transaction-context factory (DI)**. The stores gain one optional keyword-only
constructor param; production is unchanged (default factory = `conn.engine.begin`),
and tests inject a factory that runs the write inside a `SAVEPOINT` on the test's
own transactional connection so it participates in the existing per-test rollback.
This eliminates the committed-seeds fixture entirely and, with it, the four
problems the WF1 re-verify found (see the review-pass note at the end of this
section). The reversal and the DI rework are recorded inline below; entries D5a
and D5c are **superseded** by the D5 reversal, and D3 (broad `TRUNCATE`) is
**superseded** by the DI rework.

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
- **D3 — Test isolation: SUPERSEDED (2026-07-08) — replaced by injectable
  transaction DI.** The broad-`TRUNCATE` scheme is **abandoned**. The autouse
  per-test cleanup fixture, the committed session-scoped FK-parent seeds, the
  `_ATOMIC_WRITE_TABLES` truncation, the row-level `station_groups` DELETE, the
  CASCADE-avoidance analysis, the hoisted `tests/integration/store/conftest.py`
  autouse fixture + `pytest_plugins` cross-directory registration, the
  widen-beyond-`store/` coverage, and the ≈51-test migration of existing `_seed_*`
  calls to committed seeds are **all no longer needed and are removed**. Under the
  DI approach the store write runs inside a `SAVEPOINT` on the test's own
  `db_connection` (which is already a real, rolled-back-at-teardown transaction),
  so nothing commits and the existing rollback fixture provides full isolation —
  no truncation, no committed seeds, no cross-connection FK-visibility problem.
  See the reworked D4 and Step 5a. The one surviving requirement is the
  **isolation-holds test**: assert a store-method write is absent in a fresh
  connection / the next test because the outer transaction rolled back.
- **D5b — SUPERSEDED (2026-07-08) — no longer applicable under DI.** The
  cross-connection FK-parent-visibility problem existed *only* because the old D4
  wrote on a *separate* pooled `engine.begin()` connection at READ COMMITTED,
  making uncommitted `db_connection`-seeded parents invisible. Under the DI
  approach the injected test factory runs the write inside a `SAVEPOINT` on the
  **same** `db_connection`, so FK parents seeded via `db_connection` are visible
  to the write with no commit required. The committed session seeds, the two-station
  minimum, the unified seed helpers, the dual-visibility analysis, and the
  existing-test migration are therefore all removed. Existing tests keep their
  current `db_connection` seeds unchanged.
- **D4 — Core approach CONFIRMED + REFINED to an injectable transaction factory
  (2026-07-08)**: the two-phase write runs inside a transaction obtained from an
  **injectable transaction-context factory** rather than always `engine.begin()`.
  The three stores gain an OPTIONAL keyword-only constructor param
  `transaction_factory: Callable[[], ContextManager[sa.Connection]] | None = None`;
  `__init__` sets `self._begin = transaction_factory if transaction_factory is not
  None else conn.engine.begin`. Production callers are **unchanged**
  (`PgForecastStore(conn)` still works): the default factory is `conn.engine.begin`,
  i.e. a fresh pooled transactional connection that issues `BEGIN`, commits on
  success and rolls back on exception — exactly today's per-method
  `engine.begin()` behavior. This refines the earlier "zero constructor change"
  claim to **"one optional kwarg, default preserves production behavior."** The
  store methods use `with self._begin() as txn:` and execute the header + value
  inserts on `txn` (preserving the per-method `pg_insert` vs `sa.insert` dialect
  usage). In PRODUCTION `txn` is the fresh `engine.begin()` connection (atomic
  commit/rollback, separate from the shared AUTOCOMMIT connection). In TESTS the
  injected factory runs the write inside a `SAVEPOINT` (`conn.begin_nested()`) on
  the test's own transactional `db_connection` (see D3 / Step 5a) so it
  participates in the per-test rollback. Note savepoints are safe in tests
  *because* `db_connection` is transactional; they do **not** work on the
  production AUTOCOMMIT connection — which is exactly why production keeps the
  default `engine.begin` factory rather than a savepoint.
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

**Review-pass note (DI rework, 2026-07-08).** The WF1 re-verify of the earlier
committed-seeds + broad-`TRUNCATE` design raised **2 blockers + 2 majors**:
(a) `TRUNCATE station_groups CASCADE` would wipe the committed `model_artifacts`
seed (table-level cascade), (b) autouse-fixture placement forcing a `db_engine`
container onto every `tests/integration/` test, (c) a single committed station
could not satisfy the two-member `store_group` tests, and (d) a mandatory ≈51-test
migration of existing `_seed_*` calls to committed seeds. This DI rework
**resolves all four by deleting the fixture that caused them**: with the write
running inside a `SAVEPOINT` on the test's own `db_connection`, there is no
separate committed connection, so no committed seeds, no `TRUNCATE`/CASCADE, no
cross-directory autouse fixture, and no existing-test migration are needed.
D1, D2, D5 (reversed), and D6 are unchanged by this rework.

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

### Chosen approach: injectable transaction-context factory (default = `conn.engine.begin`)

SQLAlchemy 2.0.48 (our pinned version) exposes `conn.engine` as an instance
attribute that returns the originating `Engine`, whose `.begin()` yields a
short-lived transactional connection. Rather than call `engine.begin()` directly
in each method, the three affected stores obtain their transaction context from an
**injectable factory** set in `__init__`. This keeps production behavior identical
to a per-method `engine.begin()` while letting tests inject a `SAVEPOINT`-based
factory on the test's own transactional connection:

```python
from collections.abc import Callable
from contextlib import AbstractContextManager as ContextManager

class PgForecastStore:
    def __init__(
        self,
        conn: sa.Connection,
        *,
        transaction_factory: Callable[[], ContextManager[sa.Connection]] | None = None,
    ) -> None:
        self._conn = conn
        # Default = a fresh pooled transactional connection (BEGIN / commit-on-
        # success / rollback-on-exception) — exactly today's engine.begin() behavior.
        self._begin = (
            transaction_factory if transaction_factory is not None else conn.engine.begin
        )
```

Production callers are **unchanged** — `PgForecastStore(conn)` still works, and
the default factory (`conn.engine.begin`) preserves today's behavior. This
requires **zero changes** to `make_pg_stores`, `setup_production_stores`, or
`api/deps.py`; the only signature change is one optional keyword-only param that
callers may ignore. This refines the earlier "zero constructor change" framing to
**"one optional kwarg, default preserves production behavior."**

In `store_forecast`, `store_hindcast`, and `store_group`, the two-phase write
runs inside the factory's transaction context:

```python
def store_forecast(self, forecast: OperationalForecast) -> ForecastId:
    with self._begin() as txn:
        txn.execute(sa.insert(forecasts).values(...))
        rows = _build_value_rows(forecast)
        if rows:
            txn.execute(sa.insert(forecast_values), rows)
    return forecast.id

def store_hindcast(self, hindcast: HindcastForecast) -> HindcastForecastId:
    with self._begin() as txn:
        # NOTE: pg_insert here has NO on_conflict clause (== sa.insert); keep or
        # simplify, but do NOT add ON CONFLICT (no unique constraint; see Plan 040).
        txn.execute(pg_insert(hindcast_forecasts).values(...))
        rows = [...]
        if rows:
            txn.execute(sa.insert(hindcast_values), rows)
    return hindcast.id

def store_group(self, group: StationGroup) -> None:
    with self._begin() as txn:
        txn.execute(pg_insert(station_groups).values(...).on_conflict_do_update(...))
        if group.station_ids:
            txn.execute(pg_insert(station_group_members).values([...]).on_conflict_do_nothing())
```

In PRODUCTION `self._begin` is `conn.engine.begin`, so `with self._begin() as
txn:` opens a new connection, issues `BEGIN`, auto-commits on success,
auto-rolls-back on exception, and closes the connection — atomic and separate from
the shared AUTOCOMMIT connection, which is unaffected. In TESTS the injected
factory runs the same block inside a `SAVEPOINT` on the test's own transactional
`db_connection` (see Step 5a), so the write rolls back with the test.

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
(`engine.connect()`, default transactional mode, not AUTOCOMMIT). `api/deps.py`
(`~:62–65`) constructs all three affected stores (`group_store`,
`hindcast_store`, `forecast_store`) on that real transactional
`engine.connect()`-supplied connection; under DI they receive **no**
`transaction_factory` and therefore use the DEFAULT factory (`conn.engine.begin`) —
so the production API construction path is unchanged and correct, and **no factory
injection is needed there**. With the default
factory the store's `self._begin()` (`conn.engine.begin`) opens a separate pooled
connection — correct but uses an extra connection unnecessarily in that context. **However, none of
the three affected store methods (`store_forecast`, `store_hindcast`,
`store_group`) is called from any API route** — so the "extra connection"
concern is moot for the current API surface. Moreover, the API's one write
endpoint (alert acknowledge) obtains a transactional connection via
`get_connection_rw` (`api/deps.py:26–29`, which calls `engine.begin()` and
**yields the resulting connection**), then passes that live connection directly to
`PgAlertStore(conn_rw)` (`api/routes/api_alerts.py:138` — line 112 is the
`conn_rw: sa.Connection = Depends(get_connection_rw)` DI parameter, not the store
construction), bypassing `get_stores`
entirely. This is **distinct** from the pattern this plan introduces: here the
store *receives* a connection and opens a *separate* `engine.begin()` transaction
internally — whereas the alert path's store executes on the caller-supplied
transactional connection. Neither pattern conflicts with the other. Do **not**
over-apply the `engine.begin()` pattern to API-path stores that do not need it. Since any future
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
store layer uses **synchronous SQLAlchemy with psycopg v3**
(`psycopg[binary]>=3.3.3`, `pyproject.toml:32`) — **not** psycopg2 and not asyncpg.
psycopg v3 has native COPY support via `conn.copy()` that runs on the **same**
synchronous SQLAlchemy connection, so a future server-side-COPY migration
(psycopg v3 `conn.copy()`, distinct from both psycopg2 and asyncpg APIs) can host
`COPY` **inside** the `engine.begin()` block without replacing the wrapper. Only if a
future migration instead chose the asyncpg `copy_to_table()` path (separate pool)
would the `engine.begin()` wrapper need replacement rather than extension.

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

The guard lives in the **caller loop** (not inside `_reconstruct_ensemble`) — the
simpler shape (see Step 7, review Finding 9) — inserted between
`rows_for_id = values_by_id.get(fid, [])` and the `_reconstruct_ensemble(...)` call
(`hindcast_store.py:124–125` in `fetch_hindcasts`, `:185–186` in
`fetch_hindcasts_by_station`). `_reconstruct_ensemble`'s existing
`if not rows: raise ValueError(...)` (`hindcast_store.py:224`) stays as a
defence-in-depth backstop that the caller guard prevents from ever firing on an
orphan. The valid headers in the same fetch still return.

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

### Step 1 — Add the injectable `transaction_factory` param to three stores

**Files**: `forecast_store.py`, `hindcast_store.py`, `station_group_store.py`

In each store's `__init__`, add an OPTIONAL keyword-only param
`transaction_factory: Callable[[], ContextManager[sa.Connection]] | None = None`
and, after the existing `self._conn = conn` line, assign
`self._begin = transaction_factory if transaction_factory is not None else
conn.engine.begin`. Import `Callable` from `collections.abc` and a
`ContextManager` alias (e.g. `from contextlib import AbstractContextManager as
ContextManager`, or `typing.ContextManager`). The default factory
(`conn.engine.begin`) preserves production behavior; existing callers
(`make_pg_stores`, `setup_production_stores`, `api/deps.py`) are unchanged.

### Step 2 — Run two-phase writes inside `self._begin()`

**Files**: same three stores

Replace the two sequential `self._conn.execute()` calls in each method with
a `with self._begin() as txn:` block, switching the two `self._conn.execute()`
calls to `txn.execute(...)`. With the default factory `self._begin()` is
`conn.engine.begin()` (production atomicity, separate from the AUTOCOMMIT
connection); with an injected factory it is a `SAVEPOINT` on the test connection
(Step 5a). Preserve all existing logic verbatim — row
construction, the `if rows:` / `if group.station_ids:` conditionals, and the
per-method dialect usage (`sa.insert` for `store_forecast`; `pg_insert`
*without* an `ON CONFLICT` clause for the `store_hindcast` header; `pg_insert`
**with** `on_conflict_do_update` / `on_conflict_do_nothing` for `store_group`,
preserved verbatim). For `store_hindcast` specifically, keep
`pg_insert(hindcast_forecasts).values(...)` **verbatim** (no ON CONFLICT clause;
there is no unique constraint to target — see Plan 040); only switch the connection
object from `self._conn.execute(...)` to `txn.execute(...)`. Do **not** simplify it
to `sa.insert` in this plan (defer any such cleanup to a future pass).

**Do NOT catch or wrap exceptions (D5 reversed).** Let any SQLAlchemy exception
propagate raw out of the `with` block — this matches every other SQL-backed
store, none of which catch or wrap write exceptions. The transaction context
(`engine.begin()` in production, the injected `SAVEPOINT` in tests) already
rolls the whole two-phase write back atomically on exception, so no wrapper is
needed. Wrapping in `StoreError` would (a) collapse
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

**Pre-migration dry-run counts (blast radius) — run and log before the
backup+migration.** Before taking the backup and running the destructive DELETEs,
run the analogous `count(*)` dry-run for each of the two tables the migration
actually DELETEs, so the operator sees exactly how many rows will be removed
(mirroring the manual `station_groups` dry-run below):
```sql
SELECT count(*) FROM forecasts f
WHERE NOT EXISTS (SELECT 1 FROM forecast_values fv WHERE fv.forecast_id = f.id);

SELECT count(*) FROM hindcast_forecasts hf
WHERE NOT EXISTS (SELECT 1 FROM hindcast_values hv WHERE hv.hindcast_forecast_id = hf.id);
```
Record (log) both counts before proceeding — they are the pre-DELETE blast radius
for the two in-migration tables and should be captured for the deploy record.

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

**`downgrade()` — irreversible no-op (mandatory).** This is a data-DELETE
migration; deleted rows cannot be restored, so `downgrade()` cannot undo it. Every
migration in this repo defines a `downgrade()` (e.g.
`alembic/versions/0027_station_water_level_datum.py:31`), so it must be present —
but as an explicit no-op with a comment, **not** omitted (an omitted/`raise`-ing
downgrade would error on `alembic downgrade`):

```python
def downgrade() -> None:
    # Irreversible data delete — orphan rows cannot be reconstructed.
    # Recovery is via DB restore + previous image tag (see docs/standards/cicd.md),
    # not via Alembic downgrade.
    pass
```

### Step 5 — Tests

#### 5a — Test isolation via injected SAVEPOINT transaction factory (MUST be done first)

**File**: the existing store test files (`tests/integration/store/test_forecast_store.py`,
`test_hindcast_store.py`, `test_station_group_store.py`, `test_forecast_summary.py`,
`tests/integration/test_model_onboarding_integration.py`, **and
`tests/integration/test_e2e_pipeline.py`** — see the e2e sub-section below). **No new
conftest, no committed session seeds, no autouse truncation, no `pytest_plugins`
cross-directory registration, and no migration of existing `_seed_*` FK-parent
calls** — the DI approach makes all of that unnecessary (see the D3/D5b SUPERSEDED
entries).

**CRITICAL — scope of "affected test" (Reviewer blocker). Apply as a
grep-mechanical rule, not by chasing exact per-file site counts.** "No migration of
existing `_seed_*` calls" means only that FK-parent **seeding** is unchanged. It does
**NOT** mean existing tests need no edits. The rule:

> **Every existing test that constructs one of the three stores AND calls
> `store_forecast` / `store_hindcast` / `store_group` must inject the savepoint
> `transaction_factory`** (otherwise the default `conn.engine.begin` factory commits
> on a separate connection and leaks across the session-scoped container). Find the
> sites with:
> ```bash
> grep -n 'store_forecast\|store_hindcast\|store_group\b' tests/
> ```
> Single-statement write methods (`store_group_model_assignment`,
> `add_station_to_group`, etc.) write directly via `self._conn` and do **NOT** need
> the factory.

If a store is constructed *without* the savepoint factory and a write method is called,
the default factory (`conn.engine.begin`) opens a **separate pooled connection** that
**commits outside the per-test rollback scope** — the committed write is never rolled
back at teardown and **contaminates every subsequent test in the session**. This is
the whole reason the DI param exists on the test side. A test that only constructs a
store to call `fetch_*` (a pure read) does **not** need the factory — the default is
harmless when no write runs. **But a test that calls a write method only to SEED
data** (even if it then tests a read/summary) DOES need the factory for that seeding
write.

**Affected files (informative pointer — verify by grep; counts may drift):**

| File | Store(s) |
|------|----------|
| `tests/integration/store/test_forecast_store.py` | `PgForecastStore` |
| `tests/integration/store/test_hindcast_store.py` | `PgHindcastStore` |
| `tests/integration/store/test_station_group_store.py` | `PgStationGroupStore` |
| `tests/integration/store/test_forecast_summary.py` | `PgForecastStore` |
| `tests/integration/test_model_onboarding_integration.py` | `PgStationGroupStore` |
| `tests/integration/test_e2e_pipeline.py` | `PgHindcastStore`, `PgStationGroupStore` (see e2e note) |

The list is a starting pointer, not an inventory. The RULE above is authoritative:
**any test that constructs one of the three stores and calls a write method — for the
subject under test OR merely to seed — gets the savepoint factory.**

**The problem the DI approach solves.** With the default factory, the two-phase write
would run on a *separate* pooled `engine.begin()` connection that commits outside the
test's rollback scope. That commit (a) leaks across tests (breaking isolation) and (b)
cannot see FK parents that a test seeded on the uncommitted `db_connection` transaction
(READ COMMITTED → `IntegrityError`). Both problems disappear if the write runs on the
**same** connection/transaction as the test's seed data.

**The fix — inject a `SAVEPOINT` factory bound to `db_connection`.** In each affected
test, construct the store with a `transaction_factory` that wraps the write in a
`SAVEPOINT` on the test's existing (transactional, rolled-back-at-teardown)
`db_connection`:

```python
from contextlib import contextmanager

@contextmanager
def savepoint_txn(conn):
    with conn.begin_nested():   # SAVEPOINT — works because db_connection is in a real
        yield conn              # transaction (NOT autocommit)

# store constructed with the injected factory:
store = PgForecastStore(
    db_connection,
    transaction_factory=lambda: savepoint_txn(db_connection),
)
```

`conn.begin_nested()` issues a real `SAVEPOINT` and, on success, releases it (the write
stays within `db_connection`'s outer transaction); on exception it rolls back to the
savepoint. Because the write now runs on the **same** connection/transaction as the
test's seed data:

- **(a) FK parents are visible.** Parents seeded via `db_connection` (stations, models,
  `model_artifacts`) are visible to the write with no commit required — there is no
  cross-connection invisibility, so no committed session seeds and no existing-test
  seed migration are needed. Existing tests keep their current per-test `db_connection`
  seeds unchanged.
- **(b) Nothing commits.** The outer `db_connection` transaction rolls back at teardown
  (the existing `db_connection` fixture already does this), so the store write — nested
  inside it via the savepoint — rolls back too. The existing rollback fixture provides
  full isolation. No `TRUNCATE`, no `_ATOMIC_WRITE_TABLES`, no cross-directory autouse
  fixture, no committed seeds.

**Why savepoints are safe HERE but NOT in production.** `conn.begin_nested()` requires
an open transaction block; `db_connection` is a genuine transactional connection
(`db_engine.connect()` + `conn.begin()` in `tests/integration/conftest.py`), so the
`SAVEPOINT` succeeds. This is exactly the case the plan notes does **not** hold on the
production AUTOCOMMIT connection (see `### Why conn.begin() on AUTOCOMMIT doesn't work`):
there is no outer transaction to nest within, so PostgreSQL rejects `SAVEPOINT`.
Production therefore uses the **default** `engine.begin` factory (a fresh transactional
connection), never a savepoint on the shared AUTOCOMMIT connection. The savepoint
factory is a **test-only** injection.

**A small helper.** Put the `savepoint_txn` context manager (and a
`savepoint_factory(conn)` convenience returning `lambda: savepoint_txn(conn)`) somewhere
shared for the store tests — a module-level helper in each affected test file, or a
small helper importable from `tests/integration/store/` — whichever keeps the tests
readable. This is a plain helper, **not** an autouse fixture, so it never forces a
`db_engine` container onto unrelated `tests/integration/` tests (the placement problem
the old design fought is gone).

**e2e tests use a different transaction shape — bind the savepoint to `conn`, not
`db_connection`.** `tests/integration/test_e2e_pipeline.py` does **not** use the per-test
`db_connection` rollback fixture. It opens its own `with engine.begin() as conn:` blocks
and seeds FK parents (stations, models, `model_artifacts`) on `conn` **uncommitted**,
then constructs and uses the stores on that same `conn`. Concretely, the step-1
onboarding block (`tests/integration/test_e2e_pipeline.py:187–225`) builds
`PgStationGroupStore(conn)` (`:196`) and `PgHindcastStore(conn)` (`:197`) and passes them
into `_run_onboarding`, which drives `store_hindcast`/`store_group` indirectly
(`_run_onboarding` → `onboard_model` → `_run_hindcast` → `run_station_hindcast` →
`hindcast_store.store_hindcast(...)`). `hindcast_forecasts` has FK constraints on
`stations.id`, `models.id`, and `model_artifacts.id` (`db/metadata.py:722–729`).

After this plan's change, if these stores use the **default** factory, `store_hindcast`
opens a **separate** `conn.engine.begin()` connection at READ COMMITTED that **cannot
see** the uncommitted FK parents on `conn` → `IntegrityError` (FK violation). The e2e
test would break on the very first `store_hindcast` inside any `engine.begin` block that
also seeds FK parents. **Fix**: in every e2e `engine.begin` block that constructs a
write-path store, inject a savepoint factory **bound to that block's `conn`** (which is
transactional, so `begin_nested()` is legal):

```python
with engine.begin() as conn:
    ...
    hindcast_store = PgHindcastStore(
        conn, transaction_factory=lambda: savepoint_txn(conn)
    )
    group_store = PgStationGroupStore(
        conn, transaction_factory=lambda: savepoint_txn(conn)
    )
```

This keeps the two-phase write on the **same** `conn` (FK parents visible; write is a
nested savepoint inside the block's transaction, released on success). Make
`savepoint_txn` importable from a shared store-test helper so the e2e file can reuse it.
Apply the same treatment to the other e2e `engine.begin` blocks that construct
`PgHindcastStore` (`:348`, `:391`, `:426`) if a write method is invoked in them.

**Isolation-holds test.** Add an explicit test proving isolation actually holds. Write
via an affected store method constructed with the savepoint factory (e.g.
`store_forecast` / `store_hindcast` / `store_group`), then confirm the row is **absent**
in a fresh connection / the next test because the outer `db_connection` transaction
rolled back at teardown. Because the write is nested in `db_connection`'s transaction,
a fresh `db_engine.connect()` read (a different transaction) never sees it, and after
teardown nothing remains — proving no state leaks between tests without any truncation.

#### 5b — New tests

**Files**: additions to existing store test files

All tests below construct the store with the **savepoint `transaction_factory`**
from Step 5a (`transaction_factory=lambda: savepoint_txn(db_connection)`), so the
two-phase write runs inside a `SAVEPOINT` on the test's own transactional
`db_connection`. FK parents seeded via `db_connection` are visible to the write,
and the whole write rolls back with the test. No committed session seeds are
required.

1. **Atomicity rollback test** (one per store): Construct the store with the
   savepoint factory, then force the values insert (the *second* store-issued write)
   to fail while the header has **already been written**, and assert the header is
   rolled back. **Do NOT use a naive `execute`-call counter** — it targets the wrong
   statement and produces a false-positive (Reviewer Finding 1, verified below).

   **Why a monkeypatch counter is WRONG here.** With the injected savepoint factory,
   `with self._begin() as txn:` calls `db_connection.begin_nested()`, whose
   `NestedTransaction.__init__` calls `connection._savepoint_impl()` →
   `do_savepoint()` → `connection.execute(SavepointClause(...))`
   (verified: `.venv/lib/python3.12/site-packages/sqlalchemy/engine/default.py:767–768`
   `def do_savepoint(self, connection, name): connection.execute(expression.SavepointClause(name))`,
   entered from `NestedTransaction.__init__` at `engine/base.py:2810`
   `self._savepoint = self.connection._savepoint_impl()`). So the `execute` call
   sequence on `db_connection` inside the store method is: **#1 = `SAVEPOINT`**
   (emitted by `begin_nested()`), **#2 = the header INSERT**, **#3 = the values
   INSERT**. A counter that fires on `calls["n"] == 2` therefore intercepts the
   **header** insert, not the values insert — the header is never written, so
   "assert header absent" passes trivially without proving orphan rollback. The
   real orphan scenario (header written → values insert fails → savepoint rolls the
   header back) is never exercised, and a broken atomicity fix would still pass.

   **Correct approach — per store.** The technique differs by store because the
   value-row IDs for `store_forecast` / `store_hindcast` are generated by a **bare
   `uuid4()` inside the store** (`_build_value_rows` at `forecast_store.py:~249`;
   the row-dict comprehension at `hindcast_store.py:~62`), so they are **not
   injectable** and a test cannot pre-seed a colliding PK without mocking `uuid4`.
   **Explicitly DROP the "pre-seed a colliding UUID PK" framing for
   `store_forecast` / `store_hindcast`** — it is impractical for those two. Use a
   statement-type-filter monkeypatch for them, and an FK violation for
   `store_group`:

   - **`store_forecast` and `store_hindcast`** — **statement-type-filter
     monkeypatch on the write connection**. Wrap the connection's `execute` so it
     raises `sqlalchemy.exc.IntegrityError` (or `OperationalError`) **only** when the
     executed statement is an INSERT into the *values* table — `forecast_values` for
     `store_forecast`, `hindcast_values` for `store_hindcast`. This makes the
     SECOND (values) insert fail while the header INSERT has already succeeded, so the
     `SAVEPOINT` rolls the header back — leaving the header absent. Filter by statement
     **shape**, not call count (the savepoint statement offsets any counter — see the
     WRONG note above). For example:

     ```python
     import sqlalchemy as sa

     def _fail_on_values_insert(stmt, *a, **k):
         if isinstance(stmt, sa.sql.dml.Insert) and getattr(stmt.table, "name", "") == "forecast_values":
             raise sa.exc.IntegrityError("forced values-insert failure", None, Exception())
         return _real_execute(stmt, *a, **k)
     ```

     (use `"hindcast_values"` for the `store_hindcast` test). Assert the raw
     SQLAlchemy exception propagates (**not** `StoreError` — D5 reversed) and the
     header row is **absent** within the test transaction.

   - **`store_group`** — simplest is a real **FK violation** on the members insert:
     pass a member whose `station_id` does **not** exist in `stations`, so the header
     UPSERT succeeds and the members INSERT raises `sqlalchemy.exc.IntegrityError`.
     (The members insert uses `on_conflict_do_nothing`, so a *duplicate* member does
     not raise — the non-existent FK is what forces the failure.) The group **must be
     non-empty** (see the non-empty caveat in the general note below) or the second
     execute is skipped entirely. Assert the group header is **absent** after the
     savepoint rollback.

   Sketch (`store_forecast`, statement-type-filter form):

   ```python
   store = PgForecastStore(
       db_connection,
       transaction_factory=lambda: savepoint_txn(db_connection),
   )
   # monkeypatch the write connection so the forecast_values INSERT fails
   #   (header INSERT succeeds first → savepoint rolls it back).
   with pytest.raises(sqlalchemy.exc.IntegrityError):
       store.store_forecast(fc)
   # assert the header was rolled back with the values insert:
   header = db_connection.execute(
       sa.select(forecasts.c.id).where(forecasts.c.id == fc.id)
   ).first()
   assert header is None
   ```

   Assert that (a) the raw SQLAlchemy exception propagates (it is **not** wrapped in
   `StoreError` — D5 reversed) and (b) the header row is **absent** — the `SAVEPOINT`
   rolled the whole two-phase write back *after* the header was written. Read the
   absence back on `db_connection` itself: because the write was nested in
   `db_connection`'s transaction and the savepoint rolled it back to before the header,
   the header is gone from `db_connection`'s view (and never committed anywhere).
2. **Atomicity success test** (one per store): Construct the store with the
   savepoint factory, call the method, and assert both the header and the values are
   visible **within the test transaction** — read them back via `db_connection`
   (the write committed the savepoint into `db_connection`'s outer transaction, so
   `db_connection` sees them). Then confirm they are gone after rollback: at teardown
   the outer `db_connection` transaction rolls back, so a subsequent test (or a fresh
   `db_engine.connect()` read) finds nothing — the isolation half of the assertion.
3. **Default-factory smoke test**: Assert that when **no** `transaction_factory` is
   injected, the store uses the default `conn.engine.begin` factory (e.g.
   `PgForecastStore(db_connection)._begin is db_connection.engine.begin`). This
   confirms production callers get the `engine.begin` behavior unchanged. (The old
   `conn.engine is db_engine` smoke test, which assumed the committed-connection
   design, is dropped.)

**MANDATORY caveat — `store_group` rollback/success tests must use a NON-EMPTY
group.** `store_group` only issues the members insert when `group.station_ids` is
truthy: `if group.station_ids: ... execute(...)` (`station_group_store.py:39`). A test
that constructs a group with `station_ids=frozenset()` skips the second execute
entirely — the two-phase path is never exercised, so neither the atomicity rollback
test (there is no failing second insert) nor the success test (there is nothing to
insert) verifies anything. **Both `store_group` tests MUST use at least one
`station_id`** in `station_ids`, seeded as an FK parent in `stations`.

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

**PINNED to the caller-guard shape (review Finding 9).** Add the
`if not rows_for_id: log.warning(...); continue` guard directly in the loop body of
**both** methods — **immediately after `rows_for_id = values_by_id.get(fid, [])`
and immediately before the `_reconstruct_ensemble(...)` call**, which are the two
consecutive lines in each loop. Insertion points:
`fetch_hindcasts` at `hindcast_store.py:124–125` and
`fetch_hindcasts_by_station` at `hindcast_store.py:185–186`. (The bulk
`value_rows = self._conn.execute(vq)...` fetch lines — `:114` and `:175` — are
**not** the insertion point; the guard goes inside the per-header loop, not at the
bulk fetch.) This is strictly simpler than the alternative of returning a sentinel
from `_reconstruct_ensemble` (which would force every caller to check the sentinel).
Leave `_reconstruct_ensemble`'s existing `if not rows: raise ValueError(...)` guard
(`hindcast_store.py:224`, inside the function defined at `:214`) in place as a
**defence-in-depth backstop** — the caller guard means it is never reached for an
orphan, but it stays as a hard invariant.
Reuse the module-level `log = structlog.get_logger(__name__)` added in Step 3.

**Note (review Finding 9):** `station_id` is a direct method parameter in *both*
loops — `fetch_hindcasts(self, station_id: StationId, …)` (`hindcast_store.py:83`)
and `fetch_hindcasts_by_station(self, station_id: StationId, …)`
(`hindcast_store.py:154`) — so the `station_id=` structlog field in the WARNING is
unambiguous in each. Use the method-level `station_id` parameter; there is no need
to read it back off the header row. Do **not** change the
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
