---
status: READY
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

The hydrology-operations document (§14) does not yet include an open question
about input quality flagging or forecaster notification under degraded
conditions. This plan adds one (new question 10) and answers it: **yes** to
both — this is a safety-critical system and forecasters must know when they are
looking at a forecast built on incomplete or stale inputs.

Without this feature, a forecaster sees a forecast that looks normal but was
actually produced with 18-hour-old observations and a fallback NWP cycle. During
a flood event, that distinction matters.

### WMO alignment

The WMO-168 Vol I QC flag vocabulary (`good`, `suspect`, `erroneous`, `missing`)
applies to **individual observation values** — "is this measured value
reliable?" — and is already adopted by `QcStatus`. Input quality assessment
answers a different question: "how complete and fresh was the **input bundle**
assembled for this forecast run?" A forecast can be DEGRADED (observations 18 h
stale) while every observation that arrived is `QC_PASSED` (good). These are
orthogonal concerns, so a separate vocabulary is appropriate.

WMO-1072 (end-to-end flood forecasting systems) supports this approach:
forecasters should be informed when a forecast is produced under degraded input
conditions. QMF-H's pipeline quality-assurance framework is consistent with
this approach. Both publications are already catalogued in
`docs/standards/wmo.md` (document inventory, subsystem mapping); the gap
analysis table does not yet include a specific entry
for input quality flagging — step 3 adds one.

## Design

### Input quality is distinct from forecast output QC

Forecast QC (step 1.10, plan 012) checks the **output** — whether the ensemble
values are physically plausible. Input quality checks the **inputs** — whether
the data that went into the model was complete and fresh. A forecast can pass
output QC (values look reasonable) while being produced from degraded inputs
(stale observations, fallback NWP). Both are needed.

### Assessment approach

The assessment is a **pure function** that takes the existing metadata fields
and resolved thresholds, and returns a quality level plus a list of flags
explaining what is degraded and why. No new data collection is needed — all
signals are available by the end of step 1.7's input assembly sub-steps
(observation staleness and NWP metadata from earlier steps; warm-up source
and age determined within 1.7).

The function is **season-unaware** — it receives already-resolved threshold
values. The caller (Phase 8 Flow 1 wiring) is responsible for determining
the current season and selecting the appropriate thresholds before calling.
This keeps the assessment logic pure and decoupled from deployment-specific
season configuration, and scales to any number of seasons (pre-monsoon,
post-monsoon, etc.) without changes to domain types. See §Season-aware
threshold resolution for details.

### Input signals assessed

| Signal | Source | Assessment |
|--------|--------|------------|
| Observation staleness | `observation_staleness_hours` (step 1.6) | Compare against `observation_staleness_warning_hours` (PARTIAL) / `obs_degraded_hours` (DEGRADED) from config |
| Warm-up source | `warm_up_source` (step 1.7) | `FRESH` = full; `SNAPSHOT` = partial or degraded (age-dependent, see next row; `None` age → DEGRADED); `COLD_START` = degraded (age ignored); `None` = no warm-up assessment |
| Warm-up state age | `warm_up_state_age_hours` (step 1.7) | Compare against caller-resolved thresholds (only relevant when `warm_up_source == SNAPSHOT`; `COLD_START` is always DEGRADED regardless of age) |
| NWP cycle age | `issued_at - nwp_cycle_reference_time` | Compare against configurable `nwp_age_partial_hours` / `nwp_age_degraded_hours`. Age thresholds apply regardless of `nwp_cycle_source` — a PRIMARY cycle that is unusually old is still flagged. In practice, `nwp_max_wait_hours` bounds PRIMARY age, but the assessment does not assume this. |
| NWP cycle fallback | `nwp_cycle_source` (determined during Phase A / NWP fallback logic) | `FALLBACK` source is not a separate flag — it is folded into the NWP age assessment. The age check already captures the staleness that a fallback implies, because a FALLBACK cycle is always older than the expected PRIMARY by at least one NWP issue interval (the pipeline only falls back when the current cycle did not arrive). The `detail` string includes the source (e.g. `"NWP 10.2h stale, fallback cycle (threshold: 9.0h)"`) so the forecaster sees both facts in one flag. `PRIMARY` does not mention source in detail. This avoids double-flagging for the same root cause. **Tradeoff**: a FALLBACK cycle whose age is below `nwp_age_partial_hours` produces no `InputQualityFlag` — the fallback event is not visible in the quality assessment. For ICON-CH2-EPS (6h issue interval, 5h delivery offset), the minimum age of a fallback cycle is ~11h, always above the default `nwp_age_partial_hours` (9.0h) — so this blind spot is unreachable in v0. For v1 NWP sources with shorter issue intervals, the default should be re-evaluated per deployment. The raw `nwp_cycle_source` field on `OperationalForecast` (and in the DB) remains available for audit queries and trend analysis (e.g. "how often did this station use a fallback cycle?"). Flow 4 independently monitors NWP delivery failures for ops/IT staff. |
| Observations absent | `observation_staleness_hours is None` | No observation assessment — station has no observation history (zero rows in the observations store). Per-station "expects observations" config can be added for v1 if needed. **Note**: `None` means `MAX(timestamp)` returned NULL — no observation rows exist. Normal observation outages (feed goes silent) produce a large staleness float (historical rows persist), which the age thresholds catch. Total data loss (row deletion/corruption) is an infrastructure failure detected by DB backup/integrity mechanisms, not by per-forecast input quality assessment. |

### New types

**`InputQualityLevel` enum** (in `types/enums.py`):

```python
class InputQualityLevel(Enum):
    FULL = "full"
    PARTIAL = "partial"
    DEGRADED = "degraded"
```

Three levels, matching the examples in hydrology-operations.md ("full data",
"partial data", "degraded"). No inline comments on members — semantics are
described here in prose, matching the existing enum style in `enums.py`. This
is deliberately coarse — the flags carry the detail. The level is the at-a-glance indicator for forecasters and
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

    def __post_init__(self) -> None:
        if self.level == InputQualityLevel.FULL:
            raise ValueError("InputQualityFlag must not be FULL — only record actual issues")
```

Similar to `QcFlag` (which forbids `RAW`/`MISSING`) but for input-side
assessment. Unlike `QcFlag.detail` (optional), `detail` is required — every
flag must explain what triggered it. A flag with `level=FULL` is forbidden — only record actual issues;
absence of flags implies FULL. The `detail` string is intended for display in
the API response and dashboard — it should be concise and actionable.

**Security note**: `detail` strings expose internal threshold values (e.g.
`"threshold: 6.0h"`). The API layer (Phase 9) should role-filter these —
expose the `input_quality` level to all consumers but restrict
`input_quality_flags` (with `detail` strings) to operator/forecaster roles.
This is a serialization concern in the response schema, not a domain type
change. In v0 (no auth), `detail` strings are exposed to all consumers.
Role-filtering applies from v1 when the authorization model is active.

**Note**: Field-level RBAC (filtering fields within a single endpoint response)
is a new pattern not currently described in `docs/standards/security.md`. The
existing authorization matrix is endpoint-level only. This is a v1 design
concern — step 3 does **not** update security.md. The RBAC requirement is
recorded here in the plan for Phase 9 to pick up when the authorization model
is designed.

**Aggregation function** (in `types/domain.py`):

```python
def aggregate_input_quality(flags: list[InputQualityFlag]) -> InputQualityLevel:
    """Return the worst (highest-severity) level across all flags.

    Empty flags → FULL (no issues detected).
    """
```

Same pattern as `aggregate_qc_status()` — takes `list` for consistency.
Callers convert from the `tuple` stored on `OperationalForecast` if needed.

### New fields on `OperationalForecast`

```python
input_quality: InputQualityLevel = InputQualityLevel.FULL
input_quality_flags: tuple[InputQualityFlag, ...] = ()
```

Defaults preserve backward compatibility — existing forecasts are `FULL` with
no flags. `HindcastForecast` does **not** get these fields (hindcasts
reconstruct past conditions from historical archives — NWP archive or
reanalysis — that are complete by definition; the concept of operational input
degradation does not apply).

### Assessment configuration

Thresholds are deployment-configurable. The PARTIAL boundary for observation
staleness **reuses** the existing `observation_staleness_warning_hours` field on
`DeploymentConfig` — no duplication. The remaining thresholds are added as a
nested Pydantic model `InputQualityConfig` on `DeploymentConfig`:

```python
class InputQualityConfig(BaseModel):
    # Observation staleness — PARTIAL reuses DeploymentConfig.observation_staleness_warning_hours
    obs_degraded_hours: float = 12.0              # obs staleness → DEGRADED

    # NWP cycle age
    nwp_age_partial_hours: float = 9.0            # NWP cycle age → PARTIAL (> 1 cycle late)
    nwp_age_degraded_hours: float = 11.0          # NWP cycle age → DEGRADED (must be <= nwp_max_fallback_age_hours)

    # Warm-up snapshot age (default thresholds — caller may override with
    # season-resolved values; see §Season-aware threshold resolution)
    warmup_snapshot_age_partial_hours: float = 24.0   # snapshot age → PARTIAL
    warmup_snapshot_age_degraded_hours: float = 42.0  # snapshot age → DEGRADED (must be <= warm_up_snapshot_max_age_hours)
```

**Relationship to existing pipeline-gate thresholds**: The existing
`DeploymentConfig` fields (`observation_staleness_warning_hours`,
`nwp_max_fallback_age_hours`, `warm_up_snapshot_max_age_hours`,
`warm_up_snapshot_max_age_monsoon_hours`) are **pipeline gates** — they control
whether the pipeline waits, falls back, or cold-starts. The `InputQualityConfig`
thresholds are **quality labels** — they annotate an already-produced forecast.
These serve different purposes but are coupled: a DEGRADED label threshold must
not exceed the corresponding gate threshold, because the pipeline aborts/
cold-starts before that condition can arise.

A Pydantic `model_validator` on `DeploymentConfig` enforces these invariants:

```python
@model_validator(mode="after")
def _validate_input_quality_thresholds(self) -> Self:
    iq = self.input_quality
    # obs: PARTIAL reuses observation_staleness_warning_hours; DEGRADED must be >
    if iq.obs_degraded_hours <= self.observation_staleness_warning_hours:
        raise ValueError(
            f"obs_degraded_hours ({iq.obs_degraded_hours}) must be > "
            f"observation_staleness_warning_hours ({self.observation_staleness_warning_hours})"
        )
    # NWP: DEGRADED must be <= pipeline abort threshold
    if iq.nwp_age_degraded_hours > self.nwp_max_fallback_age_hours:
        raise ValueError(
            f"nwp_age_degraded_hours ({iq.nwp_age_degraded_hours}) must be <= "
            f"nwp_max_fallback_age_hours ({self.nwp_max_fallback_age_hours})"
        )
    # Warm-up: DEGRADED must be <= pipeline cold-start threshold
    if iq.warmup_snapshot_age_degraded_hours > self.warm_up_snapshot_max_age_hours:
        raise ValueError(
            f"warmup_snapshot_age_degraded_hours ({iq.warmup_snapshot_age_degraded_hours}) must be <= "
            f"warm_up_snapshot_max_age_hours ({self.warm_up_snapshot_max_age_hours})"
        )
    # PARTIAL must also be <= gate (otherwise the pipeline aborts before
    # a forecast is produced, making the PARTIAL label unreachable)
    if iq.nwp_age_partial_hours > self.nwp_max_fallback_age_hours:
        raise ValueError(
            f"nwp_age_partial_hours ({iq.nwp_age_partial_hours}) must be <= "
            f"nwp_max_fallback_age_hours ({self.nwp_max_fallback_age_hours})"
        )
    if iq.warmup_snapshot_age_partial_hours > self.warm_up_snapshot_max_age_hours:
        raise ValueError(
            f"warmup_snapshot_age_partial_hours ({iq.warmup_snapshot_age_partial_hours}) must be <= "
            f"warm_up_snapshot_max_age_hours ({self.warm_up_snapshot_max_age_hours})"
        )
    # partial < degraded ordering within InputQualityConfig
    if iq.nwp_age_partial_hours >= iq.nwp_age_degraded_hours:
        raise ValueError("nwp_age_partial_hours must be < nwp_age_degraded_hours")
    if iq.warmup_snapshot_age_partial_hours >= iq.warmup_snapshot_age_degraded_hours:
        raise ValueError("warmup_snapshot_age_partial_hours must be < warmup_snapshot_age_degraded_hours")
    return self
```

**Note**: The validator checks `warmup_snapshot_age_*` against the default
(non-monsoon) pipeline gate `warm_up_snapshot_max_age_hours`. For deployments
with monsoon-aware gates (`warm_up_snapshot_max_age_monsoon_hours`), the caller
resolves season-appropriate thresholds at runtime (see §Season-aware threshold
resolution). The validator ensures the *default* thresholds are reachable; the
caller is responsible for ensuring season-resolved overrides do not exceed their
corresponding seasonal gate.

Defaults are reasonable starting points; will be tuned during the AWS testing
phase. Thresholds are per-deployment, not per-station (per-station overrides
could be added later if needed, same pattern as `StationForecastQcOverride`).

### Season-aware threshold resolution

The assessment function is **season-unaware** — it receives resolved threshold
floats and applies them without knowing which season produced them. Seasonal
variation in warm-up thresholds is handled by the **caller** (Phase 8 Flow 1
wiring), not by the assessment function or config types.

**Why this design?**

The existing codebase has two separate "season" concepts with no formal
relationship:

1. **`SeasonDefinition`** (`domain.py`) — user-configurable with arbitrary
   `name` and `months: frozenset[int]`. Currently used **only** for skill
   metric stratification (`services/skill/service.py`). Has no operational
   role in the forecast pipeline.

2. **Hardcoded `*_monsoon_*` fields** on `DeploymentConfig` (e.g.
   `warm_up_snapshot_max_age_monsoon_hours`) — pipeline gates that assume
   exactly two seasons (monsoon vs everything-else).

Introducing a hardcoded `Season` enum (DRY/MONSOON) bridged to
`SeasonDefinition` via `name == "monsoon"` string matching would create a
third season concept, deepen the binary assumption, and introduce a
stringly-typed coupling that silently fails if the deployer names the season
anything other than `"monsoon"` (e.g. `"Monsoon"`, `"wet_season"`, a Nepali
term). The Swiss v0 config uses `"winter"` and `"summer"` — neither matches.

Instead, the assessment function takes resolved warm-up thresholds directly:

```python
def assess_input_quality(
    *,
    observation_staleness_hours: float | None,
    warm_up_source: WarmUpSource | None,
    warm_up_state_age_hours: float | None,
    nwp_cycle_source: NwpCycleSource,
    nwp_age_hours: float,
    obs_partial_hours: float,
    config: InputQualityConfig,
    warmup_partial_hours: float,    # caller-resolved (may differ by season)
    warmup_degraded_hours: float,   # caller-resolved (may differ by season)
) -> tuple[InputQualityLevel, tuple[InputQualityFlag, ...]]:
```

The caller resolves warm-up thresholds from config + current season before
calling. In Swiss v0 (no monsoon), the caller passes
`config.warmup_snapshot_age_partial_hours` and
`config.warmup_snapshot_age_degraded_hours` directly. In Nepal v1, the caller
determines the current season (by matching the current month against
`SeasonDefinition.months`) and selects tighter thresholds for monsoon months.
The exact resolution logic is a **Phase 8 concern** — this plan provides the
assessment function and config defaults.

**Benefits**:
- No `Season` enum needed in this plan — no new type coupling
- No `*_monsoon_*` fields needed in `InputQualityConfig` — no v1 config
  complexity in a v0 plan
- No monsoon cross-validators — ~30 lines of validator complexity removed
- Scales to any number of seasons (pre-monsoon, post-monsoon, shoulder)
  without touching domain types
- Consistent with "parse, don't validate" — caller resolves concrete
  thresholds at the boundary; domain function applies them
- The existing `warm_up_snapshot_max_age_monsoon_hours` pipeline gate on
  `DeploymentConfig` stays unchanged (it controls cold-start behaviour,
  not quality labels)

**Phase 8 design note**: When Phase 8 wires season-aware threshold resolution,
the proper bridge between `SeasonDefinition` and operational thresholds should
be designed. Options include: (a) adding a `kind` field to `SeasonDefinition`
for explicit tagging, (b) per-season threshold overrides in deployment config
keyed by season name, or (c) a dedicated season-resolution helper. The right
choice depends on how many subsystems need seasonal variation (warm-up
thresholds, alert levels, NWP tolerances) — that scope is clearer at Phase 8
time than now.

**Phase 8 bounding invariant**: The resolver must ensure that resolved
thresholds do not exceed the active seasonal pipeline gate. Concretely:
`warmup_degraded_hours <= warm_up_snapshot_max_age_monsoon_hours` when the
current month falls within a monsoon season, and
`warmup_degraded_hours <= warm_up_snapshot_max_age_hours` otherwise (same for
`warmup_partial_hours`). Without this cap, the assessment function could label
a forecast as PARTIAL at a warm-up age where the pipeline should have already
cold-started — a misleading quality label. The assessment function's own
call-site validation (`warmup_partial_hours < warmup_degraded_hours`) catches
ordering inversions but cannot enforce the gate cap without knowing the current
season.

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

**Call-site sketch** (pseudocode for Phase 8 wiring within step 1.7):

```python
# After assembling model inputs and resolving warm-up state:
warmup_partial, warmup_degraded = resolve_warmup_thresholds(
    deployment_config, issued_at,  # Phase 8 resolves season-appropriate values
)
input_quality, input_quality_flags = assess_input_quality(
    observation_staleness_hours=obs_staleness,          # from step 1.6
    warm_up_source=resolved_warm_up_source,             # from 1.7 warm-up resolution
    warm_up_state_age_hours=resolved_warm_up_age,       # from 1.7 warm-up resolution
    nwp_cycle_source=nwp_metadata.cycle_source,         # from Phase A
    nwp_age_hours=(issued_at - nwp_metadata.cycle_reference_time).total_seconds() / 3600,
    obs_partial_hours=deployment_config.observation_staleness_warning_hours,
    config=deployment_config.input_quality,
    warmup_partial_hours=warmup_partial,
    warmup_degraded_hours=warmup_degraded,
)
# input_quality and input_quality_flags are passed to OperationalForecast construction
```

The `obs_partial_hours` parameter comes from
`DeploymentConfig.observation_staleness_warning_hours` (the existing pipeline
gate threshold reused as the PARTIAL boundary). NWP and observation thresholds
come from `deployment_config.input_quality` (`InputQualityConfig`). Warm-up
thresholds are resolved by the caller, defaulting to `InputQualityConfig`
values unless seasonal overrides apply.

### Logging

New structlog event (added to `docs/standards/logging.md` event table):

| Event | Level | Notes |
|-------|-------|-------|
| `forecast.input_quality_assessed` | `info` if PARTIAL; `warning` if DEGRADED | Emitted when `input_quality != FULL`. Level varies by severity, following the `model.skill_gate_completed` precedent (INFO when passed, WARNING when failed). Structured kwargs below |

PARTIAL uses INFO because it represents a slightly-below-optimal but expected
operational condition (e.g. observations 7h stale against a 6h threshold).
DEGRADED uses WARNING because it represents a genuinely degraded state where
forecaster confidence should be reconsidered — matching logging.md's WARNING
definition ("Degraded state. Operation continues.") and its examples ("NWP
cycle older than expected", "Observation QC suspect").

Every existing WARNING event in the codebase fires per-anomaly, not per-station
per-cycle. Emitting WARNING for PARTIAL would generate up to 4000 warning
events/day during a widespread observation outage (1000 stations × 4
cycles/day), burying genuinely concerning DEGRADED events in noise. The split
preserves WARNING as a signal of genuine concern.

Event-specific kwargs:
- `input_quality` (str) — the overall level (`"partial"` or `"degraded"`)
- `flags` (list of dicts) — one dict per flag: `{"category": str, "level": str, "detail": str}`. Machine-queryable: consumers can filter by `category` or `level` without parsing detail strings.

**Note**: `flags` as a list-of-dicts is a new kwargs pattern — existing events
use only flat scalar kwargs. The step 3 update to `logging.md` should document
this as an accepted extension for events that carry a variable-length collection
of structured sub-items.

`station_id` is inherited from the step 1.7 structlog context. `model_id`
must be bound via `bind_contextvars` within the per-station fan-out loop when
the pipeline is wired (Phase 8) — it is not automatically present in Flow 1's
top-level context. In multi-model stations, `model_id` must be re-bound for
each model within the per-station loop. No event is emitted for `FULL`
quality (absence of the event = no issues).

**Volume note**: At 1000 stations × 4 cycles/day, if most forecasts are
PARTIAL (e.g. during a widespread observation outage), this generates up to
4000 `info`-level events/day — roughly ~2 MB/day at ~500 bytes/event, a small
fraction of the ~2 GB/day total log volume at 1000 stations (cicd.md). DEGRADED
events during the same outage would be a smaller subset at WARNING level,
keeping that channel focused. Phase 8 should confirm the volume is acceptable
in practice.

### v0 considerations

**Note**: Plan 021 collapsed the v0a/v0b distinction for NWP — v0 starts with
gridded ICON-CH2-EPS from day one (steps 1.2–1.4 are active from v0). The
v0a/v0b terminology that survives in v0-scope.md §A11 applies only to
model-onboarding gates, not NWP. v0-scope.md §A11 itself is stale and needs
separate correction (Plan 021 prescribed edits that were never applied — see
§Doc updates below).

All three assessment categories are **exercisable from v0**:

- **Observation staleness**: The primary exercisable path. Observation outages
  (feed goes silent) produce large staleness floats that the thresholds catch.
- **NWP cycle age**: With gridded ICON-CH2-EPS active from v0, real NWP
  fallback scenarios are possible. The NWP age assessment applies whenever
  `issued_at - nwp_cycle_reference_time` exceeds the configured thresholds.
- **Warm-up state**: Depends on the model type, not the NWP source.
  `LinearRegressionDaily` (the initial operational model) has
  `warm_up_source = None` (statistical and ML models have no warm-up
  state — only conceptual models with internal routing state like GR4J/HBV
  use warm-up; see `OperationalForecast.warm_up_source` type:
  `WarmUpSource | None`), so the warm-up path is unreachable for this
  specific model. It becomes reachable when conceptual models are onboarded
  (a model-onboarding gate, not an NWP-related v0a/v0b gate).

### Forecaster notification

**v0**: No push notification. Input quality is exposed in the API response and
logged. Flow 4 (pipeline monitoring), once implemented, will detect observation
staleness and NWP lateness independently — its alerts go to ops/IT staff. The
new input quality fields make the same information available to forecasters
per-forecast.

**v1**: Add a forecaster notification when a station's forecast is DEGRADED.
This could reuse the notification infrastructure (webhook/email/SMS)
with a new notification type. Deferred — requires step 1.14 of Flow 1
(notification dispatch — not implemented in v0; `NotificationAdapter` is
excluded from v0 per v0-scope §G, with rationale in §A8).

### API exposure

Forecast API responses include `input_quality` and `input_quality_flags`
alongside existing fields. The `/api/v1/stations/{id}/forecasts` endpoint
supports filtering by `input_quality` level (e.g. `?input_quality=degraded` to
find all degraded forecasts). This is a Phase 9 (API) concern — the types and
assessment logic come first.

### Doc updates

See step 3 for the full list of documents to modify.

## Scope

Four steps. No new dependencies. These types and services have no call site
until Phase 8 wires them into Flow 1.

**Why now, not Phase 8?** The hydrology-operations handover document (§14) has
no question yet about input quality flagging — this needs to be added and
answered before the next DHM review. Settling the design now — types,
assessment logic, and config — lets step 3 add the question with a confirmed
answer, final type names, and function signatures. The code is simple,
self-contained, and fully testable in isolation, so there is no integration
risk from building it ahead of Phase 8. Deferring would leave the handover doc
without this topic and force Phase 8 to design these types under time pressure
alongside pipeline wiring.

### Step 1: Types and assessment function

**Create / modify**:
- `src/sapphire_flow/types/enums.py` — add `InputQualityLevel`,
  `InputQualityCategory`
- `src/sapphire_flow/types/domain.py` — add `InputQualityFlag`,
  `aggregate_input_quality()`
- `src/sapphire_flow/types/forecast.py` — add `input_quality`,
  `input_quality_flags` fields to `OperationalForecast`. `InputQualityFlag`
  goes under `TYPE_CHECKING` (same pattern as `QcFlag`). `InputQualityLevel`
  is a runtime import (needed for `InputQualityLevel.FULL` field default).
- `tests/unit/types/test_input_quality.py` — unit tests for
  `InputQualityFlag`, `aggregate_input_quality()`

**Not in scope**: Assessment logic (step 2), pipeline integration (step 3).

**Verification**: `uv run pytest tests/unit/types/test_input_quality.py -v`

### Step 2: Assessment service

**Create**:
- `src/sapphire_flow/services/input_quality.py` — pure function
  `assess_input_quality()` that takes metadata fields + resolved thresholds
  and returns `tuple[InputQualityLevel, tuple[InputQualityFlag, ...]]`
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
    obs_partial_hours: float,       # from DeploymentConfig.observation_staleness_warning_hours
    config: InputQualityConfig,
    warmup_partial_hours: float,    # caller-resolved (may differ by season)
    warmup_degraded_hours: float,   # caller-resolved (may differ by season)
) -> tuple[InputQualityLevel, tuple[InputQualityFlag, ...]]:
```

Pure function — no I/O, no store access. `obs_partial_hours` is passed in from
`DeploymentConfig.observation_staleness_warning_hours` (avoids duplication).
Warm-up thresholds are passed as resolved floats — the caller selects
season-appropriate values before calling (see §Season-aware threshold
resolution). Easy to test — pass floats, get flags.

**Call-site validation**: The function validates
`obs_partial_hours < config.obs_degraded_hours` and
`warmup_partial_hours < warmup_degraded_hours` at call time (raises
`ValueError` if violated). This catches mismatches in tests where
`DeploymentConfig`'s cross-model validator is not in the loop, and also
catches season-resolution bugs where the caller passes inconsistent overrides.

**Bare-float convention note**: `warmup_partial_hours` and
`warmup_degraded_hours` are adjacent bare `float` parameters that could be
silently swapped. Per CLAUDE.md's NewType guidelines ("wrap where confusion is
plausible"), these would normally warrant `NewType` wrappers or a grouping
dataclass. Here, the keyword-only signature, unambiguous names (`partial` vs
`degraded`), and the call-site ordering validation provide sufficient runtime
protection. The parameters are caller-resolved values that may not correspond
to any single config field (they may be season-overridden), making a `NewType`
on `InputQualityConfig` fields insufficient — the wrapper would need to live
on the function boundary itself, adding ceremony for a two-parameter pair that
is always constructed and consumed in the same call. Accepted as a pragmatic
deviation.

**`None` handling across all categories**:

- `observation_staleness_hours is None`: No observation flag. Covers stations
  with no observation history (zero rows in the observations store). Normal
  observation outages produce a large staleness float (historical rows persist),
  not `None` — the age thresholds catch those. `None` only occurs when no
  observation rows exist at all, which is the expected state for newly onboarded
  or ungauged stations. A per-station "expects observations" config can be added
  for v1 if needed.
- `warm_up_source is None`: No warm-up flag. In practice, the pipeline always
  resolves warm-up to a concrete `WarmUpSource` value (`FRESH`, `SNAPSHOT`, or
  `COLD_START`) — even newly onboarded stations get `COLD_START`. `None` is
  accepted defensively for the same reason `observation_staleness_hours`
  accepts `None`: the type allows it (`WarmUpSource | None` on
  `OperationalForecast`), and the assessment function should handle every
  representable input without crashing. If `None` arrives, the conservative
  response is "no warm-up assessment" rather than raising an error mid-pipeline.
- `warm_up_source == SNAPSHOT` with `warm_up_state_age_hours is None`: Treated
  as DEGRADED. A snapshot without a known age is not trustworthy — the
  conservative assumption is that it is stale beyond the degraded threshold.
  The `detail` string says `"Warm-up snapshot age unknown"`.

**Not in scope**: Pipeline wiring (Phase 8), doc updates (step 3).

**Verification**: `uv run pytest tests/unit/services/test_input_quality.py -v`

### Step 3: Doc updates

**Modify**:
- `docs/handover/hydrology-operations.md` — add new §14 question 10 (input
  quality flagging and forecaster notification) with confirmed answer
- `docs/architecture-context.md` — document input quality assessment in step
  1.7 notes; add new fields to `OperationalForecast` schema (both the prose
  description and the `forecasts` table DDL block)
- `docs/spec/types-and-protocols.md` — add new types (`InputQualityLevel`,
  `InputQualityCategory`, `InputQualityFlag`, `InputQualityConfig`),
  updated `OperationalForecast` fields, updated `DeploymentConfig` with nested
  `input_quality` field and cross-config validator
- `docs/architecture-context.md` (additional) — fix prose summaries that omit
  `nwp_cycle_source` from `OperationalForecast` metadata fields (two
  locations: the forecast type descriptions and the data traceability section);
  fix stale name `nwp_cycle_is_fallback` → `nwp_cycle_source` in
  `HindcastForecast` exclusion notes
- `docs/spec/config-reference.toml` — add annotated `[input_quality]` section
  with inline comments noting the coupling between warm-up thresholds and
  seasonal pipeline gates (e.g. `warmup_snapshot_age_degraded_hours` must not
  exceed `warm_up_snapshot_max_age_monsoon_hours` during monsoon — important
  for Nepal deployers)
- `docs/standards/logging.md` — add `forecast.input_quality_assessed` event
  with level-conditional rule (INFO if PARTIAL, WARNING if DEGRADED). The
  existing canonical event table is headed "Flow 13 model onboarding events" —
  add a new subsection "Flow 1 forecast cycle events" for this entry, using
  the same "Notes" column format. Also document the
  `model.skill_gate_completed` precedent as the canonical example of
  level-conditional events.
- `docs/standards/wmo.md` — add entry to gap analysis table for WMO-1072
  (flood forecasting systems should inform forecasters of degraded input
  conditions) and QMF-H (pipeline quality assurance). Both publications are
  already in the document inventory and subsystem mapping — only the gap
  analysis entry is new.
- `docs/standards/orchestration.md` — add note for Phase 8: update Flow 1
  sketch to include input quality assessment sub-step within step 1.7; add
  note about season-aware threshold resolution as a Phase 8 design item. Also
  annotate the existing `prepare_inputs` call as step 1.7 (currently unlabeled
  — only step 1.10 has an inline comment in the sketch)
- `docs/standards/logging.md` (additional) — document list-of-dicts kwargs as an
  accepted extension for events with variable-length structured sub-items;
  document `model_id` binding in Flow 1's per-station-per-model loop (extends
  the existing `model_id` binding documented for Flow 13 only); clarify type of
  `failing_metrics` kwarg on `model.skill_gate_completed` (currently untyped —
  may already be a list, which affects the "flat scalar kwargs only" baseline)
- `docs/architecture-context.md` (additional) — add `NwpCycleSource` to the
  Metadata enums block (currently only lists `WarmUpSource` and
  `EnsembleRepresentation`; `NwpCycleSource` is stored in the `forecasts` table
  and should be listed alongside the other metadata enums)
- `docs/v0-scope.md` — register input quality assessment as an explicit Phase 4
  service; note that §E3's "Missing observations → staleness warning" scenario
  depends on this feature

**v0-scope.md stale references (Plan 021 debt)**: Plan 021 prescribed updates
to 9 sections of v0-scope.md (§A11, §A12, §A3, §E1, §H, §I1, §I2, Flow 1
table row, deferred table) that were never applied. These sections still
describe the pre-Plan-021 world where v0a used point NWP data and steps
1.2–1.4 were skipped. Step 3 of this plan should apply the overdue Plan 021
edits to v0-scope.md alongside the input quality registration, to prevent
future plans from inheriting stale framing. The specific sections and
corrections are documented in Plan 021's step 6 (doc updates).

**Not in scope**: Code changes (steps 1–2).

**Verification**: visual review (documentation only).

### Step 4: Config type

**Create / modify**:
- `src/sapphire_flow/config/deployment.py` — add `InputQualityConfig` Pydantic
  `BaseModel` (nested on `DeploymentConfig` as `input_quality: InputQualityConfig`
  with a default instance). Add `_validate_input_quality_thresholds`
  `model_validator` enforcing cross-field invariants (see §Assessment
  configuration).
- `tests/unit/config/test_input_quality_config.py` — validation tests (use
  `make_deployment_config()` from `tests/conftest.py` for config construction):
  - `partial < degraded` ordering within `InputQualityConfig`
  - Cross-config: `nwp_age_degraded_hours <= nwp_max_fallback_age_hours`
  - Cross-config: `nwp_age_partial_hours <= nwp_max_fallback_age_hours`
    (PARTIAL must be reachable — pipeline aborts before unreachable thresholds)
  - Cross-config: `warmup_snapshot_age_degraded_hours <= warm_up_snapshot_max_age_hours`
  - Cross-config: `warmup_snapshot_age_partial_hours <= warm_up_snapshot_max_age_hours`
    (same reachability invariant as NWP)
  - Cross-config: `obs_degraded_hours > observation_staleness_warning_hours`
    (equality rejected — PARTIAL and DEGRADED must not collapse)
  - Default config constructs without error (all defaults satisfy invariants)

**Not in scope**: Pipeline wiring (Phase 8 concern — the flow layer calls
`assess_input_quality()` during step 1.7 and passes the result to forecast
construction. This wiring happens when Flow 1 is implemented in Phase 8.)

**Verification**: `uv run pytest tests/unit/config/test_input_quality_config.py -v`

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
(assessment service) needs both: step 1 for the enums/flags types and step 4
for the `InputQualityConfig` type it receives as a parameter. Step 3 (docs)
runs last to reference final type names and signatures.

## What this plan does NOT cover

- **Pipeline wiring** (calling `assess_input_quality()` in Flow 1 step 1.7):
  This is a Phase 8 concern. The types, logic, and config from this plan are
  ready for Phase 8 to consume.
- **Season-aware threshold resolution**: Phase 8 designs the bridge between
  `SeasonDefinition` and operational thresholds. This plan provides
  season-unaware assessment logic that accepts resolved thresholds. See
  §Season-aware threshold resolution for design notes and options.
- **Push notifications to forecasters**: Deferred to v1 when the notification
  infrastructure (step 1.14) is operational.
- **Per-station threshold overrides**: Can be added later following the
  `StationForecastQcOverride` pattern if needed.
- **DB schema migration**: The new fields on `OperationalForecast` are
  in-memory type changes. DB columns (`input_quality`, `input_quality_flags`)
  need a migration (next available number at time of Phase 8 implementation)
  before Phase 8 pipeline wiring. Tracked as a Phase 8 prerequisite. Per
  cicd.md's additive-only migration rule, columns must be nullable or carry
  DB defaults to remain backwards-compatible with one prior version (e.g.
  `input_quality TEXT DEFAULT 'full'`, `input_quality_flags JSONB DEFAULT '[]'`).
- **Integration test coverage**: v0-scope.md §E3 requires the integration
  scenario "Missing observations → staleness warning, forecast proceeds."
  This plan provides unit tests for the isolated types/services. Phase 8 must
  add integration tests that exercise input quality assessment end-to-end within
  the forecast cycle replay, covering stale observations and additionally NWP
  fallback and cold-start warm-up scenarios (not explicitly in §E3 but implied
  by the assessment logic).
- **API endpoint changes**: Phase 9 concern. The types are ready for
  serialization.
