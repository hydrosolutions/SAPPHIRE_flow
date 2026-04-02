---
status: DONE
created: 2026-03-30
completed_tasks: [1, 2]  # output adapter + enum alignment — implemented 2026-04-01
in_flight: [3]           # FI input types PR — instructions prepared, work in FI repo
blocked: [4, 5]          # deferred to v1 — FI interface/ module not yet implemented
scope: |
  §A Tasks 1–3: v0b — output adapter, enum alignment, FI input types PR
  §A Tasks 4–5: deferred to v1 — interface module alignment, proposal direction
  §B: v0a — weather source mapping verification (step 0 documented, code in Phase 5/8)
depends_on: [008, 003, 012]  # all DONE/ARCHIVED — informational traceability
  # 008: GroupModelInputs type definitions (adapter left-hand side)
  # 003: predict_batch() multi-target return type (adapter right-hand side)
  # 012: QcFlag/QcStatus definitions (ForecastFlag mapping target)
---

# 014 — ForecastInterface Adapter Design + Weather Source Mapping

> **v0a / v0b / v1 scoping:**
>
> **Section A, Tasks 1–3 → v0b.** Model development against FI's contract starts now.
> FI's output types are stable and fully tested (3 commits, all validators test-locked).
> The output adapter (Task 1) and enum alignment (Task 2) can be built today. FI's
> input types are documented but unimplemented — SAPPHIRE Flow will **contribute input
> type implementations via PR to ForecastInterface** (Task 3), informed by our assembly
> pipeline experience. This gives the model developer a complete contract: defined input
> shape, defined output shape.
>
> **Section A, Tasks 4–5 → deferred to v1.** FI's `interface/` module is unimplemented
> (Task 4). The governance question of which side adapts (Task 5) is partially resolved
> by the PR approach but the full answer depends on v1 external model onboarding.
>
> **Section B → v0a.** Weather source mapping directly blocks Phase 5 (station
> onboarding) and Phase 8 (forecast cycle).

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
    `trajectories`, `epistemic_uncertainty`, `flags: frozenset[ForecastFlag]`,
    `status`, computed `trusted`. **Validation constraint:** if `status=SUCCESS`,
    at least one of `deterministic`/`quantiles`/`trajectories` must be populated.
  - Data containers wrap Polars DataFrames with validated schemas
    (all require `issue_datetime` + `datetime` temporal columns, all numeric
    columns must be Polars numeric dtypes):
    - `DeterministicData` — additional `value` column.
    - `QuantileData` — additional quantile columns + required `quantile_levels: list[float]`.
    - `TrajectoryData` — additional member columns + required `num_samples: int`.
    - `EpistemicUncertaintyData` — columns: `std`, `range`.
  - `VariableMetadata` → `name`, `unit` (Unit enum), `resolution` (Resolution enum),
    `timedelta`, `forecast_horizon`, `offset`.
  - `ForecastFlag` enum: `HIGH_EPISTEMIC_UNCERTAINTY`, `DATA_AVAILABILITY`.
  - `VariableStatus` enum: `SUCCESS`, `FAILURE`, `PARTIAL`.
  - `Unit` enum: `M3_PER_S`, `MM_PER_DAY`, `MM_PER_S`, `MM`, `CM`, `M`, `DEG_C`, `UNITLESS`.
  - `Resolution` enum: `SUB_HOURLY`, `HOURLY`, `SUB_DAILY`, `DAILY`, `WEEKLY`,
    `MONTHLY`, `SEASONAL`, `ANNUAL`.
  - **Note:** top-level `forecast_interface/__init__.py` does not re-export
    `ForecastFlag` or `EpistemicUncertaintyData` — import from `forecast_interface.output`.
- **Input contract** (documented in `docs/input_requirement.md`, not yet implemented):
  - Hierarchical: temporal resolution → spatial (`distributed`/`lumped`) →
    temporality → product → variable → properties.
  - Properties: `lookback` (past_known only), `future_steps` (future_known only),
    `max_nan` (both), `ensemble` (future_known only).
  - **Static inputs**: separate flat `list[str]` of variable names (not part of hierarchy).
- **Interface module** (`forecast_interface/interface/`): placeholder, not yet implemented.
  - However, `docs/model_interface.md` documents the intended contract: `forecast()`,
    `hindcast()`, `__init__()` methods, all returning `ModelOutput`.
  - **Open in FI's `TODO.md`:** "Differentiate between forecast and hindcast output" —
    `ModelOutput` may change to distinguish these. Relevant because SAPPHIRE Flow has
    separate forecast (Flow 1) and hindcast (Flow 7) pipelines.

**Findings from plan 008 review (2026-03-30):**

ForecastInterface and SAPPHIRE Flow's internal types (including plan 008's `GroupModelInputs`)
operate at different layers — they are complementary, not competing:

- **ForecastInterface** = external contract for ML model developers. Describes what data
  a model needs (per-variable requirements with product provenance) and what it returns
  (`ModelOutput`). No concept of station, group, batch, or stacking.
- **SAPPHIRE Flow internals** = how the operational system assembles and delivers data.
  `ModelDataRequirements` (typed feature-name sets + spatial/temporal constraints) →
  assembly + validation → `GroupModelInputs` / `StationModelInputs` (pre-assembled
  DataFrames ready for inference).

`ModelDataRequirements` fields (types-and-protocols.md, **authoritative**):
- `target_parameters: frozenset[str]` — what parameters the model produces
- `past_dynamic_features: frozenset[str]`
- `future_dynamic_features: frozenset[str]`
- `static_features: frozenset[str]`
- `supported_time_steps: frozenset[timedelta]`
- `lookback_steps: int`
- `spatial_input_type: SpatialRepresentation`

> **Cross-document inconsistency (resolved):** architecture-context.md lines 1423–1433
> previously showed 4 DataFrame slot names instead of the 7 authoritative
> `ModelDataRequirements` fields and used stale naming at line 1660. Both have been
> reconciled to match types-and-protocols.md.

The adapter boundary sits between these layers. **Two paths** exist depending on
`artifact_scope` (architecture-context.md Flow 1 step 1.8):

```
GROUP path:
  ModelDataRequirements → assembly → GroupModelInputs → adapter → FI input
    → model → ModelOutput → adapter → dict[StationId, tuple[dict[str, ForecastEnsemble], bytes | None]]
        (SAPPHIRE)                     (boundary)        (FI)         (boundary)        (SAPPHIRE)

STATION path:
  ModelDataRequirements → assembly → StationModelInputs → adapter → FI input
    → model → ModelOutput → adapter → tuple[dict[str, ForecastEnsemble], bytes | None]
        (SAPPHIRE)                     (boundary)         (FI)        (boundary)       (SAPPHIRE)
```

**Note on state bytes:** `predict()` / `predict_batch()` return `bytes | None` alongside
the forecast dict. This is the model's warm-up state snapshot (used by conceptual models,
`None` for stateless ML models). The adapter must either pass through state bytes
transparently or explicitly declare them unsupported for FI-wrapped models. If dropped,
conceptual models wrapped via FI lose their warm-up state — a design decision to make
explicitly.

Key divergences identified:

| Aspect | ForecastInterface | SAPPHIRE Flow | Adapter responsibility |
|--------|------------------|---------------|----------------------|
| Station identity | Not present | `station_id` in `GroupModelInputs`/`StationModelInputs`, `ForecastEnsemble` | Inject on input, populate on output (see GROUP path limitation below) |
| Output format | `ModelOutput` → `VariableOutput` → `DeterministicData`/`QuantileData`/`TrajectoryData` | `tuple[dict[str, ForecastEnsemble], bytes \| None]` | Convert `ModelOutput` → SAPPHIRE return type (including state bytes) |
| Input granularity | Per-variable with product provenance (`past_known`/`future_known` temporality) + static inputs | `ModelDataRequirements` with `past_dynamic_features`, `future_dynamic_features`, `static_features`, `target_parameters`, `supported_time_steps`, `lookback_steps`, `spatial_input_type` | Map FI's rich requirements → `ModelDataRequirements`; convert `GroupModelInputs`/`StationModelInputs` → FI input format |
| Epistemic uncertainty | First-class (`EpistemicUncertaintyData`: `std`, `range` columns) | Not modeled | **Decision needed (v1):** drop at boundary (loses WMO-1364 sharpness metric data) or add to SAPPHIRE types |
| Forecast flags | `ForecastFlag` enum (`HIGH_EPISTEMIC_UNCERTAINTY`, `DATA_AVAILABILITY`) | `QcFlag` + `QcStatus` (plan 012) — different semantics (observation-QC-flavored `rule_id` system) | New `QcFlag.rule_id` values needed; FI flags don't map 1:1 to existing QC rules |
| Ensemble representation | Separate types: `TrajectoryData` (members), `QuantileData` (quantiles), `DeterministicData` (point) | Single `ForecastEnsemble` with `EnsembleRepresentation` enum (`MEMBERS`/`QUANTILES`) | Convert between representations |
| Parameter names | `VariableMetadata.name` — free-form string | `ForecastEnsemble.parameter: str` (validated against `ForecastParameter = Literal["discharge", "water_level"]` at boundary) | Validate FI variable names against `ForecastParameter` values |
| Units | `Unit` enum (UPPER_CASE: `M3_PER_S`, `MM_PER_DAY`, etc.) | `ForecastEnsemble.units: str` (canonical strings from `parameters` table) | Convert FI `Unit` enum → SAPPHIRE canonical unit strings |
| Temporal fields | `issue_datetime` + `datetime` columns (Polars `pl.Datetime`) | `issued_at: UtcDatetime`, `valid_time` column (point timestamps) | Rename fields, enforce `ensure_utc()` |
| Forecast horizon | `VariableMetadata.forecast_horizon: int` (steps) | `ForecastEnsemble.forecast_horizon_steps: int` | Direct assignment (both int, both steps) |
| Time step / resolution | `VariableMetadata.timedelta: timedelta` (concrete); `Resolution` enum (categorical label) | `ForecastEnsemble.time_step: timedelta` | Assign from `VariableMetadata.timedelta` directly; `Resolution` is informational, not the conversion source |
| Enum casing | UPPER_CASE enum values (`SUCCESS`, `FAILURE`, etc.) | lowercase enum `.value` (conventions.md) | Convert at boundary; never pass FI enum values into SAPPHIRE domain layer |

### Investigation Tasks

#### v0b — implement for model development handoff

1. **Output adapter design** (`ForecastInterfaceAdapter`):
   - Design `ModelOutput` → `tuple[dict[str, ForecastEnsemble], bytes | None]` conversion.
   - Map `VariableOutput.status` / `flags` to SAPPHIRE Flow's QC types (plan 012).
     `VariableStatus.FAILURE`: if **all** `VariableOutput` statuses are `FAILURE`,
     the adapter must **raise `ModelOutputError`** (new `SapphireError` subclass —
     "model produced no usable output") to trigger the flow-level fallback.
     `ModelOutputError` is structurally distinct from `SanityCheckFailure`
     (plausibility, step 1.10) — it signals that the model ran but produced
     nothing convertible. The flow-level fallback catches it via the common
     `SapphireError` base class (plan 012: fallback dispatch catches subclasses
     uniformly). If only some variables fail, the adapter converts the
     successful ones and attaches `QcFlag` entries for the failed variables.
     `PARTIAL` has no direct `QcStatus` equivalent — needs new `QcFlag.rule_id`.
   - **Spec update required:** `ModelOutputError` is a new `SapphireError` subclass
     not yet in `types-and-protocols.md`. Add it to the error hierarchy (direct
     `SapphireError` subclass, parallel to `ModelLoadError`) before implementation.
     Docstring: "Model ran but produced zero convertible ensembles." Handling:
     try fallback model. Semantically distinct from `AdapterError` (which implies
     retry-then-fallback for external data source errors — inappropriate here since
     retrying a model that returned all-FAILURE outputs is pointless) and from
     `SanityCheckFailure` (which fires downstream at step 1.10, after conversion).
     Pipeline sequence: load (`ModelLoadError`) → infer → adapt (`ModelOutputError`)
     → QC (`SanityCheckFailure`).
   - Handle `TrajectoryData` → `ForecastEnsemble.from_members()` and
     `QuantileData` → `ForecastEnsemble.from_quantiles()` conversion.
     Requires passing `QuantileData.quantile_levels` and `TrajectoryData.num_samples`.
     **DataFrame column contract** (types-and-protocols.md): the adapter must
     rename FI's `datetime` column → `valid_time` and reshape FI's member/quantile
     columns into SAPPHIRE's required schema:
     - `from_members()`: `valid_time` (Datetime UTC), `member_id` (Int32), `value` (Float64)
     - `from_quantiles()`: `valid_time` (Datetime UTC), `quantile` (Float64), `value` (Float64)
     These factory classmethods validate the column contract — direct construction
     of `ForecastEnsemble` bypasses validation and must not be used.
   - **Resolved:** `DeterministicData` → single-member `MEMBERS` ensemble.
     Architecture-context.md: "Minimum member count: 1 (single member = deterministic
     forecast)." Will be flagged `insufficient_ensemble_size` and skip threshold
     evaluation. A single member produces a degenerate probability estimate
     (exceedance ∈ {0,1}), acceptable for storage/hindcast but not operational
     alert checking (architecture-context.md lines 1377–1382, types-and-protocols.md
     lines 1011–1014).
   - **Open:** `EpistemicUncertaintyData` — dropping at boundary forecloses
     FI-specific epistemic uncertainty estimates. Options: (a) drop and accept,
     (b) add optional epistemic uncertainty field to `ForecastEnsemble`,
     (c) store as separate metadata. For v0b, option (a) is acceptable — v0's
     sharpness metrics (v0-scope.md §A5, wmo.md §3) are computed from ensemble
     spread — both computable directly from `TrajectoryData`/`QuantileData` without
     `EpistemicUncertaintyData` (see wmo.md §3, WMO-1364 sharpness dimension).
     Revisit if the model developer's models produce epistemic uncertainty estimates.
   - **State bytes:** FI-wrapped ML models are stateless → adapter returns
     `(forecast_dict, None)`. Acceptable for v0b — the initial FI-wrapped models are stateless. If conceptual
     or hybrid models are later wrapped via FI (v1), state bytes must be addressed.
   - **GROUP path limitation (v0b scope: STATION path only):** FI's `ModelOutput`
     has no station-level decomposition — it returns variables for the model as a
     whole. `GroupForecastModel.predict_batch()` must return
     `dict[StationId, tuple[...]]`, requiring per-station results. Without an FI-side
     protocol for station-keyed output, the adapter cannot decompose a single
     `ModelOutput` into per-station results. **For v0b, FI-wrapped models implement
     `StationForecastModel` (STATION path) only.** GROUP-path FI support requires
     either (a) FI adding station-keyed output, or (b) the adapter calling the FI
     model per-station within `predict_batch()` — design deferred to v1 alongside
     Task 4.
   - **Boundary validations** the adapter must enforce:
     - Reject empty `ModelOutput.variables` — raise `ModelOutputError`. FI's
       `ModelOutput.success` returns `True` for empty variables (Python `all()`
       over empty iterable), so the adapter must not rely on the `success`
       property alone. This check is grouped with the all-FAILURE check above:
       both cases produce zero usable ensembles, both raise `ModelOutputError`.
     - Validate `ModelOutput.variables` keys against `ForecastParameter` Literal values
       and `ModelDataRequirements.target_parameters`. Each key becomes
       `ForecastEnsemble.parameter`; also populate `model_id` from the calling context.
     - Apply `ensure_utc()` on `ModelOutput.issue_datetime` → `ForecastEnsemble.issued_at`.
     - Assign `ForecastEnsemble.forecast_horizon_steps` directly from
       `VariableMetadata.forecast_horizon` (both `int`, both steps).
     - Assign `ForecastEnsemble.time_step` directly from `VariableMetadata.timedelta`
       (both `timedelta`). `Resolution` is a categorical label, not the source of
       truth — use it for cross-validation at most, not for conversion.
     - Map `Unit` enum → canonical unit strings from `parameters` table.
     - Temporal columns: the adapter produces `valid_time` point timestamps, not
       time ranges. The half-open `[start, end)` convention (conventions.md) applies
       to store Protocol `fetch_*` methods, not to DataFrame column values.
     - **Not the adapter's responsibility:** Implausible-value checking (`SanityCheckFailure`)
       belongs to `ForecastQualityChecker.check()` at step 1.10, downstream of the adapter.
       The adapter validates structural integrity (correct schema, non-null required fields,
       valid enum values) but does not evaluate plausibility of forecast values.

2. **Enum alignment** — Mapping summary (implement alongside output adapter):
   - `Unit` → canonical unit strings in `parameters` table (not a SAPPHIRE enum yet).
   - `Resolution` — categorical label only. The adapter uses `VariableMetadata.timedelta`
     directly; no `Resolution` → `timedelta` mapping needed. May cross-validate
     `Resolution` against `timedelta` for consistency.
   - `VariableStatus` → `QcStatus` (plan 012): `SUCCESS`→`QC_PASSED`,
     `FAILURE`→`QC_FAILED`, `PARTIAL`→`QC_SUSPECT` + flag (imprecise — needs new rule_id).
     Values stored lowercase per conventions.md.
   - `ForecastFlag` → new `QcFlag.rule_id` values (no existing equivalents).
     Proposed candidates (prefixed `fi_` to distinguish FI-origin flags from native
     forecast QC rules): `fi_partial_output` (for `PARTIAL` status),
     `fi_high_epistemic_uncertainty`, `fi_data_availability`.
   - All FI enums use UPPER_CASE; SAPPHIRE uses lowercase `.value` (conventions.md) —
     convert at boundary, never pass through. See also plan 009 (Parameter extensibility).

3. **Input types — PR to ForecastInterface** (implement FI's input contract):
   SAPPHIRE Flow will implement FI's input types as a PR to the `hydrosolutions/ForecastInterface`
   repo, informed by our assembly pipeline experience. This gives the model developer
   a complete contract from day one: defined input shape in, defined output shape out.

   **What SAPPHIRE Flow contributes:**
   - FI's `docs/input_requirement.md` describes the hierarchical spec but `input/__init__.py`
     is empty. We implement the Pydantic + Polars types that realise that spec.
   - SAPPHIRE Flow's concrete types directly inform the design:

     | FI input spec (abstract) | SAPPHIRE Flow concrete equivalent |
     |---|---|
     | `past_known` / `future_known` temporality | `past_dynamic_features` / `future_dynamic_features` |
     | `distributed` / `lumped` spatial | `spatial_input_type: SpatialRepresentation` |
     | `lookback`, `future_steps` properties | `lookback_steps: int`, forecast horizon from config |
     | Static inputs (flat list) | `static_features: frozenset[str]` from `basins.attributes` |
     | `max_nan`, `ensemble` properties | Derivable from QC config / NWP ensemble config |

   - FI has no equivalent for `target_parameters` or `spatial_input_type` — propose
     adding these to FI's input spec as part of the PR.

   **Adapter implication:** Once FI's input types exist, the SAPPHIRE-side input adapter
   converts `GroupModelInputs`/`StationModelInputs` → FI input types. This adapter
   lives in SAPPHIRE Flow, not in FI.

   **Risk:** FI's input spec may evolve as the model developer starts using it. Accept
   that the PR is a first implementation, not final. Iterate based on feedback.

#### Deferred to v1

4. **Interface module alignment** (FI interface not yet implemented):
   - When FI implements its `interface/` module (model protocol), SAPPHIRE Flow's
     `StationForecastModel`/`GroupForecastModel` protocols should be implementable
     as thin adapters around FI's interface.
   - Plan 008's `GroupModelInputs` design does not prevent this — the adapter sits
     outside `predict_batch()`.
   - **Note:** FI's `docs/model_interface.md` distinguishes `forecast()` vs `hindcast()`
     methods (different `issue_datetime` semantics). SAPPHIRE Flow's separate forecast
     (Flow 1) and hindcast (Flow 7) pipelines align with this distinction. The adapter
     must route to the correct FI method based on flow context.
   - **Risk:** FI's `TODO.md` flags forecast/hindcast output differentiation as
     unresolved — `ModelOutput` may split into two types. Monitor before implementing.

5. **Proposal direction** (partially resolved by Task 3's PR approach):
   For input types, SAPPHIRE Flow proposes via PR to FI — FI maintainers accept/modify.
   For output types, SAPPHIRE Flow adapts (FI's output types are stable and authoritative).
   Remaining governance question for v1: when FI's `interface/` module is implemented,
   who defines the model Protocol — FI (external contract) or SAPPHIRE Flow (operational
   needs)? Likely FI, with SAPPHIRE Flow wrapping via thin adapters.

### Implementation Notes (v0b)

**Prefect task placement:** The adapter conversion sits inside Flow 1's forecast
fan-out (step 1.8). Per orchestration.md §Task granularity, the adapter is pure
computation with no side effects (type conversion only, no I/O) — matching the
inline criterion: "The logic is pure computation with no side effects." The adapter
should be embedded in the `forecast_station` task body, not a separate task. (As a secondary
consideration, at ~170-station fan-out a separate `@task` per adapter call would
also add unnecessary Prefect UI noise.)

**Logging:** Per logging.md, the adapter must:
- Emit `model.prediction_completed` with `duration_ms` (mandatory per logging.md
  for all `*.completed` events). Should include `ensemble_size` as an event-specific
  keyword argument (following logging.md's event naming examples).
- Bind `station_id` via `structlog.contextvars.bind_contextvars()` (not
  `bound_contextvars()` — the adapter lives inside a Prefect task body, so
  the task scope is the clearing boundary).
- Log `VariableStatus.FAILURE`: WARNING for partial failure (some variables
  convert successfully), ERROR when all variables fail (raising `ModelOutputError`
  — unrecoverable per logging.md).
- Use `log_prints=False` on the wrapping task (FI models may use `print()` internally).
- Module-level log override: requires plan 016 (logging underscore convention fix)
  before the adapter module can be individually targeted. Module name will follow
  `conventions.md` snake_case: `forecast_interface`. See plan 016 for the env var
  encoding fix.

**Security:** FI-wrapped models execute third-party code in the same process as DB
connections and Docker secrets. The trust boundary assumption: model packages are vetted by the IT team and
installed at Docker image build time via Python entry-point registry — no
user-supplied or runtime-loaded model code is permitted.
Container privilege model (non-root, dropped capabilities per security.md) limits
host impact but not in-process access. `ModelOutput` DataFrame values pass through `ForecastQualityChecker.check()` at
step 1.10 (raising `SanityCheckFailure` on implausible values) before insertion
into `forecast_values`. This is a data integrity check, not a security boundary
(security.md §Model code trust boundary).

**Adapter class naming:** Per conventions.md `{Name}Adapter` pattern, the class
should be named `ForecastInterfaceAdapter`.

**Testing:** v0-scope.md §E2 requires `ReplayForecastInterfaceAdapter` — a test
replay adapter that serves recorded `ModelOutput` fixtures for FI-wrapped model
testing. Design of the replay adapter is out of scope for this plan (it belongs
with the Phase 3 adapter test infrastructure) but the production adapter's
interface must be designed to allow a drop-in replay implementation. Key test
cases for `ForecastInterfaceAdapter` itself: `TrajectoryData` → `from_members()`
roundtrip, `QuantileData` → `from_quantiles()` roundtrip, `DeterministicData` →
single-member conversion, `VariableStatus.FAILURE` exception raising, `PARTIAL`
status flag mapping, enum boundary conversion (UPPER_CASE → lowercase), and
temporal column renaming (`datetime` → `valid_time`).

---

## B — Weather Source Mapping Verification

### Context

Weather source mapping is fully modeled as a station attribute
(`StationWeatherSource` in `types/station.py`, `station_weather_sources` table). But
the model config (`config.toml`) also specifies NWP sources per model. Need to verify
these two don't conflict and the data flow is clear.

**Current design:**
- **Station level** (`station_weather_sources` table): which NWP sources a station
  can use + extraction type (point/basin_average/elevation_band). DB column `active`
  (BOOL) corresponds to Python type's `status: WeatherSourceStatus` (`ACTIVE`/`INACTIVE`).
- **Model level** (`config.toml [models.*.weather]`): which NWP sources a model
  expects + parameters + post-processing pipeline.
- **Resolution**: at runtime, the intersection determines what's extracted — only
  sources that both the station and model require.

**The full input preparation sequence** (architecture-context.md lines 1656–1662)
is a 6-step process downstream of intersection:
1. Run each source through its configured post-processing pipeline.
2. Transform all sources to the model's declared `spatial_input_type`.
3. Merge all parameters into a single forcing object.
4. Validate all features declared in `ModelDataRequirements.future_dynamic_features`
   and `past_dynamic_features` are present.
5. Load static catchment attributes from `basins.attributes` JSONB.
6. Fetch past target variable history into `past_targets`.

### Verification Tasks

1. Verify the intersection logic is documented and implemented correctly.
   **Reference the 6-step input preparation sequence above** — the intersection
   is step 0 (determining which sources to process), not the full preparation.
2. Check edge case: what happens when a model requires a source that's not mapped to
   the station? Should this be a hard error at onboarding validation time?
3. ~~Confirm that weather source mapping is indeed a station concern.~~ **Confirmed:**
   architecture-context.md explicitly states geometry is resolved from `basins`
   (physical property), and `station_weather_sources` maps station-to-source linkage.
   Station concern, not model concern.
4. ~~Document the relationship in architecture-context.md.~~ **Partially documented**
   at architecture-context.md lines 1615–1652 (both sides: station table + model
   config). The explicit intersection algorithm (station sources ∩ model sources =
   extraction set) is implicit but not stated as a discrete step. The 6-step input
   preparation sequence (lines 1656–1662) begins *after* the intersection.
   **Recommend:** add a "step 0" intersection description to architecture-context.md.

## Urgency

**Section B** directly blocks **Phase 5** (station onboarding) and **Phase 8** (forecast
cycle) — the intersection logic must be correct before the first station is onboarded.

**Section A, Tasks 1–3** target **v0b**. Model development against FI's contract is
starting now. The output adapter and FI input types PR should be ready before the first
FI-compatible model is handed off for integration. Tasks 1–2 (output adapter + enum
alignment) can proceed immediately — FI's output types are stable. **Prerequisite:**
plan 016 (logging underscore encoding fix) should land before or alongside Task 1
so the adapter module's per-module log override works correctly. Task 3 (FI input
types PR) should be coordinated with the model developer to validate the input shape.

**Section A, Tasks 4–5** deferred until FI's `interface/` module is implemented.

## Origin

Extracted from plan 011 §A and §D.
