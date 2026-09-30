---
status: DRAFT
created: 2026-09-30
plan: 514
title: One group member with empty past forcing must not drop the whole group; a dropped group must show in cycle health
scope: (A) In the group input path, a member whose past forcing is empty is skipped like a cadence-gap member instead of crashing the whole group. (B) Every whole-group drop becomes visible — counted in cycle health and written to the pipeline-health table with the group, model, reason and stations.
risk: high   # touches the live forecast cycle, adds a health-record type, changes ForecastCycleResult (docs/workflow.md § High-risk work: user-visible behaviour, scientific behaviour with operational consequences)
related: [262, 312, 116, 223, 404, 516]
open_decisions: [Q1, Q2, Q3, Q4]
source: 2026-09-29 pilot run `powerful-leech` — investigation by the orchestrator session
---

# Plan 514 — a group member with empty past forcing must not drop the group, and a dropped group must be visible

## Status

**DRAFT — HIGH RISK (proposed; the owner decides).** It edits the live forecast cycle
(`flows/run_forecast_cycle.py`), the group input service and the result type the flow returns, and it adds a
pipeline-health record type. Multi-model review is mandatory (`docs/workflow.md` § Multi-Model Review), plus the
one extra owner-commissioned review that high-risk work gets. Only the orchestrator sets READY.

## Why

On **2026-09-29** the pilot run `powerful-leech` ended **Completed with zero `cmal_small` rows**. Nothing in
Prefect state, in the database or in the health record said a whole 139-station group had produced nothing.

**Most likely cause (UNVERIFIED — the worker logs were gone before anyone read them):** station **2024**, the one
member of the 139-station group `swiss-cmal-small-pilot` without a reanalysis weather binding
(Plan 516). Its past forcing comes back empty, and one empty member drops the whole group.

**Ruled out:** the short 30-day discharge windows (98 stations missing 3 leading days). `aquacast` at the pinned
revision serves them. Do not chase that.

The mechanism was reproduced against real polars on `origin/main`:

1. `services/operational_inputs.py` (`assemble_station_operational_inputs`, the `# --- past_dynamic ---`
   block, ~1018–1024): when the station has no reanalysis rows, `raw_forcing_to_dataframe` returns `None` and
   the code sets `past_dynamic = pl.DataFrame()` — a frame with **zero columns**.
2. `services/run_group_forecast.py::_stack_station_frames` (~116–119) does `pl.concat([...])` (vertical). One
   zero-column frame among 138 four-column frames raises
   `ShapeError: unable to append to a DataFrame of width 4 with a DataFrame of width 1` (the width-1 is the
   `station_id` column added by `_with_station_id`). Reproduced.
3. `flows/run_forecast_cycle.py`, the group branch: the `except Exception` around
   `assemble_group_operational_inputs` logs `forecast_cycle.group_input_assembly_failed`, appends a string to
   `errors`, and `continue`s. The flow run finishes Completed. `_forecast_cycle_health` looks only at
   `stations_failed`, which this path does not touch ("do not double-count", per the comment above the
   missing-binding check). `_emit_forecast_freshness_record` is OK if *any* forecast was stored anywhere in the
   cycle. Nothing in `pipeline_health` names the group.

Part A fixes this cause. Part B fixes the blindness, which matters more: the next cause will be a different one.

## Facts checked at `origin/main` (2026-09-30)

- **The existing per-station skip is the model to copy.** In `assemble_group_operational_inputs`
  (`run_group_forecast.py`, ~138–200) a station whose assembly returns `None` is logged
  `run_group_forecast.station_inputs_unavailable` (group, station, model, issue time) and dropped; if none
  remain, `run_group_forecast.no_serviceable_stations` and `None`. The cadence-gap skip
  (`operational_inputs.cadence_mismatch_skip`, `return None`) already feeds it. **Today the log carries no
  reason** — the two causes are indistinguishable there.
- **Whole-group drop paths found (all log and return without a forecast, none reach health):**
  | Path | Where | Today's signal |
  |---|---|---|
  | input assembly raises (this bug) | flow group branch, `except Exception` | `forecast_cycle.group_input_assembly_failed` + `errors` string |
  | no station serviceable | `assemble_group_operational_inputs` returns `None` | `run_group_forecast.no_serviceable_stations`, then flow `forecast_cycle.group_skipped_no_serviceable_stations` (INFO) |
  | member without a FORECAST binding | flow: `ConfigurationError` raised in the group branch | see Q4 — where it is caught is UNVERIFIED |
  | NWP coverage short for any member | `run_group_forecast`, `nwp.insufficient_coverage`, `GroupForecastOutcome(results={})` | WARNING only |
  | `predict_batch` raises | `run_group_forecast.predict_batch_failed` (`ModelOutputError`, or any other non-store exception), `results={}` | WARNING with `forcing_gaps` |
  | `predict_batch` returns empty | `run_group_forecast.batch_empty` | WARNING |
  | model returned `ModelFailure(INPUT_DATA)` | `adapters/forecast_interface.py::_output_from_result` turns any `ModelFailure` into `ModelOutputError("ForecastInterface model failure: <CAUSE>: ...")`, which lands in the `predict_batch_failed` branch above | the cause is only inside the message string |
- **A partial batch** (`batch_missing_station_outputs`) is not a whole-group drop; it is out of scope here.
- **There is already a precedent for a per-subject health record**: `_record_station_dark` writes a
  `FORECAST_STATION_DARK` record, status CRITICAL, subject = station id, detail JSON with reason, assigned models
  and `nwp_enabled`. `pipeline_health.check_type` is TEXT, not check-constrained
  (`docs/conventions.md` ~476), so a new type needs **no migration**.
- **`_forecast_cycle_health`** (`run_forecast_cycle.py` ~1384): FAILED only when every attempted station failed;
  DEGRADED for `stations_failed > 0` or any of the four flags; else HEALTHY. `ForecastCycleResult` has no group
  field.
- **The FI rule.** CLAUDE.md: an anticipated failure is **returned as `ModelFailure`, never raised** by the
  model. The SAP3-side conversion of a returned `ModelFailure` into `ModelOutputError` is the FI adapter's own
  boundary handling (the single SAP3↔FI boundary), not a model raising — so this plan is consistent with the
  rule so long as it changes nothing on the model side. What Part B needs from that boundary is the failure
  **cause as a value**, not as text inside a message (T4). If that cannot be done without changing the FI
  contract, the FI repo gets an issue; SAP3 does not work around it.

## Decisions (recommended answers; the owner confirms)

| # | Decision | Why |
|---|---|---|
| D1 | **Part A drops the member, not the group.** A member whose `past_dynamic` has zero columns, for a model that declares at least one `past_dynamic_feature`, is treated as `inputs unavailable`: dropped before stacking, logged `run_group_forecast.station_inputs_unavailable` with a new `reason` field. | The cadence-gap skip already sets the rule: one station without usable inputs does not deny the others a forecast. |
| D2 | **The check is by declared need, not by width alone.** A model that declares no `past_dynamic_features` legitimately gets zero-column frames from every member; those are never dropped. | Otherwise the fix would drop every member of a model that needs no past forcing. |
| D3 | **`reason` is a closed vocabulary** (an `Enum`, not free text): at least `empty_past_forcing`, `cadence_mismatch`, `no_observations`, `no_nwp`, `other`. The existing `None`-returning causes in `assemble_station_operational_inputs` are given their reason where they return. | A reason that is a string in a message cannot be counted or alerted on. |
| D4 | **Part B: a dropped group is DEGRADED, not FAILED, and the flow run stays Completed.** The health value and one health record per dropped group carry the signal. | See "State decision" below. |
| D5 | **New check type `FORECAST_GROUP_DROPPED` (`forecast_group_dropped`)**, one record per group per cycle, written only when something was dropped. Status WARNING for a whole-group drop. Subject = group id. Detail JSON: `group_id`, `model_id`, `reason` (closed vocabulary), `station_ids` (the stations responsible where known, else the members), `served_station_count` (0 for a whole-group drop). | A distinct name keeps the watchdog's `FORECAST_FRESHNESS` probe (`limit=1`, latest record) unpolluted — the same reason Plans 318 and 323 gave for their own types. |
| D6 | **A partial skip (some members dropped, the group still served) is recorded in the same check type with status OK and the skipped station ids; it does not change cycle health.** | One member's data gap must not turn every cycle DEGRADED, but it must not be silent either (see Q2). |
| D7 | **The fix stays out of the model side.** No model changes. | FI adherence. |

### State decision (Prefect run state versus health)

Recommendation: **the flow run stays Completed; health becomes DEGRADED; the record is alert-eligible.**

- `docs/standards/orchestration.md` (read 2026-09-30) contains no rule that a partial cycle failure must fail the
  run; the run state answers "did the flow execute", and the flow already reports quality through
  `ForecastCycleHealth` and `ForecastCycleResult`. Failing the run for one group would make a healthy 40-station
  cycle look like an outage. (UNVERIFIED: whether any deployment schedule, retry policy or automation keys on
  state — the implementer greps `flows/` deployments and `docs/standards/orchestration.md` before T3.)
- The watchdog and alerting split (`docs/architecture-context.md`, Flow 4 and the "monitoring ≠ alerts"
  decision): Flow 4 aggregates health records and raises operations alerts (`AlertSource.PIPELINE`); model-output
  alerts are a different thing. A `pipeline_health` record is monitoring; whether it also pages an operator is a
  watchdog probe question (Q3). This plan writes the record and does not add the probe unless the owner says so.
- Precedent: `FORECAST_STATION_DARK` already reports a station-level absence by record, not by failing the run.
- The genuinely bad case — **zero forecasts stored** — is already CRITICAL through `FORECAST_FRESHNESS`. This plan
  does not change that.

## Tasks

### T1 — Failing test: one empty-forcing member serves the others
**Outcome**: a test that reproduces the drop, with real polars.
- Build a group of 3 members whose model declares a `past_dynamic` feature; one member's assembled inputs carry
  `past_dynamic = pl.DataFrame()`. Call `assemble_group_operational_inputs` (through the service's own seams; the
  file's `TestRealAssemblerWarmUpProvenance` shows how) and assert: the two others are returned in `station_ids`,
  the empty one is absent, and `run_group_forecast.station_inputs_unavailable` was logged with the empty member's
  id and `reason="empty_past_forcing"`.
- Add: a model that declares **no** `past_dynamic_features` with zero-column frames for all members keeps every
  member (D2). And: **all** members empty gives `None` plus `no_serviceable_stations` (no crash).
**In / Out**: `tests/unit/services/test_run_group_forecast.py`. Out: any source change.
**Verification**: `uv run pytest tests/unit/services/test_run_group_forecast.py -k empty_past_forcing` — fails on
`main` with the `ShapeError` (assert the exception class in the RED evidence, not just "fails").
**Pre-change**: RED for the drop test; the D2 test passes on `main` (it is a guard for T2, say so).

### T2 — The reason vocabulary and the Part A fix
**Outcome**: the empty-forcing member is dropped before stacking; every per-station skip carries a reason.
- Add the closed reason type (a frozen result or enum in `types/`, following the repo's parse-don't-validate
  style) and make the station-level skip return it instead of a bare `None`, or carry it beside the `None`.
- In `assemble_group_operational_inputs`, drop a member whose `past_dynamic.width == 0` when
  `model.data_requirements.past_dynamic_features` is non-empty (D1, D2). Log
  `run_group_forecast.station_inputs_unavailable` with `reason`.
- Do not change `_stack_station_frames` semantics: it stays strict, so a future width mismatch still fails loudly.
**In / Out**: `services/operational_inputs.py`, `services/run_group_forecast.py`, the reason type. Out: the flow,
the station path (see Not in scope), models.
**Verification**: T1 passes; `uv run pytest tests/unit/services -q`; `uv run pyright`; `uv run ruff check`.
**Pre-change**: T1's RED, mutation-checked — remove only the width check and T1 fails again.

### T3 — Count group drops into cycle health
**Outcome**: a cycle in which a group was dropped reads DEGRADED and says how many.
- `ForecastCycleResult` gains `groups_dropped: int` (default 0; the type is `kw_only`, so existing constructions
  keep working — grep tests for direct constructions anyway).
- `_forecast_cycle_health` gains a `groups_dropped` parameter: `> 0` → DEGRADED. It never yields FAILED on its
  own (D4).
- The flow's group branch counts a drop for each path in the table above; `errors` keeps its string, now also
  carrying the reason. Group drops are **not** added to `stations_failed`.
- `forecast_cycle.complete` logs `groups_dropped`.
**In / Out**: `flows/run_forecast_cycle.py`, `ForecastCycleResult`, tests in `tests/unit/flows/`. Out: the record
(T5), the watchdog.
**Verification**: a flow-level test with one group whose assembly is made to fail: health is DEGRADED,
`groups_dropped == 1`, `stations_failed` unchanged, and the run returns normally. Existing health tests pass
unchanged (the parameter defaults to 0).
**Pre-change**: RED — on `main` the same scenario returns HEALTHY.

### T4 — Return the drop reason from the group service, and the FI failure cause as a value
**Outcome**: `run_group_forecast` and the assembler say *why* nothing was produced, without the flow parsing log
text.
- `GroupForecastOutcome` gains an optional `drop` (reason from the closed vocabulary, plus detail); each
  whole-group `return GroupForecastOutcome(results={})` (`nwp.insufficient_coverage`, `predict_batch_failed`,
  `batch_empty` when nothing else came back) sets it. Existing log events keep their names and fields.
- The assembler's `None` return (no serviceable station) is given a typed reason the same way.
- `ModelOutputError` raised from `_output_from_result` carries the FI `ModelFailure.cause` as an attribute so the
  group service can map `INPUT_DATA` to a distinct reason (`model_input_data`). **UNVERIFIED:** whether
  `ModelOutputError` already has such a field; check `exceptions.py` before starting. If a new attribute is
  needed it is SAP3-internal and needs no FI change; if the FI's `ModelFailure` cannot express the cause, file an
  FI issue rather than parse the message.
**In / Out**: `services/run_group_forecast.py`, `adapters/forecast_interface.py`, `exceptions.py`, tests. Out: the
FI package, models.
**Verification**: one unit test per path asserts the reason on the outcome; a test using a fake FI model that
returns `ModelFailure(INPUT_DATA)` asserts `model_input_data`.
**Pre-change**: RED — the outcome has no such field.

### T5 — One health record per dropped group
**Outcome**: `pipeline_health` names the group.
- Add `PipelineCheckType.FORECAST_GROUP_DROPPED`; write via the existing `_append_pipeline_health_record`
  (it already swallows and logs write failures as `pipeline.health_record_write_failed`, so the record can never
  abort a cycle). Record content per D5/D6; skip the write when the run has an explicit `cycle_time` (backfill),
  as the freshness record does, and for the same reason.
- Cover the `ConfigurationError` path (member without a FORECAST binding): the drop is counted and recorded, with
  the missing station ids.
- The record is written where the drop is decided, once per group per cycle, before Phase C.
**In / Out**: `types/enums.py`, `flows/run_forecast_cycle.py`, tests. Out: the watchdog probe (Q3), migrations
(TEXT column).
**Verification**: a flow test per path reads the record back from a fake `PipelineHealthStore` and asserts
check type, subject, status, and detail keys (`group_id`, `model_id`, `reason`, `station_ids`,
`served_station_count`); a test that a served group with a skipped member writes an OK record listing that
member; a test that a failing `append_health_record` does not fail the cycle.
**Pre-change**: RED — the check type does not exist.

### T6 — Documents
**Outcome**: the record and the new events are documented where the next reader will look.
- `docs/touchpoint-maps.md`, forecast-cycle map: add the group-drop paths, the new check type, and the
  `ForecastCycleResult.groups_dropped` field to "cycle health / result assembly"; rewrite the existing Plan 312
  warning there (that "the cycle passed" is no evidence) to say the drop is now also a health record.
- `docs/standards/logging.md` event table: `run_group_forecast.station_inputs_unavailable` (now with `reason`),
  `forecast_cycle.group_input_assembly_failed`, `forecast_cycle.group_dropped` (new summary event, WARNING).
- `docs/conventions.md` (~476): add `forecast_group_dropped` to the `check_type` list.
- `docs/plans/262-onboard-cmal-small-on-a-small-swiss-group.md`: add a note that a **member-input assembly
  failure logs `forecast_cycle.group_input_assembly_failed`, not `run_group_forecast.predict_batch_failed`**, so
  the plan's "what to grep for after the first run" (its ~793 and ~1117 references) misses this cause. Per
  the memory rule on correction notes: fix the wrong sentence, do not add a note beside it.
- `docs/plans/README.md`: the row for 514.
**Verification**: grep each new event and check-type name in `docs/`; `git diff --stat` shows only these files.
**Pre-change**: N/A — documentation.

### T7 — Live check (staging host, orchestrator action)
**Outcome**: evidence on the running system, not on Prefect state.
- **Before recreating any container, save the worker logs** (`docker logs` to a file on the host), so the
  next investigation is not blind. The 2026-09-29 logs were lost this way.
- After deployment run one cycle. Measure **the number of `cmal_small` forecasts stored for the run's cycle
  time** (a database count by model id and issue time), not the Prefect state. Expect the 138 servable members
  (and 2024 absent while Plan 516 is open) and a `forecast_group_dropped` record with status OK naming 2024.
- Do not break staging to prove the whole-group drop path; T5's tests cover it. If the owner wants a live
  proof, provoke it on a scratch group, never on the pilot group.
- Record: forecast count, the record's detail JSON, the cycle's `health` and `groups_dropped`.
**Verification**: the count, the record, and the saved log file path in a dated note in this plan.
**Pre-change**: baseline 2026-09-29: 0 `cmal_small` rows.

## Exit gates

1. T1 fails on `main` with the `ShapeError` and passes after T2; the D2 guard test passes before and after.
2. Every whole-group drop path in the table has a test showing a counted drop, a record with the right reason,
   and DEGRADED health; no path adds to `stations_failed`.
3. `uv run pytest tests/unit -q`, `uv run pytest tests/integration -q`, `uv run pyright`, `uv run ruff check`
   and `uv run ruff format --check` pass after the final change. Local venv lacks the `aquacast` extra (CI has
   it): also run the CI shards, not only the touched suites.
4. Multi-model review of the plan, then of the patch; the extra owner-commissioned review (high risk).
5. T7's live count is recorded.

## Not in this plan

- **The station path** (`run_station_forecast` for STATION-scoped models). An empty `past_dynamic` there fails one
  station, which already counts in `stations_failed`. Whether it should degrade the same way is a separate
  question.
- **Fixing station 2024's binding** or removing it from the group: Plan 516.
- **A watchdog probe or an operator alert** for the new record (Q3), unless the owner says otherwise.
- **Changing `FORECAST_FRESHNESS`** semantics, or the FAILED threshold.
- **Partial batches** (`batch_missing_station_outputs`) and per-station QC rejection (Plan 404).
- **Any model or FI package change.** A cause the FI cannot express becomes an FI issue.
- **Retries.** No group is retried within a cycle.

## Risks

| Risk | Mitigation |
|---|---|
| The width check drops members of a model that needs no past forcing | D2 and its guard test. |
| DEGRADED on every cycle for a group that always has one bad member | D6: partial skips do not change health; only whole-group drops do. Q2 records the owner's call. |
| The new record hides or displaces the freshness heartbeat | A distinct check type; the freshness probe reads only its own type (`limit=1`). |
| A dropped member silently changes a group model's inputs (a group forecast trained on 139 stations now sees 138) | Group models are per-station in `predict_batch`; UNVERIFIED for pooled models — the implementer reads `aquacast`'s `predict_batch` and states whether a missing member changes the others' output. The existing cadence skip already relies on this. |
| A reason mapped wrongly (`other` swallows real causes) | `other` is logged at WARNING with the exception class and counted; T4 has one test per path. |
| A health-write failure aborts a cycle | `_append_pipeline_health_record` already swallows and logs; T5 tests it. |
| Scope creep into the station path | Not in scope above. |

## Open questions (plain language, with a recommendation)

- **Q1 — A dropped group: does the run itself fail?** Today the run finishes "Completed" even when a whole group of
  139 stations produced nothing. *Recommendation:* keep the run Completed but mark the cycle "degraded" and write a
  health record naming the group; a failed run for one group would look like a full outage. The bad case of zero
  forecasts overall is already flagged critical.
- **Q2 — Should one skipped station in an otherwise working group make the cycle "degraded"?** With 139 stations,
  a short data gap at one of them will be routine. *Recommendation:* no — record it, do not degrade the cycle.
- **Q3 — Should a dropped group page or notify someone?** This plan only writes the record. *Recommendation:* yes,
  add a watchdog check for the new record as a follow-on plan (same pattern as the QC "unchecked" check), so a group
  that stays dark for several cycles raises an operations alert.
- **Q4 — Where is the "member without a forecast source" error caught today?** It is raised inside the group
  branch; the code there does not show whether an outer handler counts it. *Recommendation:* the implementer traces
  it first (UNVERIFIED) and the plan's T5 covers it either way; if it is currently fatal to the whole cycle, tell
  the owner, because that changes its risk.

## Dependency graph

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1"] },
    { "id": "phase-2", "tasks": ["T2", "T4"], "depends_on": ["phase-1"], "parallel": true },
    { "id": "phase-3", "tasks": ["T3", "T5"], "depends_on": ["phase-2"], "parallel": false },
    { "id": "phase-4", "tasks": ["T6"], "depends_on": ["phase-3"] },
    { "id": "phase-5", "tasks": ["T7"], "depends_on": ["phase-4"] }
  ]
}
```
