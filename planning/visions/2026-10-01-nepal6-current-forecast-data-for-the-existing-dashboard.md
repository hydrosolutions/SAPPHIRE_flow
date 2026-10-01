# Nepal6 current forecast data for the existing dashboard

Program: https://github.com/hydrosolutions/SAPPHIRE_flow/issues/352
Effort: https://github.com/hydrosolutions/SAPPHIRE_flow/issues/354

## Outcome and ownership

By 2026-10-07, make current SAPPHIRE forecasts for DHM stations **447, 450, 604.5,
647, 670 and 684** available to the existing invited-access Nepal6 dashboard.
Keep forecast production and data access running after that date until the owner
explicitly retires the demonstration. This is a demonstration, not an operational
warning service. Target all six stations; report actual coverage rather than
claiming six-station success from a smaller working subset. The owner-approved
expired-rating-curve test mode below permits explicitly invalid pathway tests;
it does not establish scientifically valid or operationally usable forecasts.

**SAPPHIRE_flow owns backend forecast readiness and data availability only.**
SAPPHIRE-flow-map agents own all dashboard implementation, model selection,
refresh behaviour and display. Coordinate the data contract and end-to-end
acceptance with those agents. Do not implement dashboard changes in this Effort,
rebuild its UI, or plan a replacement model selector. The owner confirms that the
existing dashboard has a selector and fetches all available models.

The map repository is `~/Documents/GitHub/SAPPHIRE-flow-map`. Necessary backend
work belongs here; necessary consumer changes belong to its agents. A consumer
integration gap is a coordination dependency, not permission to take over the UI.
Neither the Swiss Effort #353 nor CI-speed implementation is a prerequisite.

## Confirmed forecast and freshness requirements

- Make **all available forecast models** accessible for each Nepal station, not
  one hard-coded product. The primary target is the already-onboarded `cmal_*`
  machine-learning family. Reuse those models and artifacts; do not treat their
  existence as proof of Nepal assignments, usable inputs or successful forecasts.
- Existing simple regression models in this repository are acceptable for initial
  delivery and can be exposed alongside CMAL. A regression-only initial result is
  not rejected solely for lacking CMAL, but CMAL remains an explicit target and
  any missing CMAL coverage must remain a reported gap, not a silent scope cut.
- On a dashboard reload, the backend must make the latest eligible **stored**
  forecasts available for each station/model. A newly persisted forecast must not
  wait for a separate periodic publishing batch to become readable. Reading does
  not trigger model training or forecast production.
- This reload requirement is the owner's clarification of the Program's automatic
  delivery wording: backend production continues automatically; live push or
  browser auto-refresh while a page stays open is not an added acceptance gate.
- Preserve each model's actual time resolution, valid times, horizon and issue time.
  The earlier suggested daily/five-day product was not adopted. Different models
  may complete at different times; a slow or failed model must not hold back others.
- Preserve forecast identity and selection semantics, including version/supersession,
  applicable publication policy and QC state. “Latest” does not authorize exposing
  withheld or withdrawn results or presenting failed/rejected output as valid.
  Retrieval must distinguish the latest stored result from a genuinely current one.
  Explicitly invalid test forecasts use the separate access/selection path described
  below; their inclusion must not weaken ordinary valid-result selection.

The exact routes and transport are engineering decisions grounded in the existing
consumer. Agree and test a contract with the map agents for the six-station roster,
model inventory and availability, forecast values and units, model/forecast
identifiers, issue and valid times, available uncertainty representation, QC and
freshness states, and safe station/model failure information. Supply existing
station/basin metadata needed by that contract. Do not fabricate uncertainty,
fill missing values with invented flows, or claim unmeasured skill.

## Owner-approved expired-rating-curve test mode

On 2026-10-01 the owner selected BIPAD for operational observations and confirmed
that no updated rating curves are available. To test the end-to-end pathway, the
owner explicitly approved using the **newest available curve for the same gauge**
and marking **every resulting forecast invalid, not for operational use**. This
is a bounded scientific test exception, not a claim that an expired curve is valid.
It amends the initial discharge-readiness assumption without changing backend-only
ownership or granting new observation-publication permissions.

Reuse the implemented DHM/BIPAD adapter (`adapters/dhm.py`, archived Plan 300) and
existing onboarding/conversion machinery where suitable. The adapter supplies
water level in metres, not discharge. Station-to-API bindings must be explicit and
verified; do not infer authoritative gauge identity from a similar place name or
use a nearby gauge's curve. BIPAD selection is explicit, not an automatic fallback
from another host. Respect bounded requests, station-specific failures and the
existing QC and activation prerequisites. Do not mark stations operational merely
to bypass those prerequisites.

The approved exception has these mandatory boundaries:

- Choose the newest curve belonging to the same confirmed gauge under the existing
  version/chronology contract. Do not borrow a curve from another gauge, invent one,
  or silently resolve conflicting versions. Preserve its original validity dates;
  do not extend them, relabel the curve current or falsify input timestamps.
- Establish compatibility between the live water-level reference and the curve's
  reference before conversion. Unknown datum compatibility remains a hold even
  in this test mode: report it per station and escalate to the owner for verified
  reference information; never assume a datum to unblock the test. This approval
  does not waive units, station identity, datum, curve-domain or other conversion
  safety checks.
- Record the original measured-level lineage and the exact curve identity/version
  used for each derived discharge value. Keep measured levels, restricted historical
  discharge and provisional derived inputs distinguishable. Do not overwrite the
  original observations or relabel provisional discharge as directly measured.
- Propagate the expired-curve test provenance and **invalid** status from derived
  inputs through every downstream forecast that uses them, including group and
  combined products where applicable. A fresh issue time, passing numerical QC,
  aggregation or model combination must not erase that invalidity. Preserve the
  full affected lookback lineage rather than inspecting only the latest reading.
- Make invalid test forecasts available separately to authorized Nepal viewers,
  with machine-readable invalid status and a safe reason identifying expired-rating
  test use. Ordinary latest-valid selection, valid last-good results and alerts must
  not consume or be replaced by these outputs. Keep invalid last-good results
  explicitly invalid too. Do not expose raw restricted measurements, curve points,
  coefficients or sensitive QC/diagnostic values through the test read path.
- Exclude provisional derived discharge and invalid forecasts from training,
  retraining, calibration, hindcast/skill evaluation and decisions that assert
  operational validity. Reuse legitimate historical training data and compatible
  existing artifacts. Do not pass fabricated discharge, bypass ForecastInterface,
  or misrepresent a new invalidity flag as permission to violate a model's contract.
- SAPPHIRE_flow implements and verifies backend lineage, invalidity propagation,
  exclusion and authenticated data availability. SAPPHIRE-flow-map agents own
  the visible invalid/not-for-use presentation and any consumer wiring; no UI code
  belongs here. Coordinate explicit invalid-result display before invited-viewer
  use, not merely an easily missed generic demo notice.

Current BIPAD availability and the historical curve store do not guarantee enough
recent compatible discharge history for every model. Determine actual feed history,
QC, temporal aggregation and model lookback coverage; report insufficient combinations
as unavailable rather than filling missing data or weakening the model contract.
CMAL remains the target and existing regressions remain acceptable for initial
pathway delivery. Report **invalid test-pathway coverage separately from valid
forecast coverage**. A successful invalid test demonstrates plumbing, not forecast
validity or skill. Normal validity can only follow the ordinary data/model gates;
there is no automatic validity upgrade from this exception.

## Partial failures and ongoing operation

Keep healthy station/model combinations available when another combination fails.
Maintain an explicit account of the expected six stations, available models,
successful forecasts and missing combinations; missing inputs must not silently
remove stations or whole groups from the reported coverage.

Retain last-good results on forecast-production or refresh failure and preserve
original issue times and gaps. Provide enough backend information for the map
agents to distinguish current, stale, unavailable and failed results. Do not make
an old forecast look current by changing its timestamp or treating a successful
HTTP response as fresh forecast production. Coordinate consumer-side last-good
behaviour with the map agents; this repo does not implement it.

Use the existing operational monitoring and operator handoff for continued running,
not a new unrelated observability platform. Delivery evidence must name remaining
station/model failures and operational limits. There is no promised warning-service
SLA, no public-access expansion, and no automatic stop on the demonstration date.

## Data protection and authority

Invited access only. Keep Nepal and Swiss credentials and data separate, do not
expose the office network, and keep backend credentials out of browser code,
repositories, tickets and reports. Reuse the existing scoped reader/authentication
contracts and verify that unauthorized and out-of-scope callers cannot read data.

The owner decision in
`docs/decisions/2026-09-30-dhm-data-hosting-assumption.md` remains the authority for
password-protected hosting and display. It is an **owner assumption, not DHM
consent**. Preserve the restriction on delivery-tagged observations for consumer
and reviewer access, including values recoverable through QC details or derived
output. This vision grants no bulk-download, public-release or derived-metric
publication permission. A DHM objection or changed conditions must be handled as
that decision record requires; page text must not claim DHM endorsement.

Respect the recorded backup prerequisites before moving restricted data onto the
cloud host: the backup follow-on must be planned first, with the distinction
between operational backup replication and protected preservation retained.
Coordinate Plans 208/340 where applicable; do not import their unrelated scope or
pretend a local dump proves off-box recovery. Reuse the existing Nepal staging
host, not a duplicate host. No new spending, production deployment, PR merge, or
unbounded data transfer is authorized by this vision. Staging work follows the
repository's existing orchestration and owner authority.

One clear demonstration notice per site remains the agreed presentation policy;
its implementation belongs to the map agents. Analysis panels, historical skill
research, new dashboard features, new observation-publication permissions and
operational alerting are outside this Effort.

## Reuse and reconcile existing work

Read current source and deployment evidence before deciding what remains. A plan's
status or merged PR is not proof that its acceptance ran on the host.

- **Plan 268 / PR #332:** restricted historical discharge import and QC exist.
  Verify retained import/QC evidence and station rows before any repeat import;
  do not rerun replacement merely to start this Effort.
- **Plan 143 / PRs #344 and #347:** discharge-first station/basin/history readiness
  exists. Inspect the actual six-station package, bindings and qualified history.
  It deliberately did not prove model training, activation or live forecasts.
- **CMAL integration:** inspect the current model registry, artifacts, station/group
  assignments, static attributes and supported forcing. Plans 152, 155, 262, 261
  and related model-onboarding work are evidence to reuse, not a new mandatory
  whole-plan dependency tree. Respect ForecastInterface throughout; fix a model
  that violates it or resolve a genuine contract gap upstream, never bypass it.
- **Plan 511 / merged PR #351:** the Infomaniak staging-host deployment support is
  merged. Verify the actual deployed release and services and coordinate its owner.
  Do not replace the selected host with the map's older Hetzner proposal.
- **Plans 401/402/404 / PRs #320/#328/#330:** scoped authentication and reader/QC
  capabilities already exist. Reuse their contracts without making analysis views
  a deadline blocker.
- **DRAFT Plans 516 and 049:** useful contract/access investigations, not executable
  instructions here. Plan 516's one-model-per-station selection, synthetic fallback,
  analysis-first prerequisite and separate publishing assumptions do not satisfy
  the confirmed all-model, current-on-reload outcome unchanged. Do not assume the
  proposed region-bundle routes are the only valid transport.
- The map's older deployment/export documents contain November deadlines and
  synthetic/analysis scope. Preserve useful contract facts, but coordinate their
  reconciliation with the map agents against this approved October outcome.

### Bounded discovery evidence, 2026-10-01

The local map checkout was on `feat/qc-chart-zoom`. `FlowMap.tsx` contains an
“All models” selector and forecast loading; the local Nepal6 build alias points
to `RegionDashboard.tsx`, which reads staged bundles. This discrepancy requires
consumer-owner reconciliation, not a conclusion that a selector must be built or
that local source proves the deployed view. Preserve the owner's instruction to
reuse existing dashboard capabilities.

Unauthenticated HEAD requests to
`https://sapphire-flow-map-nepal6.hsol.workers.dev/` and
`/data/nepal6/region.json` returned 401 with `Cache-Control: private, no-store`.
These are narrow access checks, not an authenticated browser or full security audit.
A GET to `https://nepal-staging.hydrosolutions.ch/api/v1/health` could not connect to
port 443 from the discovery environment. Deployment and six-station forecasting
therefore remain unverified, not proven absent. The checked-in Nepal cloud overlay
disables weather forecasting pending its Nepal feed setup. Re-measure these facts
before delivery; no live station/model inventory was obtained during discovery.

Subsequent read-only implementation investigation on 2026-10-01 reached the existing
shared Mac-mini staging stack, not the Infomaniak Nepal host, through API and SSH
and verified forecast-worker package `0.1.1039`. These findings must be re-measured
on the intended Nepal cloud host before claiming deployment there; moving restricted
data still requires the recorded backup prerequisites. All six DHM stations
exist under `chwrr` but remain onboarding, without basin bindings, forecast targets,
active station/group model assignments or stored forecasts. QC-passed historical
manual-import discharge ends in 2019 for five stations and 2023 for station 450.
All six have historical rating curves, but none is valid on the inspection date;
level units/reference metadata remain unset. Only aggregate coverage and validity
metadata were queried, in read-only transactions; no restricted values were read.
The public BIPAD metadata endpoint was reachable and supplied candidate entries for
all six gauges; these are not yet confirmed station bindings or datum evidence.
Existing adapter/config/ingest tests passed (63 tests). These checks establish reuse
opportunities and remaining prerequisites, not operational activation or model readiness.

## Evidence of delivery

The implementing agent and map agents must jointly establish the evidence below.
Keep valid forecast coverage and invalid test-pathway coverage separate throughout:
zero valid current coverage must be reported as zero. The traceability, freshness,
access, isolation and continuity checks apply to each delivered class, but invalid
results can establish only the approved test-pathway behaviour, not valid forecast
coverage. Item 6 adds the mandatory checks for that exception.

1. A measured six-station by model account: current artifacts/assignments and inputs,
   successfully stored forecasts, CMAL progress, regression output where used, and
   explicit failures/exclusions. Use real current forecasts, never synthetic fixtures
   as proof of delivery. Historical or climatological output alone cannot stand in
   for the requested current weather-driven forecasting outcome.
2. Authenticated backend reads expose all available eligible model results for the
   authorized Nepal stations with traceable IDs, actual issue/valid times, units,
   shape and status. Unauthorized and out-of-scope reads fail without leaking
   restricted observations or sensitive diagnostic values.
3. Across at least two production cycles, newly stored results become readable
   without an independent publishing-batch delay. With the map agents, reload the
   invited-viewer dashboard after a new result is stored and trace the displayed
   result to the backend forecast/model identity and issue time. No manual data
   export or redeployment is needed between these observations.
4. A failed station/model does not block healthy results; a missed cycle or failed
   refresh retains clearly marked last-good data or reports unavailability when
   no prior result exists. Verify backend semantics here and browser behaviour
   with the map agents.
5. Automatic production remains configured beyond October 7, with an operator
   handoff for access, monitoring, recovery and deliberate retirement under the
   existing authority and data restrictions.
6. For the expired-rating test path, verify exact same-gauge curve lineage,
   unchanged validity dates, compatible level references and invalidity propagation
   through the complete input lookback and downstream products. Prove invalid
   outputs cannot replace valid latest/last-good selection, trigger alerts or enter
   training/evaluation. With the map agents, verify separately selected invalid
   results are unmistakably labelled invalid/not for operational use. Count this as
   test-pathway evidence, never as valid forecast coverage or skill.

Record concrete coverage and limitations in the delivery evidence. Backend contract
completion and consumer/browser verification are distinct facts: a downstream
integration gap does not authorize dashboard implementation here, but it does mean
the end-to-end Effort is not yet verified delivered. Do not mark it delivered or
landed from code merge, an HTTP health check or model onboarding alone.

This document records the approved outcome, not an implementation plan or permission
to execute DRAFT plans. Follow the repository's planning, independent review,
readiness and owner-controlled merge requirements during implementation.
