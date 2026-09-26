---
status: DRAFT
created: 2026-09-26
plan: 404
title: Keep the member and group forecasts that QC rejects — in their own record, never as a forecast
scope: Record every station (member) or group-station forecast whose forecast QC verdict is `qc_failed` — its values, units, cadence and every parameter's flags — in a separate rejected-forecast record, not in the `forecasts` table, and serve it through one REVIEW-gated `/api/v1` route, so the flow map can show what forecast QC rejected and why. Where Plan 341's publication gate is active for a tenant, a reviewer token sees which rule rejected a forecast but not its values. The forecast cycle's behaviour is otherwise unchanged — fallback, alerting, combination, model state, re-runs (Plans 327/328), the freshness heartbeat and every reader of `forecasts` see exactly what they see today. NOT any change to forecast QC rules, thresholds or verdicts; NOT combined forecasts (already stored failed, Plan 253 OD-1); NOT hindcasts; NOT backfilling earlier rejections; NOT publishing or alerting on a rejected forecast.
risk: high   # new table + migration, live-database writes, external-facing route (docs/workflow.md § High-risk work)
depends_on: [401, 402]
blocks: []
related: [251, 253, 327, 328, 340, 341]
open_decisions: []
closed_decisions: [D1, D2, D3, D4, D5, D6]   # owner, 2026-09-26
source: 2026-09-26 — found by the round-5 review of Plan 402 — a rejected member or group forecast is dropped, so the flow map can never show a forecast-QC rejection except on a combined forecast. Owner, 2026-09-26: a separate follow-on plan; a separate record rather than the forecast table; values withheld from reviewer tokens where Plan 341's gate is active.
---

# Plan 404 — keep the member and group forecasts that QC rejects

## Status

**DRAFT — HIGH RISK — end-of-run save (D5), its hard overall limit (D6) and review corrections folded, not re-reviewed.** A new table, live writes from
the forecast cycle and an external-facing route (`docs/workflow.md` § High-risk work): the ordinary
Claude + Codex pair on the current text, plus one owner-commissioned review before READY and again
before the implementation PR. Depends on Plan 401 (the REVIEW gate) and Plan 402 (the typed flag
model and the committed map contract). All decisions (D1-D6) are closed.

## Why this exists

The flow map (Plan 402) lets a reviewer — DHM first — judge whether QC thresholds are right. For
forecasts it cannot: a member or group-station forecast that fails QC is thrown away, so the three
rejecting forecast rules (`negative_value`, `range_check`, `quantile_crossing`) appear never to
fire. Their silence says nothing about the thresholds; the evidence simply does not exist.

## What is measured (origin/main, 2026-09-26)

- **Station (member) path.** `services/run_station_forecast.py:587-599` stops at the **first**
  parameter whose worst flag is `qc_failed` and returns `AssignmentFailure(QC_FAILED)`, which carries
  only `cause` and `detail` (`:116-118`); the forecast object is never built, later parameters are
  never checked, nothing is stored, and the cycle tries the next model.
- **Three ways the cycle obtains member outcomes**: `run_all_station_forecasts_per_track`
  (`flows/run_forecast_cycle.py:3039`), `run_all_station_forecasts` (`:3429`), and — in PRIMARY mode
  on the legacy path — `run_station_forecast` (`:3350-3357`), which returns only
  `StationForecastResult | None` (`services/run_station_forecast.py:888-907`) and **discards**
  `MultiModelForecastResult.failed_models`.
- **Exits before persistence.** On the per-track path a cross-cycle mismatch `continue`s
  (`flows/run_forecast_cycle.py:3062-3076`) **before** the `all_models_failed` check (`:3078-3090`);
  the other paths exit on `all_models_failed` (`:3379`, `:3451`). In PRIMARY mode every assignment
  runs but only the selected model's forecast is stored (`:3349-3408`).
- **Group path is per station.** `run_group_forecast` returns
  `dict[StationId, StationForecastResult]` (`services/run_group_forecast.py:426-445`);
  `_build_station_result` returns `None` for a failing station only (`:287-318`); siblings keep their
  results (test `tests/unit/services/test_run_group_forecast.py:838`). The flow consumes it at
  `flows/run_forecast_cycle.py:3715`. `docs/architecture-context.md:90,116` describe it as a
  whole-batch drop — imprecise.
- **Heartbeat.** `FORECAST_FRESHNESS` is CRITICAL when `force_critical` or `forecasts_stored == 0`
  (`flows/run_forecast_cycle.py:928-934`); a fatal group forecast-store failure forces CRITICAL
  (`:3779-3786`); an explicit-cycle replay emits no freshness record (`:928-929`).
- **Why not the `forecasts` table** (the first design, rejected after review): a stored rejected row
  would make a later passing re-run a Plan 327 "row 3" refusal (`services/forecast_retry.py:76-82`)
  — dropped from alerting, and fatal on the group path (`flows/run_forecast_cycle.py:3744-3786`);
  it would count toward the heartbeat; and every reader that takes a stored row as "the" forecast
  (Forecast Lab reads and its cycle marker, `/api/v1`, the dashboard, scripts) would need an
  exclusion. A separate record touches none of these.
- **Production store wiring.** The flow's store bundle is built in `flows/_db.py::make_pg_stores`
  (`:23-73`), which `setup_production_stores` (`:78-85`) calls with one shared AUTOCOMMIT connection
  and an engine created with `pool_pre_ping=True`. **Writers that matter take their own pooled
  transaction**: `PgForecastStore`, `PgHindcastStore` and `PgStationGroupStore`
  accept a `transaction_factory` defaulting to `conn.engine.begin`, and `AuditedWriter` a `begin`
  (`store/audited_writer.py:65`; the forecast store's default at
  `store/forecast_store.py:122-136`); other stores' reads and writes go through the shared
  connection. No store sets a lock or statement timeout today. The API opens exactly one connection
  per request, shared by auth and the handler (`api/deps.py:26-37`).
- **Plan 341's boundary** (`docs/plans/341-chwrr-forecast-publication-api.md`): a reviewer token
  sees only published values; Plan 402's two REVIEW routes are outside the publication gate because
  they carry no forecast values, while this route carries them and follows D4 (`341:84`);
  `qc_flags[].detail` is gated; a `qc_failed` forecast is never publishable.

## Owner decisions

### D1 — keep rejected member and group-station forecasts. **⚖️ CLOSED — owner, 2026-09-26.**

### D2 — in a separate record, not the `forecasts` table. **⚖️ CLOSED — owner, 2026-09-26.**

A rejected forecast is never a candidate for alerting, combination, publication, re-run comparison,
model state or the freshness heartbeat.

### D3 — group rejections are per station. **⚖️ CLOSED — follows from the code.**

### D4 — where Plan 341's gate is active, reviewer tokens see the rule, not the values. **⚖️ CLOSED — owner, 2026-09-26.**

For a tenant where Plan 341's publication gate is active, a **reviewer** service token receives the
full response item (T3) with `withheld: true`, `values: null` and every flag's `detail: null` —
every other field, including `id`, `model_artifact_id`, `group_id` and `qc_status`, is returned; only
the values and flag `detail` are withheld. **Admin** tokens receive everything
(owner, 2026-09-26, as PR #316 recorded in Plan 341's *Rejected-record visibility*). Plan 341 also
specifies that its named hydrologist with a current station `review` grant may read the full
diagnostic record but cannot publish its ID; this human path is added when Plan 341 exists. Where
the gate is not active (e.g. the Swiss deployment today), reviewer and admin tokens receive
everything.

**How "withheld" looks on the wire:** `values` and each flag's `detail` are optional in the response
model from the start, and each item carries `withheld: bool`. A gated reviewer item has
`withheld: true`, `values: null` and `detail: null`, so a map can tell "withheld" from "no
detail". Switching the gate on therefore changes no response shape.

**One named check decides "gated"**: a single predicate, `publication_gate_active(tenant_id)` (the name Plan 341 cites),
that answers **no** until Plan 341 provides its tenant activation switch, and then asks that switch.
The route consults nothing else. **Both landing orders are covered:** if Plan 341 lands first, T3
wires the predicate to its switch and classifies this route in 341's route inventory; if this plan
lands first, Plan 341 already records that its activation wires this predicate and applies D4
(`341:84`, `:114`); T4 only checks those lines are present.

### D5 — rejections are saved once, at the end of the run. **⚖️ CLOSED — owner, 2026-09-26.**

The cycle collects each rejection in memory the moment it happens (so no early exit loses it) and
writes them all in **one** short, time-limited transaction as the run's last step: after the
forecasts, alerts and whichever `FORECAST_FRESHNESS` heartbeat the run emits — and also when the run
is aborting, including a `StoreError` exit that emits no heartbeat (heartbeat behaviour is
unchanged). A locked, slow or unreachable database can therefore never delay forecast delivery,
alerts or the heartbeat, and delays the run's own completion by at most D6's limit. A run with no
rejection makes no capture write at all. Trade-off accepted: if the run crashes before that point,
that run's rejections are lost; the crash itself is still logged.

### D6 — the save has a hard overall limit. **⚖️ CLOSED — owner, 2026-09-26.**

The whole save — resolving the host, connecting, the timeout settings, the inserts and the commit —
runs in a background (daemon) thread, and the flow waits for it at most
`REJECTED_CAPTURE_DEADLINE_S = 10` seconds. If it has not finished, the flow sets a shared
`threading.Event`, logs `rejected_forecast.write_timed_out` (warning; `attempt_id`, the buffered
assignment count) once, stops waiting and finishes normally. The store's write checks that event
inside its transaction, after the last INSERT and immediately before `COMMIT`, and raises to roll
back if it is set (T1), so a save that is merely slow never lands
late; only a `COMMIT` already sent when the deadline passes has an **unknown outcome** — and because
the batch is atomic, that attempt's rows are then either all present or all absent. The thread never
logs: it records its outcome (success or the exception) in a holder that the flow reads after the
join, so all capture logging happens on the flow thread with its structlog context. The per-step
limits in T1 stay as the inner bound; D6 is the only one that holds for every kind of stall.

## Record and route contract

**Table** `rejected_forecasts`, append-only:

| column | notes |
|---|---|
| `id` | UUID primary key |
| `attempt_id` | UUID, one per cycle execution — distinguishes a Plan 327 resume or Plan 328 retry of the same cycle |
| `station_id` | FK `stations` |
| `model_id` | FK `models` |
| `model_artifact_id` | FK `model_artifacts`, nullable |
| `group_id` | FK `station_groups`, null for a member forecast |
| `issued_at` | the cycle's issue time; the route's `start`/`end` filter on it |
| `parameter`, `units` | as on `forecasts` |
| `representation` | CHECK `IN ('members', 'quantiles')` |
| `time_step_seconds` | NOT NULL (every new rejected row has a known step; `forecasts` allows NULL only for legacy rows) |
| `values` | JSONB `series`, keyed by member id or quantile level, each an array of `[valid_time, value]` pairs — each series keeps **its own** timestamps, because `ForecastEnsemble` allows members with different timelines (`types/ensemble.py:42`), which a shared `valid_times` array (as in `EnsembleResponse`) would lose. The **raw** ensemble, as `forecasts` would store it — for water level, not the datum-shifted copy QC checked. Non-finite numbers use the evidence encoding `{"nonfinite": "nan" \| "inf" \| "-inf"}` (`services/forecast_evidence.py:76-77`), so a rejected ensemble containing them is stored losslessly |
| `qc_status` | CHECK over the four values; the parameter's own verdict, **taken from the capture payload, never re-derived from the flags** (`worst_qc_status([])` is `QC_PASSED`): `qc_failed`, `qc_suspect`, `qc_passed`, or `qc_unchecked` when its QC or datum handling errored after an earlier failure — so an errored parameter can never read as a pass |
| `qc_flags` | JSONB, the four-key flag shape, holding the parameter's flags — `[]` only for a passed or unchecked parameter |
| `recorded_at` | server default `now()` |

Index `(station_id, issued_at)`. A run's rows are written in **one transaction** (all or
none, D5). Every parameter of a rejected assignment is recorded, each with its own `qc_status` and flags: QC now runs on **all** parameters before the verdict, which is unchanged (any failed
parameter still rejects the assignment).

**Route** `GET /api/v1/stations/{id}/rejected-forecasts?[start=&end=][&model_id=][&limit=&offset=]`,
REVIEW-gated (Plan 401), station-scoped; `start`/`end` filter `issued_at`, optional, default the last
7 days ending at request time, `end` exclusive (Plan 402's query conventions); paginated with its own
ceiling (`limit` default 20, ≤ 50, since each item carries a full ensemble), every item carrying `attempt_id`, `recorded_at` and `withheld`, ordered newest first,
`(issued_at DESC, recorded_at DESC, id DESC)` — like `/stations/{id}/forecasts`
(`store/forecast_store.py:484`) — non-finite values in the same encoding, flags typed as Plan 402's
`QcFlagResponse`; values and `detail` withheld per D4. Added to Plan 402's committed map contract and its explicit route list.

## Tasks

Every code task carries the Task Exit Gate (`docs/workflow.md` § Task Exit Gate).

### T1 — the table and its store

**Outcome:** the table exists, append-only, with its store Protocol and implementation, wired into
production for both the flow and the API.

**In:** `db/metadata.py`; a new alembic migration (next free revision at implementation time);
`tests/unit/db/test_alembic_head_release_b.py` (the head pin); the store Protocol in
`protocols/stores.py` and its implementation under `store/`; the fake in `tests/fakes/fake_stores.py`;
`flows/_db.py::make_pg_stores` and `api/deps.py`. The store follows the existing writer pattern of an
injectable `transaction_factory`, but its production factory is **not** the shared engine's
`begin`: acquiring a pooled connection happens before any `SET LOCAL`, and replacing a dead one
waits up to psycopg's default connect timeout of 130 s (`psycopg/conninfo.py:23`; the production
URL sets none). The store module therefore provides one builder,
`rejected_capture_transaction_factory(url: sa.URL)`, which creates a dedicated engine on that URL
with `poolclass=NullPool` and `connect_args={"connect_timeout": 5}` — one fresh, bounded connection
per run, so no disposal is needed; `make_pg_stores` calls it with `conn.engine.url` (the `URL`
object, never `str(url)`, which masks the password) and is its only caller. The store constructor's
`transaction_factory` parameter has no default value, so every caller passes it explicitly:
`api/deps.py` passes `None` (the API reads on its request connection and never writes — its role
has SELECT only), and a write on a store built with `None` raises `ConfigurationError`. The transaction begins with `SET LOCAL lock_timeout = '2s'` and
`SET LOCAL statement_timeout = '5s'` (constants in the store). The shared engine is unchanged.
One write stores one run's whole batch, all rows or none (D5): the Protocol method is
`write_batch(entries: Sequence[RejectedForecastEntry], *, abandon: threading.Event) -> None`; it
builds the rows (value encoding included), inserts them, then — still inside the transaction — checks
`abandon` and raises `CaptureAbandonedError` (new, in `exceptions.py`) if it is set, so the context manager rolls back (D6). The
fake store honours `abandon` the same way. The API reads through its request connection; the
timeouts belong to the worker's write path only. The migration also adds a **role-independent
append-only guard** — triggers rejecting UPDATE, DELETE and TRUNCATE, as migration 0057 does for the
evidence tables (`alembic/versions/0057_forecast_evidence.py:82-96`). The flow parameter
`rejected_forecast_store` on `run_forecast_cycle_flow` (`flows/run_forecast_cycle.py:2323-2370`),
read from the production bundle when stores are not injected (`:2405-2428`) — an injected caller that
omits it gets no capture, which leaves the ~84 existing injected test calls unchanged; a flow
parameter `id_gen: object | None = None`, resolved to `uuid4` in the body beside `clock`/`rng`
(`flows/run_forecast_cycle.py:2340-2341`, `:2399-2402` — a callable default is not a valid Prefect
deployment parameter), from which the flow mints `attempt_id`; grants in `docker/bootstrap-roles.sql`
(worker INSERT; API SELECT; no UPDATE/DELETE/TRUNCATE for either); `docs/spec/types-and-protocols.md`,
`docs/spec/database-schema.md`, and the grant matrix in `docs/conventions.md` § Service users.

**Out:** any change to `forecasts`.

**Pre-change:** with the Pg store implemented but no migration, a store round-trip against Postgres
fails with `UndefinedTable`.

**Verification:** `uv run pytest tests/unit/db/test_alembic_head_release_b.py tests/integration/db/ tests/integration/store/test_rejected_forecast_store.py` — store round-trips use the `transaction_factory` savepoint seam of the integration fixture (as `tests/integration/store/test_forecast_store.py:66` does), since that fixture rolls back an uncommitted outer transaction (`tests/integration/conftest.py:46-51`); against PostgreSQL: with another session holding a conflicting lock on `rejected_forecasts` (committed seed rows, explicit cleanup), a write fails with `lock_not_available` (SQLSTATE `55P03`) within the lock timeout; UPDATE, DELETE and TRUNCATE are refused by the guard even for the table-owning role; round-trip of an ensemble and a quantile row, including a single-step forecast, a `qc_suspect` sibling parameter with its flags, an out-of-set `qc_status` rejected by the CHECK, members with different timelines, a run's batch written all or none (a forced failure on a later assignment's insert leaves none of the run's rows; the `write_failed` logging is T2's), and one containing NaN, `inf` and `-inf`, preserving units, cadence and every value; `write_batch` with `abandon` set before its commit leaves no rows; one read case on the savepoint seam covering the `station_id`/`issued_at` window with `end` exclusive, the `model_id` filter, the order `(issued_at DESC, recorded_at DESC, id DESC)`, `limit`/`offset` and decoding of the non-finite encoding; upgrade and downgrade on an empty table; `tests/integration/db/test_role_bootstrap.py` extended: the worker can INSERT, the API can SELECT, and neither can UPDATE, DELETE or TRUNCATE (the precedent at `:381`); the production store bundle contains the new store, built through `rejected_capture_transaction_factory`, whose engine uses `NullPool` and `connect_timeout == 5`.

### T2 — capture every rejection, change nothing else

**Outcome:** every rejected member assignment and rejected group station is recorded — in
multi-model and PRIMARY modes, on the per-track and legacy paths, on the group path, when every model
fails, and when a cross-cycle mismatch skips the station; the cycle otherwise behaves exactly as today.

**In:**
- `services/run_station_forecast.py` — QC every parameter before the verdict, logging
  `run_station_forecast.qc_failed` (warning; `station_id`, `model_id`, `parameters` = the failed
  parameters) once per rejected assignment — likewise `run_group_forecast.qc_failed` (plus
  `group_id`) once per rejected station — and `run_station_forecast.qc_parameter_unchecked` /
  `run_group_forecast.qc_parameter_unchecked` (error; `station_id`, `model_id`, `parameter`,
  `error`) for a parameter whose block errored. Once a parameter has failed, an error anywhere in a remaining parameter's
  block — the datum shift, `forecast_skipped_rules`, QC or `add_forecast_datum_details`
  (`services/run_station_forecast.py:571-587`, `services/run_group_forecast.py:289-307`) — is logged,
  records that parameter as `qc_unchecked`, and leaves the verdict at `QC_FAILED` — it never turns the
  cause into `UNEXPECTED_EXCEPTION` nor escapes the group call to drop the whole group.
  `AssignmentFailure`
  gains an optional rejected payload: artifact, issued time and, **per parameter**, `parameter`,
  `representation`, `units`, `time_step_seconds`, the raw values, the flags and an explicit
  `qc_status`;
  `run_station_forecast` (the PRIMARY wrapper) returns the rejected payloads alongside its result.
- `services/run_group_forecast.py` — QC every parameter before the verdict in
  `_build_station_result` (`:287-318`, which today returns at the first failing one), and a new return
  shape carrying results **and** per-station rejected payloads (e.g. a `GroupForecastOutcome`). If a
  later station raises after an earlier one was rejected, the call raises a `GroupForecastError`
  carrying the rejected payloads gathered so far **and the original exception**. A new handler in
  the flow, placed before the existing generic one, adds the payloads to the buffer and then takes the existing
  log-and-skip branch (`flows/run_forecast_cycle.py:3822-3838`) with the original's message, so the
  `error` field and the cycle's `errors` text are unchanged. A `StoreError` is never wrapped: no
  store call happens inside the per-station loop (`services/run_group_forecast.py:597-635`; the
  artifact fetch at `:482-510` precedes it), so one cannot follow a rejection, and any `StoreError`
  still propagates to the existing dedicated branch (`:3820`) exactly as today. The caller at
  `flows/run_forecast_cycle.py:3715` and `tests/unit/flows/test_run_forecast_cycle_group_fi_resolver.py`.
- `flows/run_forecast_cycle.py` — mint one `attempt_id` where `id_gen` is resolved; **collect** the
  payloads in an in-memory buffer **immediately after** each of `run_all_station_forecasts_per_track`,
  `run_all_station_forecasts`, `run_station_forecast` and `run_group_forecast` returns, before the
  cross-cycle preflight, the `all_models_failed` checks or any other exit; and **write the buffer once**
  (D5), in the flow's outermost `finally` (`:3940`, closing the `try` opened after `flow_t0` at `:2371`),
  which runs after whichever heartbeat the run emitted (`:2617`, `:1924`, `:2907`, `:3779`, `:3893`)
  — or none, on a `StoreError` exit — so an aborting run writes it too. Only `rejected_buffer = []` is
  bound before that `try` (the `rejected_forecast_store` parameter is always bound, and only
  reassigned inside); each buffered entry carries its `attempt_id`, so the `finally` reads only the
  buffer and the store (nothing possibly unbound under pyright strict); it writes only when the store
  is set and the buffer is non-empty, on a daemon thread joined for at most
  `REJECTED_CAPTURE_DEADLINE_S` (D6), read at call time — never bound as a default argument — so a
  test can shorten it. The write is **best-effort**: a failure the thread reports logs `rejected_forecast.write_failed` (warning; `attempt_id`,
  `station_id`, `model_id`, `group_id`, `error`; once per rejected assignment), never aborts the
  cycle, is never counted in `forecasts_stored`, and never goes through the group path's fatal store
  call. A missing store in the production bundle is an error at setup (T1), not a silent no-op.
  Because the single write is the run's last step, on its own bounded connection and transaction
  (T1), a blocked or broken capture write can neither delay the forecasts, alerts or heartbeat nor touch
  the shared connection, and D6 bounds how long it can hold up the run's end. The whole flow-side capture — starting the thread, joining it, and
  logging — sits in one `except Exception` that logs `write_failed`, so nothing raised there can
  replace an in-flight exception or turn a returned result into a raise. It is the one deliberate
  broad `except` on this path: T4 records the carve-out in `docs/conventions.md` § Flow-level
  strategy. Where `attempt_id` is minted, the flow binds it into the structlog context
  (`structlog.contextvars.bind_contextvars`, as it already does for `station_id` at `:3021`) and
  unbinds it after the capture logging in the `finally`, so both `qc_failed` events,
  `write_failed` and `write_timed_out` carry the same `attempt_id` as the stored rows.
- `docs/spec/types-and-protocols.md` — `AssignmentFailure`, `MultiModelForecastResult`, the
  `run_station_forecast` return, and a new entry for the group outcome.
- Tests broken by the new `run_station_forecast` return, updated: `tests/integration/test_e2e_pipeline.py`,
  `tests/unit/services/test_run_station_forecast_fanout.py`,
  `tests/unit/services/test_unchecked_observation_policy.py`, the direct calls in
  `tests/unit/services/test_run_station_forecast.py`, and — for the new group return shape — the
  calls through `_call_run_group_forecast` in `tests/unit/services/test_run_group_forecast.py`.

**Out:** QC rules and verdicts; `forecasts`; alerting; combination; model state; hindcasts; Plan 340
evidence capture (a rejected forecast is not an issued forecast).

**Pre-change:** with the store injected (T1) but no capture code, a flow test where a member model's
ensemble trips `negative_value` finds the rejected-forecast store empty — the fault itself, not an
import or argument error.

**Verification:** `uv run pytest tests/unit/flows/test_run_forecast_cycle.py tests/unit/flows/test_run_forecast_cycle_resume.py tests/unit/flows/test_run_forecast_cycle_group_fi_resolver.py tests/unit/services/test_run_station_forecast.py tests/unit/services/test_run_station_forecast_per_track.py tests/unit/services/test_run_group_forecast.py tests/integration/test_e2e_pipeline.py tests/unit/services/test_run_station_forecast_fanout.py tests/unit/services/test_unchecked_observation_policy.py tests/integration/flows/test_forecast_cycle_rejected_capture_pg.py` — cases:
- a rejected member is recorded with flags for **every** parameter, and the next ordinary model's forecast is stored as today; named fallback models stay out of combination; the rejected ensemble is absent from alert inputs and no model state is stored for it;
- PRIMARY mode on the legacy path records the rejection of every assignment that ran;
- a station where every model is rejected records every rejection and still reports `all_models_failed`;
- a per-track station with a cross-cycle mismatch **and** a rejected model records the rejection and still skips the station;
- a mixed group records only the failing station, with flags for **every** parameter; the sibling's forecast is stored as today;
- an error in a later parameter's block after an earlier one failed keeps the verdict `QC_FAILED`, records that parameter as `qc_unchecked` (never as a pass), on the member path and the group path, where the siblings keep their results;
- in a group, station A rejected then station B raising an ordinary error: A's rejection is recorded and the group is skipped exactly as today, with the same logged error text;
- the stored `qc_status` of an unchecked parameter is `qc_unchecked` even though its flags are `[]`;
- a rejected ensemble whose members have different timelines round-trips with each member's own timestamps;
- a rejected assignment with one failed and one suspect parameter records both, each with its own status and flags, on the member and the group path;
- every row written by one flow execution carries the same `attempt_id`, and the run's `qc_failed`, `write_failed` and `write_timed_out` events carry that same `attempt_id`, so the three can be joined;
- a row-building failure in the capture on an aborting run still re-raises the original error, and on a normal run still returns the original result;
- a rejected ensemble containing NaN or `inf` is captured, not dropped by the best-effort write;
- a re-run of the same cycle appends a second attempt and leaves Plan 327's classification of the stored forecasts unchanged;
- a failed write of the record leaves the cycle's result and heartbeat unchanged;
- the capture write is the last database write of the run: it happens after the forecasts, the alerts and the `FORECAST_FRESHNESS` record, and also runs when the flow aborts;
- a member rejection buffered before a later group `StoreError`: the capture runs once, the original `StoreError` propagates, and heartbeat behaviour is unchanged (no heartbeat on that path);
- with the capture factory from `rejected_capture_transaction_factory` pointed at a local socket that accepts and never answers, the flow completes within the connect timeout plus a margin, logs `rejected_forecast.write_failed`, and its result is unchanged;
- (D6, in `test_forecast_cycle_rejected_capture_pg.py`, driving the real Postgres store) with a capture factory that connects and then blocks on a test-owned `threading.Event` (stalls after the connection succeeds), the flow completes within the deadline plus a margin (the deadline shortened for the test), logs `rejected_forecast.write_timed_out` once and no `write_failed`, and returns its original result — or re-raises its original error on an aborting run; the test then releases the event and, once the thread ends, asserts nothing was committed and nothing more was logged (the event is also released in teardown, so no thread outlives the test);
- a run with no rejection opens no capture connection;
- (integration, `test_forecast_cycle_rejected_capture_pg.py`, PostgreSQL; only the parent seeds — stations, models, artifact — commit, with explicit cleanup; the forecast, pipeline-health and model-state stores use the rolled-back `db_connection` and its savepoint seam, because committed forecasts create undeletable evidence rows (`0057_forecast_evidence.py:82-96`, `0059_forecast_preservation.py:72-156`); only the rejected-forecast store uses a real committed transaction) a cycle run while another session holds a conflicting lock on `rejected_forecasts`: the capture write fails with `lock_not_available` and logs `rejected_forecast.write_failed` once per rejected assignment, while the run's successful forecasts and its `FORECAST_FRESHNESS` record were already written and the run's result is unchanged;
- heartbeat: a cycle whose every assignment is rejected is CRITICAL; an explicit-cycle replay emits nothing; a genuine group forecast-store failure still forces CRITICAL;
- a cycle with no rejection stores the same forecasts as today, compared with ids and timestamps normalised.

### T3 — the review route

**Outcome:** `GET /api/v1/stations/{id}/rejected-forecasts` serves the record per D4.

**In:** the route next to Plan 402's REVIEW routes; response models reusing `QcFlagResponse`; the
route-matrix entry (REVIEW); Plan 402's contract generator — its per-route role descriptions and its
REVIEW 403 set and its 400 set (the route parses query values) gain this third REVIEW route; Plan 402's map contract file and explicit route list, with its version bumped per Plan 402 D14 (a new route is additive: minor); the consumer page
`docs/spec/api-v1-review.md` (rejected forecasts live here; values withheld where Plan 341's gate is
active; every parameter of a rejected assignment is returned — including `qc_passed` and `qc_suspect`
ones — each with its own status; one rejection is the group `(attempt_id, station_id, model_id,
group_id)`; `qc_unchecked` is not a pass; the query conventions above); `docs/conventions.md` § API routes; `docs/standards/security.md` (the REVIEW-class route
list and D4's rule beside Plan 402's D13 entry); `docs/touchpoint-maps.md` (API paragraph). The gate
predicate (D4). If Plan 341's route inventory and switch exist on the base branch, wire the
predicate to the switch, classify this route there as a REVIEW diagnostic whose values follow D4,
and extend its test. The route accepts a reviewer service token with D4 redaction, an admin token
with full data, or, after Plan 341's human principal exists, a named human with a current station
`review` grant and full diagnostic data. Deny consumers, revoked humans and out-of-scope humans.
If this plan lands first, Plan 341 T3 adds the human authorization branch when it lands; if 341
lands first, this T3 adds it here. Neither landing order permits a rejected-record ID on a forecast
publication route.

**Response item** (every field, ungated): `id`, `attempt_id`, `recorded_at`, `station_id`,
`model_id`, `model_artifact_id`, `group_id`, `issued_at`, `parameter`, `representation`, `units`,
`time_step_seconds`, `qc_status`, `qc_flags`, `values`, `withheld`. A gated reviewer item is the same
item with `withheld: true`, `values: null` and every flag's `detail: null`; nothing else is removed.

**Out:** any change to the forecast list or detail routes.

**Pre-change:** a request to the route returns 404.

**Verification:** `uv run pytest tests/unit/api/test_api_rejected_forecasts.py tests/unit/api/` — reviewer → 200 in scope with values, flags and `withheld: false` (predicate answers no) and 404 for an out-of-scope station; admin → 200 for any existing station; reviewer and admin → 404 for an unknown station; no token → 401; `model_id` and `start`/`end` filter, the default window is the last 7 days and `end` is exclusive; with the predicate forced to yes, reviewer → the full item with `withheld: true`, `values: null` and every `detail: null`, admin → everything; items come newest first, `(issued_at DESC, recorded_at DESC, id DESC)`; non-finite values round-trip in their encoding; `limit` above 50 is refused; consumer → 403; `limit`/`offset` paginate; the drift test covers the route. Where Plan 341's human principal is present, test a named human with a current station `review` grant → full record, and a revoked or out-of-scope human → denial; if 404 lands first, Plan 341 T3 owns the same tests when it adds that principal. Where Plan 341's publication routes exist, a `rejected_forecasts` id submitted to publish or replace is refused, leaving the selection and decision ledger unchanged.

### T4 — documents

**Outcome:** the specification and architecture describe the rule, and the group wording is exact.

**In:** `docs/spec/types-and-protocols.md` (Flow 1 step 1.10, `:797`), `docs/architecture-context.md:90,116`
(rejections are recorded separately; group rejection is per station), the same whole-group wording
in `docs/standards/orchestration.md:176-187`, `docs/standards/logging.md` (every event of T2 with
its level and kwargs: the two `qc_failed` events, now once per assignment with the failed
`parameters`; the two `qc_parameter_unchecked` events; `rejected_forecast.write_failed`; `rejected_forecast.write_timed_out`), `docs/touchpoint-maps.md`
(the forecast-cycle paragraph and the freshness bullet: rejected records are not forecasts; and
`:412`'s "a mismatch skips ALL writes for that station this cycle"), the same cross-cycle contract in
`docs/architecture-context.md:113` and the flow comment at `flows/run_forecast_cycle.py:3057-3060` —
each reworded to "no forecast or model-state write; the rejection is collected before the preflight
and saved at the end of the run (Plan 404)" (`docs/standards/logging.md:300` already says "no forecast or state write"
and stays true), and
the sentence Plan 402 puts on the consumer page `docs/spec/api-v1-review.md` (`402:475-476`: "a failed
member or group forecast is never stored"), which becomes "is never stored as a forecast; it is
recorded on the rejected-forecast route"; and
— if Plan 402's reply to the map session has not been sent yet — its exit-gate step 4 wording
(`402:570-571`), so that the reply names this route instead of saying rejected forecasts are not
stored. Plan 341 already carries this plan's facts
(this REVIEW route carries values, withheld from reviewer tokens on a gated tenant; its activation
wires D4's predicate; rejected member/group forecasts never enter `forecasts` — `341:84`, `:114`);
T4 checks those lines are still there and does not restate them. `docs/conventions.md` § Flow-level
strategy (`:285`) records the one carve-out: the end-of-run capture write catches any exception and
logs it, because it is optional diagnostics written after the run's real outputs.

**Out:** archived Plan 253.

**Pre-change:** N/A — documentation.

**Verification:** bounded inspection — each In-listed location is changed in the branch diff; no document still says a rejected member forecast leaves no record; and, if 341 had not landed, Plan 341 states by content that this route carries values and that its activation applies D4.

## Exit gates

```bash
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright src/
uv run pytest
uv run python scripts/check_readiness.py docs/plans/404-store-qc-rejected-member-forecasts.md
```

After staging deploy (orchestrator):
1. Over the first days, count distinct `(attempt_id, station_id, model_id, group_id)` in the record
   per day — one per rejected assignment, since a station in two groups can be rejected in both — and compare with the rejection log lines
   (`run_station_forecast.qc_failed` / `run_group_forecast.qc_failed`, logged once per rejected
   assignment after T2) minus the `rejected_forecast.write_failed` events. They must match, with two
   allowed exceptions, each checked by `attempt_id`: an attempt logged as `write_timed_out` is either
   fully present or fully absent (never partial), and a run that crashed before its `finally` (D5's
   accepted loss) has no rows. The daily row count is
   the volume measurement for retention.
2. For each station and cycle with a rejection: in **combination** mode (staging runs `pooled`,
   `config/overlays/mac-mini.toml:15`) on a **fresh** cycle, the rejected model is absent from the
   stored contributors and every other successful model is stored as before (PRIMARY mode is covered
   by T2's unit case; staging never runs it). For a **re-run** of an earlier cycle,
   check only that rejected outputs never enter the current attempt's combination inputs; existing
   rows behave as Plans 327/328 prescribe.
3. A reviewer token (`--tenant sapphire`) scoped to one station → 200 on the route for that station
   and 404 for another existing station; a temporary consumer token → 403. Delete both tokens by id
   (Plan 401's single-token procedure).
4. Only then does the orchestrator tell the map session about the route and the new contract version.

## Explicitly out of scope

- Changing any forecast QC rule, threshold or verdict.
- Alerting on, publishing, combining or re-running from a rejected forecast.
- Retention or cleanup of rejected records — decided once exit-gate step 1 has measured the volume.
- Backfilling rejections before this plan; hindcasts.

## Changelog

- 2026-09-26 — drafted at the owner's request as the follow-on to Plan 402. Decisions: D1 (keep
  them), D2 (a separate record, chosen after the first review found the `forecasts` table unsafe),
  D3 (per station), D4 (values withheld from reviewer tokens where Plan 341's gate is active; admins and Plan 341's granted hydrologists keep full access — owner, 2026-09-26, as PR #316 recorded in Plan 341).
- 2026-09-26 — the owner-commissioned high-risk review (data and forecast-cycle safety) found that
  capture writes on the shared flow connection could stall or break the cycle; the store was changed
  to own a separate connection with lock and statement timeouts (superseded by D5 below).
- 2026-09-26 — review of that fix found the held connection itself fragile, and the measured wiring
  wrong (forecast writers already take their own pooled transaction). Owner chose D5: rejections are
  collected in memory and saved once, after forecasts, alerts and the heartbeat, in one
  time-limited transaction following the existing writer pattern. Also: a role-independent
  append-only trigger, savepoint-seam store tests, a committed-seed lock test asserting SQLSTATE
  `55P03`, the conventions carve-out, and Plan 341's route line is `:114`.
- 2026-09-26 — review of D5 (Claude, Codex, high-risk re-check): the capture connects through its
  own bounded, unpooled connection (a pooled reconnect could wait 130 s); the write sits in the
  flow's outermost `finally`, also after `StoreError` exits that emit no heartbeat; the atomic unit
  is the run's batch; flow PG test commits only parent seeds; T3 names its test file; citations
  corrected.
- 2026-09-26 — second review of D5: per-step limits cannot bound every stall (host resolution,
  a stall after connecting, many statements). Owner chose D6: a hard overall limit of 10 s on a
  daemon thread. Also: no capture write when nothing was rejected; one named builder for the capture
  factory, tested for `NullPool` and the connect timeout; only the buffer is bound before the flow's
  `try`; the Pre-change fails for the right reason; Plan 402's consumer-page sentence is updated.
- 2026-09-26 — review of D6 (Claude, Codex, high-risk re-check): stopping the wait did not stop the
  thread, so a slow save could commit after being logged as failed. The thread now checks a shared
  event just before `COMMIT` and rolls back; it never logs (the flow logs `write_timed_out` once, and
  a commit already in flight has an unknown but atomic outcome, reconciled by `attempt_id` in exit-gate
  step 1). Also: the deadline is read at call time; buffer entries carry `attempt_id`; the API passes
  no write factory.
- 2026-09-26 — review round on `4e860404` (Codex CLEAN; Claude; high-risk re-check): the commit
  guard now lives in the store's `write_batch` (it receives the abandon event and raises inside the
  transaction before COMMIT); the D6 test drives the real Postgres store; the flow-side capture sits
  in the single carve-out `except`; `attempt_id` is bound into the log context so rejection, timeout
  and stored rows can be joined; a Postgres read-path case; citations corrected.

## Dependency graph

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1"]},
    {"id": "phase-2", "tasks": ["T2", "T3"], "parallel": false, "depends_on": ["phase-1"]},
    {"id": "phase-3", "tasks": ["T4"], "depends_on": ["phase-2"]}
  ]
}
```
