# Plan 107 — Operational-inputs / time-series-preprocessing touchpoint map

**Status:** READY
**Type:** Docs-only (edits `docs/workflow.md` only — no code)
**Owner:** Bea (marti@hydrosolutions.ch)
**Created:** 2026-07-08
**Relates to:** the FI/model-execution touchpoint map added in commit `aec0a7c`
(`docs/workflow.md` § Multi-Model Review → `### Touchpoint map: ForecastInterface /
model execution`).

> Planning deliverable for a **docs-only** change. It does not implement code. It
> specifies a labeled sub-block to **fold into the existing FI/model-execution
> touchpoint map** so agents that touch operational input assembly or time-series
> preprocessing know what else to inspect. Routing checklist, not architecture.

## Resolved decisions (grill-me, 2026-07-08)

1. **Fold, don't add a second map.** Extend the existing `### Touchpoint map:
   ForecastInterface / model execution`; do not create a parallel map subsection.
2. **Labeled sub-block.** Keep the single map heading, broaden its intro to name
   inputs + preprocessing, and add one clearly-labeled "Operational inputs /
   time-series preprocessing" sub-block inside the map.
3. **Include all four surfaced items:** no-imputation contract; shared
   resample-helper hazard; issue-time alignment contract; coverage-gate + ordering
   subtlety.
4. **Style = FI-map precedent:** subsystem + symbol names, **no line numbers**, no
   file paths, no architecture duplication.

## Plan-review resolutions (run `wf_4238d137-ea2`, 2 rounds, stalled on thrash guard)

Round 1 folded in the **hindcast third-assembly-path** completeness finding and
corrected placement. Round 2 stalled with 3 majors + 1 minor; resolved by owner
decision (2026-07-08):

1. **Common touch triggers extended** (major #2) — added source-fetch /
   preprocessing / superset / coverage-quality-gating triggers, not just the intro
   sentence. (owner: auto-fix)
2. **Issue-time contract style** (major #1) — keep the terse operational invariant
   (FI-map contract register), soften the **hindcast** boundary to a "diff
   `_assemble_hindcast_inputs` before changing" pointer; no operator-by-operator
   duplication in the map. (owner: "terse invariant + hindcast pointer")
3. **`assess_input_quality` added** (major #3) — second downstream gate beside
   `assess_future_coverage`; verified `input_quality.py:17`, called from
   `run_station_forecast.py:257` + `run_group_forecast.py:307`, `test_input_quality.py`.
   (owner: include)
4. **`HybridForcingSource` priority-merge contract added** (minor #4) — verified
   `hybrid_reanalysis.py:37` ("at most one row per (station, valid_time, parameter)").
   (owner: include)

## Independent over-engineering review (2026-07-08, post plan-review)

A separate independent reviewer (anti-bloat mandate) judged the resolved sub-block
**OVER-ENGINEERED** (~81 lines): it re-instantiated the map's own contracts +
verification structure a second time and padded pointers with how-the-code-works
rationale that would rot. Owner chose the **aggressive trim (~22 lines)**:

- fold the 3 kept contracts (**no-imputation**, **shared-resample**,
  **hybrid-priority**) into the map's **existing** "Contracts" list; fold 2 items
  into the **existing** "Suggested verification" list — no parallel sub-lists
- collapse the 4× hindcast mentions to **one** pointer (carries its issue-time caveat)
- **cut** the ordering-in-pivot contract and the standalone issue-time operator
  mechanics (both were grilled-in but judged reference detail that belongs in code)
- keep symbol-name routing bullets only

The **"Proposed sub-block content" section below is SUPERSEDED** by the trimmed
version now in `docs/workflow.md`; it is retained only as the pre-trim record.

## Goal

An agent whose task touches source fetch, operational input assembly, resampling /
aggregation, windowing, requirement-superset construction, or the NWP coverage gate
can read one sub-block and know the touchpoints, the must-not-change-silently
contracts, and the verification to run — without re-deriving the subsystem.

## Placement

Inside `### Touchpoint map: ForecastInterface / model execution`, add the sub-block
**after** the existing "Downstream consumers to inspect" group and **before** the
existing "Contracts that must not change silently" group. This keeps the map's
checklist-closing sections — "Contracts", the main "Suggested verification" (whose
last bullet, "full Task Exit Gate for implementation PRs", reads as the end-of-map
wrap-up), and the closing "Context packet reminder" — intact and last, so nothing
new appears *after* the established end-of-checklist marker. (Reviewer major: an
earlier placement between "Suggested verification" and "Context packet reminder"
would strand ~60 lines of new contracts behind the section an operational-inputs
agent reads as "you're done", defeating the Goal.) Broaden the map's opening
sentence to mention operational input assembly and time-series preprocessing.

## Proposed sub-block content (to add verbatim, modulo review)

> **Operational inputs / time-series preprocessing**
>
> This part of the map covers how raw source data becomes model prediction inputs —
> the assembly and time-series preprocessing that runs *before* the model boundary
> above. Inspect it when a task touches source fetch, input assembly, resampling /
> aggregation, windowing, requirement-superset construction, or the NWP coverage gate.
>
> **Assembly & sources:**
>
> - per-station assembly of four channels — past_targets (observations),
>   past_dynamic (reanalysis forcing), future_dynamic (NWP), static (basin
>   attributes) — plus prior/warm-up state (`assemble_station_operational_inputs`)
> - group assembly stacks per-station frames (`assemble_group_operational_inputs`)
> - **hindcast reimplements the same input assembly independently**
>   (`_assemble_hindcast_inputs`, used by `run_station_hindcast` /
>   `run_group_hindcast`) — it builds the identical `StationModelInputs` /
>   `GroupModelInputs` by hand, does **not** route through
>   `assemble_station_operational_inputs`, does **not** call `resample_to_time_step`,
>   derives features from a single model's `data_requirements` (not
>   `build_superset_requirements`), and uses its own issue-time boundaries (forcing
>   split `<= issue_time` past / `> issue_time` future, observations strict
>   `< issue_time`) — so any input-assembly / issue-time / requirements change must
>   be checked against hindcast too, not just operational + training
> - upstream sources: observation store, reanalysis source (store-backed / hybrid /
>   MeteoSwiss), weather-forecast (NWP) store + `GridExtractor` basin-average
>   extraction (runs upstream at the flow level, *not* inside the assembly function),
>   basin store, model-state store
>
> **Preprocessing steps:**
>
> - time-step resampling / aggregation (`resample_to_time_step`; precip SUM,
>   temperature & discharge MEAN)
> - NWP hourly→daily aggregation, issue-time filtering + horizon cap
> - lookback-window construction, wide-pivot + timestamp ordering
> - UTC normalization (`ensure_utc`), ensemble member fan-out
>
> **Requirements → assembly:**
>
> - per-model requirements drive what is assembled; the cycle assembles a
>   **superset** across assigned models (`build_superset_requirements`), then each
>   model consumes its slice
>
> **Coverage gate (downstream):**
>
> - `assess_future_coverage` gates NWP horizon truncation before predict (min clean
>   future rows ≥ required steps; ensemble member-set consistency)
>
> **Additional contracts that must not change silently (inputs / preprocessing):**
>
> - **No imputation.** Missing values are *gated* (`max_nan`), never imputed,
>   interpolated, or gap-filled on the operational path. A well-meaning `fillna`
>   changes model behavior silently.
> - **Shared resample helper.** `resample_to_time_step` and its aggregation-policy
>   fallback live in the training-data module and are shared with the **training**
>   path — a change there affects operational *and* training preprocessing. Note the
>   **hindcast** path does *not* use this helper: it filters raw forcing by hand and
>   can drift out of sync, so verify resampling / boundary changes against
>   `_assemble_hindcast_inputs` separately.
> - **Issue-time alignment.** Future rows are strictly `> issue_time` (a row *at*
>   issue_time counts as past); the axis is capped to the forecast horizon; the
>   lookback window is `issue_time − lookback_steps`.
> - **Ordering lives in the pivot helper.** `fetch_observations` has no `ORDER BY`;
>   timestamp ordering is established downstream in the wide-pivot helper — the
>   ordering contract lives there, not in the store.
>
> **Suggested verification (inputs / preprocessing):**
>
> - unit tests around `resample_to_time_step` and station / group assembly
> - regression test that missing data is *gated, not filled* (assert `max_nan`
>   behavior, not imputation)
> - forecast-cycle test covering superset requirements → assembly → coverage gate
> - confirm a `resample_to_time_step` change is intentional for BOTH the operational
>   and training paths
> - confirm an issue-time / input-assembly / requirements change is intentional for
>   the **hindcast** path (`_assemble_hindcast_inputs`) too — it reimplements the
>   boundary logic independently

## Code grounding (for reviewers — not copied into the map)

- Station assembly + four channels + prior state:
  `src/sapphire_flow/services/operational_inputs.py:240` (channels `:271-388`);
  domain type `StationModelInputs` `src/sapphire_flow/types/model.py:60`.
- Group assembly: `src/sapphire_flow/services/run_group_forecast.py:110`;
  `GroupModelInputs` `types/model.py:79`.
- Hindcast (third, independent assembly): `_assemble_hindcast_inputs`
  `src/sapphire_flow/services/hindcast.py:134` (hand-rolled forcing filter
  `:151-158`; forcing split `<= issue_time` / `> issue_time` `:196-197`;
  observations strict `< issue_time` `:167`; **no** `resample_to_time_step` call —
  grep confirms zero in the module); callers `run_station_hindcast` `:254` /
  `run_group_hindcast` `:431`; per-model requirements (not superset) `:292-294`,
  `:473-475`.
- `GridExtractor` Protocol: `src/sapphire_flow/protocols/grid_extractor.py:16`;
  impls `src/sapphire_flow/preprocessing/exact_extract_grid_extractor.py`,
  `src/sapphire_flow/preprocessing/mesh_basin_extractor.py`; invoked at the flow
  level (`grid_extractor.extract(...)` `flows/run_forecast_cycle.py:730`, ahead of
  assembly) — NOT inside `operational_inputs.py`.
- Resample/aggregation policy (shared w/ training):
  `src/sapphire_flow/services/training_data.py:43`; fallback map `:29`.
- NWP hourly→daily: `operational_inputs.py:71`; issue-time filter/cap `:111`;
  lookback `:268`; wide-pivots `:61,:129,:164,:177`.
- `ensure_utc`: `src/sapphire_flow/types/datetime.py:7` (applied `operational_inputs.py:95,:268`).
- Superset requirements: `build_superset_requirements` `operational_inputs.py:199`
  (cycle `flows/run_forecast_cycle.py:1353`).
- Coverage gate: `assess_future_coverage` `src/sapphire_flow/services/nwp_coverage.py:65`.
- No-imputation: gating only via `max_nan` (`adapters/forecast_interface.py:592,:617`);
  Explore confirmed no interpolation/imputation on the operational path.
- Ordering: `fetch_observations` has no `ORDER BY`
  (`store/observation_store.py:145-157`); order set in `_observations_to_wide_dataframe`
  `operational_inputs.py:164`.
- Ensemble fan-out: `src/sapphire_flow/services/ensemble_fanout.py:102`.
- Tests: `tests/unit/services/test_operational_inputs.py`,
  `test_training_data.py`, `test_run_group_forecast.py`,
  `tests/unit/flows/test_run_forecast_cycle.py`,
  `tests/unit/adapters/test_forecast_interface_adapter_nan_gate.py`.

## Non-goals

- No code changes; no new tests (the "suggested verification" list is guidance for
  future tasks, not work to do now).
- No architecture prose; no line numbers or file paths in the map itself.
- Do not restructure or reword the existing FI/model-execution map bullets beyond
  broadening its one-sentence intro.

## Verification

- `docs/workflow.md` is the only file changed.
- The sub-block is nested under the existing map heading (one map, one heading).
- Symbol names match the repo; no line numbers/paths leak into the map.
- Reads as a routing checklist consistent with the surrounding context-packet and
  review-gate language.

## Acceptance criteria

1. Sub-block added inside the FI/model-execution map at the specified placement.
2. All four resolved content items present and code-accurate.
3. Map intro broadened to name inputs + preprocessing.
4. No code/test/plan files changed other than this plan doc + `docs/workflow.md`.
