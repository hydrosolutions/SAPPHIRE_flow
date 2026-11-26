# Generic declared-grid hindcasts and verification

status: READY

Owner: Nepal/model-onboarding session.
Branch: `feat/declared-grid-hindcast`.
Base: `11540abe` (remote main refreshed 26 November; includes #670 runoff inputs,
the latest skill API changes, #683 threshold probabilities, #688 retained forecasts
and #689 qualification documentation).

Review outcome, 26 November: **NOT READY** for the earlier full-feed proposal.
The owner subsequently directed proceeding without the Aquacast optimization and
approved explicitly labelled retrospective availability at physical closure when
publication time is unknown. The amendment below replaces that performance gate;
correctness and review gates remain. No deployed speedup is claimed.

Amendment review: independent Claude review returned **READY**, with four
additional acceptance requirements incorporated below. The orchestrator set READY
after recording those requirements. The full review is local, not committed.

## Coordination boundary

This session owns period correctness in station/group hindcasting, batched FI
inputs/outputs, retained hindcast period identity, and observation/skill pairing.
The other hindcast session owns historical-input preparation optimization. No
independent implementation may overlap these touchpoints. Share this scope and coordinate
any shared helper signature or `hindcast.py` change before the other session edits
it. Do not bundle caching, bulk-read tuning, parallelism, batch-size tuning, CPU
dependency changes, or full-period memory optimization into this correctness work.
Owner clarification on 26 November: prepare both observations and forcing once
over the full requested run span. The later instruction to proceed without the
wrapper fix permits bounded delivery views from that shared preparation: no
repeated reads or daily aggregation and no model-specific execution bypass.
The shared forecast-performance plan (#678) consumes the resulting stored scores;
this slice owns period qualification and score identity, not a second metric engine
or the dashboard's performance presentation.

## Required behavior

- Reuse `TimeGrid`, `ModelTimeGrid`, `PeriodLabel`, `ForecastPeriodDefinition`,
  existing boundary codecs, aggregation policies and physical-window helpers.
  Do not add CMAL-, Nepal-, or 18:00-specific branches.
- Preserve the issue instant independently of physical period bounds and labels.
  For CMAL SE, observed discharge, weather and verification represent identical
  18:00–18:00 UTC intervals, labelled at their declared start. Test another phase
  and end labels to prove this is generic.
- Establish source interval/label semantics before aggregation. Already-aggregated
  midnight daily values cannot be relabelled into 18:00 daily means or totals.
  Missing source semantics or insufficient resolution must remain explicit refusals.
- Admit only complete eligible input periods; preserve actual gaps and QC/purpose
  isolation. Unknown availability stays unknown under the default recorded policy.
  The explicitly selected retrospective policy may assume physical closure for
  archived discharge, with the assumption retained on runs and scores. Known later
  availability takes precedence. Never infer training provenance.
- Keep existing rejection until assembly, prediction, storage and scoring are
  supported and tested together. Unsupported combinations continue to refuse.
- Retain legacy no-declaration behavior and the model-declared lookback fix.

## Phases

### P1 — contract and source audit

Read the pinned FI protocol, requirement/result types and model/input docs before
changing the adapter. Trace live period-aware assembly and historical observation
and forcing readers, including native resolution, aggregation, stamp convention,
availability and boundary closure. Identify reusable helpers and any genuine
missing source evidence. Resolve gaps explicitly; do not remove guards to bypass
them. Inventory downstream skill/event-review readers of retained hindcasts.

### P2 — input/output period correctness, including batching

Derive the full run's required history/forecast span from the model declaration.
Aggregate observations and forcing once per station, variable and period contract
over that span. Reuse common period aggregation/qualification and extend it only
where necessary for verification. Project each issue's required physical windows
from the shared preparation into the existing `StationModelInputs` and
`GroupModelInputs` types. Reuse the existing batch adapter's bounded union and
eligibility cohorts; no new full-feed FI API is needed for this MVP. Per-issue
qualification and FI conversion may inspect/project these views but must not
repeat source reads or aggregation. Preserve labels and availability across the
FI boundary for the model to apply independently at each issue. The current
Aquacast version is supported through this bounded path; its full-feed optimization
is no longer an implementation dependency.
Validate output periods and group-grid compatibility. Preserve batch capability
negotiation, size-one fallback, final partial batches and member failure handling.
Do not promise bitwise equality of stochastic quantiles across batch sizes.

### P3 — retain identity and score matching periods

Use the existing period-definition type/codec for hindcast store round trips;
add an additive nullable migration only where the current schema cannot retain
it. Do not reinterpret old rows. Scope migration numbering against latest main.
Pair verification observations by physical interval and aggregation, rather than
timestamp coincidence. Partition/reject incompatible period definitions including
label and aggregation, not merely step/phase. Include required identity in score
generation/fingerprints and read contracts so legacy and declared-period results
cannot collide. Preserve elapsed lead meaning and document/test it independently
of start/end labels. Review observation fetch bounds, seasons, threshold metrics,
diagrams and consumer labels as part of the same contract.

### P4 — validation and handoff

Test independent literal expected periods/values for 18:00 daily, another phase,
start/end labels, on/off-boundary issues, leap/month transitions, incomplete edges,
missing samples, incompatible sources/groups and unavailable observations. Verify
single-issue versus batch input/output period equivalence and no future-target
leakage, including partial batches and per-member failures. Test store round trips,
legacy rows, migrations, score identity and ordinary/provisional data isolation.
Run the relevant existing FI, hindcast, skill and API regressions, then full CI
after the final code change. Update affected specs, touchpoint map and runbooks.

One independent Claude plan review precedes READY/implementation; one independent
patch review precedes owner PR approval/merge. This reviewed amendment authorizes
implementation; it does not authorize merging a PR. Deployment and bounded real-CMAL SE
qualification follow merged code under the existing release workflow. Historical
coverage chooses the usable run window; reanalysis skill is labelled as such,
and unknown training overlap remains unknown. Full server generation/scoring is
a subsequent operational step, not evidence supplied by unit tests.

## Dependency graph

```json
{"phases":[{"id":"P1","depends_on":[]},{"id":"P2","depends_on":["P1"]},{"id":"P3","depends_on":["P1","P2"]},{"id":"P4","depends_on":["P2","P3"]}]}
```

## Expected touchpoints

Primary: `services/hindcast.py`, `flows/run_hindcast.py`, existing shared input/grid
helpers, `store/hindcast_store.py`, `services/skill/service.py`,
`flows/compute_skills.py`, relevant types/codecs/metadata and matching tests.
Conditional: FI adapter period/batch projection, skill identity persistence and
hindcast/skill API readers where period metadata must survive. Scope each change
from P1 evidence; no upstream FI/Aquacast workaround or dependency change assumed.

## P1 decisions and review disposition

Independent Claude review completed (local report, not committed). The following
concrete decisions replace the corresponding open choices above, as refined by
the subsequently reviewed bounded MVP amendment.

1. **Source contract:** raw observations and historical forcing currently expose
   timestamps and values without period metadata. No timestamp-only inference is
   allowed. Introduce one typed, explicit source-period declaration at the historical
   input boundary, reusing `ForecastPeriodDefinition`, keyed by station, source and
   parameter. The declaration includes native period and aggregation; it is supplied
   by an evidence-backed configuration/reader, not guessed from row spacing. The
   historical preparation optimization may index/cache raw rows but must preserve
   this declaration and values. Unknown sources cannot enter declared-period
   aggregation. Regular point samples may be admitted only with an explicit native sampling grid
   and complete expected slots, using the declared snapshot-mean/max policy (as in
   qualified ERA5 temperature). No arbitrary point interpolation or inference from
   observed spacing is allowed. Interval means/totals/maxima retain interval semantics.
   The same declaration is used for model inputs and verification. A missing target
   series may be represented as missing values subject to the model's own declared
   tolerance, never converted to observed zero or counted as scoring truth.
2. **Reduction:** convert source labels to physical half-open intervals, require
   native grid nesting in the target grid, reject overlaps/duplicates and boundaries
   cut by a native interval, and require full contiguous coverage for each usable
   output bucket. Mean uses duration weighting; sum adds interval totals; max takes
   native maxima. Incompatible aggregation transitions refuse. Convert to the model
   label only after reduction. Preserve gaps. Under recorded availability,
   aggregation takes the latest constituent availability only when all constituents
   are known. The approved retrospective policy explicitly fills unknown archived
   discharge availability at physical closure, retaining known later values.
   Physical closure remains an assumption, not evidence of actual publication.
3. **Truth:** verification uses QC-passed ordinary observations only, complete
   periods ending no later than the injected evaluation clock. Historical truth need
   not have been published by the historical issue, but input observations must not
   leak future availability. Retain the selected recorded/retrospective policy at
   FI and in result provenance. No implicit
   provisional-to-standard conversion. Owner confirmed the MVP 15-minute comparison on 25 Nov. Imported modern DHM
   means represent the exact Nepal calendar day, 18:15–18:15 UTC. For verification
   only, explicitly configure a +900-second reference displacement relative to the
   18:00 forecast interval. Retain the original observation period and value, forecast
   period, and displacement in score/diagram provenance and API output. This is
   approximate verification, never an exact-period match. The separately explicit
   accepted input-support mapping is described in the bounded MVP amendment.
   Generic matching defaults to zero displacement. Require equal duration and
   aggregation; no hidden nearest-neighbour join. Historical civil-offset changes
   require matching source declarations and cannot be forced into the modern offset.
4. **Windows:** keep caller issue instants and cadence unchanged. For each issue,
   use `TimeGrid.lookback_bounds` for complete history. Declared forecast support
   starts at `grid.floor(issue)` and extends for the requested horizon, matching
   `ForecastEnsemble.__post_init__` and the deployed FI output contract. Do not use
   `TimeGrid.future_bounds` here: at an off-boundary issue it advances to the next
   boundary and would drop the first forecast period. For example a 00:00 UTC issue
   on a START-labelled 18:00 daily grid starts with the preceding day's 18:00 period
   (signed label lead -6 hours). Future-known reanalysis must cover that entire
   period; observations from its elapsed portion must not enter past targets.
   Prefetch the union of physical windows, adding native end-label fetch padding
   where needed. Test the literal first and last bounds for START and END. Do not
   create a shifted issue identity. Require requested step to equal declared step.
5. **Batching:** FI explicitly defines `hindcast(inputs, issue_datetimes)` as the
   batched equivalent of prediction. Prepare the full observation/forcing series
   once, then deliver only the union of windows needed by each bounded call. Use
   the existing issue projections and `_merge_hindcast_frames` conflict checks;
   projections do not repeat source aggregation. Do not globally mask the shared
   series at the earliest issue. The model selects each issue's history and
   enforces its availability cutoff. Conflicting duplicate values or availability
   still refuse. No per-issue source revisions are synthesized. Every member
   must carry the same declared grid. Pass `time_grid` through batch output
   reconstruction (currently missing), validate each issue's expected physical output
   windows, and test per-issue cutoff using a deliberately future-sensitive fake.
   Keep runtime errors per member and a short final batch. Compare deterministic
   fixtures exactly and stochastic runs by period identity, not sampled quantiles.
6. **Hindcast persistence:** add nullable header `period_definition` using the
   existing codec, round-trip through ensembles, and keep legacy NULL. Retain the
   existing run-scoped uniqueness key but refuse an upsert changing period identity;
   a different period contract requires a different run. No old row relabelling.
7. **Lead:** retain the public meaning of elapsed time from issue to valid label;
   preserve both label and physical bounds so consumers can distinguish start/end.
   Declared-period storage/scoring must refuse a non-integral-hour elapsed lead
   rather than floor/round it. This is an explicit support limit of the existing
   integer-hour schema, not a claim that arbitrary minute leads are supported.
   The review's example 18:00 minus 06:00 is integral (12 h); the actual truncation
   risk is a fractional-hour issue/boundary. Test both. Extending public lead units
   is separate work; daily CMAL SE does not require it.
8. **Score identity:** persist nullable period definitions on scores and diagrams,
   include their canonical identity in cohort keys, current-version natural indexes
   and generation fingerprints, and round-trip/expose the identity on review reads.
   Add observation-period/reference-displacement identity for approximate verification
   so exact and approximate scores cannot collide or be combined.
   A generation remains the complete set of its station/model/parameter/forcing
   cohorts; do not split its scope and thereby weaken existing completeness gates.
   Mixed legacy/declared or different label/aggregation cohorts never mix. Preserve
   legacy computation behavior and keys. New period scoring uses a distinct
   computation version. Seasons remain issue-based, explicitly tested/documented.
9. **Consumers:** adapt skill responses to expose forecast/reference period definitions
   and comparison displacement. Extend event-review source metadata with the retained
   forecast period definition and expose physical start/end per point, preserving
   valid labels and issue instants. Source selection remains pinned to run/artifact;
   mixed period definitions within that selection refuse. Keep operational/hindcast
   concatenation unsupported. Existing station observation endpoints retain their
   actual source timestamps. Update the bounded review schemas/OpenAPI tests and
   leave consumers that cannot represent declared periods explicitly excluded.

Implementation must not silently grow this slice to arbitrary point interpolation,
availability assumptions beyond the approved policy or fractional-hour public leads. The owner-approved
15-minute comparison is explicit approximate truth and remains distinguishable.
If the source declaration cannot be grounded for Nepal, the generic capability can
be tested but the live run remains held with the specific missing source evidence.

## Batch execution decision — owner confirmed 26 November

Use the existing capability-negotiated batch route whenever supported. Preserve
single-issue execution for models without the capability and an explicit batch
size of one. Do not replace the existing runner or introduce a second scheduler.
Any bounded issue-date batches project views from the same complete prepared
series; they do not trigger new reads or aggregation. Start current CMAL SE with
`issue_batch_size=1`, based on the measured wrapper behavior below; retain generic
batch support and defaults for other models. Seven-issue qualification remains a
comparison workload, not a seven-day observation limit.

- Start real-model qualification with seven issues and a bounded station sample.
  Compare batch sizes one and seven, including a short final batch, against the same
  artifact, source data and issue dates run individually. Check physical periods,
  deterministic values within numerical tolerance, per-issue availability and
  failure accounting; stochastic quantiles need not match across batch sizes.
- Record requested/effective batch size, run/artifact identities, usable/failed
  station-issue counts, total wall time and process peak memory. Increase size only
  after this measurement, within the deployed worker's existing memory limit.
- Retain complete, identical period definitions in every output, including the FI
  multi-issue reconstruction. The declared-grid guard remains until input assembly,
  persistence and matching-period scoring pass their tests together.
- An outer batch is not proof of vectorized inference: Aquacast 0.1.396 may execute
  observed-input issues separately internally. Measure the actual benefit and do
  not promise a speedup from the batch size alone.

Code audit: the current group runner already negotiates batch capability and
flushes partial batches; the FI multi-issue reconstruction omits `time_grid`, and
hindcast storage/scoring still lack the complete declared-period contract. These
remain implementation requirements, not completed functionality.

## Refreshed implementation inventory — 26 November

Remote main fetched again at `273615817e18d56554dca7f01587f1a0c8f52532`;
this branch has no code commits beyond that main. Preserve and reuse completed
work: #641/#644 group batching, #648 operational output periods, #666 model-owned
lookback, and #670 model-declared live runoff preparation. The #670 plan expressly
excludes hindcast/scoring. Open #679 exposes stored artifact skill; its patch
does not implement hindcast preparation or score calculation.

Read-only local execution reproduced both station/group declared-grid refusals
and the declared-period skill refusal. Schema inspection confirms operational
`forecasts.period_definition` exists and `hindcast_forecasts` lacks it. The FI
multi-issue output reconstruction still omits `time_grid`. These are executable
code gaps, not merely outdated wording in this draft.

Regression evidence on that revision: 172 tests passed across
`tests/unit/services/test_hindcast.py`, `test_rating_inputs.py`,
`tests/unit/adapters/test_forecast_interface_adapter_predict.py` and
`tests/unit/services/skill/test_service.py`. This verifies current supported
behavior; it does not qualify declared-grid hindcasts. No server hindcasts or
score recomputation were launched during this audit.

## Aquacast full-series speed audit — 26 November

Owner asked to exploit Aquacast's full-series hindcasting, not merely increase
the size of repeated date-by-date calls. Upstream main and the deployed pin both
resolve to `fc772226703940f26d29d8e62a5c39175851b2f8` (0.1.396); no dependency
upgrade is implied by this audit. Installed operational/model.py, feed.py and
data/dataset.py match the inspected source bytes.

The native path builds one data container and predictor, then
`FastTimeSeriesDataset` slices preloaded tensors and `Predictor.predict` batches
windows across stations/dates (`aquacast/pipeline.py:472–489`,
`aquacast/data/dataset.py:99–155`). FI already accepts one union time series plus
`issue_datetimes`. Its current Aquacast implementation splits observed-input
requests recursively (`aquacast/operational/model.py:784–804`), applying each
issue's availability cutoff correctly, but rebuilding the data module and predictor
and evaluating the full feed before filtering requested issues. A seven-issue
profile executed seven loaders of eight windows each (56 evaluated windows).
Removing observation inputs or bypassing their availability mask is not an
optimization option.

Local diagnostic with the actual Nov23 SE checkpoint SHA256
`d04c3942b8bbe152ddd049e7b7497716a70656bb2704429e2d00aa1155625a3e`, one synthetic
station, CPU/one Torch thread, 365-day lookback, 18UTC START periods and 15-day
horizons:

| Execution | Issues | Seconds | Windows evaluated |
|---|---:|---:|---:|
| Native full-series, first measured call | 7 | 0.360 | 8 |
| Native full-series, warmed process | 30 | 0.277 | 31 |
| Native full-series, warmed process | 365 | 1.898 | 366 |
| Current FI batch wrapper | 7 | 1.214 | 56 (separate instrumented run) |

The native timings include synthetic frame preparation, data-module/predictor
construction and inference, but exclude imports, checkpoint-file loading, database
reads/writes, Gateway fetching and score calculation. They are local synthetic
measurements, not Nepal-host throughput qualification. Whole-process peak RSS was
2,185,740,288 bytes across the experiment, not incremental batch memory.
All 105 deterministic station/issue/lead rows in the seven-issue comparison matched
FI exactly. Perturbing discharge from the first forecast period onward left that
first issue's native point forecast unchanged. This does not prove arbitrary delayed
availability handling: the synthetic observations explicitly become available at
physical closure. Quantiles are not asserted equal across batch shapes.
Evidence stays local in `/tmp/cmal-se-hindcast-profile-20261126.json` and
`/tmp/cmal-se-native-hindcast-profile-20261126.json` (matching logs alongside them).

Implementation consequences:

- Preserve the generic FI batch call. Pre-aggregate both observations and forcing
  once over the full run span and project bounded delivery views, retaining their
  values/grid/availability across issues; coordinate that change
  with the existing historical-input preparation owner. Do not implement a second
  Aquacast-specific prediction path in Flow.
- Fast full-series execution with discharge belongs inside Aquacast's FI
  implementation: reuse container/model state, restrict inference to requested
  windows and apply observation availability independently for each sample/issue.
  FI's batch contract already expresses this workload; no FI contract change is
  established by this audit. An upstream implementation change would need separate
  review, qualification and an approved dependency update; none was made here.
- Before choosing production batch size, profile source preparation, FI conversion,
  model inference and persistence separately. With the current observed-input FI
  recursion, arbitrarily larger outer batches can multiply repeated full-feed work.
   After the owner's instruction to proceed without it, the upstream optimization
   is a later improvement. Use bounded calls on the current pin; preserve per-issue
   cutoff tests and late/unknown-availability cases.

Acceptance tests for the full-series preparation must show that adding issue dates
does not increase the number of source aggregation passes; later eligible
observations remain usable for later issues; changing future observations leaves
earlier predictions unchanged; and batch/single execution uses the same physical
periods and qualified daily values. Preserve original observation periods for
the explicitly approximate 15-minute verification comparison.

## Later full-series execution gate and review findings

The independent Claude review confirmed the native speed opportunity but rejected
an inference-speedup claim for the current FI path. The measured seven-issue native
comparison is about 3.4 times faster; neither that ratio nor the native year timing
is an end-to-end Flow/server result. No year-long FI timing was measured. Feeding
the complete series into the current recursive implementation can increase repeated
inference rather than reduce it.

Before later enabling unbounded full-feed delivery, qualify the model through FI at 7, 30 and
365 issues, with requested-window work growing proportionately rather than repeated
full-feed evaluation per issue. Retain deterministic point/physical-period parity,
per-issue availability and future-target isolation tests. Then measure a bounded
real-data server run with separate preparation, conversion, inference, persistence
and scoring timings. Report memory as well as elapsed time. Single-pass source
aggregation is a separate Flow acceptance criterion, not proof of faster inference.

The following review items are addressed by the owner-approved amendment below;
their correctness gates remain mandatory:

- Historical observation availability: the owner approved the labelled closure
  assumption for archived discharge on 26 November; preserve known later
  availability and never silently apply that assumption to live forecasts.
- Shared-series admission: define each issue's NaN-counting window, eligibility
  cohorts and failure reporting using existing FI contracts. Non-batch models use
  issue-specific views of the shared aggregate without repeated aggregation; this
  is also the bounded current-version delivery mechanism.
- Consumer/score contract: explicitly resolve signed leads, storage/scoring parity,
  reference fetch bounds for END labels and the accepted +900-second comparison,
  and concrete period-identity columns/indexes/computation version. Test legacy and
  provisional separation at query boundaries and changed-period upsert refusal.
- Coordination: agree the source-period type and prepared-series helper signatures
  with the historical-preparation owner before either session edits shared code.
- Upstream execution: Sandro owns the Aquacast FI optimization. Record the approved
  dependency version before claiming its full-feed performance improvement; it is
  not a prerequisite for this bounded MVP implementation.

Owner-requested follow-up rechecked both upstream heads and directly exercised
Aquacast through FI without the Flow shim. With the same 410-day synthetic feed,
1/7/30 requested issues evaluated 31/217/930 windows and rebuilt the predictor
1/7/30 times. This confirms repeated full-feed inference independently of Flow
preparation. Evidence: `/tmp/cmal-fi-wrapper-recheck-20261126.json`.
Track the required wrapper fix and shared qualification in
[ForecastInterface #24](https://github.com/hydrosolutions/ForecastInterface/issues/24).
The implementation fault is in Aquacast; the FI batch signature already expresses
the workload. No dependency update or production change has been made.

The complete review remains local at
`/tmp/declared-grid-plan-review-20261126.json`; do not commit that report.

## Owner-approved bounded MVP amendment — 26 November

This section supersedes the previous unbounded-delivery prerequisite. Sandro owns
the upstream wrapper fix. Continue on Aquacast 0.1.396 and FI 0.2.10 without waiting
for it or changing the artifact. The owner also approved the explicitly labelled
retrospective availability assumption requested in this session.

### Measured current-version execution

Prepare all five daily series once, then use time-filtered views for each call.
No model-specific runner or direct native Aquacast call enters Flow. Direct public
FI diagnostics with the actual checkpoint and one synthetic station showed:

| Comparison | Issues | Evaluated windows | Seconds |
|---|---:|---:|---:|
| One full-feed call | 30 | 930 | 20.278 |
| Bounded calls, size 7 | 30 | 200 | 6.463 |
| Separate paired size check: bounded size 7 | 30 | 200 | 5.603 |
| Separate paired size check: bounded size 1 | 30 | 30 | 3.989 |

All cases returned 450 rows on the same issue/valid-time grid. Maximum point-value
difference was 7.2e-7 for full versus bounded seven, and 1.6e-6 for seven versus one
(native discharge units). Stochastic quantiles were not compared. Timings vary
with local load and exclude database/Gateway/scoring; work counts are the stable
evidence. This is not an improvement measurement against deployed Flow, whose
existing adapter already limits issue batches. Evidence remains local in
`/tmp/cmal-bounded-hindcast-20261126.json` and
`/tmp/cmal-bounded-size-check-20261126.json`.

Select size one for the initial CMAL SE host run using the existing flow parameter;
do not hardcode a model name, cap the generic batch API or change other models'
defaults. Batch-capable models still use the existing batch path when size > 1.

### Shared preparation and admission contract

This session owns period qualification and the following new boundary; the other
session retains bulk-read/cache/index optimization. Reuse existing stores and
keep `hindcast.py` edits limited to invoking preparation once and projecting
existing input types. New code lives in `services/hindcast_inputs.py` and its
small domain types in `types/hindcast_inputs.py`, not a second scheduler.

- `HistoricalSourcePeriod` is a frozen declaration keyed by station/source/
  parameter, containing the existing `ForecastPeriodDefinition`, an explicit
  `stored_label_shift: timedelta` (default zero), and source-evidence identity.
  The shift decodes a known reporting-date convention; it cannot be inferred
  from data spacing or used to hide an incompatible physical period.
- A separately explicit `input_support_displacement` (default zero) records an
  accepted approximate source-to-model period mapping. For the previously
  accepted DHM/CMAL comparison, +900 seconds means an 18:15 source day is used for
  the model's 18:00 day without pretending its physical support changed. Allow
  this only for equal-duration/equal-aggregation periods with the onboarding
  declaration; qualify the source period first, retain its original bounds, and
  calculate availability from its actual closure. This is not native-grid
  interpolation or a general permission to relabel incompatible daily products.
- Boundary `source_periods` declarations on the hindcast/skill flow are parsed
  once with Pydantic and the existing period codec, then passed as frozen domain
  values. Reject duplicate/conflicting keys before reading or writing output.
  No automatic model-name or station-code special cases. Production declarations
  must cite the import/source contract; a raw timestamp alone is insufficient.
- `prepare_hindcast_inputs(..., requirements: ModelDataRequirements,
  source_periods: tuple[HistoricalSourcePeriod, ...],
  availability_policy: HistoricalAvailabilityPolicy) -> PreparedHindcastInputs`
  consumes already-fetched station rows and the union physical range once. The
  result retains the complete qualified frames and their source declarations.
- `PreparedHindcastInputs.for_issue(issue_time, forecast_horizon_steps)` returns
  existing `StationModelInputs` by filtering the shared frames. It never reads a
  store or resamples. The same projection serves batch and non-batch models.
  Preserve per-variable lookback/future requirements, gaps and availability.
- Keep `BatchGroupHindcastModel.hindcast_batch(tuple[GroupModelInputs, ...])`
  unchanged. `assess_group_inputs` runs on each issue's projected windows before
  the existing station-eligibility cohorts are formed. Different eligible station
  sets deliberately split a batch; a one-issue cohort uses `predict_batch`.
  Conflicting duplicate values/availability continue to refuse during union.
- Preserve failure information FI actually returns; missing issue/station output
  gets an explicit `missing issue block` failure, never a fabricated upstream
  cause. Do not hide partial failures, invent a new FI result protocol, or broaden
  retries. Include `time_grid` in batch reconstruction and refuse mixed grids.

### Approved historical availability

Add `HistoricalAvailabilityPolicy` with `RECORDED_ONLY` and
`RETROSPECTIVE_CLOSURE` values; default to `RECORDED_ONLY`. The latter is explicitly
selected for this MVP's archived discharge. Fill only unknown discharge
availability with the end of its actual observation period. Preserve a known
later availability; a usable aggregate cannot become available before its full
physical closure. Preserve any known constituent delay through reduction.
Do not use database import `created_at` as evidence of historical publication.

Deliver the resulting epoch-microsecond values through FI's existing
`available_at_us` column. Never mask the shared frame using one global issue
cutoff. Persist the selected policy on hindcasts and propagate it to scores and
API metadata. This does not change live forecasting, training provenance, source
QC or provisional-data admission. The 15-minute comparison does not make a DHM
day available at 18:00: physical closure is still 18:15 UTC.

### Persistence, matching and consumers

- Next additive migration is scoped from main's current `0096` head and rechecked
  before commit. `hindcast_forecasts` gains nullable `period_definition` JSONB,
  nullable `source_periods` JSONB and nullable `input_availability_policy` text.
  Legacy rows remain NULL. A same-run/issue/station upsert cannot change any of
  these contracts, including NULL-to-declared; a fresh run is required.
- `SkillScore` and `SkillDiagram` gain nullable `period_definition`, nullable
  `reference_period_definition`, nullable `reference_displacement_seconds`, and
  nullable `input_availability_policy`, with JSONB/JSONB/integer/text storage.
  Validate them together at the boundary. Approximate displacement is part of
  identity, not just a caption. Declared scoring uses computation version 5;
  legacy version-4 computations and rows retain their existing meaning.
- Extend all `uq_skill_scores_natural_key*` and `uq_skill_diagrams_natural_key*`
  variants, including generation and threshold variants, with these identity
  columns using `COALESCE(jsonb::text, '')`, `COALESCE(displacement, 0)` and
  `COALESCE(policy, '')`. Preserve existing version/generation predicates and
  threshold identity. Cohort keys and generation fingerprints include the same
  canonical content and the selected source declarations. Preserve whole-scope
  generation completeness; do not publish a subset after a rejected cohort.
- Compute physical forecast intervals from each label: START means
  `[label, label + step)`; END means `[label - step, label)`. Reference interval
  is `[forecast_start + displacement, forecast_end + displacement)`. Fetch the
  union of those reference intervals, decoding each source's label convention;
  END-labelled native reads need the final closing label included. Test literal
  lower/upper fetch bounds and filter post-fetch by physical support.
- Match only equal-duration/equal-aggregation qualified truth. Default comparison
  displacement is zero; use the owner's +900-second comparison for DHM daily
  truth. Keep reference periods intact and show this as approximate comparison.
  Reference observations must be QC-passed and physically complete by evaluation
  time; they need not have been available at the historical issue.
- Store and score integral signed label leads identically. Reject fractional-hour
  leads at both boundaries rather than floor/round them. Extend event-review
  signed-lead request/domain validation to -24 hours (the maximum fixed grid
  step is 24 hours), while requiring an actual declared period for negative
  leads. Legacy negative-lead rows remain invalid. Return physical start/end and
  period metadata so -6 hours is not presented as a completed past forecast day.
- Expose the period/reference/displacement/policy fields on the existing skill
  and diagram responses, preserving latest-main artifact identity and generation
  selection. Event-review source selection must keep run/artifact/purpose scope
  and reject mixed legacy/declared periods. Operational/hindcast concatenation
  stays unsupported. Update schemas and contract fixtures together.

### Amendment acceptance

Add behavior tests for one aggregation pass regardless of issue count and batch
capability; bounded delivered unions; per-issue NaN cohorts; missing FI issue
output; late/unknown/assumed availability; literal START/END and -6-hour lead
parity; exact/approximate reference fetch bounds; contract-preserving store
replay; period/policy natural-key separation; and query-level legacy/provisional
isolation. Check another grid phase/model declaration, not only CMAL SE.
The independent review accepted this amendment, with these additional acceptance
requirements incorporated before implementation:

1. Skill/diagram readers return every evaluation identity explicitly tagged;
   no endpoint may collapse exact and approximate (or different availability)
   cohorts to one row per lead. Test a generation containing several identities.
2. Changed-contract hindcast upserts raise a typed, meaningful `StoreError` before
   value writes. A guarded upsert must handle an empty `RETURNING` result explicitly
   rather than leak `NoResultFound`. Test NULL-to-declared and changed declared
   contracts, including concurrent same-key writers.
3. At an 18:05 UTC issue, the approximate DHM day closing at 18:15 remains
   unavailable. Test the withheld input plus retained retrospective/approximate
   metadata; normal model input tolerance decides whether that issue is usable.
4. Baseline reads and latest-generation/version selection respect evaluation
   identity: declared and legacy baselines must not silently hide each other or
   be compared across incompatible periods/policies. Test both baseline and
   generated rows under the same station/model/artifact scope. Refuse a requested
   baseline comparison if no matching-period baseline exists.

Also exercise size > 1 on a batch-capable fake and END-labelled fetch bounds.
All existing scientific guards remain until their replacement is implemented and
tested. Review report: `/tmp/declared-grid-bounded-plan-review-20261126.json` (local).

## Independent patch review disposition

The independent reviewer found four issues: unsupported effective variable labels,
omitted native-source score identity, padded-window NaN admission, and partial
declared baseline publication when generations were disabled. Corrective
implementation rejects conflicting/unknown effective labels, carries the canonical
native source catalogue plus a bounded index digest, gates each declared variable
window for prediction and evidence capture, and requires generation publication
for declared scoring. The same reviewer verified all four corrections with no
remaining finding. Full CI remains required before owner merge; full reports stay
local. Legacy computation and no-grid admission remain unchanged.
