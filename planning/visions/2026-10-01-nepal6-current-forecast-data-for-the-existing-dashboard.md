# Nepal6 current forecast data for the existing dashboard

Program: https://github.com/hydrosolutions/SAPPHIRE_flow/issues/352
Effort: https://github.com/hydrosolutions/SAPPHIRE_flow/issues/354

## Outcome and ownership

By 2026-10-07, make current SAPPHIRE forecasts for DHM stations **447, 450, 604.5,
647, 670 and 684** available to the existing invited-access Nepal6 dashboard.
Keep forecast production and data access running after that date until the owner
explicitly retires the demonstration. This is a demonstration, not an operational
warning service. Target all six stations; report actual coverage rather than
claiming six-station success from a smaller working subset.

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

The exact routes and transport are engineering decisions grounded in the existing
consumer. Agree and test a contract with the map agents for the six-station roster,
model inventory and availability, forecast values and units, model/forecast
identifiers, issue and valid times, available uncertainty representation, QC and
freshness states, and safe station/model failure information. Supply existing
station/basin metadata needed by that contract. Do not fabricate uncertainty,
fill missing values with invented flows, or claim unmeasured skill.

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

## Evidence of delivery

The implementing agent and map agents must jointly establish:

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

Record concrete coverage and limitations in the delivery evidence. Backend contract
completion and consumer/browser verification are distinct facts: a downstream
integration gap does not authorize dashboard implementation here, but it does mean
the end-to-end Effort is not yet verified delivered. Do not mark it delivered or
landed from code merge, an HTTP health check or model onboarding alone.

This document records the approved outcome, not an implementation plan or permission
to execute DRAFT plans. Follow the repository's planning, independent review,
readiness and owner-controlled merge requirements during implementation.
