---
status: DRAFT
created: 2026-09-26
plan: 404
title: Keep the member and group forecasts that QC rejects — stored marked failed, never used
scope: Store a station (member) or group forecast whose forecast QC verdict is `qc_failed`, marked failed with its flags and values, instead of dropping it, so a reviewer can see what QC rejected and why — the same treatment Plan 253 OD-1 gave combined forecasts. The fallback chain, alerting and combination keep working from in-memory results exactly as today; every reader that uses a stored forecast as "the" forecast excludes failed rows explicitly. NOT any change to forecast QC rules, thresholds or verdicts; NOT publishing or alerting on a failed forecast; NOT the Forecast Lab snapshot format; NOT hindcasts; NOT backfilling forecasts already dropped.
risk: high   # live-database impact + user-visible behaviour (docs/workflow.md § High-risk work)
depends_on: []
blocks: []
related: [251, 253, 327, 328, 340, 341, 401, 402]
open_decisions: [D2, D3]
closed_decisions: [D1]   # owner, 2026-09-26
source: 2026-09-26 — found by the round-5 review of Plan 402 — a failed member or group forecast is dropped, so the flow map can never show a forecast-QC rejection except on a combined forecast. Owner, 2026-09-26: fix it in a separate follow-on plan (this one).
---

# Plan 404 — keep the member and group forecasts that QC rejects

## Status

**DRAFT — HIGH RISK — not reviewed.** It changes what the forecast cycle writes to the live
database and what reviewers see (`docs/workflow.md` § High-risk work): the ordinary Claude + Codex
pair, plus one owner-commissioned review before READY and again before the implementation PR.

## Why this exists

The flow map (Plan 402) lets a reviewer — DHM first — judge whether QC thresholds are right. For
forecasts it cannot: when a member or group forecast fails QC it is thrown away, so the three
rejecting forecast rules (`negative_value`, `range_check`, `quantile_crossing`) appear never to
fire. Their silence says nothing about the thresholds; the evidence simply does not exist.

Plan 253 OD-1 already stores a failed **combined** forecast marked failed, arguing that a dropped
row "leaves no record of what was rejected or why". It exempted member models because a failed
member "routes to fallback". That is true for *operations* — something else is forecast — but not
for *review*: the fallback replaces the forecast, not the evidence. This plan extends OD-1's
reasoning to member and group forecasts, keeping the fallback exactly as it is.

## What is measured (origin/main, 2026-09-26)

- **Station (member) path.** `services/run_station_forecast.py:588-599`: when the worst flag is
  `qc_failed`, it logs `run_station_forecast.qc_failed` and returns
  `AssignmentFailure(QC_FAILED)`; nothing is stored, and the cycle tries the next model by priority.
- **Group path.** `services/run_group_forecast.py:308-317`: the same, returning `None`; no
  per-station fallback within the batch, nothing stored.
- **Combined path.** `services/forecast_combination.py:530-536`: stored marked `qc_failed` (OD-1).
  The Forecast Lab excludes it explicitly (`services/forecast_lab/db_sources.py:274-285`, OD-1a).
- **Alerting and combination do not read stored forecasts.** `services/alert_checker.py::check_station_alerts`
  takes in-memory ensembles; combination takes the cycle's `combinable_results`
  (`flows/run_forecast_cycle.py:1944-1964`). A failed assignment is absent from both because it
  failed upstream (`docs/architecture-context.md:447`), not because of a stored-row filter.
- **Readers of stored forecast rows** (store methods and raw SQL, outside the store):
  - Forecast Lab — `fetch_latest_forecast` per model (`services/forecast_lab/db_sources.py:218-263`,
    `services/forecast_lab/snapshot.py:425`) and `fetch_forecasts_for_cycle` (combined only).
  - `/api/v1` — `GET /stations/{id}/forecasts` (`fetch_forecast_summaries`,
    `api/routes/api_stations.py:300`) and `GET /forecasts/{id}`; both already return `qc_status`.
  - Legacy admin HTML/JSON — `api/routes/forecasts.py` (raw SQL).
  - Plan 341 (DRAFT) will add publication candidates; Plan 340 captures evidence per stored forecast;
    Plans 327/328 re-run and supersede by forecast key.
  T1 re-verifies this list; it is the plan's main risk.
- **Documents that state the drop as the rule:** `docs/spec/types-and-protocols.md:789` (Flow 1
  step 1.10), `docs/architecture-context.md:90` and `:116`.

## Owner decisions

### D1 — store rejected member and group forecasts. **⚖️ CLOSED — owner, 2026-09-26.**

A member or group forecast with aggregate `qc_failed` is stored with its values, `qc_status =
qc_failed` and its flags, as a combined forecast already is. The in-memory result is still a
failure: the station falls through to its next model exactly as today, and alerting and
combination never see the failed ensemble.

### D2 — who sees a stored failed forecast? **OPEN.**

**Recommendation:**
- **Shown**, with `qc_status` and flags: `/api/v1` forecast list and detail (the map's review
  surface, Plan 402), and the admin legacy pages.
- **Excluded** explicitly, wherever a stored row is taken as *the* forecast: the Forecast Lab (as
  OD-1a does for combined), any "latest forecast" read, and — recorded for Plan 341 — publication
  candidates: a failed forecast is never publishable.

### D3 — group forecasts: one failed row per member station? **OPEN.**

A group model runs one batch and today drops it whole on any failed parameter. **Recommendation:**
store the per-station forecasts the batch produced, each carrying the batch's failed verdict and
flags, because the map reviews per station. Nothing else about group handling changes (still no
per-station fallback within the batch).

## Tasks

Every code task carries the Task Exit Gate (`docs/workflow.md` § Task Exit Gate).

### T1 — the reader inventory

**Outcome:** a table in this plan of every code path that reads stored forecast rows, each marked
**shown** or **excluded** per D2, with the file:line of its current QC handling.

**In:** a repository-wide search of `forecast_store` methods and raw SQL on `forecasts` outside
`store/`; this plan document.

**Out:** code changes.

**Pre-change:** N/A — inventory.

**Verification:** bounded inspection — `grep -rn "forecast_store\.\|forecasts\.c\." src/ | grep -v "^src/sapphire_flow/store/"` shows no reader missing from the table.

### T2 — store the rejected forecast, keep the fallback

**Outcome:** a member forecast and (per D3) a group batch that fail QC are stored marked
`qc_failed` with flags; the in-memory outcome is unchanged (member → `AssignmentFailure`, fallback
runs; group → no result).

**In:** `services/run_station_forecast.py`, `services/run_group_forecast.py`, and the storing seam
in `flows/run_forecast_cycle.py` that already stores results (`:200`, `:3749`); Plan 340's evidence
capture runs for these rows like any stored forecast.

**Out:** QC rules and verdicts; alerting; combination; hindcasts.

**Pre-change:** a flow test where a member model's ensemble trips `negative_value` asserts a stored
`qc_failed` row for that model — fails today because nothing is stored.

**Verification:** `uv run pytest tests/unit/flows/test_run_forecast_cycle.py tests/unit/services/test_run_station_forecast.py tests/unit/services/test_run_group_forecast.py` — the failed member row is stored with its flags; the fallback model's forecast is stored and is the one alerting and combination used; a passing cycle stores exactly what it stores today; a group batch with one failed parameter stores per D3 and runs no fallback.

### T3 — exclude failed rows wherever a stored row is taken as the forecast

**Outcome:** every reader T1 marks **excluded** skips `qc_failed` rows explicitly; every reader
marked **shown** returns them with `qc_status` and flags.

**In:** the readers in T1's table — at least `services/forecast_lab/db_sources.py` (member
`fetch_latest_forecast` reads) and any "latest" store method used operationally; a note in
`docs/plans/341-chwrr-forecast-publication-api.md` that a `qc_failed` forecast is never a
publication candidate.

**Out:** the Forecast Lab snapshot format; Plan 402's fields.

**Pre-change:** with T2 in place, a Forecast Lab test storing a failed member row newer than a
passing one shows the failed row as that model's forecast — the fault this task removes.

**Verification:** `uv run pytest tests/unit/services/forecast_lab/ tests/unit/api/` plus the tests for each T1 reader — excluded readers return the passing row or none; `/api/v1` list and detail return the failed row with `qc_status = qc_failed` and its flags.

### T4 — documents

**Outcome:** the specification and architecture describe the new rule.

**In:** `docs/spec/types-and-protocols.md:789`, `docs/architecture-context.md:90,116`,
`docs/touchpoint-maps.md` (forecast readers), and Plan 402's consumer-page text that says failed
member/group forecasts are not stored.

**Out:** archived Plan 253.

**Pre-change:** N/A — documentation.

**Verification:** `grep -rn "nothing is stored for that assignment\|no forecast written" docs/` returns only historical records.

## Exit gates

```bash
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright src/
uv run pytest
uv run python scripts/check_readiness.py docs/plans/404-store-qc-rejected-member-forecasts.md
```

After staging deploy (orchestrator): over the next cycles, count stored `qc_failed` rows by result
kind; confirm each failed member row has a fallback forecast for the same station and cycle, and
that the Forecast Lab snapshot shows none of them.

## Explicitly out of scope

- Changing any forecast QC rule, threshold or verdict.
- Alerting on, publishing, or combining a failed forecast.
- Backfilling forecasts dropped before this plan.
- Hindcasts (their QC does not trigger fallback already).

## Changelog

- 2026-09-26 — drafted at the owner's request as the follow-on to Plan 402. Decision D1.

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
