# Quantile-aware skill before candidate acceptance

status: READY

Independent Claude review completed November 24. Orchestrator disposition:
address findings through the concrete rules below. Full report remains local at
`/tmp/quantile-skill-plan-claude-review.txt`. This is plan readiness, not patch
approval, production authorization or acceptance of any model.

Owner-approved order: bounded Swiss input investigation, this correction,
BAFU threshold onboarding (#630), then the administrator review slice (#454).
Related lifecycle: #538. Production deployment and candidate activation remain
owner decisions. No Aquacast or ForecastInterface changes.

## Evidence and scope

`services/skill/service.py::_ensemble_matrix` discards quantile probabilities
and treats quantile values as equally likely ensemble members. This changes
CRPS, spread, exceedance-based scores and diagrams. Taking the median of the
values is also incorrect for a general, uneven quantile grid. Existing candidate
and donor hindcasts can be rescored; training need not be repeated.

The bounded investigation found the November 24 00 UTC CMAL-small cycle
completed with 161 of 173 stations. Nine exclusions have nonconsecutive daily
discharge after QC filtering: 2206, 2251, 2252, 2283, 2327, 2356, 2488, 2630,
2640. Three have no discharge rows in the preceding 30 days: 2462, 2486, 2602.
Some excluded stations have substantial suspect data (2206: 4,019 suspect rows,
17 unchecked rows across only two days). Do not bypass QC or interpolate these
inputs as part of skill work. Track input readiness under #548.

Live logs explicitly report `min_age_minutes=210`, age 0.1 minutes and
`fallback_reason=too_recent`: selecting the preceding 18 UTC weather cycle is
intentional publication policy. Weather download took about six minutes and
extraction about thirty minutes; optimization stays under #618. Neither finding
requires a scheduling or publication-policy change in this patch.

## Milestones

### Q1 — Preserve distribution semantics

Read the training/hindcast/skill touchpoint map, WMO standard, existing metric
implementations and all consumers before editing. Carry representation and
quantile probabilities through stratification in a small frozen domain value;
preserve the current member-based calculation path. Each sample carries its own
sorted, finite, strictly increasing probabilities and non-crossing finite values.
Reject mixed representations or probability grids within a computation rather
than truncating/alignment by position. Reuse existing numerical
helpers where their inputs genuinely match. No synthetic equal-weight members.

For quantiles, calculate median and interval widths from the supplied probability
grid, linearly interpolating only within its supported range. Do not publish an
ensemble range or member rank histogram for quantile output. Missing required
quantile coverage means unavailable, not zero.

A finite quantile grid does not identify the tails. Use the metric
`quantile_score_trapz_p05_p95`: twice the trapezoidal integral of pinball loss
over probability [0.05, 0.95], divided by 0.9. Include internal supplied knots;
interpolate endpoint quantiles within support when needed. This is a finite-grid
quantile score in discharge units, not full CRPS. Retain member CRPS. Comparisons
require the same metric, grid and observation pairs. Do not use the new score
for CRPSS or BMA. Reference for the underlying pinball definition:
https://scoringrules.readthedocs.io/en/latest/generated/scoringrules.crps_quantile.html
(the named metric here uses trapezoidal probability weighting, not that library's
equal average over knots).

For this correction, omit all quantile threshold scores/diagrams rather than
retaining false pseudo-member results. Member-based threshold metrics remain.
Implement the agreed per-time probability product during the subsequent
threshold milestone, with explicit interpolation, ties, tail availability and
per-metric eligible sample counts. No live alert decisions change here.

### Q2 — Consumers, versioning and verification

Audit ordinary stored skill, period skill, combined skill and fine-tuning
candidate/donor comparisons. Prevent old pseudo-member CRPS from being used as
fallback when the corrected quantile metric is unavailable or renamed. Preserve
tenant, artifact, hindcast run, forcing, lead, evaluation period and generation
boundaries. Do not introduce a new evaluation framework. BMA must not use
pre-correction probabilistic scores or map the new score to CRPS; missing eligible
scores follow the established no-weight behavior. Quantile models configured
with a CRPS gate fail closed until the owner deliberately changes that gate.
Existing fine-tuning reports already warn that common samples are not verified:
retain that caveat and do not emit a superiority or acceptance verdict. Paired
comparison is part of the administrator slice, not automatic in this patch.

Bump the computation version from 2 so corrected writes cannot collide with
old natural keys. Preserve historical records and ensure readers expose the
current complete generation. Document which old results are superseded and
which acceptance comparisons must be recomputed. Member writes also use version
3, with unchanged numerical results. Test mixed-version reader behavior and
guard generation-level replacement against resurrecting removed metric names.

Behavior tests: unequal quantile spacing; supplied q50 differs from the median
of knot values; known interval widths; interpolation and ties; no invented
tails; malformed/nonfinite/crossed inputs; unchanged member outputs; missing
metrics never zero; fresh generation selected after recomputation. Exercise a
small stored-hindcast recomputation end to end. Run lint, affected types/tests,
then full suite locally or CI after the final code change. Independent patch
review and owner merge remain required.

### Q3 — Recompute and hand off

Prepare a bounded, resumable recomputation using existing stored candidate and
donor hindcasts and the existing skill generation machinery. Wait for the
current donor evaluation to finish. Preview artifact/run/station counts and
resource limits before any production write; obtain deployment approval for the
corrected release. Publish coverage and comparable metrics, not an automatic
acceptance verdict. Leave the candidate inactive.

## Subsequent work (separate plans and PRs)

Then proceed to #630 using authoritative BAFU values and existing threshold
storage. Verify units and temporal meaning before comparing daily means to
danger levels. Finally implement the smallest administrator review slice under
#454: candidate evidence, recorded acceptance/rejection, separate activation.
The existing approval action currently promotes the artifact and must not be
presented as acceptance-only.

## Deferred

Calibration, extrapolated tail distributions, event-window probabilities,
automatic model rankings/activation, dashboard redesign, weather optimization,
QC policy changes and model dependency upgrades.

```json
{"Q1": [], "Q2": ["Q1"], "Q3": ["Q2"], "thresholds_630": ["Q3"], "admin_review_454": ["thresholds_630"]}
```
