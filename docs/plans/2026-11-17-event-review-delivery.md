---
status: READY
---

# Station historical forecast and hindcast review

## Outcome and boundaries

Deliver an agency-neutral reviewer API for one station, model, lead and valid-time
period of up to 366 days. Serve stored operational forecasts and hindcasts in bulk,
with backend-defined selection, uncertainty, provenance and visible gaps. Follow
with bounded selected-period skill. No model execution, training, weather backfill
or Prefect jobs on page reads. Fine-tuning remains separate (issue 538).

This plan is READY for the shared options-and-series slice after the owner-requested
independent Claude follow-up review and orchestrator deliberation. It is not a
deployed contract; period skill remains outside this readiness decision. The working checkout has
extensive pre-existing edits and deleted workflow/API files; implementation must
start from an isolated current-main checkout, preserving those edits. The referenced
workflow file is also absent on current GitHub main; use the supplied AGENTS review
requirements, not a reconstructed retired workflow.

## Evidence checked on 17 November 2026

Read-only SQL on the Mac mini Swiss database found both `cmal_small` and
`climatology_fallback` hindcast headers and current stored score rows for these
35 BAFU codes:

```
2030 2034 2053 2070 2078 2079 2102 2105 2125 2150 2151 2159
2161 2205 2244 2247 2282 2304 2327 2349 2351 2352 2386 2387
2403 2415 2432 2436 2437 2450 2462 2468 2487 2606 2635
```

There are 10,146 CMAL and 1,695 climatology score rows, all baseline rows with
null generation IDs. Null generation is supported by the existing latest-generation
predicate; querying only `skill_generations` would incorrectly report no skill.
Stored score existence does not alone establish API visibility: `/skill` also
filters to active assigned models and artifacts.

Recommended data fixture: Thun 2030, UUID
`780ee2f9-2e8f-4332-9810-81f1e87a277a`, valid period
`[2019-06-01T00:00:00Z,2019-07-01T00:00:00Z)`, elapsed lead 48 hours.
Both models have 30 distinct valid times and there are 30 QC-passed discharge
observations. Landquart 2150 has 10 CMAL valid times and 30 climatology valid times
for that period/lead: useful for gap tests. Station 2009 has no hindcasts for either
model. Do not advertise the whole 2016–2020 window as complete at every site.

Before the approved change, the Swiss dashboard token had 134 explicit station
grants with no overlap with these 35. Keep station-scoped access. On owner approval,
all 35 were added using the existing audited grant helper in
one transaction: 134 → 169 explicit grants, preserving all previous grants and
tenant binding. The token name still contains 134; it was not rotated. The packaged
Swiss overview roster remains 134 until its matching image/config release. Generic
station reads and the packaged overview roster are distinct.

The Mac mini's separate Nepal database contains only test gauge 123 and has no
hindcasts or skill. It is not the cloud Nepal deployment. A dated local backend
handoff (`/tmp/nepal-event-review-backend-reply.md`, deployed source 009deaef,
v0.1.1139) reports no Nepal CMAL hindcasts/skills; this is supporting evidence,
not a fresh live query. Direct cloud SSH failed host-key verification. Verify the
trusted cloud connection before claiming an exhaustive Nepal inventory.

## Proposed interface

Detailed draft: `docs/spec/event-review-draft.md`. All routes below are new,
reviewer/admin, station-scoped, GET-only, under `/api/v1`:

1. `/stations/{station_id}/review-options`: models, source identities, stored lead
   availability, cadence, coverage and completeness evidence. One small read when
   selecting a station, reusable for subsequent period selections.
2. `/stations/{station_id}/review-series`: one model, one exact elapsed lead, one
   valid-time range. Returns separately identified operational/hindcast segments,
   backend precedence decisions and explicit gaps, without per-issue requests.
3. `/stations/{station_id}/review-skill`: later phase using the exact same selection
   and observation alignment, returning period metrics and insufficiency reasons.

Preserve existing observation and stored-skill routes. Three bulk data reads means
series, observations and stored skill; the small options read is additional on
station change. Period skill is a separate explicit read when enabled. Be explicit
about this count rather than claiming five operations take three requests.

## Implementation decisions

- Query values by valid time and exact lead, restricting station/model/run/artifact
  in SQL before loading values. Existing hindcast fetches are issue-time based and
  load all leads/members: do not repeatedly call them for each issue.
- Reuse domain types, store protocols and authorization helpers. Add a focused
  review read service and projection store; no new model or ForecastInterface change.
- Reuse stored p25/p50/p75 or existing empirical type-7 member reduction semantics.
  Never treat quantile rows as equiprobable ensemble members. Missing bands remain
  null with a reason; no quantile interpolation invented for the dashboard.
- Historical artifacts must remain selectable independently of today's active
  assignment. Pin a hindcast run/artifact identity, never silently combine retries.
  Inspect actual duplicate headers and publication lineage before finalizing the
  operational revision tie-break; UUID order alone is not scientific precedence.
- Operational presence takes precedence over hindcast only within the same model,
  lead and compatible valid-period support. A withheld operational point must stay
  withheld, with no hindcast fallback that bypasses publication policy. Apply
  publication decisions before returning values, availability metadata or skill.
- Distinguish retrospective issue anchors from actual production times. Define
  elapsed lead exactly, not “day 1” by array position. Audit Nepal local-day support
  and daily aggregation before enabling it; do not apply Swiss midnight assumptions.
- Existing hindcast headers have run IDs but no durable expected-period/completion
  ledger. Do not infer a successful complete run from min/max timestamps or a
  successful Prefect exit. Use known run evidence when available; otherwise report unknown completeness.
  Defer a new durable manifest. Sparse values remain usable; orphan headers and
  evidenced partial output are incomplete, not an unhandled 500.
- First skill release: MAE, NSE and bias for the selected median against matching
  QC-passed observations, partitioned by kind, run/artifact and cadence. Reuse metric
  functions, but audit undefined/degenerate handling. Return explicit sample count,
  training/evaluation dates and covered intervals. Minimum sample policy and
  aggregation coverage rule must be fixed in review before enabling computation.
  No ranking across unequal samples, mixed forcing or training overlap; ranking is
  deferred until a common-sample comparison contract exists.
- Ordinary/provisional Nepal discharge must retain existing data-use separation
  and access rules; ordinary observations cannot validate provisional forecasts
  by an implicit fallback. Unsupported combinations return explicit unavailable.

## Performance and access

Target 10 seconds for station review, five seconds for station listing; measure
three concurrent reviewers before considering more capacity. No timer refresh.
Browser ceiling is 30 requests/minute/page, not a proven edge quota. Verify deployed
Cloudflare limiter key/window and shared-token behavior separately; Mac mini Caddy
had no limiter in the prior inspection. Do not claim independent server quotas for
two pages sharing one credential.

Propose 2 MiB uncompressed per series response and at most 8,784 hourly points per
366-day UTC interval. Deduplicate provenance at segment level. Bound candidate rows
and query time as well as output bytes; benchmark actual ensemble cardinality and
indexes. Never silently truncate or coarsen. Reject oversize requests with a clear
narrow-period action; determine whether hourly year fits using serialized fixtures.
Do not broaden the separate seven-day observation-history endpoint by accident.

## Agent handoff milestones

One implementer owns the work at a time. Agents can hand over between milestones;
do not run milestone implementers in parallel. Prefer one implementation branch/PR.
Each handoff records checkout/branch and baseline SHA, changed paths, test commands
and results, unresolved issues, and the next milestone. Keep handoffs concise and
path-based. These milestones decompose the approved design; READY is unchanged.

### M1 — Establish the implementation baseline

**Depends on:** READY plan.

Create an isolated current-main checkout and record its SHA. Read applicable AGENTS,
scope, API/persistence and training/hindcast touchpoints, security and WMO standards.
Locate current authorization, router registration, store wiring, `PublicationGate`,
authoritative forecast selection, quartile reduction and contract exporter. Map the
smallest new review domain types, projection store, service and boundary schemas.
Missing authoritative revision/support metadata uses the approved unavailable state;
do not introduce a framework or reopen policy because helper names differ.

**Exit:** A concise implementation map with reusable symbols, proposed new paths and
test commands; no unresolved dependency preventing M2. The dirty checkout remains
untouched. Genuine source/contract mismatches are recorded for the orchestrator.

**Handoff:** Checkout/branch, baseline SHA, implementation map and next test commands.

### M2 — Deliver scoped review options

**Depends on:** M1.

Implement `/review-options`: minimal typed query/results and bounded projection of
source tuples, actual stored leads and coverage. Wire existing reviewer and station
scope checks, publication policy, store dependencies and router registration.
Inspect current forecast/hindcast stores and DB metadata; do not load whole ensembles
through existing issue-range readers. Historical artifacts remain independent of
active assignments. Return explicit absent/unknown/incomplete evidence and the
approved policy and catalog-overflow states.

**Exit:** Focused store/API tests cover scope 404, policy before enumeration, no
protected metadata leakage, varying leads, historical artifacts, orphan evidence,
and catalog limits. Identical data/permissions under renamed deployment/network
metadata produce identical behavior. Update affected docs with the code.

**Handoff:** Working route, changed paths, focused test results, source tuple/schema
and query-plan evidence, remaining performance qualification.

### M3 — Deliver bulk lead series

**Depends on:** M2.

Implement `/review-series` using M2 source choices and the shared projection/service:
one consistent read transaction, valid-time/exact-lead filtering, authoritative
operational selection or explicit ambiguity, existing quartile semantics, separate
kind/provenance segments, gaps, QC states and approved fallback precedence. Integrate
API parsing/error mapping. No model jobs or per-issue reads.

**Exit:** Focused tests cover half-open/366-day bounds; exact timestamp subtraction;
unoffered leads; pinned run/artifact/forcing/cadence; stale and foreign tuples;
missing quantiles and nonfinite values; RAW/rejected QC; gaps/orphans; withheld,
rejected and missing operational rows suppressing fallback; unknown support; distinct
kinds; daily/hourly cadence, non-midnight phase and non-UTC metadata. Verify candidate,
point, byte and timeout failures. Update affected docs with the code.

**Handoff:** Both working routes, tests, complete/gapped/withheld/unsupported examples,
and limitations. Do not describe local routes as deployed.

### M4 — Qualify the contract and prepare the frontend handoff

**Depends on:** M3.

Update the existing generated OpenAPI/exporter, API documentation and validated
synthetic examples. Measure serialized daily/hourly-year fixtures, query plans and
three concurrent reviews in test/staging; add an index only on measured need. Verify
compatibility with existing long-period observations and stored skill. Check the
edge limiter/shared-credential behavior where accessible; record uncertainty rather
than promise independent per-page server quotas. Run the full suite after the final
code change locally or in CI before merge.

**Exit:** Evidence for actual payload size, query time, concurrent latency, request
count, limit errors, no per-issue fan-out and no background computation. Generated
schema/examples validate. Use authorized test-site access; production deployment is
not a prerequisite for this milestone.

**Handoff:** Immutable source/contract SHA, routes and requests, test/benchmark
results, outstanding issues, rollout verification checklist. Clearly mark undeployed.

### M5 — Independent patch review and controlled release

**Depends on:** M4.

One independent patch reviewer covers selection/time semantics, authorization,
bounded reads and shared implementation. Keep full review report local; put concise
outcomes/unresolved findings in the PR. Owner decides findings, approval and merge.
After fixes, rerun relevant checks; full suite must pass after final code changes
before merge. No automatic review loops.

**Exit:** Reviewed PR ready for owner decision. Production rollout remains
owner-controlled. After authorized rollout, verify both routes with reviewer access
and actual test-station data before announcing availability to the frontend.

**Handoff:** PR/contract links, review disposition, final test results, actual deployment
status, and verified frontend test instructions when deployed.

```json
{
  "phases": [
    {"id": "M1-baseline", "depends_on": []},
    {"id": "M2-options", "depends_on": ["M1-baseline"]},
    {"id": "M3-series", "depends_on": ["M2-options"]},
    {"id": "M4-qualification", "depends_on": ["M3-series"]},
    {"id": "M5-review-release", "depends_on": ["M4-qualification"]}
  ]
}
```

Period skill is a separately planned future slice, excluded from this executable
graph. The packaged 169-station overview roster release is separate release work;
the generic station list already exposes the approved scope.

## Independent review disposition and MVP decisions

One independent Claude review completed; full report stays outside commits.
Accepted source identity, authorization, precedence, provenance and query-bound
findings. Rejected the missing-observation-route finding: the frontend already uses
`/stations/{id}/observations` for long periods. Do not build another observation API.
The separate seven-day history endpoint is not a dependency.

The first slice implements ordinary-discharge options and bulk series once for all
deployments. Switzerland is the first live validation dataset, not an eligibility
condition. Never branch on country, network, deployment name or station allowlists.
Availability follows authorized stored data and supported temporal/data-use semantics.
Nepal audit, new completeness storage, period skill, ranking, comparator and
thresholds do not block the first slice. No new infrastructure.

Use explicit source parameters, not a new token registry: required `source_mode`
(operational, hindcast, operational_with_hindcast); hindcast modes also require
`hindcast_run_id`, `hindcast_artifact_id`, `forcing_type`, and `time_step_seconds`.
Options supply these values. Validate the full tuple against station/model/parameter;
unknown and foreign tuples both return the same 404. Treat a replaced artifact as
stale selection, not permission to silently switch it. Re-read within one consistent
transaction; no promise that mutable run IDs identify immutable values.

Precedence is per valid timestamp with identical cadence and known temporal support.
Any existing selected operational row suppresses hindcast substitution, including
withheld, rejected and missing-value rows. Only absence of an operational row allows
fallback. Unknown support prevents automatic fallback. Keep kinds in separate
segments and never score a mixed fallback series as one result. For ambiguous
operational revisions, return unavailable rather than use arbitrary UUID order;
reuse existing authoritative publication selection where available.

Use existing `require_reviewer`, principal station scope and `PublicationGate`
semantics. First-slice hindcasts support ordinary discharge on any authorized
station where the existing publication gate is inactive. Where that gate is
active or provisional authority is required, return generic unsupported/withheld
availability and no hindcast metadata or values until an explicit policy exists.
Apply this before catalog enumeration; no leaked run IDs, dates, lead lists or counts.
Withheld points expose only the policy-permitted state/reason. Derived skill must
not reveal hidden sample counts.

Check exact timestamp subtraction in addition to the stored integer lead column,
which is floored on write. Quantile median requires an exact p50; missing quantiles
stay null. Reject nonfinite values, preserve header QC status, and null rejected
forecast values with a reason. RAW hindcast status is labelled unchecked, not
silently promoted to QC-passed. Omit generation time when it is not recorded:
header created_at is first storage, and can survive value replacement.

Both options and series use a five-second SQL timeout, bounded candidate work and
2 MiB output limit. Establish row limits using daily and hourly serialized fixtures
and query plans before shipping; do not extrapolate all leads from one sampled
header. No additional index unless measured plans require it. Three-reviewer load
and edge quota verification are qualification checks, not new platform projects.

Period skill stays a subsequent slice: reuse selection/alignment; check empty pairs
before metric calls; convert nonfinite results to null with a reason; test nearly
constant observations. Percent bias is 100 * sum(predicted-observed)/sum(observed),
positive for overprediction. No CRPSS, no model ranking. Stored skill remains tied
to its own active artifact and dates, not necessarily the selected historical run.

## Shared implementation and readiness boundary

One API, domain service and store projection serve every deployment. Reuse existing
station timezone, recorded cadence/phase, source provenance, principal scope and
publication policy. Do not add a regional adapter framework, plugin registry,
per-country feature switches or a new capability configuration catalog. Supported
representation/data-use rules are a small shared code contract. Operational capacity
limits are shared defaults, not per-deployment tuning knobs in this MVP.

A timestamped series can be shown with unknown interval support explicitly labelled;
only fallback/alignment that requires known support is unavailable. Missing data
means absent, not unsupported region. Fixed UTC offsets must not stand in for local
calendar days. Use known recorded support or existing authoritative model temporal
semantics; never infer period start/end from station country or timezone alone.
Provisional discharge remains an explicit unsupported data-use category in this
first slice for every deployment, not a Nepal-specific restriction.

Tests use the same shared code for synthetic UTC and non-UTC station metadata,
daily and hourly cadence, non-midnight phase, no data, unknown support and active
publication restrictions. Renaming a tenant/network/country must not change results
when data semantics and authorization are unchanged. Swiss live fixtures qualify
the implementation; later deployment qualification verifies inputs and policy,
without duplicating endpoints or adding a regional switch.

Bound execution using a five-second statement timeout, 100,000 candidate headers,
1,000,000 candidate value rows, 8,784 selected points and 2 MiB serialized output
per request. Detect limit+1 before materializing more rows; never truncate silently.
Options enumerate at most 100 source tuples, with the same work/output limits.
Return a bounded availability error on options overflow; series overflow asks for
a narrower period. A timeout is a retryable service error, not an empty dataset.
These are conservative initial constants: qualification may reduce query work or
add an evidenced index; raising limits requires measured memory/latency evidence.
Use set-based queries; do not load a million-row Python ensemble to compute options.
Validate serialized daily/hourly year fixtures and three concurrent requests before
release. These measurements are implementation exit checks, not unresolved policy.

READY authorizes only options, series, tests, documentation and qualification of
that shared first slice. Period-skill metric/sample policy remains separate future
work. Owner retains merge and production rollout decisions. The owner requested a
focused follow-up independent review of the shared-code revision; no review loop.


## Readiness record

Independent follow-up verdict: READY for options and bulk series only. Orchestrator
agrees: shared implementation, bounded reads, source selection and fail-closed
policy are concrete; benchmarks and generated schema are implementation exit checks.
No code or deployment is claimed complete. Full review report remains local.

Resolve the review's non-blocking response details as follows. If hindcasts are
unsupported by publication/data-use policy, any hindcast-involving mode returns
HTTP 422 with `detail.code = review_source_unsupported`, before source lookup,
regardless of whether hindcasts exist. It does not silently degrade to operational.
Operational-only remains independently available under existing publication rules.
Options still return authorized operational choices and a generic hindcast
unavailable marker, without protected source metadata. Catalog work/output overflow
returns HTTP 422 with `detail.code = review_catalog_limit_exceeded`; it is never an
empty successful list. Series limit overflow uses `review_series_limit_exceeded`;
SQL timeout returns HTTP 503 with `review_query_timeout`. Error detail also carries
a human-readable message. Export and test these schemas with the routes.

Tests explicitly cover exact timestamp precedence, historical source selection
independent of active assignments, the policy decision before source lookup, and
serialized hourly-year boundary/overflow behavior. These are targeted acceptance
tests, not new features or additional review rounds.
