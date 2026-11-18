# Event review API — draft contract

Implementation status (M2, 18 November 2026): `/review-options` is implemented in
the event-review branch with generated OpenAPI contract 2.14. It is not deployed.
The series and period-skill sections below remain proposals for later milestones.
Options accepts no query parameters; its wire schema is authoritative in
`api-v1-map.openapi.json` (`ReviewOptionsResponse`, `ReviewSourceResponse`).

Status: implementation design approved for options and series; options implemented
on the branch only. Nothing deployed. Period skill remains a future proposal. See
[delivery plan](../plans/2026-11-17-event-review-delivery.md).
This file is not a replacement for generated OpenAPI. Publish an immutable generated
contract and validated examples with the first implemented slice.

## Shared semantics

- Reviewer/admin only; existing tenant/station scope; outside scope is 404.
- Discharge in m³/s; UTC RFC3339 instants. Required `start` inclusive, `end`
  exclusive, maximum 366 elapsed days. No future end for historical review.
- `lead_hours` is an exact elapsed offset between issue anchor and valid timestamp.
  Display definition: “Lead is the elapsed time from the forecast issue anchor to
  the valid timestamp. A 48-hour lead uses the issue anchored exactly 48 hours
  earlier. Hindcast issue anchors are retrospective, not forecasts published then.”
- Return valid interval start/end and aggregation meaning where known. If legacy
  support cannot be established, label it unknown and prohibit period scoring;
  a timestamp difference alone does not establish daily hydrological alignment.
- `issued_at` for a hindcast means its historical issue anchor; header `created_at` means
  first storage time and may survive replacement of values. Omit generation time
  when unknown. Do not call either a publication time.

## GET /api/v1/stations/{station_id}/review-options

Return station ID, parameter, available model IDs/names and source options. Each
source option supplies the explicit selection tuple below, kind (`operational` or `hindcast`), forcing
identity (unknown explicitly when absent), artifact ID/hash, model version when
recorded, run ID for hindcasts, cadence, stored `lead_hours`, coverage bounds and
completeness (`complete`, `incomplete`, `unknown`, `absent`) with evidence/reasons.
Lead availability is derived from stored data, not configured maximum horizon.
Coverage bounds do not promise every intermediate date exists. An empty authorized
station returns an empty list with a human-readable no-data reason.

Operational source choices can cover successive artifacts, retaining identity on
each returned segment. Hindcast choices pin a run/artifact; no automatic mixing of
different runs. Source choices use the explicit tuple below; no source-token registry. If stored
artifact identity changes, the old selection fails rather than silently switching.

## GET /api/v1/stations/{station_id}/review-series

Required query: `model_id`, integer `lead_hours`, `start`, `end`, `source_mode`.
`source_mode` is `operational`, `hindcast`, or `operational_with_hindcast`.
Hindcast modes additionally require `hindcast_run_id`, `hindcast_artifact_id`,
`forcing_type`, and `time_step_seconds`, supplied by options. Validate their full
tuple against station/model/discharge; unknown and foreign tuples both return 404.
Verify exact timestamp difference, not only the floored stored lead column.
Source selection identifies operational, a selected hindcast run, or operational
with that hindcast as fallback. Source IDs must belong to the authorized station
and model. Unknown/unoffered leads return 422 with `lead_not_offered`; offered leads
with no data in the selected period return a successful explicit empty/gap result.

Response carries request bounds, units, lead definition, cadence, availability,
and segments. Each segment carries kind, forcing, model/artifact ID and hash,
recorded version, run ID, generation provenance, training period and known hindcast
evaluation coverage. Each point carries valid time/support, issue anchor, original
forecast ID, median, p25, p75, quartile method and state/reason. Segment references
avoid repeating provenance on every point.

Numeric null means unavailable, never zero. States distinguish available, missing,
withheld, and QC-rejected; incomplete is also a source/run coverage state. An
unrecorded failure reason remains unknown. Expected-grid gaps are explicit where
cadence/support is known. Do not fabricate a grid when support is unknown.
Operational and hindcast segments are separate. For the same valid support,
operational selection wins; withheld operational values cannot be filled by a
hindcast. Applicable publication rules also cover derived statistics.

Proposed limits: 2 MiB uncompressed, 8,784 selected hourly points, bounded SQL work.
Never silently truncate; return an explicit response-too-large error with advice
to narrow the period. Exact status/error schema will follow existing API conventions
in the implemented contract. No raw ensemble fan-out to the browser.

## GET /api/v1/stations/{station_id}/review-skill — subsequent slice

Use the same range, model, lead and pinned sources as the series read. Separate
results by kind, forcing, artifact/run and temporal support. Include metric name,
value (nullable), state (`available`, `not_enough_data`, `undefined`, `unavailable`),
reason, matched sample count, expected sample count when known, covered intervals,
evaluation and training periods, observation source/QC policy, alignment method and
computation version. Return no arbitrary zero for undefined metrics.

Proposed initial metrics: median MAE, NSE and percent bias. Sample and completeness
policy must be reviewed before release. These are descriptive period metrics, not
an independent-validation or “best model” claim. No ranking in the initial slice.
Do not compute CRPS from only three displayed quantiles. No model execution or
backfill. Preserve `/stations/{station_id}/skill` as stored skill with its own dates.

## Request accounting

On station change: one small options read. On period/model/lead selection: one
series read, existing observation read(s), and stored skill on request. Period skill
is an additional explicit request when delivered. No per-issue fetches or timers.
All calls and retries count toward 30 requests/minute/page. Shared edge enforcement
still needs verification; do not promise 30 independent server requests per tab.

## Reviewed MVP restrictions

First delivery supports ordinary discharge with the same implementation in every
deployment. Switzerland is the first live validation dataset, not an allowlist.
Availability follows stored data, supported temporal semantics and existing access
policy, never a country/network switch. No data means absent; unsupported data-use is
unavailable. Unknown temporal support is labelled and prevents automatic fallback,
not basic timestamped display. Provisional discharge is unsupported uniformly in
this slice. Use existing long-range `/observations`; no new observation route.
No new feature flags, regional adapter framework or configuration catalog.

Hindcast reads require reviewer scope and an inactive station publication gate in
this first slice. Gated/unqualified stations expose no hindcast catalog provenance,
coverage, lead lists, counts or values. Use existing principal and PublicationGate
checks before enumeration. Operational reads retain their publication rules.

Fallback is per valid timestamp, with identical cadence and known interval support.
Any operational record wins, including withheld, QC-rejected and missing-value
records; fallback is allowed only when no operational record exists. Unknown support
prevents fallback. Ambiguous revisions are unavailable unless existing authoritative
selection resolves them. Never score operational-plus-hindcast fallback as one series.

Null all QC-rejected forecast values and return the reason. RAW hindcasts retain
an explicit unchecked QC label. Use exact p50 for quantile medians; no interpolation.
Legacy completeness is unknown unless evidence supports complete/incomplete;
orphan headers are explicitly incomplete. Climatology is labelled a baseline.

Options and series both have a five-second SQL timeout and 2 MiB output ceiling;
shared work limits are 100,000 candidate headers, 1,000,000 candidate values
and 100 source tuples for options. Detect overflow without full materialization;
never return partial catalogs as complete. Options overflow returns bounded
unavailability, series overflow asks for a narrower range, and SQL timeout is a
retryable service error. Measure daily/hourly fixtures before release. Do not sample one forecast
and claim its leads represent an entire history. For subsequent period skill,
positive percent bias means overprediction; undefined/nonfinite metrics become null,
empty samples are checked before calculation, and hidden values/counts remain hidden.

## Shared implementation acceptance

Identical data and permissions must produce identical results regardless of country,
network or deployment name. Exercise non-UTC station metadata, non-midnight daily
phase and hourly data in the same code paths. Existing recorded time support and
provenance determine interpretation; station timezone alone cannot establish daily
aggregation boundaries. First live validation uses Swiss stations; deployment
qualification elsewhere changes neither route nor code contract. Period skill is
future scope and is not enabled by readiness of this first series/options slice.

## First-slice error contract

Errors use `detail: {code: string, message: string}`. Unqualified hindcast policy
returns 422 `review_source_unsupported` for either hindcast-involving mode before
source lookup, whether or not data exists. No silent operational-only downgrade.
Options may still show authorized operational choices with a generic hindcast
unavailable marker, without protected metadata. Unknown/foreign source 404 remains
the rule after policy permits source selection.

Options work/output overflow: 422 `review_catalog_limit_exceeded`, never an empty
successful catalog. Series limit overflow: 422 `review_series_limit_exceeded`.
SQL timeout: 503 `review_query_timeout`, never an absent-data result. Include a
human-readable message and describe all errors in generated OpenAPI.
