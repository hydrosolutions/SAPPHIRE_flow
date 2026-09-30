---
status: DRAFT
created: 2026-09-30
plan: 514
title: A group member with a null static or empty forcing must not drop the whole group; a dropped group must show in cycle health
scope: (S) The group's static frame is projected to the model's DECLARED statics before stacking (measured cause of the 2026-09-30 12:00Z drop: undeclared null attributes), and a member whose DECLARED static is missing is skipped per member (group-local), never dropping the group. (A) In the group input path every stacked frame is conformed to the model's declared schema (missing declared columns are null-filled, never a stacking crash), so one member with missing forcing cannot drop the group; the FI adapter's max_nan gate then decides whether that member is served. (B) Every recoverable whole-group drop and every skipped or null-filled member becomes visible - counted in cycle health and written once per group-model execution to the pipeline-health table.
risk: high   # FI-boundary edit (docs/workflow.md high-risk list) and scientific behaviour: which stations get a forecast
related: [262, 312, 116, 120, 155, 223, 239, 257, 327, 404, 323, 517]
open_decisions: [Q1, Q2, Q3, Q5, Q6, Q7, Q8]
source: 2026-09-29 pilot run `powerful-leech` - investigation by the orchestrator session; revised 2026-09-30 after one Claude and one Codex review; revised again 2026-09-30 (rev 2) after the measured 12:00Z worker log; rev 3 2026-09-30 after second Claude and Codex re-reviews; rev 4 2026-09-30 after the round-3 reviews
---

# Plan 514 - a group member with a null static or missing forcing must not drop the group, and a dropped group must be visible

## Status

**DRAFT - HIGH RISK (proposed; the owner decides).** Multi-model review is mandatory
(`docs/workflow.md` § Multi-Model Review), plus the one extra owner-commissioned review high-risk work gets. Only
the orchestrator sets READY.

**Why high risk (corrected 2026-09-30).** `docs/workflow.md` (~144-165) does not treat "touches the live
deployment" as a ground. The two grounds that apply are: (1) the plan edits the **FI-boundary path** (the group
input frames, statics included, handed to the FI adapter, and the SAP3-internal cause carried by the adapter's boundary error, T4);
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
- **(b) A one-off operational step was taken on suspicion.** On 2026-09-30 the owner authorised removing station
  **2024** from the pilot group (139 -> **138**) through a direct database write, on the belief that 2024's empty
  forcing caused the 09-29 drop. The measured 12:00Z run (below) shows that removal did **not** unblock the group:
  the drop reproduced with 138 members, on a different cause. The write is recorded as an **exception, not a
  precedent** (Plan 517 owns the audited route). Consequence for this plan: the live pilot group **does**
  reproduce the primary defect, so the fix can be proven live (T9a).

## Why

On **2026-09-29** the pilot run `powerful-leech` ended **Completed with zero `cmal_small` rows**, and on
**2026-09-30 12:00Z** the scheduled cycle did the same with the group at **138** members. Nothing in Prefect state,
in the database or in the health record said a whole group had produced nothing.

**Measured cause of the 2026-09-30 12:00Z drop (worker log saved on the mini as `~/t8/cycle-12z-worker.log`,
read 2026-09-30).** At 12:29:06 the flow logged `forecast_cycle.group_input_assembly_failed` for `model_id=cmal_small`,
`error="type Float64 is incompatible with expected type Null ... failed to vstack column 'reservoir_fs'"`,
right after four `run_group_forecast.station_inputs_unavailable` lines (cadence skips). The cycle then ended
Completed with zero `cmal_small` rows. Reading of the same log for the group phase (lines carrying the group id,
12:28:44-12:29:06): 4 `cadence_mismatch_skip` (gaps of 18, 7, 18 and 3 days against a 1-day step; these are the four
skips), **98 `short_lookback`** (all 28 of 30 days; served by the model, not a fault), **0 `no_observations`**, **0
`no_past_dynamic`**. So 134 of 138 members reached the stack and **no group member had empty forcing at that
moment**; the only `no_past_dynamic` line in the whole log (station `a0aa6f2a...`, 12:25:57) is from the
station-model phase, and the group assembly would have logged it again had that station been a member. The station
phase also logged 9 `no_observations` and 133 `short_lookback` (45 needed by `nwp_regression`); those are not in the group
assembly. 0 `station_input_nan_tolerance_exceeded` lines: the run never reached the adapter.

**Database evidence (measured 2026-09-30):** for the 138 members, `basin_versions.attributes->'reservoir_fs'` (latest
non-superseded version per basin) is JSON null for **84** stations and a number for **54**. A static frame built
from a `None` gets polars dtype `Null`; stacking `Null` with `Float64` fails.

**The 2026-09-29 cause is still UNVERIFIED** (its logs were gone). The same statics failure is a strong candidate;
the run had 139 members including 2024, so either fault, or both, may have fired. The pilot worked on 2026-09-25 because its two
stations (2009, 2091) both had numeric `reservoir_fs`.

**Station 2024's zero-column `past_dynamic` remains a real latent defect of the same class** (see "Second mechanism"),
reproduced against polars, and still unfixed until Part A.

**Ruled out:** the short 30-day discharge windows (98 stations 2 days short of the declared window). `aquacast` at the pinned
revision serves them.

### Primary defect - the statics stack (measured, reproduced 2026-09-30)

1. `services/operational_inputs.py`, `# --- static ---` (~1105-1145): the frame is
   `pl.DataFrame([resolve_shared_static_frame(basin.attributes, ...)])`. `resolve_shared_static_frame` returns
   `dict(attributes)` (NATIVE) or `project_declared_static_attributes(...)` (CARAVAN), and **both keep every
   original key** of the basin package, declared or not. A key whose value is `None` becomes a column of dtype
   `Null`; nothing narrows the frame to the model's declared statics.
2. `services/run_group_forecast.py` (~209-237): `static=_stack_station_frames(static_parts)` is a vertical
   `pl.concat` (`_stack_station_frames`, ~116-119). Reproduced on `polars 1.43.2` in the worktree: stacking a
   `Null`-dtype column with a `Float64` column raises `SchemaError: type Float64 is incompatible with expected
   type Null` (the message in the log), **but only when the `Null` frame comes first**; the members are stacked
   in `sorted(station_ids, key=str)` order, so whether a group fails depends on the id order of its first member.
   `how="vertical_relaxed"` stacks it in either order (supertype `Float64`), but also silently coerces every other
   mismatch (int/float, and any future dtype drift). The primary fix is projection to the declared statics (DS1), which
   removes undeclared columns before stacking, and DS2 removes members with a declared null, so no `Null`-dtype static column reaches the
   stack. **A declared numeric static that is `Int64` in some members and `Float64` in others (JSON `5` versus `5.0`) still raises
   `SchemaError` in BOTH orders** (reproduced at polars 1.43.2, the locked version; the round-3 review saw 1.44.2 - same result): DS1c
   handles it.
3. The past/future frames go through the same `_stack_station_frames`; a
   `Null`-dtype column there (a forcing or target column present but all-null) would fail the same way. Whether
   the assemblers can produce one is UNVERIFIED; T3 covers `Null`-dtype cases on the dynamic frames with real polars (T1 covers
   statics only, and after DS2 a `Null` static cannot reach the stack).
4. **What happens next in the adapter (read from code).** `ForecastInterfaceAdapter._static_inputs`
   (`adapters/forecast_interface.py` ~1621-1650) selects only the model's **declared** statics
   (`static.select(sorted(static_names))`), and `_static_value` **raises `ConfigurationError`** for a value that is
   not int/float/str, i.e. for `None`. So an **undeclared** attribute never matters to the model, yet today it can
   crash the stack (DS1 removes it before stacking); a **declared** null static makes `predict_batch` raise for the
   **whole** batch (DS2 prevents reaching it). A member with no static frame at all reaches the adapter as `{}`
   (`types/model.py` ~116-119).
5. **The `max_nan` gate does not count statics.** `predict_batch` (~1214-1220) passes only `past_targets`,
   `past_dynamic` and `future_dynamic` to `_variables_over_nan_tolerance`; SAP3 has no per-station static gate.
6. **SAP3 training treats a declared-null static as a skip** (`services/training_data.py` ~495-550,
   `training_data.missing_static_attributes`). That describes SAP3's training path only: the `cmal_small` artifact
   was trained **externally and imported** (Plan 262, line 7), so it says nothing about that artifact's training
   history. `assemble_group_training_data` (~636-680) has the same latent `Null`-versus-`Float64` hazard; not part of
   this plan (T1 records it).
7. **Is `reservoir_fs` declared by `cmal_small`?** The checked-in config
   (`src/sapphire_flow/models/aquacast/configs/cmal_small.yaml`) declares 78 statics, none `reservoir_*` (the shim
   builds its model from that config), and the adapter reads only declared statics. So the crash is on an attribute
   the model never reads and the 84 null values justify no exclusion. **UNVERIFIED:** that the deployed image and
   artifact match the checked-in file; T1 checks this (Q7). The 84 null values are a stacking hazard only.
   A basin package holds 300 keys; six `reservoir_*` keys are JSON null for 84 members and a few others for 1-10
   members, so other undeclared columns are hazards of the same kind.


### Second mechanism (latent; station 2024)

1. `services/operational_inputs.py`, `# --- past_dynamic ---` block (~1018-1024): when the station has no
   reanalysis rows `raw_forcing_to_dataframe` returns `None` and the code sets `past_dynamic = pl.DataFrame()` - a
   frame with **zero columns**.
2. `_stack_station_frames` does a vertical `pl.concat`. One zero-column frame among four-column frames raises
   `ShapeError: unable to append to a DataFrame of width 4 with a DataFrame of width 1` (the width 1 is the
   `station_id` column `_with_station_id` adds).

### Why a drop is silent (both mechanisms)

The flow's group branch (`flows/run_forecast_cycle.py` ~3853-3862): the `except Exception` around
`assemble_group_operational_inputs` logs `forecast_cycle.group_input_assembly_failed`, appends to `errors`,
`continue`s. The run finishes Completed; `_forecast_cycle_health` looks only at `stations_failed`, which this path
does not touch; `_emit_forecast_freshness_record` is OK if any forecast was stored anywhere in the cycle; nothing in
`pipeline_health` names the group.

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
  design constraint (T4).
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
  3. **The null-fill dtype matters.** A null-fill built from `pl.lit(None)` has dtype `Null` and reproduces the same
     `SchemaError` against `Float64` siblings: filled value columns are `Float64` (or the sibling's dtype when a sibling has
     the column), and the filled `timestamp` column takes the dtype of the timestamps the assembler produces for the
     other members (read at T3; UNVERIFIED which). T3 asserts both.
  4. `max_nan` is only as strict as the model's declared tolerance. **UNVERIFIED:** the tolerances declared by
     the pinned `aquacast` model; if a tolerance is at least the window length, a fully null station is served
     from all-null forcing. T3 reads and records them before T4 lands.
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
  dropped group on top. Exit gate 3 therefore cannot require DEGRADED universally.
- **`ModelOutputError` has no cause field** (`exceptions.py` 32-33: a bare `SapphireError` subclass). The FI's
  `ModelFailure.cause` exists in the pinned package. A new **SAP3-internal** attribute set on the
  `ModelFailure` -> `ModelOutputError` conversion is enough; no FI issue is needed (Codex, agreed).
- **The FI rule.** An anticipated failure is **returned as `ModelFailure`, never raised** by the model. This plan
  changes nothing on the model side; the conversion is the adapter's own boundary handling.
- **Live state (2026-09-30):** the pilot group `swiss-cmal-small-pilot` is **138** stations (2024 removed by a
  one-off write). At 12:00Z it was dropped whole by the statics stack; of 138 members, 4 were cadence-skipped and 134
  reached the stack. **Three sparse stations exist, two with no data since 2026-09-22.** After the fixes they are
  served or skipped **per station** (cadence skip, `max_nan` gate); which are served is a **measurement** (T9a/T9b).

## Decisions (recommended answers; the owner confirms)

| # | Decision | Why |
|---|---|---|
| DS1 | **Primary statics fix: project to the model's DECLARED statics before stacking, in the GROUP assembler only.** After Caravan resolution and collision checks, after DS2 classification, each member's static frame is narrowed to the declared names in sorted order and then stacked; a model declaring no statics gets no static frame. Undeclared basin keys (300 in a package, six `reservoir_*` null for 84 members, a few others null for 1-10, any dtype drift) never reach the stack. Evidence: `cmal_small.yaml` declares 78 statics and none is `reservoir_*`; the adapter reads only declared statics. The station path is not touched. | Removes the whole class instead of one dtype; also removes the order dependence and the `Int64`-versus-`Float64` variant (reproduced, both orders). |
| DS1b | **Statics need no `Null`-cast: it is dead code there** (DS1 removes undeclared columns, DS2 removes declared nulls) and is not implemented for statics. For the DYNAMIC frames an all-`Null` column is cast to the sibling dtype (`Float64` when no sibling has a value) or, better, never created (D3 fills with an explicit dtype); if kept it needs a mutation check (T3). | No dead code. |
| DS1c | **Numeric declared statics are cast to `Float64` before stacking** (int and float only; bool and non-scalar values are not numeric and are not cast - see DS4). Chosen over `how="vertical_relaxed"` because that also coerces string-versus-float and every future dtype drift, and the model reads these columns; the T1 negative test (string versus float) must still fail. The alternative - accepting the failure as a known limit - is rejected because it would dark the pilot again on a JSON `5`/`5.0` mix. A test covers `Int64` versus `Float64` in both member orders, for a DECLARED column. | Closes the last known statics `SchemaError` on the declared path. |
| DS2 | **A member whose DECLARED static is missing is skipped per member, GROUP-LOCAL, before stacking** (`run_group_forecast.station_inputs_unavailable`, reason `missing_declared_static`). It is **not** moved into the shared sibling (D6): the station flow treats a `None` from the wrapper as skip-the-whole-station (`run_forecast_cycle.py` ~3453/3483), which would suppress static-free fallback models. Three explicit cases: a static frame that is `None` (no basin or no attributes; `operational_inputs.py` ~1105-1145; the adapter would pass `{}`), a declared column absent (Caravan resolution pops a declared name without a source, giving a different width), a declared cell `None`/NaN. Undeclared nulls skip nobody. **Applies only to names the deployed model declares** (T1 checks against the checked-in 78). Whether "skip" is the right policy for a genuinely missing declared static is Q8. | The adapter raises for a declared `None` and would drop the whole group; `max_nan` does not count statics. |
| DS3 | **PR1 = T1-T2 + T2d + T2r; PR2 = T3-T8.** PR1 stays inside `run_group_forecast.py` and its tests; each PR has its own docs, review (both get the extra high-risk review) and live checkpoint (T9a/T9b), and the full suite runs after each PR's final change. **PR2 is re-reviewed (plan text or implementation as the owner sequences it) before its implementation starts; PR1's checkpoint does not carry that approval.** | Unblocks the pilot without holding it behind a multi-part change, without skipping any gate. |
| DS4 | **Known remaining limit: a non-null bad basin area (0, negative, inf) still makes the aquacast shim refuse the WHOLE batch** (`models/aquacast/_shim.py` ~693-698 via `_units.py` ~60). Not fixed here and no general SAP3 static-value policy is invented. In PR1 it is only the existing whole-batch `ModelFailure(INPUT_DATA)` and the logged empty outcome (`run_group_forecast.py` ~597-609 logs the exception and returns an empty outcome); Part B reports it as `model_input_data` from PR2 (T6). **Other remaining limits:** `_static_value` (`forecast_interface.py` ~1642-1650) also raises `ConfigurationError` for a `bool` and for a non-scalar (list/dict) value, whole-batch; a collision raise in a member (`operational_inputs.py`, before the group assembler) still drops the whole group. Neither is fixed here; both are visible only through the generic assembly-failure log until PR2. Candidate FI gap: the FI `model_interface` is understood to require per-entry failure when other stations can produce output (review cites FI v0.1.20 `docs/model_interface.md:99`; UNVERIFIED, the pinned FI text is not readable in this checkout); if confirmed, that is an upstream FI/model issue to file, not a SAP3 workaround. | Scope discipline; FI adherence. |
| DS5 | **The adapter is not changed for a declared `None`.** Its `ConfigurationError` (`forecast_interface.py` ~1642-1650) is SAP3 adapter code and needs no upstream FI issue; DS2 prevents reaching it in the group path. A per-station gate in `predict_batch` (drop the station like the NaN case) would be a cleaner second line of defence but touches the FI boundary a second time; **decision: not in this plan**, revisit if a declared-`None` reaches the adapter in T9. | Keeps PR1 small. |
| D1 | **Part A conforms, it does not drop.** Before stacking, each member's `past_targets`, `past_dynamic` and `future_dynamic` are conformed to the model's declared schema: declared columns absent from the frame are added as nulls on the expected time grid, and the columns are put in declared order. `_stack_station_frames` itself stays strict. | Owner decision (a): consistent with Plan 239 T1b - a gap is a quality flag, not a refusal. |
| D2 | **Conformance is by declared need.** A model that declares no `past_dynamic_features` gets no columns added; its zero-column frames stay zero-column. A frame with columns **beyond** the declared schema, or with a non-zero-width mismatch the declared schema does not explain, is **not** repaired: it still fails stacking and is reported as an assembly failure (made visible by Part B). | Otherwise conformance would hide real schema faults. |
| D3 | **What is null-filled, precisely.** Past frames (`past_targets`, `past_dynamic`): the declared columns, on the expected grid (`timestamp` + nulls). `future_dynamic`: its declared columns depend on the model's ensemble mode (member-suffixed columns), so a zero-width member frame is conformed to the **column set of the other members' frames** with zero rows; the existing `nwp.insufficient_coverage` gate then judges it (today that gate drops the whole group on one short member - Q5). | The past grid is knowable from the anchor; filled columns carry an explicit dtype (Float64 value columns, the assembler's timestamp dtype), never `Null` (Facts item 3); the future column set is not derivable without the member count. UNVERIFIED which reference to use for the future frame; T4 decides with a test. |
| D4 | **The per-member skips are** a member whose assembly returns `None` (today the cadence mismatch, unchanged) and, from PR1, a DS2 `missing_declared_static` skip (group-local). There is **no new drop reason for forcing**: with a declared schema, null-filling is always possible. If the model's declared schema cannot be read, conformance is skipped and stacking stays strict (fails loudly, visible via Part B). | Owner decision (a): hard-drop only where null-filling is impossible. |
| D5 | **The quality flag survives the fill, and it has a carrier.** `GroupModelInputs` is frozen and both `_build_station_result` (~402-410) and `_group_forcing_gap_details` (~476-490) compute `past_forcing_flags` from the filled frame, which would report no gap. The pre-fill gap record (per station: the missing declared columns and buckets) travels in the `GroupAssembly` metadata (D6) **carried by the existing per-station `OperationalInputMetadata`** (a new frozen field; `run_group_forecast` already receives `metadata_by_station`, so no signature change to `run_group_forecast` or the flow call site ~3868). The flow unpacks `GroupAssembly` and passes `metadata_by_station` as today; **both call sites (inside `run_group_forecast`) read it**; a test for each proves a null-filled member keeps its own gap flag and no sibling is labelled. | `missing_buckets` is membership-only (Facts). |
| D6 | **Reasons are conveyed by a sibling function, not by changing a return type.** `assemble_station_operational_inputs` keeps returning `None` and its 2-tuple, unchanged. A sibling `assemble_station_operational_inputs_outcome` (same arguments) returns a small frozen union - the inputs, or a `StationInputsSkipped(reason)` with a closed-vocabulary `reason` (today only `cadence_mismatch`) - and the original becomes a thin wrapper that maps a skip to `None`. Only the group assembler calls the sibling; DS2 does NOT use it (DS2 is group-local). Same pattern for the group assembler: `assemble_group_operational_inputs` keeps its signature and return; a sibling returns a frozen `GroupAssembly` (inputs, metadata, skipped members with reasons, null-filled members with their columns, or a group-level drop reason) that the flow calls. | Codex P1: the station caller checks `is None`, and tests assert `None`. |
| D7 | **The unit of a record is a group-model execution**, and **exactly one record is written after its final outcome is known** - never an OK followed by a WARNING for the same run. Skipped and null-filled member information rides through successful assembly (D6) and is attached to the single final record. | Codex P2: execution iterates `(group, model)`. |
| D8 | **Part B: a dropped group-model execution is DEGRADED, the flow run stays Completed.** Recoverable drops (table above) increment `ForecastCycleResult.groups_dropped`. Fatal persistence failures and `StoreError` still propagate and are **not** counted. Existing FAILED precedence is preserved. | See "State decision". |
| D9 | **New check type `FORECAST_GROUP_DROPPED` (`forecast_group_dropped`).** Subject = group id. Status WARNING for a whole-group drop; OK for a served execution that had skipped or null-filled members. Detail JSON: `group_id`, `model_id`, `reason` (closed vocabulary), `served_station_count`, `dropped_station_count`, `null_filled_station_ids`, `skipped_station_ids`, and the responsible station ids where known. Served-versus-dropped counts are in every record. | A distinct name keeps the freshness probe (`limit=1`, latest) unpolluted. |
| D10 | **Repeated identical records are bounded.** At most one record per group-model execution per cycle; none when the run has an explicit `cycle_time` (backfill/replay); none for a Plan 327 re-run of an already-recorded cycle time. UNVERIFIED how Plan 327 re-runs reach this code; the implementer reads Plan 327 before T7. | Codex: dedupe/cap. |
| D11 | **No model change.** | FI adherence. |
| D12 | **A large dropped fraction is not a threshold this plan sets.** The record carries served-versus-dropped counts; what fraction turns a cycle DEGRADED (today: any whole-group drop) is an owner call (Q2). | Owner call. |

### State decision (Prefect run state versus health)

Recommendation unchanged: **the flow run stays Completed; health becomes DEGRADED; the record is monitoring
visibility only.**

- `docs/standards/orchestration.md` contains no rule that a partial cycle failure must fail the run. (UNVERIFIED:
  whether any deployment schedule, retry policy or automation keys on state - the implementer greps `flows/`
  deployments before T5.)
- **Wording corrected (review).** The earlier draft called the record "alert-eligible". It is **not**: the
  watchdog probes `forecast_freshness` only and does not consume the new type, and a successful sibling forecast
  keeps that probe OK while the new record is WARNING. The delivered outcome is **monitoring visibility only**
  (API health detail, admin-only). A watchdog probe is a follow-on (Q3); event-only records also need a
  recovery/expiry policy when that probe is designed.
- Precedent: `FORECAST_STATION_DARK` reports a station-level absence by record, not by failing the run.
- Zero forecasts stored is already CRITICAL through `FORECAST_FRESHNESS`. Unchanged.

## Tasks

Ownership of `services/run_group_forecast.py` is **sequential**: T2, then T4, then T6 (all edit it); they are
not parallel. **T1-T2 (+ T2d, T2r) ship first as PR1 (DS3); T3 onwards is PR2 (high risk).**

### T1 - Failing tests and read-only facts: the statics stack, the declared-static read
**Outcome**: red-first tests with real polars that reproduce the 12:00Z failure, plus the facts the fix needs.
Fixture rule: **stacking fixtures use UNDECLARED columns; missing-static fixtures use DECLARED columns** (a
declared column is removed by DS2 before stacking and would not exercise the stack). Station ids are controlled in
the fixtures because the assembler sorts members with `sorted(group.station_ids, key=str)`.
- **Stacking (undeclared):** three members whose static frame has an undeclared column `None` for some and numeric
  for others, **in both member orders**: RED on `main` with `SchemaError` in the `Null`-first order (assert the
  class); the other order passes on `main` (guard). A second variant with an undeclared column `Int64` in one
  member and `Float64` in another (RED in both orders, reproduced at polars 1.43.2). After T2: all three members
  in `station_ids`, the unused column **absent** from the stacked static, the declared columns present.
- A model declaring **no statics**: `static=None` on the inputs, no crash, all members survive. **RED on `main` for populated basins** (`operational_inputs.py` ~1105-1145 still builds a static frame and `run_group_forecast.py` ~209-237 stacks it); only the absent-basin variant is a guard.
- **Declared dtype drift (DS1c):** a DECLARED numeric static `Int64` in one member and `Float64` in another, both orders: RED on `main` (`SchemaError`); after T2 all members present, column `Float64`.
- **All members DS2-skipped:** the group falls through to `no_serviceable_stations` / `None`, no crash.
- **Negative test:** a mismatch in a DECLARED column (string versus float) still fails stacking (strict where the
  model reads).
- **DS2 tests (declared fixtures; through the real adapter or a faithful fake that mirrors `_static_inputs` /
  `_static_value`):** three explicit cases, each skips that member with reason `missing_declared_static`
  and serves the rest: (i) static frame `None` (no basin or no attributes; the adapter would otherwise receive
  `{}`); (ii) declared column **absent** (no Caravan source; `project_declared_static_attributes` pops it, giving a
  different width); (iii) declared cell `None`/NaN. An UNDECLARED null attribute skips nobody.
- **Station-fallback regression:** in the station flow, a station missing a static needed by one assigned model still
  runs its static-free fallback model (the station assembler is unchanged by this plan).
- **Bad area (DS4):** a member with a non-null area of 0 or negative makes the shim return one
  `ModelFailure(INPUT_DATA)` for the whole batch; assert only the current behaviour (whole-batch `ModelFailure(INPUT_DATA)`, logged, empty outcome). The `model_input_data` reporting is PR2 (T6). Documented limit, not fixed here.
- **Read and record in a dated note here (read-only):** (i) **CLOSED (verified 2026-09-30 by the orchestrator):** the
  sha256 of `cmal_small.yaml` inside the running `prefect-worker` container
  (`/app/src/sapphire_flow/models/aquacast/configs/cmal_small.yaml`) is `94ebec0fe4e000ce...`, equal to the repo file
  and to the recorded `config_hash`; the deployed declaration is the checked-in one (78 names, none `reservoir_*`).
  Matching declarations do NOT prove every member has all 78 values - that is (iii); (ii) every consumer of
  `GroupModelInputs.static` (grep list in exit gate 1), **plus a grep for any evidence replay or comparison that
  depends on undeclared columns of the recorded static** (`capture_group_evidence`, `forecast_evidence.py` ~297,
  records `inputs.static`, which projection narrows to the declared names, or `None` for a model declaring none;
  UNVERIFIED whether anything reads the dropped columns); (iii) by running the REAL Caravan resolver
  (`services/caravan_statics.py::project_declared_static_attributes`, via `resolve_shared_static_frame`) read-only over
  the 138 pilot basins - **not a raw-key SQL probe** (71 of the 78 declared names are derived by the Caravan
  projection, not raw `basin_versions.attributes` keys; the 7 that are raw keys were checked 2026-09-30: all numeric,
  no nulls, no int/float mix) - report (a) the number of members with a missing declared value (the expected DS2 skip
  count) and (b) per-DECLARED-column dtype consistency across the 138 (any int/float mix or non-numeric type).
  **PR1 merge gate:** if the measured DS2 skip count among the pilot's currently served stations is more than 0 (or
  above a bound the owner states), stop and ask the owner before merge; a non-zero count means PR1 removes stations that
  are served today.
**In / Out**: `tests/unit/services/test_run_group_forecast.py` (+ a station-flow test file). Out: source.
**Verification**: `uv run pytest tests/unit/services/test_run_group_forecast.py -k static`.
**Pre-change**: RED for the `Null`-first order, both dtype-drift variants (undeclared and declared), the populated-basin no-statics case and the three DS2 cases; the other order,
the absent-basin no-statics case, the negative test, the station-fallback regression and the undeclared-null guard pass on
`main` and are marked as guards.

### T2 - Project to declared statics; skip a member with a missing declared static (PR1, group assembler only)
**Outcome**: the group stacks; the pilot group is no longer dropped by this cause.
- In `services/run_group_forecast.py` (~209-237), **after** Caravan resolution and collision checks (already done
  per member in `operational_inputs.py`), classify each member (DS2) and then project each surviving static frame
  to the model's declared static names (`reqs.static_features`) in **sorted order**, then stack. No static frame
  when the model declares none. Members without a static frame are handled by DS2, not silently omitted from
  `static_parts` as today.
- Cast numeric declared statics to `Float64` before stacking (DS1c). No `Null`-cast for statics (DS1b: dead code).
- Log `run_group_forecast.station_inputs_unavailable` with `reason=missing_declared_static` (and the names) for a
  DS2 skip. The cadence skip keeps its current, reasonless log until T4.
**In / Out**: `services/run_group_forecast.py` only (a local check; T4/D6 moves nothing later). Out: the flow,
`operational_inputs.py`, health, the station path, models, the FI package.
**Verification**: T1 passes; `uv run pytest tests/unit/services -q`; `uv run pyright`; `uv run ruff check`.
**Pre-change**: mutation-check - remove only the projection and the stacking cases fail; remove only DS2 and the
three skip cases fail; remove only the `Float64` cast and the declared dtype-drift test fails.

### T2d - PR1 documentation
`docs/standards/logging.md` (the two events PR1 touches: `station_inputs_unavailable` with reason
`missing_declared_static`), `docs/touchpoint-maps.md` forecast-cycle map (group statics are projected to declared
names; DS2 is group-local; numeric declared statics cast to `Float64`), and a note that projection is not observably neutral: `capture_group_evidence` now records only the declared static columns (or `None`), `docs/plans/262-...` wrong-sentence fix moves to T8. **Verification**: grep each name in `docs/`.

### T2r - PR1 review gate
One independent Claude and one independent Codex review of the patch, plus the extra owner-commissioned high-risk
review (PR1 decides which stations get a forecast). Full test suite after the final PR1 change (exit gate 4).
Then T9a.

### T3 - Failing tests: the zero-column and partial-column class, with real polars
**Outcome**: tests that reproduce each remaining stacking crash (station 2024's shape), plus the guards. Statics are T1; the `Null`-dtype cases on the dynamic frames are here.
- Mixed group of three members, model declaring a `past_dynamic` feature, one member with `past_dynamic =
  pl.DataFrame()`: today `ShapeError` (assert the exception class in the RED evidence). After T4: all three in
  `station_ids`, the empty member's declared columns all null on the expected grid.
- The same for: partial columns (width 4 vs 3); differing column order; zero-column `past_targets`; zero-column
  `future_dynamic`.
- **All members empty**: on `main` this does **not** raise `ShapeError` (nothing mismatches) - it returns inputs;
  state this in the RED evidence. After T4 it returns inputs with all-null forcing and the adapter's `max_nan`
  gate is what refuses them (asserted through the real adapter or a faithful fake).
- **Guards that pass on `main`** (say so): a model declaring no `past_dynamic_features` keeps zero-column frames.
- **Negative test:** a member frame with a non-zero-width mismatch beyond the declared schema (an extra or
  misnamed column) still fails stacking and is reported as an assembly failure (D2).
- A null-filled member keeps its own past-forcing gap flag and its siblings have none (D5).
- **Null-fill dtype:** filled columns are `Float64` (or the sibling dtype) and the timestamp column matches the siblings' dtype; a fill built from `pl.lit(None)` is RED (`SchemaError` against `Float64` siblings). If DS1b's cast is kept for dynamic frames, a mutation check removes it.
- A test that a zero-row fill would have passed the gate (documenting why the grid is materialised).
- Read and record the pinned model's declared `max_nan` tolerances (UNVERIFIED above) in a dated note here.
- A **negative test** that the DS2 skip and D1 null-fill do not overlap: a DECLARED static that is missing is never null-filled into a served member (D1 concerns past/future frames only).
**In / Out**: `tests/unit/services/test_run_group_forecast.py`. Out: source. The existing
`TestRealAssemblerWarmUpProvenance` fixture supplies empty forcing to a forcing-declaring fake and needs adjusting
after T4 (review note).
**Verification**: `uv run pytest tests/unit/services/test_run_group_forecast.py -k conform`.
**Pre-change**: RED for the mixed, partial, order, past_targets and future_dynamic cases; the all-empty case fails
as "returned inputs", not as `ShapeError`; the guards pass.

### T4 - Conform frames to the declared schema, and the sibling result functions
**Outcome**: Part A, without changing any existing return type.
- Add the conformance step in the group assembler (D1-D5), local to `run_group_forecast.py`, with explicit dtypes for filled columns (D3). The pre-fill gap record rides in `OperationalInputMetadata` (D5).
  `operational_inputs.py` gains only the sibling (and the metadata field) `assemble_station_operational_inputs_outcome` (D6); the original
  keeps its behaviour, including the log-and-continue of `no_observations` and `no_nwp`.
- Add the closed reason type (`Enum`; PR1's local `missing_declared_static` joins it here) and the frozen `GroupAssembly` / `StationInputsSkipped` results in
  `types/`, following the repo's parse-don't-validate style.
- Log `run_group_forecast.station_inputs_unavailable` with `reason` for the cadence skip (moved here from PR1; T9a is relaxed accordingly), and a new
  `run_group_forecast.station_inputs_null_filled` (member, columns, frame) for each fill.
**In / Out**: `services/operational_inputs.py`, `services/run_group_forecast.py`, the result types. Out: the flow
(T5/T7), models, the station path's behaviour.
**Verification**: T3 passes; `uv run pytest tests/unit/services -q`; every existing test asserting `None` from
`assemble_station_operational_inputs` passes **unchanged**; `uv run pyright`; `uv run ruff check`.
**Pre-change**: mutation-check - remove only the conformance step and T3 fails again.

### T5 - Count group drops into cycle health
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
(T7), the watchdog.
**Verification**: a flow-level test per path: DEGRADED, `groups_dropped == 1`, `stations_failed` unchanged, run
returns normally. Tests for the non-drops and for `StoreError` / fatal persistence still propagating. **A
partial-skip-then-failure test** (a member is skipped, then `predict_batch` fails: one drop counted, one final
record). **A two-models-sharing-a-group test.** A test that a cycle whose stations all failed stays FAILED with a
dropped group.
**Pre-change**: RED - on `main` the same scenario returns HEALTHY.

### T6 - The drop reason from the group service, and the FI failure cause as a value
**Outcome**: `run_group_forecast` says why nothing was produced, without the flow parsing log text.
- `GroupForecastOutcome` gains an optional `drop` (closed-vocabulary reason plus detail); every whole-group
  `return GroupForecastOutcome(results={})` sets it: `nwp.insufficient_coverage`, `artifact_fetch_failed`,
  `no_active_artifact`, `predict_batch_failed`, `batch_empty`. Existing log events keep their names and fields.
- The flow maps `GroupForecastError` and the generic handler into the same vocabulary (T5/T7).
- **`ModelOutputError` gets a new SAP3-internal attribute** (`cause`, default `None`) set where the adapter's
  `_output_from_result` converts a returned `ModelFailure`, from the FI's `ModelFailure.cause`. The group service
  maps `INPUT_DATA` to `model_input_data`. No FI change and no FI issue.
This is where the bad-area case first reports `model_input_data` (moved from PR1's gate).
**In / Out**: `services/run_group_forecast.py`, `adapters/forecast_interface.py`, `exceptions.py`, tests. Out: the
FI package, models. **Runs after T4** (same file).
**Verification**: one unit test per path asserts the reason on the outcome; a fake FI model returning
`ModelFailure(INPUT_DATA)` asserts `model_input_data`; the bad-area fixture (DS4) reports `model_input_data`.
**Pre-change**: RED - the outcome has no such field.

### T7 - One health record per group-model execution
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

### T8 - Documents (PR2; PR1's smaller doc set is T2d)
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
  `forecast_cycle.group_dropped` (new summary event, WARNING); the `missing_declared_static` reason on the unavailable event.
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

### T9a / T9b - Live check (staging host, orchestrator action)
**Outcome**: evidence on the running system, not on Prefect state. T9a after PR1 (statics); T9b after PR2
(conformance + health).
- **Before recreating any container, save the worker logs** to a file on the host and record the path (the
  2026-09-29 logs were lost this way; the 09-30 12:00Z log survived only because it was saved).
- **The pilot group reproduces the primary defect**, so PR1 can be proven live: run one cycle at the next scheduled
  or pinned issue time. Grep the saved worker log for `forecast_cycle.group_input_assembly_failed` (must be absent
  for `cmal_small`), `run_group_forecast.station_inputs_unavailable` (**T9a: the four cadence skips of 12:00Z are the
  baseline and carry no reason yet; only `missing_declared_static` skips carry a reason. Cadence reasons arrive with
  T4 and are checked at T9b**), `forecast_cycle.group_completed`, and, at T9b, `forecast_cycle.group_dropped`.
- **Compare the station-ID SET, not counts:** the set of station ids with a current `cmal_small` forecast for the
  intended model, parameter, issue time and current status, against the expected set (the 138 members minus the
  stations skipped with a logged reason: cadence gaps, declared-static skips, `max_nan` refusals), and list every
  difference (duplicate or superseded rows can hide missing stations behind a right-looking count). Record how the
  three sparse stations were treated, with the adapter's `station_input_nan_tolerance_exceeded` lines.
- **Expected `missing_declared_static` skips are the members with a declared-null static only.** Measure the number
  read-only in T1 by running the real static resolver over the 138 basins; do not assume it. The 84 null `reservoir_fs`
  values are undeclared and are **not** an expected skip count.
- Do not break staging to prove the whole-group drop health path; T7's tests cover it. If the owner wants live
  proof of the record, provoke it on a scratch group, never on the pilot group.
- Record: the station-id set difference, the record's detail JSON (T9b), the cycle's `health` and `groups_dropped`,
  and the saved log path.
**Verification**: the set comparison, the grep counts and the log path in a dated note in this plan.
**Pre-change**: baseline 2026-09-29 and 2026-09-30 12:00Z: 0 `cmal_small` rows.

## Exit gates

1. **PR1 (T1-T2, T2d, T2r, T9a):** the mixed-null undeclared-static stacking case fails on `main` (`SchemaError`, and a
   dtype-drift variant in both member orders) and passes after T2 with the unused column absent from the stacked
   result; the guards pass before and after; DS2's three cases skip with reason `missing_declared_static`; the
   station-fallback regression test passes; the declared dtype-drift case passes in both orders and all-DS2-skipped falls through to `None`; the bad-area case shows only the existing whole-batch `ModelFailure(INPUT_DATA)` and the logged empty outcome (its `model_input_data` reporting is PR2's); the T1 measurement of DS2 skips and per-declared-column dtype consistency is recorded and its merge gate (skip count > 0 or above the owner's bound: ask first) is cleared; the T1 dated note records
   the closed config-hash check and every consumer of `GroupModelInputs.static`
   (`adapters/forecast_interface.py`, `services/forecast_evidence.py` including `capture_group_evidence`,
   `services/run_group_forecast.py`, `types/model.py::for_station`).
2. **PR2:** T3's mixed-frame cases fail on `main` with the `ShapeError` and pass after T4; the all-empty case
   fails as "returned inputs" on `main`; the guards pass before and after; the non-zero-width negative test still
   reports an assembly failure; D5's flag test passes at both call sites; the bad-area case reports `model_input_data` (T6); null-fill dtype tests pass.
3. Every **recoverable** whole-group drop path in the table has a test showing a counted drop, one record with the
   right reason, and DEGRADED health - **except** where the cycle is already FAILED, which stays FAILED. The two
   non-drops are not counted. `StoreError` and fatal persistence failures still propagate. No path adds to
   `stations_failed`.
4. `uv run pytest tests/unit -q`, `uv run pytest tests/integration -q`, `uv run pyright`, `uv run ruff check`
   and `uv run ruff format --check` pass after the final change of **each** PR. The local venv lacks the `aquacast`
   extra (CI has it): also run the CI shards.
5. Multi-model review of the plan, then of each patch; the extra owner-commissioned review applies to **both** PRs
   (high risk; PR1 decides which stations receive a forecast).
6. T9a/T9b's station-ID set comparison and log-grep counts are recorded after each PR; PR2 work does not start until
   T9a is recorded.

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
- **Plans 404 and 323 do not collide:** 404's rejection buffering is preserved (T5); 323 owns its own health
  check type. Plan 257 (coverage check) and Plan 327 (refused-forecast handling and re-runs) are related: the
  implementer reads both before T5/T7.

## Risks

| Risk | Mitigation |
|---|---|
| A declared static is missing and the member is served without it | DS2 skips a member with a declared static that is absent, null or has no frame, before stacking (group-local); `max_nan` does not see statics, so this is the only guard; Q8. |
| Fixing the stack exposes a second whole-group drop: a declared static `None` makes the adapter raise `ConfigurationError` for the whole batch | DS2 (per-member skip before stacking); DS5 records that the adapter is not changed. |
| A non-null bad basin area (0, negative) still makes the aquacast shim refuse the whole batch | DS4: recorded as a known limit; Part B reports it as `model_input_data`; per-entry isolation is a candidate FI gap, not a SAP3 policy. |
| Failure depends on the id order of the first group member, so it can appear and disappear as membership changes | DS1 projection removes the undeclared source; T1 tests both orders. |
| The deployed model's declared statics differ from the checked-in config | T1 checks (UNVERIFIED until read); Q7. |
| Projection or DS2 wrongly changes station-path behaviour | Both live in `run_group_forecast.py` only; T1 has a station-fallback regression test. |
| A null-filled member is **served from all-null forcing** because the model's `max_nan` tolerance is at least the window | T3 reads the declared tolerances (UNVERIFIED); the grid is materialised so the gate can count; Q6 if a tolerance is too loose. |
| The quality flag is lost by the fill (`missing_buckets` is membership-only) | D5 and its test. |
| The change breaks the station caller or the tests asserting `None` | D6: sibling functions, originals unchanged; existing tests pass unchanged. |
| Conformance hides a real schema fault | D2: only declared, missing columns are added; everything else still fails and is visible through Part B. |
| DEGRADED on every cycle for a group that always has one bad member | Only whole-group drops degrade; skipped/null-filled members are an OK record (Q2). |
| The new record displaces the freshness heartbeat | A distinct check type. |
| Rollback or an older API cannot read the new check type | T8 deploy note. |
| Record spam from repeated identical outcomes and re-runs | D10. |
| A dropped or null-filled member changes a pooled group model's other outputs | Group models are per-station in `predict_batch`; UNVERIFIED for pooled models - the implementer reads `aquacast`'s `predict_batch` (Codex notes the pinned model derives its basin set from the supplied stations, which supports subset execution but does not prove numerically identical sibling forecasts under a changed batch). |
| A reason mapped wrongly (`other` swallows causes) | `other` logs the exception class at WARNING; T6 has one test per path. |
| A health-write failure aborts a cycle | `_append_pipeline_health_record` swallows and logs; T7 tests it. |

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
  forecast.** T3 will read the tolerance. *Recommendation:* if it is that loose, ask the model owner to tighten it
  rather than adding a SAP3-side rule; a SAP3 history-length gate is deliberately out of scope.

- **Q7 (closed on the description; the measurement remains) - Does the running forecast model ask for the same station properties as the copy we have checked in?** We
  read the checked-in description, which does not include the reservoir-share property that 84 of the 138 stations
  lack. **Answered 2026-09-30:** the copy inside the running system is byte-identical (checksum match), so those 84 stations need no
  special treatment. What stays open is the separate T1 measurement of how many members lack a property the model DOES ask for.
- **Q8 - If a station is missing a property the model does ask for, should it get a forecast?** Only relevant if
  the T1 measurement finds such stations, or for a new station later. The model was trained outside this system, so what it did with
  missing properties in training is unknown to us. *Recommendation:* skip the station and say why in the log
  (no forecast rather than a possibly wrong one), and ask the model's authors what their training did with missing
  values; confirm with the hydrologist. This plan does not judge bad-but-present values (see DS4).

## What changed after the reviews (2026-09-30)

Rev 3, after a second Claude and a second Codex review (both RECOMMEND NOT READY, overlapping reasons). A third round
needs to confirm ONLY the items below; everything not listed is unchanged from rev 2. Each review claim was checked
against `origin/main` before folding (statuses in brackets).

- **Primary statics fix is now PROJECTION, not dtype-casting (DS1).** [verified: `cmal_small.yaml` declares 78
  statics, none `reservoir_*`; `forecast_interface.py` `_static_inputs` selects only declared names; the group
  assembler stacks every basin key.] `Null`-only casting is demoted to a defence for declared columns and the
  dynamic frames (DS1b). The all-`Null` sentence in "Primary defect" item 2 is reconciled with the Float64 fallback.
- **DS2 stays group-local and is classified explicitly.** [verified: station flow `run_forecast_cycle.py` ~3483 skips
  the whole station on `None`, so a shared sibling would suppress static-free fallback models.] Three cases
  (no frame, absent declared column, declared null cell), a station-fallback regression test, and the non-null
  bad-area limit recorded as DS4 (a known remaining limit).
- **PR split now has per-PR docs, review and checkpoints** (T2d, T2r, T9a/T9b in the graph); the extra high-risk review
  applies to PR1 too; the cadence-reason logging stays in PR2 and checkpoint 1 is relaxed to match.
- **Q7 rewritten in plain language and split into Q7 (does the deployment match the checked-in declaration) and
  Q8 (declared-static policy for the externally trained model).** The claim that the artifact "never saw" such
  stations is removed (Plan 262 says it was trained externally). The "about 84 skips" expectation is removed from T9
  and from the risks: 84 null `reservoir_fs` values are undeclared and justify no exclusion.
- **T1 fixtures** separate undeclared stacking fixtures from declared missing-static fixtures.
- **D5 has a carrier** for the pre-fill gap record; exit gate 1 lists `capture_group_evidence`; the adapter's
  `ConfigurationError` on `None` is decided (DS5).
- Rejected or narrowed claims: see the hand-off report; none of the four blockers was rejected.

### Rev 4 (2026-09-30, after the round-3 Claude and Codex reviews; both NOT READY, narrowly)

A confirmation review reads ONLY these items. Claims were checked at `origin/main` 63169e55 (statuses in brackets).

- **PR1's exit gate no longer needs PR2** [verified: `run_group_forecast.py` ~597-609 only logs and returns an empty
  outcome; T6 is PR2]. The bad-area case asserts the existing whole-batch refusal in PR1; `model_input_data` moved to T6 / PR2's gate.
- **Declared dtype drift is handled (DS1c)** [reproduced: int versus float raises `SchemaError` in both orders at polars
  1.43.2]. Numeric declared statics are cast to `Float64` before stacking; `vertical_relaxed` still rejected (coerces
  string/float and future drift); test added; T1 measures per-declared-column dtype consistency with the REAL Caravan
  resolver over the 138 basins (no raw-key SQL). DS1b is dropped for statics (dead code) and mutation-checked for dynamic frames.
- **PR1 merge gate on the T1 measurement** (DS2 skip count > 0 or over the owner's bound: ask first). The config-identity
  prerequisite is CLOSED (checksum equal; stale statements in T1, Q7 updated); the missing-declared-values measurement stays.
- **Null-fill dtype specified** (Facts item 3, D3, T3): explicit `Float64` / sibling timestamp dtype; `Null` dtype is RED;
  the T1-versus-T3 coverage contradiction is reconciled.
- **D4 reworded** (DS2 is a second per-member skip). **DS4 lists more limits**: bool and non-scalar statics raise in
  `_static_value`; a collision raise in a member still drops the group.
- **D5 carrier chosen**: the existing per-station `OperationalInputMetadata` (no signature change to `run_group_forecast`
  or the flow call site).
- **Projection is not observably neutral**: `capture_group_evidence` records the narrowed static; noted in T2d; T1 greps
  for any evidence replay depending on undeclared columns (UNVERIFIED).
- **New T1 tests**: all members DS2-skipped falls through to `None`; the no-statics test is RED for populated basins and a
  guard only for the absent-basin case (Codex P2).
- **DS3 states PR2 is re-reviewed before its implementation.**

## Dependency graph

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1"] },
    { "id": "phase-2", "tasks": ["T2"], "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T2d", "T2r"], "depends_on": ["phase-2"], "note": "PR1 docs and review (incl. the extra high-risk review); full-suite gate after the final PR1 change" },
    { "id": "phase-4", "tasks": ["T9a"], "depends_on": ["phase-3"], "note": "PR1 live checkpoint; PR2 work may not start before it is recorded" },
    { "id": "phase-5", "tasks": ["T3"], "depends_on": ["phase-4"] },
    { "id": "phase-6", "tasks": ["T4"], "depends_on": ["phase-5"] },
    { "id": "phase-7", "tasks": ["T6"], "depends_on": ["phase-6"] },
    { "id": "phase-8", "tasks": ["T5", "T7"], "depends_on": ["phase-7"], "parallel": false },
    { "id": "phase-9", "tasks": ["T8"], "depends_on": ["phase-8"] },
    { "id": "phase-10", "tasks": ["T9b"], "depends_on": ["phase-9"], "note": "PR2 live checkpoint; PR2 review and full-suite gate precede it" }
  ]
}
```
