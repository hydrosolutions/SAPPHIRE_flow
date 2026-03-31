---
status: ARCHIVED
created: 2026-03-27
scope: types + config + db schema + services + docs
depends_on: []  # no blocking dependencies; plan 005 (ARCHIVED) already references this plan
---

# 010 — Multi-Model Alert Strategy

## Problem

Flow 1 Phase C (steps 1.11–1.13) checks forecast ensembles against alert thresholds
and raises/resolves alerts. The architecture supports multiple active models per station
simultaneously (`types-and-protocols.md` ~line 701: "All model types can be active for
the same station simultaneously"), but **no mechanism exists** for deciding which model's
output — or which combination of outputs — drives alert decisions.

Current gaps:
- `ExceedanceResult` has no `model_id` — alerts are untraceable to the model that produced them
- `Alert` has no model identity — the alerts table upsert key is `(station_id, alert_level, source)` with no model dimension
- `ForecastEnsemble` has no `model_id` — convergence code cannot identify which model produced which ensemble
- `check_thresholds` takes a single `ForecastEnsemble` — no multi-model dispatch
- `architecture-context.md` Flow 1 steps 1.11–1.12 say "per station" but don't specify how multiple models' ensembles are combined or selected
- `orchestration.md` Phase C sketch shows `check_thresholds(all_results)` but `all_results` is never assembled and the function signature doesn't match

### Design principles

SAPPHIRE promotes **multi-model consensus-based forecasting** — the system's value
increases with each additional model, and alert decisions should benefit from model
diversity rather than discarding it. Following WMO-1091 §10 ("consideration of input
from multiple forecasting systems may give additional information on the probability of
extreme events") and established operational practice (EFAS, Delft-FEWS BMA), we support
four strategies selectable per deployment:

| Strategy | Level | Description | Precedent |
|---|---|---|---|
| `primary` | Forecast selection | Use the ensemble from the highest-priority model (`ModelAssignment.priority == 0`). **Note:** this extends the architecture's `priority` field beyond its documented fallback-order semantics (what to run when a model fails) to also mean alert-selection priority (whose output to use when all succeed). | Delft-FEWS Forecast Manager |
| `pooled` | Forecast combination | Concatenate all models' ensemble members into a grand ensemble; compute exceedance probability over pooled set. **Requires homogeneous MEMBERS representation** — mixed MEMBERS/QUANTILES falls back to `primary` (structurally incompatible DataFrames). | EFAS (51 ECMWF + 20 COSMO-LEPS members) |
| `bma` | Forecast combination | Bayesian Model Averaging — skill-weighted combination with per-model bias correction, producing calibrated quantiles | Delft-FEWS BMA module, WMO-1091 §9.1.1 |
| `consensus` | Decision combination | Check thresholds per model independently; trigger alert when fraction of models agreeing ≥ configurable threshold | Novel — intuitive for stakeholder communication |

**`primary`**, **`pooled`**, and **`bma`** are *forecast combination* strategies: they
produce a single combined ensemble (or select one), then standard `check_thresholds` runs
on it. **`consensus`** is a *decision combination* strategy: it runs `check_thresholds`
per model, then aggregates the binary outcomes.

**Cascading fallback:** The deployment configures a *preferred* strategy. At runtime, the
system degrades gracefully:
- `bma` → falls back to `pooled` unconditionally in v0 (no `BmaStrategy` implementation exists). v1 adds BMA weight check: falls back to `pooled` only when weights are unavailable.
- `consensus` → falls back to `pooled` in v0 (no `ConsensusVotingStrategy` activation until v1+). v1+ activates when stakeholder demand exists.
- `pooled` / `consensus` / `bma` → falls back to `primary` if only one model is active
- `pooled` / `bma` / `consensus` → falls back to `primary` if models have mixed representations (MEMBERS + QUANTILES cannot be concatenated)
- `primary` → always works (selects priority-0 model)

Fallback is the **convergence service's** responsibility (`_resolve_strategy_and_filter`), not the
individual strategies'. Strategies assume they receive valid input (≥2 models for
pooled/consensus). This avoids double-implementing the fallback logic.

This ensures the configured default works everywhere without per-station overrides, even
during cold-start or for stations with a single model.

**WMO alignment:**
- WMO-1091 §9.1.1 requires per-model bias correction before any combination → applies
  to `pooled` and `bma` strategies (bias correction is the `ForecastPostProcessor` step 1.9)
- WMO-1091 §8(c): run each ensemble member through the hydrological model, then compute
  exceedance probability from the resulting distribution → our existing ensemble architecture
- EFAS uses ≥50% of pooled ensemble members for formal alerts, ≥40% for informal →
  validates the probability-threshold approach used by `DangerLevelDefinition.trigger_probability`

---

## Changes

### Phase 0 — Types and Config

#### 0A. `src/sapphire_flow/types/enums.py` — `AlertModelStrategy` enum

```python
class AlertModelStrategy(Enum):
    PRIMARY = "primary"      # highest-priority model only
    POOLED = "pooled"        # grand ensemble from all models
    BMA = "bma"              # Bayesian Model Averaging (skill-weighted)
    CONSENSUS = "consensus"  # per-model threshold check, then vote
```

Also update `docs/spec/types-and-protocols.md` enum section to include `AlertModelStrategy`.

**Scope:** Enum definition only. No behavior.
**Verification:** `uv run pyright --strict src/sapphire_flow/types/enums.py`

#### 0B. `src/sapphire_flow/config/deployment.py` — config fields

Add to `DeploymentConfig`:

```python
alert_model_strategy: AlertModelStrategy = AlertModelStrategy.PRIMARY
min_operational_ensemble_size: int = 20  # skip threshold evaluation when total MEMBERS below this
min_operational_quantile_levels: int = 7  # skip threshold evaluation when total QUANTILES levels below this
```

Default is `primary` for v0 — this honestly reflects runtime behavior (v0 stations
typically have one active model, and BMA/pooled would cascade to primary anyway).
Mature multi-model deployments should set `bma` or `pooled` explicitly when the
prerequisites exist (multiple models, BMA weights trained).

`min_operational_ensemble_size` is referenced in the spec prose (~line 979: "operational
threshold evaluation requires `min_operational_ensemble_size`") but was never added to the
`DeploymentConfig` class. This plan adds it. The guard is representation-aware: MEMBERS
ensembles are checked against `min_operational_ensemble_size` (default 20), QUANTILES
ensembles against `min_operational_quantile_levels` (default 7). The default of 7 matches
the `ForecastEnsemble.from_quantiles()` factory minimum (spec ~line 995: "at least 7
quantile levels with tail coverage"). A lower config default would be dead code since
the factory rejects ensembles below 7 at construction time.

`consensus_model_fraction` is deferred to v1+ alongside `ConsensusVotingStrategy` (see
2D). The enum value `CONSENSUS` exists for forward compatibility; `_resolve_strategy_and_filter`
cascades it to `pooled` in v0.

**Note:** `enable_forecast_alerts`, `enable_observation_alerts`, `enable_pipeline_alerts`,
and `threshold_check_mode` already exist in `DeploymentConfig` (spec lines 2284–2287).
This plan does not duplicate them — Phase 3A's convergence service reads the existing
`enable_forecast_alerts` field.

Validation via `@field_validator` (not `@model_validator` — avoid logging side effects
during construction, which may run before structlog is configured):
- `min_operational_ensemble_size` must be `>= 1` — raise `ConfigurationError` on violation
- `min_operational_quantile_levels` must be `>= 7` — raise `ConfigurationError` on violation (matches factory minimum)

`ConfigurationError` is defined in `src/sapphire_flow/exceptions.py` as a subclass of
`SapphireError` (see `types-and-protocols.md` ~line 1596). It is the standard exception
for invalid deployment configuration across the codebase.

Also update:
- `config.toml`: add `alert_model_strategy = "primary"`, `min_operational_ensemble_size = 20`, `min_operational_quantile_levels = 7`
- `docs/spec/config-reference.toml`: add all three fields with documentation comments
- `docs/spec/types-and-protocols.md` `DeploymentConfig` section (~line 2245): add all three fields

**Scope:** Config field additions. No behavioral change.
**Verification:** `uv run pytest tests/unit/config/ -x -q`
**Dependencies:** 0A.

#### 0C. `src/sapphire_flow/types/domain.py` — `ExceedanceResult` model traceability

Add `model_ids` field:

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class ExceedanceResult:
    station_id: StationId
    danger_level: str
    parameter: Literal["discharge", "water_level"]
    threshold_value: float
    exceedance_probability: float | None
    observed_value: float | None
    exceeded: bool
    model_ids: tuple[ModelId, ...] = ()                        # NEW — models that contributed to this result; () until Phase 2 callers populate it
    strategy: AlertModelStrategy = AlertModelStrategy.PRIMARY   # NEW — which strategy produced this result; PRIMARY default until Phase 2 callers populate it
```

Defaults allow Phase 0C to land without breaking existing `ExceedanceResult` construction
sites. Phase 2 callers (strategies) always set both fields explicitly — the defaults exist
only to decouple Phase 0 from Phase 2 timing.

`tuple[ModelId, ...]` instead of `list[ModelId]` preserves deep immutability on the
frozen dataclass.

For `primary`: `model_ids = (primary_model_id,)`. For `pooled`/`bma`: all contributing
model IDs. For `consensus`: model IDs that voted "exceeded" (not all models — only the
agreeing ones, so the tuple length relative to total active models conveys agreement level).
The same `model_ids` tuple propagates from `ExceedanceResult` to `Alert` when the alert
is upserted.

Also update `docs/spec/types-and-protocols.md` `ExceedanceResult` definition (~line 609).
Preserve existing inline comments on other fields when adding the new fields.

**Scope:** Type change only. Callers updated in Phase 2.
**Verification:** `uv run pyright --strict src/sapphire_flow/types/domain.py`
**Dependencies:** 0A.

#### 0D. `src/sapphire_flow/types/alert.py` + `db/metadata.py` — `Alert` model traceability

Add to `Alert` dataclass:

```python
model_ids: tuple[ModelId, ...]  = ()                        # NEW — models that contributed; () for observation/pipeline alerts
alert_model_strategy: AlertModelStrategy | None = None      # NEW — strategy that produced the decision; None for observation/pipeline alerts
```

Both fields have defaults so observation-source and pipeline-source alerts (which have no
model dimension) can be constructed without specifying them.

`tuple[ModelId, ...]` instead of `list[ModelId]` preserves deep immutability on the
frozen dataclass.

Add to `alerts` table in `metadata.py`:

```python
sa.Column("model_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
sa.Column("alert_model_strategy", sa.Text, nullable=True),  # NULL for observation/pipeline alerts
```

Create Alembic migration adding both columns to the `alerts` table.

Also update:
- `docs/spec/types-and-protocols.md` `Alert` definition (~line 754)
- `docs/architecture-context.md` alerts table schema

**Note:** The `AlertStore.upsert_alert` upsert key remains `(station_id, alert_level, source)` —
we do not add a model dimension to the key. A station+danger_level has one active alert
regardless of how many models contributed. The `model_ids` field is for auditability,
not for keying.

**Scope:** Type + schema change. Store implementations updated in Phase 2.
**Verification:** `uv run pytest tests/ -x -q` after migration
**Dependencies:** 0A.

#### 0E. `src/sapphire_flow/types/ensemble.py` — thread `model_id` through `ForecastEnsemble`

Add optional field and computed property:

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastEnsemble:
    # ... existing fields ...
    model_id: ModelId | None = None    # NEW — set during forecast cycle; None for test/legacy

    @property
    def member_count(self) -> int:
        """Number of ensemble members (MEMBERS) or quantile levels (QUANTILES)."""
        match self.representation:
            case EnsembleRepresentation.MEMBERS:
                return self.values["member_id"].n_unique()
            case EnsembleRepresentation.QUANTILES:
                return self.values["quantile"].n_unique()
            case _:
                raise ValueError(f"Unknown representation: {self.representation}")
```

`member_count` is used by the convergence service (Phase 3A) to guard against
insufficient ensemble size. The spec prose (~line 978) references this concept but
it was never formalized as an accessor on `ForecastEnsemble`. Note: the guard is
representation-aware — the convergence service uses `min_operational_ensemble_size`
for MEMBERS and `min_operational_quantile_levels` for QUANTILES (see Phase 3A).

This allows convergence code to identify which model produced each ensemble without
requiring a separate tracking structure.

Also update:
- `docs/spec/types-and-protocols.md` `ForecastEnsemble` definition: add `model_id` field and `member_count` property
- `ForecastEnsemble.from_members()` and `ForecastEnsemble.from_quantiles()` factory
  classmethods: add `model_id: ModelId | None = None` parameter and forward it to the
  constructor. Since `ForecastEnsemble` is a frozen dataclass, fields cannot be set
  post-construction — the factories are the primary construction path and must accept
  `model_id` to make it settable.

**Scope:** Optional field, computed property, factory method signature additions. Existing code unaffected (default None).
**Verification:** `uv run pyright --strict src/sapphire_flow/types/ensemble.py` && `uv run pytest tests/ -x -q`
**Dependencies:** None.

### Phase 1 — Architecture Documentation

#### 1A. `docs/architecture-context.md` — Flow 1 Phase C

Update the step table annotations for 1.11 and 1.12 (~lines 112–113) to document:
1. Multi-model strategy dispatch: Phase C receives all models' ensembles per station,
   dispatches to the configured `AlertModelStrategy`
2. Strategy descriptions (primary/pooled/bma/consensus) — brief, with cross-reference
   to this plan for details
3. Cascading fallback behavior (including mixed-representation fallback to `primary`)
4. `model_ids` traceability on `ExceedanceResult` and `Alert`
5. **v0 deviation on step 1.12**: Add annotation to step 1.12: "v0:
   `DangerLevelDefinition` fields exist (`trigger_probability`, `resolve_probability`,
   `min_trigger_duration`, `min_resolve_duration`); alert service does not enforce
   duration-based hysteresis — triggers and resolves within a single cycle. v1 adds
   cross-cycle state tracking."

Update the sequencing block (~line 163) Phase C description:
- "Phase C runs after all Phase B units complete" — add: "Phase C collects all
  models' forecast ensembles per station and applies the configured `alert_model_strategy`
  to determine exceedance per danger level."

Update the alerts table schema block to include `model_ids` and `alert_model_strategy`.

Update the Priority convention paragraph (~line 888) to acknowledge that priority-0
also determines whose output drives alert decisions when all models succeed.

Update `model_assignments.priority` field comment (~line 1489) and
`ModelAssignment.priority` docstring in `types-and-protocols.md` (~line 688) to document
the dual semantics: fallback order (what to run when a model fails) AND alert-selection
priority (whose output drives alerts when all succeed). See Open Item §7.

Update `group_model_assignments.priority` comment (~line 1504) to note that group
priorities are expanded to per-station entries by the Phase B accumulation logic
for use in Phase C's strategy dispatch.

**Scope:** Doc-only.
**Verification:** Manual review.
**Dependencies:** None (can proceed in parallel with Phase 0).

#### 1B. `docs/v0-scope.md` — §A8

Add new subsection **§A8d. Multi-model alert strategy** and update **§C alerts row**
to note the two new columns (`model_ids JSONB`, `alert_model_strategy TEXT`) so the
v0 table list stays in sync with `architecture-context.md`:

> `alerts` — as designed, plus `model_ids` (JSONB, `[]` for observation/pipeline alerts)
> and `alert_model_strategy` (TEXT, NULL for observation/pipeline alerts) for forecast
> alert traceability (see §A8d). Keep `notified_at` as always-NULL.

§A8d text:

> **Full design**: Four strategies (primary, pooled, bma, consensus) selectable per
> deployment via `alert_model_strategy` config. BMA is the recommended default for
> mature multi-model deployments. Cascading fallback: bma → pooled → primary.
>
> **v0**: `alert_model_strategy` config field exists with default `primary`. The
> strategy enum, config field, convergence structure, and type traceability
> (`model_ids` on `ExceedanceResult` and `Alert`) are implemented from day one.
> Only `PrimaryModelStrategy` is exercised at runtime.
>
> **v0b**: `pooled` strategy implemented when second model is onboarded per station.
> Deployers with multiple models per station switch config to `pooled`.
>
> **v1**: `bma` strategy implemented with weight training pipeline (linked to
> Flow 8/10 skill recomputation). Deployers switch config to `bma` once weights are
> trained. `consensus` strategy implemented if stakeholder demand exists.

Also add to **§D6 (per-step instrumentation)** a budget row for Phase C:

> | 1.11–1.13 Alert checking | < 5s | In-memory |

This step is not currently in the §D6 table — adding it satisfies the §D6 mandate that
every Flow 1 step is instrumented with a target budget.

Also add to **§I (v1 compatibility risks)**:

> ### I3. Decouple alert-selection priority from fallback priority
>
> `ModelAssignment.priority` is extended in v0 from "fallback order" to also mean
> "alert-selection priority" (whose ensemble drives alerts when all models succeed).
> These semantics are consistent today (priority 0 = run first = use for alerts) but
> could diverge in v1 if a fast-but-less-accurate model gets priority 0 for fallback
> speed but should not drive alert decisions.
>
> **v1 action:** Add `alert_priority: int | None` to `ModelAssignment` (and
> `group_model_assignments`). When set, overrides `priority` for alert selection.
> When NULL, falls back to `priority`. This is an additive, nullable column — safe
> migration on small data.

**Scope:** Doc-only.
**Verification:** Manual review.
**Dependencies:** None.

#### 1C. `docs/standards/orchestration.md` — Phase B accumulation + Phase C sketch

Two changes to the Flow 1 illustrative sketch:

**Phase B:** The current sketch reassigns `group_results` / `station_results` each
iteration without accumulating them. Add an `all_ensembles` collector and an
`all_priorities` collector before the loops:

```python
# Structure: station → model → parameter_name → ForecastEnsemble
all_ensembles: dict[StationId, dict[ModelId, dict[str, ForecastEnsemble]]] = defaultdict(dict)
# Structure: station → model → priority_value
all_priorities: dict[StationId, dict[ModelId, int]] = defaultdict(dict)

# Populate all_priorities from BOTH assignment tables:
# - model_assignments (station-scoped models): direct station → model → priority
# - group_model_assignments (group-scoped models): expand group priority to all
#   stations in the group, so each station inherits the group's priority value

for model in group_scoped_models:
    group_results = forecast_station.map(...)
    # accumulate: all_ensembles[station_id][model_id][parameter] = ensemble

for model in station_scoped_models:
    station_results = forecast_station.map(...)
    # accumulate: all_ensembles[station_id][model_id][parameter] = ensemble
```

**Phase C:** Replace the opaque `check_thresholds(all_results)  # converge` line (~line 78)
with the convergence call:

```python
check_station_alerts(all_ensembles, all_thresholds, danger_levels, all_priorities, config, alert_store, clock)  # Phase C (plan 010)
```

The full dispatch loop and strategy details belong in `architecture-context.md` (task 1A)
and the implementation (Phase 3A), not in orchestration.md's illustrative sketch.

**Scope:** Doc-only.
**Verification:** Manual review.
**Dependencies:** None.

#### 1D. `docs/standards/wmo.md` — multi-model alert reference

Add a paragraph under §3 "Alert and warning system" noting:
- WMO-1091 §10: multiple forecasting systems give additional probability information
- WMO-1091 §9.1.1: per-model bias correction required before combination (applied by
  analogy to hydrological model combination — the original section addresses NWP
  ensemble post-processing, but the principle extends: bias-correct each model's output
  before combining)
- Cross-reference to `architecture-context.md` Flow 1 Phase C for SAPPHIRE's four-strategy approach
- Clarify that SAPPHIRE's BMA (skill-weighted hydrological model combination) is distinct
  from WMO-1254 Tier 3 BMA (atmospheric EPS post-processing). Both use Bayesian Model
  Averaging but operate at different points in the forecast chain.

Non-WMO operational precedents (EFAS pooled ensemble, Delft-FEWS BMA) are documented
in `architecture-context.md` Phase C (task 1A), not in wmo.md — they are system
implementations, not WMO standards.

**Scope:** Doc-only.
**Verification:** Manual review.
**Dependencies:** None.

### Phase 2 — Strategy Protocol and Primary Implementation (v0)

#### 2A. `src/sapphire_flow/types/domain.py` + `src/sapphire_flow/protocols/alert_strategy.py` — Type alias + Strategy Protocol

`ForecastParameter` lives in `types/domain.py` (not the protocol file) since it is a
cross-cutting type used by both protocols and services:

```python
# types/domain.py
ForecastParameter = Literal["discharge", "water_level"]
```

Strategy Protocol in `protocols/alert_strategy.py`:

```python
@runtime_checkable
class ModelAlertStrategy(Protocol):
    def evaluate(
        self,
        station_id: StationId,
        parameter: ForecastParameter,
        model_ensembles: dict[ModelId, ForecastEnsemble],
        thresholds: list[StationThreshold],
        danger_levels: list[DangerLevelDefinition],
        priorities: dict[ModelId, int],
    ) -> list[ExceedanceResult]:
        """Evaluate alert thresholds using the strategy's combination method.

        Returns one ExceedanceResult per (danger_level, parameter) pair where
        the station has a defined threshold.

        `priorities` maps each model to its assignment priority (0 = primary).
        Used by PrimaryModelStrategy to select the primary model; ignored by
        pooled/consensus strategies but present for interface uniformity.
        """
        ...
```

`parameter` is `Literal["discharge", "water_level"]` (via `ForecastParameter` alias),
matching `StationThreshold.parameter` and `ExceedanceResult.parameter`. The convergence
service (Phase 3A) narrows from `ForecastEnsemble.parameter: str` to this Literal
before dispatch — strategies receive an already-narrowed type and can construct
`ExceedanceResult` without type errors under pyright strict.

**Note:** `ForecastEnsemble.parameter` intentionally remains `str` (not narrowed to
`ForecastParameter`) — models may produce parameters that don't participate in alerting.
The convergence service (`_check_station`) is the single narrowing point. Do not "fix"
this by narrowing `ForecastEnsemble.parameter` to the Literal.

All four strategies implement this Protocol. The caller does not need to know whether
combination happens at the forecast level or decision level.

**Resolved:** Open Item §1 (priority information) — option (a) adopted. `priorities`
is an explicit parameter on the Protocol. `PrimaryModelStrategy` uses it to select the
highest-precedence model (lowest `priority` value; 0 = primary). Other strategies accept
but ignore it. The convergence service (Phase 3A) populates `priorities` from both
`model_assignments` (station-scoped) and `group_model_assignments` (group-scoped) records
fetched alongside ensembles.

Also update `docs/spec/types-and-protocols.md`:
- Add `ModelAlertStrategy` Protocol to the Protocol section
- Add `ForecastParameter` type alias (`Literal["discharge", "water_level"]`) to the type alias section

**Scope:** Protocol definition only.
**Verification:** `uv run pyright --strict src/sapphire_flow/protocols/alert_strategy.py`
**Dependencies:** 0A, 0C.

#### 2B. `src/sapphire_flow/services/alert_strategy.py` — `PrimaryModelStrategy`

Select the ensemble from the highest-precedence model (lowest `priority` value; 0 = primary)
among active assignments. Compute exceedance probability per danger level using existing
`check_thresholds` logic. Set `model_ids = (selected_model_id,)`.

If no model has priority 0, select the lowest available priority. If `model_ensembles`
is empty, return empty list (no alerts possible).

```python
class PrimaryModelStrategy:
    def evaluate(
        self,
        station_id: StationId,
        parameter: ForecastParameter,
        model_ensembles: dict[ModelId, ForecastEnsemble],
        thresholds: list[StationThreshold],
        danger_levels: list[DangerLevelDefinition],
        priorities: dict[ModelId, int],
    ) -> list[ExceedanceResult]:
        if not model_ensembles:
            return []
        if not priorities:
            log.warning("alert.priorities_not_found", station_id=station_id,
                        n_models=len(model_ensembles))
        # Select highest-precedence model (lowest priority value, then lowest model_id for determinism)
        primary_model_id = min(
            model_ensembles.keys(),
            key=lambda mid: (priorities.get(mid, 999), str(mid)),
        )
        ensemble = model_ensembles[primary_model_id]
        param_thresholds = [t for t in thresholds if t.parameter == parameter]
        return [
            ExceedanceResult(
                ...,
                model_ids=(primary_model_id,),
                strategy=AlertModelStrategy.PRIMARY,
            )
            for dl in danger_levels
            if (threshold := _find_threshold(param_thresholds, dl)) is not None
        ]
```

Tie-breaking: when two models share the same priority value, `str(model_id)` provides
stable, deterministic ordering. This is a secondary sort — the primary remains the
priority value from `ModelAssignment`.

**Shared helpers** (used by both `PrimaryModelStrategy` and `PooledEnsembleStrategy`):

- `_find_threshold(thresholds: list[StationThreshold], danger_level: DangerLevelDefinition) -> StationThreshold | None` — looks up the threshold matching the given danger level. Returns `None` if the station has no threshold defined for that level (station skips that danger level).
- `_compute_exceedance(ensemble: ForecastEnsemble, threshold_value: float) -> float` — computes the exceedance probability from an ensemble. For MEMBERS: fraction of members exceeding the threshold. For QUANTILES: linear interpolation between adjacent quantile levels to invert the CDF — if the threshold falls between quantile level Q(p1)=v1 and Q(p2)=v2, the exceedance probability is `1 - (p1 + (p2 - p1) * (threshold - v1) / (v2 - v1))`; if the threshold exceeds all quantile values, returns 0.0; if below all, returns 1.0. Returns a float in `[0.0, 1.0]`. **Note:** callers pass `StationThreshold.value` (the spec field name) as `threshold_value` (the `ExceedanceResult` field name) — the naming differs between the two types.

**Scope:** Primary strategy implementation only.
**Verification:** `uv run pytest tests/unit/services/test_alert_strategy.py -x -q`
**Dependencies:** 2A.

#### 2C. `src/sapphire_flow/services/alert_strategy.py` — `PooledEnsembleStrategy`

Concatenate all models' ensemble members into one grand ensemble DataFrame. Recompute
exceedance probability over the pooled set. Set `model_ids = tuple(model_ensembles.keys())`.

**Precondition:** All ensembles must have `representation == MEMBERS`. The convergence
service (`_resolve_strategy_and_filter`) guarantees this — it falls back to `primary` when
representations are mixed (MEMBERS + QUANTILES DataFrames are structurally incompatible:
different column schemas). `PooledEnsembleStrategy.evaluate()` asserts this precondition
defensively.

```python
class PooledEnsembleStrategy:
    def evaluate(
        self,
        station_id: StationId,
        parameter: ForecastParameter,
        model_ensembles: dict[ModelId, ForecastEnsemble],
        thresholds: list[StationThreshold],
        danger_levels: list[DangerLevelDefinition],
        priorities: dict[ModelId, int],
    ) -> list[ExceedanceResult]:
        # Assumes ≥2 models, all MEMBERS (convergence service handles fallback)
        if not all(
            e.representation == EnsembleRepresentation.MEMBERS
            for e in model_ensembles.values()
        ):
            raise ValueError("PooledEnsembleStrategy requires homogeneous MEMBERS representation")
        pooled = _pool_ensembles(model_ensembles)
        # ... check_thresholds on pooled ensemble ...
        # model_ids = tuple(model_ensembles.keys())
```

**`_pool_ensembles` contract:** Concatenates `values` DataFrames from all models. Must
renumber `member_id` to sequential integers starting from 0 to avoid collisions (model A
members 0–20 and model B members 0–20 → pooled members 0–41). Original member IDs may
be arbitrary labels, not contiguous integers — the renumbering assigns new sequential
IDs in model-iteration order. Uses the outer dict key (`ModelId`) as the authoritative
model identity — never `ensemble.model_id` (which may be `None` in test/legacy contexts).

**WMO constraint:** Per WMO-1091 §9.1.1 (applied by analogy — the original section
addresses NWP ensemble post-processing, but the principle extends to hydrological
model combination), per-model bias correction must happen before pooling. This is the
responsibility of step 1.9 (`ForecastPostProcessor`), not this strategy. The strategy
assumes ensembles are already bias-corrected.

**Scope:** Pooled strategy implementation.
**Verification:** `uv run pytest tests/unit/services/test_alert_strategy.py -x -q`
**Dependencies:** 2A.
**Implementation timing:** Code lands at Phase 8 alongside PrimaryModelStrategy and
the convergence service. Exercised at runtime from v0b when second model is onboarded
per station. Before v0b, `_resolve_strategy_and_filter` cascades to `primary` for single-model
stations.

#### 2D. `src/sapphire_flow/services/alert_strategy.py` — `ConsensusVotingStrategy` (deferred)

**Not implemented in v0.** No class is created — `_resolve_strategy_and_filter` (Phase 3A)
never instantiates `ConsensusVotingStrategy`. When the preferred strategy is `consensus`,
the convergence service falls back to `pooled` (or `primary` if single model). This
follows the same deferral pattern as BMA (2E).

v1+ implementation: per-model threshold check, then vote aggregation with configurable
agreement fraction. The `ConsensusVotingStrategy` class is created alongside stakeholder
demand — not before.

**Scope:** No v0 code. Class + implementation deferred to v1+ plan.
**Dependencies:** 2A.
**Implementation timing:** v1+.

#### 2E. `src/sapphire_flow/services/alert_strategy.py` — `BmaStrategy` (deferred)

**Not implemented in v0.** No stub class is created — `_resolve_strategy_and_filter` (Phase 3A)
never instantiates `BmaStrategy`. When the preferred strategy is `bma`, the convergence
service falls back to `pooled` (or `primary` if single model). This avoids a
`NotImplementedError` crash if any code path sets `bma_weights` to non-None in v0.

v1 implementation: BMA weight estimation from hindcast verification data (Flow 8/10),
producing `BmaWeights` per `(station_id, model_id)`. The strategy computes a weighted
mixture distribution and derives exceedance probability from it. The `BmaStrategy` class
is created alongside the weight training pipeline — not before.

**BMA weight training** is out of scope for this plan — it belongs in a future plan
tied to Flow 8/10 (skill recomputation), which already computes per-model verification
metrics from hindcast data. The weights are a function of the same data.

**Scope:** No v0 code. Class + implementation deferred to v1 plan.
**Dependencies:** 2A.
**Implementation timing:** v1.

### Phase 3 — Convergence Service

#### 3A. `src/sapphire_flow/services/alert_checker.py` — strategy dispatch + cascading fallback

The convergence service is the entry point for Phase C. It is wrapped as a single
Prefect `@task` (not `@flow`) — it has DB-write side effects via `AlertStore`, which
per orchestration.md's task granularity rule ("crosses a system boundary — DB write")
warrants `@task` for retry and Prefect UI observability. A single task wrapping the
per-station loop is appropriate since Phase C runs once per cycle (not per station).

Steps:

1. **Guard on `enable_forecast_alerts`** — early return if disabled (§A8c)
2. Receives all models' forecast ensembles for all stations (collected from Phase B fan-out)
3. Iterates stations, then iterates parameters within each station
4. **Narrows `parameter: str` → `ForecastParameter`**: filters to only parameters that
   appear in both the station's ensembles and `StationThreshold` definitions. Parameters
   not in `("discharge", "water_level")` are skipped with a debug log. This is the single
   narrowing point — strategies receive `ForecastParameter` and can construct
   `ExceedanceResult` without pyright errors.
5. **Resolves the effective strategy** per (station, parameter) via cascading fallback
   (`_resolve_strategy_and_filter`). Strategy resolution happens first so that the ensemble size
   check (step 6) evaluates only the ensembles the resolved strategy will actually use.
6. **Guards: ensemble size check** on the effective ensemble (post-strategy-resolution).
   For `primary`: checks only the selected model's ensemble. For `pooled`: checks the
   combined ensemble. Representation-aware: MEMBERS checked against
   `config.min_operational_ensemble_size` (default 20), QUANTILES against
   `config.min_operational_quantile_levels` (default 7).
   Logs warning with `alert.ensemble_skipped` event.
7. Dispatches to the strategy implementation
8. **Accumulates** all parameters' `ExceedanceResult`s per station, then feeds to
   `_process_results` for alert upsert/resolution **after all parameters are evaluated**.
   This prevents premature resolution of alerts for one parameter while another parameter
   has not yet been evaluated. `Alert` is keyed on `(station_id, alert_level, source)`
   with no parameter dimension — a DL3 alert is station-scoped, so resolution must check
   ALL parameters before resolving.

**v0 direction guard:** All danger level definitions use `ThresholdDirection.ABOVE` per
v0-scope.md §A8a. `_compute_exceedance` computes `P(forecast > threshold)`. A `BELOW`
threshold reaching this code path would produce incorrect probabilities. The convergence
service filters out danger levels with `direction != ABOVE` and logs
`alert.direction_skipped` at warning level. v1 adds BELOW support.

**Priority assembly for group-scoped models:** `all_priorities` must include entries for
both station-scoped models (from `model_assignments`) and group-scoped models (from
`group_model_assignments`, expanded to per-station entries for all stations in the group).
The Phase B accumulation logic (orchestration sketch, task 1C) is responsible for
assembling `all_priorities` from both tables. Group-scoped models inherit the group
assignment's `priority` value.

The task wraps the full per-station loop. Phase C runs once per cycle (not per station).
At v0 scale (~1000 stations), a single task is appropriate: Phase C is pure in-memory
computation (~2ms per station × 1000 = ~2s) — negligible relative to the 60s cycle target.
Per-station task creation overhead would dominate at this scale. DB-write side effects (alert upsert)
stay within the `@task` boundary for retry and observability.

```python
# Valid forecastable parameters for threshold/alert logic
_FORECAST_PARAMETERS: set[ForecastParameter] = {"discharge", "water_level"}

# Tracks strategies that have already logged an unimplemented-fallback warning
# in this process lifetime. Keyed on (preferred, actual) to avoid logging
# "fell back to pooled" when the actual fallback was to primary (mixed reps).
_STRATEGY_FALLBACK_WARNED: set[tuple[AlertModelStrategy, str]] = set()

@task(name="check_station_alerts", log_prints=False)
def check_station_alerts(
    all_ensembles: dict[StationId, dict[ModelId, dict[str, ForecastEnsemble]]],
    all_thresholds: dict[StationId, list[StationThreshold]],
    danger_levels: list[DangerLevelDefinition],
    all_priorities: dict[StationId, dict[ModelId, int]],
    config: DeploymentConfig,
    alert_store: AlertStore,
    clock: Callable[[], UtcDatetime],
) -> None:
    if not config.enable_forecast_alerts:
        return

    # v0: only raw threshold checking is supported (§A8b). Flow 3 (review/publish)
    # is deferred, so threshold_check_mode must be "raw". Guard against misconfiguration.
    # log.error (not warning) because this completely disables all alert checking.
    if config.threshold_check_mode != "raw":
        log.error("alert.check_mode_rejected",
                  mode=config.threshold_check_mode,
                  reason="flow_3_deferred_v0")
        return

    # Filter to ABOVE-direction danger levels only (v0 — §A8a)
    above_levels = [
        dl for dl in danger_levels
        if dl.direction == ThresholdDirection.ABOVE
    ]
    skipped = len(danger_levels) - len(above_levels)
    if skipped:
        log.warning("alert.direction_skipped", count=skipped, direction="BELOW")

    t0 = time.perf_counter()
    for station_id, model_ensembles in all_ensembles.items():
        with bound_contextvars(station_id=str(station_id)):
            thresholds = all_thresholds.get(station_id, [])
            priorities = all_priorities.get(station_id, {})
            _check_station(
                station_id, model_ensembles, thresholds,
                above_levels, priorities, config, alert_store, clock,
            )
    log.info("alert.completed",
             duration_ms=round((time.perf_counter() - t0) * 1000, 1),
             stations_checked=len(all_ensembles))


def _unique_parameters(
    model_ensembles: dict[ModelId, dict[str, ForecastEnsemble]],
) -> set[str]:
    """Return the union of all parameter names across all models' ensembles."""
    return {param for ens in model_ensembles.values() for param in ens}


def _check_station(
    station_id: StationId,
    model_ensembles: dict[ModelId, dict[str, ForecastEnsemble]],
    thresholds: list[StationThreshold],
    danger_levels: list[DangerLevelDefinition],
    priorities: dict[ModelId, int],
    config: DeploymentConfig,
    alert_store: AlertStore,
    clock: Callable[[], UtcDatetime],
) -> None:
    # Accumulate results across ALL parameters before resolving alerts.
    # Alert upsert key is (station_id, alert_level, source) — no parameter dimension.
    # Resolving per-parameter would falsely resolve alerts for parameters not yet evaluated.
    all_results: list[ExceedanceResult] = []
    evaluated_parameters: set[ForecastParameter] = set()  # tracks which parameters were actually evaluated

    for raw_parameter in sorted(_unique_parameters(model_ensembles)):
        # Narrow str → ForecastParameter (single narrowing point;
        # adding a new alertable parameter requires updating BOTH ForecastParameter
        # Literal and _FORECAST_PARAMETERS — pyright enforces consistency)
        if raw_parameter not in _FORECAST_PARAMETERS:
            log.debug("alert.parameter_skipped", parameter=raw_parameter)
            continue
        parameter: ForecastParameter = cast(ForecastParameter, raw_parameter)

        param_ensembles = {
            mid: ens[raw_parameter]
            for mid, ens in model_ensembles.items()
            if raw_parameter in ens
        }
        if not param_ensembles:
            continue

        # Resolve strategy FIRST — determines which ensembles are actually used
        representations = {e.representation for e in param_ensembles.values()}
        strategy, effective_ensembles = _resolve_strategy_and_filter(
            preferred=config.alert_model_strategy,
            param_ensembles=param_ensembles,
            representations=representations,
            priorities=priorities,
        )

        # Guard: ensemble size check on the EFFECTIVE ensemble (post-strategy-resolution)
        if not _ensemble_size_adequate(effective_ensembles, config, station_id, parameter):
            continue

        evaluated_parameters.add(parameter)
        results = strategy.evaluate(
            station_id, parameter, effective_ensembles, thresholds,
            danger_levels, priorities,
        )
        all_results.extend(results)

    # Resolve after ALL parameters evaluated — prevents cross-parameter false resolution.
    # Skip resolution entirely when no parameter was evaluated (e.g. all ensembles too
    # small) — an unevaluated cycle must NOT resolve existing active alerts.
    if evaluated_parameters:
        _process_results(all_results, station_id, evaluated_parameters, thresholds, alert_store, clock)


def _ensemble_size_adequate(
    ensembles: dict[ModelId, ForecastEnsemble],
    config: DeploymentConfig,
    station_id: StationId,
    parameter: ForecastParameter,
) -> bool:
    """Check if the effective ensemble is large enough for reliable threshold evaluation.

    Representation-aware: MEMBERS ensembles are checked against
    min_operational_ensemble_size (default 20), QUANTILES against
    min_operational_quantile_levels (default 7).

    Precondition: all ensembles must share the same representation.
    _resolve_strategy_and_filter guarantees this (mixed reps fall back to
    primary, returning a single-entry dict), but we assert defensively.
    """
    if not ensembles:
        log.debug("alert.ensemble_not_found", station_id=station_id,
                  parameter=parameter)
        return False

    representations = {e.representation for e in ensembles.values()}
    if len(representations) > 1:
        raise ValueError(
            f"_ensemble_size_adequate requires homogeneous representations, "
            f"got {representations} for station {station_id} parameter {parameter}"
        )
    total = sum(e.member_count for e in ensembles.values())

    if EnsembleRepresentation.MEMBERS in representations:
        min_required = config.min_operational_ensemble_size
    else:
        # All QUANTILES
        min_required = config.min_operational_quantile_levels

    if total < min_required:
        log.warning(
            "alert.ensemble_skipped",
            station_id=station_id, parameter=parameter,
            reason="ensemble_too_small",
            total=total, min_required=min_required,
            representations=[r.value for r in representations],
        )
        return False
    return True


def _resolve_strategy_and_filter(
    preferred: AlertModelStrategy,
    param_ensembles: dict[ModelId, ForecastEnsemble],
    representations: set[EnsembleRepresentation],
    priorities: dict[ModelId, int],
) -> tuple[ModelAlertStrategy, dict[ModelId, ForecastEnsemble]]:
    """Resolve the effective strategy and return the ensemble subset it will use.

    Returns (strategy, effective_ensembles) so the caller can size-check the
    ensemble that will actually be evaluated, not the full input set.

    Cascading fallback with three triggers: unimplemented strategy, single model,
    mixed representations.

    Fallback order:
      bma → pooled → primary
      consensus → pooled → primary
      pooled → primary (when n_models ≤ 1 or mixed representations)
      primary → always works

    v0: BMA and consensus always fall back (no implementation exists).
    v0b: Pooled becomes available when PooledEnsembleStrategy is exercised.
    v1: Add `bma_weights` parameter and instantiate BmaStrategy when weights exist.
         Activate ConsensusVotingStrategy when stakeholder demand exists.
    """
    n_models = len(param_ensembles)

    if n_models <= 1:
        return PrimaryModelStrategy(), param_ensembles

    # Mixed MEMBERS + QUANTILES cannot be pooled — structurally incompatible DataFrames
    is_homogeneous_members = representations == {EnsembleRepresentation.MEMBERS}

    def _select_primary_ensemble() -> dict[ModelId, ForecastEnsemble]:
        """Return a single-entry dict with only the primary model's ensemble."""
        primary_id = min(
            param_ensembles.keys(),
            key=lambda mid: (priorities.get(mid, 999), str(mid)),
        )
        return {primary_id: param_ensembles[primary_id]}

    match preferred:
        case AlertModelStrategy.BMA:
            # v0: no BMA implementation — fall back to pooled (or primary if mixed)
            actual = "pooled" if is_homogeneous_members else "primary"
            _warn_fallback_once(preferred, actual, "bma_not_implemented")
            if not is_homogeneous_members:
                return PrimaryModelStrategy(), _select_primary_ensemble()
            return PooledEnsembleStrategy(), param_ensembles
        case AlertModelStrategy.CONSENSUS:
            # v0: no consensus implementation — fall back to pooled (or primary if mixed)
            actual = "pooled" if is_homogeneous_members else "primary"
            _warn_fallback_once(preferred, actual, "consensus_not_implemented")
            if not is_homogeneous_members:
                return PrimaryModelStrategy(), _select_primary_ensemble()
            return PooledEnsembleStrategy(), param_ensembles
        case AlertModelStrategy.POOLED:
            if not is_homogeneous_members:
                log.warning("alert.strategy_degraded", preferred="pooled",
                            actual="primary", reason="mixed_representations")
                return PrimaryModelStrategy(), _select_primary_ensemble()
            return PooledEnsembleStrategy(), param_ensembles
        case AlertModelStrategy.PRIMARY:
            return PrimaryModelStrategy(), _select_primary_ensemble()
        case _:
            raise ValueError(f"Unhandled strategy: {preferred}")


def _warn_fallback_once(
    preferred: AlertModelStrategy,
    actual: str,
    reason: str,
) -> None:
    """Log unimplemented-strategy fallback warning once per (preferred, actual) pair."""
    key = (preferred, actual)
    if key not in _STRATEGY_FALLBACK_WARNED:
        log.warning("alert.strategy_degraded", preferred=preferred.value,
                    actual=actual, reason=reason)
        _STRATEGY_FALLBACK_WARNED.add(key)
```

**Fallback warnings:** `_STRATEGY_FALLBACK_WARNED` ensures unimplemented-strategy
warnings are logged once per process lifetime, not per station per cycle. This prevents
log pollution at scale (N stations × M parameters × K cycles). The key is
`(preferred, actual)` so that BMA→pooled and BMA→primary (mixed reps) are logged
separately. **Process-scope limitation:** after changing `alert_model_strategy` in
config, the Prefect worker must be restarted to restore fallback warning visibility —
the set retains keys from the previous config. Mixed-representation fallback for
`pooled` uses `log.warning` (consistent with other degradation paths — logging.md
defines WARNING as "degraded state, operation continues").

**Cascading fallback order:** `bma` → `pooled`, `consensus` → `pooled`, then
`pooled`/`primary` → `primary` when `n_models <= 1` or representations are mixed. This
means at Phase 8 (v0), only `PrimaryModelStrategy` and `PooledEnsembleStrategy` need to
exist. At v0 scale (single model per station), all strategies cascade to `primary`
regardless.

**Ensemble filtering:** `_resolve_strategy_and_filter` returns both the strategy and
the ensemble subset it will operate on. For `primary`, this is a single-entry dict with
only the primary model. For `pooled`, this is the full input set. The caller then
size-checks only the effective ensemble — this prevents a mixed-representation scenario
where the total count passes the threshold but the primary model alone does not.

**`_process_results` — alert upsert and resolution logic:**

Called once per station after ALL parameters have been evaluated. This is critical:
`Alert` is keyed on `(station_id, alert_level, source)` with no parameter dimension.
A DL3 alert for a station is station-scoped — it must remain raised if ANY parameter
exceeds DL3. Processing resolution per-parameter would falsely resolve water_level
alerts during discharge processing (or vice versa).

**Caller contract:** `_process_results` is only called when at least one parameter was
actually evaluated (`evaluated_parameters` non-empty in `_check_station`). If all
parameters were skipped (ensemble too small, BELOW direction, etc.), the caller skips
this function entirely. An unevaluated cycle must NOT resolve existing active alerts —
otherwise a transient model failure (producing too-small ensembles) would silently clear
live flood alerts.

**Parameter-aware resolution:** `_process_results` receives `evaluated_parameters` — the
set of parameters that were actually evaluated in this cycle. Resolution only considers
danger levels whose **all** threshold-configured parameters were evaluated. If a station
has thresholds for both discharge and water_level, but only water_level was evaluated
(discharge model failed in Phase B), active discharge-related alerts are NOT resolved —
the system cannot confirm non-exceedance for an unevaluated parameter. This prevents
false resolution when a model partially fails. The `thresholds` list provides the mapping
from danger level to configured parameters.

**model_ids union:** When multiple parameters exceed the same danger level with
different contributing models (e.g. discharge via models A+B, water_level via models
B+C), `_process_results` accumulates `model_ids` as the union across all exceeded
results per danger level, then calls `upsert_alert` once per level. The tuple is sorted
by `str(model_id)` for deterministic ordering. This prevents the nondeterministic
overwrite that would occur if `upsert_alert` were called once per result (set iteration
order over parameters is undefined).

```python
def _process_results(
    results: list[ExceedanceResult],
    station_id: StationId,
    evaluated_parameters: set[ForecastParameter],
    thresholds: list[StationThreshold],
    alert_store: AlertStore,
    clock: Callable[[], UtcDatetime],
) -> None:
    """Upsert exceeded results and resolve previously-raised alerts that are no longer exceeded.

    Resolution logic: fetch active forecast alerts for this station, then resolve any
    whose (alert_level) is not in the current exceeded set AND whose configured
    parameters were all evaluated. `results` must contain ExceedanceResults from ALL
    evaluated parameters — caller must not call this per-parameter.

    **Caller contract:** only called when `evaluated_parameters` is non-empty. If all
    parameters were skipped (ensemble too small, BELOW direction, etc.), the caller
    must NOT call this function — an unevaluated cycle must not resolve active alerts.

    **Parameter-aware resolution:** Only resolves alerts for danger levels where ALL
    configured parameters were evaluated. If a danger level has thresholds for both
    discharge and water_level, but only water_level was evaluated, the alert is
    preserved — we cannot confirm non-exceedance for the unevaluated parameter.

    **model_ids union:** When multiple parameters exceed the same danger level with
    different contributing models, model_ids is the union across all parameters. This
    ensures the audit trail captures all models that contributed to the alert decision,
    regardless of which parameter triggered it.

    **Strategy invariant:** All parameters for a given station resolve to the same
    effective strategy because `_resolve_strategy_and_filter` uses a single
    `config.alert_model_strategy` for the entire cycle. `exceeded_strategy` uses
    first-write-wins per danger level, which is safe under this invariant. If v1
    introduces per-parameter strategy selection, this must be revisited.
    """
    now = clock()

    # Build mapping: danger_level → set of configured parameters (from thresholds)
    level_parameters: dict[str, set[ForecastParameter]] = {}
    for t in thresholds:
        if t.parameter in _FORECAST_PARAMETERS:
            level_parameters.setdefault(t.danger_level, set()).add(
                cast(ForecastParameter, t.parameter)
            )

    # Accumulate model_ids per danger level as a union across parameters.
    # A DL3 alert triggered by discharge (models A, B) and water_level (models B, C)
    # stores model_ids = (A, B, C) — the full set of contributing models.
    exceeded_models: dict[str, set[ModelId]] = {}
    exceeded_strategy: dict[str, AlertModelStrategy] = {}

    for result in results:
        if result.exceeded:
            if result.danger_level not in exceeded_models:
                exceeded_models[result.danger_level] = set()
                exceeded_strategy[result.danger_level] = result.strategy
            exceeded_models[result.danger_level].update(result.model_ids)

    for level, model_id_set in exceeded_models.items():
        alert_store.upsert_alert(Alert(
            ...,
            model_ids=tuple(sorted(model_id_set, key=str)),
            alert_model_strategy=exceeded_strategy[level],
        ))

    # Resolve active forecast alerts for danger levels that are:
    # (a) no longer exceeded by ANY parameter, AND
    # (b) fully evaluated — ALL configured parameters for that level were evaluated.
    # Condition (b) prevents false resolution when a model partially fails (e.g.,
    # discharge model crashes but water_level succeeds — active discharge alert preserved).
    exceeded_levels = set(exceeded_models.keys())
    active = alert_store.fetch_active_alerts(
        station_id=station_id, source=AlertSource.FORECAST,
    )
    for alert in active:
        if alert.alert_level in exceeded_levels:
            continue  # still exceeded — do not resolve
        configured = level_parameters.get(alert.alert_level, set())
        if configured and not configured.issubset(evaluated_parameters):
            # Not all configured parameters were evaluated — cannot confirm
            # non-exceedance. Preserve the alert to avoid false resolution.
            log.debug("alert.resolution_deferred", station_id=station_id,
                      alert_level=alert.alert_level,
                      missing=sorted(configured - evaluated_parameters))
            continue
        alert_store.resolve_alert(alert.id)
```

**Duration hysteresis (`min_trigger_duration` / `min_resolve_duration`):** Deferred to v1.
These fields exist on `DangerLevelDefinition` but require cross-cycle state tracking
(timestamps of first exceedance / first non-exceedance). v0 triggers and resolves alerts
within a single cycle — acceptable for initial deployment where forecaster review (Flow 3,
also v1) provides the human judgment layer. Phase 1A must annotate step 1.12 in
`architecture-context.md` as a v0 deviation (hysteresis deferred). When hysteresis is
implemented, it will be added to `_process_results` with a `first_detected_at` lookup
against the existing `Alert.first_detected_at` field (a new `last_exceeded_at` column
on `alerts` will also be needed — it does not exist today).

**Stale model output:** If a model stops producing forecasts, its entries are absent from
`all_ensembles`. The convergence service processes only the models present — no special
handling needed. If the absent model was the only one exceeding a threshold, the alert
will be resolved in the next cycle by the resolution logic above. A separate concern
(detecting that a model failed to produce output) belongs to Flow 4 (pipeline monitoring),
not Phase C.

**Scope:** Convergence service with strategy dispatch and cascading fallback.
**Verification:** `uv run pytest tests/unit/services/test_alert_checker.py -x -q`
**Dependencies:** Phase 0, Phase 2.

#### 3B. `src/sapphire_flow/store/alert_store.py` — update for new fields

Update `PgAlertStore.upsert_alert` to write `model_ids` (JSONB) and
`alert_model_strategy` (text) columns. Serialization round-trips:
- `model_ids: tuple[ModelId, ...]` → `list[str]` JSON on write; `list[str]` JSON → `tuple(ModelId(...) for ...)` on read
- `alert_model_strategy: AlertModelStrategy | None` → `.value` (str) on write, `None` → `NULL`; `str` → `AlertModelStrategy(value)` on read, `NULL` → `None`

Update `PgAlertStore.fetch_active_alerts` and `fetch_alert_history` to read the new columns.

**Scope:** Store implementation changes for new columns.
**Verification:** `uv run pytest tests/integration/store/test_alert_store.py -x -q`
**Dependencies:** 0D.

#### 3C. `tests/fakes/fake_stores.py` — update `FakeAlertStore`

Update fake to handle `model_ids` and `alert_model_strategy` fields on `Alert`.

**Scope:** Fake update only.
**Verification:** `uv run pytest tests/unit/ -x -q`
**Dependencies:** 0D.

### Phase 4 — Tests

#### 4A. `tests/unit/services/test_alert_strategy.py` — strategy unit tests

1. `TestPrimaryModelStrategy`:
   - `test_selects_lowest_priority_model` — 3 models with priorities 0, 1, 2; asserts ensemble from priority-0 model used
   - `test_deterministic_tie_breaking` — 2 models both priority 0; asserts the model with lower `str(model_id)` wins, deterministically
   - `test_warns_when_priorities_missing` — empty priorities dict with 2 models → logs `alert.priorities_not_found` warning
   - `test_single_model_returns_that_model` — single model, any priority
   - `test_empty_ensembles_returns_empty` — no models, no alerts
   - `test_model_ids_contains_only_primary` — auditability check

2. `TestPooledEnsembleStrategy`:
   - `test_pools_all_members` — 2 models × 21 members = 42 pooled members (renumbered member IDs)
   - `test_exceedance_probability_from_pooled_set` — known ensemble values, verify probability
   - `test_model_ids_contains_all_models` — auditability check
   - `test_rejects_mixed_representations` — one MEMBERS + one QUANTILES → `ValueError` (defensive; convergence service should prevent this)

3. No `TestConsensusVotingStrategy` in v0 — class does not exist until v1 (same as BMA).
4. No `TestBmaStrategy` in v0 — class does not exist until v1.

#### 4B. `tests/unit/services/test_alert_checker.py` — convergence + fallback tests

1. `TestResolveStrategyAndFilter`:
   - `test_bma_falls_back_to_pooled` — even with multiple models, bma → pooled (no BMA implementation)
   - `test_consensus_falls_back_to_pooled` — even with multiple models, consensus → pooled (no consensus implementation in v0)
   - `test_pooled_falls_back_to_primary_with_single_model`
   - `test_consensus_falls_back_to_primary_with_single_model` — single model, n_models ≤ 1 guard → primary
   - `test_pooled_falls_back_to_primary_with_mixed_representations` — 2 MEMBERS + 1 QUANTILES models, `pooled` configured → falls back to `primary`; returned ensemble contains only the primary model
   - `test_bma_falls_back_to_primary_with_mixed_representations` — mixed reps cascade: bma → primary (pooled also requires homogeneous MEMBERS); returned ensemble contains only the primary model
   - `test_consensus_falls_back_to_primary_with_mixed_representations` — mixed reps: consensus → primary
   - `test_primary_always_works`
   - `test_primary_returns_single_model_ensemble` — returned ensemble dict has exactly one entry (the primary model)
   - `test_fallback_warning_logged_once` — BMA fallback warning emitted on first call, not repeated on subsequent calls. **Test isolation:** clear `_STRATEGY_FALLBACK_WARNED` in fixture teardown to prevent cross-test pollution.
   - `test_fallback_warning_distinguishes_actual` — BMA→pooled and BMA→primary (mixed reps) log separately

2. `TestCheckStationAlerts`:
   - `test_multi_parameter_dispatch` — discharge + water_level ensembles from 2 models; alerts checked per parameter
   - `test_upsert_called_for_exceeded` — exceeded result → `alert_store.upsert_alert` called with correct `model_ids`
   - `test_skipped_evaluation_preserves_active_alerts` — all parameters skipped (ensemble too small) with active DL3 alert → `resolve_alert` NOT called, active alert preserved
   - `test_resolve_called_for_not_exceeded` — previously-raised alert resolved when no longer exceeded
   - `test_skipped_when_forecast_alerts_disabled` — `enable_forecast_alerts=False` → no checking
   - `test_skipped_when_threshold_check_mode_not_raw` — `threshold_check_mode="published"` → early return with `alert.check_mode_rejected` error log
   - `test_skipped_when_effective_ensemble_too_small` — after strategy resolves to primary, single model with MEMBERS below `min_operational_ensemble_size` → skip with `alert.ensemble_skipped` warning
   - `test_skipped_when_quantile_levels_too_few` — QUANTILES total below `min_operational_quantile_levels` → skip with `alert.ensemble_skipped` warning
   - `test_quantile_ensemble_not_skipped_at_default_member_threshold` — 9-quantile model is NOT skipped (would be if using the MEMBERS threshold of 20)
   - `test_below_direction_danger_levels_filtered` — danger levels with `ThresholdDirection.BELOW` are skipped with `alert.direction_skipped` warning

3. `TestProcessResults` (cross-parameter resolution):
   - `test_exceeded_result_upserts_alert` — `ExceedanceResult.exceeded=True` → `alert_store.upsert_alert` called with correct `model_ids` and `alert_model_strategy`
   - `test_previously_raised_alert_resolved_when_not_exceeded` — active alert exists for danger_level_3, current cycle shows no exceedance for that level across all evaluated parameters → `resolve_alert` called
   - `test_no_resolution_when_no_active_alerts` — no active alerts, not-exceeded results → no store calls
   - `test_cross_parameter_no_false_resolution` — **critical**: discharge does not exceed DL3 but water_level does; active DL3 alert must NOT be resolved. Verifies that resolution waits for all parameters before deciding.
   - `test_partial_model_failure_preserves_alert` — **critical**: station has thresholds for discharge + water_level at DL3; only water_level is evaluated (discharge model absent from ensembles); active DL3 alert must NOT be resolved because discharge was not evaluated. Verifies parameter-aware resolution.
   - `test_model_ids_union_across_parameters` — **critical**: discharge exceeds DL3 via models (A, B), water_level exceeds DL3 via models (B, C) → single upsert with `model_ids = (A, B, C)` (sorted union), NOT two separate upserts
   - `test_model_ids_sorted_deterministically` — model_ids tuple is sorted by `str(model_id)` regardless of parameter iteration order
   - `test_alert_level_danger_level_field_mapping` — verifies `Alert.alert_level` and `ExceedanceResult.danger_level` use the same `DangerLevelDefinition.name` values end-to-end

#### 4C. `tests/unit/config/test_deployment.py` — config validation tests

1. `test_min_ensemble_size_validation` — `< 1` raises `ConfigurationError`
2. `test_min_quantile_levels_validation` — `< 7` raises `ConfigurationError`
3. `test_alert_model_strategy_from_toml` — round-trip from TOML string to enum
4. `test_enable_alert_flags_already_exist` — verify existing `enable_*_alerts` fields are accessible on `DeploymentConfig`

---

## File-Level Change Summary

| File | Change type | Phase |
|---|---|---|
| `src/sapphire_flow/types/enums.py` | Add `AlertModelStrategy` enum | 0A |
| `src/sapphire_flow/config/deployment.py` | Add `alert_model_strategy`, `min_operational_ensemble_size`, `min_operational_quantile_levels` fields | 0B |
| `src/sapphire_flow/exceptions.py` | Add `ConfigurationError` (if absent) | 0B |
| `config.toml` | Add alert strategy config keys | 0B |
| `docs/spec/config-reference.toml` | Add alert strategy config documentation | 0B |
| `src/sapphire_flow/types/domain.py` | Add `model_ids`, `strategy` to `ExceedanceResult` | 0C |
| `src/sapphire_flow/types/alert.py` | Add `model_ids`, `alert_model_strategy` to `Alert` | 0D |
| `src/sapphire_flow/db/metadata.py` | Add `model_ids`, `alert_model_strategy` columns to `alerts` table | 0D |
| `alembic/versions/NNNN_alert_model_fields.py` | Migration: add columns to `alerts` | 0D |
| `src/sapphire_flow/types/ensemble.py` | Add optional `model_id` and `member_count` property to `ForecastEnsemble` | 0E |
| `docs/spec/types-and-protocols.md` | Update `AlertModelStrategy`, `ExceedanceResult`, `Alert`, `ForecastEnsemble`, `DeploymentConfig` | 0A–0E |
| `docs/architecture-context.md` | Update Flow 1 Phase C (1.11–1.12), alerts table schema | 1A |
| `docs/v0-scope.md` | Add §A8d (multi-model alert strategy), add §I3 (priority dual semantics risk) | 1B |
| `docs/standards/orchestration.md` | Update Phase B accumulation + Phase C convergence line | 1C |
| `docs/standards/wmo.md` | Add multi-model alert reference | 1D |
| `src/sapphire_flow/types/domain.py` | Add `ForecastParameter` type alias | 2A |
| `src/sapphire_flow/protocols/alert_strategy.py` | New: `ModelAlertStrategy` Protocol | 2A |
| `src/sapphire_flow/services/alert_strategy.py` | New: `PrimaryModelStrategy`, `PooledEnsembleStrategy` (no BMA or consensus in v0) | 2B–2C |
| `src/sapphire_flow/services/alert_checker.py` | New: convergence service with strategy dispatch | 3A |
| `src/sapphire_flow/store/alert_store.py` | Update for `model_ids`, `alert_model_strategy` columns | 3B |
| `tests/fakes/fake_stores.py` | Update `FakeAlertStore` for new fields | 3C |
| `tests/unit/services/test_alert_strategy.py` | New: strategy unit tests | 4A |
| `tests/unit/services/test_alert_checker.py` | New: convergence + fallback tests | 4B |
| `tests/unit/config/test_deployment.py` | New: config validation tests | 4C |

---

## Dependency Graph

```json
{
  "phases": [
    {
      "id": "phase-0-enum",
      "tasks": ["0a"],
      "parallel": false
    },
    {
      "id": "phase-0-types",
      "tasks": ["0b", "0c", "0d", "0e"],
      "parallel": true,
      "depends_on": ["phase-0-enum"]
    },
    {
      "id": "phase-1",
      "tasks": ["1a", "1b", "1c", "1d"],
      "parallel": true
    },
    {
      "id": "phase-2-protocol",
      "tasks": ["2a"],
      "depends_on": ["phase-0-types"]
    },
    {
      "id": "phase-2-impl",
      "tasks": ["2b", "2c"],
      "parallel": true,
      "depends_on": ["phase-2-protocol"]
    },
    {
      "id": "phase-3-convergence",
      "tasks": ["3a"],
      "depends_on": ["phase-0-types", "phase-2-impl"]
    },
    {
      "id": "phase-3-stores",
      "tasks": ["3b", "3c"],
      "parallel": true,
      "depends_on": ["phase-0-types"]
    },
    {
      "id": "phase-4",
      "tasks": ["4a", "4b", "4c"],
      "parallel": true,
      "depends_on": ["phase-2-impl", "phase-3-convergence", "phase-3-stores"]
    }
  ]
}
```

Phase 0 and Phase 1 can proceed fully in parallel. Phase 2 depends on Phase 0 (types).
Phase 3 is split: 3a (convergence) depends on Phase 0 + Phase 2; 3b/3c (store updates)
depend only on Phase 0 (no strategy code needed). Phase 4 depends on all prior phases.

---

## Guardrails

- `uv run pytest tests/ -x -q` after each phase
- `uv run pyright --strict src/sapphire_flow/` after type changes (Phase 0, 2)
- After Phase 0D: verify `alembic upgrade head` succeeds and `alembic downgrade -1` reverses cleanly
- After Phase 2: verify `isinstance(PrimaryModelStrategy(), ModelAlertStrategy)` passes
- After Phase 2C: verify `PooledEnsembleStrategy.evaluate()` raises `ValueError` on non-MEMBERS representation (defensive — convergence service should prevent this)
- After Phase 3: verify cascading fallback with single-model station produces identical exceedance results to running `PrimaryModelStrategy` directly
- After Phase 3: verify mixed MEMBERS+QUANTILES representations cascade to `primary` via `_resolve_strategy_and_filter`, and that the returned ensemble contains only the primary model
- After Phase 3: verify ensemble size check runs on the effective ensemble (post-strategy), not the full input set
- After Phase 3: verify `_process_results` resolves previously-raised alerts when danger level no longer exceeded by ANY parameter AND all configured parameters were evaluated (parameter-aware resolution)
- After Phase 3: verify multi-parameter stations do not falsely resolve alerts — discharge processing must not resolve water_level alerts
- After Phase 3: verify partial model failure (one parameter absent from ensembles) preserves active alerts for danger levels that depend on the absent parameter
- After Phase 3: verify `_process_results` upserts once per danger level (not once per result) with `model_ids` as the sorted union across all exceeding parameters
- After Phase 3: verify `_check_station` skips `_process_results` when no parameter was evaluated (all skipped) — active alerts must be preserved, not resolved
- After Phase 3: verify `check_station_alerts` emits `alert.completed` event with `duration_ms` rounded to 1 decimal (mandatory for all Flow 1 steps per v0-scope.md §D6)
- After Phase 3: verify BELOW-direction danger levels are filtered out with `alert.direction_skipped` warning
- Version bump: `uv run bump-my-version bump patch` before committing (per CLAUDE.md)
- Doc updates (Phase 1) must be reviewed for consistency with code changes (Phase 0)

---

## Implementation Timing

| Phase | When | What |
|---|---|---|
| 0 (types + config) | **Now** — pre-Phase 8 | Types, config, migrations. No behavioral change. |
| 1 (docs) | **Now** — parallel with Phase 0 | Architecture documentation. |
| 2A–2C (strategies) | **Phase 8** — with forecast service | Primary + Pooled strategy classes land together. Pure computation, no external deps. Deferred from v0-scope Phase 4 to Phase 8 because there is no caller until the forecast cycle is implemented. 2D (Consensus) and 2E (BMA) are deferred — no code in v0. |
| BMA (new class + impl) | **v1** — with weight training pipeline | Tied to Flow 8/10. No stub in v0; `_resolve_strategy_and_filter` cascades to pooled. |
| 3 (convergence) | **Phase 8** — with forecast service | Core dispatch logic. |
| 4 (tests) | **With each implementation phase** | Tests land alongside code. |

**Runtime activation vs code delivery:** Primary and Pooled strategy code ships at
Phase 8. BMA and Consensus are deferred entirely (no class, no stub) — `_resolve_strategy_and_filter`
cascades them to the next available strategy. Pooled activates at v0b (second model per
station with homogeneous MEMBERS representation). Primary is the only strategy exercised
until then.

---

## Impact on Plan 005

Plan 005 is **ARCHIVED**. Its Phase 2 (alert routing) has already been marked as
"moved to plan 010" in the archived version. No further updates needed.

---

## Open Items

1. ~~**Priority information in strategy dispatch**~~ **Resolved:** Option (a) adopted —
   `priorities: dict[ModelId, int]` added to `ModelAlertStrategy.evaluate()` Protocol.
   See Phase 2A.

2. **BMA weight storage** — Where do trained BMA weights live? Options: (a) new
   `bma_weights` table keyed on `(station_id, model_id)`, (b) stored as a model artifact
   attribute, (c) stored in `skill_scores` as a derived metric. Defer to the BMA
   implementation plan (v1).

3. **BMA weight training trigger** — Should BMA weights be recomputed as part of Flow 8
   (skill computation, runs after every hindcast) or Flow 10 (skill recomputation, runs
   on schedule)? Flow 8 is the natural fit since it already processes hindcast verification
   data per model. Defer to v1.

4. ~~**Mixed representations**~~ **Resolved:** Pooling requires homogeneous MEMBERS
   representation. Mixed MEMBERS + QUANTILES ensembles are structurally incompatible
   (different DataFrame column schemas). `_resolve_strategy_and_filter` detects mixed
   representations, falls back to `primary`, and returns only the primary model's ensemble
   so that the size check evaluates the effective ensemble. See Phase 3A.

   **Pooled ensemble member count normalization** — When pooling, should we resample each
   model's ensemble to equal member count before concatenation? Without normalization, a
   model with 51 members dominates one with 21 members. EFAS does not normalize (implicit
   count-weighting). Recommend: no normalization in v0b (EFAS precedent), revisit if
   model member counts diverge significantly.

5. **Observation alerts** — This plan focuses on forecast alerts (Flow 1 Phase C).
   Observation alerts (Flow 2 steps 2.8–2.10) are single-source (one observation value vs
   threshold) and do not have a multi-model dimension. No changes needed for observation
   alerts. The `model_ids` field on `Alert` is `()` for observation-source alerts.

6. **`check_thresholds` formalization** — The `check_thresholds` function is currently only
   an example pattern in `types-and-protocols.md` (~line 2324), not a Protocol or implemented
   function. This plan's strategies internalize threshold checking. The standalone
   `check_thresholds` function may still be useful as a shared utility called by multiple
   strategies. Decide during implementation.

7. ~~**`priority` dual semantics**~~ **Resolved as tracked v1 risk (§I3).** Phase 1A
   documents the dual semantics in `model_assignments.priority` and
   `group_model_assignments.priority`. Phase 1B adds §I3 to `v0-scope.md` with the
   recommended v1 action: add `alert_priority: int | None` to decouple the two concerns.
   For v0 the semantics are consistent (priority 0 = run first = use for alerts).
   The architecture's convention (linear regression = priority 0) means the simplest model
   drives alerts — this is intentional for v0 where linear regression is the only model,
   but must be revisited when better-skill models are added.

8. ~~**Flow 3 re-check path**~~ **Noted:** Flow 3 step 3.5 re-triggers threshold logic
   on published forecasts. After forecaster review, a single published forecast per station
   feeds `check_station_alerts` as a single-model dict, which correctly cascades to
   `PrimaryModelStrategy`. Flow 3 is deferred to v1 — no action needed in v0, but the
   caller construction path should be documented when Flow 3 is designed. **v1 alignment
   note:** When Flow 3 re-checks thresholds on a published forecast, it should update
   `model_ids` and `alert_model_strategy` on the resulting alert — stale values from the
   raw forecast's Phase C run must be overwritten.

9. **Stale alert expiry** — If a station's models consistently produce undersized ensembles
   (below `min_operational_ensemble_size`), `evaluated_parameters` stays empty across
   cycles, `_process_results` is never called, and active alerts persist indefinitely.
   This is the correct per-cycle behavior (an unevaluated cycle must not resolve alerts),
   but over many cycles it means a DL3 flood alert could become permanently stale. Flow 4
   (pipeline monitoring, deferred in v0) would detect the model failure via a separate
   alert channel, but the stale forecast alert itself has no automatic expiry. **v1 action:**
   Add a background sweep that resolves forecast alerts older than N consecutive cycles
   without successful evaluation, using a `stale_resolved` resolution reason. This requires
   a `last_evaluated_at` timestamp on the `alerts` table (not present today).
