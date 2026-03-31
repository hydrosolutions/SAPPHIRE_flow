---
status: DONE
created: 2026-03-30
revised: 2026-03-31e
reviewed: 2026-03-31
scope: investigation + design — forecast QC insertion in Flow 1, step numbering, fallback behavior, alert suppression, logging events, data dependencies
depends_on: []  # blocks Phase 8 (Flow 1 implementation) — design only
---

# 012 — Forecast QC Integration in Flow 1

## Problem

The `ForecastOutputQualityChecker` service is fully implemented
(`services/forecast_qc.py`, implementing the `ForecastQualityChecker` Protocol) with 7
rules (negative_value, range_check, flat_ensemble, ensemble_spread, climatology_outlier,
temporal_consistency, quantile_crossing). Types are complete (`ForecastQcRuleSet`,
`QcFlag`, `QcStatus`). DB schema supports it.

**The gap:** There is **no step in Flow 1** (operational forecast) that invokes the
checker. The architecture shows: model output (1.8) → post-process (1.9) → store (1.10)
→ alert thresholds (1.11). Forecast plausibility checking is missing between model
output and storage/alerting.

### Naming note

The Protocol is `ForecastQualityChecker` (`types-and-protocols.md` line 568); the
implementation is `ForecastOutputQualityChecker` (`services/forecast_qc.py`). This
follows the codebase convention where implementations never mirror Protocol names
(cf. `ForecastStore` → `PgForecastStore`, `ModelAlertStrategy` →
`PrimaryModelStrategy`). This plan uses the Protocol name when discussing the spec and
the implementation name when discussing the service. They are the same interface.

### Cross-document inconsistencies

Two authoritative documents conflict on step 1.9:

- **`architecture-context.md` line 110** defines step 1.9 as: "Forecast *output* bias
  correction (discharge / water level). Distinct from NWP input correction in 1.5.
  Pass-through when not configured."
- **`types-and-protocols.md` line 585** maps step 1.9 to forecast QC: "Step 1.9:
  Forecast output QC. Runs `ForecastQualityChecker.check()` on each ensemble."

Additionally, **`v0-scope.md` line 25** declares: "Steps 1.5 (NWP post-process) and
1.9 (forecast post-process) are pass-through throughout v0." If QC is mapped to step
1.9, it would also be pass-through in v0 — contradicting the intent of this plan.

### Existing design intent

`types-and-protocols.md` line 585 already specifies behavior:
- Aggregate `QC_FAILED` → raise `SanityCheckFailure` → flow tries fallback model
- `QC_PASSED` / `QC_SUSPECT` → results stored on `OperationalForecast`
- For hindcasts: `QC_FAILED` flags the hindcast but does not trigger fallback

This design must be validated and reconciled with the architecture, not re-derived.

## Investigation Tasks

**Task dependencies:** Tasks 4 and 5 are blocked by task 3 (fallback design must be
settled before hindcast QC and alert suppression can be finalized). Task 5 is also
blocked by task 1 (alert filtering depends on knowing the step number and what
`qc_status` field is available at Phase C) and task 2 (per-parameter fallback dispatch
determines which `QC_FAILED` forecasts reach Phase C). Tasks 6, 7, and 8 produce decision records
and document updates; all can parallelize once tasks 1–5 are resolved.

1. **Resolve step numbering conflict.** Decide whether forecast QC becomes a new step
   (e.g. 1.8a between model output and bias correction) or step 1.9 is split/redefined.
   Update `architecture-context.md`, `types-and-protocols.md`, and `v0-scope.md` to be
   consistent. Assigning QC its own step number (independent of step 1.9) is the
   prerequisite for forecast QC being active in v0: if QC shares the 1.9 number, the
   `v0-scope.md` line 25 "1.9 pass-through" declaration silently suppresses it.

   Beyond the step table and Mermaid diagram, three additional locations in
   `architecture-context.md` require updates as consequences of the numbering decision:
   - **Line 95**: "Steps 1.2, 1.3, 1.4, and 1.9 are conditional" — the new QC step must
     be added to or excluded from this sentence depending on whether QC is always-on or
     skippable.
   - **Line 154**: "On raw forecasts (immediately after 1.9)" — the threshold-checking
     resolved-decision section uses 1.9 as the anchor for "immediately before Phase C."
     If a new QC step is inserted between 1.9 and Phase C, this reference must be updated.
   - **Line 366 (Flow 3, steps 3.5–3.7)**: Flow 3 re-triggers steps 1.11–1.13 on
     published values. If alert suppression (task 5) filters QC-failed forecasts before
     Phase C, the same filter must apply in the Flow 3 re-trigger path — otherwise
     QC-failed forecasts could surface as alerts at publication time.

2. **Confirm insertion point.** Validate whether QC should run before or after bias
   correction (step 1.9). Reference `types-and-protocols.md` line 585 as the existing
   spec. Trade-off: QC on raw model output catches model bugs; QC after bias correction
   also catches correction bugs. Since 1.9 is pass-through in v0, QC always runs on raw
   model output in v0 regardless of the ordering decision — but the architecture must
   specify the correct v1 position so the step table is not wrong-by-accident.

   **Data dependencies for QC invocation:** `ForecastQualityChecker.check()` requires
   four inputs: `ensemble` (available from step 1.8), `rule_set` (loaded via
   `config/forecast_qc_rules.py` — pre-loadable at flow start), `overrides` (per-station,
   from `forecast_qc_overrides` table — requires DB fetch), and `baselines` (from
   `ClimBaseline` store — requires DB fetch). The insertion point design must account for
   where and when `overrides` and `baselines` are fetched: batch pre-fetch for all
   stations at flow start (like observation fetch in 1.6) or per-station inside the QC
   step. Batch is preferred for performance.

   **Return type coercion:** `check()` returns `list[QcFlag]`, but
   `OperationalForecast.qc_flags` and `HindcastForecast.qc_flags` are typed as
   `tuple[QcFlag, ...]` (frozen dataclass convention). The flow layer must coerce
   `tuple(flags)` before constructing the forecast object.

   **Loader caveat:** `load_forecast_qc_rules()` raises `ValueError` when
   `SAPPHIRE_CONFIG` is unset and no path argument is provided. `load_qc_rules()`
   (observation QC) has the same `ValueError` behavior — both loaders fall back to
   Swiss defaults only when the config file exists but lacks their respective TOML
   section (`[forecast_qc_rules]` / `[qc_rules]`). The `docker-compose.yml`
   `prefect-worker` service does not currently set `SAPPHIRE_CONFIG` (though it
   mounts `config.toml` at `/app/config.toml`). The Flow 1 implementation must
   either (a) add `SAPPHIRE_CONFIG=/app/config.toml` to the worker environment
   block, or (b) wrap the call defensively following the pattern in
   `flows/onboard.py` (check env var, fall back to
   `_default_swiss_forecast_qc_rules()` directly). Option (b) follows the
   established convention — `onboard.py` uses this pattern precisely because the
   loader raises rather than falling back when the env var is absent.

   **Per-parameter invocation:** `check()` receives a single `ForecastEnsemble` — it
   operates on one (station, parameter) pair at a time. `OperationalForecast` wraps a
   single `ForecastEnsemble` whose `parameter` field identifies the forecast target
   (accessed as `forecast.ensemble.parameter` in code; denormalized as a column on the
   `forecasts` table for indexed querying) — so there is one `OperationalForecast` per
   (station, model, parameter). v0 exercises two forecast
   parameters (`discharge`, `water_level` — see `v0-scope.md` §A13). Rules `range_check`
   and `climatology_outlier` are inherently parameter-specific (valid discharge ranges
   differ completely from valid water level ranges). The QC step invokes `check()` once
   per (station, model, parameter). No cross-parameter aggregation is needed on a single
   `OperationalForecast`.

   **Per-parameter fallback dispatch:** Since each (station, model, parameter) pair
   receives its own `QcStatus`, the fallback question is per-parameter: if discharge is
   `QC_FAILED` and water_level is `QC_PASSED` for the same (station, model), does the
   flow trigger fallback for the failing parameter only, or does any parameter failure
   trigger fallback for the entire model assignment? Per-parameter fallback is the
   natural reading (each `OperationalForecast` is independent), but the flow must handle
   the case where a fallback model produces only one parameter — confirm whether partial
   parameter coverage from a fallback model is acceptable. A third option — store the
   `QC_FAILED` result without fallback and let alert suppression (task 5) handle it — is
   also viable; see task 3 option (d) for the same approach in the group-model case.

   **Overrides lifecycle:** `forecast_qc_overrides` stores per-station QC threshold
   overrides (`StationForecastQcOverride`). Confirm who creates and maintains these
   records: are they set during Flow 5 station onboarding, by a model admin through the
   API, or by a configuration import? If onboarding does not populate them, the batch
   pre-fetch at flow start will always return empty lists and `check()` will silently use
   only the default `ForecastQcRuleSet` thresholds — which may produce systematic false
   positives for stations with unusual hydrological regimes.

3. **Validate fallback behavior on QC failure.** `conventions.md` line 232 prescribes
   "try fallback model" for `SanityCheckFailure`, and `types-and-protocols.md` line 585
   already specifies that `QC_FAILED` raises `SanityCheckFailure` and the flow tries a
   fallback model. The investigation task is to **validate this prescription** — not
   re-derive it — and resolve the open questions it leaves unanswered:
   - **Is `SanityCheckFailure` the right signal for plausibility failure?** The current
     spec conflates two failure modes: runtime model failure (model crashes) and
     plausibility failure (model runs, output is implausible). Both map to
     `SanityCheckFailure` → "try fallback model." Confirm whether a single exception type
     is correct, or whether a distinct exception (e.g. `QcFailure`) would give the flow
     layer cleaner branching. Note: `check()` returns `list[QcFlag]` (data); the spec
     places exception-raising responsibility at the **flow layer** after calling
     `aggregate_qc_status()` (`types/domain.py`; spec: `types-and-protocols.md`
     line 404) — so the service boundary is already clean. The question is
     whether the flow layer should raise or branch on the aggregated status.
   - **Exhausted fallbacks:** What happens when all assigned models fail QC for a
     station? (Skip station, store with `QC_FAILED` flag, or raise to pipeline
     monitoring?)
   - **GroupForecastModel QC and fallback:** `GroupForecastModel.predict_batch()` returns
     results for all stations in a group in a single call. If QC runs on the resulting
     ensembles and one station's output fails, the fallback path differs from the
     station-scoped case: (a) apply a station-scoped fallback model for the failing
     station only (mixing group and station-scoped outputs in the same cycle);
     (b) skip the failing station and store with `QC_FAILED` without fallback;
     (c) re-run the entire group model excluding the failing station (only if the group
     model supports partial groups — not specified in the current Protocol);
     (d) store the QC-failed result with `QC_FAILED` status and let alert suppression
     (task 5) filter it from Phase C — same treatment the spec already prescribes for
     hindcasts. This is the simplest option and avoids mixing group and station-scoped
     outputs.
     Decide which behavior is correct. This is distinct from a model *runtime* failure inside
     `predict_batch()`, which currently has no fallback specification either
     (`architecture-context.md` step 1.8: "detail in future iteration" — note: that
     phrase covers all model failure, not just group-model batch failures).

4. **Design hindcast QC integration (Flow 7).** `types-and-protocols.md` line 585
   already says: "For hindcasts, `QC_FAILED` flags the hindcast but does not trigger
   fallback." The `HindcastForecast` type and DB schema already have `qc_status` and
   `qc_flags` fields — currently inert (always `RAW`). **Dependency:** if hindcast QC
   is applied, Flow 8 skill computation (`flows/compute_skills.py`) must filter or flag
   QC-failed hindcasts. Currently it filters observations by `qc_status` but not
   hindcasts. Decide whether to (a) exclude QC-failed hindcasts from skill computation,
   (b) include them but flag the skill report, or (c) defer hindcast QC entirely.

   **Timing consideration:** QC thresholds — particularly `climatology_outlier` and
   `range_check` — benefit from calibration against operational experience. Hindcasts
   are generated (Flow 7) before operational forecasting (Flow 1) accumulates that
   experience. `ClimBaseline` records may not yet exist when hindcasts are generated
   for a newly onboarded station; `check()` skips rules that depend on missing baselines,
   but if baselines are added later and hindcast QC is applied retroactively, the same
   hindcasts could receive different QC outcomes depending on evaluation time. Option (c)
   — defer hindcast QC to after operational baselines stabilize — is the conservative v0
   default. If option (a) or (b) is chosen, specify whether hindcast QC runs at
   generation time (with whatever baselines exist) or retroactively (with mature
   baselines), and whether re-evaluation is ever triggered.

5. **Design alert suppression for QC-failed forecasts.** QC-failed forecasts should not
   trigger alerts. **Type-level gap:** `alert_checker.py` receives
   `dict[StationId, dict[ModelId, dict[str, ForecastEnsemble]]]` — raw ensembles with
   no `qc_status` field. Two options:
   - (a) Filter QC-failed forecasts out of the ensemble dict before Phase C — simpler,
     consistent with "don't alert on implausible forecasts."
   - (b) Pass `qc_status` alongside ensembles into the alert checker.
   Option (a) is recommended unless there's a reason to alert on suspect forecasts.

   Note: if option (a) is adopted, the same filter must apply in the Flow 3 re-trigger
   path (`architecture-context.md` line 366, steps 3.5–3.7). Flow 3 re-runs steps
   1.11–1.13 on published forecasts; QC-failed forecasts must be excluded there as well,
   or they can surface as alerts at publication time. The task 6 documentation update must
   specify this filter in the Flow 3 notes.

   **All-models-fail case:** If all assigned models for a station fail QC in the same
   cycle, option (a) removes the station from Phase C entirely — no flood alert is
   generated, which is correct, but also no signal that the station was evaluated and
   suppressed. This is a silent operational failure. Decide whether total QC suppression
   for a station should trigger a **pipeline monitoring event** (logged and surfaced via
   Flow 4 / `pipeline_health` when implemented, immediately visible in structured logs).
   This is distinct from a flood alert — it notifies the operations team that a station
   produced no plausible forecast this cycle.

6. **Document the integration step.** This task produces decision records; document
   edits are deferred to implementation once tasks 1–5 are resolved. Targets:

   **`architecture-context.md`** — seven locations require updates:
   - Step table (lines 81–93): add the new QC step row.
   - Step notes (line 110 area): add a note for the new step.
   - Line 95: "Steps 1.2, 1.3, 1.4, and 1.9 are conditional" — update to include or
     exclude the QC step depending on whether it is always-on or skippable.
   - Line 154: "On raw forecasts (immediately after 1.9)" — update to reference the new
     step number if QC is inserted between 1.9 and Phase C.
   - Lines 168–169: Phase B sequencing (`1.7 → 1.8 → [1.9] → 1.10`) — insert QC step
     in the correct position.
   - Mermaid diagram (lines 177–218): add node for the new step.
   - Flow 3, line 366 (steps 3.5–3.7): add sentence specifying that QC-failed forecasts
     are filtered before the re-trigger, consistent with the alert suppression design
     from task 5. Also fix stale cross-reference: "see Flow 1 open decision" → the
     decision is resolved at line 151; update the pointer accordingly.
   - Line 998 (Flow 7/H.2): fix the same class of stale cross-reference — "same open
     decision as Flow 1 step 1.7" refers to the ML lookback forcing source, which is
     resolved at line 126.

   **`orchestration.md`** — the Flow 1 fan-out sketch (lines 59–88) shows the Prefect
   task structure but is currently step-agnostic (no step numbers). Add the QC step
   invocation and specify where `overrides`/`baselines` are passed (batch pre-fetched
   per the task 2 recommendation).

   **`v0-scope.md` §C**: update the table count (currently 23) to include
   `forecast_qc_overrides` (created by migration 0012). Annotate the `forecasts` and
   `hindcast_forecasts` entries with the `qc_status` and `qc_flags` columns added by the
   same migration.

   **`v0-scope.md` §D6**: add a per-step time budget for QC (expect sub-second —
   pure in-memory on already-fetched data, but the table must be complete for
   performance planning).

7. **Define forecast QC logging events.** This task produces a decision record; the
   `logging.md` edit is deferred to implementation. `docs/standards/logging.md` (line
   215) defines `observation.qc_passed`, `observation.qc_failed`, `observation.qc_suspect`
   in the event table but has no `forecast.qc_*` counterparts. The decision: add
   `forecast.qc_passed`, `forecast.qc_failed`, `forecast.qc_suspect` to the event table.

   `logging.md` line 208 states: "Developers create new event names following this
   pattern. No need to update this document." That policy is unconditional as written —
   it does not distinguish ad-hoc events from core-subsystem events. However, the event
   table already includes `observation.qc_*` events (line 215, alongside `ingested`),
   functioning as a de facto canonicalization reference for the observation QC subsystem.
   Forecast QC mirrors this structure. The implementation edit to `logging.md` should:
   (a) add `forecast.qc_passed`, `forecast.qc_failed`, `forecast.qc_suspect` to the
   forecast row, and (b) update the policy note at line 208 to acknowledge that
   core-subsystem events (observation QC, forecast QC) are canonicalized in the table
   for discoverability — resolving the tension between the unconditional "no need to
   update" wording and the fact that the table already contains subsystem-specific
   entries.

8. **Resolve v0 scope for forecast QC.** Blocked by task 1. `conventions.md` line 410
   scopes forecast QC rule IDs as `v0+v1`, indicating QC is in scope for v0. But
   `v0-scope.md` line 25 declares step 1.9 pass-through. Resolution depends on task 1's
   outcome:
   - **If task 1 assigns QC a new step number** (e.g. 1.8a): the pass-through
     declaration applies to step 1.9 (bias correction) only, not to the new QC step.
     Update `v0-scope.md` line 25 to name both steps explicitly and confirm forecast QC
     is active in v0.
   - **If task 1 redefines step 1.9** (QC replaces or shares the 1.9 designation): the
     `v0-scope.md` line 25 "1.9 pass-through" declaration must be narrowed or removed —
     it cannot suppress QC if QC is mapped to 1.9. Update line 25 to distinguish the
     pass-through component (bias correction) from the active component (QC) within
     step 1.9.

   In either case, also update the conditional-steps sentence in
   `architecture-context.md` line 95 to reflect the QC step's v0 status (see task 6).

## Decisions

### Task 1: Step numbering — new step 1.10

QC becomes **step 1.10** (Forecast QC), inserted between 1.9 (bias correction) and
the former 1.10 (store). Steps renumbered:

| Old | New | Step |
|-----|-----|------|
| — | **1.10** | **Forecast QC** (new) |
| 1.10 | 1.11 | Store forecast results |
| 1.11 | 1.12 | Check alert thresholds |
| 1.12 | 1.13 | Raise / resolve alerts |
| 1.13 | 1.14 | Notify |

Step 1.10 is **always-on** (not conditional). The "Steps 1.2, 1.3, 1.4, and 1.9 are
conditional" sentence in `architecture-context.md` is unchanged — QC is not conditional.

Phase B becomes: `1.7 → 1.8 → [1.9] → 1.10 → 1.11`
Phase C becomes: `[1.12] → [1.13] → [1.14]`

Rationale: Giving QC its own step number (not 1.9) decouples it from the "1.9
pass-through" declaration in `v0-scope.md` line 25. QC is active in v0 (per
`conventions.md` line 410: forecast QC rule IDs scoped `v0+v1`).

### Task 2: Insertion point — after bias correction (step 1.9)

QC runs after bias correction to catch both model bugs and correction bugs. In v0,
step 1.9 is pass-through, so QC runs on raw model output regardless — the ordering
only matters for v1 when bias correction becomes active.

**Data dependencies (resolved):**
- `rule_set`: Pre-loaded at flow start. Loader fix: add `SAPPHIRE_CONFIG=/app/config.toml`
  to `docker-compose.yml` `prefect-worker` environment (the file is already mounted at
  that path). Both `load_forecast_qc_rules()` and `load_qc_rules()` then work without
  wrappers — falling back to Swiss defaults when the TOML section is absent.
- `overrides`: Batch pre-fetched from `forecast_qc_overrides` table at flow start
  (same pattern as observation fetch in 1.6).
- `baselines`: Batch pre-fetched from `ClimBaseline` store at flow start.
- `ensemble`: Available from step 1.8 (or 1.9 if bias correction is active).

**Per-parameter invocation:** `check()` invoked once per (station, model, parameter).
No cross-parameter aggregation needed — each `OperationalForecast` is independent.

**Per-parameter fallback:** Fallback is per-parameter. If discharge is `QC_FAILED` and
water_level is `QC_PASSED` for the same (station, model), only the failing parameter
triggers fallback for that model. Partial parameter coverage from a fallback model is
acceptable — the station may have discharge from the fallback model and water_level
from the primary model.

**Overrides lifecycle:** Created during station onboarding (Flow 5) or via the API.
Initially empty for most stations — `check()` uses only the default `ForecastQcRuleSet`
thresholds. Stations with unusual hydrological regimes should have overrides configured
before forecast QC goes live.

### Task 3: Fallback behavior — SanityCheckFailure confirmed

`SanityCheckFailure` is the correct signal for QC failure. Rationale:
- Three error types already map to "try fallback model" (`SanityCheckFailure`,
  `InsufficientDataError`, `ModelLoadError`). The fallback dispatch at the flow layer
  catches `SapphireError` subclasses uniformly. A distinct `QcFailure` would not
  provide cleaner branching.
- `SanityCheckFailure`'s docstring ("Model output failed plausibility checks") is
  exactly what QC failure means.
- The service boundary is clean: `check()` returns `list[QcFlag]` (data); the flow
  layer calls `aggregate_qc_status()` and raises `SanityCheckFailure` if `QC_FAILED`.
  No exception crosses the service boundary.

**Exhausted fallbacks:** Store the last model's result with `QC_FAILED` status. Log
`forecast.all_models_qc_failed` at WARNING level with `station_id`, `cycle_time`,
`model_ids`. The `QC_FAILED` forecast is filtered from Phase C (task 5 decision) —
no alert generated.

**GroupForecastModel:** Option (d) — store QC-failed results with `QC_FAILED` status,
let alert suppression handle. Rationale:
- Simplest option; avoids mixing group and station-scoped outputs in the same cycle.
- Consistent with the spec's hindcast treatment (flag, don't trigger fallback).
- Group models produce batch predictions via `predict_batch()`; per-station fallback
  within a batch adds complexity without clear benefit. Individual station failures
  within a group batch are likely data issues, not model issues.
- Station-scoped model QC failure follows normal fallback (try next model by priority).

### Task 4: Hindcast QC — deferred (option c)

Defer hindcast QC to post-v0. Rationale:
- QC thresholds (especially `climatology_outlier` and `range_check`) benefit from
  operational calibration against real forecast experience.
- `ClimBaseline` records may not exist for newly onboarded stations when hindcasts are
  generated (Flow 7 runs before Flow 1 accumulates operational data).
- `check()` skips rules that depend on missing baselines, but partial QC evaluation
  produces inconsistent outcomes — the same hindcasts could receive different QC
  results depending on when they are evaluated.
- `compute_skills.py` currently does not filter hindcasts by `qc_status` — this is
  correct for v0 (all hindcasts contribute to skill computation).
- `qc_status` and `qc_flags` fields on `HindcastForecast` remain `RAW`/empty until
  hindcast QC is implemented.

When hindcast QC is implemented (post-v0):
- Run at hindcast generation time with available baselines.
- Flag hindcasts but do not trigger fallback (per spec).
- Exclude `QC_FAILED` hindcasts from skill computation (option a of the original task).
- Re-evaluation not needed in v0; consider for v1.

### Task 5: Alert suppression — filter before Phase C (option a)

Filter QC-failed forecasts out of the `all_ensembles` dict at the flow layer before
calling `check_station_alerts()`. The filter removes entries where the corresponding
`OperationalForecast.qc_status == QC_FAILED`. `QC_SUSPECT` forecasts pass through
(they are stored and may trigger alerts — the QC flag is informational).

Same filter applies in the Flow 3 re-trigger path (steps 3.5–3.7) — QC-failed
forecasts must not surface as alerts at publication time.

**All-models-fail case:** If all models for a station fail QC in the same cycle, the
station is removed from Phase C entirely (no flood alert generated). Log
`forecast.station_all_qc_failed` at WARNING level with `station_id`, `cycle_time`,
`model_ids`, `qc_statuses`. This is a pipeline monitoring event — distinct from a
flood alert. Visible in structured logs immediately; surfaced via
Flow 4 / `pipeline_health` when implemented.

### Task 6: Documentation — edits applied

All document edits applied alongside these decisions (not deferred). Targets:

**`architecture-context.md`** — step table (add 1.10, renumber 1.11–1.14), step notes
(add 1.10 note, renumber), Phase B/C sequencing, Mermaid diagram, line 154 (update
anchor from 1.9 to 1.10), multi-model alert strategy (renumber), Flow 3 line 366
(renumber + fix stale cross-reference), line 998 (fix stale cross-reference).

**`types-and-protocols.md`** — Flow 1 integration note: step 1.9 → step 1.10.

**`v0-scope.md`** — line 25 (distinguish 1.9 pass-through from 1.10 active), §A8b/§A8c
(renumber 1.11–1.13 → 1.12–1.14), §C (table count 23→24, annotate `qc_status`/`qc_flags`
columns), §D6 (add QC row, renumber alert row).

**`orchestration.md`** — add QC invocation to Flow 1 fan-out sketch.

**`logging.md`** — add `forecast.qc_passed`, `forecast.qc_failed`, `forecast.qc_suspect`
to event table; update line 208 policy note.

### Task 7: Logging events — three new events

Add `qc_passed`, `qc_failed`, `qc_suspect` to the `forecast` row in the `logging.md`
event table (alongside existing `run_started`, `run_completed`, `stored`).

Update the policy note at line 208: "Developers create new event names following this
pattern. Core-subsystem events (observation QC, forecast QC, alerting) are
canonicalized in the table below for discoverability."

### Task 8: v0 scope — QC active in v0

Step 1.10 (Forecast QC) is active in v0. Step 1.9 (forecast output bias correction)
remains pass-through. The `v0-scope.md` line 25 pass-through declaration applies to
step 1.9 only. Confirmed by `conventions.md` line 410: forecast QC rule IDs scoped
`v0+v1`.

## Urgency

Design must be complete before Phase 8 (Flow 1 implementation) begins, so the flow
structure accommodates the QC step. QC implementation can follow Phase 8 scaffolding.

## Origin

Extracted from plan 011 §C. Revised 2026-03-31 after cross-document consistency review.
Reviewed 2026-03-31: all factual claims verified against source documents. Added data
dependencies for QC invocation (task 2), exception layering clarification (task 3),
precise alert checker input type (task 5), orchestration.md + performance budget scope
(task 6), logging.md convention justification (task 7).

Revised 2026-03-31 (critical review): fixed Protocol line number (568, not 567); added
explicit task dependency graph; removed "moot" framing in task 2, replaced with factual
statement that v0 always runs QC on raw output; added per-parameter invocation and
overrides lifecycle sub-questions to task 2; reframed task 3 as spec validation, added
GroupForecastModel batch fallback problem; added three missing architecture-context.md
update targets (lines 95, 154, 366) to tasks 1, 5, and 6; acknowledged logging.md
policy override explicitly in task 7; clarified tasks 6 and 7 as decision-record tasks
with edits deferred to implementation; added blocked-by note to task 8.

Revised 2026-03-31c (critical review): fixed OperationalForecast false attribute claim
— parameter lives on `ensemble.parameter`, not directly on OperationalForecast
(denormalized on DB table); added loader caveat to task 2 — `load_forecast_qc_rules()`
raises ValueError when SAPPHIRE_CONFIG unset, unlike observation QC loader; reframed
task 7 logging argument — dropped invented "ad-hoc" policy characterization, proposed
updating the unconditional line 208 policy to acknowledge core-subsystem
canonicalization; tightened Phase B sequencing citation from lines 166–173 to 168–169;
added two stale cross-reference fixes to task 6 (architecture-context.md lines 366 and
998 both say "open decision" for resolved decisions); added `observation.ingested`
omission note to task 7 event table citation.

Revised 2026-03-31b (cross-document verification review): fixed per-parameter
aggregation false premise in task 2 — `OperationalForecast` is per-(station, model,
parameter), no cross-parameter aggregation exists; replaced with per-parameter fallback
dispatch question. Added all-models-fail pipeline alert question to task 5. Added
v0-scope.md §C (table count, qc columns) to task 6 update targets. Fixed task 8 to
cover both task 1 outcomes (new step vs redefined 1.9). Added hindcast QC timing
chicken-and-egg note to task 4 (baseline availability at generation time). Added fourth
GroupForecastModel fallback option (d) to task 3 — store with QC_FAILED, let alert
suppression handle. Fixed Mermaid diagram line range (177–218, not 197–218). Clarified
orchestration.md sketch is step-agnostic. Noted step 1.8 "detail in future iteration"
covers all model failure.

Revised 2026-03-31d (critical review): fixed loader caveat false asymmetry — both
`load_forecast_qc_rules()` and `load_qc_rules()` raise `ValueError` when
`SAPPHIRE_CONFIG` is unset; both fall back to Swiss defaults only when the config
file exists but lacks their respective TOML section; reframed option (b) justification
to reference `onboard.py` wrapper pattern rather than a non-existent loader difference.
Added return type coercion note to task 2 (`check()` returns `list[QcFlag]`,
forecast types store `tuple[QcFlag, ...]`). Added `aggregate_qc_status()` location
cite to task 3 (`types/domain.py`, `types-and-protocols.md` line 404). Noted
`docker-compose.yml` mounts `config.toml` at `/app/config.toml` despite not setting
`SAPPHIRE_CONFIG`.

Revised 2026-03-31e (implementation): all 8 investigation tasks resolved. Decisions
section added. Document updates applied to `architecture-context.md` (step 1.10
inserted, steps renumbered 1.11–1.14, Mermaid diagram updated, Phase B/C sequences
updated, two stale cross-references fixed), `types-and-protocols.md` (Flow 1
integration note updated to step 1.10, `ExceedanceResult` and `DeploymentConfig`
step references renumbered), `v0-scope.md` (step references updated, §C table count
24, `forecast_qc_overrides` added, `qc_status`/`qc_flags` columns annotated, §D6
QC budget row added), `logging.md` (forecast QC events added, policy note updated),
`orchestration.md` (QC filtering block added to fan-out sketch),
`docker-compose.yml` (`SAPPHIRE_CONFIG` added to prefect-worker).
