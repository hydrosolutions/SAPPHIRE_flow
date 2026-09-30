---
status: DRAFT
created: 2026-09-30
plan: 514
title: One group member with empty past forcing must not drop the whole group; a dropped group must show in cycle health
scope: (A) In the group input path every stacked frame is conformed to the model's declared schema (missing declared columns are null-filled, never a stacking crash), so one member with missing forcing cannot drop the group; the FI adapter's max_nan gate then decides whether that member is served. (B) Every recoverable whole-group drop and every skipped or null-filled member becomes visible - counted in cycle health and written once per group-model execution to the pipeline-health table.
risk: high   # FI-boundary edit (docs/workflow.md high-risk list) and scientific behaviour: which stations get a forecast
related: [262, 312, 116, 223, 239, 257, 327, 404, 323, 517]
open_decisions: [Q1, Q2, Q3, Q5, Q6]
source: 2026-09-29 pilot run `powerful-leech` - investigation by the orchestrator session; revised 2026-09-30 after one Claude and one Codex review
---

# Plan 514 - a group member with missing forcing must not drop the group, and a dropped group must be visible

## Status

**DRAFT - HIGH RISK (proposed; the owner decides).** Multi-model review is mandatory
(`docs/workflow.md` § Multi-Model Review), plus the one extra owner-commissioned review high-risk work gets. Only
the orchestrator sets READY.

**Why high risk (corrected 2026-09-30).** `docs/workflow.md` (~144-165) does not treat "touches the live
deployment" as a ground. The two grounds that apply are: (1) the plan edits the **FI-boundary path** (the group
input frames handed to the FI adapter, and the SAP3-internal cause carried by the adapter's boundary error, T4);
(2) it changes **scientific behaviour with operational consequences** - which stations receive a forecast, and
from which inputs (a null-filled member may now be served or refused by `max_nan`, where today the whole group
crashes).

## Owner decisions (2026-09-30)

- **(a) Consistent with the 2026-09-08 decision (Plan 239 T1b: past-forcing gaps become an input-quality flag,
  never a refusal; see `services/run_group_forecast.py` ~398-402).** A group member with missing or empty past
  forcing is **not refused and not dropped**. Its missing declared columns are **null-filled at stack time** so
  the frames stack, the FI adapter's `max_nan` gate then decides whether that station is served, and
  `past_forcing_flags` still records the quality flag. This **replaces** the earlier draft's "drop the member
  with a typed reason" design. Strict stacking is kept for **non-zero-width schema mismatches beyond the declared
  schema** (extra or wrongly named columns): those still surface as an assembly failure, made visible by Part B.
- **(b) The immediate operational unblock is already done.** On 2026-09-30 the owner authorised removing station
  **2024** from the pilot group (139 -> **138**) through a one-off direct database write. This is recorded as an
  **exception, not a precedent** (see Plan 517); the audited group-member route in Plan 517 is the durable path.
  Consequence for this plan: the live pilot group no longer contains the suspected trigger, so Part A cannot be
  proven on the pilot group any more (T7).

## Why

On **2026-09-29** the pilot run `powerful-leech` ended **Completed with zero `cmal_small` rows**. Nothing in
Prefect state, in the database or in the health record said a whole 139-station group had produced nothing.

**Most likely cause - still UNVERIFIED (the worker logs were gone before anyone read them; nothing measured
since confirms it):** station **2024**, the one member without a reanalysis weather binding (Plan 517), whose
past forcing comes back empty and crashes the group's frame stacking. The removal of 2024 on 2026-09-30 is an
operational mitigation, not a confirmation. Where this plan says "the bug" it means the mechanism reproduced on
`origin/main` below, not that it was proven to be the 09-29 cause.

**Ruled out:** the short 30-day discharge windows (98 stations missing 3 leading days). `aquacast` at the pinned
revision serves them.

The mechanism, reproduced against real polars on `origin/main` (re-read 2026-09-30):

1. `services/operational_inputs.py`, `# --- past_dynamic ---` block (~1018-1024): when the station has no
   reanalysis rows `raw_forcing_to_dataframe` returns `None` and the code sets `past_dynamic = pl.DataFrame()` - a
   frame with **zero columns**.
2. `services/run_group_forecast.py::_stack_station_frames` (~116-119) does a vertical `pl.concat`. One
   zero-column frame among four-column frames raises `ShapeError: unable to append to a DataFrame of width 4 with
   a DataFrame of width 1` (the width 1 is the `station_id` column `_with_station_id` adds).
3. The flow's group branch (`flows/run_forecast_cycle.py` ~3853-3862): the `except Exception` around
   `assemble_group_operational_inputs` logs `forecast_cycle.group_input_assembly_failed`, appends to `errors`,
   `continue`s. The run finishes Completed; `_forecast_cycle_health` looks only at `stations_failed`, which this
   path does not touch; `_emit_forecast_freshness_record` is OK if any forecast was stored anywhere in the
   cycle; nothing in `pipeline_health` names the group.

### The same defect class is wider than the empty `past_dynamic` (review finding, verified)

The stack is strict about every frame, so any frame whose schema differs from its siblings crashes it the same way:

| Frame | How it goes wrong today | Verified at |
|---|---|---|
| `past_dynamic`, partial columns (width 4 vs 3) | one of several declared features has no rows | `raw_forcing_to_dataframe` builds columns only from rows present |
| `past_dynamic`, different column order | `pl.concat` vertical is strict on order | polars behaviour; UNVERIFIED as a live case |
| `past_targets`, zero columns | no observations: `observations_to_wide_dataframe([])` returns `pl.DataFrame()` (`operational_inputs.py` ~432); the station path logs `operational_inputs.no_observations` (~966) and continues | code |
| `future_dynamic`, zero columns | no NWP records: `_pivot_nwp_records([])` returns an empty `pl.DataFrame()`; the stack crashes **before** the `nwp.insufficient_coverage` gate would have judged it | code; the gate runs after assembly |

**General rule (this plan's Part A):** *every frame stacked into a `GroupModelInputs` is conformed to the model's
declared schema first* - missing declared columns are added as nulls, columns are ordered as declared - and
only then stacked. Stacking stays strict for anything the declared schema does not explain.

**Caution kept from review:** in the **station** path `no_observations` and `no_nwp` deliberately log-and-continue
so that a fallback model (for example a climatology floor) still runs on that station. They are **not** turned
into skips in the shared function. The group assembler change is local to `run_group_forecast.py`.

## Facts checked at `origin/main` (2026-09-30)

- **Two callers of `assemble_station_operational_inputs`, one checks `is None`.** `flows/run_forecast_cycle.py`
  (~3453, ~3483) tests `inputs_result is None` and otherwise unpacks a 2-tuple; many tests assert `None`. Changing
  the return type would break the station path (Codex P1, verified). Backward compatibility is therefore a
  design constraint (T2).
- **The max_nan gate is per station and counts nulls in the rows it has.** `ForecastInterfaceAdapter.predict_batch`
  (`adapters/forecast_interface.py` ~1201-1235) filters each station's rows out of the stacked frames, calls
  `_variables_over_nan_tolerance`, drops the station (logging
  `forecast_interface.station_input_nan_tolerance_exceeded`) when a variable is over tolerance, and raises
  `ModelOutputError` only when **all** stations fail. `_missing_value_count` counts nulls and NaNs
  (`series.null_count()`). **Consequences that shape the null-fill:**
  1. A **zero-row** null-filled frame has `null_count() == 0`, so the gate would **pass** and the model would be
     handed an empty series. The null-fill therefore must materialise the **expected time grid**
     (`expected_past_buckets(anchor, time_step, lookback_steps)`, already used by `past_forcing_flags`) with null
     values, so the gate can count them.
  2. `services/training_data.py::missing_buckets` is **membership only** (a slot counts as present if its
     timestamp appears). A null-filled grid therefore makes `past_forcing_flags` report **no gap** - the
     quality flag the owner decision requires would be lost. The flag must be derived from the **pre-fill**
     frame (or from an explicit null-filled record carried out of the assembler), with a test.
  3. `max_nan` is only as strict as the model's declared tolerance. **UNVERIFIED:** the tolerances declared by
     the pinned `aquacast` model; if a tolerance is at least the window length, a fully null station is served
     from all-null forcing. T1 reads and records them before T2 lands.
- **The existing per-station skip** (`run_group_forecast.station_inputs_unavailable`, then
  `no_serviceable_stations` and `None`) exists for a station whose assembly returns `None` (today: the cadence
  mismatch, `operational_inputs.cadence_mismatch_skip`, ~929-941). Its log carries no reason.
- **Whole-group drop paths (all recoverable, all log and return without a forecast, none reach health).**
  Corrected and completed after review:

  | Path | Where | Today's signal |
  |---|---|---|
  | input assembly raises (this bug) | flow group branch, generic handler | `forecast_cycle.group_input_assembly_failed` + `errors` string |
  | no station serviceable | assembler returns `None` | `run_group_forecast.no_serviceable_stations`; flow `group_skipped_no_serviceable_stations` (INFO) |
  | member without a FORECAST binding | flow: `ConfigurationError` raised in the group branch (~3815-3823) | caught by the generic handler (~4006-4021), **not cycle-fatal** (Q4 answered from code); the stations were already counted in `stations_failed` |
  | NWP coverage short for any member | `run_group_forecast`, `nwp.insufficient_coverage`, `results={}` | WARNING only; note **one** short member drops the whole group here too (Q5) |
  | active artifact fetch failed | `run_group_forecast.artifact_fetch_failed` (~556-569), `results={}` | WARNING (a fatal connection error is re-raised as `StoreError` and must keep propagating) |
  | no active artifact | `run_group_forecast.no_active_artifact` (~571-577), `results={}` | WARNING |
  | `predict_batch` raises | `run_group_forecast.predict_batch_failed` (`ModelOutputError` or other), `results={}` | WARNING with `forcing_gaps` |
  | `predict_batch` returns empty | `run_group_forecast.batch_empty` | WARNING |
  | model returned `ModelFailure(INPUT_DATA)` | the adapter's `_output_from_result` raises `ModelOutputError("ForecastInterface model failure: <CAUSE>: ...")`, landing in the `predict_batch_failed` branch | the cause exists only inside a message string |
  | result building raises | `run_group_forecast` wraps it in `GroupForecastError` (~710); the flow handler (~3981-4004) logs `forecast_cycle.group_forecast_failed`, buffers `exc.rejected` (Plan 404), appends to `errors`, `continue`s | WARNING + `errors` string |
  | any other exception in the group branch | generic `except Exception` (~4006-4021) logs the same `forecast_cycle.group_forecast_failed` | WARNING + `errors` string; a **fatal** persistence failure (`fatal_group_store_failure`) re-raises there |

- **Not drops, and classified as such (must not be counted):** an intentional skip
  (`group_skipped_duplicate_station_model_members`, every member already produced by another route) and an
  **all-QC-rejected** outcome (the group ran; its forecasts were rejected and buffered by Plan 404). A partial
  batch (`batch_missing_station_outputs`) is out of scope.
- **Must keep propagating:** `StoreError` and a fatal `store_forecast` failure. They must never become
  Completed/DEGRADED.
- **Execution unit is a (group, model) pair.** `discover_group_runs` (`run_group_forecast.py` ~251) yields
  `(group, model_id)` for every group model. One group can run several models; the earlier "one record per group
  per cycle with one `model_id`" was ill-defined.
- **Precedent for a per-subject health record:** `_record_station_dark` writes `FORECAST_STATION_DARK`.
  `pipeline_health.check_type` is TEXT, not check-constrained (`docs/conventions.md` ~476): no migration.
- **`_forecast_cycle_health`** (`run_forecast_cycle.py` ~1384): FAILED only when every attempted station failed;
  DEGRADED for `stations_failed > 0` or any of four flags; else HEALTHY. **FAILED precedence is existing
  behaviour and is preserved:** a cycle whose stations all already failed reads FAILED and stays FAILED with a
  dropped group on top. Exit gate 2 therefore cannot require DEGRADED universally.
- **`ModelOutputError` has no cause field** (`exceptions.py` 32-33: a bare `SapphireError` subclass). The FI's
  `ModelFailure.cause` exists in the pinned package. A new **SAP3-internal** attribute set on the
  `ModelFailure` -> `ModelOutputError` conversion is enough; no FI issue is needed (Codex, agreed).
- **The FI rule.** An anticipated failure is **returned as `ModelFailure`, never raised** by the model. This plan
  changes nothing on the model side; the conversion is the adapter's own boundary handling.
- **Live state (2026-09-30):** the pilot group `swiss-cmal-small-pilot` is now **138** stations. **Three sparse
  stations exist, two with no data since 2026-09-22.** After Part A they are neither dropping the group nor
  refused by SAP3: each is served or skipped **per station** by the adapter's `max_nan` gate (and by the cadence
  skip). Which of the three are served is a **measurement**, not a promise of this plan (T7).

## Decisions (recommended answers; the owner confirms)

| # | Decision | Why |
|---|---|---|
| D1 | **Part A conforms, it does not drop.** Before stacking, each member's `past_targets`, `past_dynamic` and `future_dynamic` are conformed to the model's declared schema: declared columns absent from the frame are added as nulls on the expected time grid, and the columns are put in declared order. `_stack_station_frames` itself stays strict. | Owner decision (a): consistent with Plan 239 T1b - a gap is a quality flag, not a refusal. |
| D2 | **Conformance is by declared need.** A model that declares no `past_dynamic_features` gets no columns added; its zero-column frames stay zero-column. A frame with columns **beyond** the declared schema, or with a non-zero-width mismatch the declared schema does not explain, is **not** repaired: it still fails stacking and is reported as an assembly failure (made visible by Part B). | Otherwise conformance would hide real schema faults. |
| D3 | **What is null-filled, precisely.** Past frames (`past_targets`, `past_dynamic`): the declared columns, on the expected grid (`timestamp` + nulls). `future_dynamic`: its declared columns depend on the model's ensemble mode (member-suffixed columns), so a zero-width member frame is conformed to the **column set of the other members' frames** with zero rows; the existing `nwp.insufficient_coverage` gate then judges it (today that gate drops the whole group on one short member - Q5). | The past grid is knowable from the anchor; the future column set is not derivable without the member count. UNVERIFIED which reference to use for the future frame; T2 decides with a test. |
| D4 | **The only remaining hard drop** is a member whose assembly returns `None` (today the cadence mismatch) - existing behaviour, unchanged. There is **no new drop reason**: with a declared schema, null-filling is always possible. If the model's declared schema cannot be read, conformance is skipped and stacking stays strict (fails loudly, visible via Part B). | Owner decision (a): hard-drop only where null-filling is impossible. |
| D5 | **The quality flag survives the fill.** `past_forcing_flags` is computed from the pre-fill frame (or from an explicit null-filled record); a test proves a null-filled member still carries its own past-forcing gap flag and no sibling is labelled. | `missing_buckets` is membership-only (Facts). |
| D6 | **Reasons are conveyed by a sibling function, not by changing a return type.** `assemble_station_operational_inputs` keeps returning `None` and its 2-tuple, unchanged. A sibling `assemble_station_operational_inputs_outcome` (same arguments) returns a small frozen union - the inputs, or a `StationInputsSkipped(reason)` with a closed-vocabulary `reason` (today only `cadence_mismatch`) - and the original becomes a thin wrapper that maps a skip to `None`. Only the group assembler calls the sibling. Same pattern for the group assembler: `assemble_group_operational_inputs` keeps its signature and return; a sibling returns a frozen `GroupAssembly` (inputs, metadata, skipped members with reasons, null-filled members with their columns, or a group-level drop reason) that the flow calls. | Codex P1: the station caller checks `is None`, and tests assert `None`. |
| D7 | **The unit of a record is a group-model execution**, and **exactly one record is written after its final outcome is known** - never an OK followed by a WARNING for the same run. Skipped and null-filled member information rides through successful assembly (D6) and is attached to the single final record. | Codex P2: execution iterates `(group, model)`. |
| D8 | **Part B: a dropped group-model execution is DEGRADED, the flow run stays Completed.** Recoverable drops (table above) increment `ForecastCycleResult.groups_dropped`. Fatal persistence failures and `StoreError` still propagate and are **not** counted. Existing FAILED precedence is preserved. | See "State decision". |
| D9 | **New check type `FORECAST_GROUP_DROPPED` (`forecast_group_dropped`).** Subject = group id. Status WARNING for a whole-group drop; OK for a served execution that had skipped or null-filled members. Detail JSON: `group_id`, `model_id`, `reason` (closed vocabulary), `served_station_count`, `dropped_station_count`, `null_filled_station_ids`, `skipped_station_ids`, and the responsible station ids where known. Served-versus-dropped counts are in every record. | A distinct name keeps the freshness probe (`limit=1`, latest) unpolluted. |
| D10 | **Repeated identical records are bounded.** At most one record per group-model execution per cycle; none when the run has an explicit `cycle_time` (backfill/replay); none for a Plan 327 re-run of an already-recorded cycle time. UNVERIFIED how Plan 327 re-runs reach this code; the implementer reads Plan 327 before T5. | Codex: dedupe/cap. |
| D11 | **No model change.** | FI adherence. |
| D12 | **A large dropped fraction is not a threshold this plan sets.** The record carries served-versus-dropped counts; what fraction turns a cycle DEGRADED (today: any whole-group drop) is an owner call (Q2). | Owner call. |

### State decision (Prefect run state versus health)

Recommendation unchanged: **the flow run stays Completed; health becomes DEGRADED; the record is monitoring
visibility only.**

- `docs/standards/orchestration.md` contains no rule that a partial cycle failure must fail the run. (UNVERIFIED:
  whether any deployment schedule, retry policy or automation keys on state - the implementer greps `flows/`
  deployments before T3.)
- **Wording corrected (review).** The earlier draft called the record "alert-eligible". It is **not**: the
  watchdog probes `forecast_freshness` only and does not consume the new type, and a successful sibling forecast
  keeps that probe OK while the new record is WARNING. The delivered outcome is **monitoring visibility only**
  (API health detail, admin-only). A watchdog probe is a follow-on (Q3); event-only records also need a
  recovery/expiry policy when that probe is designed.
- Precedent: `FORECAST_STATION_DARK` reports a station-level absence by record, not by failing the run.
- Zero forecasts stored is already CRITICAL through `FORECAST_FRESHNESS`. Unchanged.

## Tasks

Ownership of `services/run_group_forecast.py` is **sequential**: T2, then T4 (both edit it); they are not
parallel.

### T1 - Failing tests: the schema-conformance class, with real polars
**Outcome**: tests that reproduce each stacking crash, plus the guards.
- Mixed group of three members, model declaring a `past_dynamic` feature, one member with `past_dynamic =
  pl.DataFrame()`: today `ShapeError` (assert the exception class in the RED evidence). After T2: all three in
  `station_ids`, the empty member's declared columns all null on the expected grid.
- The same for: partial columns (width 4 vs 3); differing column order; zero-column `past_targets`; zero-column
  `future_dynamic`.
- **All members empty**: on `main` this does **not** raise `ShapeError` (nothing mismatches) - it returns inputs;
  state this in the RED evidence. After T2 it returns inputs with all-null forcing and the adapter's `max_nan`
  gate is what refuses them (asserted through the real adapter or a faithful fake).
- **Guards that pass on `main`** (say so): a model declaring no `past_dynamic_features` keeps zero-column frames.
- **Negative test:** a member frame with a non-zero-width mismatch beyond the declared schema (an extra or
  misnamed column) still fails stacking and is reported as an assembly failure (D2).
- A null-filled member keeps its own past-forcing gap flag and its siblings have none (D5).
- A test that a zero-row fill would have passed the gate (documenting why the grid is materialised).
- Read and record the pinned model's declared `max_nan` tolerances (UNVERIFIED above) in a dated note here.
**In / Out**: `tests/unit/services/test_run_group_forecast.py`. Out: source. The existing
`TestRealAssemblerWarmUpProvenance` fixture supplies empty forcing to a forcing-declaring fake and needs adjusting
after T2 (review note).
**Verification**: `uv run pytest tests/unit/services/test_run_group_forecast.py -k conform`.
**Pre-change**: RED for the mixed, partial, order, past_targets and future_dynamic cases; the all-empty case fails
as "returned inputs", not as `ShapeError`; the guards pass.

### T2 - Conform frames to the declared schema, and the sibling result functions
**Outcome**: Part A, without changing any existing return type.
- Add the conformance step in the group assembler (D1-D5), local to `run_group_forecast.py`.
  `operational_inputs.py` gains only the sibling `assemble_station_operational_inputs_outcome` (D6); the original
  keeps its behaviour, including the log-and-continue of `no_observations` and `no_nwp`.
- Add the closed reason type (`Enum`) and the frozen `GroupAssembly` / `StationInputsSkipped` results in
  `types/`, following the repo's parse-don't-validate style.
- Log `run_group_forecast.station_inputs_unavailable` with `reason` for the cadence skip, and a new
  `run_group_forecast.station_inputs_null_filled` (member, columns, frame) for each fill.
**In / Out**: `services/operational_inputs.py`, `services/run_group_forecast.py`, the result types. Out: the flow
(T3/T5), models, the station path's behaviour.
**Verification**: T1 passes; `uv run pytest tests/unit/services -q`; every existing test asserting `None` from
`assemble_station_operational_inputs` passes **unchanged**; `uv run pyright`; `uv run ruff check`.
**Pre-change**: mutation-check - remove only the conformance step and T1 fails again.

### T3 - Count group drops into cycle health
**Outcome**: a cycle in which a group-model execution was dropped reads DEGRADED and says how many.
- `ForecastCycleResult` gains `groups_dropped: int = 0` (grep tests for direct constructions anyway).
- `_forecast_cycle_health` gains a defaulted `groups_dropped` parameter: `> 0` -> DEGRADED. It never yields FAILED
  on its own, and **existing FAILED precedence is untouched**.
- The flow counts a drop for **each recoverable path in the table above**, including the artifact paths, the
  `GroupForecastError` handler and the generic handler. It does **not** count an intentional skip, an
  all-QC-rejected outcome or a partial batch. Plan 404's rejection buffering (both `group_outcome.rejected` and
  `exc.rejected` in the `GroupForecastError` handler) is preserved. `StoreError` and the fatal-persistence
  re-raise stay exactly as they are.
- Group drops are **not** added to `stations_failed`.
**In / Out**: `flows/run_forecast_cycle.py`, `ForecastCycleResult`, tests in `tests/unit/flows/`. Out: the record
(T5), the watchdog.
**Verification**: a flow-level test per path: DEGRADED, `groups_dropped == 1`, `stations_failed` unchanged, run
returns normally. Tests for the non-drops and for `StoreError` / fatal persistence still propagating. **A
partial-skip-then-failure test** (a member is skipped, then `predict_batch` fails: one drop counted, one final
record). **A two-models-sharing-a-group test.** A test that a cycle whose stations all failed stays FAILED with a
dropped group.
**Pre-change**: RED - on `main` the same scenario returns HEALTHY.

### T4 - The drop reason from the group service, and the FI failure cause as a value
**Outcome**: `run_group_forecast` says why nothing was produced, without the flow parsing log text.
- `GroupForecastOutcome` gains an optional `drop` (closed-vocabulary reason plus detail); every whole-group
  `return GroupForecastOutcome(results={})` sets it: `nwp.insufficient_coverage`, `artifact_fetch_failed`,
  `no_active_artifact`, `predict_batch_failed`, `batch_empty`. Existing log events keep their names and fields.
- The flow maps `GroupForecastError` and the generic handler into the same vocabulary (T3/T5).
- **`ModelOutputError` gets a new SAP3-internal attribute** (`cause`, default `None`) set where the adapter's
  `_output_from_result` converts a returned `ModelFailure`, from the FI's `ModelFailure.cause`. The group service
  maps `INPUT_DATA` to `model_input_data`. No FI change and no FI issue.
**In / Out**: `services/run_group_forecast.py`, `adapters/forecast_interface.py`, `exceptions.py`, tests. Out: the
FI package, models. **Runs after T2** (same file).
**Verification**: one unit test per path asserts the reason on the outcome; a fake FI model returning
`ModelFailure(INPUT_DATA)` asserts `model_input_data`.
**Pre-change**: RED - the outcome has no such field.

### T5 - One health record per group-model execution
**Outcome**: `pipeline_health` names the group, once, after the final outcome.
- Add `PipelineCheckType.FORECAST_GROUP_DROPPED`; write via `_append_pipeline_health_record` (swallows and logs
  write failures as `pipeline.health_record_write_failed`). Content per D9. One record per group-model
  execution, written where the final outcome is known (D7); skipped when the run has an explicit `cycle_time`,
  as the freshness record does (D10).
- The `ConfigurationError` (member without FORECAST binding) path records a WARNING with the missing station ids.
**In / Out**: `types/enums.py`, `flows/run_forecast_cycle.py`, tests. Out: watchdog probe (Q3), migrations.
**Verification**: a flow test per path reads the record back from a fake `PipelineHealthStore` and asserts check
type, subject, status and detail keys; a served group with null-filled or skipped members writes one OK record
listing them; **no OK-then-WARNING pair for one execution**; a failing `append_health_record` does not fail the
cycle; repeated records respect D10.
**Pre-change**: RED - the check type does not exist.

### T6 - Documents
**Outcome**: the record and the new events are documented where the next reader looks.
- `docs/touchpoint-maps.md` forecast-cycle map (~524-526, the freshness heartbeat entry, and "cycle health /
  result assembly"): the group-drop paths, the new check type, `groups_dropped`; rewrite the Plan 312 warning to
  say a drop is now also a health record.
- `docs/spec/types-and-protocols.md` (`PipelineCheckType` list ~213-221 and its NOTE ~221-223) and
  `docs/architecture-context.md` (~2673 the `check_type` list, ~2686 the enum mapping, ~2696 the detail shapes):
  add `forecast_group_dropped` and its detail shape. `docs/conventions.md` (~476): add it to the list.
- `docs/standards/logging.md`: **no group events are documented today**; add
  `run_group_forecast.station_inputs_unavailable` (with `reason`), `run_group_forecast.station_inputs_null_filled`,
  `forecast_cycle.group_input_assembly_failed`, `forecast_cycle.group_forecast_failed` and
  `forecast_cycle.group_dropped` (new summary event, WARNING).
- `docs/plans/262-...`: fix the wrong sentence (do not add a note beside it): a member-input assembly failure
  logs `forecast_cycle.group_input_assembly_failed`, not `run_group_forecast.predict_batch_failed`, so 262's
  "what to grep for" (~793, ~1117) misses this cause.
- **Deploy note (rollback hazard, verified):** `pipeline_health_store._row_to_domain` does
  `PipelineCheckType(row["check_type"])`, which raises `ValueError` for a value the running code does not know.
  After a rollback to a build without the new member, any read of a `forecast_group_dropped` row (API health
  detail) fails; an older API reading a newly written value fails the same way. Deploy note: roll the API and
  worker together (API first when rolling forward); before rolling back, delete or hide the new rows.
- `docs/plans/README.md`: the 514 row already exists; update it only if the title changes.
**Verification**: grep each new event and check-type name in `docs/`; `git diff --stat` shows only these files.

### T7 - Live check (staging host, orchestrator action)
**Outcome**: evidence on the running system, not on Prefect state.
- **Before recreating any container, save the worker logs** to a file on the host (the 2026-09-29 logs were
  lost this way).
- After deployment run one cycle. **Compare the actual station-ID set, not counts:** the set of station ids with
  a current `cmal_small` forecast for the intended model, parameter, issue time and current status, against the
  138 group members, and list every difference (duplicate or superseded rows can hide missing stations behind a
  right-looking count). Record how the three sparse stations (two with no data since 2026-09-22) were treated:
  served, or skipped by `max_nan`/cadence, with the adapter's `station_input_nan_tolerance_exceeded` lines.
- 2024 is no longer in the group, so **Part A cannot be shown live on the pilot group**. If the owner wants a live
  proof, provoke it on a scratch group, never on the pilot group. Do not break staging to prove the whole-group
  drop path; T5's tests cover it.
- Record: the station-id set difference, the record's detail JSON, the cycle's `health` and `groups_dropped`.
**Verification**: the set comparison, the record, and the saved log file path in a dated note in this plan.
**Pre-change**: baseline 2026-09-29: 0 `cmal_small` rows.

## Exit gates

1. T1's mixed-frame cases fail on `main` with the `ShapeError` and pass after T2; the all-empty case fails as
   "returned inputs" on `main`; the guards pass before and after; the negative test (non-zero-width mismatch)
   still reports an assembly failure.
2. Every **recoverable** whole-group drop path in the table has a test showing a counted drop, one record with the
   right reason, and DEGRADED health - **except** where the cycle is already FAILED, which stays FAILED. The two
   non-drops are not counted. `StoreError` and fatal persistence failures still propagate. No path adds to
   `stations_failed`.
3. `uv run pytest tests/unit -q`, `uv run pytest tests/integration -q`, `uv run pyright`, `uv run ruff check`
   and `uv run ruff format --check` pass after the final change. The local venv lacks the `aquacast` extra (CI
   has it): also run the CI shards.
4. Multi-model review of the plan, then of the patch; the extra owner-commissioned review (high risk).
5. T7's station-ID set comparison is recorded.

## Not in this plan

- **The station path.** `no_observations` / `no_nwp` keep logging and continuing so fallback models run.
- **Fixing station 2024's binding or its group membership:** Plan 517.
- **A watchdog probe or operator alert** for the new record (Q3).
- **`FORECAST_FRESHNESS` semantics**, or the FAILED threshold.
- **Partial batches** (`batch_missing_station_outputs`) and per-station QC rejection (Plan 404).
- **Per-member handling of `nwp.insufficient_coverage`** (Q5).
- **Any model or FI package change.**
- **Retries.**
- **The duplicate `linear_regression_daily` rows** (268 vs 134 on the 2026-09-29 00Z cycle; each cycle now shows
  134 per issue time). Unverified and unowned here; they are **not** evidence for the group failure. Follow-up
  (separate, read-only): a `GROUP BY issued_at, parameter, version` measurement of `linear_regression_daily`
  rows, recorded by whoever picks it up.
- **Plans 404 and 323 do not collide:** 404's rejection buffering is preserved (T3); 323 owns its own health
  check type. Plan 257 (coverage check) and Plan 327 (refused-forecast handling and re-runs) are related: the
  implementer reads both before T3/T5.

## Risks

| Risk | Mitigation |
|---|---|
| A null-filled member is **served from all-null forcing** because the model's `max_nan` tolerance is at least the window | T1 reads the declared tolerances (UNVERIFIED); the grid is materialised so the gate can count; Q6 if a tolerance is too loose. |
| The quality flag is lost by the fill (`missing_buckets` is membership-only) | D5 and its test. |
| The change breaks the station caller or the tests asserting `None` | D6: sibling functions, originals unchanged; existing tests pass unchanged. |
| Conformance hides a real schema fault | D2: only declared, missing columns are added; everything else still fails and is visible through Part B. |
| DEGRADED on every cycle for a group that always has one bad member | Only whole-group drops degrade; skipped/null-filled members are an OK record (Q2). |
| The new record displaces the freshness heartbeat | A distinct check type. |
| Rollback or an older API cannot read the new check type | T6 deploy note. |
| Record spam from repeated identical outcomes and re-runs | D10. |
| A dropped or null-filled member changes a pooled group model's other outputs | Group models are per-station in `predict_batch`; UNVERIFIED for pooled models - the implementer reads `aquacast`'s `predict_batch` (Codex notes the pinned model derives its basin set from the supplied stations, which supports subset execution but does not prove numerically identical sibling forecasts under a changed batch). |
| A reason mapped wrongly (`other` swallows causes) | `other` logs the exception class at WARNING; T4 has one test per path. |
| A health-write failure aborts a cycle | `_append_pipeline_health_record` swallows and logs; T5 tests it. |

## Open questions (plain language, with a recommendation)

- **Q1 - A dropped group: does the run itself fail?** *Recommendation:* keep the run "Completed", mark the cycle
  "degraded" and write a health record; a failed run for one group would look like a full outage. Zero forecasts
  overall is already critical.
- **Q2 - Should one skipped or all-null station in an otherwise working group make the cycle "degraded", and what
  share of a group being dropped is "too much"?** *Recommendation:* no for a single station (record it, do not
  degrade); leave any percentage threshold for later - it is your call, and the record carries
  served-versus-dropped counts so you can pick one from evidence.
- **Q3 - Should a dropped group notify someone?** This plan writes a record only; the outcome is visibility on the
  health page, not a message. *Recommendation:* yes as a follow-on plan (a watchdog check for the new record,
  with a recovery rule), so a group that stays dark for several cycles raises an operations alert.
- **Q4 - Answered from code:** a member without a forecast source is caught by the ordinary group handler; it does
  not stop the whole cycle, and its stations were already counted. No owner decision needed.
- **Q5 - One station with too little weather forecast today drops its whole group.** The same kind of
  all-or-nothing rule, on the forecast side, outside your 2026-09-08 decision (which was about past data).
  *Recommendation:* make it visible now (this plan does) and decide per-station handling in a separate plan.
- **Q6 - If a model tolerates a fully missing series, a station with no forcing at all would still get a
  forecast.** T1 will read the tolerance. *Recommendation:* if it is that loose, ask the model owner to tighten it
  rather than adding a SAP3-side rule; a SAP3 history-length gate is deliberately out of scope.

## Dependency graph

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1"] },
    { "id": "phase-2", "tasks": ["T2"], "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T4"], "depends_on": ["phase-2"] },
    { "id": "phase-4", "tasks": ["T3", "T5"], "depends_on": ["phase-3"], "parallel": false },
    { "id": "phase-5", "tasks": ["T6"], "depends_on": ["phase-4"] },
    { "id": "phase-6", "tasks": ["T7"], "depends_on": ["phase-5"] }
  ]
}
```
