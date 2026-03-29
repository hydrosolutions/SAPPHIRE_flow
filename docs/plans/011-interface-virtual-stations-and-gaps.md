---
status: DRAFT
created: 2026-03-27
scope: investigation + design — ForecastInterface alignment, virtual stations, forecast QC integration, weather source mapping, paid services
depends_on: []
---

# 011 — ForecastInterface Alignment, Virtual Stations, and Architectural Gaps

## Context

This plan collects several loosely related investigation and design items that need
resolution before progressing deeper into implementation. Some are architectural gaps
discovered during review; others are new capability requirements (virtual stations)
or alignment tasks with the external `ForecastInterface` contract package.

---

## Investigation Areas

### A — ForecastInterface Contract Alignment

**What:** Review the `hydrosolutions/ForecastInterface` package (local:
`~/Documents/GitHub/ForecastInterface`) and assess consistency with SAPPHIRE Flow's
internal types and protocols.

**ForecastInterface summary (as of 2026-03-27):**

- Pydantic + Polars package defining contracts between an operational forecast system
  and ML model developers.
- **Output contract** (fully implemented):
  - `ModelOutput` → top-level container: `model_name`, `issue_datetime`,
    `variables: dict[str, VariableOutput]`, computed `success` property.
  - `VariableOutput` → per-variable: `metadata`, `deterministic`, `quantiles`,
    `trajectories`, `epistemic_uncertainty`, `flags`, `status`, `trusted`.
  - Data containers (`DeterministicData`, `QuantileData`, `TrajectoryData`,
    `EpistemicUncertaintyData`) wrap Polars DataFrames with validated schemas.
    All require `issue_datetime` + `datetime` temporal columns.
  - `VariableMetadata` → `name`, `unit` (Unit enum), `resolution` (Resolution enum),
    `timedelta`, `forecast_horizon`, `offset`.
  - `ForecastFlag` enum: `HIGH_EPISTEMIC_UNCERTAINTY`, `DATA_AVAILABILITY`.
  - `VariableStatus` enum: `SUCCESS`, `FAILURE`, `PARTIAL`.
- **Input contract** (documented in `docs/input_requirement.md`, not yet implemented):
  - Hierarchical: temporal resolution → spatial → temporality → product → variable → properties.
  - Properties: `lookback`, `future_steps`, `max_nan`, `ensemble`.
- **Interface module** (`forecast_interface/interface/`): placeholder, not yet implemented.

**Investigation tasks:**

1. **Type mapping** — Compare SAPPHIRE Flow's `ForecastEnsemble` / `OperationalForecast`
   types with ForecastInterface's `ModelOutput` / `VariableOutput`. Identify:
   - Semantic overlaps and naming divergences.
   - Whether SAPPHIRE Flow's `predict()` return type can be constructed from or converted
     to `ModelOutput` at the boundary.
   - Gaps: fields present in one but not the other (e.g. `epistemic_uncertainty`,
     `ForecastFlag`, `offset`).

2. **Enum alignment** — Compare `Unit`, `Resolution`, `VariableStatus` enums with
   SAPPHIRE Flow equivalents (`Parameter`, `QcStatus`, time-step handling).

3. **DataFrame contract** — SAPPHIRE Flow uses xarray internally for ensemble data;
   ForecastInterface uses Polars DataFrames. Define the conversion boundary — where
   does the format switch happen? The natural boundary is the model adapter.

4. **Input contract** — ForecastInterface's input spec describes what a model needs
   (lookback, future_steps, etc.). Compare with SAPPHIRE Flow's `ModelInputs` /
   `prepare_model_inputs()` protocol. Determine if the input contract should drive
   SAPPHIRE Flow's input preparation or vice versa.

5. **Proposal direction** — Where ForecastInterface and SAPPHIRE Flow diverge, decide
   which should adapt. Criteria: ForecastInterface is the public contract for external
   model developers; SAPPHIRE Flow is the operational system. Changes to
   ForecastInterface should be proposed when they improve the contract generality.

---

### B — Virtual Station Support

**What:** SAPPHIRE Flow currently defers virtual stations to v2.0
(`architecture-context.md`). This section scopes the design needed to bring them
forward.

**Two kinds of virtual stations:**

1. **Ungauged sites** — No observations exist. A location on a river where forecasts
   are desired but no gauge is installed. The model runs on NWP forcing and basin
   characteristics alone (regionalized parameters or ML transfer learning).

2. **Calculated stations** — Derived from gauged tributaries. Typical example: reservoir
   inflow = weighted sum of upstream gauged tributaries. Common in Central Asia.
   Formula: `Q_virtual = Σ(wᵢ × Qᵢ)` where `Qᵢ` are observed/forecast flows.

**Design questions to resolve:**

1. **Station type enum** — Extend `StationType` (or create new) to distinguish
   `GAUGED`, `UNGAUGED`, `CALCULATED`. Affects which flows apply (e.g. calculated
   stations skip model prediction, ungauged stations skip observation QC).

2. **Calculated station formula** — How to represent the aggregation formula.
   Options: (a) config-driven weighted sum, (b) expression DSL, (c) Python callable
   registered per station. Weighted sum covers 90%+ of cases.

3. **Observation handling** — Ungauged stations have no observations → no observation
   QC, no skill scores against observations, no rating curves. Calculated stations
   have "observations" derived from component stations → need propagated QC flags.

4. **Model assignment** — Ungauged stations still need forecast models (regionalized).
   Calculated stations may not need a forecast model if they're purely derived from
   component forecasts.

5. **Basin delineation** — Virtual stations need basin outlines for NWP extraction.
   Options:
   - **HydroSHEDS API** (paid) — pre-computed basin outlines worldwide. Could serve as
     quality-check for user-uploaded outlines.
   - **User upload** — allow uploading custom basin outlines (GeoJSON/Shapefile).
   - Both paths should be supported. HydroSHEDS integration could be a paid add-on.

6. **Onboarding flow impact** — Flow 5 (station onboarding) needs virtual station
   branches. Flow 0 (deployment onboarding) / organization onboarding could integrate
   HydroSHEDS for basin delineation.

---

### C — Forecast QC Integration Gap

**What:** The `ForecastOutputQualityChecker` service is fully implemented
(`services/forecast_qc.py`) with 7 rules (negative_value, range_check, flat_ensemble,
ensemble_spread, climatology_outlier, temporal_consistency, quantile_crossing). Types
are complete (`ForecastQcRuleSet`, `QcFlag`, `QcStatus`). DB schema supports it.

**The gap:** There is **no step in Flow 1** (operational forecast) that invokes the
checker. The architecture shows: model output (1.8) → post-process (1.9) → store (1.10)
→ alert thresholds (1.11). Forecast plausibility checking is missing between model
output and storage/alerting.

**Investigation tasks:**

1. Confirm the service is not called anywhere in the operational flow code.
2. Determine the correct insertion point in Flow 1 — likely between steps 1.8 and 1.9
   (or between 1.9 and 1.10 if post-processing should happen before QC).
3. Design the fallback behavior when `SanityCheckFailure` is raised — the exception
   is defined but never caught. Architecture mentions "try fallback model" but this
   isn't implemented.
4. Decide whether hindcast flow (Flow 7) should also apply forecast QC (for
   consistency / flag propagation to skill scores).
5. Check if QC-failed forecasts should suppress alerts (likely yes — don't alert on
   implausible forecasts).
6. Document the integration in `architecture-context.md` as an explicit step.

---

### D — Weather Source Mapping: Station Attribute vs. Model Configuration

**What:** Weather source mapping is fully modeled as a station attribute
(`StationWeatherSource` in `types/station.py`, `station_weather_sources` table). But
the model config (`config.toml`) also specifies NWP sources per model. Need to verify
these two don't conflict and the data flow is clear.

**Current design:**
- **Station level** (`station_weather_sources` table): which NWP sources a station
  can use + extraction type (point/basin_average/elevation_band).
- **Model level** (`config.toml [models.*.weather]`): which NWP sources a model
  expects + parameters + post-processing pipeline.
- **Resolution**: at runtime, the intersection determines what's extracted — only
  sources that both the station and model require.

**Investigation tasks:**

1. Verify the intersection logic is documented and implemented correctly.
2. Check edge case: what happens when a model requires a source that's not mapped to
   the station? Should this be a hard error at onboarding validation time?
3. Confirm that weather source mapping is indeed a station concern (physical location
   determines available NWP coverage) not a model concern (model just declares what
   it needs). Current design seems correct.
4. Document the relationship clearly in architecture-context.md if not already explicit.

---

### E — Remote Training for Large ML Models

**What:** Large models (LSTM, transformer-based) may require GPU resources not
available on the default Docker Compose worker. The design for remote/GPU training
exists in `docs/design/v0-flow13-model-onboarding.md` §9 but is barely surfaced in
`architecture-context.md` (only a passing mention of "work pool separation" in the
CI/CD standards summary, line ~2845).

**Existing design (v0-flow13 §9):**
- v1: `ModelDataRequirements` gains `compute_backend: Literal["cpu", "gpu"]`. Prefect
  routes to a `gpu_training` work pool via `with_options(work_pool_name=...)`.
- v0: all training on CPU `default` pool; GPU routing annotated but not active.
- No Protocol changes — routing stays in the flow layer.

**Investigation tasks:**

1. Decide whether `architecture-context.md` should promote remote training to a
   first-class architectural concern (dedicated subsection) or if the flow-13 design
   doc is sufficient.
2. If promoted: document the work pool topology, GPU pool provisioning (cloud provider
   agnostic), artifact transfer between pools, and failure/retry semantics.
3. Consider whether remote training should be offered as an optional paid service
   (see `docs/business/paid-services.md`) — this affects whether the feature is
   behind a deployment-level toggle.

---

### F — Selective Hindcast Recomputation (v1)

**What:** When individual hindcast steps fail (recorded as `HindcastStepResult(success=False)`),
the only recovery path is re-running the entire Flow 13 onboarding for that model/station
unit — which re-runs the full hindcast. v0 starts with a smaller station set but scales
to ~1000 stations with sub-daily data for planned large-scale experiments. At that scale,
full-period re-runs for individual step failures are prohibitively expensive.

**Design tasks:**

1. **Flow 7 selective replay** — Extend `run_{station|group}_hindcast()` to accept an
   optional set of timestamps to replay, instead of always running the full period.
   Only re-run steps where `success=False`.
2. **Flow 10 gap-fill mode** — Flow 10 (skill recomputation, scoped for v1) could
   include a "fill gaps" mode: identify failed hindcast steps, re-run them selectively,
   then recompute skill scores from the updated result set.
3. **Incremental skill update** — When individual steps are re-run, skill scores should
   be updated incrementally rather than recomputed from scratch. Design the merge
   semantics (replace failed step results, recompute affected strata scores).
4. **API/CLI trigger** — Provide a way to trigger selective hindcast re-runs for
   specific (model, station, timestamp) combinations — either via API endpoint or
   CLI command.

---

### G — Update v0 Scale Assumptions (~50 → ~1000 Stations)

**What:** Multiple docs reference "~50 stations" as the v0 scale target. This is outdated —
v0 starts smaller but must scale to ~1000 stations with sub-daily data for planned
large-scale experiments. The "~50 stations" figure drives simplification rationales,
performance budgets, and "acceptable at v0 scale" justifications that need re-evaluation.

**Affected files:**

- `docs/v0-scope.md` — line 9 ("~50 stations, single VM"), line 34 (Flow 4 deferral
  rationale), line 50 (partitioning rationale), lines 248/256/284 (performance targets
  and per-step budgets)
- `docs/architecture-context.md` — line 595 (onboarding timing for "50 stations")
- `docs/spec/database-schema.md` — line 10 ("~50 stations")
- `docs/design/v0-flow2-observation-pipeline.md` — lines 61, 385, 387 (query-time
  aggregation viability at "~50 stations")

**Tasks:**

1. Update all station count references to reflect the scale range (starting smaller,
   scaling to ~1000).
2. Re-evaluate simplification rationales that depend on small scale — particularly:
   - **A1 (no partitioning)**: ~1000 stations × sub-daily × multiple parameters may
     produce significantly more than "a few GB/year." Re-assess whether partitioning
     is still safely deferred.
   - **Flow 4 deferral**: Manual supervision at ~1000 stations is less credible than
     at ~50. Re-assess timeline.
   - **D2 batch write budgets**: 1000 stations × 21 members × 120 timesteps = 2.52M
     rows per forecast cycle (not 126K). Verify COPY performance at this scale.
3. Update performance budgets (§D) for 1000-station target. The 60-second forecast
   cycle target may need revisiting or the budget breakdown needs rebalancing.
4. Check if "single VM" (line 9) still holds at 1000-station scale or if the
   infrastructure section needs updating.

---

### H — Frozen Dataclass Immutable-Container Consistency

**What:** Three QC frozen dataclasses in the spec (`QcRuleParams`, `StationQcOverride`,
`ForecastQcRuleParams`) use `dict[str, float]` fields, which makes instances non-hashable
and allows mutation despite `frozen=True`. Plan 006 (D3) fixes this for `SkillGateResult`
by using `tuple[tuple[str, float], ...]`, but the QC types remain inconsistent.

**Cleanup tasks:**

1. Audit all frozen dataclasses in `types-and-protocols.md` for mutable container fields
   (`list`, `dict`, `set`). Replace with immutable equivalents (`tuple`, `tuple[tuple[...], ...]`,
   `frozenset`) where the type is used as a result or value object.
2. For config-like types that are never hashed or collected in sets, decide whether the
   ergonomic cost of `tuple[tuple[...], ...]` outweighs the purity benefit — document
   the convention either way.
3. Also fix `TrainingResult.hindcast_steps: list[HindcastStepResult]` →
   `tuple[HindcastStepResult, ...]` for consistency with `OnboardingUnitResult.hindcast_steps`
   (plan 006 D4 noted this but deferred it).
4. Codify the resulting convention in `conventions.md` (currently unwritten).

---

## Dependencies

| Area | Blocks / Blocked by |
|------|---------------------|
| A (ForecastInterface) | Informs model adapter design (Phase 5–6) |
| B (Virtual stations) | Informs Flow 5 redesign, station type system |
| C (Forecast QC) | Blocks Flow 1 implementation (Phase 8) |
| D (Weather mapping) | Informational — verify existing design |
| E (Remote training) | Informational — existing design may need promotion to architecture-context |
| F (Selective hindcast) | v1 — needed before large-scale experiments hit failure-recovery limits |
| G (Scale assumptions) | Foundational — affects performance targets, simplification rationales across all docs |
| H (Frozen dataclass containers) | Low priority — cleanup after plan 006 implementation |

## Next Steps

1. **Investigate A** — Detailed type-by-type comparison, propose adapter layer or
   ForecastInterface changes.
2. **Investigate C** — Confirm the gap, propose Flow 1 step insertion.
3. **Design B** — Station type taxonomy, calculated station formula spec.
4. **Verify D** — Read the intersection logic, confirm correctness.
5. **Verify E** — Decide if architecture-context.md needs a remote training section.
6. **Design F** — Selective hindcast replay, incremental skill update, CLI/API trigger.
7. **Update G** — Sweep all docs for ~50 station references, re-evaluate affected rationales.
8. **Cleanup H** — Audit frozen dataclasses for mutable containers, codify convention.
