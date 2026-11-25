---
status: READY
created: 2026-11-25
issues: [630, 454]
---

# Per-time threshold probabilities and honest verification

Extends M2 of `2026-11-24-bafu-threshold-import.md`. M1 is deployed on the Mac
mini at 0.1.1191: 165 stations, 660 authority thresholds; 20 missing and two
water-level-only sources excluded. No forecast-alert switch or activation change.

Owner decision (25 November): show bounds outside supplied quantiles; no invented
tail probabilities. Score supported probabilities only, with excluded counts.
These are conditional-subset scores, not whole-period skill or model ranking.

## Evidence and reuse

- `services/skill/quantiles.py` validates noncrossing finite quantiles and already
  interpolates quantile values for scoring. Its 5–95% support constraint is for
  that score, not a universal probability-converter requirement.
- `services/alert_strategy.py::_compute_exceedance` interpolates but clamps tails
  and reduces by maximum across times. Do not use that as a per-time probability
  or change existing alert policy as part of this delivery.
- `services/skill/metrics.py` and `diagrams.py` contain BSS, contingency,
  reliability and ROC formulas. They currently derive probabilities from members;
  extract probability-input cores and retain existing member wrappers.
- `services/skill/service.py` correctly omits these metrics for quantile outputs
  at computation version 3. Preserve the omission until provenance/counts exist.
- `review-series` already selects visible, scoped, run/artifact-pinned raw values
  in bulk. Extend it additively; no new per-issue endpoint or frontend arithmetic.
  Review temporal support currently says unknown: cadence is not interval support.

## A: shared probability conversion and review delivery

One pure typed conversion function accepts a representation and one timestamp's
label/value pairs plus a finite threshold. Return immutable estimate with method,
point probability (nullable), conservative lower/upper bounds, and reason/state.
Use strict exceedance `P(Q > threshold)` consistently with existing forecast skill;
state this explicitly. Observation alerts use >= and remain unchanged.

Members: equal-weight empirical fraction > threshold, using all finite members;
reject empty/nonfinite values and duplicate labels rather than silently dropping.
Quantiles: validate >=2 unique strictly increasing probabilities in [0,1], finite
nondecreasing values. Never count quantiles as members. Between distinct knots,
interpolate the CDF linearly and label the estimate as interpolation. For a tied
value at the threshold, strict exceedance uses the rightmost probability at that
value, provided there is a greater-value knot to the right; this corresponds to a
flat segment of the piecewise-linear inverse CDF (an atom). At/above the last
value, return [0, 1-p_last] with no point estimate unless p_last=1. Below the first
value return [1-p_first, 1], with no point estimate unless p_first=0. At the first
value use rightmost equal knot when followed by a greater knot; no artificial
endpoint mass or tail extension. All-equal quantiles follow the same tail rule.
Bounds describe the supplied information; interpolated point values are an
explicit approximation. Missing/invalid input returns unavailable, not zero.

Wire this helper into existing `review-series` processing after current QC and
publication/scoping selection. Fetch station thresholds once through the existing
station store, filter parameter and validate matching units (canonical discharge
m3/s). Snapshot threshold identity/value/source once per response, not repeated
per point; point results reference that snapshot by stable level key. Existing
source/point IDs supply model/artifact/run/issue/valid-time provenance. No values
or probability results for missing, rejected, withheld or unavailable points.
Explicit empty snapshot means thresholds not configured. Preserve existing 2MiB
response cap and source/record bounds. State current-threshold retrospective use;
unknown temporal support remains unknown, never presented as instantaneous
flood/peak risk. CMAL daily-mean meaning requires recorded model output support,
not inference from time_step or a model-name special case. No new future-forecast
endpoint in A; the shared helper is reusable there in a later bounded addition.

Tests: unequal quantile spacing; interior interpolation; repeated values and exact
threshold equality; endpoint/tail/all-equal cases; invalid and nonfinite input;
member parity; threshold parameter/unit validation; missing thresholds; scoped and
publication-gated API; rejected values expose no probabilities; contract drift;
response size. Update API contract and touchpoint/operator documentation.

### Review resolutions (A)

Independent Claude review completed 25 November; report kept locally at
`/tmp/threshold-probability-plan-review.txt`. Incorporated these clarifications;
A READY under delegated orchestration authority. B and C remain gated below.

For interior distinct knots i,j, bounds are [1-p_j,1-p_i]. For equality at a
rightmost tied knot r followed by greater knot j, bounds are [1-p_j,1-p_r]
and the point is 1-p_r under the labelled piecewise-linear inverse-CDF assumption.
This includes the first value and p_first=0; it is not an exact probability.
At/above the final value use tail bounds; all-equal values use lower-tail bounds
strictly below the value and upper-tail bounds at/above it. A collapsed bound is
an exact result; other tail results have no point. Compare stored floats directly,
without epsilon. Reject nonfinite thresholds at the boundary.

Discharge thresholds are canonical m3/s by import contract, without a unit field.
Check forecast source units against canonical aliases; unsupported units yield
unavailable probabilities. Return all source classifications with a key encoding
(parameter, level, source), not level alone; snapshot value and updated_at.
Fetch thresholds only after station scope/tenant checks for the same station ID;
validate their station identity in the service too. Both modes share this rule.
RAW/QC_UNCHECKED AVAILABLE points retain qc_unchecked at point level and may have
probabilities; rejected/missing/unavailable points have an empty collection.
Keep temporal_support=unknown and say these are forecast-value exceedances,
not peaks or flood events. Reject oversized responses without truncation.
A changes no skill computation version. Member counts and quantile support
metadata may follow; method and bounds suffice for A.

## B: probability-based verification (after A)

Extract reusable probability-input metric cores; member wrappers retain behavior.
Use the same converter for quantile and member samples. Filter paired samples by
available point probability per threshold; preserve all counts. Add Brier score,
BSS, POD/FAR/CSI, reliability and ROC, keeping rank histograms member-only. Use
strict > observed events and the existing documented 0.5 decision probability.
BSS reference is the event rate of the same scored subset (in-sample climatology),
not an independent climatology forecast; zero reference variance is undefined.
Return undefined for zero metric denominators; no eligible samples means no score.

Before B implementation, detail/review the smallest durable metadata addition:
threshold value/source/snapshot identity, probability method, selected/excluded
sample and event counts, conditional-subset label, decision probability, temporal
statistic, and current-threshold retrospective caveat must survive storage and API
readback. Do not squeeze this into metric names or misuse sample_size. Reuse skill
generations and existing publication transaction; preserve old results. Increment
computation_version for changed scores. Do not rank models with different paired
subsets. Existing admission, data-use isolation and scope checks stay intact.
B is a separate bounded patch, not permission for an unreviewed schema migration.

## C: evaluation (after B is deployed)

Verify replacement donor/candidate hindcast run coverage first. Snapshot thresholds
and score the exact run IDs using corrected observations. No retraining, artifact
activation, old-result deletion, new warning decisions or expensive page-triggered
work. Report station/lead/period, coverage and event counts, missing thresholds,
conditional-subset limitations, forcing and training/availability caveats. Keep
acceptance separate from operational activation.

## Gates and handoff

A is the immediate implementation unit. One independent Claude plan review before
READY and one independent patch review before owner merge, per owner-supplied
instructions. Full CI after final code change. B's metadata detail gets its own
bounded review before implementation. No production deploy without owner approval.

```json
{"steps":[{"id":"A","depends_on":[]},{"id":"B","depends_on":["A","B_metadata_review"]},{"id":"C","depends_on":["B_deployed","replacement_hindcasts_verified"]}]}
```

### A implementation evidence

Independent Claude patch review found no numerical blocker; requested route-level
scope/publication/size tests and contract verification. Added those tests, including
366 daily points with four thresholds under 2 MiB and 8,784 hourly points rejected
at the existing cap. Contract regeneration initially exposed the missing example
fields; corrected the example and passed the drift/example tests. 162 targeted
probability, review, contract and existing quantile-skill tests pass. Changed
production modules pass Pyright; Ruff passes. Full CI remains a pre-merge gate.
No skill output, alert behavior, or live deployment has changed in A.
