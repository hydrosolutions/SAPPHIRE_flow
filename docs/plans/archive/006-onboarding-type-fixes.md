---
status: READY
created: 2026-03-26
revised: 2026-03-28
scope: design doc + spec + architecture-context + conventions consistency fixes and design decisions
depends_on: [001, 002]
---

# 006 — Onboarding type fixes from critical review

Consolidates fixes for issues 1, 3–10 from the critical review of plan 001.
Issue 2 (multi-target predict chain) is covered by plan 003.

**Naming note**: After D1 drops `OnboardingUnit`, the remaining `OnboardingUnitResult`,
`OnboardingOutcome`, and `ModelOnboardingResult` types retain "Onboarding" in their names.
This refers to the _Flow 13 model onboarding process_, not the dropped type.

## Decisions

### D1. Drop `OnboardingUnit` — reuse `TrainingUnit` (Issue 3)

`OnboardingUnit` is structurally identical to `TrainingUnit` (same fields, same XOR
invariant). Drop it. `OnboardingUnitResult.unit` references `TrainingUnit` directly.

```python
# TrainingUnit (types/training.py)
@dataclass(frozen=True, kw_only=True, slots=True)
class TrainingUnit:
    model_id: ModelId
    station_id: StationId | None
    group_id: StationGroupId | None
    station_ids: frozenset[StationId]
    training_period_start: UtcDatetime
    training_period_end: UtcDatetime
    time_step: timedelta

    def __post_init__(self) -> None:
        if (self.station_id is None) == (self.group_id is None):
            raise ValueError("Exactly one of station_id or group_id must be set")
```

`OnboardingUnit` only differed in field names (`onboarding_period_start/end` vs
`training_period_start/end`) with the same XOR invariant — the types are equivalent.
The spec's `OnboardingUnit` uses `"Exactly one of station_id / group_id must be set"`
(slash); standardize to `"or"` (more natural English) in `TrainingUnit` and the spec.
Callers currently constructing `OnboardingUnit` with `onboarding_period_start/end`
must switch to `training_period_start/end`.

**Prerequisite**: `TrainingUnit` and `HindcastStepResult` are already implemented in
`src/sapphire_flow/types/training.py` (per the training pipeline design doc §3a). However,
`types/training.py` does not yet have an entry in the spec's module map. D8 adds it.

**Note**: The training pipeline design doc §3a code block for `TrainingUnit` omits the
`__post_init__` validation (though §3a prose describes it as enforced via `__post_init__`).
The source code already includes it. This pre-existing doc/code discrepancy is outside this
plan's scope but should be fixed when the training pipeline design doc is next revised.

Files: design doc §3f, §1 (mermaid diagram label "Per OnboardingUnit" + node ordering — see D6 note on §1 mermaid), §6d (`onboard_model` signature), §8 (five `@task` signatures using `unit: OnboardingUnit`), §11 (P4 JSON type list, P4 phase summary table, P8 `make_onboarding_unit` factory → `make_training_unit`); spec (types/model_onboarding.py section). Note: the spec section description fix ("Flow 5 step 5.10" → "Flow 13") is owned by D8. Note: §2 "What already exists" table does not reference `OnboardingUnit` — no D1 edit needed there.

### D2. `CompatibilityReport.compatible` → `@property is_compatible` (Issue 4)

Remove `compatible: bool` stored field. Add:

```python
@property
def is_compatible(self) -> bool:
    return (
        self.protocol_conforms
        and not self.missing_target_parameters
        and not self.missing_past_dynamic
        and not self.missing_future_dynamic
        and not self.missing_static_features
        and self.time_step_compatible
    )
```

Removing the stored field avoids drift between `compatible` and the sub-check fields it
derives from — on a frozen dataclass the boolean could only be set at construction time,
creating a risk of inconsistency if the constructor logic diverges from the field checks.
A `@property` computes the value on demand from the authoritative fields.

**`@property` over plain method**: Same rationale as D3's `passed` — using a plain method
would create a footgun where `if report.is_compatible` (without `()`) evaluates as a
bound method object (always truthy). `@property` ensures both `report.is_compatible` and
`if report.is_compatible` work correctly. Both D2 and D3 use `@property` for consistency.

**`model_id` retained**: Unlike `SkillGateResult` (D3), `CompatibilityReport` retains
`model_id` because the report may be consumed standalone for diagnostics (e.g., logging
which model failed compatibility at which station) without a parent `OnboardingUnitResult`
in scope.

Files: design doc §3f (type definition), §6a step 7 (`compatible = True iff all checks pass` prose), §6d line 497 (`not compatible` → `not is_compatible`), §11 P4 phase summary table (`CompatibilityReport.compatible logic` → `is_compatible` property); spec

### D3. Simplify `SkillGateResult` (Issues 5, 8)

Remove `model_id`, `station_id`, `group_id` from the result type — context comes from
the parent `TrainingUnit`. Add `artifact_id: ArtifactId` for audit trail (the skill gate
only runs when an artifact exists, so non-nullable).

Note: the `evaluate_skill_gate()` service function still accepts `model_id`, `station_id`,
and `group_id` as input parameters (needed to query the skill score store). Only the
_result type_ loses them.

**`passed` → `@property`**: Remove `passed: bool` stored field (same rationale as
D2 — avoid drift). Add a `@property` that computes the value from `failing_metrics`.
Using `@property` (not a plain method) avoids a footgun where `result.passed` without
parentheses would be a bound method object (always truthy). With `@property`, both
`result.passed` and `if result.passed` work correctly.

**Immutable containers**: `metric_scores` and `thresholds` use `tuple[tuple[str, float], ...]`
instead of `dict[str, float]` on the frozen dataclass. Mutable `dict` fields would make
instances non-hashable and violate frozen dataclass conventions. Callers that need dict
access can use `dict(result.metric_scores)`.

**Pre-existing inconsistency**: Four QC frozen dataclasses in the spec (`QcRuleParams`,
`StationQcOverride`, `ForecastQcRuleParams`, `StationForecastQcOverride`) already use
`dict[str, float]` (or `dict[str, float | None]`) on frozen dataclasses. `SkillGateResult`
is treated differently because it is a _result type_ that may be collected, compared, or
used in sets during orchestration, whereas QC params are config-like objects that are never
hashed. The QC types are a latent inconsistency in the spec — tracked in plan 011 §H for
future cleanup, not addressed by this plan.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class SkillGateResult:
    artifact_id: ArtifactId
    metric_scores: tuple[tuple[str, float], ...]
    thresholds: tuple[tuple[str, float], ...]
    failing_metrics: frozenset[str]

    def __post_init__(self) -> None:
        score_keys = {k for k, _ in self.metric_scores}
        if len(score_keys) != len(self.metric_scores):
            raise ValueError("Duplicate metric name in metric_scores")
        thresh_keys = {k for k, _ in self.thresholds}
        if len(thresh_keys) != len(self.thresholds):
            raise ValueError("Duplicate metric name in thresholds")

    @property
    def passed(self) -> bool:
        return not self.failing_metrics
```

**Duplicate-key guard**: `metric_scores` and `thresholds` use
`tuple[tuple[str, float], ...]` (not `dict`) for frozen-dataclass hashability.
`__post_init__` rejects duplicate metric names to prevent silent data loss when
callers convert with `dict(result.metric_scores)`.

**`failing_metrics` population rule**: `evaluate_skill_gate()` must treat a missing
score as a failure. For each configured threshold key, if the metric has no score in
`metric_scores`, the key is added to `failing_metrics`. This ensures that zero valid
strata + active thresholds = `GATE_REJECTED`, never a false `PROMOTED`. Document this
rule in the design doc §6b gate logic and the spec.

Files: design doc §3f, §6b (step 4 prose: `passed = True iff` → derived from `failing_metrics`; failing_metrics population rule), §8 (`evaluate_skill_gate_task` return type), spec

### D4. `OnboardingOutcome` enum + drop stored counters (Issues 6, 7)

Add enum to `types/enums.py`:

```python
class OnboardingOutcome(Enum):
    PROMOTED = "promoted"
    GATE_REJECTED = "gate_rejected"
    SKIPPED_COMPAT = "skipped_compat"
    SKIPPED_NO_DATA = "skipped_no_data"
    FAILED_TRAINING = "failed_training"
    FAILED_HINDCAST = "failed_hindcast"
    FAILED_SKILL = "failed_skill"
    FAILED_ASSIGNMENT = "failed_assignment"
```

The eight values partition into four counter categories (see methods below):
promoted (1) + gate_rejected (1) + skipped (2) + failed (4) = total.

**`gate_rejected_count()` is net-new** — no equivalent stored field exists in the current
spec. It is additive API surface for monitoring gate rejection rates once the gate is
active.

**`GATE_REJECTED` — fully implemented from v0**: The skill gate logic is implemented
completely from day one, including the `GATE_REJECTED` code path. With the default
`skill_gate_thresholds = {}` (D6), the gate is a pass-through and `GATE_REJECTED` is
unreachable in the default configuration. However, a hydrologist can configure thresholds (e.g.,
`skill_gate_thresholds = {"crpss": 0.0}`) at any time to activate the gate — no code
changes required. This enables threshold experimentation during v0 and ensures the gate
path is tested end-to-end from the start. `v0-scope.md` §A7's "auto-promote sufficient
for v0" refers to the default config policy, not missing implementation.

No catch-all `FAILED_UNKNOWN` — **expected** domain exceptions (`SapphireError`
subclasses, data/model issues) during any phase are caught and mapped to the nearest
`FAILED_*` variant for that phase, with details in `error: str`. True unexpected
exceptions (`TypeError`, `AttributeError`, etc.) must propagate to Prefect as task-level
failures per `conventions.md` Flow-level strategy and `orchestration.md`.

**Exception mapping rules**:
- `InsufficientDataError` (a `SapphireError` subclass) maps to `SKIPPED_NO_DATA` when
  raised **before training** (steps 1–2: compatibility check, data assembly).
  Rationale: "no data available" is logically a skip (no training attempted).
  Once training begins (steps 3+), `InsufficientDataError` maps to the `FAILED_*`
  variant for the current phase (e.g., raised during training → `FAILED_TRAINING`,
  raised during hindcast → `FAILED_HINDCAST`), because training was attempted and the
  skip semantics ("no training attempted") no longer apply.
- Other `SapphireError` subclasses map to the `FAILED_*` variant for the phase that raised
  them. The mapping is phase-based, not type-based — the same exception type produces
  different outcomes depending on where it is caught (e.g., `AdapterError` raised during
  training → `FAILED_TRAINING`; raised during hindcast → `FAILED_HINDCAST`).

**Convention deviation — `InsufficientDataError` handling**: `conventions.md`'s exception
table says `InsufficientDataError` → "Try fallback model." That convention applies to
forecast-cycle flows (Flow 1) where fallback models exist. In model onboarding (Flow 13),
there is no fallback model — the unit is skipped (pre-artifact) or failed (post-artifact).
Add a footnote to `conventions.md`:

> **Flow 13 exception**: In model onboarding (and other multi-phase initialization
> flows), there is no fallback model. Exception mapping is phase-based, not type-based:
> `InsufficientDataError` before training maps to `SKIPPED_NO_DATA`; once training
> begins, any `SapphireError` subclass maps to the `FAILED_*` variant for the current
> phase (e.g., `FAILED_TRAINING`, `FAILED_HINDCAST`, `FAILED_SKILL`,
> `FAILED_ASSIGNMENT`). True unexpected exceptions (`TypeError`, `AttributeError`)
> propagate to Prefect as task-level failures per the standard rule.

**Partial hindcast failure policy**: Individual hindcast step failures do not produce
`FAILED_HINDCAST`. Each step is independent — a failing step is recorded as
`HindcastStepResult(success=False)` and the pipeline continues. `FAILED_HINDCAST` only
fires when the hindcast service itself raises (catastrophic failure, e.g., store
connection lost). The `min_skill_samples` filter (already in `DeploymentConfig`) protects
against skill scores computed from too few surviving pairs.

**Edge case — all hindcast steps fail**: If every step records `success=False`, the
pipeline proceeds to skill gate with zero valid skill scores. The `min_skill_samples`
filter ensures no strata qualify, producing an empty `metric_scores`. With an empty
`skill_gate_thresholds` (v0), the gate trivially passes. With configured thresholds,
no metric can meet its threshold → `GATE_REJECTED`. The orchestration must handle this
correctly: zero valid pairs + active gate = rejection, not a false `PROMOTED`.

Update `OnboardingUnitResult` — replaces `promoted: bool` and `assigned: bool`:

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class OnboardingUnitResult:
    unit: TrainingUnit              # was OnboardingUnit (D1)
    outcome: OnboardingOutcome
    compatibility: CompatibilityReport
    artifact_id: ArtifactId | None
    hindcast_steps: tuple[HindcastStepResult, ...]
    skill_gate: SkillGateResult | None
    error: str | None = None
```

**Removed fields**: `promoted: bool` and `assigned: bool` are replaced by `outcome`.
`PROMOTED` guarantees the full post-gate pipeline succeeded: artifact promoted
(`TRAINING → ACTIVE`) and assignment created. It is a clean success signal with `error`
always `None`. `FAILED_ASSIGNMENT` covers any failure in the post-gate phase (promotion
or assignment creation) with `error` capturing the failure detail. The name slightly
understates scope (it also covers promotion failures), but adding a separate
`FAILED_PROMOTION` variant is not warranted — promotion is a simple status update that
virtually never fails independently of the subsequent assignment step. **v1 revisit**:
when `PENDING_APPROVAL` and human-approval workflows are added, promotion failure becomes
more plausible (e.g., approval timeout, reviewer rejection). Evaluate whether
`FAILED_ASSIGNMENT` should be split into `FAILED_PROMOTION` + `FAILED_ASSIGNMENT` at
that point.

**`compatibility` is non-nullable**: The compatibility check (step 1 in the orchestration)
always runs before data assembly (step 2). `validate_compatibility()` always returns a
`CompatibilityReport` regardless of pass/fail, so it is constructable for both
`SKIPPED_COMPAT` (check ran, found incompatible) and `SKIPPED_NO_DATA` (check ran,
passed, but data was insufficient). This ordering guarantee ensures `CompatibilityReport`
is always available.

**`hindcast_steps: list → tuple`**: The current spec uses `list[HindcastStepResult]`
(mutable). Changed to `tuple[HindcastStepResult, ...]` — `list` would violate frozen
dataclass conventions. Matches `ModelOnboardingResult.units` which already uses `tuple`.
Note: `TrainingResult.hindcast_steps` in the training pipeline design doc currently uses
`list[HindcastStepResult]` — that is a separate type and not changed by this plan, but
the inconsistency should be noted for a future cleanup.

**`skill_gate` semantics**: `None` = step not reached (earlier failure or skip).
When `skill_gate_thresholds` is `{}` (v0), the gate function is still called but
short-circuits: returns `SkillGateResult(metric_scores=(), thresholds=(), failing_metrics=frozenset())` with `passed` returning `True`. This ensures `None`
unambiguously means "not reached", never "not configured."

**Group hindcast flattening**: `run_group_hindcast()` returns
`dict[StationId, list[HindcastStepResult]]`. The orchestration flattens this into a single
`tuple[HindcastStepResult, ...]` for `OnboardingUnitResult`. (Station-scoped models return
`list[HindcastStepResult]` directly — no flattening needed.)

**Semantic expansion**: The original spec's `skipped` counter covered compatibility
failures only (`# compatibility failures` comment). `skipped_count()` now also includes
`SKIPPED_NO_DATA` — this is intentional, as "no data available" is logically a skip
(no training attempted), not a failure. The spec comment must be updated to reflect this.

Update `ModelOnboardingResult` — drop stored counters (`total`, `promoted`, `skipped`,
`failed`), add methods:

```python
# Module-level constants (avoid slots=True class variable conflict)
ONBOARDING_FAILED_OUTCOMES = frozenset({
    OnboardingOutcome.FAILED_TRAINING,
    OnboardingOutcome.FAILED_HINDCAST,
    OnboardingOutcome.FAILED_SKILL,
    OnboardingOutcome.FAILED_ASSIGNMENT,
})
ONBOARDING_SKIPPED_OUTCOMES = frozenset({
    OnboardingOutcome.SKIPPED_COMPAT,
    OnboardingOutcome.SKIPPED_NO_DATA,
})

@dataclass(frozen=True, kw_only=True, slots=True)
class ModelOnboardingResult:
    model_id: ModelId
    units: tuple[OnboardingUnitResult, ...]

    def __len__(self) -> int:
        return len(self.units)

    def promoted_count(self) -> int:
        return sum(1 for u in self.units if u.outcome == OnboardingOutcome.PROMOTED)

    def failed_count(self) -> int:
        return sum(1 for u in self.units if u.outcome in ONBOARDING_FAILED_OUTCOMES)

    def skipped_count(self) -> int:
        return sum(1 for u in self.units if u.outcome in ONBOARDING_SKIPPED_OUTCOMES)

    def gate_rejected_count(self) -> int:
        return sum(1 for u in self.units
                   if u.outcome == OnboardingOutcome.GATE_REJECTED)
```

**`OnboardingOutcome` not in conventions.md enum master table**: That table tracks
DB-persisted and cross-flow shared runtime enums (e.g., `FlowRunState`).
`OnboardingOutcome` is internal pipeline state confined to Flow 13 orchestration logic —
it is registered in the spec's `enums.py` module map entry (D8) only.

**`total()` → `__len__`**: `len(result)` is idiomatic Python for "how many items." No
need for a separate `total()` method.

Files: design doc §3f, spec (types/model_onboarding.py section + enums.py section), conventions.md (exception table footnote for Flow 13)

### D5. `fetch_groups_for_model()` queries `group_model_assignments` (Issue 1)

Redefine `StationGroupStore.fetch_groups_for_model(model_id)` to query
`group_model_assignments` table instead of cross-joining per-station
`model_assignments`.

Files: spec (StationGroupStore Protocol — update `fetch_groups_for_model` doc comment
from "active artifact" to "active assignment"), design doc §10 (store naming in both
alignment table and Protocol gaps table: `store_group_assignment` →
`store_group_model_assignment`), design doc §11 (P6 and P7 JSON entries:
`store_group_assignment` → `store_group_model_assignment`)

Note: this changes the semantics from "groups with an active _artifact_" to "groups with
an active _assignment_." These differ when a model has an artifact but the skill gate
rejected it. Callers should be aware that post-D5, `fetch_groups_for_model` returns groups
where the model is actively assigned, not merely where an artifact exists. The spec's
current doc comment (`# All groups that have an active artifact for this model.`) must be
updated to reflect the new semantics.

**Design decision, not alignment**: The training pipeline design doc
(`v0-flow678-training-pipeline.md` §6a) uses assignment-based semantics for station-scoped
model filtering (line 267: "A station without an active assignment for a model is
excluded"). D5 extends the same principle to group-scoped models — groups should similarly
require an active assignment, not just an active artifact. This is a deliberate semantic
change to the spec, not a correction of a pre-existing inconsistency.

**Naming fix:** The spec uses `store_group_model_assignment()` while the design doc uses
`store_group_assignment()` in four locations: §10 alignment table, §10 Protocol gaps
table, §11 P6 JSON, and §11 P7 JSON. Rename all four to the spec's longer form.

**Ordering prerequisite**: Post-D5, `fetch_groups_for_model` returns groups with an active
`GroupModelAssignment`. These rows only exist after Flow 13 (model onboarding) creates
them at step M.7. Flow 6 (retraining) callers that use `fetch_groups_for_model` to build
`TrainingScope` will get an empty scope for group-scoped models until Flow 13 has run.
This is correct by design (onboard before retrain) but the dependency is implicit.

**Implementation note**: The `protocols/stores.py` Protocol stub's doc comment already
diverges from the spec — it reads "All groups with at least one station that has an
active model assignment" (per-station assignment semantics), while the spec says "active
artifact." D5 changes both to group-level assignment semantics. No concrete store
implementation exists yet (`stores/station_group_store.py` is not implemented), so D5
only affects the Protocol doc comment and spec text.

**Out of scope**: `SkillStore.fetch_skill_scores(model_id, artifact_id)` is also missing
from the spec (identified as a gap in design doc §10 line 657). That gap is tracked in the
design doc's implementation phases (P6) and is not addressed by this plan.

### D6. `skill_gate_thresholds` in `DeploymentConfig` (Issue 9)

Add to spec's `DeploymentConfig`:

```python
skill_gate_thresholds: dict[str, float] = {}  # metric_name → minimum value
# Empty dict = no gate (pass-through) — all models auto-promoted
# Candidate: {"crpss": 0.0}
# Final values require hydrologist input (design doc §12 open item 2)
# Aggregation: min-across-strata (see D7)
```

**Behavioral change acknowledged**: `architecture-context.md` currently defines the gate
with concrete defaults (`crpss_min = 0.0`, `bss_danger_min = 0.0` inside a structured
`model_onboarding_skill_gate` sub-object). Those defaults are a _permissive_ gate — a
model with negative skill would still be rejected. The new `{}` default is _no_ gate
(complete pass-through). Having a correct-looking but untested default like
`crpss_min = 0.0` is more dangerous than an explicit empty dict that forces active
configuration. The gate logic is fully implemented from v0 (see D4 `GATE_REJECTED`
note) — configuring thresholds activates it immediately, enabling hydrologist
experimentation without code changes.

Default `{}` (empty dict) means the skill gate is a pass-through until thresholds are
configured. `DeploymentConfig` is a Pydantic model, so a required field without a
default would break construction.

Also update `architecture-context.md` in **six locations**:
1. **M.3 Steps table row — v0 clause** (line 1316): currently says "v0: auto-promotes to
   `ACTIVE`." This contradicts late promotion (M.5 gate → M.6 promote). Change to:
   "v0: auto-promotes to `ACTIVE` at M.6 (after skill gate pass-through)." The v0
   auto-promote note belongs at M.6, not M.3.
2. **M.3 Steps table row — v1 clause** (same line 1316): currently says "v1: transitions
   to `PENDING_APPROVAL` if a champion already exists (see M.6)." Under late promotion,
   this transition also belongs at M.6, not M.3. Qualify with: "v1: transitions to
   `PENDING_APPROVAL` at M.6 if a champion already exists." Both v0 and v1 clauses
   at M.3 have the same early-transition implication; both must be fixed.
3. **M.5 Notes bullet** (§M.5 detail, line 1327): references
   `DeploymentConfig.model_onboarding_skill_gate` (a structured sub-object with
   `crpss_min`, `bss_danger_min`). Replace with `DeploymentConfig.skill_gate_thresholds`
   (`dict[str, float]`) to match the spec.
4. **M.5 Steps table row** (line 1318): hardcodes specific metric names and thresholds
   (`CRPSss > 0`, `BSS at danger levels > 0`). Update to reference
   `skill_gate_thresholds` generically instead of listing specific metrics. Also qualify
   "no regression vs existing champion if one exists" with `(v1)` — champion comparison
   is not implemented in v0 and is not captured by `skill_gate_thresholds` (which holds
   absolute floor thresholds, not relative comparisons). Also generalize "no seasonal
   collapse (skill must hold across all seasons)" to "skill must hold across all strata
   (lead time × season × flow regime)" for consistency with D7's min-across-strata
   semantics.
5. **Artifact status transitions table** (lines ~1530–1531): annotates
   `'training' → 'active'` as "training complete, initial mode — auto-promote." This
   annotation covers two invocation contexts: standalone Flow 6 (auto-promote at T.5,
   no skill gate) and Flow 13 (auto-promote at M.6 after skill gate). Replace with a
   dual-context annotation: "initial mode — auto-promote after T.5 when standalone, or
   after skill gate at M.6 when called from Flow 13."
6. **Flow 6 initial training sequencing** (lines ~905, ~928): currently reads
   `T.1 → T.2 → T.3 → T.4 → T.5 → auto-promote` and
   `Initial: T.1 → T.2 → T.3 → T.4 → T.5 → promote`. When Flow 13 calls Flow 6,
   promotion is deferred to M.6 — it is not part of Flow 6. Add a parenthetical:
   "auto-promote (when standalone; deferred to M.6 when called from Flow 13)." This
   clarifies the two invocation contexts without changing standalone Flow 6 behavior.
7. **Flow 6 intro paragraph** (line 847): currently reads "If no existing artifact →
   initial training (auto-promote)." Add the same parenthetical as #6: "initial training
   (auto-promote when standalone; deferred to M.6 when called from Flow 13)."

**Artifact status — architecture-context.md M.5/M.6 are correct**: Lines 1318 and 1329
say "Failing the gate keeps the artifact in `TRAINING` status" and "M.3–M.5 failures
leave the artifact in `TRAINING` status." These are correct under late promotion: the
artifact stays `TRAINING` until the gate passes, then M.6 promotes to `ACTIVE`. The
additional locations (#5, #6 above) are elsewhere in architecture-context.md — residual
references to early promotion that would survive as contradictions without explicit fixes.

Also update `v0-scope.md` **§A7** (line 106): "Training produces artifact with `training`
status → auto-promote to `active` → done" implies immediate promotion after training.
Add a parenthetical: "Training produces artifact with `training` status → auto-promote to
`active` (after skill gate) → done." Line 108 already mentions the skill gate but the
sequencing at line 106 is misleading without this qualifier.

**§6d ordering fix (late promotion)**: The design doc §6d currently has auto-promote at
step 4, before hindcast (step 5) and skill gate (step 7). This contradicts both
architecture-context.md's M-series ordering (M.3 train → M.4 hindcast → M.5 gate →
M.6 promote) and the design doc's own §1 mermaid diagram. Fix §6d to match:

```
For each unit in units (parallelized at flow layer):
  1. validate_compatibility()
     → skip unit if not is_compatible
  2. assemble_training_data()
     → skip unit if returns None
  3. train_{station|group}_model()
  4. store_artifact()                            # TRAINING status
  5. run_{station|group}_hindcast()
  6. compute_skill_for_station()
  7. evaluate_skill_gate()
     → skip promotion if gate fails (artifact remains TRAINING)
  8. promote_artifact()                          # TRAINING → ACTIVE
  9. create_{station|group}_assignment()
```

Step 4 becomes `store_artifact()` (no promote — artifact lands in `TRAINING`). New step 8
`promote_artifact()` promotes `TRAINING → ACTIVE` only after the gate passes. Step 7's
skip comment changes from "skip assignment" to "skip promotion" (which also skips
assignment). The §6d note `(artifact remains ACTIVE, not assigned)` becomes
`(artifact remains TRAINING, not promoted or assigned)`.

**§1 mermaid promotion ordering is already correct**: The mermaid shows training →
hindcast → skill gate → auto-promote → assignment, which matches the corrected §6d
ordering above. No promotion-ordering mermaid changes needed (D1's label rename
"Per OnboardingUnit" → "Per TrainingUnit" still applies; D9 adds a scope determination
node separately).

Also update the design doc §6b field definition to include the `= {}` default (the design
doc currently declares `skill_gate_thresholds: dict[str, float]` without a default — the
spec gains this field from D6, and the design doc must match).

Also update the design doc §12 open item 2: D6 resolves the `skill_gate_thresholds`
default question with `= {}`. Mark the item as resolved (default settled; specific
threshold values still require hydrologist input per §12 open item 2's original note).

Files: spec (DeploymentConfig section), design doc §6d (reorder: promote moves from step 4 to step 8, after skill gate), design doc §6b (field default + gate logic), design doc §10 (alignment table row at line 630: `store_and_promote_artifact()` → `store_artifact()` + `promote_artifact()`, update "Implemented via existing" note), design doc §12 (open item 2 — mark default resolved), architecture-context.md (M.3 Steps table row: qualify v0 auto-promote with "at M.6"; M.5 Steps table row + M.5 Notes bullet: threshold reference, strata generalization, champion comparison qualifier), v0-scope.md §A7 (line 106: add "after skill gate" parenthetical; line 108: change "logs results but does not block promotion" to "logs results and does not block promotion by default (`skill_gate_thresholds = {}`); configuring thresholds activates blocking")

### D7. Skill gate aggregation: min across strata (Issue 10)

Two design decisions (both tighten gate semantics in the same direction):

**Change A — architecture-context.md: resolve M.5 internal contradiction (semantic
tightening)**: The M.5 Steps table row says "no seasonal collapse (skill must hold across
all seasons)" (AND-across-strata), while the M.5 Notes bullet says "at any lead time
passes by default" (OR-across-strata — one passing stratum is enough). These are
contradictory. This plan aligns to AND-across-strata: "a model must meet the threshold at
every stratum (lead time × season × flow regime)." This is the stricter interpretation —
the Notes bullet's permissive "at any lead time" language is replaced, not merely
clarified. The tightening is deliberate: see Change B rationale below.

**Change B — design decision: mean → min in design doc §6b**: The current design doc
says "compute mean score across valid strata." This plan changes it to min-across-strata.
Rationale: for safety-critical hydrological forecasting, a model that performs well on
average but has hidden weaknesses in specific regimes (e.g., poor performance at long
lead times during flood season) is dangerous. Min-across-strata ensures the model meets
the threshold in _every_ stratum, preventing deployment of models with regime-specific
blind spots. This is stricter than mean aggregation — a model must be consistently
adequate, not just adequate on average.

Document in design doc §6b and spec:

> The skill gate evaluates `min(metric_value across all strata)` against the
> threshold. A model must meet the threshold in every stratum (lead time × season
> × flow regime) to pass. This prevents deploying models with hidden regime-specific
> weaknesses. `min_skill_samples` is the critical companion parameter: strata with
> fewer valid pairs than `min_skill_samples` are excluded before min-aggregation,
> preventing noisy low-sample strata from producing spurious rejections.
>
> With the default `skill_gate_thresholds = {}`, the gate is a pass-through
> (auto-promote). Configuring thresholds activates blocking — `GATE_REJECTED`
> leaves the artifact in `TRAINING` status.

Files: design doc §6b (step 3: "mean score" → "min score"), spec (if gate logic is described there), architecture-context.md (M.5 Notes bullet: "at any lead time" → "at every stratum")

### D8. Spec module map updates (Issue from review)

Four changes to the spec:

1. **Add `types/training.py` entry.** This module is defined in the training pipeline
   design doc (§3a) but has no entry in the spec's module map. Add it with:
   `TrainingUnit`, `HindcastStepResult`. (Other types like `TrainingScope`,
   `TrainingResult` are also defined there but are not in scope for this plan —
   the entry is intentionally partial.)

2. **Update `types/model_onboarding.py` entry.** Remove `OnboardingUnit` (dropped per D1).
   Add `ONBOARDING_FAILED_OUTCOMES` and `ONBOARDING_SKIPPED_OUTCOMES` (module-level
   public constants, per D4). Note that it imports `TrainingUnit`, `HindcastStepResult`
   from `types/training.py` and `ArtifactId` from `types/ids.py` (`ArtifactId` is already
   used by `OnboardingUnitResult.artifact_id`; D3 adds it to `SkillGateResult` as well).
   Clarify filename: `model_onboarding.py` (not `onboarding.py`,
   which is Flow 5's station onboarding).

3. **`OnboardingOutcome` placement.** The enum is _defined_ in `types/enums.py` (per
   project convention — all enums live there). The `model_onboarding.py` entry should
   list it as an _import_, not a definition. The `enums.py` entry in the module map
   gains `OnboardingOutcome`. Note: the spec's `## Enums` section has a blanket statement
   "Values match the DB convention (lowercase `.value`)." `OnboardingOutcome` is purely
   in-memory (no DB column). Add a parenthetical note to the `enums.py` entry:
   "`OnboardingOutcome` (in-memory only, no DB column)."

4. **Fix section description.** The spec's model onboarding types section description
   reads "Result types for Flow 5 model onboarding (step 5.10)" — this is wrong. Flow 5
   is station onboarding; Flow 13 is model onboarding. Update to "Flow 13 model
   onboarding." Note: the `StationGroup` description (spec line 705) also references
   "Flow 5 step 5.10" — that reference is _correct_ (station group management during
   station onboarding) and must NOT be changed.

Files: spec (module map — three entries: `types/training.py`, `types/model_onboarding.py`, `types/enums.py`; section description fix)

### D9. Explicit scope determination step + flow signature boundary fix

**Problem**: For a brand-new model with no existing assignments, the current design has
a silent failure mode. The flow signature defaults `group_ids=None`, which calls
`fetch_groups_for_model(model_id)` — but a new model has no assignments, so this returns
an empty list. No groups → no training units → `ModelOnboardingResult` with zero units
and no error. The operator gets a success result that did nothing.

For station-scoped models, `station_ids=None` defaults to "all operational stations",
which works — but is undocumented and may surprise operators who expected the model's
`data_requirements` to drive candidate selection.

**Fix — add scope determination step (M.0) to §6d and §8**:

Insert a `determine_onboarding_scope()` step before the per-unit loop. This step:

1. Loads the model's `ModelRegistryEntry` (from M.1) to get `artifact_scope` and
   `data_requirements`.
2. If `station_ids`/`group_ids` are explicitly provided: use them (parse to domain types).
3. If `station_ids is None` and model is station-scoped: fetch all operational stations
   via `station_store.fetch_all_stations()` and post-filter by
   `station_status == StationStatus.OPERATIONAL`. (Note: `StationStore` currently has no
   `fetch_by_status()` method — the post-filter is in Python. If this becomes a
   performance concern at ~1000 stations, add a store method with a DB-side filter.)
4. If `group_ids is None` and model is group-scoped: **raise `ConfigurationError`** — group-scoped
   models require explicit group IDs for initial onboarding. (After first onboarding,
   `fetch_groups_for_model` returns existing assignments for retraining via Flow 6.)
5. Build `tuple[TrainingUnit, ...]` from the resolved scope.

```python
def determine_onboarding_scope(
    model_id: ModelId,
    model: ForecastModel,
    station_ids: frozenset[StationId] | None,
    group_ids: frozenset[StationGroupId] | None,
    station_store: StationStore,
    group_store: StationGroupStore,
    training_period_start: UtcDatetime,
    training_period_end: UtcDatetime,
    time_step: timedelta,
) -> tuple[TrainingUnit, ...]:
    """Resolve onboarding scope to concrete TrainingUnits.

    Raises ConfigurationError for group-scoped models when group_ids
    is None and no existing assignments exist.
    """
```

**Mermaid update**: Add a scope determination node between trigger and M.1, or between
M.1 and M.2 (after registration, before per-unit work).

**§6d update**: The `onboard_model` service signature changes
`units: list[OnboardingUnit]` → `units: tuple[TrainingUnit, ...]` (D1 rename +
immutable container). The scope determination step is called in the flow layer (§8)
before invoking `onboard_model`.

**Convention deviation — `ConfigurationError` at runtime**: `conventions.md`'s exception
table says `ConfigurationError` → "Fail fast at startup." That convention targets process
startup (e.g., missing env vars, invalid TOML). In `determine_onboarding_scope()`, the
same exception is raised at flow invocation time when required scope parameters are
missing. The "fail fast" principle is the same — reject the invocation immediately rather
than proceeding with an empty scope. Add a footnote to `conventions.md`:

> **Flow 13 exception**: `ConfigurationError` is also raised at flow invocation time
> when required scope parameters are missing (e.g., `group_ids=None` for a group-scoped
> model with no existing assignments). The "fail fast" principle applies: reject the
> invocation immediately rather than proceeding with an empty scope.

**Flow signature boundary fix**: The §8 flow signature uses `list[str]` for
`station_ids` and `group_ids` — this is correct at the Prefect boundary (JSON
serialization). Inside the flow body, these must be parsed to domain types
(`frozenset[StationId]` / `frozenset[StationGroupId]`) before passing to
`determine_onboarding_scope()`. The flow body is the parse-don't-validate boundary.

Files: design doc §1 (mermaid — add scope determination node), §6d (add scope
determination step as new subsection, `units` param type: `list[OnboardingUnit]` →
`tuple[TrainingUnit, ...]`), §8 (add `determine_onboarding_scope_task`, document parse
boundary in flow body), architecture-context.md (add M.0 as a preamble line immediately
above the ASCII box at lines ~1291–1308: "M.0 Scope determination (flow-layer preamble
— resolves station_ids/group_ids to TrainingUnits)"; update box title to
"Flow 13 — Model onboarding (per unit)"; update sequencing summary block to
`M.0 → M.1 → M.2 → ... → M.7`; add failure note: "M.0 failures
(`ConfigurationError`) are terminal — provide explicit group IDs and re-run."),
conventions.md (add footnote to `ConfigurationError` row documenting
runtime usage for missing scope parameters in Flow 13)

---

## File-level change summary

| File | Changes |
|------|---------|
| `docs/design/v0-flow13-model-onboarding.md` | D1–D7, D9: §3f types, §1 diagram (rename "Per OnboardingUnit" label + add scope determination node per D9), §6a step 7 prose (`compatible = True` → `is_compatible`), §6b service + gate logic + `skill_gate_thresholds` default + step 3 aggregation (mean → min) + step 4 prose (`passed = True iff` → derived from `failing_metrics`) + failing_metrics population rule (missing score = failing), §6d reorder (promote moves from step 4 to after skill gate; `store_and_promote_artifact()` → `store_artifact()` + new `promote_artifact()` step; "skip assignment" → "skip promotion"; "artifact remains ACTIVE" → "artifact remains TRAINING") + `onboard_model` signature + line 497 `not compatible` → `not is_compatible`, §8 task signatures, §10 store naming in both tables (`store_group_assignment` → `store_group_model_assignment`) + §10 alignment table row (`store_and_promote_artifact()` → `store_artifact()` + `promote_artifact()`), §11 P4 JSON type list (remove `OnboardingUnit`), §11 P4 phase summary table (`OnboardingUnit` → `TrainingUnit`, `CompatibilityReport.compatible` → `is_compatible`), §11 P6/P7 JSON (`store_group_assignment` → `store_group_model_assignment`), §11 P8 JSON (`make_onboarding_unit` → `make_training_unit`), §12 open item 2 (mark default resolved) |
| `docs/spec/types-and-protocols.md` | D1–D9: update onboarding types, CompatibilityReport (`compatible: bool` → `@property is_compatible`), SkillGateResult (`passed` → `@property`, `dict` → `tuple`, add `__post_init__` duplicate-key guard), DeploymentConfig (`skill_gate_thresholds`), `fetch_groups_for_model` doc comment ("active artifact" → "active assignment"), section description ("Flow 5 step 5.10" → "Flow 13"), module map (add `types/training.py`, update `model_onboarding.py` with `ArtifactId` import + `ONBOARDING_FAILED_OUTCOMES`/`ONBOARDING_SKIPPED_OUTCOMES` constants, add `OnboardingOutcome` to `enums.py` with in-memory note), update `skipped` comment ("compatibility failures" → include `SKIPPED_NO_DATA`) |
| `docs/architecture-context.md` | D6, D7, D9: D9: add M.0 preamble line above ASCII box (lines ~1291–1308) + update box title to "(per unit)" + update sequencing summary block `M.0 → M.1 → ...` + add M.0 failure note to failure handling paragraph; D6: qualify M.3 v0 auto-promote with "at M.6 (after skill gate pass-through)" + qualify M.3 v1 `PENDING_APPROVAL` transition with "at M.6" + rename `model_onboarding_skill_gate` → `skill_gate_thresholds` in M.5 Notes bullet + update M.5 Steps table row to reference `skill_gate_thresholds` generically + generalize "no seasonal collapse" to "all strata" + qualify "no regression vs existing champion" as `(v1)` + update artifact status transitions table annotation ("training complete, initial mode — auto-promote" → dual-context: "auto-promote after T.5 when standalone, or after skill gate at M.6 when called from Flow 13") + qualify Flow 6 initial training sequencing "auto-promote" with "(when standalone; deferred to M.6 when called from Flow 13)" at lines ~905, ~928, and line 847 intro paragraph; D7: tighten "at any lead time" → "at every stratum" in M.5 Notes bullet (semantic tightening, not just editorial) |
| `docs/v0-scope.md` | D6: §A7 line 106 — add "(after skill gate)" parenthetical to auto-promote sequencing; line 108 — update "does not block promotion" to "does not block by default; configuring thresholds activates blocking" |
| `docs/conventions.md` | D4: add footnote to `InsufficientDataError` row documenting phase-based exception mapping for multi-phase initialization flows (Flow 13): `InsufficientDataError` → `SKIPPED_NO_DATA` pre-training; any `SapphireError` → `FAILED_*` for current phase post-training; unexpected exceptions propagate to Prefect. D9: add footnote to `ConfigurationError` row documenting runtime usage for missing scope parameters in Flow 13 |

## Verification

After implementation, these greps confirm all changes landed:

**D1 — OnboardingUnit dropped**:
- Grep for `OnboardingUnit[^R]` in spec — should find zero matches (bare `OnboardingUnit` removed; `OnboardingUnitResult` is retained)
- Grep for `OnboardingUnit[^R]` in design doc — should find zero matches (replaced by `TrainingUnit` in §1, §3f, §6d, §8, §11)
- Grep for `make_onboarding_unit` in design doc — should find zero matches (renamed to `make_training_unit` in §11 P8)
- Grep for `Flow 5 model onboarding` in spec — should find zero matches (fixed to "Flow 13" per D8). Note: "Flow 5 step 5.10" in the `StationGroup` description (spec line ~705) is correct and must remain — it refers to station group management during station onboarding.

**D2 — compatible field removed, is_compatible property added**:
- Grep for `compatible:` as a field in spec — should find zero matches on CompatibilityReport
- Grep for `CompatibilityReport.compatible` in design doc — should find zero matches (§3f field removed, §11 P4 table updated)
- Grep for `not compatible` in design doc §6d — should find zero matches (line 497 updated to `not is_compatible`)
- Grep for `is_compatible` in spec and design doc — should match `@property` definition in both

**D3 — SkillGateResult simplified + failing_metrics population rule**:
- Grep for `station_id` in SkillGateResult definition — should find zero matches
- Grep for `group_id` in SkillGateResult definition — should find zero matches
- Grep for `passed: bool` as a stored field on SkillGateResult — should find zero matches (now `@property`)
- Grep for `missing score` in design doc §6b — should match the rule that missing scores are treated as failing

**D4 — OnboardingOutcome enum + counters**:
- Grep for `promoted: bool` / `assigned: bool` on OnboardingUnitResult — should find zero matches
- Grep for `total:` / `promoted:` / `skipped:` / `failed:` as stored fields on ModelOnboardingResult — zero
- Grep for `OnboardingOutcome` in spec enums.py section — should match definition + in-memory note
- Grep for `OnboardingOutcome` in conventions.md enum master table — should find zero matches (not a DB-backed enum; only appears in the exception table footnote)
- Grep for `compatibility failures` as comment on `skipped` in spec — should find zero matches (updated to include SKIPPED_NO_DATA)
- Grep for `FAILED_ASSIGNMENT` in spec enums.py section — should match OnboardingOutcome definition
- Grep for `hindcast_steps: list` in spec/design doc — should find zero matches (changed to `tuple`)

**D5 — fetch_groups_for_model semantics + naming**:
- Grep for `store_group_assignment` (without `model_`) in design doc — should find zero matches across §10 AND §11 (renamed in all four locations)
- Grep for `active artifact` in spec `fetch_groups_for_model` doc comment — should find zero matches (replaced with "active assignment")

**D6 — skill_gate_thresholds + late promotion**:
- Grep for `skill_gate_thresholds` in spec — should match DeploymentConfig section with `= {}` default
- Grep for `skill_gate_thresholds.*= \{\}` in design doc §6b — should match (default added)
- Grep for `skill_gate_thresholds` in architecture-context.md — should match M.5 detail (replaces `model_onboarding_skill_gate`)
- Grep for `model_onboarding_skill_gate` in architecture-context.md — should find zero matches
- Grep for `CRPSss > 0` in architecture-context.md M.5 Steps table — should find zero matches (replaced with generic reference)
- Grep for `no regression vs existing champion` without `(v1)` qualifier in architecture-context.md — should find zero matches (qualified as v1)
- Grep for `store_and_promote_artifact` in design doc (full file, not just §6d) — should find zero matches (split into `store_artifact` + `promote_artifact` in §6d; alignment table row in §10 also updated)
- Grep for `artifact remains ACTIVE` in design doc §6d — should find zero matches (corrected to `artifact remains TRAINING`)
- Grep for `skip assignment` in design doc §6d — should find zero matches (changed to `skip promotion`)
- Grep for `no seasonal collapse` in architecture-context.md M.5 Steps table — should find zero matches (generalized to "all strata")
- Grep for `v0: auto-promotes to .ACTIVE.\."` (with period/end-of-cell immediately after) in architecture-context.md M.3 — should find zero matches (the text now continues with "at M.6 (after skill gate pass-through)" instead of ending)
- Grep for `v1: transitions to .PENDING_APPROVAL.` without `at M.6` in architecture-context.md M.3 — should find zero matches (v1 clause also qualified with "at M.6")
- Grep for `training complete.*initial mode` in architecture-context.md artifact status transitions table — should find zero matches (replaced with dual-context annotation: "auto-promote after T.5 when standalone, or after skill gate at M.6 when called from Flow 13")
- Grep for `auto-promote` in architecture-context.md Flow 6 sequencing (lines ~905, ~928) — should match with "(when standalone; deferred to M.6 when called from Flow 13)" qualifier
- Grep for `auto-promote` in architecture-context.md Flow 6 intro paragraph (line ~847) — should match with the same "(when standalone; deferred to M.6 when called from Flow 13)" qualifier
- Grep for `auto-promote to .active.` in v0-scope.md §A7 — should match with "(after skill gate)" parenthetical

**D7 — min-across-strata**:
- Grep for `mean score across valid strata` in design doc — should find zero matches (replaced by min-across-strata)
- Grep for `min.*across.*strata` in design doc §6b — should match the replacement text
- Grep for `at any lead time` in architecture-context.md M.5 — should find zero matches
- Grep for `every stratum` in architecture-context.md M.5 — should match the replacement text

**D8 — Module map updates**:
- Grep for `types/training.py` in spec module map — should match new entry with `TrainingUnit`, `HindcastStepResult`
- Grep for `OnboardingUnit[^R]` in spec module map `model_onboarding.py` entry — should find zero matches (removed per D1; `OnboardingUnitResult` retained)
- Grep for `ONBOARDING_FAILED_OUTCOMES` in spec module map `model_onboarding.py` entry — should match (constants added per D4)
- Grep for `OnboardingOutcome` in spec `enums.py` entry — should match with "in-memory only" note
- Grep for `Flow 13 model onboarding` in spec section description — should match (replaces "Flow 5")

**D9 — Scope determination step**:
- Grep for `determine_onboarding_scope` in design doc §6d or §8 — should match the new function
- Grep for `units: list\[OnboardingUnit\]` in design doc §6d — should find zero matches (changed to `tuple[TrainingUnit, ...]` per D1 + D9)
- Grep for `group_ids is None.*raise` or `ConfigurationError` in design doc §8 — should match the explicit-group-ids requirement for group-scoped models
- Grep for `determine_onboarding_scope` in design doc §6d — should match the new subsection
- Grep for `M.0` in architecture-context.md — should match as preamble line above the ASCII box and in the sequencing summary block
- Grep for `per unit` in architecture-context.md Flow 13 ASCII box title — should match "(per unit)"
- Grep for `M.0 failures` or `ConfigurationError.*terminal` in architecture-context.md — should match failure handling note

## Notes for implementers

- **D1 callers**: Code constructing `OnboardingUnit(onboarding_period_start=..., onboarding_period_end=...)`
  must switch to `TrainingUnit(training_period_start=..., training_period_end=...)`.
- **D2 callers**: Any future source code implementing the onboarding service must use
  `report.is_compatible` (property), not access `.compatible` (removed field). The `not`
  checks on `frozenset` fields rely on empty frozenset being falsy.
- **D3 service signature**: `evaluate_skill_gate()` retains `model_id`, `station_id`,
  `group_id` as input parameters (needed for store queries). Only the result type loses them.
- **D3 call guard**: `artifact_id` on `SkillGateResult` is non-nullable. The skill gate task
  is only reachable after successful artifact storage (step 4 in the orchestration — artifact
  exists in `TRAINING` status). Callers must never invoke `evaluate_skill_gate_task` with a
  None `artifact_id`.
- **D3 `passed` is a `@property`**: `SkillGateResult.passed` is computed from
  `failing_metrics`, not stored. Access as `result.passed` (attribute syntax), not
  `result.passed()` (would raise `TypeError` on a `@property`).
- **D3 container access**: `metric_scores` and `thresholds` are `tuple[tuple[str, float], ...]`.
  Callers needing dict-style lookup use `dict(result.metric_scores)`.
- **D3 failing_metrics rule**: `evaluate_skill_gate()` must treat a missing score as a
  failure — for each configured threshold key absent from `metric_scores`, add the key to
  `failing_metrics`. Zero valid strata + active thresholds = `GATE_REJECTED`.
- **D5 semantic change**: `fetch_groups_for_model` returns groups with an active
  group-level assignment (not just an active artifact). The Protocol stub in
  `protocols/stores.py` needs its doc comment updated. When a concrete store
  implementation is written, it must query `group_model_assignments`, not
  cross-join `station_group_members` with `model_assignments`.
- **D5 naming**: All four design doc references to `store_group_assignment` (§10 × 2,
  §11 P6, §11 P7) must be renamed to `store_group_model_assignment` to match the spec.
- **D4 class variables**: `ONBOARDING_FAILED_OUTCOMES` and `ONBOARDING_SKIPPED_OUTCOMES`
  are module-level public constants (not class-level, to avoid conflicts with `slots=True`
  on frozen dataclasses). Named `UPPER_CASE` per conventions.md.
- **D4 partition-completeness test**: Implementers must add a test verifying that
  `ONBOARDING_FAILED_OUTCOMES | ONBOARDING_SKIPPED_OUTCOMES | {OnboardingOutcome.PROMOTED, OnboardingOutcome.GATE_REJECTED} == set(OnboardingOutcome)`.
  This prevents silent miscounting if a new enum member is added later without updating
  the constant sets.
- **D4 exception mapping**: `InsufficientDataError` → `SKIPPED_NO_DATA` before training
  (steps 1–2); → `FAILED_*` for the current phase once training begins (steps 3+). Other `SapphireError` subclasses → the `FAILED_*` variant for the phase
  that raised them (phase-based, not type-based). `FAILED_HINDCAST` is for catastrophic
  service errors only — individual step failures are absorbed into `hindcast_steps` results.
- **D4 `GATE_REJECTED` in v0**: The gate logic and `GATE_REJECTED` code path are fully
  implemented. With the default `skill_gate_thresholds = {}`, the gate is a pass-through.
  Configuring thresholds activates it immediately — no code changes needed.
- **D4 skill_gate short-circuit**: When `thresholds={}`, `evaluate_skill_gate()` is still
  called but returns a trivially-passed result (empty scores/thresholds, empty
  `failing_metrics`). `skill_gate=None` on `OnboardingUnitResult` means the step was not
  reached, never "not configured."
- **D6 + D7 coupling**: `skill_gate_thresholds` (D6) defines _what_ to check;
  min-across-strata (D7) defines _how_ to aggregate. Both must be implemented together.
  With the default empty thresholds, the gate trivially passes and promotion proceeds.
  Configuring thresholds activates the gate: failing models get `GATE_REJECTED` and the
  artifact remains in `TRAINING`.
- **D6 late promotion**: §6d step ordering is: store artifact (`TRAINING`) → hindcast →
  skill → gate → promote (`ACTIVE`) → assign. `GATE_REJECTED` leaves artifact in
  `TRAINING` status. This matches architecture-context.md's M-series ordering and the
  §1 mermaid diagram.
- **D9 scope determination**: `determine_onboarding_scope()` is called in the flow layer
  (§8) before `onboard_model()`. The flow body parses `list[str]` parameters from Prefect
  into `frozenset[StationId]` / `frozenset[StationGroupId]` at the boundary. Group-scoped
  models with `group_ids=None` and no existing assignments raise `ConfigurationError`
  (a `SapphireError` subclass, per conventions.md) — the operator must provide explicit
  group IDs for initial onboarding.
- **D9 boundary types**: The §8 flow signature keeps `list[str] | None` for Prefect JSON
  compatibility. Inside the flow body, parse immediately to domain types (frozenset of
  NewTypes). The service layer (`determine_onboarding_scope`, `onboard_model`) never sees
  raw strings.
- **Log levels per `logging.md`**: `SKIPPED_*` outcomes → `WARNING` (degraded state, flow
  continues with remaining units). Per-unit `FAILED_*` outcomes → `WARNING` (handled
  per-unit degradation, flow continues with remaining units). At the end of
  `onboard_model`, if `failed_count() > 0`, emit one `ERROR`-level summary event with
  the count and affected units — this is the aggregate signal requiring human attention.
  `PROMOTED` and `GATE_REJECTED` → `INFO` (normal operational outcomes). Follow
  `docs/standards/logging.md` for event naming and context fields.
  Rationale: `logging.md` defines ERROR as "Unrecoverable failure. Requires human
  attention" and WARNING as "Degraded state. Operation continues." Individual unit
  failures are handled (the flow continues), matching WARNING. The aggregate summary
  is the actionable signal for operators, matching ERROR.
- **No source code changes in this plan** — all changes are documentation-only. No test runs
  required.
