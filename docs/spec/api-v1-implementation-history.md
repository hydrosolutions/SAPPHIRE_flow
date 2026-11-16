# API implementation history

Historical material separated from the reference on **16 November 2026**. These
passages preserve decisions, compatibility evidence and rollout context; their
release claims describe when they were written, not what a server provides now.
Use the [guide](../api/README.md) for usage and the [maintainer reference](api-v1-review.md)
for active contracts. Future history belongs in plans, PRs and commits; do not
append current endpoint instructions here.

## Original purpose and export (Plan 402)

**Consumer page for the SAPPHIRE-flow-map project** (and any other reviewer
dashboard). The map reads authenticated `/api/v1` routes, including
`GET /api/v1/forecast-lab/snapshot` for archived BAFU forecasts and ordinary
SAPPHIRE comparisons. Its `forecast-lab-snapshot/v2` wire format is unchanged;
see `docs/spec/forecast-lab-snapshot.md`. It does not replace authoritative
evaluation selection, QC diagnostics or skill routes.

The authoritative shape for the dashboard routes is the committed,
drift-tested contract: `docs/spec/api-v1-map.openapi.json`
(`api/map_contract.py::build_map_openapi()`, `tests/unit/api/test_map_contract.py`).
This page explains conventions the schema alone does not — auth, error
shape, query conventions, and the QC/skill semantics a reviewer needs to
interpret what is served.

## Historical DHM QC activation context

The
station view mirrors **scheduled ingest only**: history checked at
onboarding used the network thresholds; history checked by a delivery
import (the six DHM stations, once Plan 268 T7 has run) used their declared
ceilings — the view's current limits are **not** necessarily the ones
behind that historical data's flags. Scheduled DHM ingest judges **water
level only** (touchpoint-maps.md) — for the six DHM stations the station
view shows the network limits (`station_qc: "not_judged"`) until Plan 300's
activation follow-on promotes them, and even once operational, scheduled
ingest there judges water level only — discharge ceilings shown after
promotion apply to **no scheduled reading**; discharge QC on those stations
comes only from Plan 268's import until a rating-curve path exists.

## Historical in-sample evaluation statement

Today every active artifact's eval window is inside its training period —
every served score is in-sample; the map shows these as fit scores and
ranks nothing.

## Operational weather contract 2.8

Forecast detail adds optional, nullable `weather_use` with station-scoped past and
future freshness and source-run facts. Legacy absence stays unknown; this does not
change publication, visibility, rating lineage, QC or prediction acceptance.
The new `WeatherSourceRunResponse` component describes source/run pairs. Existing
`WeatherSourceResponse` and `StationDetail.weather_sources` keep their original
binding schema and reference. Paths and Swiss observation-history contract 2.7
behavior remain unchanged. This additive contract version is separate from the
package release target. Notify the map consumer before operational deployment;
this change does not authorize deployment or activation.

## Provisional read regression and compatibility evidence

Provisional SQL/decode/fetch failures are translated only at their boundary.
Unexpected TypeError/KeyError programming faults remain sanitized500. A fixed
structured diagnostic records only operation/category/whitelisted exception class
and validated SQLSTATE when available; no raw exception, SQL, IDs or evidence.
Existing positional Python route callers retain their dependency order; the new
purpose and reader parameters are keyword-only.

Real bearer-token regression tests assert consumer null and reviewer false for
standard-only station fixtures, then compare every remaining response field.
Other principal routes retain their existing whole-response equality checks.

## Migration 0084 rating-history validation optimization

Migration 0084 introduced reuse of an already validated exact frozen
curve/reference text pair within one request, reducing repeated evidence traversal.
It did not change evidence, authorization, discharge values or public errors.
Current validation rules and budgets remain in the
[rating-history contract](api-v1-review.md#forecast-specific-rating-derived-input-context-contract210).

## Rating-input history deployment statement before qualification

The original contract 2.10 text said:

> This backend contract still needs reviewed deployment before any frontend reader can use it.

That statement describes the pre-deployment handoff. See the dated
[compatibility note](../api/README.md#deployment-compatibility) for the verified
baseline; source availability is not deployment approval.

## Contract additions through 2.11

The original contract started at 1.0. The owner decision of 2026-09-26 made
change classification a review judgement, with CI enforcing a greater version.
The current rule remains in [Versioning](api-v1-review.md#versioning-d14).

- 2.3 exported the existing forecast-lab snapshot route; its v2 wire format did not change.
- 2.4 added water-level selection to bulk latest observations.
- 2.6 added Swiss overview compact CMAL series; 2.7 added the disabled Swiss
  mixed-resolution history build. The old overview introduction still named 2.7
  after later additions; this was a stale document version claim.
- 2.9 added explicit provisional forecast reads and nullable station availability.
- 2.10 added forecast-specific frozen rating-derived daily input context.
- 2.11 added network overview. Threshold import and alert activation stayed separate.

These implementation steps do not establish activation in any deployment.

## Nepal discharge-history deployment: 16 November 2026

Nepal MVP release **v0.1.1139** (source `009deaef45f435346ed3fa11869be4e29f1655cc`)
deployed contract **2.13** and migration **0085**. Exact merged-source CI passed;
release image checks, private delivery and native server probes passed. The API
was given read-only access to the workers' existing 17 rating-station bindings;
without that deployment setting, stored conversions were held as `not_configured`.

Live verification covered 42 sequential requests: native and hourly reads for all
21 stations over 9–16 November, 18:00 UTC to 18:00 UTC. All succeeded. Hourly
statistics matched native samples, numeric derived points referenced frozen curves,
and 14 station histories contained discharge. Stations 447, 620 and 647 had no
source observations in that window; the four onboarding stations were also empty.
These are dated observations, not promises of current availability or freshness.
The slowest request in that pass took 1.185 seconds and the largest response was
804,907 bytes. This was not a concurrent-load test or live Swiss qualification.

All 17 existing forecasts were unchanged, and worker pools resumed with their
prior schedules. The next naturally scheduled forecast had not run at verification
time. Overview integration remains a follow-up; the new route does not populate
the overview's discharge block. See the [client guide](../api/README.md#shared-nativehourly-discharge-history)
for the maintained request and interpretation rules.
