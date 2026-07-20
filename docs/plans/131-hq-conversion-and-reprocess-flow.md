---
status: READY
created: 2026-07-20
plan: 131
title: level→discharge (h→Q) conversion service
scope: v1 — the pure rating-curve conversion service. The reprocess flow (Flow 12 Branch A) is deferred (see Non-goals).
---

# Plan 131 — level→discharge (h→Q) conversion service

**Status**: READY
**Depends on**: Plan 035 Task 1 (`RatingCurve` + `RatingCurveStore`, #100) — MERGED. Adopts
the DHM `RT_*.txt` template + provisional conversion policies documented in Plan 035 §1a.
**Consumed by (future, out of scope here)**: the level→discharge-**at-ingest** producer
(Flow 2 step 2.5 / DHM adapter, which will emit `source='rating_curve_derived'` observations)
and the `reprocess_observations` flow (Flow 12 Branch A). Both reuse this one service.

## Why (and why this scope)

Plan 035 built the *storage + provenance* for rating curves. Nothing in the codebase actually
**converts a water level to discharge** through a curve. That conversion is the keystone: it is
needed identically by (a) the future ingest path that *produces* `rating_curve_derived`
observations and (b) the reprocess flow that recomputes them when a curve changes.

A `/plan` loop on the fuller "conversion + reprocess flow" scope surfaced that the reprocess
flow would be a **validated no-op until a producer of `rating_curve_derived` observations
exists** — and that producer (Flow 2 step 2.5, blocked on the DHM ingestion track) is itself
not built. So this plan builds only the **pure conversion service** now; the flow is deferred
until its input producer lands. This keeps the plan small, convergeable, and free of the
flow-registration / trigger / AUTOCOMMIT-idempotency concerns that belong with the flow.

## What it is

A pure, injectable domain service — **no I/O, no store, no clock, no randomness** — that turns
a water level into a discharge through a `RatingCurve`. Lives in `services/` (e.g.
`services/rating_conversion.py`).

### Public surface

```python
class RatingRange(Enum):          # which side of the tabulated domain a level fell outside
    IN_RANGE = auto()
    BELOW = auto()                # level < lowest tabulated stage
    ABOVE = auto()                # level > highest tabulated stage

@dataclass(frozen=True, kw_only=True, slots=True)
class ConversionResult:
    discharge: float
    range_flag: RatingRange       # caller (ingest/reprocess) decides whether to attach a QC flag

class RatingConverter:
    """Built once per curve (validates + prepares); convert() is cheap and pure."""
    @classmethod
    def from_curve(cls, curve: RatingCurve) -> RatingConverter: ...
    def convert(self, level: float) -> ConversionResult: ...
```

- Building via `from_curve` **validates the curve once** (see D3) and captures the sorted
  points + interpolation method. `convert()` does no validation beyond the input `level` being
  finite. A convenience `convert_level_to_discharge(level, curve)` may wrap the two for
  one-shot callers, but batch callers (reprocess) build the converter once.

## Ratified design decisions

### D1 — Interpolation: implement BOTH `linear` and `log_linear` (`InterpolationMethod`, enums.py:280)
- **`linear`**: piecewise-linear in (stage, discharge). For a level between tabulated stages
  `h0<h<h1`: `Q = Q0 + (Q1-Q0) * (h-h0)/(h1-h0)`. Exact hit on a tabulated stage returns that
  row's discharge.
- **`log_linear`**: piecewise-linear in **log-discharge** vs stage — i.e. interpolate
  `ln(Q)` linearly against `h`, then exponentiate: `Q = exp( ln(Q0) + (ln(Q1)-ln(Q0)) *
  (h-h0)/(h1-h0) )`. This matches the hydrometric convention that Q grows exponentially with
  stage between control points. Requires `discharge > 0` at every tabulated point (see D3);
  the RT sample's lowest row is `1,59.6` (positive), consistent. **Grill-me:** confirm
  log-discharge-vs-**stage** (not log-log stage-discharge) is the intended DHM convention
  (dhm §4.5).

### D2 — Out-of-range: **clamp + flag** (RT §1a / dhm §4.4, provisional)
- Level below the lowest tabulated stage → `discharge = Q(lowest stage)`, `range_flag=BELOW`.
- Level above the highest tabulated stage → `discharge = Q(highest stage)`, `range_flag=ABOVE`.
- **No extrapolation.** The service never invents discharge outside the table; it clamps and
  reports the flag so the *caller* attaches a QC flag / decides policy. This keeps the DHM
  §4.4 policy question (clamp vs reject vs "≈0 below stage") at the caller boundary, not baked
  into the math. Provisional default = clamp; revisit when DHM answers §4.4.

### D3 — Validate the curve in the SERVICE, not by retyping `RatingCurve.points`
`RatingCurve.points` stays `list[dict]` (`types/rating_curve.py:21`, as merged and stored
straight into JSONB by `PgRatingCurveStore.store_rating_curve`, `store/rating_curve_store.py:30`).
**We do not retype it** — a dataclass retype would break JSONB write/read and gate every DB
read through new validation. Instead `RatingConverter.from_curve` validates at build time:
- non-empty points;
- each point is a mapping with numeric `water_level` and `discharge` keys — a missing key or
  a non-numeric value raises a **clear domain error**, never an incidental `KeyError`/`TypeError`
  (points is raw JSONB-shaped `list[dict]`);
- every `water_level` and `discharge` is **finite** (reject NaN/±inf — consistent with raw
  observation validation, `store/observation_store.py:259`);
- **sort points by `water_level` inside `from_curve`** (do not assume DB order — an unsorted
  but otherwise valid table is accepted and sorted); after sorting, `water_level` must be
  **strictly increasing** — a **duplicate stage** (same `water_level` on two rows) is an error:
  a stage must map to exactly one discharge;
- `discharge` **non-decreasing** — ties allowed. (We deliberately do **not** require strict
  discharge monotonicity: real rating tables have equal discharge at adjacent low-flow stages;
  single-valuedness of level→Q only needs the *stage* axis strictly increasing. Strict
  discharge would only matter for the discharge→level inverse, which is a Non-goal.);
- for `log_linear`, additionally every `discharge > 0`.
Raise a clear `ValueError`/domain error on violation.

### D4 — Pure and injectable
No store, connection, clock, `datetime.now()`, or RNG. Deterministic: same (level, curve) →
same result. Trivially unit-testable with hand-built `RatingCurve`s; no DB or container needed.

## Tasks

### Task 1 — `RatingConverter` service + result types
Add `services/rating_conversion.py` with `RatingRange`, `ConversionResult`, `RatingConverter`
(+ optional `convert_level_to_discharge` wrapper). Implement D1–D4. Add `RatingRange` /
`ConversionResult` to `docs/spec/types-and-protocols.md`; note the service in
`docs/conventions.md` enum list if `RatingRange` qualifies.

**Verification** (all pure unit tests, no DB): `uv run pytest tests/unit/services/test_rating_conversion.py`
- exact tabulated hit; interior linear interpolation (known value from the RT sample:
  `h=1.05 → 64.9` for the `1,59.6`/`1.1,70.2` rows);
- `log_linear` interior value (computed by hand);
- below-range → clamp to lowest + `BELOW`; above-range → clamp to highest + `ABOVE`;
- validation errors: empty points, NaN/inf, **duplicate stage**, `log_linear` with a
  non-positive discharge;
- **malformed JSONB points**: a point missing `water_level`/`discharge`, or with a non-numeric
  value, raises a clear domain error (not `KeyError`/`TypeError`);
- **unsorted-but-unique** points are accepted (sorted internally) and convert correctly;
- discharge ties at adjacent stages are ACCEPTED (regression against the dropped invariant);
- non-finite input `level` rejected.

## Non-goals (deferred)
- **`reprocess_observations` flow (Flow 12 Branch A)** — a separate plan once a producer of
  `rating_curve_derived` observations exists. That plan owns: extending
  `fetch_derived_observations_by_curve` to be curve+window-bounded, archive-before-recompute
  ordering, the `(station_id,"observation_write")` lock, the trigger (no upload endpoint yet),
  AUTOCOMMIT idempotency, run-name registration, and the `audit_log`/`AuditEventType`
  dependency (step 12.6). None of that is in this plan.
- **Producer of `rating_curve_derived` observations** (Flow 2 step 2.5 ingest / DHM adapter) —
  blocked on dhm §4; will *consume* this service.
- **discharge→level inversion**, **rating-curve upload API** (Plan 035 Task 7), **DHM
  `RT_*.txt` parser**, and the **`rating_curve_correction_version` correction** (dhm §4.6, TBD).

## Residual forks for the human (grill-me)
1. **`log_linear` convention** — log-discharge-vs-stage (D1) vs some other form? Confirm with
   DHM (§4.5) before a real curve uses it. Low risk now (no `log_linear` curves exist yet).
2. **Out-of-range default = clamp** (D2) — vs reject, or "≈0 below the lowest stage" (dhm §4.4
   floated this). Kept at the caller boundary via `range_flag`, so changing the policy later
   doesn't touch the math.
3. **Where the QC flag is attached** — the service only reports `range_flag`; the ingest /
   reprocess caller maps it to a QC flag. Confirm that boundary split is what you want.
