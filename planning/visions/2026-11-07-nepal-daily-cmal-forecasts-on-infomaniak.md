# Nepal daily CMAL forecasts on Infomaniak

## Outcome and completion

Operate `cmal_small` on the existing Infomaniak Nepal staging server, using real
Nepal observations and weather inputs, and make its results available through the
existing SAPPHIRE API. This is development/staging, not a production deployment.
The owner wants usable forecasts immediately and a working demonstration on
**19 November 2026 at 08:00 CET (07:00 UTC)**.

Attempt the existing **21 Nepal stations**. Success requires **at least 10 stations**
to produce usable daily forecasts; 21 is an ambition, not a hard acceptance boundary.
Station-specific missing data or other genuine limitations may exclude stations.
Retain successful results and report each failure with its reason. Partial success
is an intermediate milestone, never a reason to hide omitted stations.

The first operational milestone is one normal server run with persisted forecasts
for at least 10 stations. Completion requires at least 10 successful stations on
**two consecutive automatically scheduled daily runs**, with each day's forecasts
available through the API by **07:00 Nepal Standard Time (Asia/Kathmandu,
UTC+05:45), equivalent to 01:15 UTC**. The successful station sets need not be
identical. A manual run is not proof of automatic daily operation.

Forecasts must be finite, have the configured model horizon and correct station,
issue-time, units and provenance, and remain distinguishable from observations.
Numerical execution and honest presentation are acceptance criteria; proving
hydrological predictive skill is not a gate for this development demonstration.

## Settled daily weather and missing-data policy

- Issue a **new runoff forecast on that Nepal calendar day**. An older runoff
  forecast does not satisfy the new daily-success count, even if it remains useful.
- Use the latest available weather run that covers the **entire required ten-day
  forecast window**, not merely a weather product whose nominal duration is ten days.
  Retain its original weather-cycle timestamp separately from the runoff issue time.
- Weather may be up to **36 hours old at runoff issuance**. Apply the age check
  honestly at issuance; do not backdate execution or relabel a source cycle to pass it.
  This explicitly permits an eligible previous-day weather cycle at the Nepal deadline.
- If no qualifying weather run or other required input is available, retain the prior
  runoff forecast with its original date and report a missed daily run. Do not count
  it as today's success. Fewer than 10 successful stations is a missed daily target.
- Preserve full-window coverage, rainfall interval semantics, temperature aggregation,
  provenance and strict weather-input checks. Do not shorten the horizon, stitch
  incompatible cycles, invent members or fill missing rainfall/temperature to meet time.
- Pass missing discharge (`Q`) through unchanged where the adopted Aquacast declaration
  permits it. Do not fabricate discharge, bypass FI, change the checkpoint, or treat
  finite output as evidence of predictive skill. Preserve availability warnings.

The 36-hour policy is an owner-confirmed change to the current selection rules,
not permission to disable general freshness validation. Implement it explicitly in
all relevant candidate-selection and execution-age gates while preserving other
routes and their established behavior. Choose the simplest adequate scoped mechanism.

## Repository and deployment starting point

As verified on 7 November 2026:

- Weather integration PR470 and minimal Aquacast adoption PR471 are merged.
  The qualified starting source is `20444391946d6baae963ab3fc1afaf33218558eb`,
  whole tree `c8fc310d2d3257cd779a4d118e9bb6aa1ec976d9`, release target `0.1.1116`.
  Its exact-merge CI run `42369247180` passed. Recheck current main and release state;
  a target, merged commit, CI build or local qualification image is not a published
  canonical release or a delivered-image receipt.
- This source pins Aquacast to immutable commit
  `b57a1872188e87e443fed2aa960b0885d4d537e2` (package `0.1.377`), with FI `0.1.20`.
  The owner authorized temporary staging adoption while Aquacast PR144 remained
  unmerged. Do not claim upstream model-owner approval or release from that adoption.
- The minimal five-file adoption passed 169 focused tests with no skips and independent
  Claude/Codex review. An installed Linux/amd64 image, using the real checkpoint and
  normal `ForecastInterfaceAdapter.assess_group_inputs` then `predict_batch`, admitted
  6, 7, 29 and all 30 missing-Q values with finite, repeatable results. Complete-input
  output remained unchanged; all four missing past/future P/T controls were refused.
  This is bounded offline numerical/interface evidence, not a live 21-station result.
- `DATA_AVAILABILITY` is currently observed through the existing structured boundary
  warning event, not a field on `ForecastEnsemble`. Do not describe it as a persisted
  forecast/API flag without evidence. Keep provisional and missing-data limitations
  visible through the existing appropriate API surfaces and the dashboard handoff.
- The latest read-only server receipt found release `0.1.1104`, schema `0080`, and
  Aquacast `0.1.376`, with API, PostgreSQL and Prefect healthy. It found no active
  flows at that read. Caddy had an older unhealthy status, not a newly demonstrated
  regression. All these host facts require fresh verification before mutation.
- Existing station records, observations, rating material and the approved CMAL
  artifact are already present. Native station ingestion currently covers 17 stations;
  the 21 weather/station target does not itself authorize expanding that ingest scope.
  Verify actual usable inputs rather than treating the project as a fresh onboarding.

Current selected-route code requires an explicit UTC-midnight issue. The CMAL
configuration has a 30-day lookback and ten-day forecast. Qualified IFS 00 UTC runs
support 360 hours; 06/12/18 UTC runs support only 72 hours and cannot supply this
full horizon. The current default candidate walkback is 18 hours and a separate
execution-clock freshness limit also exists. Therefore the qualified source alone
is **not** proof of the newly agreed 36-hour policy or the 07:00 Nepal deadline.
Provider availability of complete past inputs and ten-day P/T by the deadline, and
actual server runtime, remain to be established. Do not promise them from offline tests.

Useful starting points are `flows/run_forecast_cycle.py`,
`flows/operational_weather_prepare.py`, `flows/operational_weather_refresh.py`,
`services/track_resolution.py`, `services/recap_daily_weather.py`,
`adapters/recap_gateway.py`, `models/aquacast/configs/cmal_small.yaml`, the selected
weather sections of `docs/spec/types-and-protocols.md`, and current CI/CD,
orchestration and security standards. Paths above are under `src/sapphire_flow/`
unless explicitly rooted in `docs/`.

## Milestones and observable evidence

### 1. Deploy the qualified foundation

Publish and deliver the qualified release through the existing supported release
path, then adopt it on the existing server. Retain source/image/receipt identity and
one designated deployment operator. Verify a sufficiently fresh recoverable database
restore point before migration; the recorded pre-1104 backup is not automatically
sufficient for this change.

Apply the actual migration chain from the observed schema, including `0081` operational
weather role/cache and `0082` immutable forecast evidence marker, and normal role
bootstrap. Verify essential actual worker/API permissions. Keep the new weather
selector off during image/schema/configuration checks. Install the reviewed NHS
forecast overlay/checker through the existing procedure, mount the provider file
read-only before selecting it, and verify the actual forecast worker's configuration
and principal. Preserve native ingestion and existing data.

Evidence: exact running release/image and schema, essential service health and
configuration readback, recovery/rollback reference, and preserved data/ingestion
state. A successful upload alone does not complete this milestone.

### 2. Verify the existing Nepal stations' data readiness

Attempt readiness for all 21 using current supported procedures and real data.
Check station identity, approved model assignment/artifact, statics, observation
coverage, rating-derived discharge where available, weather bindings and complete
required P/T windows. Record eligible stations and specific blockers. Reuse known
observations and rating custody; do not repeat exhausted history recovery or invent
historical discharge. Missing-Q capability does not waive unrelated input requirements.

Evidence: a station-by-station readiness/result inventory, actual input coverage and
provenance, and sufficient eligible stations to attempt the at-least-10 objective.

### 3. Produce the first usable forecasts immediately

Run the existing native provisional group forecast pipeline on Infomaniak using the
approved `cmal_small` artifact, real inputs and an honest explicit issue time. Preserve
all successful stations and report failures. No synthetic demo data or direct-model
shortcut may replace the normal operational path.

Evidence: run/attempt and persisted forecast identifiers for at least 10 stations,
finite forecast values with the configured horizon, actual input/source lineage,
quality/provisional distinctions, and authenticated API readback. Run this milestone
as soon as the foundation and inputs permit; do not hold it for the recurring schedule.

### 4. Implement and qualify the agreed daily deadline

Make the scoped 36-hour weather-selection policy explicit and verify its interaction
with the full ten-day window and existing UTC issue/claim rules. Establish provider
input readiness and measured end-to-end runtime, then choose a schedule that produces
new same-day forecasts by 07:00 Nepal time. Preserve honest weather-cycle and runoff
issue timestamps; changing schedule labels alone is not a solution.

Evidence: correct selection/refusal around the agreed age and coverage boundaries,
normal scheduling/configuration readback, and a bounded operational run demonstrating
the deadline. If unavailable inputs make the deadline infeasible, report the concrete
constraint; do not silently substitute an older runoff forecast or weaken the policy.

### 5. Prove recurring operation and API/dashboard delivery

Observe two consecutive automatically scheduled daily runs meeting the at-least-10
station threshold and deadline. Keep failure and freshness information accessible.
Verify the existing API serves both BAFU and Nepal observations and forecasts,
including Nepal measured water levels and available provisional discharge with clear
quantity, units and status distinctions. Missing observations remain gaps.

The dashboard is developed in another repository and is **not this work's implementation
responsibility**. Reuse existing API routes and conventions, preserve BAFU behavior,
and give its owner concrete station/forecast identifiers, endpoint/query examples,
units, timestamps and provisional/freshness semantics. Verify API responses; coordinate
and record the owner's/dashboard session's confirmation that actual observations and
forecasts are visible. Do not claim a chart was verified from HTTP 200 alone, or create
a parallel dashboard-specific API merely to avoid using the established contract.

Evidence: two daily run receipts, per-day successful station sets and deadline times,
API readbacks for observation and forecast surfaces, and the external dashboard handoff
and visibility confirmation. A pending dashboard confirmation is an explicit external
dependency, not permission to modify that repository from this effort.

## Ownership boundary: the bipad session

The separate **bipad** agent session owns work on station-onboarding process
documentation. This vision **does not authorize changes to station-onboarding
procedures, their documentation, or that session's implementation/branches**.
Do not redesign, duplicate or replace its workflow as part of Nepal deployment.

This effort may inspect readiness and use already-onboarded stations through existing
supported processes. If a station needs an onboarding change or a new procedure,
record the prerequisite and coordinate it with the bipad session and owner. Do not
silently implement the change here. Such a station may remain explicitly blocked
while the other eligible stations progress toward the at-least-10 objective.

## Delivery constraints and exclusions

- Use the shortest supported release/deployment path. Reuse qualified work, existing
  adapters, native flows, tests and delivery mechanisms; do not restart weather/AQ
  reviews or duplicate full suites without a material change or required gate.
- Retain required independent reviews, exact-head CI, release/image custody, essential
  permission/configuration checks and database recovery safeguards. The request for
  speed does not authorize bypassing repository protection or scientific contracts.
- Keep one live operator; reconcile any other deployment session before host writes.
  Preserve unrelated local work, native17 ingestion, cursor/capture/observation data,
  ratings, approved artifact/configuration, volumes and unrelated deployment settings.
- Do not change station-onboarding procedures, implement the external dashboard,
  retrain or replace the model, fabricate Q/weather, bypass FI, add new alert/publication
  eligibility, expand provider scope, or treat staging authorization as production authority.
- Existing provisional-rating restrictions remain. No public forecast publication or
  alert activation is implied by making review/dashboard data available.
- Keep credentials, private checkpoint/input assets and raw sensitive logs out of Git
  and PRs. Report only sanitized identity, numerical and operational evidence.

This is a standalone vision, not an Effort/Program ticket or a replacement for current
repository safeguards. Implementation should reconstruct current Git/GitHub and host
state, reuse the completed source qualification, and track the milestones above.
