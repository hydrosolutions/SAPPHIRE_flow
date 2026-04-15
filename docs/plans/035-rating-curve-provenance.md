# Plan 035 — Rating Curve Provenance for Skill Score Integrity

**Status**: DRAFT
**Phase**: v1 preparation (schema + types + flow logic)
**Scope**: v1 (Nepal). No v0 code changes — all new columns are nullable, all new tables are additive.
**Depends on**: Rating curves table creation (currently designed in architecture-context.md §2206 but not implemented)

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
  Data tiers: provisional → checked → approved. Skill against provisional data is
  flagged as preliminary. Full revision history maintained.
- **BOM (Australia)**: Quality codes on every discharge value. Estimated vs verified
  discharge distinguished. Skill against estimated data carries a lower confidence flag.
- **EFAS/GloFAS**: Often verify against water level directly, sidestepping the rating
  curve. For discharge, they use national services' published data and freeze the
  verification dataset for formal skill assessments (WMO-No. 1076 approach).
- **WMO guidance**: Verify against the best available observation at verification time.
  Freeze the verification dataset for formal campaigns. Ad hoc recomputation against
  revised data is discouraged because it makes historical scores non-reproducible.

### Design principles

1. **Provenance first**: Track which curve produced which value — on observations,
   forecasts, and skill scores. This is non-negotiable traceability.
2. **Best-truth verification** (WMO approach): Flow 10 verifies against the current
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

#### 1. `rating_curves` table (create — already designed, architecture-context.md §2206)

No schema changes from existing design. Just needs implementation:

```
rating_curves:
  id: UUID PK
  station_id: UUID FK → stations
  version: INT                    -- monotonically increasing per station
  valid_from: TIMESTAMPTZ
  valid_to: TIMESTAMPTZ NULL      -- NULL = currently active
  points: JSONB                   -- [{"water_level": float, "discharge": float}, ...]
  interpolation: TEXT DEFAULT 'linear'
  uploaded_by: UUID NULL
  created_at: TIMESTAMPTZ

Indexes:
  (station_id, valid_from DESC)
  UNIQUE (station_id) WHERE valid_to IS NULL   -- at most one active curve per station
  UNIQUE (station_id, version)
```

#### 2. `observations` — add rating curve columns (v1)

```sql
ALTER TABLE observations
    ADD COLUMN rating_curve_id UUID REFERENCES rating_curves(id),
    ADD COLUMN rating_curve_correction_version TEXT;
```

Already on the Python types (`Observation`, `RawObservation`). Currently hardcoded
to `None` in `PgObservationStore._row_to_domain()`. The DB column catch-up.

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
creation time by looking up:
`rating_curves WHERE station_id = X AND valid_from <= issued_at AND (valid_to IS NULL OR valid_to > issued_at)`.

#### 4. `observation_versions` table (new — lightweight archive)

```
observation_versions:
  id: UUID PK
  observation_id: UUID FK → observations
  station_id: UUID NOT NULL      -- denormalised for efficient lookup
  timestamp: TIMESTAMPTZ NOT NULL
  parameter: TEXT NOT NULL
  value: FLOAT                   -- the old discharge value
  rating_curve_id: UUID FK → rating_curves   -- the curve that produced this value
  superseded_at: TIMESTAMPTZ DEFAULT now()
  superseded_by_curve_id: UUID FK → rating_curves  -- the curve that replaced it

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

No unique index change needed in v1 — `rating_curve_id` stays NULL for all rows.

#### 6. `skill_diagrams` — same treatment

```sql
ALTER TABLE skill_diagrams
    ADD COLUMN rating_curve_id UUID REFERENCES rating_curves(id);
```

### Flow logic changes

#### Flow 1 (Forecast Cycle) — bind curve at issuance

When creating a forecast for a station that has rating curves, look up the active
curve and set `forecast.rating_curve_id`. This is a single query per station per
cycle — negligible overhead.

```
Before step 1.6 (Store forecast):
  IF station uses rating curves:
    active_curve = rating_curve_store.fetch_active_curve(station_id)
    forecast.rating_curve_id = active_curve.id if active_curve else None
```

#### Flow 12 Branch A — archive before overwrite

Modify step 12.3a to archive old values before reprocessing:

```
Step 12.3a (modified):
  FOR each derived observation in old curve's validity period:
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

Step 12.4a (unchanged): Upsert observations with new values and new rating_curve_id.
```

#### Flow 8/10 (Skill Computation) — count transitions

Modify step S.3 to detect rating curve transitions:

```
Step S.3 (modified):
  FOR each (station, eval_period) in scope:
    curves = rating_curve_store.fetch_curves_in_range(station_id, period_start, period_end)
    transition_count = max(0, len(curves) - 1)

Step S.6 (modified):
  Store skill scores with rating_curve_transitions = transition_count
```

This is the minimal v1 change — count transitions, don't partition by them. The
transition count is a transparency signal for API consumers and dashboards.

**Future extension (not in v1)**: If operational experience shows that cross-epoch
aggregate scores are misleading, add automatic epoch partitioning to S.4. The
provenance columns (`forecasts.rating_curve_id`, `observation_versions`) already
support this — it's a logic change in the skill service, not a schema change.

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
    {"id": "uuid", "version": 2, "valid_from": "...", "valid_to": null, "interpolation": "log-linear"}
  ]
}
```

### What this does NOT cover (deferred)

| Item | Why deferred | Enabler already in place |
|------|-------------|------------------------|
| Automatic epoch-partitioned skill computation | Wait for operational evidence that cross-epoch scores are misleading | `forecasts.rating_curve_id` + `observation_versions` table |
| Same-epoch verification mode | Complex; best-truth (WMO) is the standard default | `observation_versions` stores old values |
| Model retraining trigger on curve change | Separate concern (Flow 9) | `rating_curve_transitions` flag on skill scores |
| DHM correction parameter semantics | Awaiting DHM data discussions | `rating_curve_correction_version` column on observations |
| Shift-adjusted curves (USGS-style) | Requires DHM buy-in on operational workflow | `rating_curves.points` JSONB is flexible enough |

---

## Tasks

### Task 1: Create `rating_curves` table + `RatingCurveStore` implementation

**Scope**: Add `rating_curves` table to `metadata.py`. Implement `PgRatingCurveStore`
satisfying the existing `RatingCurveStore` protocol. Add `fetch_curves_in_range()`
method for epoch queries.

**Not in scope**: Rating curve upload API, h↔Q conversion logic.

**Verification**: `uv run pytest tests/store/test_rating_curve_store.py -v`

### Task 2: Add `rating_curve_id` columns to `observations` and `forecasts`

**Scope**: Add nullable FK columns to `observations` and `forecasts` tables in
`metadata.py`. Add `rating_curve_id` field to `OperationalForecast` type. Update
`PgObservationStore._row_to_domain()` and `PgForecastStore` to read/write the
new columns. All existing rows remain NULL.

**Not in scope**: Setting the value at forecast creation time (Task 4).

**Verification**: `uv run pytest tests/store/test_observation_store.py tests/store/test_forecast_store.py -v`

### Task 3: Create `observation_versions` table + store

**Scope**: Add `observation_versions` table to `metadata.py`. Add
`ObservationVersionStore` protocol and `PgObservationVersionStore` implementation
with `archive_before_reprocessing()` and `fetch_archived_values()` methods.

**Not in scope**: Integration with Flow 12 (Task 5).

**Verification**: `uv run pytest tests/store/test_observation_version_store.py -v`

### Task 4: Bind rating curve at forecast creation (Flow 1)

**Scope**: In the forecast cycle flow, look up the active rating curve for each
station and set `forecast.rating_curve_id` before storing. No-op for stations
without rating curves.

**Not in scope**: Hindcast curve binding (uses `forcing_type`, not operational curves).

**Verification**: `uv run pytest tests/flows/test_run_forecast_cycle.py -v`

### Task 5: Archive old values in Flow 12 Branch A

**Scope**: Modify Flow 12 Branch A (step 12.3a) to archive old observation values
to `observation_versions` before upserting with new curve. Implement
`fetch_derived_observations_by_curve()` stub in `PgObservationStore`.

**Not in scope**: Full Flow 12 Branch A implementation (requires h↔Q conversion
service which is a separate design).

**Verification**: `uv run pytest tests/flows/test_reprocess_observations.py -v`

### Task 6: Add `rating_curve_transitions` to skill computation

**Scope**: In the skill computation service, count rating curve transitions within
the evaluation period. Store the count on `skill_scores`. Add `rating_curve_id`
and `rating_curve_transitions` columns to `skill_scores` and `skill_diagrams` in
`metadata.py`. Update `SkillScore` type.

**Not in scope**: Epoch-partitioned skill computation (deferred).

**Verification**: `uv run pytest tests/services/test_skill_service.py -v`

### Task 7: API — rating curve endpoints and skill/forecast annotations

**Scope**: Add `GET /stations/{id}/rating-curves` endpoint. Add
`rating_curve_transitions` and `rating_curve_epochs` to skill endpoint responses.
Add `rating_curve_id` to forecast endpoint responses. Add `?rating_curve_id` filter
to forecast listing.

**Not in scope**: Dashboard UI changes (external consumer).

**Verification**: `uv run pytest tests/api/ -v`

### Task 8: Update architecture-context.md and handover docs

**Scope**: Update Flow 12 Branch A steps in architecture-context.md to include the
archive step. Add `rating_curve_id` to forecast schema. Add `observation_versions`
table schema. Add `rating_curve_transitions` to skill_scores schema. Update
handover/data-flows.md Flow 8/10 and Flow 12 notes. Update types-and-protocols.md
for new fields.

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
      "parallel": true
    },
    {
      "id": "phase-2",
      "name": "Flow integration",
      "tasks": ["4", "5", "6"],
      "parallel": true,
      "depends_on": ["phase-1"]
    },
    {
      "id": "phase-3",
      "name": "API + docs",
      "tasks": ["7", "8"],
      "parallel": true,
      "depends_on": ["phase-2"]
    }
  ]
}
```

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
pure logic change in the skill service (no schema migration required). Estimate:
1–2 days of work on top of this plan's foundation.
