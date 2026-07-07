# Plan 097 — SAP3 observability: warn when the delivered lookback is short

**Status**: DRAFT
**Priority**: low — observability/upstream complement to Plan 093; the model
guard already prevents the crash, this surfaces the *cause* earlier.
**Phase**: v0b — operational observability
**Parent**: Plan 093 (grill-me spun this out as a companion, 2026-07-03)
**Related**: `services/operational_inputs.py` (past-target/lag assembly),
`services/hindcast.py`, `models/nwp_regression.py` (`artifact.n_lags`), the FI
`PastKnownVariable.lookback` contract
**Created**: 2026-07-03

---

## Problem

Plan 093 makes `nwp_regression` return a typed `ModelFailure("insufficient lag
history: got 3, need 8")` instead of crashing. That fixes the symptom, but the
*root gap* — SAP3 delivered fewer past discharge rows than the model's declared
`lookback`/`n_lags` needs (a short observation archive) — is only visible **at
predict time, per cycle**, once the model fails. There is no earlier signal that
a station is structurally under-supplied for a model it is assigned.

## Goal

SAP3 emits an **early, explicit** warning when the past-target window it
assembles for a station/model is shorter than the model's declared `lookback`
(FI `PastKnownVariable.lookback`) — at input-assembly time, not (only) when
`predict` fails — so operators see "station X has only N of the M lag rows model
Y needs" without waiting for a failed forecast.

## Design decisions (resolved in review-loop iteration 1; owner-ratified 2026-07-07)

The multi-model review loop (Claude design lens + Codex repo-grounded pass)
settled these against repo facts, and the owner **ratified** the two escalated
items (decision 3 multi-target count semantics, decision 6 archive-fill noise
policy) on 2026-07-07. All six items below are decided — none remain open. Line
references are `src/sapphire_flow/...` (so `flows/...` = `src/sapphire_flow/flows/...`).

1. **Where to detect — RESOLVED: `assemble_station_operational_inputs`,
   immediately after the `past_targets` resample.** Slot the check right after
   `past_targets = resample_to_time_step(...)`
   (`services/operational_inputs.py:284–286`), before the existing
   `operational_inputs.no_observations` warning block (`:288`). This is the
   closest point to the delivered data and fires every cycle.
2. **What we compare against — RESOLVED: the *effective assembled* requirement,
   not a single model.** `assemble_station_operational_inputs` is intentionally
   model-agnostic: when a station has heterogeneous models the caller passes a
   **superset** `requirements_override`, and the function compares against
   `reqs.lookback_steps` (`services/operational_inputs.py:263–268`). The warning
   therefore reports the **effective** shortfall for the station's assembled
   window, keyed on `reqs.lookback_steps`. It does **not** attribute the
   shortfall to a specific model Y (the flow passes only the first model plus the
   superset override — the assembly call sets `model_id=sorted_assignments[0].model_id`
   and `requirements_override=superset_reqs`, `flows/run_forecast_cycle.py:1376,1389`
   — so per-model attribution is not available at this layer). Per-model attribution
   is a **non-goal** here → Flow 4 monitoring (see non-goals). Include the `model_id`
   passed into assembly as context **only** — note it is
   `sorted_assignments[0].model_id`, the station's *first-priority* assignment
   (`flows/run_forecast_cycle.py:1319–1320`), which is **not** necessarily the model
   driving `max(lookback_steps)`; the superset requirement is built from *all*
   assigned models via `build_superset_requirements`
   (`flows/run_forecast_cycle.py:1343`). Log it as a `representative_model_id` for
   correlation, not as the culprit or the superset-driving model.
3. **Definition of "clean row count" — RATIFIED (owner, 2026-07-07): per-target
   minimum non-null count after resampling.** There was no pre-existing "clean row
   count" helper in assembly; this plan defines it. `past_targets` is built from
   `QcStatus.QC_PASSED` observations and then resampled
   (`services/operational_inputs.py:273–286`). Note `resample_to_time_step`
   **does not densify** — it groups existing rows into dynamic windows and
   emits a row only where data exists (`services/training_data.py:93–99`); a
   short archive therefore yields a **shorter frame** (fewer rows), not a
   full-length frame padded with nulls.

   The naive count would be the raw **height** of the resampled `past_targets`
   frame (`past_targets.height`) against `reqs.lookback_steps` — but for a
   **multi-target** requirement that overcounts: `_observations_to_wide_dataframe`
   pivots the **union** of target timestamps and fills only the parameters present
   (`services/operational_inputs.py:164`), and the FI adapter maps *all*
   `req.targets` into `target_parameters` (`adapters/forecast_interface.py:515`;
   multi-target confirmed by
   `tests/unit/adapters/test_forecast_interface_adapter.py:196`), so
   `past_targets.height` counts windows with **any** target present and can
   **overcount** when one target column is short.

   The ratified count is therefore the **per-target minimum non-null count after
   resampling**, compared against `reqs.lookback_steps`. Concretely: for each
   declared target parameter, count the non-null rows in that parameter's column
   of the resampled `past_targets` frame; the reported `lookback_got` is the
   **minimum** of those per-target counts. **A declared target with no column
   present counts as 0** (a wholly-absent target is the most-short target, and must
   not be silently skipped) — the wide-pivot returns only observed parameters as
   columns, and an empty observation set returns an empty frame
   (`services/operational_inputs.py:167`), so the check iterates the **declared**
   `reqs.target_parameters` (known before fetch,
   `services/operational_inputs.py:271`) rather than the frame's columns.

   - **Reject union height** because it can false-negative when a healthy sibling
     target masks a short target — the exact overcount failure above.
   - **Reject discharge-only** because it hard-codes a privileged parameter into a
     parameter-agnostic layer; `assemble_station_operational_inputs` is
     intentionally model- and parameter-agnostic.

   v0 runs multi-parameter (discharge + water_level), so this is a live case, not
   hypothetical. Emit `per_target_counts={parameter: non_null_count}` (the full
   per-target breakdown so operators see *which* target is short),
   `lookback_needed=reqs.lookback_steps`, and `lookback_got=<the per-target
   minimum>` in the warning event.
4. **Requirement source is populated for FI models — CONFIRMED (repo-grounded).**
   SAP3-side `ModelDataRequirements.lookback_steps` exists and is validated
   (`types/model.py:261,273`) and assembly already uses it for `lookback_start`
   (`services/operational_inputs.py:268`). The FI adapter projects
   `PastKnownVariable.lookback` into it: `_project_requirements` sets
   `data_requirements` (`adapters/forecast_interface.py:439`), collecting
   `past_known` vars (`:468`), taking `max(..., variable.lookback)` (`:506`), and
   returning `lookback_steps=` (`:514`). Covered by tests
   (`tests/unit/services/test_model_discovery_fi.py:117`,
   `tests/unit/adapters/test_forecast_interface_adapter.py:190`). No adapter
   change needed.
5. **Severity + channel — RESOLVED: `structlog` WARNING only, event
   `operational_inputs.short_lookback`.** Matches the sibling events in the same
   module (`operational_inputs.no_observations` `:293`,
   `operational_inputs.no_past_dynamic` `:313`, `operational_inputs.no_nwp`
   `:331`) and the module logger (`:39`). A Flow 4 monitoring signal is a
   deliberate later step, not this plan.
6. **Noise control — RATIFIED (owner, 2026-07-07): emit a per-cycle WARNING with
   no SAP3-side suppression.** `assemble_station_operational_inputs` runs once per
   station per forecast cycle, so the check fires at most once per station per cycle
   by construction — no dedupe logic needed. During the archive-fill window
   (Mac-mini run, lags accrue over ~7 days) a structurally short station will warn
   each cycle: **the warning storm during archive fill is expected by design** —
   that is the intended early signal, and the WARNING level keeps it out of INFO
   noise. The **interim mitigation** is a **log-query/dashboard filter on
   `operational_inputs.short_lookback` during the known fill window** — not SAP3
   code. **Flow 4 owns all lifecycle-aware throttling/dedup/suppression**;
   cross-cycle throttling (warn-once / until-N-days-accrued) needs station lifecycle
   state this layer does not hold, so it is explicitly deferred there.

## Non-goals

- The model-side guard itself (Plan 093 — done separately).
- Padding/synthesising missing lags (never; a short archive means a real
  data-availability limit).
- **Per-model attribution** of the shortfall (which specific assigned model Y is
  under-supplied). Assembly sees only the superset requirement, not each model
  individually — attribution belongs to Flow 4 pipeline monitoring, not this log
  event.
- **Cross-cycle warning suppression / throttling** — deferred to Flow 4 (needs
  station lifecycle state this layer does not hold). During archive fill, the
  interim mitigation is a log/dashboard query filter, not SAP3 code.

## Acceptance criteria

- A station whose **per-target minimum** non-null count (after resampling; a
  wholly-absent declared target counts as 0) is fewer than `reqs.lookback_steps`
  logs exactly one `operational_inputs.short_lookback` WARNING per assembly call,
  carrying `station_id`, `issue_time`, `representative_model_id` (the
  first-priority assignment, correlation only — not the superset-driving model),
  `per_target_counts` (`{parameter: non_null_count}`),
  `lookback_needed` (= `reqs.lookback_steps`), and `lookback_got` (= the per-target
  minimum).
- A station whose per-target minimum count is `>= lookback_steps` logs nothing.
- No change to return type, control flow, or the model-side Plan 093 guard.
- Unit test uses `structlog.testing.capture_logs()` (per
  `docs/standards/logging.md:408`) with a fake `obs_store` returning `< lookback`
  QC-passed rows for at least one declared target (including the wholly-absent
  target = 0 case).

## Process

Design decisions above settle the detection point, requirement source, count
definition, and noise policy. The two escalated items —
decision 3 (multi-target count semantics: per-target minimum non-null count) and
decision 6 (per-cycle warning during archive fill, no SAP3-side suppression) —
were **owner-ratified 2026-07-07**. Remaining step to READY: owner sets
`status: READY` and phases are drawn up. Small: a per-target count-vs-lookback
check + a `structlog` warning at input assembly; test that a station whose
per-target minimum is `< lookback` logs the event with `per_target_counts`.
