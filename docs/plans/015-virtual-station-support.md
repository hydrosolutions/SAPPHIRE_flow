---
status: DRAFT
created: 2026-03-30
updated: 2026-07-20
# RE-DRAFT 2026-07-20 (grill-me owner-resolved): narrowed to CALCULATED (component-
# derived) stations only. UNGAUGED support split to a separate deferred plan. Formula
# audit via structlog now (audit_log/AuditEventType rows deferred to the Wave-3
# auth/audit plan). Flow 12-triggered re-derivation deferred to a follow-up. The v0
# GaugingStatus enum slice shipped (1a88f92); COMPONENT_DERIVED + GaugingStatus.CALCULATED
# now exist in code (Plan 035 Task 2 shipped COMPONENT_DERIVED). Reverted READY→DRAFT per
# Plan 106 §4. Must merge before Plan 017 starts.
scope: design — CALCULATED station formulas, Flow 2 step-2.5 derivation, QC propagation, Flow 5 CALCULATED onboarding
depends_on: [014, 013]  # both DONE/archived; target: v1 (types + GaugingStatus enum landed in v0)
---

# 015 — Calculated Station Support (component-derived observations)

## Re-draft scope (2026-07-20) — calculated stations only

Originally "Virtual Station Support" covering **two** kinds of virtual station. Per an
owner grill-me it is narrowed to the buildable, self-contained half:

- **IN SCOPE — Calculated stations** (`GaugingStatus.CALCULATED`): discharge derived from
  gauged tributaries by a config-driven weighted sum `Q_virtual = Σ(wᵢ · Qᵢ)`, stored
  with `source = 'component_derived'`. Self-contained and testable now (even against Swiss
  BAFU gauged discharge). Covers: `calculated_station_formulas` table + triggers (D2),
  the Flow 2 step-2.5 derivation, QC propagation (D6), and the Flow 5 CALCULATED
  onboarding branch.
- **SPLIT OUT — Ungauged stations** (`GaugingStatus.UNGAUGED`, no observations): moved to
  a **separate deferred plan**, hard-blocked on two unbuilt plans — the **baseline-model
  design** (D5a) and **basin-outline upload** (D7, with a `security.md` file-upload gate).
  The ungauged-specific content below is **reference-only** and moves to that plan:
  D3-ungauged, D4-ungauged, **D5**, **D5a**, **D7**, the **UNGAUGED branch of the Flow 5
  onboarding spec** (steps 5.1–5.12 + its checklist), the file-upload `security.md`
  requirements, the WMO/WIGOS virtual-station identity items in D8/§Standards, and the
  **"Ungauged stations" column of the Flow Impact table**. Each such section below now
  carries an inline `⚠ REFERENCE-ONLY (ungauged plan)` marker.

  > **NOTE:** the ungauged material should ultimately move into a stub
  > `016-ungauged-station-support.md`, but this revision is constrained to editing *this*
  > file only, so it is marked `⚠ REFERENCE-ONLY` in place; creating the stub is a
  > mechanical follow-up for the owner. Implementers MUST NOT build any `⚠ REFERENCE-ONLY`
  > section from this plan.

**Owner-resolved forks (this re-draft implements):**
1. **Formula audit** — `FORMULA_CONFIGURED` / `FORMULA_CLOSED` need `audit_log` /
   `AuditEventType`, which are not built. **Decision: log formula create/close via
   structlog now; the `audit_log` rows land with the Wave-3 auth/audit plan.** Not a gate.
2. **Flow 12-triggered re-derivation** — couples to the `reprocess_observations` flow
   (blocked on the DHM producer). **Decision: build Flow 2 forward-derivation now; the
   Flow 12 Branch A/C re-derivation is a follow-up** once that flow exists.

**Already shipped since first draft:** the `GaugingStatus` enum + `stations.gauging_status`
column (v0, `1a88f92`); `GaugingStatus.CALCULATED`; and `ObservationSource.COMPONENT_DERIVED`
(Plan 035 Task 2, `#101`) — so those "add the enum value" tasks are done; this plan wires
the flow logic that writes it.

## Problem

SAPPHIRE Flow defers virtual stations to v2.0 (plan 011 §B categorization). Calculated
station support is a core modelling capability needed for v1 — one of our modellers
specialises in calculated station derivation (Central Asia: reservoir inflow = weighted
sum of upstream gauged tributaries). This plan promotes that design to v1.

## v0 Scope

Types and enum values already landed in v0; the table + flow logic are this plan's v1
work. Specifically:

**Already shipped in v0 (do NOT re-implement):**
- `GaugingStatus` enum + `gauging_status TEXT NOT NULL DEFAULT 'gauged'` column on
  `stations` (`1a88f92`; enum at `src/sapphire_flow/types/enums.py:187-190`, DB CHECK at
  `src/sapphire_flow/db/metadata.py:122-126`), including `GaugingStatus.CALCULATED`.
- `ObservationSource.COMPONENT_DERIVED` (Plan 035 Task 2, `#101`) — the enum member
  exists (`src/sapphire_flow/types/enums.py:183`, ordered third, before `MANUAL_IMPORT`)
  **and** is already admitted by the `observations.source` CHECK constraint
  (`src/sapphire_flow/db/metadata.py:296-303`). No enum/CHECK change is in scope; only
  the Flow 2 logic that *writes* `COMPONENT_DERIVED` rows is v1 work.
- `conventions.md` enum master list already carries `GaugingStatus` and
  `component_derived`.

So the "add the enum value" and "carve out in v0-scope.md §G" tasks from the original
draft are **done** — struck here to avoid directing edits for already-merged code.

**v1 (this plan's flow logic):**
- `calculated_station_formulas` table + DB trigger (§D2)
- Flow 2 tiered derivation, Flow 5 branching, QC propagation, all other flow
  changes described below
- ForecastInterface contract suggestions for input requirements declaration and
  structured error types (see §D5, reference-only)

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

The `GaugingStatus` enum, the `StationConfig.gauging_status` field, and the
`stations.gauging_status TEXT NOT NULL DEFAULT 'gauged'` column with CHECK constraint
are **already shipped** — see §v0 Scope "Already shipped" (`1a88f92`;
`src/sapphire_flow/types/enums.py:187-190`, `src/sapphire_flow/db/metadata.py:122-129`
with `nullable=False`, `docs/spec/types-and-protocols.md:788`). Do **NOT** run an
add-column migration: the column already exists and is already `NOT NULL`, so a
migration N (add column) would fail outright and a migration N+1 (add NOT NULL) is
redundant. D1 exists only to establish the StationKind-vs-GaugingStatus orthogonality
rationale above; no enum, field, migration, or `conventions.md` change is in this plan's
scope.

### D2. Calculated station formula — config-driven weighted sum

Option (a) from the original design: config-driven weighted sum. Options (b) expression
DSL and (c) Python callable are rejected — (b) has formula injection risk not covered
by `security.md` OWASP mitigations, (c) violates the model code trust boundary
("No user-supplied or runtime-loaded model code is permitted").

Weighted sum covers 90%+ of Central Asia use cases. Weights are physical scaling
factors (e.g. catchment area ratios), not normalized probabilities — they need **not**
sum to 1. This is intentional: the formula `Q_virtual = Σ(wᵢ × Qᵢ)` is a physical
aggregation, not a statistical mixture.

**Signed weights (contract alignment — blocker fix).** The committed schema in
`architecture-context.md:2637-2655` defines this table with **signed** weights
("difference formulas use negative weights", e.g. reach gain/loss = downstream −
upstream) and a per-**parameter** formula. An earlier draft of this plan proposed
positive-only weights (`0 < w < 1e6`), a composite PK, and `valid_from/valid_to` naming —
a direct conflict with that contract. **Decision: this plan conforms to the committed
architecture** rather than rewriting it. Weights are therefore signed; validation
requires each weight be **finite and nonzero** with bounded magnitude
(`w != 0 AND -1e6 < w < 1e6`) — a zero weight is a configuration error (the component
would contribute nothing), and the magnitude bound guards against fat-finger overflow.
The formula is **parameter-scoped** (`parameter` column). Because weights are signed,
any QC/severity aggregation over components MUST NOT divide by a naive `Σ wᵢ` (which can
be zero or negative) — see §D6, which uses `max()` over component severities and never
touches the weights.

**Separate table** (not a field on `StationConfig`) — the component-weight relation is
a queryable dependency graph used by Flow 2 for derivation ordering:

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class ComponentWeight:
    id: FormulaId                    # surrogate UUID PK (matches committed schema)
    calculated_station_id: StationId  # the calculated station (gauging_status = 'calculated')
    component_station_id: StationId   # contributing gauged station
    parameter: str                    # canonical parameter this formula derives, e.g. "discharge"
    weight: float                     # signed wᵢ (negative allowed for difference formulas)
    effective_from: UtcDatetime
    effective_to: UtcDatetime | None  # None = current; non-None = superseded
    created_at: UtcDatetime

    def __post_init__(self) -> None:
        if not (self.weight != 0 and -1e6 < self.weight < 1e6):
            raise ValueError(
                f"weight must be nonzero and finite (|w| < 1e6), got {self.weight}"
            )
```

Weight validated via `__post_init__` (nonzero, finite, `|w| < 1e6`), following the
`GeoCoord`/`QcFlag` pattern — no `NewType` wrapper (a `NewType` on `float` carries zero
runtime enforcement). The DB CHECK is the second line of defense.

**Surrogate PK (`id UUID`), matching the committed schema.** `architecture-context.md`
already defines this table with `id: UUID PK`, `calculated_station_id`,
`component_station_id`, `parameter`, `effective_from`/`effective_to`. This plan adopts
that verbatim (the earlier composite-PK / `valid_from` proposal is dropped as a contract
conflict). No `conventions.md` "secondary composite-PK pattern" change is needed.

`effective_from`/`effective_to` enable formula history: when weights change, the old row
is closed (`effective_to = now()`) and a new row inserted. Derivation queries filter on
`effective_to IS NULL` for the current formula. Historical re-derivation (Flow 12) uses
the formula valid at the observation's timestamp.

```sql
CREATE TABLE calculated_station_formulas (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    calculated_station_id UUID NOT NULL REFERENCES stations(id),
    component_station_id UUID NOT NULL REFERENCES stations(id),
    parameter            TEXT NOT NULL,        -- e.g. "discharge"
    weight               DOUBLE PRECISION NOT NULL,
    effective_from       TIMESTAMPTZ NOT NULL,
    effective_to         TIMESTAMPTZ,          -- NULL = current formula
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (calculated_station_id != component_station_id),
    CHECK (weight != 0 AND weight > -1e6 AND weight < 1e6),  -- signed, nonzero, bounded
    CHECK (effective_to IS NULL OR effective_to > effective_from)
);
CREATE INDEX idx_csf_component ON calculated_station_formulas(component_station_id);
-- At most one CURRENT formula per (calculated_station, component, parameter) triple.
-- Partial UNIQUE index — mirrors the rating_curves precedent
-- (architecture-context.md:2236: `(station_id) WHERE valid_to IS NULL`); no extension
-- required. Doubles as the derivation-ordering lookup index for current formulas.
CREATE UNIQUE INDEX idx_csf_current ON calculated_station_formulas(
    calculated_station_id, component_station_id, parameter
) WHERE effective_to IS NULL;
```

The partial `UNIQUE` index enforces the load-bearing invariant — **at most one current
formula per `(calculated_station_id, component_station_id, parameter)` triple** — at the
DB level on every INSERT and UPDATE, so application code cannot create two live rows for
the same triple regardless of the code path. This deliberately mirrors the existing
`rating_curves` "at most one active curve per station" pattern
(`architecture-context.md:2236`: `(station_id) WHERE valid_to IS NULL`) and needs **no
new Postgres extension**. The earlier draft's `EXCLUDE USING gist` — which would have
required the `btree_gist` extension plus a dedicated migration-ordering provisioning rule
(extension availability on managed/staging Postgres, an init-db.sh-vs-Alembic ordering
hazard) — is dropped as disproportionate for an admin-edited, low-volume config table
whose actual invariant is fully covered here. The committed schema itself only lists
EXCLUDE as a *commented-out suggestion*, not a requirement
(`architecture-context.md:2653-2654`), so this is contract-aligned.

**Overlapping historical ranges — deterministic lookup now; strict non-overlap deferred.**
The partial unique index guarantees at most **one current** (`effective_to IS NULL`) row
per `(calculated_station_id, component_station_id, parameter)`, but does not prevent two
*already-closed* rows from having overlapping historical windows. Forward derivation (step
2.5) reads only the current row, so it is unaffected. But the **5.C3 bootstrap** and any
future valid-at-time lookup query the formula *valid at a past timestamp* `t`, and could in
principle hit two overlapping closed rows. **Resolution (major fix): the valid-at-time
lookup is deterministic by rule, independent of any DB overlap constraint** — for each
`(component_station_id, parameter)`, pick the single row with the **greatest
`effective_from ≤ t`** (`... WHERE effective_from <= t AND (effective_to IS NULL OR
effective_to > t) ORDER BY effective_from DESC LIMIT 1` per component). "Latest-configured
wins" is the intuitive admin semantics. In v1-now a calculated station is configured with
**one** formula version at onboarding and not re-weighted (re-weighting/formula history is
itself later work), so overlaps do not arise in practice; the deterministic rule makes the
lookup well-defined regardless. Strict DB-level historical non-overlap (a `btree_gist`
`EXCLUDE` or equivalent) is a follow-up if ever required — not worth the extension /
migration-ordering cost now, given the deterministic rule already resolves ambiguity.

**Trigger — both-invariant enforcement on the formula table (major fix).** A single
`BEFORE INSERT OR UPDATE` trigger on `calculated_station_formulas` validates **both**
sides of the relation against the `stations` table:
- the **target** `calculated_station_id` has `gauging_status = 'calculated'`, and
- the **component** `component_station_id` has `gauging_status = 'gauged'`
  **and** `station_status = 'operational'`.

**Closure-only updates are exempt (blocker fix).** The check above must run **only** on
`INSERT` and on **relation-changing** `UPDATE`s. A closure-only `UPDATE` — one that sets
`effective_to` (closes the row) while leaving `calculated_station_id`, `component_station_id`,
`parameter`, `weight`, and `effective_from` unchanged — is **allowed even when the
component is no longer operational**. This is required by the plan's own decommissioning
path (§D2 below): an admin suspends a component (a `station_status` change), *then* closes
the affected formula rows — that close is an `UPDATE`, and without this exemption the
trigger would reject the very operation it documents. The trigger function detects a
closure-only update as `NEW.effective_to IS NOT NULL AND (NEW.calculated_station_id,
NEW.component_station_id, NEW.parameter, NEW.weight, NEW.effective_from) IS NOT DISTINCT FROM
(OLD.calculated_station_id, OLD.component_station_id, OLD.parameter, OLD.weight,
OLD.effective_from)` and returns `NEW` without re-validating component eligibility.
(Column is `calculated_station_id`, not `station_id` — per the D2 schema.) Integration tests MUST cover "suspend
component → close formula row succeeds" and "reopen/re-point formula with a suspended
component → rejected".

`gauging_status` and `station_status` are orthogonal axes (§D1): `gauging_status` never
changes for a physical station, but `station_status` does — a component can be
SUSPENDED/DECOMMISSIONED (`src/sapphire_flow/types/enums.py:173-178`) while still
`gauging_status = 'gauged'` (there is no "decommissioned" `GaugingStatus` value). Checking
**both** mirrors the live Flow 2 eligibility gate
(`src/sapphire_flow/flows/ingest_observations.py:307-309`, which requires
`gauging_status == GAUGED` **and** `station_status.value == 'operational'`), so the
trigger is a real DB-level backstop for the 5.C2 onboarding precondition ("all components
must already be OPERATIONAL", line ~639) rather than a gauging-status-only half-check that
a suspended/decommissioned component would sail through.
Application-level validation in step 5.C2 catches errors at onboarding time; this trigger
is the safety net against direct DB inserts / migration scripts. Both gauging-status
invariants are load-bearing for the step-2.5 derivation ordering in Flow 2 (§Tiered
Derivation Design): the target-must-be-CALCULATED half keeps derived stations out of the
normal fetch/QC path, and the component-must-be-GAUGED half keeps the dependency graph exactly two
tiers deep. The trigger function uses `RAISE EXCEPTION`, surfaced as a SQLAlchemy
`IntegrityError` (production stores use **sync SQLAlchemy + psycopg**, `flows/_db.py:73`;
not asyncpg), with an actionable message naming the offending station id and its actual
status.

**No second trigger on `stations` (proportionality fix — read-time check instead).** No
trigger is added on `stations` UPDATE to reject transitioning a referenced component: a
hard DB-level block on a legitimate, low-frequency admin operation (suspension /
decommissioning) is disproportionate. The real risk — "pipeline silently computes from
stale data" — is closed at the point that matters, **step-2.5 derivation read-time**: the
derivation step re-reads each component's **current `gauging_status` *and*
`station_status`** and treats a component that is not
(`gauging_status = 'gauged'` **and** `station_status = 'operational'`) exactly like a
missing observation (skip + store `qc_status = 'missing'` + log
`station.formula_component_not_gauged`), mirroring both the existing missing-component
skip and the live Flow 2 eligibility gate (`ingest_observations.py:307-309`). Checking
`station_status` too is essential: decommissioning/suspension is a `station_status`
transition, **not** a `gauging_status` one (`GaugingStatus` has no "decommissioned" value —
`enums.py:187-190`), so a gauging-status-only re-check would let a suspended or
decommissioned component (which stays `gauging_status = 'gauged'` forever) produce a stale
derived value. Combined with the app-level check in 5.C2 and the formula-table trigger,
this covers the operational path without a bespoke migration-per-trigger.

> **NOTE (resolution):** the *stations*-side protection lives at step-2.5 read-time (soft
> skip + log on any non-operational or non-gauged component), not in a hard `stations`
> trigger; the cheap both-axes invariant check stays on the *formula* table where a write
> is already happening. A hard block on decommissioning would be disproportionate, and the
> read-time check closes the stale-data window at the only point a stale value could
> actually be produced.

**Component decommissioning resolution path**: Decommissioning/suspension is a
**`station_status`** transition — the admin routes it through the **existing**
`update_station_status()` store method (`protocols/stores.py:545`,
`store/station_store.py:319`) to move the component to `SUSPENDED`/`DECOMMISSIONED`.
`gauging_status` stays unchanged — it answers "does this station have real observations",
which decommissioning does not change — and is therefore effectively immutable
post-creation (no `update_gauging_status()` method is introduced). From the moment the
component is non-operational, the step-2.5 read-time check above makes the calculated
station's derivation produce `qc_status = 'missing'` (no stale value), independent of
formula-close timing. The admin then closes the affected formula rows
(`effective_to = now()`) and either configures a replacement formula or suspends the
calculated station.

**Audit trail for formula changes** *(re-draft decision — structlog now, `audit_log`
deferred)*: `audit_log` / `AuditEventType` do not exist yet (they land with the Wave-3
auth/audit plan). Until then, formula creation and closure are logged via **structlog**
— events `station.formula_configured` / `station.formula_closed` (scoped under the
`station` entity, not `formula` — formulas are configuration artifacts), carrying the
calculated `station_id`, the component `station_id`s + weights, and the actor. When the
Wave-3 plan lands, these become append-only `audit_log` rows with new `AuditEventType`
values `FORMULA_CONFIGURED` / `FORMULA_CLOSED` (`actor_id` = the `model_admin`,
`target_type` = `"calculated_station_formula"`, `target_id` = the formula row `id`,
`detail` = components + weights). No inline `created_by`/`modified_by` columns on the
formula table — attribution lives in structlog now, `audit_log` later.

**Trigger migration strategy**: the (single) trigger + its function are added via a
rollback-safe Alembic migration; the exact `op.execute()` DDL mechanics belong in the
migration PR, not this design doc. `cicd.md` gains a one-line note on the pattern (see
§Standards Document Updates).

### D3. Model assignment — all virtual stations require models

Both ungauged and calculated stations **must** have forecast models assigned:
- ⚠ REFERENCE-ONLY (ungauged plan): Ungauged stations need models deployed via transfer
  learning (5.11 Branch A: existing `GroupForecastModel` applied to new station) or
  trained with regionalized parameters (a `StationForecastModel` implementation detail,
  not a new model type). Both rely on NWP forcing + basin characteristics.
- Calculated stations need models to produce forecasts — the formula only derives
  observations, not forecasts

The formula applies to **observations only** (Flow 2). Forecasts are always produced
by model runs (Flow 1). `OperationalForecast.model_id` and `model_artifact_id` remain
non-optional. The standard go-live precondition (`≥1 active model artifact`) applies
to all station types including calculated stations. For calculated stations this is the
**only** model-related go-live gate — there is **no** baseline-model precondition in this
plan (D5a's baseline-model requirement is ungauged-scoped and deferred). Note that
calculated stations *do* compute baseline **artifacts** (climatology) at 5.8 for skill
scoring; that is distinct from the fallback baseline **model** discussed in D5a.

**Future extension (low priority)**: A `DerivedForecast` type could allow computing
forecasts from component station forecasts via the weighted-sum formula, analogous to
how derived observations work. This is deferred — not needed for v1.

### D4. Observation handling

**Ungauged stations** — ⚠ REFERENCE-ONLY (ungauged plan): No observations → no
observation QC, no skill scores against observations, no rating curves. Flow 2
pre-filters to exclude ungauged stations before the fan-out.

**Calculated stations**: After component observations are ingested and QC'd by the
standard Flow 2 path, a derivation step computes `Q_virtual = Σ(wᵢ × Qᵢ)` and stores
the result with `ObservationSource.COMPONENT_DERIVED` — which **already exists** in the
shipped enum (ordered third, before `MANUAL_IMPORT`) and is already admitted by the DB
CHECK (§v0 Scope). No enum or `conventions.md` change is in scope; only the Flow 2 write
path is new:

```python
class ObservationSource(Enum):
    MEASURED = "measured"
    RATING_CURVE_DERIVED = "rating_curve_derived"
    COMPONENT_DERIVED = "component_derived"   # SHIPPED (enums.py:183); this plan writes it
    MANUAL_IMPORT = "manual_import"
```

### D5. `past_targets` for ungauged stations — ⚠ REFERENCE-ONLY (ungauged plan)

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

### D5a. Baseline model dependency — ⚠ REFERENCE-ONLY (ungauged plan)

**Ungauged** stations require a **baseline model** — a simple, always-available forecast
(e.g. climate norm, linear regression) that serves as both skill-comparison reference and
the initial operational model until a more sophisticated model is trained. (A
baseline/fallback model may be a desirable cross-cutting default for *all* station types,
but that is a separate concern owned by the baseline-model plan — it is explicitly **not**
a calculated-station go-live gate in this plan; see §D3. Calculated stations go live on
the standard `≥1 active model artifact` precondition alone.) The baseline-model design
(model types, assignment policy, Flow 1 execution pattern) is out of scope here — see Plan
TBD (Related Plans). **Hard dependency**: the baseline-model plan must complete before
ungauged station support ships.

### D6. QC flag propagation for calculated stations

When component stations have QC flags, the calculated station's derived observation
inherits the **worst** component QC status. Weights do **not** enter the QC aggregation.

New **observation** QC rule convention (distinct from the existing forecast QC rule
ID list in `conventions.md` — a new observation QC rule ID row must be added to the
enum master list):

- `rule_id`: `"upstream_propagated"`
- `detail`: structured JSON string encoding provenance, e.g.
  `{"component_station_id": "...", "component_status": "qc_suspect", "weight": 0.4}`
  (`weight` is recorded for provenance only — it is signed and does not affect the
  derived status)

**Aggregation policy (simplified — major fix).** Derivation only proceeds when all
components have `QC_PASSED` or `QC_SUSPECT` status (§Missing component observations —
`QC_FAILED`/missing trigger a skip). Under that policy the propagated status is simply:

> **any component `QC_SUSPECT` ⇒ derived `QC_SUSPECT`, else `QC_PASSED`**

i.e. `max()` over component severities, exactly matching the codebase's existing
`aggregate_qc_status()` (`src/sapphire_flow/types/domain.py:104-109`, which uses `max`,
**not** a weighted average). The earlier draft's `ceil(Σ(wᵢ·severityᵢ)/Σ wᵢ)` is dropped:
(1) it mis-cited `aggregate_qc_status()` as weighted when it is `max`; (2) with signed
weights (§D2) `Σ wᵢ` can be zero or negative, making the quotient undefined or
sign-flipped; (3) even with positive weights a tiny `wᵢ/Σw` can underflow to `0.0` in
IEEE-754 and silently misreport a `QC_SUSPECT` component as `QC_PASSED` — a
safety-relevant misclassification `max()` is structurally immune to. `max()` produces
identical output to the intended behavior with none of these failure modes. (If a future
N-tier design ever admits `QC_FAILED` components and needs graded severity, revisit then —
a one-line code comment preserves the idea; we do not build speculative generality now.)

Propagated flags are added alongside any locally-computed flags on the derived
observation. `aggregate_qc_status()` then picks the worst across all flags as usual.

**Downstream handling of `QC_SUSPECT` derived observations (minor fix).** Operational
input loading, training, and hindcast all fetch **`QC_PASSED` only**
(`src/sapphire_flow/services/operational_inputs.py:352`,
`src/sapphire_flow/services/training_data.py:161`,
`src/sapphire_flow/services/hindcast.py:160`). This plan does **not** relax those filters:
a `QC_SUSPECT` derived observation is **review-only** — it is stored (visible in Flow 3
review and staleness monitoring) but is **not** fed to models. This is deliberate and
conservative; admitting suspect derived values into training/inference would be a separate,
explicitly-scoped change to those three filters, out of scope here.

### D7. Basin delineation — ⚠ REFERENCE-ONLY (ungauged plan; incl. file-upload security)

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

All flows that touch station processing are affected. Note the starting point: Flow 2
today already carries a `GAUGED` + operational guard
(`src/sapphire_flow/flows/ingest_observations.py:305-310`) and runs **sequentially**
(single fetch task → store → QC `for`-loop), **not** a `task.map` fan-out. This plan
**keeps** that guard (so calculated stations stay excluded from the normal fetch/QC path)
and **appends a sequential derivation step 2.5** after the QC loop (see §Tiered Derivation
Design). When no calculated stations are present (including all of v0), the new step is a
no-op and Flow 2 behavior is unchanged. Other changes are additive.

The "Ungauged stations" column below is **⚠ REFERENCE-ONLY (ungauged plan)** — retained
for context, not in scope here.

| Flow | Ungauged stations ⚠ REFERENCE-ONLY | Calculated stations |
|------|-------------------|---------------------|
| **Flow 0** (deployment onboarding) | Basin delineation for ungauged sites (HydroSHEDS / user upload) | Same |
| **Flow 1** (forecast cycle) | Model runs with zero-row `past_targets`; input prep step 1.7 passes zero-row DataFrame with correct schema | Standard model run — model uses `COMPONENT_DERIVED` observations as `past_targets` like any other observation source |
| **Flow 2** (observation ingest) | **Skipped** — pre-filter excludes ungauged stations | New derivation step after standard QC: compute `Q_virtual = Σ(wᵢ × Qᵢ)` from component observations with `QC_PASSED` or `QC_SUSPECT` status (skip on `QC_FAILED` or missing — see §Tiered Derivation Design), store with `source=COMPONENT_DERIVED`, propagate QC flags (D6). Ordering: calculated stations derived after their components complete QC |
| **Flow 3** (forecast review) | No observation overlay in review UI | Display formula breakdown (component contributions) alongside forecast |
| **Flow 4** (pipeline monitoring) | **Exclude** from observation staleness checks — no expected observations | *(no monitor in this plan — Flow 4 is deferred; skipped derivations surface via structlog + a `qc_status='missing'` placeholder. A component-freshness monitor is future Flow 4 work.)* |
| **Flow 5** (station onboarding) | Modified path — see below. **v1 impl note**: `services/onboarding.py:_run_onboarding()` currently runs QC/baselines/flow-regimes for all stations unconditionally — must add `gauging_status` branching (ungauged skips steps 5.4–5.9) | Modified path — see below |
| **Flow 5w** (weather station onboarding) | No impact — virtual stations are non-weather (`StationKind.RIVER` etc.) | No impact |
| **Flow 6/9** (model training/retraining) | Standard training path — `past_targets` may be zero-row for ungauged stations; model implementation handles regionalized parameters internally. **v1 impl note**: `services/scope.py:determine_training_scope()` filters by `station_status` only — must also consider `gauging_status` for ungauged stations with regionalized training | Standard training path — model trains on `COMPONENT_DERIVED` observations |
| **Flow 7** (hindcast generation) | Hindcast with zero-row `past_targets`; model must handle this | Standard hindcast — model uses historical `COMPONENT_DERIVED` observations |
| **Flow 8/10** (skill computation) | **No skill scores** — no observations to verify against. Alternative: cross-validation on regionalized model parameters (future work) | Standard skill computation — model forecasts verified against `COMPONENT_DERIVED` observations. Note: baselines (5.8) and flow regimes (5.9) computed from accumulated `COMPONENT_DERIVED` history — see onboarding notes |
| **Flow 11** (NWP archive) | No change — ungauged stations still consume NWP | No change |
| **Flow 12** (observation reprocessing) | No impact — no observations to reprocess | **DEFERRED to a follow-up plan (re-draft decision).** Re-deriving calculated stations after a component reprocessing (Branch A/B/C) couples to the `reprocess_observations` flow, which is not built (blocked on the DHM producer). **No implementation requirement here.** Until then, a component reprocessing leaves downstream calculated stations on their prior derived values (documented gap). See §Flow 12 for the follow-up forward-pointer. |
| **Flow 13** (model onboarding) | No new model types. Ungauged stations use existing `GroupForecastModel` (via 5.11 Branch A transfer learning) or `StationForecastModel` with regionalized parameters — both are training/deployment strategies, not new Protocols | No new model types needed — uses standard models |

## Tiered Derivation Design (Flow 2 + Flow 12)

### Derivation is a sequential post-QC step (Flow 2 step 2.5) — NOT a task.map fan-out

**Grounding (corrects the earlier "two-wave fan-out" design).** The real Flow 2
(`flows/ingest_observations.py`) is **not** a per-station `task.map` fan-out. It runs
sequentially: step 2.0 selects `eligible` stations with an explicit **`GAUGED` +
operational guard** (`ingest_observations.py:305-310`), so calculated stations are
*already excluded*; step 2.1 fetches all observations in a **single** task
(`_fetch_observations_task(adapter, eligible, since)`, `:339`); step 2.2 stores them
(`:357`); steps 2.3–2.4 run QC in a **sequential `for` loop** over `(station, parameter)`
pairs (`:373-395`), not `task.map`. There is therefore no fan-out to add a second wave
to, and no `ThreadPoolTaskRunner` pressure to reason about.

Calculated-station derivation is a **new sequential step 2.5**, inserted *after* the QC
loop completes (line ~402) and before the result assembly. Because the flow is
sequential, the QC loop finishing **is** the barrier — no `task.map` + `.result()` sync
gather is needed. The step:

1. Selects calculated stations separately from `eligible`:
   `calculated = [s for s in all_stations if s.gauging_status == CALCULATED and
   s.station_status.value == "operational"]` (they were filtered out of `eligible` by the
   `GAUGED` guard). Empty in v0 → the step is a no-op and Flow 2 behavior is unchanged.
2. Pre-fetches formulas once: `formula_store.fetch_formulas_for_stations([s.id for s in
   calculated])` — a single cheap query (calculated stations are a small minority even at
   ~1000 stations).
3. For each calculated station, derives from its components' just-QC'd observations (see
   §Missing component observations for source-precedence + exact-timestamp matching), then
   `obs_store.store_observations([...])`. **Concurrency (grounding fix): there is no
   `observation_write` Prefect lock in Flow 2 today** — `_store_raw_task` writes directly
   (`ingest_observations.py:356`) and the store upserts on the natural key
   (`store/observation_store.py`, `on_conflict_do_update`). Derivation writes rely on that
   same idempotent natural-key upsert `(station_id, timestamp, parameter, source)`; a re-run
   overwrites rather than duplicates. A per-station observation-write lock does **not** exist
   and is **not introduced by this plan**; concurrent Flow 2 / future-Flow 12 writes to the
   same calculated station are a shared concern for the Flow 12 follow-up (§Flow 12).

The **two-tier depth is guaranteed** by the §D2 formula trigger (components must be
`GAUGED`), so no topological sort is needed — derivation reads component observations that
step 2.4 already QC'd in the same run. Because the derivation reads happen *after* the
whole QC loop, they see the latest committed component state. A **read-time defensive
check** is still applied (the trigger is a write-time, not read-time, guarantee, and this
plan deliberately adds no `stations`-side trigger; see §D2): before deriving, re-read each
component's current `gauging_status` **and** `station_status`, and treat a component that
is not (`gauging_status = 'gauged'` **and** `station_status = 'operational'`) exactly like
a missing observation (skip + `qc_status = 'missing'` + log). Checking `station_status` is
required because suspension/decommissioning is a `station_status` transition, not a
`gauging_status` one (§D2). If the two-tier invariant is ever relaxed (calculated
components), replace this single step with an N-tier topological pass
(`graphlib.TopologicalSorter`) over the formula dependency graph.

### Missing component observations

**Derivation proceeds** when all component stations have an observation for the current
time window **and the formula's `parameter`** with QC status `QC_PASSED` or `QC_SUSPECT`.
The QC propagation rule (§D6) then determines the derived observation's QC status.

**Component observation selection — deterministic source (major fix).** The `observations`
natural key includes `source` (unique index `uq_observations_natural_key` at
`src/sapphire_flow/db/metadata.py:359-365`; `:296-303` is only the `source` CHECK,
`store/observation_store.py:254` keys on `(station_id, timestamp, parameter, source)`),
and `fetch_observations_batch(..., source=None)` returns rows for **all** sources
(`store/observation_store.py:196-217`). A component station can therefore legitimately
have several rows for the same `(timestamp, parameter)` — e.g. a `measured` row and a
`rating_curve_derived` row, or a `manual_import` backfill. Selecting "an observation" is
underspecified. Derivation MUST apply a **deterministic source precedence** per component,
highest-trust first:
`measured` > `rating_curve_derived` > `manual_import` > `component_derived`.
Concretely: fetch with an explicit source-precedence resolution (or per-source queries
walked in that order), take the **single** highest-precedence row present for each
`(component_station_id, timestamp, parameter)`, and derive from those. `component_derived`
is last so that a calculated-of-calculated row (which the two-tier invariant forbids as a
current component anyway) can never be silently preferred. Tests MUST cover a component
carrying duplicate `measured`/`manual_import`/`rating_curve_derived` rows and assert the
precedence winner.

**Derivation is skipped** when — **for a timestamp `t` that at least one component
reported this run** — any component has no observation at `t` (data gap, source outage)
**or** has one with `QC_FAILED` status. A placeholder observation is stored for the
calculated station at `t` with `qc_status = 'missing'`. Rationale: a partial weighted sum
is not a valid derivation — the formula requires all components. `QC_FAILED` is excluded as
known-bad. Missing data propagates honestly rather than silently degrading.

**Placeholders are per-reported-timestamp, and step 2.5 runs only when the QC loop ran
(grounding fix).** The derivation window is the set of timestamps present in *this run's*
QC'd component observations; a placeholder is written only for a `t` where some component
reported but another is missing/failed — never for timestamps no component reported. This
matters because Flow 2 **returns early when `raw_obs` is empty**
(`ingest_observations.py:342`), *before* the QC loop and step 2.5. That is correct and
intentional: an empty run means **no** component reported new data, so there is no timestamp
to derive at and no placeholder to write. Step 2.5 therefore lives after the QC loop (which
only executes on a non-empty run); it never needs to run on the empty-run early-return path.

**No automated monitor in this plan.** Flow 4 (pipeline monitoring) is deferred in v0
scope (`docs/v0-scope.md`) and there is no component-dependency freshness monitor today.
A skipped derivation is surfaced only via structlog (`observation.derivation_skipped`
with the missing/failed component id + reason) and the stored `qc_status = 'missing'`
placeholder. Wiring a component-freshness monitor is left to the future Flow 4 plan; this
plan states the gap rather than assuming a monitor exists.

The distinction: `QC_SUSPECT` means "possibly degraded, flagged for review" and is safe
to propagate with appropriate QC flag inheritance (§D6). `QC_FAILED` means "known bad"
and must not enter the derivation.

### Flow 12 Branches A and C — sequential re-derivation *(DEFERRED to a follow-up)*

> **Re-draft decision:** Flow 12-triggered re-derivation is **out of scope for this
> plan** — it couples to the `reprocess_observations` flow (Flow 12 Branch A), which is
> not built and is blocked on the DHM producer of `rating_curve_derived` observations.
> This plan builds only **Flow 2 forward-derivation**. Until the reprocess flow exists, a
> curve/QC reprocessing on a component station leaves downstream calculated stations on
> their previously-derived values (a documented, acceptable gap for v1-now).

**Forward-pointer for the follow-up plan:** re-derivation should reuse the same sequential step-2.5 derivation (reprocess components first, then re-derive calculated stations) once Flow 12
Branch A/C exists. The concurrency semantics — **including whether to introduce a
per-station observation-write lock (`concurrency("observation_write:{station_id}")`),
which does NOT exist today** (only the natural-key upsert serialises writes; grep confirms
no such lock in Flow 2), and how a Flow 2 / Flow 12 temporal overlap on a shared component
resolves (component reads are unlocked) — must be **decided against Flow 12's actual
implementation at that time**, not pinned here against a flow that does not yet exist. That analysis belongs in the follow-up plan,
consistent with how the ungauged content is split out.

## Onboarding Flow Modifications (Flow 5)

Flow 5 needs three branches based on `GaugingStatus`. Step numbers reference
`architecture-context.md` Flow 5 steps.

**GAUGED** (existing path, unchanged):
Steps 5.1–5.12 as currently defined.

**UNGAUGED** — ⚠ REFERENCE-ONLY (moves to the ungauged plan; not implemented here):
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
- 5.1 (calculated) also sets the station's `measured_parameters` to include the
  **formula-derived parameter** (e.g. `"discharge"`). *(major fix)* Flow 1 uses
  `measured_parameters` to pick the observation parameter for staleness + baselines
  (`run_forecast_cycle.py`), and onboarding requires it — a calculated station that omits
  its derived parameter would lose staleness/baseline context. The parameter is derived,
  not sensor-measured, but it **is** the station's reportable/observed parameter.
- 5.C1 Configure formula: specify component stations + weights **and an explicit initial
  `effective_from`** for the formula. *(major fix — bootstrap validity)* `effective_from`
  defaults to the **earliest component observation timestamp** available (so the retroactive
  bootstrap in 5.C3 is covered by a validity window), not `now()`; the admin may override
  it. All components must already be `OPERATIONAL`.
- 5.C2 Validate formula: check components exist; each weight is **nonzero and finite**
  (`w != 0 AND |w| < 1e6`; signed — see §D2); the target has `gauging_status = 'calculated'`;
  and every component has `gauging_status = 'gauged'` **AND `station_status = 'operational'`**
  *(major fix — mirrors the D2 trigger + the live Flow 2 gate at
  `ingest_observations.py:305-310`; a gauged-but-suspended component must be rejected at
  onboarding)*. No explicit circular-dependency check is needed: the gauging-status
  invariant forbids calculated→calculated references, so cycles are impossible by
  construction — recorded as a code comment, not a separate runtime check.
- Skip 5.4–5.7 (no direct historical obs import, no rating curves)
- 5.C3 Bootstrap derived observation history: for each historical timestamp, apply the
  formula **valid at that timestamp** (query `calculated_station_formulas` by
  `effective_from <= t < effective_to`, per the D2 validity semantics — **not** blindly the
  just-configured row), compute `Q_virtual`, and store with `source = 'component_derived'`.
  With a single initial formula whose `effective_from` covers the component history, this
  derives the whole history; timestamps before `effective_from` are left underived. Derived
  observations **skip Stage 1/Stage 2 QC** (sensor-range validation is meaningless for
  computed values) — they inherit QC status from their components via the D6 propagation
  rule. Tests MUST span a timestamp before and after `effective_from`.
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

## Implementation surface (code touchpoints)

The design above touches these concrete code sites — each is a task for the build (the
earlier draft named only the spec-doc change, which understated the work):

1. **`types/ids.py`** — add `FormulaId = NewType("FormulaId", UUID)` (the `ComponentWeight`
   row uses a surrogate UUID PK; there is no such id type today).
2. **`types/` (new module, e.g. `types/calculated_station.py`)** — `ComponentWeight` frozen
   dataclass with signed-weight `__post_init__` (`w != 0 and |w| < 1e6`), fields
   `calculated_station_id`, `component_station_id`, `parameter`, `weight`,
   `effective_from`, `effective_to`, `created_at`.
3. **`db/metadata.py`** — `calculated_station_formulas` table (surrogate `id` PK + the
   **partial UNIQUE index** `(calculated_station_id, component_station_id, parameter)
   WHERE effective_to IS NULL` per §D2 — mirrors the `rating_curves` active-curve
   precedent, **no `btree_gist`/`EXCLUDE` extension**, the CHECKs, and the component index).
4. **Alembic migration(s)** — the table (no extension needed); the eligibility trigger +
   function in its
   **own** revision (`op.execute` `CREATE OR REPLACE FUNCTION` / `CREATE TRIGGER`;
   `DROP` in downgrade), including the closure-only-update exemption (§D2).
5. **`protocols/stores.py`** — `FormulaStore` Protocol (**parameter-scoped** — the table
   keys on `(calculated_station_id, component_station_id, parameter)`, §D2, and a station
   may report >1 parameter via `measured_parameters`): `store_formula`, `close_formula`,
   `fetch_current_formula(station_id, parameter) -> Sequence[ComponentWeight]`,
   `fetch_formula_at(station_id, parameter, at) -> Sequence[ComponentWeight]`,
   `fetch_formulas_for_stations(station_ids) -> dict[tuple[StationId, str],
   Sequence[ComponentWeight]]` (current formula rows grouped by `(station_id, parameter)`).
   Each "formula" is the *set* of component-weight rows for one `(station, parameter)`.
6. **`store/calculated_station_formula_store.py`** — `PgFormulaStore` implementing it.
7. **`tests/fakes/fake_stores.py`** — `FakeFormulaStore` + a `test_fakes.py` conformance case.
8. **`flows/_db.py` `make_pg_stores()`** — add the `"formula_store"` slot (it has no such
   slot today) so production Flow 2 receives it.
9. **`flows/ingest_observations.py`** — the new sequential **step 2.5** derivation (select
   calculated stations, pre-fetch formulas, source-precedence + exact-timestamp match,
   weighted sum, QC propagation, store `component_derived` / `missing` placeholder), plus
   the `structlog` formula-audit + derivation events.
10. **`services/onboarding.py`** — the CALCULATED branch (5.C1–5.C3, `measured_parameters`).
11. **`services/rating_conversion.py`-style pure helper (optional)** — a pure
    `derive_component_value(components, weights) -> value` for unit-testability.
12. **Integration tests** — formula store round-trip + overlap-exclusion; suspend-then-close;
    exact-timestamp derivation with source precedence; missing/failed-component skip;
    bootstrap spanning `effective_from`.

## Standards Document Updates Required

Each bullet states *what changes and why*; exact replacement prose belongs in the
implementing PR, not this plan.

- **`types-and-protocols.md`**: add `ComponentWeight` dataclass (signed-weight
  `__post_init__`, `parameter` field, `effective_from/to` — §D2); add `FormulaStore`
  Protocol for `calculated_station_formulas`. (`GaugingStatus`, `gauging_status` field,
  and `COMPONENT_DERIVED` are already shipped — no change.) `FORMULA_CONFIGURED` /
  `FORMULA_CLOSED` `AuditEventType` values are **deferred** to the Wave-3 auth/audit plan
  (§D2 audit note) — not added here.

- **Store & code touchpoints (major fix — the step-2.5 derivation needs a real
  status filter):**
  - Add an optional `gauging_status: GaugingStatus | None = None` parameter to
    `StationStore.fetch_all_stations()` and `fetch_stations_by_ownership()` in the
    Protocol (`src/sapphire_flow/protocols/stores.py:499-509` — neither has it today),
    the production store (`src/sapphire_flow/store/station_store.py:93`, which applies no
    status filter), **and** the fake (`tests/fakes/fake_stores.py:860,867`, likewise
    unfiltered). Add tests asserting the filter on both implementations. step 2.5's
    read-time re-check (§D2) additionally needs each component's current `station_status`;
    it reads the full station rows it already fetches — no new write method is required.
  - **No `update_gauging_status()` method.** `gauging_status` is set once at station
    creation (5.1 registers the station with `gauging_status = 'calculated'`/`'gauged'`)
    and is effectively immutable thereafter — it answers "does this station have real
    observations", which neither suspension nor decommissioning changes. Decommissioning
    is a **`station_status`** transition and routes through the **existing**
    `update_station_status()` (`protocols/stores.py:545`, `store/station_store.py:319`) —
    no new store method. (There is a pre-existing latent divergence — `update_station()`
    at `store/station_store.py:155-174` does not write `gauging_status` while the fake
    replaces the whole station — but this plan exercises no gauging_status *update* path,
    so it is left as-is rather than widened here.)

- **`architecture-context.md`**: update the `past_targets` 4-slot wording ("Always
  present for stateful models" → "Always non-None; may be zero-row for ungauged
  stations") — *ungauged, reference-only, lands with the ungauged plan*; add the Flow 2
  step-2.5 sequential-derivation note (after the QC loop); update the Flow 5 sequencing so CALCULATED 5.8/5.9 depend on
  5.C3. The pre-existing **H.3→H.4 typo is dropped from this plan's scope** — fix it in a
  separate trivial commit, not folded here. (`COMPONENT_DERIVED`, the formula table, and
  `gauging_status` are already in the schema doc.)

- **`conventions.md`**: add a new **observation QC rule ID** row registering
  `range_check`, `rate_of_change`, `frozen_sensor`, `spike`, `gross_outlier`,
  `upstream_propagated` (`range_check` intentionally shared with the forecast list —
  domain-specific implementations). No composite-PK convention change (this plan uses the
  standard UUID PK — §D2). (`GaugingStatus` / `component_derived` already present.)

- **`orchestration.md`**: document the Flow 2 **sequential step-2.5 derivation** (a new
  step after the existing QC `for`-loop — Flow 2 is not a `task.map` fan-out, so there is
  no wave/`unmapped()` pattern to add; the QC loop completing is the ordering barrier), the
  formula pre-fetch at flow start, the missing-component + non-`GAUGED`-component skip policy,
  the deterministic component source precedence, and the concurrency read-gap trade-off
  (component reads are unprotected by **any observation-write lock — none exists today**;
  `observation_write` is aspirational in `orchestration.md`/`touchpoint-maps.md` and is not
  introduced here; the risk is covered by the step-2.5 read-time status re-check + the
  idempotent natural-key upsert). Flow 12 re-derivation is deferred (§Flow 12), so it is a
  forward note only.

- **`logging.md`**: add `gauging_status` as a per-task bound context field in the step-2.5 derivation
  (`bound_contextvars()`); add paired `observation.derivation_started` /
  `observation.derivation_completed` (with `duration_ms`),
  `station.formula_validation_failed`, `station.formula_component_not_gauged` events;
  step-2.5 derivation inherits `log_prints=False` from the Flows 1/2 rule.

- **`cicd.md`**: a one-line note that the formula trigger + function are added via a
  rollback-safe Alembic migration. **No `btree_gist` provisioning note** — the plan now
  enforces the one-current-formula invariant with a plain partial `UNIQUE` index and has
  **no** Postgres-extension dependency (§D2). The two-step `NOT NULL`-column-with-default
  pattern is already documented from the shipped `gauging_status` column and needs no new
  entry here.

- **`v0-scope.md`** *(minor cleanup — stale)*: `v0-scope.md:481` still lists
  `ObservationSource.COMPONENT_DERIVED` as *deferred to v1*, but it has shipped (enum
  `types/enums.py:180`, admitted by the `observations.source` CHECK
  `db/metadata.py:296-303`, Plan 035 Task 2). Update that §G/§B line to "shipped" so the
  scope doc stops contradicting the code. Small, fold into this plan's implementation PR.

- **`security.md`** — ⚠ REFERENCE-ONLY (ungauged plan): file-upload validation section
  (MIME, size, geometry complexity) + authorization-matrix entry for basin-outline
  upload. Not in scope here.

- **`wmo.md`** — ⚠ REFERENCE-ONLY (ungauged plan for the virtual-station identity parts):
  `GaugingStatus` has no direct WMO-49 Vol III / WMO-168 enum precedent; WIGOS ID policy
  for virtual stations (`wigos_id = NULL` acceptable; excluded from WIGOS exchange).

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
