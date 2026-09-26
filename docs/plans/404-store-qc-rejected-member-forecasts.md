---
status: DRAFT
created: 2026-09-26
plan: 404
title: Keep the member and group forecasts that QC rejects — in their own record, never as a forecast
scope: Record every station (member) or group-station forecast whose forecast QC verdict is `qc_failed` — its values and every parameter's flags — in a separate rejected-forecast record, not in the `forecasts` table, and serve it to reviewers through one REVIEW-gated `/api/v1` route, so the flow map can show what forecast QC rejected and why. The forecast cycle's behaviour is otherwise unchanged — fallback, alerting, combination, model state, re-runs (Plans 327/328), the freshness heartbeat and every reader of `forecasts` see exactly what they see today. NOT any change to forecast QC rules, thresholds or verdicts; NOT combined forecasts (already stored failed, Plan 253 OD-1); NOT hindcasts; NOT backfilling earlier rejections; NOT publishing or alerting on a rejected forecast.
risk: high   # new table + migration, live-database writes, external-facing route (docs/workflow.md § High-risk work)
depends_on: [401, 402]
blocks: []
related: [251, 253, 327, 328, 340, 341]
open_decisions: []
closed_decisions: [D1, D2, D3]   # owner, 2026-09-26
source: 2026-09-26 — found by the round-5 review of Plan 402 — a rejected member or group forecast is dropped, so the flow map can never show a forecast-QC rejection except on a combined forecast. Owner, 2026-09-26: a separate follow-on plan; then, after the first review, a separate record rather than the forecast table.
---

# Plan 404 — keep the member and group forecasts that QC rejects

## Status

**DRAFT — HIGH RISK — redesigned after its first review, not re-reviewed.** A new table, live
writes from the forecast cycle and an external-facing route (`docs/workflow.md` § High-risk work):
the ordinary Claude + Codex pair, plus one owner-commissioned review before READY and again before
the implementation PR. Depends on Plan 401 (the REVIEW gate) and Plan 402 (the typed flag model
and the committed map contract).

## Why this exists

The flow map (Plan 402) lets a reviewer — DHM first — judge whether QC thresholds are right. For
forecasts it cannot: a member or group-station forecast that fails QC is thrown away, so the three
rejecting forecast rules (`negative_value`, `range_check`, `quantile_crossing`) appear never to
fire. Their silence says nothing about the thresholds; the evidence simply does not exist.

## What is measured (origin/main, 2026-09-26)

- **Station (member) path.** `services/run_station_forecast.py:587-599` stops at the **first**
  parameter whose worst flag is `qc_failed` and returns `AssignmentFailure(QC_FAILED)` — which
  carries only `cause` and `detail` (`:116-118`); the forecast object is never built, later
  parameters are never checked, nothing is stored, and the cycle tries the next model.
- **Group path is per station, not per batch.** `services/run_group_forecast.py:287-318` returns
  `None` for the failing station only; siblings keep their results (`:601-635`; test
  `tests/unit/services/test_run_group_forecast.py:838`). `docs/architecture-context.md:90,116`
  describe it as a whole-batch drop — imprecise.
- **All models failing ends the station early.** `flows/run_forecast_cycle.py:3083-3090, 3379,
  3451` (`all_models_failed`) exit before persistence; the PRIMARY wrapper returns `None`
  (`services/run_station_forecast.py:928`). In PRIMARY mode every assignment runs but only the
  selected model's forecast is stored (`flows/run_forecast_cycle.py:3349-3408`).
- **Why not the `forecasts` table** (the first draft's design, rejected after review):
  - `store_forecast` classifies a repeated key; a different QC verdict on equal values is "row 3",
    refused by Plan 327 (`services/forecast_retry.py:76-82`). A stored rejected row would turn a
    later passing re-run into a refusal (dropped from alerting), and on the group path a refusal
    is fatal to the cycle (`flows/run_forecast_cycle.py:3744-3786`).
  - `FORECAST_FRESHNESS` is CRITICAL only when `forecasts_stored == 0` (`:928-934`); rejected rows
    would count.
  - Every reader that takes a stored row as "the" forecast — the Forecast Lab's latest reads and
    its cycle marker `fetch_latest_uncombined_issued_at`, `/api/v1`, the dashboard, scripts — would
    need an exclusion.
  A separate record touches none of these.
- **Alerting and combination read in-memory results**, not stored rows
  (`services/alert_checker.py::check_station_alerts`; `combinable_results`, which also excludes the
  named fallback models, `services/run_station_forecast.py:132-136`).

## Owner decisions

### D1 — keep rejected member and group-station forecasts. **⚖️ CLOSED — owner, 2026-09-26.**

### D2 — in a separate record, not the `forecasts` table. **⚖️ CLOSED — owner, 2026-09-26.**

A new table holds rejected forecasts. Nothing that reads `forecasts` changes, and a rejected
forecast is never a candidate for alerting, combination, publication, re-run comparison, model
state or the freshness heartbeat.

### D3 — group rejections are per station. **⚖️ CLOSED — follows from the code.**

Only the failing station's forecast is recorded, with its own flags; its siblings are untouched.

## Record and route contract

**Record** — one row per rejected (station, model, parameter) per attempt, append-only:
`station_id`, `model_id`, `model_artifact_id`, `group_id` (null for a member), `issued_at` (the
cycle), `parameter`, `representation`, the ensemble or quantile values, `qc_flags` (the four-key
shape), `recorded_at`. An attempt stores **every** parameter of the rejected assignment, each with
its own flags — QC is run on all parameters before the verdict, which is unchanged (any failed
parameter still rejects the assignment). A re-run of the same cycle appends; nothing is replaced.

**Route** — `GET /api/v1/stations/{id}/rejected-forecasts?start=&end=[&model_id=]`, REVIEW-gated
(Plan 401), station-scoped like its siblings, returning the rows above with flags typed as Plan
402's `QcFlagResponse`. Added to Plan 402's committed map contract (its explicit route list).

## Tasks

Every code task carries the Task Exit Gate (`docs/workflow.md` § Task Exit Gate).

### T1 — the table

**Outcome:** the rejected-forecast table exists, append-only, with its store Protocol and
implementation.

**In:** `db/metadata.py`, a new alembic migration (next free revision at implementation time), a
store Protocol in `protocols/stores.py` and implementation under `store/`, the fake in
`tests/fakes/fake_stores.py`, `docs/spec/types-and-protocols.md`, `docs/spec/database-schema.md`,
and grants in `docker/bootstrap-roles.sql` (worker INSERT; API SELECT).

**Out:** any change to `forecasts`.

**Pre-change:** a store test inserting a rejected row fails on the missing table.

**Verification:** `uv run pytest tests/integration/store/` plus the new store and migration tests, named in the PR — insert and range read round-trip; upgrade and downgrade on an empty table.

### T2 — capture every rejection, change nothing else

**Outcome:** each rejected member assignment and each rejected group station is written to the new
record, including when every model for the station fails and in PRIMARY mode; the cycle otherwise
behaves exactly as today.

**In:** `services/run_station_forecast.py` (QC every parameter before the verdict;
`AssignmentFailure` gains an optional rejected payload — values, flags, artifact, issued time),
`services/run_group_forecast.py` (the same for the failing station), and
`flows/run_forecast_cycle.py`, which writes payloads **before** any `all_models_failed` exit and in
both PRIMARY and multi-model modes. The write is **best-effort**: a failure logs a structured
warning and never aborts the cycle, is never counted in `forecasts_stored`, and never goes through
the group path's fatal store call. The spec entries for `AssignmentFailure`,
`MultiModelForecastResult` and the group result (`docs/spec/types-and-protocols.md`).

**Out:** QC rules and verdicts; `forecasts`; alerting; combination; model state; hindcasts; Plan
340 evidence capture (a rejected forecast is not an issued forecast).

**Pre-change:** a flow test where a member model's ensemble trips `negative_value` asserts a
rejected-record row — fails today because nothing is kept.

**Verification:** `uv run pytest tests/unit/flows/test_run_forecast_cycle.py tests/unit/services/test_run_station_forecast.py tests/unit/services/test_run_group_forecast.py` — a rejected member is recorded with flags for **every** parameter and the next ordinary model's forecast is stored as today; named fallback models stay out of combination; a rejected ensemble is absent from alert inputs and no model state is stored for it; a station where every model is rejected records every rejection and still reports `all_models_failed`; PRIMARY mode records every assignment that was rejected; a mixed group records only the failing station and the sibling's forecast is stored as today; a failed write of the record leaves the cycle's result and heartbeat unchanged; a cycle in which every assignment is rejected leaves `FORECAST_FRESHNESS` CRITICAL; the forecasts stored by a cycle with no rejection are byte-identical to today's.

### T3 — the review route

**Outcome:** `GET /api/v1/stations/{id}/rejected-forecasts` serves the record to reviewer and admin
tokens.

**In:** the route next to Plan 402's REVIEW routes, response models reusing `QcFlagResponse`, the
route-matrix entry (REVIEW), Plan 402's map contract file and its explicit route list, the consumer
page `docs/spec/api-v1-review.md` (rejected forecasts live here, not in the forecast list), and
`docs/conventions.md` § API routes.

**Out:** any change to the forecast list or detail routes.

**Pre-change:** a request to the route returns 404.

**Verification:** `uv run pytest tests/unit/api/` — reviewer → 200 in scope, 404 out of scope; consumer → 403; the drift test covers the route.

### T4 — documents

**Outcome:** the specification and architecture describe the rule, and the group wording is exact.

**In:** `docs/spec/types-and-protocols.md` (Flow 1 step 1.10 at `:789`), `docs/architecture-context.md:90,116`
(rejections are recorded separately; group rejection is per station), `docs/touchpoint-maps.md`
(the forecast-cycle paragraph and the freshness bullet: rejected records are not forecasts), and
Plan 402's consumer-page line that rejected forecasts are not stored.

**Out:** archived Plan 253.

**Pre-change:** N/A — documentation.

**Verification:** bounded inspection — each In-listed location is changed in the branch diff, and no document still says a rejected member forecast leaves no record.

## Exit gates

```bash
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright src/
uv run pytest
uv run python scripts/check_readiness.py docs/plans/404-store-qc-rejected-member-forecasts.md
```

After staging deploy (orchestrator): over the next cycles, count rejected records by model and
station; for each, confirm that — where a lower-priority ordinary model succeeded — its forecast is
stored as before, and that the count of stored forecasts per cycle is unchanged from the days before
the deploy.

## Explicitly out of scope

- Changing any forecast QC rule, threshold or verdict.
- Alerting on, publishing, combining or re-running from a rejected forecast.
- Retention or cleanup of rejected records (rare and small; revisit if volume says otherwise).
- Backfilling rejections before this plan; hindcasts.

## Changelog

- 2026-09-26 — drafted at the owner's request as the follow-on to Plan 402 (D1). After its first
  review the owner chose a separate record over the `forecasts` table (D2); rewritten on that design.

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
