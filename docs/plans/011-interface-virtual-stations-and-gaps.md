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

## Dependencies

| Area | Blocks / Blocked by |
|------|---------------------|
| A (ForecastInterface) | Informs model adapter design (Phase 5–6) |
| B (Virtual stations) | Informs Flow 5 redesign, station type system |
| C (Forecast QC) | Blocks Flow 1 implementation (Phase 8) |
| D (Weather mapping) | Informational — verify existing design |
| E (Remote training) | Informational — existing design may need promotion to architecture-context |

## Next Steps

1. **Investigate A** — Detailed type-by-type comparison, propose adapter layer or
   ForecastInterface changes.
2. **Investigate C** — Confirm the gap, propose Flow 1 step insertion.
3. **Design B** — Station type taxonomy, calculated station formula spec.
4. **Verify D** — Read the intersection logic, confirm correctness.
5. **Verify E** — Decide if architecture-context.md needs a remote training section.
