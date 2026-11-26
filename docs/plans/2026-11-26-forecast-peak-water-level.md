# Forecast P50/P90 peak water-level equivalents

status: READY

Owner: forecast-peak API agent, coordinated by the Nepal orchestrator.
Branch: `feat/forecast-peak-water-level`.
Base: `550d231e` at planning; rebased onto `e67b99e1` before PR (26 November).

## Outcome and scope

Reviewers need to compare a forecast's median and upper range with the warning
and danger water levels shown by the dashboard. The owner selected P90 as the
upper estimate. Add an explicitly requested provisional comparison to the
existing forecast-detail route, for any admitted model. Do not alter forecasts,
observations, thresholds, model execution, publication, scheduling or deployment.

The original request permits discharge-equivalent thresholds as an alternative.
However, the current Nepal overview has empty threshold lists at all 21 stations;
that alternative alone would not provide the requested display. Use the original
inverse-conversion request instead. No threshold import or configuration change
belongs to this patch. The dashboard continues to own its existing threshold
display; it must match the returned unit/reference before comparing numbers.

## Existing behavior and reuse

- `services/rating_conversion.py::RatingConverter.from_curve` validates and sorts
  a monotone stage/discharge table. Linear and log-linear interpolation are
  supported. The forward method clamps out-of-range inputs and reports the range;
  do not alter that contract. There is no inverse implementation to reuse.
- A valid forward curve may contain discharge plateaus. Only a requested
  discharge lying on such a plateau has a non-unique inverse; do not reject the
  entire curve when another discharge can be uniquely inverted.
- `DeploymentConfig.provisional_ratings` already carries explicit tenant,
  station, model and immutable curve/reference bindings. The API loads this
  config already for station-history reads. References are either verified or
  explicitly owner-assumed same-zero. Reuse that evidence; do not infer a datum
  from station coordinates, curve magnitudes, timestamps or a model name.
- `derive_rating_sample` permits the explicitly configured expired curve on the
  provisional route and retains expiry warnings. Reuse that provisional policy
  for this opt-in review comparison. Do not make expired curves generally valid.
- `rating_curves` is an ordinary API-readable table. Its protected delivery and
  reference tables remain inaccessible. Compare the ordinary curve row to the
  exact configured immutable snapshot; no new grants or migration are required.
- Existing forecast-detail admission handles station scope, parent data-use,
  publication selection and withdrawal. Add enrichment only after that admission.
- `reduce_forecast_point` provides existing validated pointwise P50 behavior.
  Preserve existing overview and event-review reductions. Reuse type-7 empirical
  quantile behavior for member P90 and the existing five-member band minimum.

## Statistical and temporal meaning

Compute two independent maxima: the maximum of the pointwise P50 series, and the
maximum of the pointwise P90 series. Their times may differ. This is not P50/P90
of member-wise trajectory maxima and is not a window exceedance probability.

For stored quantiles use exact float equality for 0.5 and 0.9 levels (consistent
with existing stored percentile identity); 0.9000000001 does not count as 0.9; missing P90 is explicitly
unavailable, not a substituted maximum or interpolated tail. For members use the
median and empirical type-7 P90; fewer than five members leaves P90 unavailable.
Retain the method and member count. P50 and P90 availability are independent.
Validate the complete ordered quantile sequence, including P90 against P50 and
all lower quantiles, rather than relying on the existing P25/P75 reducer.
Member type-7 quantiles are calculated in discharge space before inversion; this
is not type-7 interpolation of transformed members on nonlinear curves. Duplicate/nonfinite coordinates or values,
negative discharge, and crossing quantiles refuse the affected statistic rather
than dropping bad timesteps and reporting an underestimated peak. A missing
required percentile at any eligible timestep makes that statistic unavailable.

The comparison covers remaining stored forecast support at one injected `as_of`
instant. For declared periods, include periods whose physical end is after
`as_of`; expose each peak's original valid label, physical bounds and aggregation.
For undeclared legacy support include labels at or after `as_of`, expose unknown
support and no invented physical interval. Empty remaining support is expired.
Partially elapsed declared periods remain included. Tied maxima select the
earliest original label deterministically. Name the entries `remaining_peaks`
and echo `as_of`: this wall-clock-dependent subset can omit an earlier forecast
maximum. Statistical metadata and schema wording say “maximum of pointwise
quantiles” / “upper envelope”; never imply a percentile of trajectory maxima or
a 90% probability that the level stays below the P90 equivalent.

For a daily mean discharge forecast, inversion produces the **water-level
equivalent of that discharge**, not a forecast daily mean water level, an
instantaneous flood peak, or a threshold crossing within the day. State this in
the API contract and frontend handoff. Preserve the parent forecast's QC status,
input-quality and provisional labels. Failed/suspect/missing forecast QC refuses
numeric comparison; raw/unchecked remains explicitly labelled as on the parent.

## Rating conversion and selection

Add a pure inverse method alongside the existing forward converter. Return a
frozen typed result with availability/range status and nullable water level.
Interior linear inversion uses the same segment as the forward interpolation;
log-linear inversion solves the same log-discharge relation. Exact unique endpoints are
in range. Exact float equality to a plateau discharge is ambiguous, including
minimum/maximum plateaus. No tolerance merges distinct discharges. Zero/negative
Q below a positive log-linear domain returns BELOW before taking a logarithm. Values below/above
the tabulated discharge domain return a flag and no water-level number: no
clamping, extrapolation, artificial endpoint estimate or model-specific branch.
Reject nonfinite arithmetic with the existing meaningful conversion error.
The provisional API comparison admits only LINEAR curves with at least two
points and nonnegative discharge, matching `convert_provisional_level`;
LOG_LINEAR returns `unsupported_interpolation`. The reusable pure inverse still
supports both interpolation methods. Forward provisional extrapolation and
non-extrapolating display inversion are intentionally asymmetric.

The API reader selects exactly one matching configured tenant/station/model
reference. Validate station code, reference identity, units, effective reference
time, and equality of the currently read curve with the canonical configured
snapshot using exactly
`curve_snapshot(curve).content == assumed_curve_content(reference.curve.content)`.
The ordinary store mapping returns every snapshot field, including delivery_id,
rating_type_label, created_at and uploaded_by; verify with a real row-mapping
test. Reference verified_at/binding_at must be <= as_of. A changed/missing bound curve, multiple matching bindings or unsupported
reference refuses conversion; never fall back to another version or delivery.

Map inverse curve-native stage into the configured measurement frame once:
subtract the verified `offset_m`; for owner-assumed same-zero use zero and retain
that basis explicitly. The returned frame is `level_reference`, never
`curve_reference`: label `masl` as m a.s.l. and `gauge_zero` as m above gauge zero.
An assumed-zero result has `reference=null` plus basis/warning; no datum claim.
The frontend must match its threshold unit/reference against the returned
`water_level_unit` and `water_level_reference`; assumed-zero results cannot be
compared to an explicit-datum threshold. Matching live threshold metadata cannot
be tested from the current empty backend lists. Missing
or incompatible unit/reference metadata leaves numeric output unavailable.

Expose curve id, version, valid_from, valid_to, interpolation, tabulated level and
discharge ranges, reference basis and comparison-time validity. Each peak also
reports whether its declared physical support crosses/outlives curve validity
(for unknown support, validity only at its label). Expired use stays provisional
and visibly warned. `valid_from > as_of` means not-yet-valid and refuses all
numeric conversion. `valid_to` is exclusive: as_of >= valid_to is expired. A
peak period beginning before valid_from reports `begins_before_validity` and
refuses that peak conversion. It crosses the expiry boundary when
start < valid_to < end; start >= valid_to is expired, and end == valid_to is
fully within validity. Unknown support uses point-in-time validity at its label.
Expiry at the peak is permitted only by the explicit provisional policy and
retains its warning. This comparison is
read-time context from an explicitly configured curve, **not** a claim that the
model predicted water level or used this curve for output conversion at issuance.

When a binding is absent, report `reference_not_configured`. Ordinary curve
metadata may distinguish `curve_missing`, `curve_expired`, `curve_ambiguous` and
`reference_not_configured`; no numeric conversion follows unbound selection.
Return only whitelisted metadata, never curve points, actor names, endpoints,
private evidence text, tokens, configuration documents or protected lineage.

## Request and response contract

Extend `GET /api/v1/forecasts/{id}` with optional
`rating_comparison=provisional_rating`. Absence preserves current behavior.
Only reviewer/admin roles may opt in, checked before any parent forecast read
(403 independently of existence and tenant scope), not just enrichment reads.
Parent `data_use` continues to select the forecast itself; the comparison is a
separately labelled provisional view, including when the parent is standard.

Examples:

```
GET /api/v1/forecasts/{id}?data_use=standard&rating_comparison=provisional_rating
GET /api/v1/forecasts/{id}?data_use=provisional_rating&rating_comparison=provisional_rating
```

Add nullable `peak_water_level_comparison` to both detail schemas. When requested,
it contains `data_use=provisional_rating`, `as_of`, explicit statistical/window
definitions, parent period metadata, safe curve/reference metadata and two
`remaining_peaks` entries identified by `quantile` (0.5 and 0.9). Each entry contains its discharge
value/unit, original valid time and nullable support bounds, reduction method,
nullable water-level equivalent/unit/reference, conversion status and warnings.
Availability/reason is explicit for no curve, missing reference, invalid/missing
percentiles, expired forecast, bad QC, ambiguous inverse and out-of-range peaks.
Discharge peaks remain useful when conversion is unavailable.

No overview enrichment or additional per-station dashboard polling endpoint in
this slice. The frontend can use its existing admitted detail reads. No new
default access to provisional evidence; denied/out-of-scope/withdrawn forecasts
must return existing responses without querying curves or exposing comparison.
One composition helper enriches each admitted detail return: no publication gate,
inactive tenant gate, active published parent, and admitted provisional parent.
404 and 410 payloads carry no block; responses retain no-store caching.

## Phases and touchpoints

1. **P1 — reviewed contract.** Confirm the above policy/shape and conduct one
   independent Claude plan review covering numerical meaning, permission scope
   and performance. Root orchestrator alone sets READY after disposition.
2. **P2 — pure calculation.** Extend `services/rating_conversion.py`; add small
   frozen comparison types and pure forecast peak/comparison service. Tests use
   independent literal answers, nonlinear curves and differing peak times.
3. **P3 — API composition.** Use request-scoped ordinary curve/store and existing
   config/principal; add opt-in detail composition and Pydantic response types.
   Preserve all existing response/admission branches and no-store behavior.
4. **P4 — validation and handoff.** Update API spec, touchpoint map, generated map
   OpenAPI/minor version and focused fixtures. Run focused and full validation,
   one independent patch review, then open a reviewable PR. No merge/deploy.

```json
{"phases":[{"id":"P1","depends_on":[]},{"id":"P2","depends_on":["P1"]},{"id":"P3","depends_on":["P2"]},{"id":"P4","depends_on":["P2","P3"]}]}
```

Expected files: rating conversion/service/types, API comparison adapter/schemas,
`api/routes/api_forecasts.py`, `api/deps.py` if store wiring is needed, existing
map contract/spec/docs and matching tests. No hindcast, skill, model/FI, dependency,
database schema, threshold-admission or host files. Parent owns hindcast changes.

## Required acceptance

- Linear/log-linear interior and endpoint round trips; plateau ambiguity only at
  tied Q; below/above range returns no height; invalid and nonfinite inputs refuse.
- Independent P50/P90 maxima/ties, exact stored quantiles, member type-7, insufficient
  members, missing/crossing quantiles and contaminated timesteps.
- Daily START/END, another phase, expired/future support and unknown legacy support;
  aggregated discharge is never labelled mean stage or within-day maximum.
- Verified nonzero offset applied once; assumed same-zero labelled; missing,
  changed, multiple and future bindings; expired curve and validity crossing.
- Standard and provisional parent routes, explicit reviewer opt-in, consumer403,
  default-null field, denied404/withdrawn410 before any curve read; no private
  fields in response, safe error logging, no stored forecast mutation.
- One bounded curve read per requested detail; no raw observation/protected-history
  scan, no new overview work, representative maximum admitted forecast detail
  remains within the existing performance budget.
- Ruff and changed-module type gates, focused route/conversion/contract tests,
  independent patch review and full suite after final change locally or in CI.
- Explicit fixtures for first/middle/last plateaus, near-domain Q, nonlinear pure
  inverse and API unsupported log-linear, member trajectory-max contrast, P90
  below P50/lower quantiles and float-jitter level, all reference frames/offsets,
  real store mapping/snapshot mutations, exact time boundaries and expiry-crossing
  periods, every publication branch, schema whitelist, and one query at maximum
  admitted detail size.

Operational delivery remains a later owner-approved release. Repository tests do
not establish which live bindings are present or whether a particular curve is
scientifically current. Report live prerequisites separately from implementation.

## Independent plan review disposition

One Claude Sonnet review completed 26 November (local report, not committed).
Its five major findings are incorporated above: provisional interpolation policy,
measurement-frame labelling, complete snapshot/reference checks, explicit temporal
boundaries, and full quantile ordering/identity. Medium findings are covered by
route composition, exact plateau handling and the acceptance matrix. No extra
review loop is proposed. Root orchestrator decides readiness.

Orchestrator readiness, 26 November: the independent review is complete and all
five major findings and the listed medium concerns are addressed in the revised
contract and required tests. Implementation may proceed; patch review and owner
PR approval/merge remain outstanding.
