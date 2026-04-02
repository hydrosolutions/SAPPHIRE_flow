---
status: READY
created: 2026-03-30
updated: 2026-04-02
scope: design — virtual station types, calculated station formulas, flow impacts, QC propagation
depends_on: [014, 013]  # both DONE/archived; target: v1 (types and schema land in v0)
---

# 015 — Virtual Station Support

## Problem

SAPPHIRE Flow defers virtual stations to v2.0 (plan 011 §B categorization). Virtual
station support is a core modelling capability needed for v1 — one of our modellers
specialises in ungauged catchment prediction and calculated station derivation. This
plan promotes the design to v1.

## v0 Scope

Types and schema land in v0; flow logic is v1-only. Specifically:

**v0 (Phase 1a — types + DB schema):**
- `GaugingStatus` enum added to `types-and-protocols.md` and implemented alongside
  other enums per §G ("implement the full type system"). Once added to the spec,
  `GaugingStatus` falls under §G's blanket "all enums (minus deferred ones)" rule —
  no §G exclusion-list update needed; the exclusion list must **not** include
  `GaugingStatus`. (The spec update is a prerequisite: §G implements what
  `types-and-protocols.md` defines, so the enum must be added there first.)
- `gauging_status TEXT NOT NULL DEFAULT 'gauged'` column on `stations` table
- `conventions.md` enum master list updated with `GaugingStatus`

The `calculated_station_formulas` table and `COMPONENT_DERIVED` enum value are
**deferred to v1** per v0-scope.md §B ("empty 'for later' tables add migration
maintenance burden"). `COMPONENT_DERIVED` is a single enum *value*, not a whole enum —
§G's blanket "all enums" rule covers whole enums, not individual values. To avoid
ambiguity, `COMPONENT_DERIVED` must be explicitly carved out in v0-scope.md §G (as a
deferred value of the non-deferred `ObservationSource` enum) rather than in §B (which
lists tables and full schema items, not individual enum values). The
`calculated_station_formulas` table belongs in §B as usual. The `ObservationSource` enum
is implemented in v0 with its three existing values; `COMPONENT_DERIVED` is added in v1
alongside the flow logic that writes it.

All v0 stations are `GAUGED` and no flow logic branches on `GaugingStatus` in v0. The
cost of carrying the enum and column is negligible; the benefit is that v1
implementation builds on a stable, tested schema.

**v1 (this plan's flow logic):**
- `calculated_station_formulas` table + DB triggers (§D2)
- `COMPONENT_DERIVED` value added to `ObservationSource` enum
- Flow 2 tiered derivation, Flow 5 branching, QC propagation, all other flow
  changes described below
- ForecastInterface contract suggestions for input requirements declaration and
  structured error types (see §D5)

## Two Kinds of Virtual Stations

1. **Ungauged sites** — No observations exist. A location on a river where forecasts
   are desired but no gauge is installed. The model runs on NWP forcing and basin
   characteristics alone (regionalized parameters or ML transfer learning).

2. **Calculated stations** — Derived from gauged tributaries. Typical example: reservoir
   inflow = weighted sum of upstream gauged tributaries. Common in Central Asia.
   Formula: `Q_virtual = Σ(wᵢ × Qᵢ)` where `Qᵢ` are observed flows.

## Design Decisions

### D1. Station classification — new `GaugingStatus` enum

`StationKind` answers "what physical domain?" — `GaugingStatus` answers "does this
station have real observations?" These are orthogonal axes. A calculated reservoir
inflow is still `StationKind.RIVER`.

Note: `StationKind` currently has `WEATHER`/`RIVER` in `architecture-context.md` and
`WEATHER`/`RIVER`/`LAKE` in `types-and-protocols.md`. This pre-existing inconsistency
is out of scope for this plan but should be resolved. If `LAKE` is the intended final
value, virtual stations on lakes (e.g. calculated lake inflow) would use
`StationKind.LAKE` + `GaugingStatus.CALCULATED` — the two axes are orthogonal and
compose correctly regardless of which `StationKind` values exist.

```python
class GaugingStatus(Enum):
    GAUGED = "gauged"
    UNGAUGED = "ungauged"
    CALCULATED = "calculated"
```

New field on `StationConfig`: `gauging_status: GaugingStatus = GaugingStatus.GAUGED`.
Convention: place after existing fields. (`kw_only=True` removes the positional
ordering restriction, so placement is flexible — but appending is clearest.)

New column on `stations` table: `gauging_status TEXT DEFAULT 'gauged'` with CHECK
constraint. Added as **nullable with default** in migration N, then `NOT NULL`
constraint added in migration N+1 — two-step pattern consistent with `cicd.md`
§Rollback's additive-only rule (previous image must be able to insert rows without
knowledge of new columns during rolling deployment). The explicit two-step procedure
is proposed as a new documented pattern in §Standards Document Updates. Existing stations are all `GAUGED` — both
migrations are no-op backfills.

Must be added to `conventions.md` enum master list with scope `v0+v1`.

### D2. Calculated station formula — config-driven weighted sum

Option (a) from the original design: config-driven weighted sum. Options (b) expression
DSL and (c) Python callable are rejected — (b) has formula injection risk not covered
by `security.md` OWASP mitigations, (c) violates the model code trust boundary
("No user-supplied or runtime-loaded model code is permitted").

Weighted sum covers 90%+ of Central Asia use cases. Weights are physical scaling
factors (e.g. catchment area ratios), not normalized probabilities — they need **not**
sum to 1. Validation requires only that each weight is positive and finite (`0 < w <
1e6`); no constraint on the sum. This is intentional: the formula `Q_virtual = Σ(wᵢ ×
Qᵢ)` is a physical aggregation, not a statistical mixture.

**Separate table** (not a field on `StationConfig`) — the component-weight relation is
a queryable dependency graph used by Flow 2 for derivation ordering:

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class ComponentWeight:
    station_id: StationId    # the calculated station
    component_station_id: StationId  # upstream gauged tributary
    weight: float
    valid_from: UtcDatetime
    valid_to: UtcDatetime | None  # None = current; non-None = superseded
    created_at: UtcDatetime

    def __post_init__(self) -> None:
        if not (0 < self.weight < 1e6):
            raise ValueError(
                f"weight must be positive and finite, got {self.weight}"
            )
```

Weight is validated at construction via `__post_init__` (`0 < value < 1e6`), matching
the established pattern for domain types with invariants (`GeoCoord`, `QcFlag`). A
`PositiveWeight` NewType was considered but rejected: `NewType` on `float` provides
zero runtime enforcement — the invariant (`0 < w < 1e6`) can only be enforced via
`__post_init__` validation, making a `NewType` wrapper redundant. The DB CHECK
constraint is the second line of defense, not the only one.

**Composite primary key**: `calculated_station_formulas` uses `PK: (station_id,
component_station_id, valid_from)` — a composite key, not a surrogate `id UUID`. This
follows the established pattern for junction/configuration tables (`model_assignments`,
`station_weather_sources`, `station_thresholds`, `station_group_members`,
`group_model_assignments` — all use composite PKs). Formula rows are not independently
addressable entities; they are always accessed through the station they configure. The
`conventions.md` PK convention ("Primary keys: `id` UUID") does not explicitly list
composite PKs as an exception, but the schema precedent across 5 existing tables is
unambiguous. `conventions.md` should be updated to document this secondary pattern.

`valid_from`/`valid_to` enable formula history: when weights
change, the old row is closed (`valid_to = now()`) and a new row inserted. Derivation
queries filter on `valid_to IS NULL` for the current formula. Historical re-derivation
(Flow 12) uses the formula valid at the observation's timestamp.

```sql
CREATE TABLE calculated_station_formulas (
    station_id           UUID NOT NULL REFERENCES stations(id),
    component_station_id UUID NOT NULL REFERENCES stations(id),
    weight               DOUBLE PRECISION NOT NULL,
    valid_from           TIMESTAMPTZ NOT NULL,
    valid_to             TIMESTAMPTZ,          -- NULL = current formula
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    validity              TSTZRANGE NOT NULL GENERATED ALWAYS AS (
        tstzrange(valid_from, valid_to, '[)')
    ) STORED,
    PRIMARY KEY (station_id, component_station_id, valid_from),
    CHECK (station_id != component_station_id),
    CHECK (weight > 0 AND weight < 1e6),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    EXCLUDE USING gist (
        station_id WITH =,
        component_station_id WITH =,
        validity WITH &&
    )
);
CREATE INDEX idx_csf_component ON calculated_station_formulas(component_station_id);
CREATE INDEX idx_csf_current ON calculated_station_formulas(station_id)
    WHERE valid_to IS NULL;
```

The `EXCLUDE USING gist` constraint prevents overlapping validity periods for the same
`(station_id, component_station_id)` pair at the DB level. This requires the `btree_gist`
extension (`CREATE EXTENSION IF NOT EXISTS btree_gist`). The generated `validity` column
converts the `valid_from`/`valid_to` pair into a `TSTZRANGE` (`NULL` `valid_to` maps to
an open upper bound). This is enforced on every INSERT and UPDATE — application code
cannot create overlapping formula histories regardless of the code path.

Weight validation: values must be positive and finite. The CHECK constraint enforces
this at the DB level. The partial index `idx_csf_current` optimises the common query
path (fetch current formula for derivation).

**DB-level enforcement of the component-must-be-gauged invariant**: A trigger on
`calculated_station_formulas` (INSERT/UPDATE) verifies that the referenced
`component_station_id` has `gauging_status = 'gauged'` in the `stations` table.
Application-level validation in step 5.C2 catches errors at onboarding time, but the
trigger is the safety net: it prevents direct DB inserts, migration scripts, or
post-onboarding status changes from silently violating the constraint. This invariant
is load-bearing for the two-wave derivation ordering in Flow 2 (see §Tiered
Derivation Design) — if it breaks, the pipeline silently computes from stale data.

A complementary trigger on `stations` (UPDATE of `gauging_status`) must reject
changing a station from `GAUGED` to another status if it is referenced as a **current**
component in `calculated_station_formulas` (i.e., rows with `valid_to IS NULL`). The
trigger error message must be actionable: "Cannot change gauging_status: station {id}
is a current component of calculated station {id}. Close the formula first
(`valid_to = now()`)."

**Component decommissioning resolution path**: To decommission a component station, a
model admin must first close all formula rows referencing it (`valid_to = now()`). From
that point, the calculated station's derivation produces `qc_status = 'missing'`
(missing-component skip policy, §Tiered Derivation Design). The model admin then either
configures a replacement formula or suspends the calculated station. This is a deliberate
two-step process — the trigger prevents accidental formula breakage while keeping the
decommissioning path explicit and auditable.

**Audit trail for formula changes**: Formula creation and closure are tracked via
`audit_log` entries, consistent with the codebase pattern (no inline `created_by` /
`modified_by` columns on configuration tables — attribution lives in the append-only
`audit_log`). New `AuditEventType` values: `FORMULA_CONFIGURED` (formula row inserted)
and `FORMULA_CLOSED` (formula row's `valid_to` set). Both log `actor_id` = the model
admin, `target_type` = `"calculated_station_formula"`, `target_id` = the calculated
station ID, `detail` = component station IDs and weights. The `model_admin` role is the
appropriate principal — they already manage model assignments and station configurations.

**Trigger migration strategy**: Both triggers are created via Alembic migrations using
`op.execute()` with raw SQL `CREATE OR REPLACE FUNCTION` + `CREATE TRIGGER` statements.
Each trigger gets its own migration revision (not bundled with table DDL) so that
rollback is granular: `op.execute("DROP TRIGGER ...")` + `op.execute("DROP FUNCTION
...")` in the downgrade path. Trigger functions use `RAISE EXCEPTION` for constraint
violations (surfaced as `IntegrityError` by asyncpg). `cicd.md` must be updated to
document this pattern (see §Standards Document Updates).

### D3. Model assignment — all virtual stations require models

Both ungauged and calculated stations **must** have forecast models assigned:
- Ungauged stations need models deployed via transfer learning (5.11 Branch A:
  existing `GroupForecastModel` applied to new station) or trained with regionalized
  parameters (a `StationForecastModel` implementation detail, not a new model type).
  Both rely on NWP forcing + basin characteristics.
- Calculated stations need models to produce forecasts — the formula only derives
  observations, not forecasts

The formula applies to **observations only** (Flow 2). Forecasts are always produced
by model runs (Flow 1). `OperationalForecast.model_id` and `model_artifact_id` remain
non-optional. The standard go-live precondition (`≥1 active model artifact`) applies
to all station types including calculated stations.

**Future extension (low priority)**: A `DerivedForecast` type could allow computing
forecasts from component station forecasts via the weighted-sum formula, analogous to
how derived observations work. This is deferred — not needed for v1.

### D4. Observation handling

**Ungauged stations**: No observations → no observation QC, no skill scores against
observations, no rating curves. Flow 2 pre-filters to exclude ungauged stations
before the fan-out.

**Calculated stations**: After component observations are ingested and QC'd by the
standard Flow 2 path, a derivation step computes `Q_virtual = Σ(wᵢ × Qᵢ)` and stores
the result with a new `ObservationSource` value:

```python
class ObservationSource(Enum):
    MEASURED = "measured"
    RATING_CURVE_DERIVED = "rating_curve_derived"
    MANUAL_IMPORT = "manual_import"
    COMPONENT_DERIVED = "component_derived"   # v1 only: calculated station aggregation
```

`conventions.md` enum master list: `COMPONENT_DERIVED` is added to the
`ObservationSource` row in v1 alongside the flow logic that writes it.

### D5. `past_targets` for ungauged stations

`StationInputData.past_targets` (the data payload within `StationModelInputs`) stays
`pl.DataFrame` (non-optional). For ungauged stations, it is a **zero-row DataFrame
with the correct column schema** (timestamp + target parameter columns). Models must
not assume `height > 0`.

**ForecastInterface contract**: The orchestrator follows the standard ForecastInterface
interaction pattern — no orchestrator-side input sufficiency checks:

1. **Query model for input requirements** — the model declares what past known data it
   needs (features and forecast targets are distinct; either may be absent)
2. **Deliver what's available** — for ungauged stations, `past_targets` is zero-row;
   some past known features may also be absent on occasion for any station type
3. **Model decides** — the model validates its own inputs and either produces forecasts
   (per variable + success indicator) or returns a structured error defined in
   ForecastInterface
4. **On model error** — the orchestrator uses the baseline model's forecast, which runs
   in parallel (see D5a)

The `min_rows` check previously proposed for `PastKnownVariable` is **not needed on
the orchestrator side** — input validation is the model's responsibility per the
ForecastInterface contract. Suggestions for ForecastInterface improvements:
- A method for the model to declare input requirements (features vs targets, required
  vs optional)
- A structured error type for "insufficient input data" (distinct from runtime errors)
- A per-variable success/failure return type

The 4-slot model input contract wording in `architecture-context.md` ("Always present
for stateful models") must be updated to: "Always non-None. May be zero-row for
ungauged stations (`GaugingStatus.UNGAUGED`)."

### D5a. Baseline model dependency

All stations (gauged, ungauged, calculated) require a **baseline model** — a simple,
always-available forecast (e.g. climate norm, linear regression) that serves as both
skill comparison reference and fallback when the primary model fails. For ungauged
stations, the baseline is the initial operational model until a more sophisticated model
is trained. The baseline model design (model types, assignment policy, Flow 1 execution
pattern) is out of scope for this plan — see Plan TBD (Related Plans). **Hard
dependency**: baseline model plan must be completed before ungauged station support
ships.

### D6. QC flag propagation for calculated stations

When component stations have QC flags, the calculated station's derived observation
inherits **weighted QC status** — severity is weighted by the component's contribution
weight in the formula.

New **observation** QC rule convention (distinct from the existing forecast QC rule
ID list in `conventions.md` — a new observation QC rule ID row must be added to the
enum master list):

- `rule_id`: `"upstream_propagated"`
- `detail`: structured JSON string encoding provenance, e.g.
  `{"component_station_id": "...", "component_status": "qc_suspect", "weight": 0.4}`

**Aggregation policy**: Derivation only proceeds when all components have `QC_PASSED`
or `QC_SUSPECT` status (see §Missing component observations — `QC_FAILED` and missing
observations trigger a skip). Each component's worst QC status is mapped to a numeric
severity (`QC_PASSED`=0, `QC_SUSPECT`=1) — the same mapping used by the existing
`aggregate_qc_status()` function. The calculated station's propagated severity is the
weight-averaged severity across components, rounded **up** to the nearest integer:
`severity = ceil(Σ(wᵢ × severityᵢ) / Σ(wᵢ))`. This means:
- All components `QC_PASSED` → `QC_PASSED` (`ceil(0) = 0`)
- Any `QC_SUSPECT` component → `QC_SUSPECT` (`ceil()` of any positive value ≥ 1)

In practice, with the skip-on-`QC_FAILED` policy, the severity range is [0, 1] and
`ceil()` produces a binary outcome: either all components passed (→ `QC_PASSED`) or at
least one is suspect (→ `QC_SUSPECT`). This is the correct conservative behavior — a
derived value that incorporates any suspect input is itself suspect. The `ceil()`
formulation is retained (rather than simplifying to `max()`) because it generalises
cleanly if the skip policy is ever relaxed to admit `QC_FAILED` components in the
future.

`ceil()` is chosen over `round()` for conservatism: when ambiguous, escalate. Python's
`round()` uses banker's rounding (`round(0.5) = 0`), which would silently resolve
ambiguous cases toward the less severe status — unacceptable for a safety-critical
propagation path.

Propagated flags are added alongside any locally-computed flags on the derived
observation. `aggregate_qc_status()` then picks the worst across all flags as usual.

### D7. Basin delineation

Virtual stations need basin outlines for NWP extraction. Two paths:
- **HydroSHEDS** — our own product, pre-computed basin outlines worldwide
- **User upload** — custom basin outlines (GeoJSON/Shapefile)

Both should be supported. Integration details deferred — HydroSHEDS is an internal
product and the API design is evolving independently.

Note: `architecture-context.md` currently references HydroATLAS + MERIT DEM for basin
attributes in Flow 0/5. HydroSHEDS basin outlines are a related but distinct product.
Reconciliation of these two data sources is deferred.

File upload security requirements (MIME validation, size limits, geometry complexity
limits, authorization) must be added to `security.md` before this feature ships.
**Hard gate**: the security.md §File Upload section and authorization matrix entry
must be merged before basin outline upload implementation begins.

### D8. Network identifiers for virtual stations

Virtual stations need a `network` value for the `(network, code)` composite unique
constraint on `stations`. Convention: use the same network as the deployment (e.g.
`"bafu"`, `"dhm"`) with a station `code` that follows deployment-specific naming. The
`network` identifies the organizational owner, not the data source — virtual stations
are owned by the same organization as their component stations.

## Flow Impact Analysis

All flows that touch station processing are affected. When no calculated stations are
present (including all of v0), existing flow logic is unmodified — Flow 2 remains a
single homogeneous fan-out. When calculated stations exist (v1), Flow 2 is restructured
into a two-wave fan-out with a sync barrier (see §Tiered Derivation Design). Other
changes are additive (pre-filters, new branches).

| Flow | Ungauged stations | Calculated stations |
|------|-------------------|---------------------|
| **Flow 0** (deployment onboarding) | Basin delineation for ungauged sites (HydroSHEDS / user upload) | Same |
| **Flow 1** (forecast cycle) | Model runs with zero-row `past_targets`; input prep step 1.7 passes zero-row DataFrame with correct schema | Standard model run — model uses `COMPONENT_DERIVED` observations as `past_targets` like any other observation source |
| **Flow 2** (observation ingest) | **Skipped** — pre-filter excludes ungauged stations | New derivation step after standard QC: compute `Q_virtual = Σ(wᵢ × Qᵢ)` from component observations with `QC_PASSED` or `QC_SUSPECT` status (skip on `QC_FAILED` or missing — see §Tiered Derivation Design), store with `source=COMPONENT_DERIVED`, propagate QC flags (D6). Ordering: calculated stations derived after their components complete QC |
| **Flow 3** (forecast review) | No observation overlay in review UI | Display formula breakdown (component contributions) alongside forecast |
| **Flow 4** (pipeline monitoring) | **Exclude** from observation staleness checks — no expected observations | Monitor that component stations are fresh; derived freshness is implicit |
| **Flow 5** (station onboarding) | Modified path — see below. **v1 impl note**: `services/onboarding.py:_run_onboarding()` currently runs QC/baselines/flow-regimes for all stations unconditionally — must add `gauging_status` branching (ungauged skips steps 5.4–5.9) | Modified path — see below |
| **Flow 5w** (weather station onboarding) | No impact — virtual stations are non-weather (`StationKind.RIVER` etc.) | No impact |
| **Flow 6/9** (model training/retraining) | Standard training path — `past_targets` may be zero-row for ungauged stations; model implementation handles regionalized parameters internally. **v1 impl note**: `services/scope.py:determine_training_scope()` filters by `station_status` only — must also consider `gauging_status` for ungauged stations with regionalized training | Standard training path — model trains on `COMPONENT_DERIVED` observations |
| **Flow 7** (hindcast generation) | Hindcast with zero-row `past_targets`; model must handle this | Standard hindcast — model uses historical `COMPONENT_DERIVED` observations |
| **Flow 8/10** (skill computation) | **No skill scores** — no observations to verify against. Alternative: cross-validation on regionalized model parameters (future work) | Standard skill computation — model forecasts verified against `COMPONENT_DERIVED` observations. Note: baselines (5.8) and flow regimes (5.9) computed from accumulated `COMPONENT_DERIVED` history — see onboarding notes |
| **Flow 11** (NWP archive) | No change — ungauged stations still consume NWP | No change |
| **Flow 12** (observation reprocessing) | No impact — no observations to reprocess | **Branch A** (rating curve reprocessing): changes component `rating_curve_derived` observations → must re-derive downstream calculated stations using the two-wave pattern (post-Branch-A step). **Branch B** (source correction): if the correction changes observation **values** (not just source metadata), must re-derive downstream calculated stations using the same two-wave pattern; if only source metadata changes, no re-derivation needed. Conservative default: always re-derive after Branch B on component stations. **Branch C** (QC re-evaluation): after Branch C completes on component stations, re-derive calculated stations using the same two-wave pattern. This is a post-Branch-C step, not part of Branch C itself |
| **Flow 13** (model onboarding) | No new model types. Ungauged stations use existing `GroupForecastModel` (via 5.11 Branch A transfer learning) or `StationForecastModel` with regionalized parameters — both are training/deployment strategies, not new Protocols | No new model types needed — uses standard models |

## Tiered Derivation Design (Flow 2 + Flow 12)

### Two-wave fan-out

Calculated station observations depend on their component stations' QC-completed
observations (status `QC_PASSED` or `QC_SUSPECT` — see §Missing component observations). The standard `task.map()` fan-out is homogeneous and cannot express
inter-station dependencies. Solution: a **two-wave fan-out with an explicit sync
barrier**, combining `task.map()` with a `.result()` gather (a new pattern proposed for
`orchestration.md` — see §Standards Document Updates).

```
Wave 1:  task.map(gauged_stations)       → [f.result() for f in futures]  ← sync
Wave 2:  task.map(calculated_stations)   → derive from wave 1 results
```

Ungauged stations are excluded entirely (no observations to process).

**Thread pool pressure**: The two-wave pattern issues two `task.map()` calls per
Flow 2 invocation. Per `orchestration.md`, the default `ThreadPoolTaskRunner` spawns
one OS thread per mapped item — at ~1000 stations, Wave 1 already saturates the pool.
Wave 2 (calculated stations, a small minority) runs after Wave 1 completes, so the
peak thread count is `max(len(gauged), len(calculated))`, not the sum. Still, the
`max_workers` cap recommended in `orchestration.md` must apply to both waves.

This works because the DB-level trigger (§D2) guarantees that component stations are
always `GAUGED` at formula-insertion time — so the dependency graph is always exactly
two tiers. No topological sort is needed. Note: the trigger provides a *schema-time*
guarantee, not a *read-time* guarantee — during a live Wave 2 derivation, a concurrent
admin could theoretically close a formula and change a component's status between Wave 1
completion and Wave 2's read. This is an accepted trade-off: the window is extremely
narrow, the per-station write lock serialises observation writes, and the component reads
see the latest committed state. See §Flow 12 interaction below for the full concurrency
analysis. If the constraint is relaxed in the future (calculated stations with calculated
components), the two-wave approach must be replaced with an N-tier topological sort using
`graphlib.TopologicalSorter`.

### Pre-fetch formulas at flow start

`formula_store.fetch_formulas_for_stations()` is called once at flow start for all
calculated stations, not inside each task. At v1 scale (~1000 stations), calculated
stations will be a small minority — the pre-fetch is a single cheap query. Pass the
result via `unmapped()`.

### Missing component observations

**Derivation proceeds** when all component stations have an observation for the current
time window with QC status `QC_PASSED` or `QC_SUSPECT`. The QC propagation rule (§D6)
then determines the derived observation's QC status based on weighted component severity.

**Derivation is skipped** when any component station has no observation for the current
time window (data gap, source outage) **or** has an observation with `QC_FAILED` status.
A placeholder observation is stored for the calculated station with
`qc_status = 'missing'`. Rationale: a partial weighted sum is not a valid derivation —
the formula semantics require all components. `QC_FAILED` observations are excluded
because they represent known-bad data that should not propagate into derived values.
Missing data propagates honestly rather than producing a silently degraded value.
Pipeline monitoring (Flow 4) detects the missing derivation via component station
freshness checks.

The distinction: `QC_SUSPECT` means "possibly degraded, flagged for review" and is safe
to propagate with appropriate QC flag inheritance (§D6). `QC_FAILED` means "known bad"
and must not enter the derivation.

### Flow 12 Branches A and C — same two-wave pattern

When reprocessing a time window that affects calculated stations (Branch A: rating
curve reprocessing changes component `rating_curve_derived` observations; Branch C:
QC re-evaluation changes component QC status), apply the same two-wave structure:
reprocess component stations first (wave 1), then re-derive calculated stations
(wave 2). The existing per-station write lock
(`concurrency("observation_write:{station_id}")`) guards each station's write
independently — no chain-wide lock is needed.

Edge case: if Flow 2 and Flow 12 overlap on the same calculated station, the
per-station concurrency slot serialises the writes. The component *reads* during
re-derivation are not locked, so they see the latest committed state. In steady-state
operation this is correct (component observations are already committed before the
calculated station's wave 2 runs). The theoretical race — Flow 12 re-deriving while
Flow 2 is mid-write on a component — produces an observation derived from the
pre-update component values. This is an accepted trade-off: Flow 12 is
operator-triggered on historical windows, Flow 2 runs on current data, and temporal
overlap is rare.

## Onboarding Flow Modifications (Flow 5)

Flow 5 needs three branches based on `GaugingStatus`. Step numbers reference
`architecture-context.md` Flow 5 steps.

**GAUGED** (existing path, unchanged):
Steps 5.1–5.12 as currently defined.

**UNGAUGED**:
- 5.1 Register station metadata (with `gauging_status = 'ungauged'`)
- 5.2 Fetch catchment attributes (basin geometry from HydroSHEDS / user upload)
- 5.3 Configure weather source mappings (NWP extraction config — critical for
  ungauged stations since models depend entirely on NWP forcing)
- Skip 5.4–5.9 (no historical obs, no QC, no rating curves, no baselines, no flow
  regimes)
- 5.10 Assign model (required — ungauged stations must have a model)
- 5.11 Model readiness (branch A: transfer learning with existing group artifact, or
  branch B/C: train new station/group model with regionalized parameters)
- 5.12 Go-live — standard precondition: `≥1 active model artifact`

Onboarding checklist for ungauged stations:
- ✅ Station metadata registered
- ✅ Catchment attributes available
- ✅ Weather source mapped
- ⬜ Historical observations — N/A (ungauged)
- ⬜ Baselines / flow regimes — N/A (ungauged)
- ✅ At least one model artifact active
- ⬜ Alert thresholds defined (optional)

**CALCULATED**:
- 5.1 Register station metadata (with `gauging_status = 'calculated'`)
- 5.2 Fetch catchment attributes (basin geometry required for NWP extraction)
- 5.3 Configure weather source mappings
- 5.C1 Configure formula: specify component stations + weights (all components must
  already be `OPERATIONAL`)
- 5.C2 Validate formula: check components exist, weights are positive and finite,
  component stations have `gauging_status = 'gauged'`, no circular dependencies
  (note: the circular dependency check is technically redundant — the gauging-status
  check already prevents calculated→calculated references, which makes cycles
  impossible by construction. Retained as belt-and-suspenders defense.)
- Skip 5.4–5.7 (no direct historical obs import, no rating curves)
- 5.C3 Bootstrap derived observation history: apply formula retroactively to available
  component observation history, store with `source = 'component_derived'`. Derived
  observations **skip Stage 1/Stage 2 QC** (sensor-range validation is meaningless
  for computed values) — they inherit QC status from their components via the D6
  propagation rule
- 5.8 Compute baseline artifacts from `COMPONENT_DERIVED` observation history
  (required for skill computation in Flows 8/10)
- 5.9 Compute flow regime boundaries from `COMPONENT_DERIVED` observation history
  (required for stratified skill in Flows 8/10)
- 5.10 Assign model (required — calculated stations need models for forecasting)
- 5.11 Model readiness (trains on `COMPONENT_DERIVED` observations)
- 5.12 Go-live — standard precondition: `≥1 active model artifact`

Onboarding checklist for calculated stations:
- ✅ Station metadata registered
- ✅ Catchment attributes available
- ✅ Weather source mapped
- ✅ Formula configured and validated (5.C1 + 5.C2)
- ✅ Derived observation history bootstrapped (5.C3)
- ✅ Baseline artifacts computed (from derived obs)
- ✅ Flow regime boundaries computed (from derived obs)
- ✅ At least one model artifact active
- ⬜ Alert thresholds defined (optional)

## Standards Document Updates Required

The following standards documents need updates when this plan is implemented:

- **`types-and-protocols.md`**: ~~Add `GaugingStatus` enum~~ (done, v0); ~~add
  `gauging_status` field to `StationConfig`~~ (done, v0); add `ComponentWeight`
  dataclass (with `__post_init__` weight validation); add `FormulaStore` Protocol
  (entity-based store for `calculated_station_formulas`); add optional
  `gauging_status: GaugingStatus | None` parameter to `StationStore.fetch_all_stations()`
  and `fetch_stations_by_ownership()` (required for v1 two-wave fan-out partitioning in
  Flow 2); add `FORMULA_CONFIGURED` and `FORMULA_CLOSED` to
  `AuditEventType` (v1 only — `AuditEventType` is §G-deferred, so these values land
  alongside the v1 flow logic, not in Phase 1a); add `COMPONENT_DERIVED` to
  `ObservationSource` (v1)
- **`architecture-context.md`**: Update 4-slot model input contract wording for
  `past_targets` — replace "Always present for stateful models" with "Always non-None.
  May be zero-row for ungauged stations (`GaugingStatus.UNGAUGED`)" (also fix
  pre-existing H.3→H.4 error in same paragraph); add `COMPONENT_DERIVED` to
  `ObservationSource` enum definition **and** `observations` table column comment; add
  `calculated_station_formulas` table to DB schema; add `gauging_status` column to
  `stations` table; add pre-filter step to Flow 2 step table (ungauged station
  exclusion); add ungauged exclusion note to Flow 4 step 4.2; update Flow 5 sequencing
  diagram — for CALCULATED stations, steps 5.8/5.9 depend on 5.C3 (not 5.5/5.7);
  update `suspended → operational` transition note to acknowledge `GaugingStatus`
  context; document ForecastInterface as the model contract (see Related Plans)
- **`v0-scope.md`**: ~~Update §C `stations` table entry to include `gauging_status`
  column~~ (done, v0); ~~add `calculated_station_formulas` table to §B deferred-items
  list~~ (done, v0); ~~add `COMPONENT_DERIVED` as a deferred value of
  `ObservationSource` in §G~~ (done, v0); ~~add §I entry warning against hard-coding
  "all stations are GAUGED" assumptions in v0 flow code~~ (done, v0)
- **`conventions.md`**: ~~Add `GaugingStatus` to enum master list~~ (done, v0);
  ~~add `component_derived` to `ObservationSource` values~~ (done, v0);
  add new **observation QC rule ID** row to enum master list (distinct from existing
  forecast QC rule IDs) — register all known observation rule IDs (`range_check`,
  `rate_of_change`, `frozen_sensor`, `spike`, `gross_outlier`, `upstream_propagated`)
  to avoid an incomplete registry. Note: `range_check` intentionally appears in both
  forecast and observation lists — the rule ID string is shared but the implementations
  are domain-specific (sensor-range validation for observations, physical-range
  validation for forecasts); document composite PK as a secondary convention for
  junction/configuration tables (5 existing tables already follow this pattern);
  add `FORMULA_CONFIGURED` and `FORMULA_CLOSED` to `AuditEventType` values
- **`orchestration.md`**: Add note to Flow 5 table entry about UNGAUGED/CALCULATED
  branches; document the two-wave derivation pattern in Flow 2 as a new pattern (sync
  barrier between gauged and calculated station processing — this extends the existing
  `task.map()` convention, not an existing pattern), the formula pre-fetch convention
  via `unmapped()`, the missing-component skip policy, and the same two-wave pattern
  for Flow 12 Branches A and C; update concurrency controls section to cover the
  inter-station dependency read gap in the two-wave pattern (component reads are
  unprotected by the per-station write lock — accepted trade-off, must be documented)
- **`logging.md`**: Add `gauging_status` as recommended context field bound inside
  each Wave 2 derivation task (per-task scope via `bound_contextvars()`, not
  flow-level); add event taxonomy entries for `observation.derivation_started` /
  `observation.derivation_completed` (paired per `_started`/`_completed` convention,
  with `duration_ms`), `station.formula_validation_failed` (scoped under `station`
  entity, not `formula` — formulas are configuration artifacts, not runtime domain
  objects); Wave 2 derivation tasks inherit `log_prints=False` from the existing
  "all tasks in Flows 1 and 2" rule
- **`security.md`**: Add file upload validation section (MIME types, size limits,
  geometry complexity) before basin outline upload ships; add authorization matrix
  entry for basin outline upload endpoint
- **`cicd.md`**: Document trigger migration pattern — triggers managed via dedicated
  Alembic revisions using `op.execute()` with `CREATE OR REPLACE FUNCTION` /
  `CREATE TRIGGER` in upgrade and `DROP TRIGGER` / `DROP FUNCTION` in downgrade;
  document the two-step column addition pattern for `NOT NULL` columns with defaults
  (nullable + default in migration N, `NOT NULL` constraint in migration N+1);
  add `btree_gist` to the `init` service's first-boot extension list alongside
  `postgis`, `pg_partman`, and `pg_cron` (consistent with the existing pattern where
  all extensions are created in the init step, not via Alembic migrations); document
  the `btree_gist` requirement for exclusion constraints on
  `calculated_station_formulas`
- **`wmo.md`**: Note that `GaugingStatus` has no direct WMO-49 Vol III or WMO-168
  precedent (WMO-168 discusses ungauged catchments in hydrological practice but has no
  formal enum equivalent); clarify WIGOS ID policy for virtual stations (`wigos_id =
  NULL` is acceptable — column is already nullable; virtual stations are excluded from
  WIGOS-compliant station exchange)

## Urgency

v1 target. Core modelling capability — one team member specialises in this area.
Design should be completed before v1 station onboarding is finalised.

## Related Plans

- **Plan 014** (DONE, archived) — ForecastInterface adapter design. Ungauged models go
  through this interface. The `min_rows` upstream change previously proposed here is no
  longer needed — models validate their own inputs per the ForecastInterface contract
  (§D5). Tasks 1–2 implemented (output adapter + enum alignment); task 3 in-flight (FI
  input types PR); tasks 4–5 blocked on v1 FI interface/ module.
- **Plan 013** (DONE, archived) — v0 scale re-evaluation. Confirms virtual stations are
  not in v0 station set.
- **Plan 017** — Manual vs automatic station support. Orthogonal axis
  (`AutomationLevel`) on station metadata, identified during this plan's review.
- **Plan TBD — Baseline model design** — Baseline/fallback model for all stations
  (climate norm, linear regression, etc.), parallel execution in Flow 1, model
  assignment policy. Required before ungauged station support ships. Scope:
  architecture-context.md model assignment update.
- **Plan TBD — ForecastInterface architecture update** — Document ForecastInterface
  as the model contract in architecture-context.md. Covers: input requirements
  declaration, structured error types, per-variable success/failure, orchestrator
  interaction pattern. Coordinate with hydrosolutions/ForecastInterface.

## Origin

Extracted from plan 011 §B. Promoted from v2.0 to v1.
