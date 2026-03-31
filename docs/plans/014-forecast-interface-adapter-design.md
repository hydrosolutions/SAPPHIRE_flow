---
status: DRAFT
created: 2026-03-30
scope: investigation + design — ForecastInterface adapter layer, weather source mapping verification
depends_on: []  # informs Phase 5-6 (adapters + model framework)
---

# 014 — ForecastInterface Adapter Design + Weather Source Mapping

## A — ForecastInterface Contract Alignment

### Context

Review the `hydrosolutions/ForecastInterface` package (local:
`~/Documents/GitHub/ForecastInterface`) and assess consistency with SAPPHIRE Flow's
internal types and protocols.

**Related:** Plan 008 (GroupModelInputs) — see Open Item 9 for how plan 008's internal
types relate to ForecastInterface's external contract.

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

**Findings from plan 008 review (2026-03-30):**

ForecastInterface and SAPPHIRE Flow's internal types (including plan 008's `GroupModelInputs`)
operate at different layers — they are complementary, not competing:

- **ForecastInterface** = external contract for ML model developers. Describes what data
  a model needs (per-variable requirements with product provenance) and what it returns
  (`ModelOutput`). No concept of station, group, batch, or stacking.
- **SAPPHIRE Flow internals** = how the operational system assembles and delivers data.
  `ModelDataRequirements` (flat feature-name sets) → assembly → `GroupModelInputs` /
  `ModelInputs` (pre-assembled DataFrames ready for inference).

The adapter boundary sits between these layers:

```
ModelDataRequirements → assembly → GroupModelInputs → adapter → FI input → model → ModelOutput → adapter → dict[str, ForecastEnsemble]
     (SAPPHIRE)          (SAPPHIRE)   (SAPPHIRE)      (boundary)  (FI)              (FI)         (boundary)    (SAPPHIRE)
```

Key divergences identified:

| Aspect | ForecastInterface | SAPPHIRE Flow | Adapter responsibility |
|--------|------------------|---------------|----------------------|
| Station identity | Not present | `station_id` in `GroupModelInputs`, `ForecastEnsemble` | Inject on input, extract on output |
| Output format | `ModelOutput` → `VariableOutput` → `DeterministicData`/`QuantileData`/`TrajectoryData` | `dict[str, ForecastEnsemble]` | Convert `ModelOutput` → `dict[str, ForecastEnsemble]` |
| Input granularity | Per-variable with product provenance (`past_known`/`future_known` temporality) | Flat feature-name sets + assembled DataFrames (`past_dynamic`/`future_dynamic`) | Map FI's rich requirements → `ModelDataRequirements`; convert `GroupModelInputs` → FI input format |
| Epistemic uncertainty | First-class (`EpistemicUncertaintyData`) | Not modeled | Gap in SAPPHIRE Flow — consider adding or dropping at boundary |
| Forecast flags | `ForecastFlag` enum (`HIGH_EPISTEMIC_UNCERTAINTY`, `DATA_AVAILABILITY`) | No equivalent on `ForecastEnsemble` | Could map to SAPPHIRE Flow's forecast QC flags |
| Ensemble representation | Separate types: `TrajectoryData` (members), `QuantileData` (quantiles), `DeterministicData` (point) | Single `ForecastEnsemble` with `EnsembleRepresentation` enum (`MEMBERS`/`QUANTILES`) | Convert between representations |

### Investigation Tasks

1. **Output adapter design** (highest priority — FI output types are fully implemented):
   - Design `ModelOutput` → `dict[str, ForecastEnsemble]` conversion.
   - Map `VariableOutput.status` / `flags` to SAPPHIRE Flow's QC / metadata fields.
   - Handle `TrajectoryData` → `ForecastEnsemble(MEMBERS)` and
     `QuantileData` → `ForecastEnsemble(QUANTILES)` conversion.
   - Decide what to do with `DeterministicData` (no SAPPHIRE Flow equivalent —
     wrap as single-member ensemble? Separate field?).
   - Decide what to do with `EpistemicUncertaintyData` (drop at boundary? Add to
     SAPPHIRE Flow types? Store as metadata?).

2. **Enum alignment** — Compare `Unit`, `Resolution`, `VariableStatus` enums with
   SAPPHIRE Flow equivalents (`Parameter`, `QcStatus`, time-step handling). Map or
   convert at the adapter boundary.

3. **Input requirements alignment** (lower priority — FI input types not yet implemented):
   - ForecastInterface's input spec describes requirements per-variable with product
     provenance. SAPPHIRE Flow's `ModelDataRequirements` uses flat `frozenset[str]`
     feature-name sets with a single `lookback_steps` int.
   - When FI implements its input types, `ModelDataRequirements` should be derivable
     from or compatible with FI's richer per-variable spec.
   - The `past_known`/`future_known` temporality axis maps to SAPPHIRE Flow's
     `past_dynamic`/`future_dynamic` split (and plan 008's `GroupModelInputs` fields).
   - Flag as future alignment point, not a current blocker.

4. **Interface module alignment** (deferred — FI interface not yet implemented):
   - When FI implements its `interface/` module (model protocol), SAPPHIRE Flow's
     `StationForecastModel`/`GroupForecastModel` protocols should be implementable
     as thin adapters around FI's interface.
   - Plan 008's `GroupModelInputs` design does not prevent this — the adapter sits
     outside `predict_batch()`.

5. **Proposal direction** — Where ForecastInterface and SAPPHIRE Flow diverge, decide
   which should adapt. Criteria: ForecastInterface is the public contract for external
   model developers; SAPPHIRE Flow is the operational system. Changes to
   ForecastInterface should be proposed when they improve the contract generality.

---

## B — Weather Source Mapping Verification

### Context

Weather source mapping is fully modeled as a station attribute
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

### Verification Tasks

1. Verify the intersection logic is documented and implemented correctly.
2. Check edge case: what happens when a model requires a source that's not mapped to
   the station? Should this be a hard error at onboarding validation time?
3. Confirm that weather source mapping is indeed a station concern (physical location
   determines available NWP coverage) not a model concern (model just declares what
   it needs). Current design seems correct.
4. Document the relationship clearly in architecture-context.md if not already explicit.

## Urgency

Informs Phase 5-6 (adapter and model framework implementation). Not a hard blocker
but should be resolved before adapter code is written.

## Origin

Extracted from plan 011 §A and §D.
