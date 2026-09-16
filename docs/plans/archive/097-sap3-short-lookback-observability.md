# Plan 097 — SAP3 observability: warn when the delivered lookback is short

**Status**: READY (2026-07-13 — WF1 plan-review converged [3 rounds, 0 blockers/
majors] + independent adversarial Codex review both recommend READY; all
citations verified against HEAD)
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
   immediately after the `no_observations` block.** Slot the check right after the
   `latest_obs_ts` computation and the existing `operational_inputs.no_observations`
   warning block ends (`services/operational_inputs.py:292–297`) — i.e. the check
   is placed AFTER line 297, guarded by `if latest_obs_ts is not None:`. (Do **not**
   place it at `:287`, immediately after `past_targets = resample_to_time_step(...)`
   at `:284–286`; that is before the `no_observations` block and would make the
   `with_obs=False` existing test accumulate a spurious `short_lookback` event —
   see the existing-test note.) This is the
   closest point to the delivered data and fires every cycle. **Gate the check on
   `all_observations` being non-empty (equivalently `latest_obs_ts is not None`):**
   the wholly-empty case is already covered by `no_observations` (`:293–297`), so
   emitting `short_lookback` there too would double-fire two warnings for the same
   root cause in a single cycle. `short_lookback` is only meaningful when *some*
   target observations exist but fewer than `lookback_steps` arrived. (This is why
   the check goes **after** the `no_observations` block, not before it.)
2. **What we compare against — RESOLVED: the *effective assembled* requirement,
   not a single model.** `assemble_station_operational_inputs` is intentionally
   model-agnostic and compares against `reqs.lookback_steps` (whatever the caller
   passes, directly or via `requirements_override`). The warning reports the
   **effective** shortfall for the station's assembled window, keyed on
   `reqs.lookback_steps`. It does **not** attribute the shortfall to a specific
   model Y — per-model attribution is a **non-goal** here → Flow 4 monitoring (see
   non-goals). Log the `model_id` passed into assembly as `representative_model_id`
   for correlation only (not the culprit / not the superset-driving model). Its
   provenance differs by caller; the routing detail belongs in a **call-site
   comment** in `run_forecast_cycle.py` and `run_group_forecast.py` (where it will
   be maintained), not at plan level. Summary of the two shapes:
   - **STATION path** passes `requirements_override=<superset>`, so `reqs.lookback_steps`
     is the `max(lookback_steps)` across all assigned models (a superset threshold),
     while `representative_model_id` is *a* representative assignment, **not** the
     superset-driving model.
   - **GROUP path** passes **no** `requirements_override`, so `reqs` is the GROUP
     model's own `model.data_requirements` and `representative_model_id` **is** the
     GROUP model's id — `lookback_steps` is exactly the right threshold, the warning
     fires correctly. The GROUP call to `assemble_station_operational_inputs` lives
     **inside** `assemble_group_operational_inputs`
     (`services/run_group_forecast.py:128–150`, passing `model_id=<group model>` and
     no `requirements_override`), reached both directly and transitively via
     `flows/run_forecast_cycle.py`. (See the GROUP-path acceptance note below.)
3. **Definition of "clean row count" — RATIFIED (owner, 2026-07-07): per-target
   minimum non-null count after resampling.** Use the **per-target minimum
   non-null count** (not frame height) so a healthy sibling target cannot mask a
   short one in multi-parameter configurations. Concretely: for each declared
   target parameter, count the non-null rows in that parameter's column of the
   resampled `past_targets` frame; the reported
   `lookback_got` is the **minimum** of those per-target counts, compared against
   `reqs.lookback_steps`. The check iterates the **declared**
   `reqs.target_parameters` (known before fetch, `services/operational_inputs.py:271`),
   not the frame's columns. Emit `per_target_counts={parameter: non_null_count}`
   (full breakdown so operators see *which* target is short),
   `lookback_needed=reqs.lookback_steps`, and `lookback_got=<per-target minimum>`.

   **Column-presence guard (BLOCKER fix — required implementation detail):** a
   **wholly-absent declared target counts as 0**. `_observations_to_wide_dataframe`
   (`services/operational_inputs.py:164–174`) only builds a column for a parameter
   that produced at least one observation, so a declared target with zero
   observations in the lookback window has **no column** in the resampled
   `past_targets` frame. A bare `past_targets[parameter]` access on such a target
   raises `polars.exceptions.ColumnNotFoundError` — a new crash in a best-effort
   observability addition, and it is exactly the acceptance-required "wholly-absent
   target = 0" scenario (which is *not* skipped by the zero-obs gate when a
   sibling target has observations, so `all_observations` is non-empty). The count
   MUST therefore be computed with an explicit column-presence guard, e.g.:

   ```python
   count = (
       past_targets[parameter].drop_nulls().len()
       if parameter in past_targets.columns
       else 0
   )
   ```

   (equivalently `past_targets.height - past_targets[parameter].null_count()` when
   present, else 0). **Never index an absent column.** This idiom is a required
   implementation detail, not just a semantic intent.

   The union-height overcount reasoning and the rejected-alternatives (union
   height / discharge-only) belong as an **inline comment at the implementation
   site** in `operational_inputs.py`, not in this archived plan.

   **Empty-`target_parameters` guard (BLOCKER fix):** `ModelDataRequirements.__post_init__`
   validates only `lookback_steps >= 1` and `forecast_horizon_steps >= 1`
   (`types/model.py:273–279`) — it does **not** require `target_parameters` to be
   non-empty, so `frozenset()` is constructible. A native SAP3 model (not via the
   FI adapter) could therefore declare zero targets. (FI-projected models cannot:
   FI's own `targets` validator requires at least one entry — the implementer can
   confirm this directly in the FI repo; the plan need only mandate the guard.)
   Taking `min()` over an empty
   per-target-count collection raises `ValueError: min() arg is an empty sequence`
   — a new crash path in a best-effort observability addition. **The check MUST
   early-exit when `reqs.target_parameters` is empty** (no declared targets = no
   lags to count): `if not reqs.target_parameters: <skip, optional debug note>`.
   (A cleaner alternative — adding a `target_parameters` non-emptiness check to
   `ModelDataRequirements.__post_init__` — is out of scope for this doc-only plan;
   note it as a possible follow-up. Until then, the early-exit is mandatory.)
4. **Requirement source is populated for FI models — CONFIRMED (repo-grounded).**
   `ModelDataRequirements.lookback_steps` is already populated by the FI adapter
   (via `_project_requirements` in `adapters/forecast_interface.py`, which folds in
   `variable.lookback`) and used by assembly for `lookback_start`. No adapter change
   needed.
5. **Severity + channel — RESOLVED: `structlog` WARNING only, event
   `operational_inputs.short_lookback`.** Matches the sibling events in the same
   module (`operational_inputs.no_observations` `:293–297`,
   `operational_inputs.no_past_dynamic` `:313`, `operational_inputs.no_nwp`
   `:333`) and the module logger (`:39`). A Flow 4 monitoring signal is a
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
- **Short-lookback observability in the hindcast path.** `services/hindcast.py`
  (in the Related header) has its own parallel input assembly
  (`_assemble_hindcast_inputs`, `services/hindcast.py:134`) that fetches
  observations, short-circuits on `if not observations` and builds `past_targets`
  independently of `assemble_station_operational_inputs`. The new
  `short_lookback` warning will **not** fire for hindcast runs. Closing the same
  gap there (e.g. during commissioning with short archives) is a **separate
  follow-up**, deliberately out of scope for this plan (the operational Flow-1
  path is the priority; hindcast is a commissioning/verification path). The
  Related header lists hindcast only as an adjacent assembly site, not an in-scope
  change.

## Acceptance criteria

- A station whose **per-target minimum** non-null count (after resampling; a
  wholly-absent declared target counts as 0) is fewer than `reqs.lookback_steps`
  — **and has at least one target observation** (see the zero-obs criterion below)
  — logs exactly one `operational_inputs.short_lookback` WARNING per assembly call,
  carrying `station_id`, `issue_time`, `representative_model_id` (correlation only
  — the assembly `model_id`, not the superset-driving model),
  `per_target_counts` (`{parameter: non_null_count}`),
  `lookback_needed` (= `reqs.lookback_steps`), and `lookback_got` (= the per-target
  minimum).
- A station whose per-target minimum count is `>= lookback_steps` logs nothing.
- **Column-presence guard is mandatory (see Decision 3).** The per-target count for
  parameter `p` is `past_targets[p].drop_nulls().len() if p in past_targets.columns
  else 0` — a bare `past_targets[p]` access raises
  `polars.exceptions.ColumnNotFoundError` when `p` had zero observations (no column).
  This is a required implementation detail, verified by the wholly-absent-target
  test case below.
- **When `reqs.target_parameters` is empty, no `short_lookback` warning is emitted**
  (early-exit; see Decision 3 — avoids the `min()`-of-empty crash for native
  zero-target models). Note this bounds only `short_lookback`: a zero-target model
  fetches no observations, so the existing `operational_inputs.no_observations`
  WARNING still fires for that case — which is correct and expected.
- **When there are no target observations at all** (`all_observations` empty),
  `short_lookback` is **not** emitted — that case is covered by
  `operational_inputs.no_observations` (`:293–297`); emitting both would
  double-warn for one root cause (see Decision 1).
- No change to return type, control flow, or the model-side Plan 093 guard.
- **GROUP path**: `assemble_group_operational_inputs`
  (`services/run_group_forecast.py:128–150`) reaches the same warning through its
  per-member `assemble_station_operational_inputs` call; a short GROUP-member
  station fires `short_lookback` with `representative_model_id` = the GROUP model's
  id (correct threshold). The test SHOULD cover at least the STATION path; a GROUP
  case is nice-to-have (same code path, different `model_id` provenance).
- Unit test uses `structlog.testing.capture_logs()` (per
  `docs/standards/logging.md:412`) with a fake `obs_store` returning `< lookback`
  QC-passed rows for at least one declared target (including the wholly-absent
  target = 0 case), and asserts **no** `short_lookback` when observations are
  wholly absent and when `target_parameters` is empty.
- **Required fixture fix (BLOCKER)**: the shared fixture `_make_stores_and_sources`
  seeds `obs_start = _utc(2026, 1, 9, 2)` (`tests/unit/services/test_operational_inputs.py:178`)
  as a **fixed** timestamp, unrelated to `_ISSUE` (`:39`, `2026-01-10 00:00`). With
  the default `n_obs=20` at 1h and `lookback_steps=10` (`:126`), the lookback window
  is `_ISSUE − 10 h = 2026-01-09 14:00 .. 2026-01-10 00:00`; only 8 of the 20 obs
  (14:00–21:00) fall inside it. 8 < 10, so the new check would fire a spurious
  `operational_inputs.short_lookback` on the notional happy path in **all five**
  `with_obs=True` (default) tests that use `_make_stores_and_sources`:
  `test_happy_path_returns_inputs_and_fresh_metadata` (`:210`),
  `test_no_warm_up_state_returns_cold_start` (`:335`),
  `test_empty_past_dynamic_features_skips_reanalysis` (`:366`),
  `test_missing_nwp_returns_none` (`:248`, `with_nwp=False` but default `with_obs=True`;
  the check fires before the NWP-missing `return None`), and
  `test_stale_warm_up_state_returns_snapshot` (`:308`, `state_age_hours=30.0`, default
  `with_obs=True`). Those tests pass today only because none use
  `capture_logs`, so the spurious WARNING is silent — but the "healthy station,
  no warning" invariant is violated and any future `capture_logs` assertion on them
  would fail. **Fix (required, not optional): in `_make_stores_and_sources`, derive
  `obs_start` from `_ISSUE` so `n_obs` obs at the fixture interval fill the whole
  lookback window**, e.g. `obs_start = ensure_utc(_ISSUE - n_obs * timedelta(hours=1))`.
  This restores `per-target count >= lookback_steps` for every `with_obs=True`
  existing test, preserving the happy-path "no warning" invariant.
- **Existing-test note**: `test_missing_observations_returns_inputs_with_none_staleness`
  (`tests/unit/services/test_operational_inputs.py:275`) runs with `with_obs=False`,
  so it emits only `no_observations`, **not** `short_lookback` — no change needed.

## Process

Design decisions above settle the detection point, requirement source, count
definition, and noise policy. The two escalated items —
decision 3 (multi-target count semantics: per-target minimum non-null count) and
decision 6 (per-cycle warning during archive fill, no SAP3-side suppression) —
were **owner-ratified 2026-07-07**. Plan is now **READY** (2026-07-13): WF1
plan-review + independent Codex review both converged clean; the WF2 build draws
up the concrete steps. Small: a per-target count-vs-lookback check + a `structlog`
warning at input assembly + the required `_make_stores_and_sources` fixture fix;
test that a station whose per-target minimum is `< lookback` logs the event with
`per_target_counts` (and that healthy / wholly-absent-obs / empty-target cases do
not).
