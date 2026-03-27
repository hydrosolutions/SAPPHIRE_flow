---
status: DRAFT
created: 2026-03-27
scope: types + config + db schema + services + docs
depends_on: []  # no blocking dependencies; plan 005 Phase 2A depends on THIS plan
---

# 010 — Multi-Model Alert Strategy

## Problem

Flow 1 Phase C (steps 1.11–1.13) checks forecast ensembles against alert thresholds
and raises/resolves alerts. The architecture supports multiple active models per station
simultaneously (`types-and-protocols.md` line 691: "All model types can be active for
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
| `primary` | Forecast selection | Use the ensemble from the highest-priority model (`ModelAssignment.priority == 0`) | Delft-FEWS Forecast Manager |
| `pooled` | Forecast combination | Concatenate all models' ensemble members into a grand ensemble; compute exceedance probability over pooled set | EFAS (51 ECMWF + 20 COSMO-LEPS members) |
| `bma` | Forecast combination | Bayesian Model Averaging — skill-weighted combination with per-model bias correction, producing calibrated quantiles | Delft-FEWS BMA module, WMO-1091 §9.1.1 |
| `consensus` | Decision combination | Check thresholds per model independently; trigger alert when fraction of models agreeing ≥ configurable threshold | Novel — intuitive for stakeholder communication |

**`primary`**, **`pooled`**, and **`bma`** are *forecast combination* strategies: they
produce a single combined ensemble (or select one), then standard `check_thresholds` runs
on it. **`consensus`** is a *decision combination* strategy: it runs `check_thresholds`
per model, then aggregates the binary outcomes.

**Cascading fallback:** The deployment configures a *preferred* strategy. At runtime, the
system degrades gracefully:
- `bma` → falls back to `pooled` if BMA weights are unavailable for the station
- `pooled` / `consensus` → falls back to `primary` if only one model is active
- `primary` → always works (selects priority-0 model)

This ensures the configured default (e.g., `bma`) works everywhere without per-station
overrides, even during cold-start or for stations with a single model.

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
alert_model_strategy: AlertModelStrategy = AlertModelStrategy.BMA
consensus_model_fraction: float = 0.5  # fraction of models that must agree (consensus strategy only)
```

Validation in `@model_validator`:
- `consensus_model_fraction` must be in `(0.0, 1.0]`
- Warn (log, don't fail) if `alert_model_strategy` is `consensus` and `consensus_model_fraction` is at default — nudge deployers to set it explicitly

Also update:
- `config.toml`: add `alert_model_strategy = "bma"` and `consensus_model_fraction = 0.5`
- `docs/spec/config-reference.toml`: add both fields with documentation comments
- `docs/spec/types-and-protocols.md` `DeploymentConfig` section (~line 2224)

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
    model_ids: list[ModelId]           # NEW — models that contributed to this result
    strategy: AlertModelStrategy       # NEW — which strategy produced this result
```

For `primary`: `model_ids = [primary_model_id]`. For `pooled`/`bma`: all contributing
model IDs. For `consensus`: model IDs that voted "exceeded" (not all models — only the
agreeing ones, so the list length relative to total active models conveys agreement level).

Also update `docs/spec/types-and-protocols.md` `ExceedanceResult` definition (~line 597).

**Scope:** Type change only. Callers updated in Phase 2.
**Verification:** `uv run pyright --strict src/sapphire_flow/types/domain.py`
**Dependencies:** 0A.

#### 0D. `src/sapphire_flow/types/alert.py` + `db/metadata.py` — `Alert` model traceability

Add to `Alert` dataclass:

```python
model_ids: list[ModelId]               # NEW — models that contributed to the alert trigger
alert_model_strategy: AlertModelStrategy  # NEW — strategy that produced the decision
```

Add to `alerts` table in `metadata.py`:

```python
sa.Column("model_ids", JSONB, nullable=False, server_default="[]"),
sa.Column("alert_model_strategy", sa.Text, nullable=True),  # NULL for observation/pipeline alerts
```

Create Alembic migration adding both columns to the `alerts` table.

Also update:
- `docs/spec/types-and-protocols.md` `Alert` definition (~line 743)
- `docs/architecture-context.md` alerts table schema

**Note:** The `AlertStore.upsert_alert` upsert key remains `(station_id, alert_level, source)` —
we do not add a model dimension to the key. A station+danger_level has one active alert
regardless of how many models contributed. The `model_ids` field is for auditability,
not for keying.

**Scope:** Type + schema change. Store implementations updated in Phase 2.
**Verification:** `uv run pytest tests/ -x -q` after migration
**Dependencies:** 0A.

#### 0E. `src/sapphire_flow/types/ensemble.py` — thread `model_id` through `ForecastEnsemble`

Add optional field:

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastEnsemble:
    # ... existing fields ...
    model_id: ModelId | None = None    # NEW — set during forecast cycle; None for test/legacy
```

This allows convergence code to identify which model produced each ensemble without
requiring a separate tracking structure.

Also update `docs/spec/types-and-protocols.md` `ForecastEnsemble` definition.

**Scope:** Optional field addition. Existing code unaffected (default None).
**Verification:** `uv run pytest tests/ -x -q`
**Dependencies:** None.

### Phase 1 — Architecture Documentation

#### 1A. `docs/architecture-context.md` — Flow 1 Phase C

Update the step table annotations for 1.11 and 1.12 (~lines 112–113) to document:
1. Multi-model strategy dispatch: Phase C receives all models' ensembles per station,
   dispatches to the configured `AlertModelStrategy`
2. Strategy descriptions (primary/pooled/bma/consensus) — brief, with cross-reference
   to this plan for details
3. Cascading fallback behavior
4. `model_ids` traceability on `ExceedanceResult` and `Alert`

Update the sequencing block (~line 163) Phase C description:
- "Phase C runs after all Phase B units complete" — add: "Phase C collects all
  models' forecast ensembles per station and applies the configured `alert_model_strategy`
  to determine exceedance per danger level."

Update the alerts table schema block to include `model_ids` and `alert_model_strategy`.

**Scope:** Doc-only.
**Verification:** Manual review.
**Dependencies:** None (can proceed in parallel with Phase 0).

#### 1B. `docs/v0-scope.md` — §A8

Add new subsection **§A8d. Multi-model alert strategy**:

> **Full design**: Four strategies (primary, pooled, bma, consensus) selectable per
> deployment via `alert_model_strategy` config. BMA is the recommended default for
> mature deployments. Cascading fallback: bma → pooled → primary.
>
> **v0**: `alert_model_strategy` config field exists with default `bma`. Runtime
> behavior is `primary` (cascading fallback — no BMA weights exist, and v0 stations
> typically have one active model). The strategy enum, config field, convergence
> structure, and type traceability (`model_ids` on `ExceedanceResult` and `Alert`) are
> implemented from day one.
>
> **v0b**: `pooled` strategy implemented when second model is onboarded per station.
>
> **v1**: `bma` strategy implemented with weight training pipeline (linked to
> Flow 8/10 skill recomputation). `consensus` strategy implemented if stakeholder
> demand exists.

**Scope:** Doc-only.
**Verification:** Manual review.
**Dependencies:** None.

#### 1C. `docs/standards/orchestration.md` — Phase C sketch

Update the fan-out sketch (~line 78) to show convergence explicitly:

```python
# Phase C — alert checking (after all Phase B units complete)
if enable_forecast_alerts:
    # Collect all models' ensembles per station
    all_ensembles: dict[StationId, dict[ModelId, dict[str, ForecastEnsemble]]]
    all_ensembles = collect_ensembles(group_results, station_results)

    # Dispatch to configured strategy per station
    for station_id, model_ensembles in all_ensembles.items():
        check_station_alerts(
            station_id, model_ensembles, thresholds, config
        )  # strategy dispatch inside
```

Replace the opaque `check_thresholds(all_results)  # converge` line.

**Scope:** Doc-only.
**Verification:** Manual review.
**Dependencies:** None.

#### 1D. `docs/standards/wmo.md` — multi-model alert reference

Add a paragraph under §3 "Alert and warning system" noting:
- WMO-1091 §10: multiple forecasting systems give additional probability information
- WMO-1091 §9.1.1: per-model bias correction required before combination
- EFAS precedent: pooled ensemble exceedance probability with configurable thresholds
- Delft-FEWS BMA: formal skill-weighted combination
- Cross-reference to this plan for SAPPHIRE's four-strategy approach

**Scope:** Doc-only.
**Verification:** Manual review.
**Dependencies:** None.

### Phase 2 — Strategy Protocol and Primary Implementation (v0)

#### 2A. `src/sapphire_flow/protocols/alert_strategy.py` — Strategy Protocol

```python
@runtime_checkable
class ModelAlertStrategy(Protocol):
    def evaluate(
        self,
        station_id: StationId,
        parameter: str,
        model_ensembles: dict[ModelId, ForecastEnsemble],
        thresholds: list[StationThreshold],
        danger_levels: list[DangerLevelDefinition],
    ) -> list[ExceedanceResult]:
        """Evaluate alert thresholds using the strategy's combination method.

        Returns one ExceedanceResult per (danger_level, parameter) pair where
        the station has a defined threshold.
        """
        ...
```

All four strategies implement this Protocol. The caller does not need to know whether
combination happens at the forecast level or decision level.

Also add to `docs/spec/types-and-protocols.md` Protocol section.

**Scope:** Protocol definition only.
**Verification:** `uv run pyright --strict src/sapphire_flow/protocols/alert_strategy.py`
**Dependencies:** 0A, 0C.

#### 2B. `src/sapphire_flow/services/alert_strategy.py` — `PrimaryModelStrategy`

Select the ensemble from the model with the lowest `priority` value (0 = primary) among
active assignments. Compute exceedance probability per danger level using existing
`check_thresholds` logic. Set `model_ids = [selected_model_id]`.

If no model has priority 0, select the lowest available priority. If `model_ensembles`
is empty, return empty list (no alerts possible).

```python
class PrimaryModelStrategy:
    def evaluate(
        self,
        station_id: StationId,
        parameter: str,
        model_ensembles: dict[ModelId, ForecastEnsemble],
        thresholds: list[StationThreshold],
        danger_levels: list[DangerLevelDefinition],
    ) -> list[ExceedanceResult]:
        if not model_ensembles:
            return []
        # Select primary model (lowest priority value)
        primary_model_id = self._select_primary(model_ensembles, station_id)
        ensemble = model_ensembles[primary_model_id]
        param_thresholds = [t for t in thresholds if t.parameter == parameter]
        return [
            ExceedanceResult(
                ...,
                model_ids=[primary_model_id],
                strategy=AlertModelStrategy.PRIMARY,
            )
            for dl in danger_levels
            if (threshold := _find_threshold(param_thresholds, dl)) is not None
        ]
```

**Note:** `_select_primary` requires knowing model priorities. The `model_ensembles` dict
is keyed by `ModelId`, but priority lives on `ModelAssignment`. The convergence service
(Phase 3) must pass priority information alongside ensembles, or the strategy must receive
a `priorities: dict[ModelId, int]` parameter. **Open item** — see Open Items §1.

**Scope:** Primary strategy implementation only.
**Verification:** `uv run pytest tests/unit/services/test_alert_strategy.py -x -q`
**Dependencies:** 2A.

#### 2C. `src/sapphire_flow/services/alert_strategy.py` — `PooledEnsembleStrategy`

Concatenate all models' ensemble members into one grand ensemble DataFrame. Recompute
exceedance probability over the pooled set. Set `model_ids = list(model_ensembles.keys())`.

```python
class PooledEnsembleStrategy:
    def evaluate(self, ...) -> list[ExceedanceResult]:
        if len(model_ensembles) < 2:
            return PrimaryModelStrategy().evaluate(...)  # fallback
        pooled = _pool_ensembles(model_ensembles)
        # ... check_thresholds on pooled ensemble ...
```

**WMO constraint:** Per WMO-1091 §9.1.1, per-model bias correction must happen before
pooling. This is the responsibility of step 1.9 (`ForecastPostProcessor`), not this
strategy. The strategy assumes ensembles are already bias-corrected.

**Scope:** Pooled strategy implementation.
**Verification:** `uv run pytest tests/unit/services/test_alert_strategy.py -x -q`
**Dependencies:** 2A.
**Implementation timing:** v0b (when second model is onboarded per station).

#### 2D. `src/sapphire_flow/services/alert_strategy.py` — `ConsensusVotingStrategy`

Run `check_thresholds` independently per model. For each danger level, count fraction of
models whose `exceeded == True`. Trigger alert if fraction ≥ `consensus_model_fraction`.

```python
class ConsensusVotingStrategy:
    def __init__(self, consensus_fraction: float) -> None:
        self._fraction = consensus_fraction

    def evaluate(self, ...) -> list[ExceedanceResult]:
        if len(model_ensembles) < 2:
            return PrimaryModelStrategy().evaluate(...)  # fallback
        per_model_results: dict[ModelId, list[ExceedanceResult]] = {}
        for model_id, ensemble in model_ensembles.items():
            per_model_results[model_id] = _check_single(ensemble, thresholds, danger_levels)
        # Vote per danger level
        ...
```

`model_ids` on the result contains only the models that voted "exceeded" — the list
length relative to total models conveys agreement level.

**Scope:** Consensus strategy implementation.
**Verification:** `uv run pytest tests/unit/services/test_alert_strategy.py -x -q`
**Dependencies:** 2A.
**Implementation timing:** v1+ (lower priority given BMA covers the use case more rigorously).

#### 2E. `src/sapphire_flow/services/alert_strategy.py` — `BmaStrategy` (stub)

v0 stub that raises `NotImplementedError` — the cascading fallback in Phase 3 ensures
this is never called directly when weights are unavailable.

v1 implementation: BMA weight estimation from hindcast verification data (Flow 8/10),
producing `BmaWeights` per `(station_id, model_id)`. The strategy computes a weighted
mixture distribution and derives exceedance probability from it.

```python
class BmaStrategy:
    def evaluate(self, ...) -> list[ExceedanceResult]:
        # v0: stub — cascading fallback prevents reaching here without weights
        raise NotImplementedError(
            "BMA strategy requires trained weights. "
            "Cascading fallback should have selected 'pooled' or 'primary'."
        )
```

**BMA weight training** is out of scope for this plan — it belongs in a future plan
tied to Flow 8/10 (skill recomputation), which already computes per-model verification
metrics from hindcast data. The weights are a function of the same data.

**Scope:** Stub only. Full implementation deferred.
**Verification:** `uv run pyright --strict src/sapphire_flow/services/alert_strategy.py`
**Dependencies:** 2A.
**Implementation timing:** v1.

### Phase 3 — Convergence Service

#### 3A. `src/sapphire_flow/services/alert_checker.py` — strategy dispatch + cascading fallback

The convergence service is the entry point for Phase C. It:

1. Receives all models' forecast ensembles for all stations (collected from Phase B fan-out)
2. Groups by `(station_id, parameter)`
3. Resolves the effective strategy per station (cascading fallback)
4. Dispatches to the strategy implementation
5. Feeds `ExceedanceResult`s to `AlertStore.upsert_alert` / `resolve_alert`

```python
def check_station_alerts(
    station_id: StationId,
    model_ensembles: dict[ModelId, dict[str, ForecastEnsemble]],
    thresholds: list[StationThreshold],
    danger_levels: list[DangerLevelDefinition],
    config: DeploymentConfig,
    alert_store: AlertStore,
    bma_weights: dict[ModelId, BmaWeights] | None,
    clock: Callable[[], UtcDatetime],
) -> None:
    for parameter in _unique_parameters(model_ensembles):
        param_ensembles = {
            mid: ens[parameter]
            for mid, ens in model_ensembles.items()
            if parameter in ens
        }
        strategy = _resolve_strategy(
            preferred=config.alert_model_strategy,
            n_models=len(param_ensembles),
            has_bma_weights=bma_weights is not None,
            consensus_fraction=config.consensus_model_fraction,
        )
        results = strategy.evaluate(
            station_id, parameter, param_ensembles, thresholds, danger_levels,
        )
        _process_results(results, alert_store, clock)


def _resolve_strategy(
    preferred: AlertModelStrategy,
    n_models: int,
    has_bma_weights: bool,
    consensus_fraction: float,
) -> ModelAlertStrategy:
    """Cascading fallback: bma → pooled → primary."""
    if n_models <= 1:
        return PrimaryModelStrategy()
    match preferred:
        case AlertModelStrategy.BMA:
            if has_bma_weights:
                return BmaStrategy(...)
            log.info("alert_strategy.fallback", preferred="bma", actual="pooled",
                     reason="no_bma_weights")
            return PooledEnsembleStrategy()
        case AlertModelStrategy.POOLED:
            return PooledEnsembleStrategy()
        case AlertModelStrategy.CONSENSUS:
            return ConsensusVotingStrategy(consensus_fraction)
        case AlertModelStrategy.PRIMARY:
            return PrimaryModelStrategy()
```

**Scope:** Convergence service with strategy dispatch and cascading fallback.
**Verification:** `uv run pytest tests/unit/services/test_alert_checker.py -x -q`
**Dependencies:** Phase 0, Phase 2.

#### 3B. `src/sapphire_flow/store/alert_store.py` — update for new fields

Update `PgAlertStore.upsert_alert` to write `model_ids` (JSONB) and
`alert_model_strategy` (text) columns.

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
   - `test_single_model_returns_that_model` — single model, any priority
   - `test_empty_ensembles_returns_empty` — no models, no alerts
   - `test_model_ids_contains_only_primary` — auditability check

2. `TestPooledEnsembleStrategy`:
   - `test_pools_all_members` — 2 models × 21 members = 42 pooled members
   - `test_exceedance_probability_from_pooled_set` — known ensemble values, verify probability
   - `test_falls_back_to_primary_with_single_model` — cascading fallback
   - `test_model_ids_contains_all_models` — auditability check

3. `TestConsensusVotingStrategy`:
   - `test_triggers_when_majority_agrees` — 3 models, 2 exceed → fraction 0.67 ≥ 0.5 → trigger
   - `test_no_trigger_when_minority_agrees` — 3 models, 1 exceeds → fraction 0.33 < 0.5 → no trigger
   - `test_configurable_fraction` — fraction 0.75, 3 of 4 agree → 0.75 ≥ 0.75 → trigger
   - `test_model_ids_contains_only_agreeing_models`
   - `test_falls_back_to_primary_with_single_model`

4. `TestBmaStrategy`:
   - `test_stub_raises_not_implemented` — v0 guard

#### 4B. `tests/unit/services/test_alert_checker.py` — convergence + fallback tests

1. `TestCascadingFallback`:
   - `test_bma_falls_back_to_pooled_without_weights`
   - `test_pooled_falls_back_to_primary_with_single_model`
   - `test_consensus_falls_back_to_primary_with_single_model`
   - `test_primary_always_works`

2. `TestCheckStationAlerts`:
   - `test_multi_parameter_dispatch` — discharge + water_level ensembles from 2 models; alerts checked per parameter
   - `test_upsert_called_for_exceeded` — exceeded result → `alert_store.upsert_alert` called with correct `model_ids`
   - `test_resolve_called_for_not_exceeded` — not exceeded → `alert_store.resolve_alert` called
   - `test_skipped_when_alerts_disabled` — `enable_forecast_alerts=False` → no checking

#### 4C. `tests/unit/config/test_deployment.py` — config validation tests

1. `test_consensus_fraction_validation` — out of range raises
2. `test_alert_model_strategy_from_toml` — round-trip from TOML string to enum

---

## File-Level Change Summary

| File | Change type | Phase |
|---|---|---|
| `src/sapphire_flow/types/enums.py` | Add `AlertModelStrategy` enum | 0A |
| `src/sapphire_flow/config/deployment.py` | Add `alert_model_strategy`, `consensus_model_fraction` fields | 0B |
| `config.toml` | Add alert strategy config keys | 0B |
| `docs/spec/config-reference.toml` | Add alert strategy config documentation | 0B |
| `src/sapphire_flow/types/domain.py` | Add `model_ids`, `strategy` to `ExceedanceResult` | 0C |
| `src/sapphire_flow/types/alert.py` | Add `model_ids`, `alert_model_strategy` to `Alert` | 0D |
| `src/sapphire_flow/db/metadata.py` | Add `model_ids`, `alert_model_strategy` columns to `alerts` table | 0D |
| `alembic/versions/NNNN_alert_model_fields.py` | Migration: add columns to `alerts` | 0D |
| `src/sapphire_flow/types/ensemble.py` | Add optional `model_id` to `ForecastEnsemble` | 0E |
| `docs/spec/types-and-protocols.md` | Update `AlertModelStrategy`, `ExceedanceResult`, `Alert`, `ForecastEnsemble`, `DeploymentConfig` | 0A–0E |
| `docs/architecture-context.md` | Update Flow 1 Phase C (1.11–1.12), alerts table schema | 1A |
| `docs/v0-scope.md` | Add §A8d (multi-model alert strategy) | 1B |
| `docs/standards/orchestration.md` | Update Phase C sketch with convergence structure | 1C |
| `docs/standards/wmo.md` | Add multi-model alert reference | 1D |
| `src/sapphire_flow/protocols/alert_strategy.py` | New: `ModelAlertStrategy` Protocol | 2A |
| `src/sapphire_flow/services/alert_strategy.py` | New: `PrimaryModelStrategy`, `PooledEnsembleStrategy`, `ConsensusVotingStrategy`, `BmaStrategy` (stub) | 2B–2E |
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
      "tasks": ["2b", "2c", "2d", "2e"],
      "parallel": true,
      "depends_on": ["phase-2-protocol"]
    },
    {
      "id": "phase-3",
      "tasks": ["3a", "3b", "3c"],
      "parallel": true,
      "depends_on": ["phase-0-types", "phase-2-impl"]
    },
    {
      "id": "phase-4",
      "tasks": ["4a", "4b", "4c"],
      "parallel": true,
      "depends_on": ["phase-2-impl", "phase-3"]
    }
  ]
}
```

Phase 0 and Phase 1 can proceed fully in parallel. Phase 2 depends on Phase 0 (types).
Phase 3 depends on Phase 0 + Phase 2. Phase 4 depends on Phase 2 + Phase 3.

---

## Guardrails

- `uv run pytest tests/ -x -q` after each phase
- `uv run pyright --strict src/sapphire_flow/` after type changes (Phase 0, 2)
- After Phase 0D: verify `alembic upgrade head` succeeds and `alembic downgrade -1` reverses cleanly
- After Phase 2: verify `isinstance(PrimaryModelStrategy(), ModelAlertStrategy)` passes
- After Phase 3: verify cascading fallback with single-model station produces identical results to direct `check_thresholds` call
- Version bump: `uv run bump-my-version bump patch` before committing (per CLAUDE.md)
- Doc updates (Phase 1) must be reviewed for consistency with code changes (Phase 0)

---

## Implementation Timing

| Phase | When | What |
|---|---|---|
| 0 (types + config) | **Now** — pre-Phase 8 | Types, config, migrations. No behavioral change. |
| 1 (docs) | **Now** — parallel with Phase 0 | Architecture documentation. |
| 2B (primary) | **Phase 8** — when forecast service is implemented | Only strategy needed for v0. |
| 2C (pooled) | **v0b** — when second model onboarded per station | Simple, no training needed. |
| 2D (consensus) | **v1+** — if stakeholder demand | Lower priority than BMA. |
| 2E (bma stub → impl) | **v1** — with weight training pipeline | Tied to Flow 8/10. |
| 3 (convergence) | **Phase 8** — with forecast service | Core dispatch logic. |
| 4 (tests) | **With each implementation phase** | Tests land alongside code. |

---

## Impact on Plan 005

Plan 005 Phase 2A (alert routing) is **replaced** by this plan's Phase 2 + Phase 3.
Plan 005 should:
1. Remove Phase 2A entirely
2. Add a note: "Alert routing is defined in plan 010. Phase C convergence, strategy
   dispatch, and multi-model support are out of scope for this plan."
3. Phases 0, 1, 3 of plan 005 are unaffected

---

## Open Items

1. **Priority information in strategy dispatch** — `ModelAlertStrategy.evaluate` receives
   `model_ensembles: dict[ModelId, ForecastEnsemble]` but `PrimaryModelStrategy` needs to
   know each model's priority. Options: (a) add `priorities: dict[ModelId, int]` parameter,
   (b) embed priority in `ForecastEnsemble`, (c) have the convergence service pre-sort and
   pass `model_ensembles` as an ordered dict. Recommend (a) — explicit, no type pollution.

2. **BMA weight storage** — Where do trained BMA weights live? Options: (a) new
   `bma_weights` table keyed on `(station_id, model_id)`, (b) stored as a model artifact
   attribute, (c) stored in `skill_scores` as a derived metric. Defer to the BMA
   implementation plan (v1).

3. **BMA weight training trigger** — Should BMA weights be recomputed as part of Flow 8
   (skill computation, runs after every hindcast) or Flow 10 (skill recomputation, runs
   on schedule)? Flow 8 is the natural fit since it already processes hindcast verification
   data per model. Defer to v1.

4. **Pooled ensemble member count normalization** — When pooling, should we resample each
   model's ensemble to equal member count before concatenation? Without normalization, a
   model with 51 members dominates one with 21 members. EFAS does not normalize (implicit
   count-weighting). Recommend: no normalization in v0b (EFAS precedent), revisit if
   model member counts diverge significantly.

5. **Observation alerts** — This plan focuses on forecast alerts (Flow 1 Phase C).
   Observation alerts (Flow 2 steps 2.8–2.10) are single-source (one observation value vs
   threshold) and do not have a multi-model dimension. No changes needed for observation
   alerts. The `model_ids` field on `Alert` is `[]` for observation-source alerts.

6. **`check_thresholds` formalization** — The `check_thresholds` function is currently only
   an example pattern in `types-and-protocols.md` (line 2267), not a Protocol or implemented
   function. This plan's strategies internalize threshold checking. The standalone
   `check_thresholds` function may still be useful as a shared utility called by multiple
   strategies. Decide during implementation.
