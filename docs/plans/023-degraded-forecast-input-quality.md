---
status: DRAFT
created: 2026-04-10
scope: Degraded forecast input quality flagging — types, assessment logic, pipeline integration, API exposure
depends_on: []
---

# 023 — Degraded Forecast Input Quality Flagging

## Problem

`OperationalForecast` already carries raw metadata about input conditions:
`observation_staleness_hours`, `warm_up_source`, `warm_up_state_age_hours`,
`nwp_cycle_source`, and `nwp_cycle_reference_time`. These fields tell a
developer what happened, but they do not tell a forecaster whether to trust
the forecast.

The hydrology-operations document (§14, question 4) asks DHM whether SAPPHIRE
should (a) flag each forecast with a quality indicator and (b) notify the
forecaster when a forecast is produced under degraded conditions. The answer to
both is **yes** — this is a safety-critical system and forecasters must know
when they are looking at a forecast built on incomplete or stale inputs.

Without this feature, a forecaster sees a forecast that looks normal but was
actually produced with 18-hour-old observations and a fallback NWP cycle. During
a flood event, that distinction matters.

## Design

### Input quality is distinct from forecast output QC

Forecast QC (step 1.10, plan 012) checks the **output** — whether the ensemble
values are physically plausible. Input quality checks the **inputs** — whether
the data that went into the model was complete and fresh. A forecast can pass
output QC (values look reasonable) while being produced from degraded inputs
(stale observations, fallback NWP). Both are needed.

### Assessment approach

The assessment is a **pure function** that takes the existing metadata fields
and configurable thresholds, and returns a quality level plus a list of flags
explaining what is degraded and why. No new data collection is needed — all
signals are already available by step 1.7.

### Input signals assessed

| Signal | Source | Assessment |
|--------|--------|------------|
| Observation staleness | `observation_staleness_hours` (step 1.6) | Compare against configurable `obs_partial_hours` / `obs_degraded_hours` thresholds |
| Warm-up source | `warm_up_source` (step 1.7) | `FRESH` = full; `SNAPSHOT` = partial; `COLD_START` = degraded |
| Warm-up state age | `warm_up_state_age_hours` (step 1.7) | Compare against configurable thresholds (only relevant when `warm_up_source == SNAPSHOT`) |
| NWP cycle fallback | `nwp_cycle_source` (step 1.1) | `PRIMARY` = full; `FALLBACK` = partial or degraded depending on age |
| NWP cycle age | `issued_at - nwp_cycle_reference_time` | Compare against configurable `nwp_age_partial_hours` / `nwp_age_degraded_hours` |
| Observations absent | `observation_staleness_hours is None` | Station has no observations at all — degraded if model expects them |

### New types

**`InputQualityLevel` enum** (in `types/enums.py`):

```python
class InputQualityLevel(Enum):
    FULL = "full"           # All expected inputs available and fresh
    PARTIAL = "partial"     # Some inputs degraded; forecast meaningful but reduced confidence
    DEGRADED = "degraded"   # Critical inputs missing or severely stale; reliability reduced
```

Three levels, matching the examples in hydrology-operations.md ("full data",
"partial data", "degraded"). This is deliberately coarse — the flags carry
the detail. The level is the at-a-glance indicator for forecasters and
dashboards.

**`InputQualityCategory` enum** (in `types/enums.py`):

```python
class InputQualityCategory(Enum):
    OBSERVATION = "observation"
    NWP = "nwp"
    WARM_UP = "warm_up"
```

**`InputQualityFlag` dataclass** (in `types/domain.py`):

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class InputQualityFlag:
    category: InputQualityCategory
    level: InputQualityLevel
    detail: str  # Human-readable: "Observations 8.2h stale (threshold: 6.0h)"
```

Similar to `QcFlag` but for input-side assessment. The `detail` string is
intended for display in the API response and dashboard — it should be concise
and actionable.

**Aggregation function** (in `types/domain.py`):

```python
def aggregate_input_quality(flags: tuple[InputQualityFlag, ...]) -> InputQualityLevel:
    """Return the worst (highest-severity) level across all flags.

    Empty flags → FULL (no issues detected).
    """
```

Same pattern as `aggregate_qc_status()`.

### New fields on `OperationalForecast`

```python
input_quality: InputQualityLevel = InputQualityLevel.FULL
input_quality_flags: tuple[InputQualityFlag, ...] = ()
```

Defaults preserve backward compatibility — existing forecasts are `FULL` with
no flags. `HindcastForecast` does **not** get these fields (hindcasts use
reanalysis, not operational inputs — degradation is not applicable).

### Assessment configuration

Thresholds are deployment-configurable. Added to `DeploymentConfig` (or a nested
`InputQualityConfig`):

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class InputQualityConfig:
    obs_partial_hours: float = 6.0     # obs staleness → PARTIAL
    obs_degraded_hours: float = 12.0   # obs staleness → DEGRADED
    nwp_age_partial_hours: float = 9.0   # NWP cycle age → PARTIAL (> 1 cycle late)
    nwp_age_degraded_hours: float = 18.0 # NWP cycle age → DEGRADED (> 2 cycles late)
    warmup_snapshot_age_partial_hours: float = 24.0  # snapshot age → PARTIAL
    warmup_snapshot_age_degraded_hours: float = 72.0 # snapshot age → DEGRADED
```

Defaults are reasonable starting points; will be tuned during the AWS testing
phase. Thresholds are per-deployment, not per-station (per-station overrides
could be added later if needed, same pattern as `StationForecastQcOverride`).

### Pipeline integration

The assessment runs as a **sub-step within step 1.7** (input preparation).
After assembling model inputs and resolving warm-up state, the flow calls the
assessment function with the collected metadata and thresholds. The result is
carried forward on the `OperationalForecast` record stored at step 1.11.

No new pipeline step number is needed — this is part of input preparation, not
a separate gate. Unlike forecast QC (step 1.10), input quality assessment does
**not** trigger model fallback. A degraded-input forecast is still stored and
can still be published — the flag informs the forecaster, it does not suppress
the forecast.

### Logging

New structlog events (added to `docs/standards/logging.md` event table):

| Event | Level | When |
|-------|-------|------|
| `forecast.input_quality_partial` | `warning` | Forecast produced with PARTIAL input quality |
| `forecast.input_quality_degraded` | `warning` | Forecast produced with DEGRADED input quality |

Context fields: `station_id`, `model_id`, `input_quality`, `flags` (list of
flag details).

### Forecaster notification

**v0**: No push notification. Input quality is exposed in the API response and
logged. Flow 4 (pipeline monitoring) already detects observation staleness and
NWP lateness independently — its alerts go to ops/IT staff. The new input
quality fields make the same information available to forecasters per-forecast.

**v1**: Add a forecaster notification when a station's forecast is DEGRADED.
This could reuse the existing notification infrastructure (webhook/email/SMS)
with a new notification type. Deferred — requires the notification system from
Phase C step 1.14 to be operational.

### API exposure

Forecast API responses include `input_quality` and `input_quality_flags`
alongside existing fields. The `/api/v1/stations/{id}/forecasts` endpoint
supports filtering by `input_quality` level (e.g. `?input_quality=degraded` to
find all degraded forecasts). This is a Phase 9 (API) concern — the types and
assessment logic come first.

### Doc updates

- **`docs/handover/hydrology-operations.md`** §14 question 4: Convert from open
  question to confirmed decision. Both sub-questions answered "yes". Reference
  this plan.
- **`docs/architecture-context.md`**: Add input quality assessment as a sub-step
  of 1.7. Document the new fields on `OperationalForecast`.
- **`docs/spec/types-and-protocols.md`**: Add new enums, `InputQualityFlag`,
  `InputQualityConfig`, updated `OperationalForecast` fields.
- **`docs/standards/logging.md`**: Add new events.

## Scope

Four steps. No new dependencies.

### Step 1: Types and assessment function

**Create / modify**:
- `src/sapphire_flow/types/enums.py` — add `InputQualityLevel`,
  `InputQualityCategory`
- `src/sapphire_flow/types/domain.py` — add `InputQualityFlag`,
  `aggregate_input_quality()`
- `src/sapphire_flow/types/forecast.py` — add `input_quality`,
  `input_quality_flags` fields to `OperationalForecast`
- `tests/unit/types/test_input_quality.py` — unit tests for
  `InputQualityFlag`, `aggregate_input_quality()`

**Not in scope**: Assessment logic (step 2), pipeline integration (step 3).

**Verification**: `uv run pytest tests/unit/types/test_input_quality.py -v`

### Step 2: Assessment service

**Create**:
- `src/sapphire_flow/services/input_quality.py` — pure function
  `assess_input_quality()` that takes metadata fields +
  `InputQualityConfig` and returns
  `tuple[InputQualityLevel, tuple[InputQualityFlag, ...]]`
- `tests/unit/services/test_input_quality.py` — unit tests covering all
  signal combinations (full, partial, degraded for each category; worst-wins
  aggregation)

**Design**: The function signature:

```python
def assess_input_quality(
    *,
    observation_staleness_hours: float | None,
    warm_up_source: WarmUpSource | None,
    warm_up_state_age_hours: float | None,
    nwp_cycle_source: NwpCycleSource,
    nwp_age_hours: float,
    config: InputQualityConfig,
) -> tuple[InputQualityLevel, tuple[InputQualityFlag, ...]]:
```

Pure function — no I/O, no store access. Easy to test.

**Not in scope**: Pipeline wiring (step 3), doc updates (step 4).

**Verification**: `uv run pytest tests/unit/services/test_input_quality.py -v`

### Step 3: Doc updates

**Modify**:
- `docs/handover/hydrology-operations.md` — convert §14 question 4 from open
  question to confirmed decision
- `docs/architecture-context.md` — document input quality assessment in step
  1.7 notes; add new fields to `OperationalForecast` schema
- `docs/spec/types-and-protocols.md` — add new types and updated forecast
  fields
- `docs/standards/logging.md` — add `forecast.input_quality_partial`,
  `forecast.input_quality_degraded` events

**Not in scope**: Code changes (steps 1–2).

**Verification**: visual review (documentation only).

### Step 4: Config type

**Create / modify**:
- `src/sapphire_flow/types/config.py` (or wherever `DeploymentConfig` lives) —
  add `InputQualityConfig` dataclass and wire into deployment config
- `tests/unit/types/test_input_quality_config.py` — validation tests for
  threshold invariants (partial < degraded)

**Not in scope**: Pipeline wiring (Phase 8 concern — the flow layer calls
`assess_input_quality()` during step 1.7 and passes the result to forecast
construction. This wiring happens when Flow 1 is implemented in Phase 8.)

**Verification**: `uv run pytest tests/unit/types/test_input_quality_config.py -v`

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1",
      "tasks": ["1", "4"],
      "parallel": true
    },
    {
      "id": "phase-2",
      "tasks": ["2"],
      "parallel": false,
      "depends_on": ["phase-1"]
    },
    {
      "id": "phase-3",
      "tasks": ["3"],
      "parallel": false,
      "depends_on": ["phase-2"]
    }
  ]
}
```

Step 1 (types) and step 4 (config) are independent — parallel. Step 2
(assessment service) needs both. Step 3 (docs) runs last to reference
final type names and signatures.

## What this plan does NOT cover

- **Pipeline wiring** (calling `assess_input_quality()` in Flow 1 step 1.7):
  This is a Phase 8 concern. The types, logic, and config from this plan are
  ready for Phase 8 to consume.
- **Push notifications to forecasters**: Deferred to v1 when the notification
  infrastructure (step 1.14) is operational.
- **Per-station threshold overrides**: Can be added later following the
  `StationForecastQcOverride` pattern if needed.
- **DB schema migration**: The new fields on `OperationalForecast` are
  in-memory type changes. The DB column additions happen when the forecast
  store implementation is built (Phase 2 is complete but the Postgres
  implementation may need migration — tracked separately).
- **API endpoint changes**: Phase 9 concern. The types are ready for
  serialization.
