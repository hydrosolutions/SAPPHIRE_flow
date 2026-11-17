---
status: DRAFT
created: 2026-11-17
title: Hydrological time consistency — bounded MVP milestones
---

# Hydrological time consistency — MVP execution milestones

This is an execution index for existing Plans 252, 254, 258, 263 and 267, not a
replacement specification. Initial source audit: deployed 009deaef (v0.1.1139).
M0 working baseline refreshed to remote main 98ae8af8 on 17 Nov; the two incoming
commits changed documentation/release target only, not audited runtime code. Re-inventory
changed call sites against the implementation branch before coding. One owner per
milestone; no parallel edits to shared time helpers. The overall rollout remains
DRAFT; only milestones explicitly marked READY below authorize their bounded
implementation scope. No live grid switch is authorized by M1.

## Recovered decisions and corrections

| Topic | Existing decision / present disposition |
|---|---|
| Local hydrological time | Owner reaffirmed 17 Nov: weather must be assembled on the hydrological time basis before model input; the same basis governs observations, forecasting and skill. UTC storage of instants remains valid. This does not mean changing only dashboard display. |
| Operating boundary | Plan 252 OD-12: explicitly declared per deployment, optional station override; not derived from a display/IANA zone. Group members must share step and phase. |
| Nepal | Plan 252 OD-3 and owner confirmation on 17 Nov: target 18:00 UTC daily origin, or 23:45 Nepal local time. Owner accepts its 15-minute displacement from the confirmed local-midnight DHM reference for preliminary evaluation. This is not exact NPT midnight at 18:15 UTC and does not authorize changing the training basis of existing models. |
| Current aquacast models | Owner correction on 17 Nov: currently trained on UTC days. Preserve that basis in operation until a compatible model/grid combination is qualified. Future models will declare their model time zone; that declaration is not available in the current pinned FI requirements. |
| Switzerland | Plan 252 OD-15: target 06:00 UTC precipitation day, fixed year-round. Existing phase 0 remains until the coordinated retrain/cutover. Prior 23:00 UTC and civil/DST-day proposals were withdrawn. Plan 263's off-grid temperature treatment remains unresolved. |
| No invented resampling | Plan 252 OD-13 withdrew interpolation/apportionment for time realignment. OD-14 handles point-reading boundary tolerance. Do not split an hourly rainfall total or retimestamp it to make a grid fit. |
| Weather gaps | The separately approved Nov 14 isolated-weather-gap implementation is a narrow later exception to the old blanket no-fill rule. Preserve its methods, one-day limit and five-day warning cutoff. It does not authorize rainfall apportionment or arbitrary temporal resampling. |
| Interval labels | Plan 267 records the owner's end-period-stamping decision for interval values; point readings remain instants. Plan 258 owns source/measurement support and interval bounds. Neither may be implemented by blindly changing all timestamps. |
| Complete buckets | Plan 228 D4's no-partial-past-bucket/no-future-leakage guarantees survive. Its UTC-only grid assumption may be superseded only when all consumers agree on the declared grid (252 OD-9 / 254). |
| Aggregation | Plan 234: use the FI-declared parameter aggregation and lookback; this is SAP3 work, not an FI extension. Several pieces are now implemented; do not redo completed work from stale plan status. |
| Display and requests | Plan 099: display timezone is separate; frontend picked local ranges must become explicit aware bounds. No dashboard redesign is included here. |

The earlier `/tmp/nepal-cmal-hindcast-next-steps.md` draft incorrectly recommended
civil-time-derived grids, rainfall apportionment and a DST FI extension. Those
proposals are withdrawn. The related upstream-issue draft must not be filed. The
previous Swiss DST question is withdrawn: the existing 06:00 UTC decision answers
it unless the owner explicitly changes policy.

**Owner confirmation, 17 Nov:** proceed with the existing Nepal decision as the
straightforward MVP path. Target 18:00 UTC for the operational daily grid; do not reopen
the civil/DST alternatives. This confirmation alone does not establish that a
different reference interval is an exact-match skill target.

**Owner acceptance, 17 Nov:** water levels and discharge are collected in Nepal
standard time, and the 15-minute difference between the adopted forecasting day
and DHM's midnight-to-midnight daily reference is accepted for the initial,
explicitly labelled preliminary evaluation. Preserve original reference dates and
measurement instants; do not shift them or describe the comparison as identical
physical intervals. This answers the focused M0 question. As proposed in that
question, preliminary results remain outside ordinary published skill until their
treatment is reviewed. The timing acknowledgement does not convert instantaneous
water-level readings into daily aggregates or establish unknown provider semantics.

**Owner correction, 17 Nov:** current aquacast models are trained on UTC, and future
models will declare their model time zone. The active artifact's stored note claiming
local-per-basin training is therefore stale and must not govern this plan. No artifact
metadata is changed by M0. Current UTC-trained operation and the target 18:00 UTC
Nepal grid are distinct contracts: the accepted 15-minute reference difference does
not authorize a six-hour change in model input-day boundaries. A future time-zone
declaration must distinguish model training periods from UTC timestamp serialization
and from dashboard display. If FI needs a new field, co-design it upstream; no private
SAP3 extension is included in this milestone.

**Owner follow-up, 17 Nov:** replace or retrain current models to fit the Nepal
day before cutover. The route is decided; M2e/M3a must qualify the resulting artifact,
not seek permission again for the principle of replacement/retraining. Selecting
and running that bounded model work remains a separate implementation/evaluation
scope. Historical ERA5 supplied by the gateway must use the same temporal contract
as its operational ERA5 delivery. The documentation check below now confirms this,
so no fallback assumption is needed.

## Current blockers that must not be hidden

- Current code still aligns shared assembly and scoring to UTC midnight. Newer
  rating-input, weather-gap and default-off daily-evaluation paths add more consumers.
- DHM daily discharge import labels source dates at Nepal civil midnight
  (18:15 UTC today). Owner confirmation establishes the working local-day meaning;
  the accepted 15-minute comparison applies to qualified 18:00-grid output only.
  It does not make current UTC-day output an exact or accepted near-match reference.
- FI v0.1.20 defines fixed-duration steps and UTC output instants. Declared fixed
  phases fit this design; a calendar/DST extension is not a prerequisite here.
  Check the exact pinned FI protocol, requirements and results and CMAL artifact's
  training convention. Looser validators in an older version are not permission
  to bypass its documented contract.
- No Nepal hindcasts, scores or diagrams are stored. Six stations have historical
  ordinary discharge; only 450 extends after the artifact's recorded 2020 end,
  through 2023. Recorded bounds are not full training-membership evidence.
- Sources with unknown interval meaning, unavailable sub-daily coverage or
  incompatible model training grids remain explicit unsupported cases, not zeros,
  synthetic hourly values or silently shifted inputs.

### M0 source/model contract table — investigation complete

Plan 268 D6 already closed the import-day question with an explicit working
assumption: DHM date-only daily discharge represents local 00:00 to the following
00:00 in Asia/Kathmandu, with historical offset handling and correction by controlled
re-import if better evidence arrives. It explicitly did not settle alignment with
the forecast grid. Do not ask the owner to repeat that import decision or wait for
DHM before continuing the investigation.

| Source/model | Native step and time basis | Physical support and label | Aggregation / disposition |
|---|---|---|---|
| Selected Recap precipitation (ERA5 + IFS operational route) | ERA5 hourly; IFS 3-hourly through lead 144, then 6-hourly. Source cycles and timestamps stay UTC. | Qualified contract: incremental interval totals, START-labelled; explicit end = start + native step. Units mm after conversion. | SUM complete contiguous intervals. The 18:00 boundary nests these native intervals, so no splitting is needed. Fetch coverage across UTC source days without relabelling source runs. Current daily selection still uses 00:00 UTC; change belongs to M2a. |
| Selected Recap temperature (same route) | Same native cadence as above. | Instantaneous snapshots, degrees Celsius; no interval end. | Existing equal-weight MEAN of required snapshots, not a time-weighted mean or invented interval mean. Preserve completeness checks. Six-hourly sampling can introduce diurnal sampling bias compared with hourly ERA5. |
| ERA5-only historical forcing through Recap | Hourly P/T; aware UTC valid times and source version are retained. | Latest client documentation explicitly covers ERA5-Land: precipitation is an hourly incremental total labelled at START; temperature is a snapshot at its timestamp. The client preserves gateway-normalized labels. | Same convention as operational gateway ERA5. P SUM, temperature snapshot MEAN. No second deaccumulation or timestamp shift. The historical adapter preserves returned valid times; lack of interval fields in the stored type does not leave the documented source contract unknown. Historical regridding's source-semantics hold is resolved. |
| DHM imported daily discharge | One date-only daily value; modern Nepal 00:00–00:00 = 18:15–18:15 UTC. Historical offsets use Asia/Kathmandu. | Pre-aggregated daily mean, m³/s; importer labels the local-day START instant. Local-day interpretation confirmed by owner. | Retain the supplied mean and original date. Preliminary comparison with a qualified 18:00-grid forecast may use the accepted +15-minute reference displacement and both intervals. Cannot reconstruct a UTC-day mean from these daily values. |
| Native DHM water levels | Irregular/sub-hourly readings; offset-aware measured times normalized to UTC. | Point readings at actual measurement instants; no implied daily support. | Keep timestamps unchanged. Any daily assembly must apply declared grid and existing QC/coverage rules, not treat one point as a full-day measurement. |
| Rating-derived discharge | Same actual reading instants as parent water levels. | Provisional point estimates with curve provenance and QC. Current daily input assembly uses UTC-day buckets and marks partial daily support. | Existing mean of accepted estimates is an input estimate, not proof of complete daily observation coverage. Preserve provisional separation and coverage flags when making assembly phase-aware. Not ordinary verification truth. |
| Current aquacast / CMAL-small inputs | Owner confirms UTC training. CMAL uses daily steps, 30-day lookback and 10 forecast days; current serving phase is 00:00 UTC. | Daily training periods are UTC days. Pinned aquacast docs identify daily dates as the days being forecast, with history through the preceding day. The stored local-training note is stale. | P SUM, temperature MEAN, discharge input MEAN through the existing shim. Keep current operation aligned until replacement/retrained models fit the Nepal day, as decided by owner. Qualify those artifacts before 18:00-grid activation. |
| Current CMAL-small outputs | Ten daily steps: requested day D through D+9; first valid label equals the model's first forecast-day label. | Daily START/date label: D means the UTC day [D 00:00, D+1 00:00), using the owner-confirmed UTC training basis. The model forecasts daily discharge depth in mm/day, converted by the shim to mean m³/s over that day. | Semantics resolved from pinned docs/code and unit conversion, not inferred from UTC serialization. FI offset=1 refers to the gap from the preceding observed day to the first forecast day, not a one-day delay after the issue label. Future Nepal integration must preserve explicit physical periods and apply the separately agreed END publication convention once. |
| Future model-declared time zone | Planned by owner; not a current capability of the pinned FI requirements. | Must identify the training-day convention and its relation to output periods; timestamp storage may remain UTC. | Record as future contract work. A time zone alone must not override an explicitly chosen hydrological origin. Resolve any FI contract gap upstream rather than adding a local workaround. |

Evidence anchors (repository-relative):

- [Configured operational daily weather](../spec/types-and-protocols.md#configured-operational-daily-weather),
  [sample/reducer contract](../../src/sapphire_flow/types/operational_weather.py) and
  [selected-source parser](../../src/sapphire_flow/adapters/recap_operational.py).
  The pinned source revision qualifies this contract, not the identity of every
  deployed producer; no new producer-attestation project is required for M0.
- [Historical forcing shape](../../src/sapphire_flow/types/historical_forcing.py) and
  [Recap historical route](../../src/sapphire_flow/adapters/recap_gateway.py).
- [DHM import decision](268-dhm-barkhk-runoff-delivery.md),
  [local-day conversion](../../src/sapphire_flow/adapters/nepal_local_day.py),
  [native DHM adapter](../../src/sapphire_flow/adapters/dhm.py) and
  [rating daily assembly](../../src/sapphire_flow/services/rating_inputs.py).
- [CMAL configuration](../../src/sapphire_flow/models/aquacast/configs/cmal_small.yaml),
  [aquacast shim](../../src/sapphire_flow/models/aquacast/_shim.py) and
  [FI adapter](../../src/sapphire_flow/adapters/forecast_interface.py), plus the
  owner's 17 Nov UTC-training correction. Pinned FI v0.1.20 defines fixed-duration
  steps and UTC instants, not a model-time-zone declaration.

M0 now closes under exit (a): source/model semantics and concrete period examples
are resolved. M1 has its own reviewed READY scope below; this does not authorize later
milestones or activation. Compatibility of the replacement/retrained artifacts still gates
M2c/M2e qualification and M3a/M3b execution. This is implementation verification,
not an unanswered question about the current model's output date.
The 18:00 target and preliminary reference tolerance remain settled. Neither a new
timestamp label nor successful prediction establishes compatibility for UTC-trained
weights. No code, source records, live artifact metadata or deployment was changed.

#### CMAL output-period resolution, 17 Nov

The question was whether a daily output labelled D describes the day beginning at
D, the preceding day ending at D, or a point measurement. That matters for matching
weather, reference discharge and lead time: a correct number joined to the wrong
day would give misleading skill.

The exact pinned aquacast revision `cea602191534c37615c069c1c5416e2c99149668`
settles current behavior:

- [Issue datetime documentation](https://github.com/hydrosolutions/aquacast/blob/cea602191534c37615c069c1c5416e2c99149668/docs/forecast-interface-usage.md#6-issue-datetimes)
  identifies horizon 1 as the requested forecast day itself, with the last observed
  day one step earlier. Daily-only stamps identify the calendar day being forecast.
- [Row construction](https://github.com/hydrosolutions/aquacast/blob/cea602191534c37615c069c1c5416e2c99149668/aquacast/inference/records.py#L77-L99)
  takes the first output date at `start + lookback`; subsequent outputs advance
  through the daily date index. There is no additional one-day output shift.
- [Target requirements](https://github.com/hydrosolutions/aquacast/blob/cea602191534c37615c069c1c5416e2c99149668/aquacast/operational/requirement.py)
  declare daily targets in mm/day. SAPPHIRE's existing aquacast unit conversion
  divides daily water volume by 86,400 seconds to report mean m³/s. This does not
  turn a daily forecast into an instantaneous reading or a daily peak.

Example: a current model request for 2026-11-17 has its first daily output labelled
2026-11-17 00:00 UTC, covering [2026-11-17 00:00, 2026-11-18 00:00). Its tenth
label is 2026-11-26 00:00 and that interval ends on 27 Nov. These model labels are
distinct from the actual time the forecast was calculated or published.

For the target Nepal hydrological day named 2026-11-17, the agreed physical period
is [2026-11-16 18:00, 2026-11-17 18:00) UTC. Its mean discharge belongs to that
whole interval. Under Plan 267's target END convention its label is the latter
boundary; retained bounds distinguish it from today's START-labelled UTC output.
Actual issue/publication times remain separate. This is the replacement model's
required period contract, not a reinterpretation of existing forecasts.

One concrete integration task remains for M2c: pinned aquacast's
`operational/datasource.py` converts daily timestamps to `pl.Date`, and its daily
issue matching is calendar-date based. Replacing/retraining weights alone does
not preserve an 18:00 phase through that code. The model-side daily-time handling
must support the declared period, with any necessary FI change co-designed upstream.
Do not hide the phase loss by relabelling outputs after prediction. Prove the first
and last physical output periods against the input periods in M2c/M3a. This known
work does not block pure grid-contract implementation in M1.

#### ERA5 documentation resolution, 17 Nov

SAPPHIRE pins `recap-dg-client` revision `9340e4025e05a72729d93d1d6881abcd5f298ff1`
in `pyproject.toml` and `uv.lock`. Its README explains incremental values and UTC
timestamps but does not explicitly identify the interval endpoint. The current
upstream README at `63c2578535265eff8ce49d0010e964871131f790` now resolves that omission
in [Timestamp policy](https://github.com/hydrosolutions/recap-dg-client/blob/63c2578535265eff8ce49d0010e964871131f790/README.md#timestamp-policy):

- Gateway ERA5-Land rainfall at `2026-01-01 23:00 UTC` covers
  `[2026-01-01 23:00, 2026-01-02 00:00)`; the value is already incremental.
- ERA5-Land radiation is also start-labelled accumulated support. Temperature is
  instantaneous; do not shift its timestamp to match an accumulation convention.
- Raw ECMWF accumulated timestamps and normalized gateway timestamps differ.
  The gateway handles that conversion; the client does not repeat it.
- For the Nepal target day ending `2026-01-02 18:00 UTC`, sum the 24 rainfall
  increments labelled `2026-01-01 18:00` through `2026-01-02 17:00`. The increment
  labelled `2026-01-02 18:00` belongs to the next day. Apply the agreed snapshot
  mean to the temperature samples on the same target grid.

The native historical adapter calls `ecmwf.era5_land_reanalysis`, retains returned
valid times, converts units and excludes forecast-fill rows. This is the gateway
contract, not the raw CDS convention cited by older Plan 267. No SDK upgrade is
needed merely to apply this documented server-side normalization. This establishes
the intended contract; it is not a new audit of every stored historical value.

[Existing client issue #5](https://github.com/hydrosolutions/recap-dg-client/issues/5)
is still open with no comments at this check. Its central timestamp clarification
is now in the README; no new issue or comment was posted. The issue's more detailed
IFS cadence-transition and version/legacy examples can be checked when that issue
is closed, but do not leave the ERA5 hourly contract unresolved.

**Bounded reconciliation proposal for Plan 267:** qualify its ERA5 period-ending
row as raw ECMWF/CDS data and add gateway-normalized ERA5 as period-beginning. Its
blanket historical claim that all established sources stamp END must not govern
the gateway route. The separate proposed internal END convention is unchanged:
implementing it would require an explicit, single conversion at the adapter
boundary, coordinated with consumers. Do not apply that future conversion now.

The accepted modern-Nepal comparison must be explicit and one-to-one. This example
is for qualified 18:00-grid output, not current UTC-trained output. Example for
the named Nepal day 2021-07-01: forecast support is 2021-06-30 18:00 UTC through
2021-07-01 18:00 UTC; the daily reference represents 2021-06-30 18:15 UTC through
2021-07-01 18:15 UTC. Keep both intervals, the source date and the accepted +15-minute
reference displacement in the preliminary result's provenance. This is not an
exact-interval score. Do not use unconstrained nearest-time joins, move a missing
reference to another day, duplicate a reference across adjacent days, or extend
this exception to input weather, different offsets or provisional rating truth.
Physical interval identity is independent of whether an output labels its start
or end; end-period convention work must preserve this pairing. Restrict this
example to the modern-offset pilot rather than applying +05:45 to all history.

## Milestones and handoffs

Every handoff contains: exact base SHA, changed paths, behavior/decision table,
focused tests and unresolved findings. Full review reports stay outside commits.
Scope each implementation PR to one milestone or one named sub-milestone; do not
activate a new time basis between coupled milestones.

### M0 — reconcile the actual source and model contracts (read-only + docs)

**Owner:** one planning agent. **Dependencies:** none. **Status:** COMPLETE under exit (a), after the ERA5 and pinned CMAL output checks. No implementation approval.

Read 252/254/258/263/267, 268, 519, `2026-10-30-native-operational-era5-history`,
`2026-11-13-nepal-missing-discharge-and-weather-age`, and
`2026-11-14-nepal-weather-gap-filling`. Produce one short
table for the actual Nepal precipitation, temperature, imported daily discharge,
native water levels/rating Q and CMAL outputs: native step/phase, point or interval,
start/end/date-only stamp, intended physical window, declared aggregation and
training-grid evidence. Explicitly distinguish pre-aggregated interval means from
point observations. Inspect existing delivery metadata/docs before asking DHM
again. Reconcile the 18:00 versus 18:15 issue explicitly; obtain owner disposition
only if current evidence does not settle it. Do not assume the plan's provisional
hour or the importer's timestamp establishes the correct measurement period.

**Exit:** either (a) resolved source/model table and concrete Nepal input/target/
output examples, or (b) completed investigation naming missing facts and dependent
milestones still held. Outcome (b) is not approval to implement. Return questions
to the orchestrator; do not contact DHM or wait indefinitely. Supply a diff-only
proposal for contradictory paragraphs in the listed plans; no code, config, source
records or unrelated docs. Swiss 06:00 and off-grid-temperature work stay separate.

Plan 267's coordinated Swiss boundary/stamping/retrain cutover remains intact.
Its wording explicitly concerns Swiss T6, not a blanket requirement to deploy
Nepal and Switzerland together. Nepal must likewise qualify and switch its coupled
phase/stamp/model contract once. M0 records this interpretation; escalate if a
broader prior owner restriction is found, rather than assuming a waiver.

### M1 — small shared grid/support contract, with existing behavior preserved

**Owner:** root implementer. **Depends:** M0. **Status:** IMPLEMENTED; independent patch reviews passed. Full-suite merge gate remains pending.

Use Plan 252's step-and-phase design and existing phase metadata; implement only
the config/domain pieces needed for the resolved Nepal case. Declare the currently
used phase zero explicitly in unchanged deployments. Add phase-aware pure bounds
helpers and the minimum source/measurement interval declaration required by M0
(Plan 258), reusing existing types where sufficient. Validate group compatibility.
No generic provenance platform or global source-registry migration.

**Exit:** phase-zero parity and agreed nonzero-grid examples pass; invalid grids and
incompatible groups fail clearly. No running configuration is switched. New phase
live activation remains unavailable until M2a–M2e and isolated M3a are complete.
Pure helper tests and the isolated M3a harness may exercise a nonzero grid; they
do not alter live configuration or write live skill results.

#### M1 implementation scope (17 Nov, READY after independent review)

Base: `98ae8af86ffe79e64a18178f93ad850f88b139b0`. One implementer; no deployment,
model execution or schema changes in this pass.

1. Add frozen `TimeGrid(step, phase)` in `types/time_grid.py`. Both are timedeltas;
   step must be positive and divide 24 hours exactly; phase is an explicit whole
   number of minutes with `0 <= phase < step`. Use exact timedelta arithmetic,
   not floating-point epoch conversion. No IANA-zone inference.
2. Provide `alignable` (equal step and phase), `fine.nests_into(coarse)` (coarse step divisible
   by fine step and phases differ by a multiple of fine step), `floor`,
   `lookback_bounds` and `future_bounds`. Bounds are physical half-open intervals,
   independent of START/END labels: past excludes the bucket in progress; future
   includes the current bucket only at its exact start. Counts must be nonnegative;
   zero returns `(floor, floor)` for lookback and `(first_future, first_future)`
   for future bounds. Reuse `ensure_utc`; reject naive instants.
3. Add a small pure common-grid validator that refuses empty or mixed-grid groups.
   This establishes the contract; runtime group assembly is wired in M2c.
4. Add optional `daily_grid_origin` and `daily_grid_origin_overrides` to the existing
   deployment boundary. Parse strict `HH:MM` UTC origins to timedeltas and validate
   them with `TimeGrid`. Overrides are keyed by station UUID, not station code;
   reject malformed UUID keys. The resolver requires
   an explicit deployment origin, uses an override for daily steps, and retains
   phase zero for subdaily steps. An omitted origin means undeclared legacy config,
   never an implicit declaration of zero; existing runtime readers continue unchanged.
   Overrides without a deployment origin are rejected. No station metadata migration.
5. Declare `00:00` in root `config.toml` and the shipped Nepal/Mac/staging deployment
   overlays; this records current operation, not the future Nepal/Swiss target.
   Deployment validation rejects every nonzero origin/override in this release:
   callers must not mistake a parsed future value for an implemented live switch.
   Pure TimeGrid fixtures can exercise 18:00 and 18:15 independently of deployment.
   M2e owns removal of this restriction with its activation gate; the accepted
   15-minute displacement provenance must be implemented before activation.
6. Update conventions, the type reference, `docs/spec/config-reference.toml` and relevant touchpoint map.
   Existing selected-weather sample types already express precipitation intervals
   and temperature points; M1 adds no parallel source-support registry or stamp enum.

This deliberately stages the older Plan 252 scope: replacing skill's observed
microsecond-phase tuples belongs to M2d, because those include legacy sub-minute
phases which this declared-grid type must not silently truncate or reject. Requiring
the new field from all legacy config callers is also deferred; the new resolver
refuses an undeclared grid. All current assembly/scoring calls remain untouched.
No second operational grid implementation is introduced: M1 helpers are the shared
destination for the later coupled migration, not a newly selectable execution route.

Verification: domain invalid-value tests; the Plan 252 10/15-minute nesting examples;
Nepal boundary at/before/after 18:00, month/year/leap and pre-epoch boundaries;
zero and negative count behavior; exact phase-zero parity with current helpers for
supported steps; group rejection; strict origin parsing, missing declaration and
nonzero activation refusal; real merged config fixtures. Run focused tests, Ruff
and changed-module type checks. Full suite must pass locally or in CI before merge.
Independent plan review precedes READY; independent patch review precedes delivery.

Independent Codex and Claude reviews recommend READY. The orchestrator accepted
the UUID-key, precise zero-count, named config-layer and later guard-removal
clarifications above and set this bounded M1 scope READY before coding. Reports remain local.

#### M1 implementation result

Added the declared grid type and exact bounds/nesting/group helpers, strict UTC
origin parsing with canonical HH:MM serialization, UUID overrides and explicit
phase-zero declarations. Legacy config loading is preserved; resolution requires
an explicit origin. Nonzero live origins remain rejected. No assembly, scoring,
model or live deployment change.

Independent Claude patch review approved. Codex found a JSON round-trip defect in
the initial timedelta serialization; canonical serialization and an evidence-JSON
regression test resolved it, independently verified by the reviewer. No outstanding
material findings. Full reports remain local.

Validation after the final behavior change: 535 tests passed across the new grid
contract, all unit configuration tests, training-data tests and forecast-evidence
flow tests. All 72 release-target tests also passed: 69 in the initial run and
three cache-access-blocked cases after rerunning with the required cache access.
The repository tool advanced release intent from 0.1.1140 to 0.1.1141; no release
was published. Changed-file Ruff lint/format, changed-module Pyright and diff
whitespace checks passed. This is focused regression evidence, not a full-suite claim. Full
suite success is still required locally or in CI before merge. M2 milestones remain
DRAFT; no server deployment or live origin switch has occurred.

### M2a — selected-weather and gap-evidence path

**Owner:** one implementer. **Depends:** M1. **Status:** DRAFT.

Own `types/operational_weather.py`, `services/recap_daily_weather.py`,
`types/weather_gap.py`, `services/weather_gap_fill.py` and their selected-weather
receipt callers. These have midnight/start-label assumptions before the resampler.
Distinguish provider-native UTC days from target hydrological periods; never
reinterpret a source day or receipt under a changed grid. Require M0-established
support and adequate fine-resolution coverage. An incompatible already-daily
product needs a resolved contract, not a different timestamp.
Make `fill_daily_weather` use the declared grid and reject inconsistent frames.
Preserve the agreed isolated-gap methods without authorizing time realignment.

**Exit:** actual selected-weather route fixture covers snapshot/receipt compatibility,
complete target coverage and a complete nonzero-phase frame producing zero fills.
Preserve UTC NWP cycle identity and elapsed 60-hour age checks. No live grid switch.

### M2b — shared training and operational assembly

**Owner:** one implementer. **Depends:** M2a. **Status:** DRAFT.

Own phase-aware bounds/resampling calls in `training_data.py`,
`operational_inputs.py` and rating daily assembly. Replace
`_normalize_provisional_discharge`'s midnight check with a declared-grid check,
retaining off-grid rejection and ordinary/provisional separation. Use agreed source
support and interval labels; preserve point timestamps and actual issue instants.
No blanket source-row rewrite or partial past bucket.

**Exit:** bounded training/live-input parity fixture, complete lookback, rating daily
Q and ordinary no-observation CMAL inputs. Existing deployments retain their old
contract; nonzero live activation stays held.

### M2c — hindcast, FI tracks and model-output anchoring

**Owner:** one implementer. **Depends:** M2b. **Status:** DRAFT.

Own `hindcast.py`, `track_assembly.py`, daily-model anchoring and group stacking.
Include the upstream aquacast daily-date conversion identified by M0: Nepal phase
must survive the model input/output path, not just a weight replacement. Resolve
that in the model/FI contract rather than a SAPPHIRE timestamp workaround.
Enforce step-and-phase compatibility at runtime, not only config parsing. Prove
actual FI input frames and first/last output intervals with the same source fixture
as M2b. Respect the pinned FI contract; do not reset issue time or strip timezone
information to satisfy it. Keep default-off daily-evaluation execution disabled
for the new grid, including its separate midnight output-integrity validator,
until adapted in a later explicit slice.

**Exit:** train/live/hindcast interval equivalence, output identity and runtime
incompatible-group rejection. No live switch.

### M2d — matching stored periods, skill joins and consumer labels

**Owner:** one implementer. **Depends:** M2c. **Status:** DRAFT.

Complete Plan 254 T7's required grid identity and Plan 267's scorer/label coupling.
Reuse skill generations/phase metadata; add storage only where the current schema
cannot distinguish meanings. Match observed and predicted physical intervals,
not merely dates. Resolve the existing difference between integer lead flooring
in storage and rounding in scoring without changing issue instants. Preserve the
existing season-reference rule while applying its documented hydrological date.
Expose enough period meaning for existing API consumers; do not bundle the new
event-review API. Keep old records explicitly old; no in-place time rewrite and
no use of new labels to reinterpret old scores.

**Exit:** exact observation/forecast matches, complete-period checks, lead precision,
score sample counts and old/new record separation pass. Check completeness against
the actual interval end; an end-stamped label must not gain another day. Send
pre-aggregated daily means down an interval-aware path; one instantaneous point
does not establish a daily mean. Assert CMAL's target aggregation is mean or thread
its declared alternative into scoring rather than using a name-only fallback.

Specify publication identity before coding: generation precedence currently excludes
evaluation window/grid, and baseline writes can also become visible. Choose an
explicit whole-scope compatible replacement or a minimal separate semantic identity.
A new run/generation UUID alone is not pilot isolation.

### M2e — artifact compatibility at activation and prediction

**Owner:** one implementer. **Depends:** M2d. **Status:** DRAFT.

Own Plan 254 T5's narrow compatibility gate. Persist or bind qualified step, phase
and support/stamp convention to the exact artifact, reusing existing provenance
where sufficient. Enforce agreement at activation/load/prediction for the new route,
including later artifact replacements. Unknown artifacts remain unsupported there.
No fleet backfill or automatic retraining.

Owner has chosen replacement or retraining for Nepal-day compatibility. Arrange
that bounded model deliverable before qualification; do not reinterpret the current
UTC-trained weights as compatible merely by changing metadata.

**Exit:** exact qualified artifact works; incompatible/unknown artifacts are refused
before prediction. Inventory every artifact used by the 17-station rollout.

### M3a — bounded isolated Nepal qualification

**Owner:** one evaluation agent. **Depends:** M2e. **Status:** DRAFT.

Verify the replacement/retrained artifact's Nepal training-grid compatibility and
output support. The owner has settled the replacement/retraining direction; obtain
the resulting model evidence before running this pilot. Run one station (450 if M0 qualifies it), a short
explicit historical window, a pinned artifact/run identity and ordinary source Q
through existing hindcast and skill services. Use bounded resources and an
evaluation-only scope; do not change live group membership/status. Check actual
finite outputs and matched samples, not only Prefect success. Keep the new pilot's
results separate from operational forecasts. Use existing dependency-injected
services with disposable output stores, not the live hindcast/skill writers.
Explicitly verify live score rows and published generation visibility are unchanged;
turning off generation publication alone is insufficient because baseline rows are
also served. Do not change live deployment configuration for the pilot.

**Exit:** actual pilot values, matched samples, bounded cost and artifact/grid
evidence, with no live skill exposure. This is a plumbing test, not a skill claim.

### M3b — coordinated Nepal cutover

**Owner:** one operator. **Depends:** M3a. **Status:** DRAFT.

After patch review, passing checks and owner PR approval, deploy with the existing
procedure. A dark deployment may preserve the old phase/stamp convention. A live
switch requires all active station/artifact contracts qualified and compatible;
switch the coupled input/output/scorer meanings together. Preserve the immediate
rollback path without relabeling already-written records. Verify actual finite
outputs for all 17 stations by 07:00 Nepal and correct API period labels.

**Exit:** correct scheduled delivery and consistent stored/API semantics. Successful
container health or delivery time alone does not establish scientific correctness.

### M4 — useful hindcast/skill evaluation

**Owner:** one evaluation agent. **Depends:** M3a. **Status:** DRAFT.

Expand to an agreed full seasonal/year window (450 in 2021–2023 is a candidate).
Use existing supported metrics, baselines and sample counts by lead, with explicit
period/forcing/training-overlap labels; reanalysis is not actual forecast-weather
skill. Qualify the other five historical stations separately. Provisional rating
Q is not silently admitted as ordinary verification truth. An isolated full
evaluation can proceed after M3a; publication to the live API additionally requires
M3b and the M2d publication contract. A short pilot must never replace it.

### M5 — separate event-review read contract

**Owner:** one API agent. **Depends:** M4 and M3b. **Status:** DRAFT, proposed follow-up.

Take a separate small read-only delivery slice from Plan 343: explicit series
identity; station-level forecast/hindcast lead series, long-period discharge and
backend period/lead metrics, with matching filters and limits. Network batching
comes after station semantics are proven. Keep fixtures explicitly provisional;
no calendar delivery date is promised by this plan.

## Required tests and non-goals

Test agreed Nepal boundaries, period-ending versus point semantics, rain just
before/after a boundary, conserved totals without invented sub-hourly values,
exactly complete lookbacks, leap/month/year boundaries, old/recent gap flags,
group-grid rejection, train/serve/hindcast equivalence and old/new score identity.
Test display DST changes do not move the fixed hydrological grid. Check historical
Kathmandu source-date conversions only for periods actually admitted to evaluation.

Deferred: Swiss cutover/retrain and Plan 263 implementation; civil-calendar FI
extensions; new model families; broad temporal-support metadata backfills; automatic
recomputation infrastructure; load/concurrency hardening; warning publication,
protected backup commissioning and email warnings. Existing security/provenance
rules remain in force; deferring hardening does not authorize removing them.

```json
{"M0":{"status":"COMPLETE","depends_on":[]},"M1":{"status":"IMPLEMENTED","depends_on":["M0"]},"M2a":{"status":"DRAFT","depends_on":["M1"]},"M2b":{"status":"DRAFT","depends_on":["M2a"]},"M2c":{"status":"DRAFT","depends_on":["M2b"]},"M2d":{"status":"DRAFT","depends_on":["M2c"]},"M2e":{"status":"DRAFT","depends_on":["M2d"]},"M3a":{"status":"DRAFT","depends_on":["M2e"]},"M3b":{"status":"DRAFT","depends_on":["M3a"]},"M4":{"status":"DRAFT","depends_on":["M3a"]},"M5":{"status":"DRAFT","depends_on":["M4","M3b"]}}
```

## Review state

Independent Codex repository review and Claude bounded-public-text review completed
17 Nov. Both permit M0 after tightening scope/outcomes; neither approves implementation
yet. The orchestrator initially authorized only M0 investigation, subsequently
completed after resolving the source/model questions. M1 has separate plan and
patch review records below; M2 onward remain DRAFT. The source/model table and owner's
UTC-training correction are investigation results, not independently approved code
or an approval to change the model time basis.

Folded in: artifact gate (254 T5), selected-weather/Recap/receipt assumptions,
nonzero-phase gap and rating validation, interval-aware completeness/reference
handling, target aggregation, explicit skill-publication semantics, isolated pilot
stores, runtime group checks, smaller assembly slices and separate cutover/API work.
Interpretation: 267's cited bundling restriction concerns the Swiss cutover; preserve
that and require one coordinated Nepal switch, rather than adding a Swiss rollout
as a Nepal prerequisite. Review examples alleging all gaps would be filled or an
end-stamp necessarily admits partial data were broader than the code proves; the
concrete wrong-anchor and delayed/missed completeness defects are covered instead.
Full reports remain local. Source/model investigation is now resolved; implementation
and replacement-artifact qualification remain required for dependent scopes.
Final targeted Codex check after these edits found the four material findings
resolved, no circular activation gate, no implementation approval leakage, and
confirmed the Swiss-only interpretation of 267's cited bundling rule.
