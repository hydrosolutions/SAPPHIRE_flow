---
status: READY
title: Shared discharge history for dashboard and API users
---

# Shared discharge history for dashboard and API users

Deliver existing rating-table-derived discharge through a small agency-neutral
API that also serves agency-provided discharge. The dashboard receives values,
aggregation, quality and provenance from the backend. This is Nepal MVP work;
it does not approve provisional data for operational warnings or training.

Owner decisions, 16 November: expose stored worker-produced results with clear
provenance and a live source/QC match; do not repeat rating-policy evaluation on
GET. Deliver the shared history endpoint first, keeping ordinary and provisional
series separate. Overview integration is a follow-up, outside this first patch.

## Evidence and scope

- Inspected deployed v0.1.1136, commit
  `b2982aa0b734a3728bb8654d86e36f5fc83ba1cb`. Main at
  `2f4b12260464412b4d9aefe36a475391f8106751` differs only in the SnowMapper
  requirements document. The root checkout has unrelated changes; preserve them
  and use an isolated checkout for implementation.
- Recurring ingest already calls `_reconcile_native_ratings_task` and stores
  `rating_derived_discharge`, including the current day. No new converter,
  scheduler or history store is needed merely to expose these results.
- Read-only server inspection on 16 November found recent stored records for
  14 stations. Some timestamps have multiple retained versions; rejected records
  also exist. These counts do not establish current eligible sample counts.
  Station 670's newest retained timestamp was 11 November. Stations 447, 620 and
  647 have no recent derived records.
- The shared overview exposes measured/supplied observations, 72 hourly
  last-reading slots and a separate seven-day latest-reading window. Its
  observation block deliberately excludes provisional derived discharge.
- The existing station observations endpoint returns native observation records.
  It has no rating-purpose selector or aggregation. Preserve its current response.
- `/swiss/observation-history` is a default-off, roster-bound API with three-hour
  means/min/max/count and a recent native tail. Its pure aggregation ideas are
  reusable; its Swiss roster, fixed parameter/source and response tuples are not
  a suitable public contract for additional agencies.
- Forecast `rating-input-history` exposes frozen, issue-anchored daily model
  input context. It must remain distinct from current observation history.
- API database credentials cannot read raw rating/capture/reference tables.
  A narrow safe read projection is required; do not grant blanket SELECT or use
  worker credentials in the API.
- Older draft Plan 522 covers broader conversion orchestration and predates
  landed native reconciliation. Do not execute or expand that draft for this task.

## Proposed API

Add one route, with no agency name in its path:

```text
GET /api/v1/stations/{id}/observation-history
    ?parameter=discharge
    &start=<UTC instant>&end=<UTC instant>
    &resolution=hourly
    &data_use=standard&data_use=provisional_rating
```

Initial scope is discharge, a maximum seven-day half-open window `[start,end)`,
and `resolution=native|hourly` (default native). Parameter is explicit; unsupported
parameters fail validation rather than silently returning discharge. Start/end
are required, avoiding a second relative-window convention. Repeated data_use
matches the network overview convention; omission means standard only.
Provisional requests require existing reviewer/admin permission and station/tenant
scope. No new credential or agency-specific permission system.

Authenticate and scope before reading or reporting protected counts. Reject unknown
query keys, repeated keys other than data_use, repeated purpose values, invalid
timestamps, start >= end, future end, and windows over seven days with 422.
Accept timezone-aware instants and normalize to UTC. Standard-only access retains
the existing authenticated observation permissions; consumer denial applies to
requests that include provisional_rating, not to all history reads.

One response envelope identifies station, parameter, unit, requested bounds,
generation time and resolution. It contains separate source/purpose series keyed
by `(source, data_use)`, each with availability, latest_timestamp (null if no
numeric result), support counts and points. Agency-provided and locally derived values
must not overwrite or be averaged with one another. A client can request both
in one call and use one rendering contract in Switzerland and Nepal.

Native points retain their actual timestamps and returned values. Hourly points
identify the actual bucket start/end and contain arithmetic sample mean, minimum,
maximum and accepted sample count. Means are sample means, not time-weighted
means; irregular sampling does not imply known coverage. Buckets align to UTC
hours. Clip boundary buckets to the requested window, disclose clipping and mark
an unfinished hour partial. Bucket clipping and incomplete source results are
separate states; neither proves a percentage of expected readings is present.
Empty hourly buckets have null statistics and count zero. Native empty series
have no invented points. Do not interpolate, carry forward or fill with forecasts.

Expose source and data_use explicitly, preserve quality labels without certifying
derived Q from source-level QC, and return the existing provisional warning codes.
Provide frozen curve identity/content digest/version where retained, validity and
extrapolation warnings in the same read. Deduplicate curve metadata in a small
response dictionary; points/buckets reference contributing curves. Hourly buckets
retain the union of contributing warnings, curve references, QC support and
rejected/held counts. No fake observation UUIDs for aggregates, no raw protected
capture/reference documents in responses.

Availability is `no_source`, `no_eligible_result`, or `available`. Source counts
are counted after scope, trust and delivery exclusions, before numerical/QC
selection. A series with any numeric results is available; an empty series is
never labelled available. Result completeness is `partial` when any visible source
is held or rejected, otherwise `complete`; physical sampling coverage remains
`unknown`. For ordinary sources, no_source means no discharge readings. For the
derived series, it means no source water levels. Missing configuration or a
missing stored conversion with source levels present means no_eligible_result.
Return counts for accepted numeric results, rejected results (including a source-QC
breakdown) and held sources. A native rejected/held point can have null value and
an explicit outcome; never fabricate a timestamp when no source reading exists.

Bound each single-station request to 10,000 authorized candidate records, counting
retained derived versions before choosing a version, and bound the returned body
to 2 MiB. Preserve bounded evidence parsing (including byte/node limits) using the
existing rating-history pattern: 32 MiB aggregate evidence, 256 KiB per document,
500,000 parsing nodes and 64 frozen curves. The aggregate budget was increased
from 16 MiB after read-only measurements found valid trusted seven-day histories
above that initial limit. Existing forecast-input history limits are unchanged. Candidate/data/evidence/timeout failures return
generic 503, not a partial success or empty series. Invalid client windows return
422. Qualify these initial caps against actual seven-day station histories before
rollout; limits must not silently truncate the requested interval.

## Stored result selection and reuse

The response describes `stored_worker_result`, not proof that every current rating
setting was re-evaluated at request time. This is the owner's accepted MVP scope.
The existing read_current_window is NOT a reusable API reader: it locks, resolves
runtime bindings and recomputes conversions before comparing fingerprints. Do not
call it from GET or reproduce that conversion policy in SQL.

Add a narrow SECURITY DEFINER read projection with explicit tenant/station/window
arguments, fixed safe search_path and API-only EXECUTE. Enforce parent tenant and
station agreement inside the function; route authorization must separately enforce
bearer scope because a shared DB role does not know a caller's token entitlements.
Read source rows, selected stored results and diagnostics from one database snapshot.
Validate stored bytes/digest and frozen parent/source/curve/capture identity using
the existing safe projection pattern; expose only necessary scalar metadata.
Historical provenance validation is distinct from recomputing current policy.

For each `(tenant_id, station_id, observation_id)`, choose the newest persisted
result by stored_order BEFORE filtering accepted results. Then compare its frozen
source measurement and QC snapshot with the live trusted observation. Compare all
persisted scientific/identity fields, including timestamp, value, source, delivery,
QC status, flags and rule version; ignore only the transient read/context fields
already omitted by the producer. Compare typed values at the producer's numeric
precision, not decimal spellings. A source mismatch or absent conversion is a
held gap. A selected rejected result remains rejected. Never search older versions
for a successful or source-matching fallback. Across observation IDs, a conflicting
same-source timestamp is held and counted rather than silently averaged or selected.

Both evidence modes retain this source/QC snapshot: derive_rating_sample stores
asdict(observation) except transient fields at services/rating_derived.py:154-165.
The approved legacy path calls that same producer and matches its persisted content
at store/rating_derived_store.py:132-151. V2 additionally retains a capture/source
version digest in feed; v1 does not. Do not require a nonexistent universal
source_version field or use the V2 digest as a substitute for comparing mutable QC.

Expose `stored_at` from captured_at for native derived points. It is the record's
initial storage time, NOT its last validation time: reconciliation skips inserts
for an existing fingerprint. Hourly results may report the contributing storage
time range. Curve/reference/feed configuration changes are reflected after worker
reconciliation produces a new result. Until then, show the stored curve, policy
and warnings without claiming current-policy certification. If reconciliation is
paused or fails, that lag can persist; generation time is not a validation receipt.
GET performs no conversion, reconciliation, locking write workflow or persistence.

Reuse the existing native conversion policy unchanged: expiry, unverified reference
assumptions, unassessed QC and endpoint extrapolation remain visible. Source-specific
evidence checks stay behind the reader boundary; Nepal endpoint/gauge rules must
not become requirements on every agency's API request.

Ordinary finite readings use overview eligibility: raw, qc_unchecked, qc_suspect
and qc_passed. Keep trusted unit/hold/scope/protected-delivery exclusions. Do not
change the legacy Swiss history route's stricter QC-passed-only rule.
Derived numerical eligibility follows the existing converter: suspect, failed
and missing source QC reject conversion; raw/unchecked source levels can yield
numeric provisional Q with qc_unassessed. Expose source_qc_status and source
assessment as assessed/unassessed, never a synthetic qc_passed on derived Q.
Accepted counts mean numerically accepted for this display, not scientifically
approved for operations. Keep source-specific QC/rejection counts explicit.

Extract only aggregation primitives that are actually shared with Swiss history.
Do not change the existing Swiss endpoint's three-hour/native-tail behavior or
enable its default-off feature. Both deployments can adopt the new generic route
when their dashboard is updated; removing old routes is a separate task.

## Overview follow-up

No overview DTO, source preference, fallback or ETag changes in this first patch.
The owner accepted history-first delivery and separate sources. A later small PR
can add separately labelled derived latest/history output using the proven reader;
its exact overview fields and batched cost need review in that follow-up. Do not
mix provisional values into the existing ordinary observation slots.

Overview hourly values remain last eligible readings. New history hourly values
are explicitly labelled sample means. Return latest_timestamp and generated_at
without inventing a new freshness policy; retain actual data times so existing
dashboard observation-age handling can identify stale readings such as station 670.

## Phases and acceptance

1. Independent plan review completed; findings and owner decisions are incorporated
   below. The orchestrator sets this revised plan READY; this does not constitute
   code approval or deployment authority.
2. Implement the bounded projection and generic history route in an isolated
   checkout. Add only the required projection function/ACL migration; avoid a new
   observation table or materialization framework unless review demonstrates a
   specific unavoidable need, in which case revise scope before proceeding.
3. Update the API guide, maintained schema, security/touchpoint documentation and
   frontend handoff with ordinary, derived, empty, held and partial examples.
4. Run focused behavior, real-role database, migration and regression tests; Ruff;
   independent patch review; full test suite locally or CI after the final code
   change. Owner approves and merges the PR. Staging rollout is a later action.

Acceptance covers:

- Identical common DTO shape for Swiss supplied/measured and Nepal derived series;
  old ordinary observation, Swiss history and forecast history reads unchanged.
- Explicit native timestamps; correct hourly sample statistics and UTC bounds;
  empty/partial hours, irregular support and multiple curve versions in a bucket.
- Conversion before aggregation, never conversion of an averaged water level.
- Source/QC correction in both v1 and v2 evidence, absent conversion, duplicate
  versions, rejected latest result, same-timestamp ambiguity, expired curves and
  both extrapolation directions. Verify no fallback to an older matching success.
- A changed runtime rating policy does not silently rewrite history; stored_at
  and generated_at are never labelled as last worker validation times.
- 447/620/647 stay gaps; stale station 670 is not presented as current merely
  because an old sample is available; no missing value becomes zero.
- Reviewer/admin authorization and tenant/station isolation before protected reads;
  consumer denial for provisional requests, ordinary consumer access preserved,
  unchanged raw-table denials, correct no-store/error/CORS handling.
- Bounded seven-day single-station cost across the Nepal roster and representative
  Swiss data, plus existing Swiss regressions; limits fail explicitly rather than
  dropping samples. This does not qualify 21 concurrent history requests.

```json
{"phases":[{"id":"independent-plan-review","depends_on":[]},{"id":"history-reader-and-api","depends_on":["independent-plan-review"]},{"id":"docs-and-consumer-handoff","depends_on":["history-reader-and-api"]},{"id":"validation-and-independent-patch-review","depends_on":["docs-and-consumer-handoff"]},{"id":"owner-merge","depends_on":["validation-and-independent-patch-review"]}]}
```

No forecast/model, rating science, alert eligibility, station activation, broad
API rewrite or production deployment is included. The ordinary forecast freshness
configuration gap identified on 16 November is separate from discharge delivery.

## Review disposition

One independent Claude Sonnet CLI review completed on 16 November (initial verdict
REVISE, USD 0.15). Full report is local at
`/tmp/sapphire-discharge-plan-review/review.md`, not part of a commit.

- B1: resolved by the owner's stored-result decision; removed the impossible
  read-only current-policy guarantee and prohibited reuse of the locking converter.
  Follow-up source inspection verified the required snapshot exists in both modes.
- H1: version selection is explicit and conservative: select newest first, then
  check its source; never resurrect an older successful record. Timestamp ambiguity
  becomes a counted hold.
- H2: ordinary QC eligibility and provisional conversion eligibility are now
  separate; source QC is not represented as independent discharge certification.
- H3: owner accepted history first and separate sources. All overview changes
  removed from this patch.
- M1: bounds, UTC buckets, completeness and availability defined. Retained the
  compact curve dictionary/digests/validity because the frontend explicitly needs
  version provenance; no full curve points will leave the API.
- M2: source/purpose series remain separate. Rejected the suggestion to label an
  empty standard series available; its correct state is no_source/no_eligible_result.
- M3: latest_timestamp and generated_at are explicit; no new freshness policy.

Owner decisions are resolved. READY is the orchestrator's disposition after
incorporation, not a claim that the reviewer approved unseen final edits. No code
has been implemented or independently approved by this plan review.
