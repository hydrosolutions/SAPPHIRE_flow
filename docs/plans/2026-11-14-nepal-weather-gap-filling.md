---
status: READY
created: 2026-11-14
owner: orchestrator
scope: Shared model-aware short weather-gap preparation and Nepal retained-weather recovery
---

# Produce Nepal test forecasts with explicitly estimated weather gaps

## Owner policy

CMAL needs complete weather arrays. Fill usable weather gaps before prediction
instead of rejecting a forecast solely because a weather day is missing.
Use linear interpolation for temperature and radiation. The owner approved stored same-day forecast substitution first, then adjacent-day
mean precipitation when no same-day substitute exists. Apply the same filling method to old and recent gaps.

A filled gap at least five days old must not itself produce an input-degradation
warning. A more recent filled gap must produce a forecast warning explicitly
stating that weather data was missing and estimated. Preserve a record of every
fill, including older fills, and preserve independent quality warnings.

Proposed exact daily boundary: compare the missing UTC model-day label with the
forecast issue-day label. D-5 and earlier are old; D-4 through D-1 are recent.
The result must not change simply because the same issue day is retried later.
Five days is the owner's warning policy, not a claim that older precipitation
errors have no hydrological effect.

## Findings and constraints

- Current source base inspected: `d7c24c62d` (main, 2026-11-14).
- The selected operational route consumes daily precipitation and temperature.
  It does not currently deliver radiation, and CMAL small does not need radiation.
  Record the agreed radiation policy without expanding the source contract merely
  to support an unused variable. A radiation-consuming model needs its own wired
  source path before that part can be claimed implemented.
- `adapters/recap_operational.py` parses identified source samples and calls
  `preprocessing/recap_operational_weather.py::reduce_daily_window`.
  `OperationalWeatherDay` currently requires complete native support for both
  variables; `OperationalWeatherWindow` requires a contiguous daily window.
- `services/operational_weather_projection.py` then requires a verified cached
  snapshot covering the exact requested past window. `selected_weather_inputs.py`
  constructs the model dataframe. Filling only that dataframe is too late when
  incomplete source data has already been refused.
- The existing morning probe at 2026-11-14 00:30 UTC reported
  `source_data_missing` for both variable requests. A wholly unavailable response
  is different from holes in an otherwise usable response. Repairing only returned
  interior gaps will not by itself establish the 07:00 delivery criterion.
- Read-only coverage inspection found already stored `ifs_ecmwf` precipitation
  and temperature for 13 November for all 17 stations: eight distinct three-hourly
  timestamps, including the 12 November 00 UTC cycle stored on 12 November.
  These are candidate same-day substitutes, not yet a completed per-station
  source/support qualification. Prefer investigating this existing input over
  adding a donor service. This evidence changes the initial recommendation to
  include the recent-tail case using retained history plus qualified stored IFS.
- Existing cache snapshots and weather evidence are hashed and revalidated.
  Estimated values cannot be presented as genuine ERA5/IFS source samples, nor
  may new serialization silently invalidate existing snapshot hashes.
- FI protocol, requirement/result types and model/input documentation were read.
  Keep CMAL and its declared NaN/horizon requirements unchanged. Supply finite
  prepared inputs through the existing FI boundary; do not bypass admission gates.

## Approved decisions and latest scope (2026-11-14)

The owner approved both proposed methods and requested pragmatic independent review
followed by implementation. Filling must be shared, globally configurable or
model-specific, not hard-coded to CMAL/Nepal. Long gaps stay missing; new simpler
regression/fallback models are explicitly deferred.

Implementation defaults: daily inputs, at most one consecutive missing day;
`max_gap_days` is configurable. A common policy supports `strict_models`, `always`
and `disabled`, with per-model overrides. `strict_models` applies only to weather
features for which the model declares zero missing-value tolerance. Do not infer
zero tolerance for an undeclared non-FI model. Apply per assignment, never mutate a
shared station frame that feeds different models. Initial shared variables are
precipitation, temperature and global radiation. The Nepal source route remains
P/T-only; do not invent a radiation source.

Prefer a complete qualified stored forecast for the same day and basin before
linear interpolation for an interior gap. A one-day rain gap then receives the
mean of its two original complete neighbors. Never use filled neighbors to bridge
a longer hole. Bound the original missing run before substitution so successive
fills cannot evade the limit. No endpoint extrapolation, implicit zero rain,
synthetic whole histories, or future-cycle lookahead. Keep authentic partial-day
samples; an incomplete aggregate is missing, not a complete daily total. Where a
whole-day estimate would contradict a known partial precipitation total, leave it
unfilled rather than invent a rescaling rule.

## Minimal implementation direction, subject to those decisions

1. Put a shared policy at the operational daily weather preparation boundary,
   resolved per model. Support both existing standard input assembly and the
   selected Nepal route. Validate source identity, units, duplicates, timestamps and
   interval semantics before considering a value fill. Identity errors remain
   errors; a missing value is not an identity error.
2. Preserve authentic source support and separately represent estimated daily
   values with method, affected dates/variables and the actual supporting values
   and source times. Reuse existing weather evidence/cache storage. Keep old
   snapshots readable with unchanged hashes; do not fabricate source provenance.
3. Feed the complete result to the existing input assembly and FI validation.
   Add the recent-gap message through `selected_weather_quality_flags`; old fills
   remain recorded without adding a degraded flag. Do not suppress unrelated
   observation, source-age or refresh-failure warnings under this policy.
4. If the upstream request returns no usable series, use a separately specified,
   bounded retained-data path. If that path cannot be justified from available
   data, report the precise missing support; do not claim an interpolation patch
   alone fixes morning availability. Coordinate any provider contract change with
   the owner, drafting rather than sending messages.

## Verification and phases

- Complete inputs remain numerically unchanged.
- A qualifying isolated gap produces a finite input and forecast; temperature
  interpolation and the approved precipitation calculation match explicit values.
- D-5 has no gap-degradation flag; D-4 and newer do. Mixed gaps retain the recent
  warning, and independent warnings survive. Both old and recent fills are recorded.
- Nonnegative rainfall, zero rainfall as valid data, partial-day support, boundary
  gaps, consecutive gaps and a wholly missing response receive explicit tests.
- Source identity/duplicate/interval errors still fail, and estimated values cannot
  masquerade as source samples. Snapshot round trips preserve legacy hashes and
  the actual as-used evidence. No post-issue source cycles enter a historical run.
- Exercise the real selected-weather path through FI and persisted forecast
  quality/evidence; include mixed ordinary/rating-derived station forecasts.
- Independent Claude plan review precedes orchestrator READY; one independent
  patch review, Ruff, relevant tests and full CI precede owner merge. Live rollout
  and 07:00 qualification are later steps, not claimed by local tests.

```json
{"phases":[{"id":"policy-and-data-check","depends_on":[]},{"id":"independent-plan-review","depends_on":["policy-and-data-check"]},{"id":"implementation","depends_on":["independent-plan-review"]},{"id":"validation-and-patch-review","depends_on":["implementation"]},{"id":"owner-merge-and-live-qualification","depends_on":["validation-and-patch-review"]}]}
```

## Method references

Daily precipitation imputation is conditional on available contemporaneous donor
data and local rainfall behavior. Published comparisons use neighboring-station
regression/normal-ratio methods; they do not establish temporal adjacent-day
averaging as best for Nepal basin rainfall. For this MVP, the latter would be an
explicitly accepted simple fallback, with masked-day evaluation useful before
extending it beyond isolated gaps.

- [Wangwongchai et al., 2023: daily rainfall imputation comparison](https://pmc.ncbi.nlm.nih.gov/articles/PMC10654590/)
- [EMO-5: daily gridded meteorological estimates and interpolation uncertainty](https://essd.copernicus.org/articles/14/3249/2022/essd-14-3249-2022.html)

Independent Claude plan review completed: approve with minimal changes. The
orchestrator sets READY after incorporating the corrections below. Implementation
is authorized; owner PR merge remains required. No server activation yet.

## Independent plan review corrections incorporated

- Resolve each per-model override first, then the global field, then defaults.
  `always` fills declared supported weather features; `disabled` fills none.
- Rainfall temporal interpolation is only for one-day gaps even if the configurable
  maximum is larger. Longer permitted gaps require same-day donor support; otherwise
  they remain missing. Temperature/radiation interpolate up to the configured bound.
- Donor aggregation must match the exact UTC day, units and accumulation convention.
  Exclude source cycles after issue time and require complete single-cycle support.
- One common helper returns prepared values and typed fill evidence together. Keep
  old serialized fields/hashes stable and add optional evidence only where needed.
- Review assessed a generic design packet, not live source data. The full report
  stays local.

## Implementation review

Independent Claude patch review identified one compatibility issue: the selected
route must still refuse unfilled weather for native models without an explicit
missing-value contract. The assembler now preserves that refusal after attempting
authorized fills; FI models retain their own declared NaN gate. Regression tests
cover disabled/default native policies and a tail exceeding the fill bound.
Policy resolution lives in the selected assembler to keep the existing forecast
flow within its type checker's complexity limit. No model contract was changed.
The existing optional next-day buffer still requires complete support; missing
required historical days alone enter the new fill policy.
Missing numeric samples still validate their native source grid before omission.
