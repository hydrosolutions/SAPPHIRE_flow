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
closed_decisions: [D1, D2, D3, D4]   # owner, 2026-09-26
source: 2026-09-26 — found by the round-5 review of Plan 402 — a rejected member or group forecast is dropped, so the flow map can never show a forecast-QC rejection except on a combined forecast. Owner, 2026-09-26: a separate follow-on plan; a separate record rather than the forecast table; values withheld from reviewer tokens where Plan 341's gate is active.
---

# Plan 404 — keep the member and group forecasts that QC rejects

## Status

**DRAFT — HIGH RISK — review corrections folded, not re-reviewed.** A new table, live writes from
the forecast cycle and an external-facing route (`docs/workflow.md` § High-risk work): the ordinary
Claude + Codex pair on the current text, plus one owner-commissioned review before READY and again
before the implementation PR. Depends on Plan 401 (the REVIEW gate) and Plan 402 (the typed flag
model and the committed map contract). All decisions are closed.

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
  `StationForecastResult | None` (`services/run_station_forecast.py:888-900`) and **discards**
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
- **Production store wiring.** The flow's stores come from `flows/_db.py::setup_production_stores`
  (`:78`); the API's from `api/deps.py`.
- **Plan 341's boundary** (`docs/plans/341-chwrr-forecast-publication-api.md`): a reviewer token
  sees only published values; REVIEW routes are outside the publication gate because they carry no
  forecast values; `qc_flags[].detail` is gated; a `qc_failed` forecast is never publishable.

## Owner decisions

### D1 — keep rejected member and group-station forecasts. **⚖️ CLOSED — owner, 2026-09-26.**

### D2 — in a separate record, not the `forecasts` table. **⚖️ CLOSED — owner, 2026-09-26.**

A rejected forecast is never a candidate for alerting, combination, publication, re-run comparison,
model state or the freshness heartbeat.

### D3 — group rejections are per station. **⚖️ CLOSED — follows from the code.**

### D4 — where Plan 341's gate is active, reviewers see the rule, not the values. **⚖️ CLOSED — owner, 2026-09-26.**

For a tenant where Plan 341's publication gate is active, a **reviewer** token receives each
rejection's `attempt_id`, station, model, issued time, parameter, `rule_id`, `rule_version` and
`status` — no
values and no flag `detail`. **Admin** tokens receive everything; whether Plan 341's signed-in
hydrologist principal may read the full record is 341's decision. Where the gate is not active
(e.g. the Swiss deployment today), reviewers receive everything.

**One named check decides "gated"**: a single predicate (e.g. `publication_gate_active(tenant_id)`)
that answers **no** until Plan 341 provides its tenant activation switch, and then asks that switch.
The route consults nothing else. **Both landing orders are covered:** if Plan 341 lands first, T3
wires the predicate to its switch and classifies this route in 341's route inventory; if this plan
lands first, T4 records in Plan 341 that this REVIEW route **carries values**, that its activation
must wire the predicate and apply D4, and that `341:78`'s "Plan 404 may retain such rows" now means
this separate record.

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
| `parameter`, `representation`, `units`, `time_step_seconds` | as on `forecasts` |
| `values` | JSONB, exactly the `EnsembleResponse` value fields (`api/schemas.py:108`): `valid_times` and `series`, keyed by member id or quantile level. The **raw** ensemble, as `forecasts` would store it — for water level, not the datum-shifted copy QC checked. Non-finite numbers use the evidence encoding `{"nonfinite": "nan" \| "inf" \| "-inf"}` (`services/forecast_evidence.py:76-77`), so a rejected ensemble containing them is stored losslessly |
| `qc_flags` | JSONB, the four-key flag shape |
| `recorded_at` | server default `now()` |

Index `(station_id, issued_at)`. Every parameter of a rejected assignment is recorded, each with its
own flags: QC now runs on **all** parameters before the verdict, which is unchanged (any failed
parameter still rejects the assignment).

**Route** `GET /api/v1/stations/{id}/rejected-forecasts?start=&end=[&model_id=][&limit=&offset=]`,
REVIEW-gated (Plan 401), station-scoped, paginated with its own ceiling (`limit` ≤ 50, since each item
carries a full ensemble), every item carrying `attempt_id` and `recorded_at`, non-finite values in
the same encoding, flags typed as Plan 402's `QcFlagResponse`; values and
`detail` withheld per D4. Added to Plan 402's committed map contract and its explicit route list.

## Tasks

Every code task carries the Task Exit Gate (`docs/workflow.md` § Task Exit Gate).

### T1 — the table and its store

**Outcome:** the table exists, append-only, with its store Protocol and implementation, wired into
production for both the flow and the API.

**In:** `db/metadata.py`; a new alembic migration (next free revision at implementation time);
`tests/unit/db/test_alembic_head_release_b.py` (the head pin); the store Protocol in
`protocols/stores.py` and its implementation under `store/`; the fake in `tests/fakes/fake_stores.py`;
`flows/_db.py::setup_production_stores` and `api/deps.py`; the flow parameter
`rejected_forecast_store` on `run_forecast_cycle_flow` (`flows/run_forecast_cycle.py:2323-2365`),
read from the production bundle when stores are not injected (`:2405-2428`) — an injected caller that
omits it gets no capture, which leaves the ~84 existing injected test calls unchanged; grants in `docker/bootstrap-roles.sql`
(worker INSERT; API SELECT; no UPDATE/DELETE/TRUNCATE for either); `docs/spec/types-and-protocols.md`,
`docs/spec/database-schema.md`.

**Out:** any change to `forecasts`.

**Pre-change:** with the Protocol and the fake in place, a store round-trip test against Postgres
fails on the missing table.

**Verification:** `uv run pytest tests/unit/db/test_alembic_head_release_b.py tests/integration/db/ tests/integration/store/` including the new store and migration tests, named in the PR — round-trip of an ensemble and a quantile row, including a single-step forecast and one containing NaN, `inf` and `-inf`, preserving units, cadence and every value; upgrade and downgrade on an empty table; `tests/integration/db/test_role_bootstrap.py` extended: the worker can INSERT, the API can SELECT, and neither can UPDATE, DELETE or TRUNCATE (the precedent at `:381`); the production store bundle contains the new store.

### T2 — capture every rejection, change nothing else

**Outcome:** every rejected member assignment and rejected group station is recorded — in
multi-model and PRIMARY modes, on the per-track and legacy paths, on the group path, when every model
fails, and when a cross-cycle mismatch skips the station; the cycle otherwise behaves exactly as today.

**In:**
- `services/run_station_forecast.py` — QC every parameter before the verdict, logging
  `run_station_forecast.qc_failed` once per rejected assignment (likewise the group warning, once
  per rejected station); `AssignmentFailure`
  gains an optional rejected payload (values, units, cadence, flags, artifact, issued time);
  `run_station_forecast` (the PRIMARY wrapper) returns the rejected payloads alongside its result.
- `services/run_group_forecast.py` — QC every parameter before the verdict in
  `_build_station_result` (`:287-318`, which today returns at the first failing one), and a new return
  shape carrying results **and** per-station rejected payloads (e.g. a `GroupForecastOutcome`), with its caller at
  `flows/run_forecast_cycle.py:3715` and `tests/unit/flows/test_run_forecast_cycle_group_fi_resolver.py`.
- `flows/run_forecast_cycle.py` — write the payloads **immediately after** each of
  `run_all_station_forecasts_per_track`, `run_all_station_forecasts`, `run_station_forecast` and
  `run_group_forecast` returns, before the cross-cycle preflight, the `all_models_failed` checks or
  any other exit. The write is **best-effort**: a failure logs a structured warning, never aborts the
  cycle, is never counted in `forecasts_stored`, and never goes through the group path's fatal store
  call. A missing store in the production bundle is an error at setup (T1), not a silent no-op.
- `docs/spec/types-and-protocols.md` — `AssignmentFailure`, `MultiModelForecastResult`, the
  `run_station_forecast` return, and a new entry for the group outcome.
- Tests broken by the new `run_station_forecast` return, updated: `tests/integration/test_e2e_pipeline.py`,
  `tests/unit/services/test_run_station_forecast_fanout.py`,
  `tests/unit/services/test_unchecked_observation_policy.py`.

**Out:** QC rules and verdicts; `forecasts`; alerting; combination; model state; hindcasts; Plan 340
evidence capture (a rejected forecast is not an issued forecast).

**Pre-change:** with the store injected (T1) but no capture code, a flow test where a member model's
ensemble trips `negative_value` finds the rejected-forecast store empty — the fault itself, not an
import or argument error.

**Verification:** `uv run pytest tests/unit/flows/test_run_forecast_cycle.py tests/unit/flows/test_run_forecast_cycle_resume.py tests/unit/flows/test_run_forecast_cycle_group_fi_resolver.py tests/unit/services/test_run_station_forecast.py tests/unit/services/test_run_station_forecast_per_track.py tests/unit/services/test_run_group_forecast.py` — cases:
- a rejected member is recorded with flags for **every** parameter, and the next ordinary model's forecast is stored as today; named fallback models stay out of combination; the rejected ensemble is absent from alert inputs and no model state is stored for it;
- PRIMARY mode on the legacy path records the rejection of every assignment that ran;
- a station where every model is rejected records every rejection and still reports `all_models_failed`;
- a per-track station with a cross-cycle mismatch **and** a rejected model records the rejection and still skips the station;
- a mixed group records only the failing station, with flags for **every** parameter; the sibling's forecast is stored as today;
- a rejected ensemble containing NaN or `inf` is captured, not dropped by the best-effort write;
- a re-run of the same cycle appends a second attempt and leaves Plan 327's classification of the stored forecasts unchanged;
- a failed write of the record leaves the cycle's result and heartbeat unchanged;
- heartbeat: a cycle whose every assignment is rejected is CRITICAL; an explicit-cycle replay emits nothing; a genuine group forecast-store failure still forces CRITICAL;
- a cycle with no rejection stores the same forecasts as today, compared with ids and timestamps normalised.

### T3 — the review route

**Outcome:** `GET /api/v1/stations/{id}/rejected-forecasts` serves the record per D4.

**In:** the route next to Plan 402's REVIEW routes; response models reusing `QcFlagResponse`; the
route-matrix entry (REVIEW); Plan 402's map contract file and explicit route list, with its version bumped per Plan 402 D14 (a new route is additive: minor); the consumer page
`docs/spec/api-v1-review.md` (rejected forecasts live here; values withheld where Plan 341's gate is
active); `docs/conventions.md` § API routes; `docs/standards/security.md` (the REVIEW-class route
list and D4's rule beside Plan 402's D13 entry); `docs/touchpoint-maps.md` (API paragraph). The gate
predicate (D4). If Plan 341's route inventory and switch exist on the base branch, wire the
predicate to the switch, classify this route there as a REVIEW diagnostic whose values follow D4,
and extend its test.

**Out:** any change to the forecast list or detail routes.

**Pre-change:** a request to the route returns 404.

**Verification:** `uv run pytest tests/unit/api/` — reviewer → 200 in scope with values and flags (predicate answers no), 404 out of scope; with the predicate forced to yes, reviewer → rule fields and `attempt_id` only, no values and no `detail`, admin → everything; non-finite values round-trip in their encoding; `limit` above 50 is refused; consumer → 403; `limit`/`offset` paginate; the drift test covers the route.

### T4 — documents

**Outcome:** the specification and architecture describe the rule, and the group wording is exact.

**In:** `docs/spec/types-and-protocols.md` (Flow 1 step 1.10, `:789`), `docs/architecture-context.md:90,116`
(rejections are recorded separately; group rejection is per station), `docs/touchpoint-maps.md`
(the forecast-cycle paragraph and the freshness bullet: rejected records are not forecasts), and
Plan 402's consumer-page line that rejected forecasts are not stored. **If Plan 341 has not landed:**
a note in `docs/plans/341-chwrr-forecast-publication-api.md` that this REVIEW route carries values,
that 341's activation must wire D4's predicate to its switch and apply D4, and that its line on
Plan 404 retaining rows now means this separate record (see D4).

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
1. Over the first days, count distinct `(attempt_id, station_id, model_id)` in the record per day —
   one per rejected assignment — and compare with the rejection log lines
   (`run_station_forecast.qc_failed` / `run_group_forecast.qc_failed`, logged once per rejected
   assignment after T2) minus the logged capture failures. They must match. The daily row count is
   the volume measurement for retention.
2. For each station and cycle with a rejection: in **combination** mode (staging runs `pooled`,
   `config/overlays/mac-mini.toml:15`), the rejected model is absent from the stored contributors
   and every other successful model is stored as before; in **PRIMARY** mode, the stored forecast is
   the highest-priority successful model's.

## Explicitly out of scope

- Changing any forecast QC rule, threshold or verdict.
- Alerting on, publishing, combining or re-running from a rejected forecast.
- Retention or cleanup of rejected records — decided once exit-gate step 1 has measured the volume.
- Backfilling rejections before this plan; hindcasts.

## Changelog

- 2026-09-26 — drafted at the owner's request as the follow-on to Plan 402. Decisions: D1 (keep
  them), D2 (a separate record, chosen after the first review found the `forecasts` table unsafe),
  D3 (per station), D4 (values withheld from reviewer tokens where Plan 341's gate is active).

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
