# Plan 035 — Rating Curve Provenance for Skill Score Integrity

**Status**: READY
**Phase**: v1 preparation (schema + types + flow logic)
**Scope**: v1 (Nepal). **Implementation begins at v1 phase start**, not during v0
development. v0-scope.md §B explicitly defers `rating_curves` ("don't create tables
until needed"), §C drops `rating_curve_id` from `observations`, §G excludes
`RatingCurveStore`, and the Flows table defers Flow 12 Branch A to v1. This plan
is a committed design document; Tasks 1–8 execute when v1 work begins.
**Depends on**: Rating curves table design (architecture-context.md §2206)

## Context

### The problem

Nepal's DHM updates rating curves (hQ tables) periodically — typically yearly, but
also mid-monsoon when major floods shift river geometry. When a new curve is uploaded,
Flow 12 Branch A reprocesses historical discharge observations:

- Water level observations (the raw measurement) are untouched
- Discharge observations (derived via the old curve) are recomputed with the new curve
- Historical operational forecasts remain immutable (standard practice: EFAS, NWS, BOM)

An external review raised concern that this creates a mismatch: old forecasts verified
against newly-reprocessed observations would show artificially degraded skill scores.

### Why this is narrower than it first appears

If rating curves are **maintained contemporaneously** — i.e., the new curve takes effect
from the transition point forward and the old curve remains valid for its historical
period — then the discharge signal is continuous and no mismatch occurs. Each forecast
is verified against observations from its own era. Flow 12 Branch A does not fire
because there is nothing to reprocess.

The problem only materialises under **retroactive correction**: the new curve is applied
backward in time, overwriting historical observations that forecasts were already
verified against. This happens when:

1. The river changed earlier than detected (monsoon shifts the bed in July, gauging
   campaign discovers this in October, new curve is backdated to July)
2. The original curve was wrong for a period (new gaugings reveal systematic bias)
3. Institutional delay — DHM updates yearly, so mid-monsoon the active curve is
   increasingly inaccurate, with the correction arriving months later as a batch update

Cases 1 and 3 are the Nepal reality. The design must handle retroactive corrections
gracefully.

### How other hydromets handle this

- **USGS**: Continuous shift-adjusted curves with frequent (often automated) gaugings.
  Data tiers: provisional -> checked -> approved. Skill against provisional data is
  flagged as preliminary. Full revision history maintained.
- **BOM (Australia)**: Quality codes on every discharge value. Estimated vs verified
  discharge distinguished. Skill against estimated data carries a lower confidence flag.
- **EFAS/GloFAS**: Often verify against water level directly, sidestepping the rating
  curve. For discharge, they use national services' published data and freeze the
  verification dataset for formal skill assessments (WMO-1364 approach).
- **WMO guidance (WMO-1364, §4.3–4.4)**: Verify against the best available observation
  at verification time. Freeze the verification dataset for formal campaigns. Ad hoc
  recomputation against revised data is discouraged because it makes historical
  scores non-reproducible. *(Note: wmo.md summarises WMO-1364's metric guidance
  (CRPS, Brier, rank histograms) but not this procedural guidance on dataset
  management — the above is drawn from the full WMO-1364 document.)*
- **WMO-1044** (Manual on Stream Gauging): Rating curve methodology reference,
  relevant to Nepal v1 rating curve correction parameter (see wmo.md line 95).

### Design principles

1. **Provenance first**: Track which curve produced which value — on observations,
   forecasts, and skill scores. This is non-negotiable traceability.
2. **Best-truth verification** (WMO-1364): Flow 10 verifies against the current
   (most recent) observation values. If the river changed and the new curve is correct,
   that IS the truth.
3. **Transparency over correction**: Expose discontinuities to consumers rather than
   trying to automatically fix them. Dashboards plot epoch boundaries; humans interpret.
4. **Archive, don't discard**: When reprocessing overwrites old values, archive the
   superseded values. This enables future same-epoch verification if needed, without
   complicating the default verification path.
5. **Minimal v1 scope**: Build the provenance plumbing and transparency features.
   Defer automatic epoch-partitioned skill computation until operational experience
   shows it's needed.

---

## Design

### Schema changes

#### 1. `rating_curves` table (create)

Target schema (post-migration), based on the design in architecture-context.md
§2206, with additions: a `UNIQUE (station_id, version)` index (not present in the
architecture doc — to be added in Task 8) and the `uploaded_by` FK (deferred to a
later migration — see below).

```
rating_curves:
  id: UUID PK
  station_id: UUID FK -> stations
  version: INT                    -- monotonically increasing per station
  valid_from: TIMESTAMPTZ
  valid_to: TIMESTAMPTZ NULL      -- NULL = currently active
  points: JSONB                   -- [{"water_level": float, "discharge": float}, ...]
  interpolation: TEXT DEFAULT 'linear'  -- InterpolationMethod enum: "linear" | "log_linear"
  uploaded_by: UUID NULL                  -- FK -> users(id) added in later migration
  created_at: TIMESTAMPTZ

Indexes:
  (station_id, valid_from DESC)
  UNIQUE (station_id) WHERE valid_to IS NULL   -- at most one active curve per station
  UNIQUE (station_id, version)                 -- NEW: monotonic version enforcement
```

**New enum**: `InterpolationMethod` with values `linear`, `log_linear`. Add to
conventions.md enum master list. The `interpolation` column stores the enum `.value`
per convention. This replaces the existing `Literal["linear", "log-linear"]` on
`RatingCurve.interpolation` — the hyphenated `"log-linear"` is a pre-convention
anomaly; all other multi-word enum values use underscores.

**`uploaded_by` FK**: Target state references `users(id)`. Task 1's Alembic migration
adds `uploaded_by UUID NULL` **without** the FK constraint (the `users` table may not
yet exist in the migration chain). A subsequent v1 migration adds the FK after the
`users` table is created.
Role constraint: curve uploads require `model_admin` or `it_admin` role (to be added
to security.md authorization matrix when the upload endpoint is designed).

**DB permissions**: `sapphire_worker` already has `SELECT` on `rating_curves`
(conventions.md line 309). No grant change needed for the worker. `sapphire_api`
already has `SELECT all` — no explicit per-table addition needed for `rating_curves`.

#### 2. `observations` — add rating curve columns

The `rating_curve_id` and `rating_curve_correction_version` fields already exist on
the Python types (`Observation`, `RawObservation`) with `None` defaults, and already
appear in the architecture-context.md observations schema (lines 2167-2168). The DB
columns were deliberately omitted from the v0 Alembic migration per v0-scope.md §C.
`PgObservationStore._row_to_domain()` currently hardcodes both to `None`.

This task catches up the actual database to match the existing design:

```sql
ALTER TABLE observations
    ADD COLUMN rating_curve_id UUID REFERENCES rating_curves(id),
    ADD COLUMN rating_curve_correction_version TEXT;
```

Both nullable, metadata-only additions in PostgreSQL. All existing v0 rows remain NULL.

Additionally, extend the `observations.source` CHECK constraint to include
`'rating_curve_derived'` (v0 deliberately restricted it to `('measured',
'manual_import')` per v0-scope.md §C). **Note**: the v0 `metadata.py` uses an
anonymous `CheckConstraint` — the actual constraint name in PostgreSQL may differ
from `ck_observations_source`. At implementation time, confirm the name via
`\d observations` before issuing the DROP.

The extended constraint should also include `'component_derived'`
(architecture-context.md line 2180, conventions.md line 406) to avoid a redundant
migration when Plan 015 (component-derived observations) is implemented:

```sql
ALTER TABLE observations
    DROP CONSTRAINT <actual_constraint_name>;  -- confirm via \d observations
ALTER TABLE observations
    ADD CONSTRAINT ck_observations_source
        CHECK (source IN ('measured', 'manual_import', 'rating_curve_derived', 'component_derived'));
```

Add the index documented in architecture-context.md (line 2185) for Flow 12
Branch A queries:

```sql
CREATE INDEX ix_observations_station_source_ts
    ON observations (station_id, source, timestamp);
```

#### 3. `forecasts` — add rating curve binding

```sql
ALTER TABLE forecasts
    ADD COLUMN rating_curve_id UUID REFERENCES rating_curves(id);

CREATE INDEX ix_forecasts_rating_curve ON forecasts(rating_curve_id);
```

New field on `OperationalForecast` type:

```python
rating_curve_id: RatingCurveId | None = None
```

**Semantics**: The active curve for this station at `issued_at`. NULL for stations
that report discharge directly (Swiss BAFU, weather-only stations). Set at forecast
storage time (step 1.11) by looking up:
`rating_curves WHERE station_id = X AND valid_from <= issued_at AND (valid_to IS NULL OR valid_to > issued_at)`.

#### 4. `observation_versions` table (new — not in architecture-context.md)

This is a **new table** not present in the current architecture doc. Task 8 adds it.

```
observation_versions:
  id: UUID PK
  observation_id: UUID FK -> observations
  station_id: UUID NOT NULL      -- denormalised for efficient lookup
  timestamp: TIMESTAMPTZ NOT NULL
  parameter: TEXT NOT NULL
  value: FLOAT NULL               -- the old discharge value (NULL if observation was MISSING)
  rating_curve_id: UUID FK -> rating_curves   -- the curve that produced this value
  superseded_at: TIMESTAMPTZ DEFAULT now()
  superseded_by_curve_id: UUID FK -> rating_curves  -- the curve that replaced it

Indexes:
  UNIQUE (observation_id, rating_curve_id)  -- one archive row per curve version
  (station_id, parameter, timestamp, rating_curve_id)  -- epoch-matched lookups
```

**Purpose**: Before Flow 12 Branch A overwrites a derived observation, the old
(value, rating_curve_id) is archived here. This preserves the operational record
without complicating the main `observations` table. The archive enables future
same-epoch verification (comparing v1-era forecasts against v1-era observations)
without building that logic now.

**Why not full observation versioning?** Only the value and curve reference change
during rating curve reprocessing. The timestamp, station, QC flags, and source
remain the same. A lightweight archive is simpler than a full versioned observation
store.

**DB permissions**: `sapphire_worker` needs `SELECT/INSERT` on `observation_versions`
(Flow 12 Branch A archives rows; Flow 8/10 reads for epoch queries). Add to
conventions.md permissions table.

#### 5. `skill_scores` — add curve context

```sql
ALTER TABLE skill_scores
    ADD COLUMN rating_curve_id UUID REFERENCES rating_curves(id),
    ADD COLUMN rating_curve_transitions INT NOT NULL DEFAULT 0;
```

Also add to `SkillScore` type:

```python
rating_curve_id: RatingCurveId | None = None
rating_curve_transitions: int = 0
```

**Semantics**:
- `rating_curve_id`: NULL in v1 (cross-epoch aggregate; the default mode).
  Reserved for future per-epoch scores if automatic partitioning is implemented.
- `rating_curve_transitions`: Count of curve version changes within the evaluation
  period. If > 0, consumers know the score spans a discontinuity.

**Unique index**: No change to `uq_skill_scores_natural_key` in v1 — `rating_curve_id`
stays NULL for all rows. **Future migration note**: When per-epoch partitioning is
implemented, `rating_curve_id` must be added to the unique index (with COALESCE for
NULL) to prevent the idempotent upsert from silently overwriting cross-epoch scores
with per-epoch scores. This is a schema migration, not just a logic change.

#### 6. `skill_diagrams` — same treatment

```sql
ALTER TABLE skill_diagrams
    ADD COLUMN rating_curve_id UUID REFERENCES rating_curves(id),
    ADD COLUMN rating_curve_transitions INT NOT NULL DEFAULT 0;
```

Also add to `SkillDiagram` type:

```python
rating_curve_id: RatingCurveId | None = None
rating_curve_transitions: int = 0
```

**Rationale**: A user viewing a reliability diagram or ROC curve that spans a rating
curve transition needs the same transparency signal as on scalar skill scores.
Asymmetric treatment would force consumers to join back to `skill_scores` for the
transition count. Same semantics as §5. **Note**: the `uq_skill_diagrams_natural_key`
index has a different column set than `uq_skill_scores_natural_key` (e.g. no
`forcing_type`), so the future migration to add `rating_curve_id` to both indexes
will require different ALTER statements. **Pre-existing concern**: investigate
whether `forcing_type` absence from `uq_skill_diagrams_natural_key` is intentional
or a schema gap — not introduced by this plan, but relevant to the future migration.

### Flow logic changes

#### Flow 1 (Forecast Cycle) — bind curve at storage (step 1.11)

When storing a forecast for a station that has rating curves, look up the active
curve and set `forecast.rating_curve_id`. Inserted **before step 1.11** (Store
forecast results), not before step 1.6 (Fetch latest observations).

**Guard**: `rating_curve_store` is a flow-function parameter with default `None`
(not a `make_pg_stores()` dict key). When `None` (v0 deployments — callers simply
omit it) or the station has no curves (`gauging_status != 'gauged'` or no
`rating_curve_derived` observations), skip the lookup entirely. This is a no-op
for all Swiss BAFU stations.

**Batch lookup**: To avoid N+1 queries at architectural scale (~1000 stations across
deployments), use a single batch query:

```
Before step 1.11 (Store forecast results):
  station_ids_with_curves = [s.id for s in stations if s.gauging_status == GaugingStatus.GAUGED]
  IF station_ids_with_curves AND rating_curve_store is not None:
    active_curves = rating_curve_store.fetch_active_curves_batch(station_ids_with_curves)
    FOR each forecast in batch:
      forecast.rating_curve_id = active_curves.get(forecast.station_id)
```

**Task granularity**: The batch lookup is a single DB read — wrap in its own `@task`
per orchestration.md §Task granularity (crosses a system boundary).

#### Flow 12 Branch A — archive before overwrite (modify step 12.2a)

Extend **step 12.2a** to include archiving. The fetch and archive are logically coupled
— you must fetch the old values to archive them, and both precede recomputation. This
avoids a step numbering collision (the suffix letter denotes the branch: `12.2b` is
already Branch B's "Validate CSV" step in the architecture doc).

Modified Branch A sequence: 12.2a (fetch + archive), 12.3a (recompute), 12.4a (upsert).

```
Step 12.2a (modified — Fetch and archive derived observations):
  1. Fetch all 'rating_curve_derived' observations for old curve's validity period
     (existing behaviour)
  2. Archive old values before they are overwritten:
     FOR each derived observation fetched:
       INSERT INTO observation_versions (
           observation_id, station_id, timestamp, parameter,
           value, rating_curve_id,
           superseded_by_curve_id
       ) VALUES (
           obs.id, obs.station_id, obs.timestamp, obs.parameter,
           obs.value, obs.rating_curve_id,
           new_curve.id
       ) ON CONFLICT (observation_id, rating_curve_id) DO NOTHING;
       -- idempotent: don't re-archive if already archived

Step 12.3a (unchanged): Recompute with new curve.
Step 12.4a (unchanged): Upsert reprocessed observations with new values and new rating_curve_id.
```

**Concurrency**: The entire sequence 12.2a -> 12.3a -> 12.4a executes within the
per-station concurrency lock `concurrency("observation_write:{station_id}", occupy=1)`
per orchestration.md line 184. The archive operation within 12.2a must be inside this
lock to prevent races with Flow 2 observation ingest.

#### Flow 8/10 (Skill Computation) — count transitions (new step S.3b)

Add a **new step S.3b** that runs **in parallel with S.2 and S.3** (it reads a
different table — `rating_curves` — with no data dependency on observation or
forecast fetches). All three join at S.4 (Compute verification metrics). S.3
remains a pure observation store read; S.3b queries the rating curve store.

```
Step S.3b (new — Count rating curve transitions):
  FOR each (station, eval_period) in scope:
    IF rating_curve_store is not None:
      curves = rating_curve_store.fetch_curves_in_range(station_id, period_start, period_end)
      transition_count = max(0, len(curves) - 1)
    ELSE:
      transition_count = 0

Step S.6 (modified):
  Store skill scores and diagrams with rating_curve_transitions = transition_count
```

This is the minimal v1 change — count transitions, don't partition by them. The
transition count is a transparency signal for API consumers and dashboards.

**Task granularity**: S.3b is a DB read (system boundary). Implement as a separate
`@task`. S.2, S.3, and S.3b all run in parallel (S.2 and S.3 already parallel per
architecture-context.md line 1165-1167; S.3b has no data dependency on either).

**Future extension (not in v1)**: If operational experience shows that cross-epoch
aggregate scores are misleading, add automatic epoch partitioning to S.4. The
provenance columns (`forecasts.rating_curve_id`, `observation_versions`) already
support this — it's a logic change in the skill service, not a schema change
(except the unique index migration noted in §5).

### Logging

New canonical events following logging.md `{entity}.{action}` convention:

| Event | Level | Context fields | Flow |
|-------|-------|---------------|------|
| `rating_curve.bound` | DEBUG | `station_id`, `rating_curve_id`, `rating_curve_version` | 1 |
| `rating_curve.bind_skipped` | DEBUG | `station_id`, `reason` ("no_curves" or "v0_deployment") | 1 |
| `observation.archive_completed` | INFO | `station_id`, `old_rating_curve_id`, `new_rating_curve_id`, `record_count`, `duration_ms` | 12 |
| `observation.reprocess_completed` | INFO | `station_id`, `rating_curve_id`, `record_count`, `duration_ms` | 12 |
| `skill_score.transitions_counted` | INFO | `station_id`, `eval_period_start`, `eval_period_end`, `transition_count` | 8/10 |

Notes:
- `rating_curve.bound` is DEBUG (high-frequency, one per station per forecast cycle).
- `observation.archive_completed` and `observation.reprocess_completed` are INFO
  (infrequent, operationally significant).
- `skill_score.transitions_counted` emits only when `transition_count > 0` (skip when 0
  to avoid noise — conditional emission is a design choice; logging.md is silent on this).
  INFO rather than WARNING because a transition count is an informational annotation,
  not a degraded state — the score itself is still valid (best-truth verification).

**`log_prints=False`**: Per logging.md, all new `@task` definitions in Flow 1
(Task 4) must set `log_prints=False` (mandatory for Flows 1 and 2). Flow 12
tasks (Task 5) should also use `log_prints=False` as a conservative choice
(logging.md does not mandate it for Flow 12 but it avoids noise).

**Audit log (v1)**: Flow 12 Branch A step 12.6 already emits `observation_reprocessed`
to the audit log (architecture-context.md line 1316, `AuditEventType` enum in
conventions.md line 407). The archive sub-step within 12.2a is part of the same reprocessing
transaction — no separate audit event needed. The audit entry's summary should include
`archived_count` alongside the existing `row_count`.

### API changes

#### `GET /api/v1/stations/{id}/skill` — expose transition context

Add to each skill score in response:

```json
{
  "rating_curve_transitions": 1,
  "rating_curve_id": null
}
```

Add `rating_curve_epochs` metadata to station skill responses:

```json
{
  "skill_scores": [...],
  "rating_curve_epochs": [
    {"id": "uuid-v1", "version": 1, "valid_from": "2025-11-01", "valid_to": "2026-07-15"},
    {"id": "uuid-v2", "version": 2, "valid_from": "2026-07-15", "valid_to": null}
  ]
}
```

Dashboards use `rating_curve_epochs` to overlay vertical epoch boundaries on skill
time series. The `rating_curve_transitions > 0` flag is the machine-readable signal
that the score spans a discontinuity.

#### `GET /api/v1/stations/{id}/forecasts` — expose curve binding

Add `rating_curve_id` to each forecast in the response. Optional filter:
`?rating_curve_id=<uuid>`.

#### `GET /api/v1/stations/{id}/rating-curves` (new endpoint)

Lists all rating curves for a station with validity periods. Enables dashboards to
plot epoch boundaries on any time-series chart.

```json
{
  "rating_curves": [
    {"id": "uuid", "version": 1, "valid_from": "...", "valid_to": "...", "interpolation": "linear"},
    {"id": "uuid", "version": 2, "valid_from": "...", "valid_to": null, "interpolation": "log_linear"}
  ]
}
```

**Security (v1)**: Add to security.md authorization matrix — all human roles +
API consumers (read-only, scope-filtered). Same access pattern as
`GET /stations/{id}/observations`. **Sensitivity note**: the `points` JSONB
contains raw hQ table data. Confirm with DHM whether this is publicly shareable
or requires restricted access before implementing. If restricted, the listing
endpoint should omit `points` and a separate detail endpoint should serve them
under tighter access control.

### What this does NOT cover (deferred)

| Item | Why deferred | Enabler already in place |
|------|-------------|------------------------|
| Automatic epoch-partitioned skill computation | Wait for operational evidence that cross-epoch scores are misleading | `forecasts.rating_curve_id` + `observation_versions` table + unique index migration path documented |
| Same-epoch verification mode | Complex; best-truth (WMO-1364) is the standard default | `observation_versions` stores old values |
| Model retraining trigger on curve change | Separate concern (Flow 9) | `rating_curve_transitions` flag on skill scores |
| DHM correction parameter semantics | Awaiting DHM data discussions | `rating_curve_correction_version` column on observations |
| Shift-adjusted curves (USGS-style) | Requires DHM buy-in on operational workflow | `rating_curves.points` JSONB is flexible enough |
| Rating curve upload endpoint | Separate API design (requires role constraints, validation) | `uploaded_by` FK + `InterpolationMethod` enum ready |
| `uploaded_by FK -> users(id)` constraint | `users` table does not yet exist in the migration chain | Column added as `UUID NULL` without FK in Task 1; FK migration tracked as separate v1 task after `users` table is created |
| `fetch_active_curves_batch()` method | ~~Deferred~~ **Moved to Task 1** (trivial `WHERE station_id = ANY($1)` variant of `fetch_active_curve()`; required by Task 4) | Both `fetch_active_curves_batch()` and `fetch_curves_in_range()` added in Task 1 |

---

## Tasks

### Task 1: Create `rating_curves` table + `RatingCurveStore` implementation

**Scope**: Add `rating_curves` table to `metadata.py` (Alembic migration; v1-scope,
executes at v1 phase start per R4).
Implement `PgRatingCurveStore` satisfying the existing `RatingCurveStore` protocol.
**Extend** the `RatingCurveStore` protocol with two new methods:
- `fetch_curves_in_range(station_id, start, end)` — for epoch queries (Flow 8/10)
- `fetch_active_curves_batch(station_ids)` — for batch lookup (Flow 1)

Add `InterpolationMethod` enum to types. Replace the existing
`Literal["linear", "log-linear"]` on `RatingCurve.interpolation` with
`InterpolationMethod` (normalises the hyphenated `"log-linear"` to `"log_linear"`
per the codebase-wide underscore convention).

**Alembic**: Extend the linear migration chain (next revision after current head).
This migration must precede Tasks 2 and 3 in the chain (FK dependency).

**Not in scope**: Rating curve upload API, h<->Q conversion logic.

**Verification**: `uv run pytest tests/integration/store/test_rating_curve_store.py -v`

### Task 2: Add `rating_curve_id` columns to `observations` and `forecasts`

**Scope**: Add nullable FK columns to `observations` and `forecasts` tables in
`metadata.py` (Alembic migration, depends on Task 1's migration). Extend the
`observations.source` CHECK constraint from `('measured', 'manual_import')` to
include `'rating_curve_derived'`. Add `(station_id, source, timestamp)` index on
`observations` (architecture-context.md line 2184, needed for Branch A queries).
Add `rating_curve_id` field to `OperationalForecast` type. Update store functions:
- `PgObservationStore._row_to_domain()`: read from DB column instead of hardcoding `None`
- `PgObservationStore._obs_to_values()`: include `rating_curve_id` and
  `rating_curve_correction_version` in the INSERT dict
- `PgObservationStore.store_raw_observations()`: include `rating_curve_id` and
  `rating_curve_correction_version` in the INSERT dict (currently omitted, same gap
  as `_obs_to_values()` — `RawObservation` carries both fields)
- `PgForecastStore.store_forecast()`: write the new column
- `PgForecastStore._rows_to_domain()`: read the new column

All existing rows remain NULL.

**Not in scope**: Setting the value at forecast creation time (Task 4).

**Verification**: `uv run pytest tests/integration/store/test_observation_store.py tests/integration/store/test_forecast_store.py -v`

### Task 3: Create `observation_versions` table + store

**Scope**: Add `observation_versions` table to `metadata.py` (Alembic migration,
depends on Task 1's migration for FK to `rating_curves`). Add
`ObservationVersionId = NewType("ObservationVersionId", UUID)` to `types/ids.py`.
Add `ArchivedObservationValue` frozen dataclass:

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class ArchivedObservationValue:
    id: ObservationVersionId
    observation_id: ObservationId
    station_id: StationId
    timestamp: UtcDatetime
    parameter: str
    value: float | None          # None if the superseded observation was MISSING
    rating_curve_id: RatingCurveId
    superseded_at: UtcDatetime
    superseded_by_curve_id: RatingCurveId
```

Add `ObservationVersionStore` protocol and `PgObservationVersionStore` implementation:

```python
class ObservationVersionStore(Protocol):
    def archive_observation_values(
        self,
        observations: Sequence[Observation],
        superseded_by_curve_id: RatingCurveId,
    ) -> int:
        """Archive current values before reprocessing. Returns count of rows archived."""
        ...

    def fetch_archived_values(
        self,
        station_id: StationId,
        parameter: str,
        start: UtcDatetime,
        end: UtcDatetime,
        rating_curve_id: RatingCurveId | None = None,
    ) -> Sequence[ArchivedObservationValue]:
        """Fetch archived values, optionally filtered by the curve that produced them."""
        ...
```

Update conventions.md: add `SELECT/INSERT` grant for `sapphire_worker` on
`observation_versions`. Add `ObservationVersionId` to types-and-protocols.md
protocol module inventory.

**Not in scope**: Integration with Flow 12 (Task 5).

**Verification**: `uv run pytest tests/integration/store/test_observation_version_store.py -v`

### Task 4: Bind rating curve at forecast storage (Flow 1, step 1.11)

**Scope**: In the forecast cycle flow, before step 1.11 (Store forecast results),
batch-lookup active rating curves for stations that use them and set
`forecast.rating_curve_id` before storing. No-op when `rating_curve_store` is not
injected (v0 deployments) or station has no curves. Add logging events
(`rating_curve.bound`, `rating_curve.bind_skipped`).

**Not in scope**: Hindcast curve binding (uses `forcing_type`, not operational curves).

**Verification**: `uv run pytest tests/unit/flows/test_run_forecast_cycle.py -v`

### Task 5: Archive old values in Flow 12 Branch A (extend step 12.2a)

**Depends on**: Task 2 (`observations.rating_curve_id` column must exist for
`fetch_derived_observations_by_curve()`) and Task 3 (`ObservationVersionStore`).

**Scope**: Extend step 12.2a in Flow 12 Branch A — after fetching derived observations,
archive old values to `observation_versions` before reprocessing. The archive runs
inside the `concurrency("observation_write:{station_id}", occupy=1)` lock. Add logging
event (`observation.archive_completed`). Implement `fetch_derived_observations_by_curve()`
in `PgObservationStore` (method already declared on the `ObservationStore` Protocol).

**Not in scope**: Full Flow 12 Branch A implementation (requires h<->Q conversion
service which is a separate design). Note: `AuditEventType` (referenced by step
12.6's audit log emission) exists in `docs/spec/types-and-protocols.md` and
`docs/conventions.md` but has no Python implementation in `src/` yet — it must be
added to `types/enums.py` when Flow 12 Branch A is fully implemented (separate task).

**Verification**: `uv run pytest tests/unit/flows/test_reprocess_observations.py -v`

### Task 6: Add `rating_curve_transitions` to skill computation (new step S.3b)

**Scope**: Add new step S.3b to the skill computation flow — count rating curve
transitions within the evaluation period using `fetch_curves_in_range()`. Store
the count on both `skill_scores` and `skill_diagrams`.

**Schema**: Add `rating_curve_id` and `rating_curve_transitions` columns to
`skill_scores` and `skill_diagrams` in `metadata.py` (Alembic migration). Update
`SkillScore` and `SkillDiagram` types with both new fields. Add logging event
(`skill_score.transitions_counted`).

Guard: no-op when `rating_curve_store` is not injected.

**Not in scope**: Epoch-partitioned skill computation (deferred).

**Verification**: `uv run pytest tests/unit/services/skill/test_service.py tests/integration/store/test_skill_store.py -v`

### Task 7: API — rating curve endpoints and skill/forecast annotations

**Scope**: Add `GET /stations/{id}/rating-curves` endpoint. Add
`rating_curve_transitions`, `rating_curve_id`, and `rating_curve_epochs` to skill
endpoint responses. Add `rating_curve_id` to forecast endpoint responses. Add
`?rating_curve_id` filter to forecast listing.

Update security.md authorization matrix: add `GET /stations/{id}/rating-curves`
with same access pattern as `GET /stations/{id}/observations` (all roles + API
consumers, scope-filtered).

**Not in scope**: Dashboard UI changes (external consumer), rating curve upload
endpoint.

**Verification**: `uv run pytest tests/integration/api/ -v` (create test directory
if absent; follow existing API test patterns or establish new ones if this is the
first API test module).

### Task 8: Update architecture-context.md, conventions.md, and handover docs

**Scope**:
- **architecture-context.md**: Add `UNIQUE (station_id, version)` index to
  `rating_curves` schema. Update `uploaded_by` from bare `UUID NULL` to
  `UUID NULL FK -> users(id)` with note that FK is added in a later migration
  (after `users` table). Add `rating_curve_id` to `forecasts` schema. Update
  `interpolation` value from `"log-linear"` to `"log_linear"` (convention alignment).
  Add `observation_versions` table schema. Add `rating_curve_id` and
  `rating_curve_transitions` to `skill_scores` and `skill_diagrams` schemas. Update
  step 12.2a in Flow 12 Branch A (archive sub-step). Add step S.3b to Flow 8/10
  (parallel with S.2 and S.3). Update step 1.11 note to include rating curve binding.
- **docs/spec/database-schema.md**: Update `interpolation` value from `"log-linear"`
  to `"log_linear"` (line 588, same convention alignment as architecture-context.md).
  Update v0 ER diagram (line 124) to add `rating_curve_derived` and
  `component_derived` to `observations.source` values when the v1 migration runs.
- **conventions.md**: Add `InterpolationMethod` to enum master list. Add
  `observation_versions` to `sapphire_worker` permissions (`SELECT/INSERT`).
  Fix pre-existing gap: add `skill_diagrams` to `sapphire_worker`'s
  `SELECT/INSERT/UPDATE` grant list (currently missing — only `skill_scores` is
  listed, but `PgSkillStore.store_skill_diagrams()` writes to `skill_diagrams`).
  Note: `sapphire_api` already has `SELECT all` — no explicit per-table addition
  needed for `rating_curves`.
  **Note — wider grant audit needed (out of scope)**: The `sapphire_worker` grant
  list has a systemic gap beyond `skill_diagrams` — 14 tables written by
  `Pg*Store` classes are missing or under-granted (onboarding/initialization
  stores added after the original grant list: `models`, `basins`, `stations`,
  `station_thresholds`, `model_assignments`, `group_model_assignments`,
  `flow_regime_configs`, `clim_baselines`, `historical_forcing`, `model_states`;
  plus `station_weather_sources`, `station_groups`, `station_group_members`
  listed as SELECT-only but receiving writes). A comprehensive grant rewrite
  should be done as a separate task before production RBAC is wired up. This
  plan fixes only the `skill_diagrams` and `observation_versions` grants
  directly relevant to rating curve provenance.
- **types-and-protocols.md**: Add `fetch_curves_in_range()` and
  `fetch_active_curves_batch()` to `RatingCurveStore` protocol. Add
  `ArchivedObservationValue` domain type and `ObservationVersionStore` protocol
  to the protocol module inventory. Add `rating_curve_id` to `OperationalForecast`.
  Add `rating_curve_id` and `rating_curve_transitions` to `SkillScore` and
  `SkillDiagram`. Replace `Literal["linear", "log-linear"]` with
  `InterpolationMethod` on `RatingCurve.interpolation`.
- **handover/data-flows.md**: Update Flow 8/10 and Flow 12 notes.
  Harmonise Flow 12 concurrency notation at line 1336 with orchestration.md format:
  `concurrency("observation_write:{station_id}", occupy=1)`.
- **logging.md**: Add canonical events table for rating curve operations.
- **security.md**: Add `GET /stations/{id}/rating-curves` to authorization matrix.
- **wmo.md**: WMO-1044 already appears at lines 25 and 95 with Nepal v1 context.
  Add a cross-reference from the existing entry to this plan's provenance design.

**Not in scope**: v0-scope.md (no v0 changes).

**Verification**: Manual review — no stale docs.

---

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1",
      "name": "Schema + stores",
      "tasks": ["1", "2", "3"],
      "parallel": false,
      "note": "Task 1 must generate its Alembic migration first (rating_curves table). Tasks 2 and 3 depend on Task 1 for FK references but can run in parallel with each other after Task 1 completes.",
      "order": "1 -> (2 || 3)"
    },
    {
      "id": "phase-2",
      "name": "Flow integration",
      "tasks": ["4", "5", "6"],
      "parallel": true,
      "depends_on": ["phase-1"],
      "note": "Task 5 additionally depends on Task 2 (rating_curve_id column on observations) and Task 3 (ObservationVersionStore). Tasks 4 and 6 depend only on Task 1."
    },
    {
      "id": "phase-3",
      "name": "API + docs",
      "tasks": ["7", "8"],
      "parallel": true,
      "depends_on": ["phase-2"],
      "note": "Task 7 requires Phase 9 (FastAPI) to be sufficiently complete for new endpoints."
    }
  ]
}
```

**Alembic migration chain**: Task 1's migration (CREATE `rating_curves`) must be
the earliest in the chain. Tasks 2 and 3 generate separate migrations that depend
on Task 1's (Alembic `down_revision`). Task 6's migration (ALTER `skill_scores`,
`skill_diagrams`) is independent of Tasks 2/3 and can slot anywhere after Task 1.

---

## Risks

### R1: DHM correction parameter still undefined

The `rating_curve_correction_version` column is present but its semantics depend
on DHM discussions. This plan does not implement the correction logic — it provides
the storage column. No risk to the plan; the column is nullable and informational.

### R2: observation_versions table growth

For stations with frequent curve updates, the archive table grows proportionally.
At Nepal scale (~1000 stations, yearly updates, ~365 derived observations per
station per year), this is ~365K rows per reprocessing cycle — negligible.

### R3: Epoch-partitioned skill may be needed sooner than expected

If the first monsoon season produces confusing skill scores, the team may need
per-epoch partitioning urgently. The provenance columns built here make that a
logic change in the skill service **plus a unique index migration** on `skill_scores`
and `skill_diagrams` (adding `rating_curve_id` to the natural key with COALESCE).
Estimate: 1-2 days of work on top of this plan's foundation.

### R4: v0-scope.md compliance and deployment safety

v0-scope.md explicitly defers rating curve infrastructure:
- §B: `rating_curves` listed under "Deferred schemas (don't create tables)"
- §C: `observations` drops `rating_curve_id` and `rating_curve_correction_version`
- §G: `RatingCurveStore` excluded from v0 store Protocols
- Flows table: Flow 12 Branch A "requires v1"

§B's rationale: "Empty 'for later' tables add migration maintenance burden and
clutter the schema." The phrase "add via Alembic migrations when actually
implemented" means: create migrations when the feature is operationally implemented
at v1 phase start — not as preparatory scaffolding during v0.

**Therefore**: Tasks 1–8 execute at v1 phase start, not during v0 development. This
plan is committed now as a design document to lock decisions and enable review.

**Scope distinction — deferred architecture vs. new design decisions**: Some schema
changes in this plan implement architecture that was already designed but deferred
from v0 (e.g., `rating_curves` table per §B, `observations.rating_curve_id` per §C).
Others are **new design decisions committed by this plan** and not present in any
existing architecture document:
- `forecasts.rating_curve_id` — new column, not in architecture-context.md's
  `forecasts` schema
- `observation_versions` table — new archive table for superseded values
- `rating_curve_transitions` on `skill_scores`/`skill_diagrams` — new transparency
  signal

Task 8 updates the architecture docs to reflect all of these.

When Tasks 1–8 do execute at v1 phase start, the migrations are technically safe
for any v0 database that runs `alembic upgrade head`:
1. All schema changes are additive (new tables, nullable FK columns). No existing
   queries, constraints, or indexes are affected.
2. All flow logic changes are guarded: `rating_curve_store` is injected as a flow
   parameter with default `None`. In v0 deployments, callers do not pass it, so
   all rating-curve code paths are no-ops.
3. Type changes to `SkillScore`, `SkillDiagram`, and `OperationalForecast` are
   additive (new fields with default values `None`/`0`) and do not affect existing
   v0 code — no existing constructor call or assertion needs updating.
4. The result is empty tables and NULL columns — no orphaned state.

**Integration risk**: Task 4 modifies `run_forecast_cycle.py`, an actively developed
v0 flow. Commits to this file during the v0→v1 gap increase merge conflict risk.
Recommendation: rebase Task 4 against current `main` at v1 phase start before
implementing.
