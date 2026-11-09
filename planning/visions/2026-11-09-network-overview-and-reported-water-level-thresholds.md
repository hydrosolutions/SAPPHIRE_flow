# Network overview and reported water-level thresholds

## Outcome, priority and authority

Provide one network-neutral, authorized overview response for the forecast dashboard,
replacing its per-station detail/QC/observation request fan-out. Deliver this first.
Then persist and expose the supplied DHM reported water-level thresholds for display,
and deliver their scientifically justified discharge equivalents with reproducible
curve/date provenance. Keep both requirements in this handoff; the second must not
be forgotten when the overview is delivered.

This is a standalone vision from the owner's confirmed frontend proposals A and B.
No GitHub issue numbers were supplied for those proposals. It is not an Effort or a
Program ticket. The owner authorized preparing this work on 9 November 2026.
Implementation requires verified publication on the intended target branch and the
applicable independent reviews; this document alone is not evidence of either.
Publication and implementation review follow current AGENTS.md and installed global
skills. The owner approves and merges every PR; no agent merges. Required independent Claude,
Codex and relevant high-risk reviews remain mandatory. Do not infer deployment,
live imports, schema application, credential changes, scheduling or provider-call
authority from this vision. Those require separate operational authorization.

This repository owns backend facts, delivery semantics, OpenAPI and the handoff to
the separate dashboard repository. It does not authorize edits in the dashboard or
Aquacast repositories. Preserve ingestion, forecast computation/science, FI contracts,
weather selection, stored forecasts and frozen ledgers. Do not rerun models, alter
QC results, activate alerts, add stations or expand access as a delivery shortcut.

## Confirmed observation contract

- One request returns all stations in the requested network that the caller is
  authorized to see, including stations without readings or forecasts. Network is
  a selection boundary, not a country-specific implementation or tenant bypass.
  Include sufficient stable station identity and display metadata to avoid detail
  requests solely to render the overview. Unknown metadata remains unknown.
- Return latest water level and discharge independently, with explicit parameter,
  units, actual observation time, source and QC state. Include finite unchecked/raw,
  suspect and passed readings; exclude QC-failed readings. Missing/null/nonfinite
  values never become numeric samples. This is a display rule, not promotion to
  trusted/QC-passed/model-eligible data. Existing unit, delivery, quarantine, purpose,
  source-binding and read-currentness protections still apply before selection.
  Physical validation remains parameter-specific: negative datum-relative water
  level can be valid, so no generic nonnegative filter is allowed. Preserve existing
  parameter/domain QC and science rather than inventing new validity rules.
- Expose whether a newer failed reading exists, without substituting its value for
  the displayed reading. Compute this from the same otherwise-authorized cohort,
  independently of the display QC exclusion. Define the no-eligible-reading case
  explicitly, for example with a nullable latest-failed timestamp and reason; do
  not claim a newer-than comparison when no selected reading exists. Failed data
  must not influence display values, band extrema or unauthorized counts.
- Latest readings are independent of the 72-hour series. Declare and test a bounded
  latest-reading search window and disclose it; absence in that window is not proof
  that a station has never reported. Do not silently search only the chart window.
- Return exactly 72 completed UTC hourly slots for each parameter. Let H be the
  response's UTC as-of rounded down to the hour. Slots end at H-71h through H;
  each selects the latest eligible reading in (end-1h, end], matching the existing
  overview's right-closed hourly ownership. Preserve the reading's actual timestamp.
  At 10:20 UTC the newest slot is (09:00,10:00]; the partial current hour is absent.
  At exactly 10:00 UTC that same slot is complete. The first slot starts H-72h.
  Use deterministic source/version tie handling from the supported read authority.
- A slot without an eligible reading is explicitly null/a gap, not zero, an average,
  an interpolated value or a carry-forward. Do not manufacture a continuous line.
  Return latest data separately even when its timestamp lies in the incomplete hour.
- Provide response generation/as-of information. The client applies the confirmed
  180-minute observation staleness rule to the actual observation time. Response age
  and observation age are separate. Do not introduce a universal server stale flag
  that overwrites this client policy or falsely refresh an old reading on polling.
- Measured discharge, supplied discharge and rating-derived discharge remain
  distinguishable. Do not silently substitute derived discharge for measured Q or
  relabel water level as discharge. Any derived-observation exposure must reuse its
  existing permitted purpose, custody and provenance boundary; no new conversion
  policy is included in A.

## Forecast headline and compact series

For each currently applicable model/output purpose at each authorized station,
provide an explicit headline or a safe absence state. Do not silently choose one
model for the whole station. Selection is parameter- and purpose-aware: preserve
scheduled authority, supersession, publication and existing eligibility rules before
choosing the latest eligible issue. A newer raw database row is not automatically
an authoritative current output. Keep standard and `provisional_rating` separate,
with explicit opt-in and the existing reviewer/admin access restriction for the
latter. Do not expose rejected/withheld numbers through a new aggregate route.

Each usable headline identifies model and forecast/output, parameter and units,
issue time, original horizon, purpose, provisional label, actual QC and input-quality
level plus stable quality codes/reasons. Forecast discharge is labelled m³/s, not
water level. Multi-output models retain separate parameter identities. Reuse stored
facts; unknown quality evidence is unknown, not healthy. Keep ordinary detail/full
trajectory retrieval available on demand without automatic per-row fan-out.

The confirmed peak is over the **remaining future forecast portion**, not the full
original trajectory. Define remaining support using each output's real interval
semantics; retain a current interval whose valid start precedes as-of but whose end
is still future. Exclude expired support. Use the earliest maximum on ties. Report
the median peak value/time and p25/p75 at that **same time**, never independently
maximized quartiles. Expose the selected peak window so original horizon and
remaining-window peak cannot be confused.

Deliver a bounded compact median/p25/p75 series. Exact stored quantile values are
not ensemble members: read p25/p50/p75 by their levels, not empirical percentiles of
those values. For actual member representations, use a documented established
empirical quantile method and sample-count eligibility. Missing/duplicate/nonfinite
levels or unsupported output structure get explicit unavailable/band-missing states;
never invent bands or interpolate missing quantiles. The compact representation
must preserve the stated peak, its same-time band, timestamps and real gaps. Define
its point budget and reduction semantics in the reviewed implementation contract;
never silently truncate a model's horizon or imply a reduced display is the full
trajectory. Full trajectory remains an on-demand read.

Currentness is independent of input quality and numeric availability. For the Nepal
daily product, the scheduled runtime is **06:00 Asia/Kathmandu (00:15 UTC)**, while
the automatic forecast's persisted issue time is **00:00 UTC (05:45 Nepal time)**.
The expected delivery deadline is **07:00 Asia/Kathmandu (01:15 UTC)**. Map an eligible
persisted issue to its product day; do not rewrite `issued_at` or require a 00:15 UTC
issue timestamp merely because that is the scheduled runtime. Yesterday's eligible
forecast remains current until today's deadline. At/after that deadline, if no
replacement exists, label it **STALE** rather than quietly current; keep its original
identity/history. Stale does not authorize displaying withheld or expired numerical
support. A current replacement wins when available before the deadline. An eligible,
authoritative product-day replacement must be visible in the response snapshot;
a late arrival clears STALE once visible. A failed
retry does not invalidate an already eligible current output. Implement product-
specific cadence/deadline policy, not a universal Nepal hardcode. Preserve the Swiss
product's separately confirmed next-issue-time freshness boundary.

Distinguish no forecast, stale prior forecast, withheld/ineligible output, and read
failure. Missing data is not proof of a failed run. This work does not require a new
attempt-tracking engine or change the existing forecast production schedule.

## Delivery, access and performance

Use an additive network-neutral endpoint and stable maintained OpenAPI/schema
examples. Final naming and reversible internal mechanisms belong to implementation.
Do not repurpose the Swiss-only roster route or change existing endpoint defaults.
The existing Swiss history product remains separate: its seven-day mixed-resolution
means/min–max and QC-passed-only observations are not these 72 latest-hour samples.

All selection, aggregates, counts, errors, limit decisions and validators are scoped
before they can reveal data. An unknown/unauthorized network or station must not
leak a hidden roster count. Return every admitted authorized station, including
honest no-data states. Backend errors must not masquerade as empty success. Preserve
standard-purpose publication gating, provisional role checks and database RLS/native
lineage permissions. Define the new route's entitlement matrix explicitly: preserve
existing observation and standard-forecast access, and the reviewer/admin restriction
for provisional forecasts. Aggregating sections is not permission union; each section
uses only its authorized cohort and reveals no hidden existence/counts. Do not invent
new roles/grants. A broad admin role must not accidentally bypass product data
eligibility exclusions. Do not grant API credentials new write access.

Support sixty-second polling with secure conditional requests on the **new contract**.
Existing forecast-bearing routes, Swiss overview/history and latest observations
retain their current `no-store` behavior. Do not weaken them to make this work easy.
Any private conditional caching is explicitly scoped to the new route. Authenticate,
recheck tenant/station grants, purpose entitlement and publication/read eligibility
on every request, including a possible 304. No shared personalized cache or service-
worker reuse across credentials/scopes. A validator must cover the representation,
selection parameters, scope and relevant data/QC/permission/publication changes.
Time alone can change hourly boundaries, peak support and freshness at the delivery
deadline: those changes must invalidate/recompute the snapshot even without new rows.

A newly generated wall-clock field on every request must not defeat meaningful
conditional requests. Use a coherent stable snapshot/as-of contract plus separate
check time if needed; a 304 must not renew sample timestamps or hide snapshot age.
Failure, access denial, token/scope change and expiry must not keep an old personalized
response silently current. Test cache/CORS/error headers and authorization before
conditional handling. If the safe conditional contract cannot be proven, report that
as an unmet requirement, not a reason to disable safeguards or claim completion.

Targets are about **one second** per overview and **under 1 MiB compressed**, at a
60-second refresh. Measure rather than promise them. Prove the 21-station Nepal case
and at least a 160-station representative network, with the explicitly admitted
bounded model/output counts and series sizes, missingness and quality/provenance
metadata. At 160 stations, observations alone require 160×2×72 = 23,040 hourly slots;
forecasts add station_count×model/output_count×display_point_count. These arithmetic
counts are not a benchmark or evidence of gzip size. Measure raw/compressed bytes,
query/row work, cold/warm p50/p95 and concurrent refreshes with disclosed conditions.

Bound both server work and response size. Reuse batch-capable query/read boundaries;
one HTTP request hiding per-station or 72-per-station query loops is not success.
Choose documented supported limits from measurements, including behavior for more
than 160 stations and multiple model outputs. Beyond admitted bounds, fail explicitly
without leaking excluded-scope counts; never silently omit stations/models/points.
Pagination is not an unnoticed substitute for the confirmed one-answer roster.
Explain any target shortfall before expanding limits or adding materialization/indexes.

Thresholds may join A cheaply when an approved display source already exists. Their
absence does not block A; do not implement B's import or conversion behind the scenes.

## Reported DHM thresholds: B, after the overview

Persist the validated 29 October DHM CSV and expose its reported **water-level**
warning/danger values through the scoped station API, with a usable interim export.
This is **display-only**, not operational alert adoption. Do not insert values into
an alert-consumed path merely because its columns look suitable. Keep existing
configured alert thresholds and alert behavior unchanged. Decide the smallest typed
storage/read seam that can prove this separation; the old metadata draft is context,
not an instruction to reproduce its proposed JSONB design.

The retained source is `dhm_published_levels.csv`, with an adjacent provenance JSON
whose `read_at` is 2026-10-29 and whose source is DHM River Watch. Its 21 rows include
metre/gauge-height warning/danger columns. The acquisition/read date is not a proven
threshold effective date, gauge epoch or operational approval date. Preserve source
identity/hash, reported units/reference, source date and its meaning, station and
installation/series association, and unknowns. Retain original source evidence.
Validate finite values, textual codes, duplicates, identity/unit conflicts and source
mapping at the boundary. A replay of identical admitted data is a no-op; a conflicting
source/association is held, not a broad station upsert or silent overwrite.

Confirmed missing/ambiguous cases must survive DB/API/export round trips:

- Code **445** has no match in this source snapshot; that does not prove no threshold
  exists anywhere.
- Code **695** is ambiguous between Chatara installations; do not select one merely
  because reported values agree.
- **602, 670, 439.7 and 604.5** have warning only; danger stays null with a reason.
- The CSV selects Chisapani series691 for280 and Devghat series4140 for450. Validate
  those source associations against the applicable feed/gauge evidence rather than
  inheriting every alternative in the older 23-installation assessment. Devghat and
  Narayanghat Bridge are not interchangeable.

The imported/displayed source values do not by themselves establish compatibility
with current sensor datum or a rating table. Show that distinction. Unknown datum,
effective date or installation must not become an implicit zero adjustment. Do not
introduce an unauthenticated HTTP write path. Any eventual data apply needs explicit
scope, reviewed input identity, least privilege, audit/readback and separate live
operation authorization. Preserve unrelated metadata, assignments and observations.

The interim export carries the same source/version, units, association decisions,
missing/ambiguous reasons and display-only status as the API. It is not a second
independently edited set of thresholds or proof that a DB import occurred.

## Derived discharge thresholds and frozen provenance

Complete B with discharge equivalents only where the source water-level threshold,
installation/gauge/reference, units and applicable rating curve support a valid
conversion. Keep the original reported level and derived Q as different fields,
with parameter-specific units and provenance. Do not compare metre values directly
to discharge forecasts or present a derived Q threshold as a DHM-reported Q value.

Use the curve applicable at the overview's aware as-of instant for a current display,
and the relevant **actual persisted forecast `issued_at` instant** for a forecast
comparison—not its scheduled runtime, future valid time or whichever curve happens
to be newest now. Preserve the exact selection rule and curve identity/version/content
hash, validity and evaluation date, source
threshold version, reference evidence and conversion method. Ambiguous, missing or
incompatible curve/reference evidence yields no equivalent and a concise reason.

**Outside the supported rating-curve range, return no discharge equivalent. Neither
extrapolation nor clamping is allowed.** Reusing either existing converter blindly is
unsafe: the ordinary converter clamps, while the provisional converter extrapolates.
Test both ends, exact endpoints and supported interpolation inside the range without
changing either existing scientific policy for other callers.

Historical reproducibility requires frozen evidence, not a mutable historical-table
lookup. Reuse the forecast's retained lineage where applicable; do not attach today's
curve to an old issue, rewrite a forecast ledger, or assume one curve describes every
input day. The issue-time threshold conversion must identify the exact applicable
curve and distinguish it from the forecast's possibly multiple input-day curves.
Where retained evidence cannot establish the association or reproduce it, return
unavailable rather than infer missing lineage. Expose only the authorized provenance
needed by consumers, never private files, unrestricted native lineage or secrets.

## Existing work and source evidence

Discovery on 9 November found no open repository PRs. The isolated authoring clone
starts from `main` at `ba06b46b276d108cb84f127ddfeab121f8983587`; PR484 only adds a backup
vision above `30ac103c28bc71545f2d730104cc49420011952b` (release1121). The owner reports
worker1121 delivery complete while API remains1120; the API source is unchanged
between1120 and1121. These are dated evidence, not a permanent runtime assumption.
Recheck main, active PRs and deployment identity before implementation/integration.

Source landmarks under `src/sapphire_flow/`:

- `api/routes/api_swiss_overview.py`, `config/swiss_overview.py` and
  `services/swiss_overview.py`: fixed hash-pinned Swiss roster, discharge-only
  measured/QC-passed samples, daily authority and BAFU comparison. This is not a
  portable Nepal setting. Existing compact series has a ten-point limit.
- `store/observation_store.py::fetch_latest_observations`: batched lateral latest
  selection, supported read-purpose/unit protections, and explicit hourly boundary.
  Its single-status QC argument does not directly express the new accepted cohort.
- `api/publication_cache.py`, `api/provisional_forecasts.py`, publication gates,
  map contract/CORS and `docs/spec/api-v1-map.openapi.json`: preserve existing no-store,
  scoping, purpose and native-lineage boundaries. Provisional metadata currently says
  `rating_curves=[]` and `rating_provenance_status=not_exposed`; exact curve exposure
  is additional authorized contract work, not an already available headline field.
- `types/ensemble.py` requires at least seven quantiles plus tail coverage, not
  necessarily p25/p50/p75. `services/swiss_overview.py` distinguishes stored quantiles
  from member empirical quartiles. Current `cmal_small` configuration declares the
  reporting levels; other models/configurations may differ.
- `types/domain.py::StationThreshold` supports water_level/discharge but lacks
  per-row unit/datum/source-document provenance. `store/station_store.py` supports
  threshold reads/upserts; generic onboarding supplies an empty threshold list.
  No existing production import is established by those primitives.
- `store/rating_curve_store.py::fetch_curve_at` uses half-open validity and version
  ordering; `services/rating_derived.py::select_eligible_rating` uses delivery-scoped
  historical blocks. Preserve their different authorities rather than conflating
  their selection policies. `services/rating_conversion.py` contains the clamping
  and provisional extrapolation behavior that B must not apply out of range.

Bounded read-only evidence for 9 November Bimalnagar and Khokana `cmal_small` /
`provisional_rating` outputs found levels [.05,.1,.25,.5,.75,.9,.95], ten valid times,
and no missing/duplicate p25/p50/p75. This verifies those two outputs only, not every
model or network. An implementation acceptance read must check its actual cohort.

The retained threshold CSV SHA256 is
`19e9dc468252c6e3f722daf7ef2206c672092daf6d8b90ff4c870d6a0b5141c1`;
the adjacent JSON SHA256 is
`48dee04b84101aed2d32228f0695e1d031421948582a89d9d5ad98970b3436aa`.
These identify authoring evidence, not an operational import approval. Obtain those
exact files from owner-controlled custody; do not fetch a changed public page and
call it the same handoff. Keep restricted raw packets out of public commits.

Reconcile, do not execute, the historical
`docs/plans/2026-11-04-shared-dashboard-overview-delivery.md`: its generic-route proposal
is relevant, but its default-three-hour/single-headline/no-cache choices are not this
confirmed contract. The retained local `REPORTED-THRESHOLD-METADATA-SMALL-DRAFT.md`
(revision3, implementation_authorized:false) preserves 23 alternative installations
from an earlier capture; it is not the same source packet or an approved import.

Preserve the accepted Swiss observation-history and Swiss CMAL input-quality visions.
Their history aggregation, existing no-store and issue-time freshness policies remain
in force for those products. This new contract does not silently migrate Swiss
consumers, remove the Swiss routes, or include deferred Swiss categorical ingestion.

Station preparation Effort453 revision14 is concurrent **offline proposal work**,
not an implementation dependency. Its planned preparation currentness composes at
observation stores, supported provisional reads and shared dashboard aggregate SQL
(`ordinary_forecast_rows` in `api/routes/tables.py`, reused by `api/routes/dashboard.py`).
Coordinate exact helper/filter changes, including new overview direct-SQL seams,
with its owner before shared integration. No public helper name is allocated yet.
The proposal exposes only a non-value currentness view/predicate; private preparation
objects remain inaccessible. Reuse supported currentness boundaries; do not copy
predicates, depend on its unmerged private schema or weaken them to show more data.
Integration tests must distinguish stale/invalidated old winners, held rows, valid
non-winning alternatives, approved current winners and unmarked legacy stations.
A lower-priority arrival must not hide an unchanged winner; collection-permission
expiry alone must not hide valid reviewed history. Preserve unit/quarantine semantics,
current observation-version history restrictions, existing21 stations/native17 ingest,
OFF lifecycle/assignment/training/forecast/alert hooks, provisional privacy and frozen
ledgers. This does not authorize implementing453, editing its private schema or
changing login/templates/api_review.py. A prior review of an older proposal does not
cover revision14. No overlap migration/rollback/live authority is implied.

## Delivery slices and acceptance evidence

Use one durable vision with separately reviewable implementation PRs, in this order:

1. **A: complete overview delivery.** Scope/read contract, batch queries, both latest
   observations and72 slots, all admitted model headlines/compact bands, purpose and
   freshness labels, stable OpenAPI/examples, safe conditional polling and measured
   resource bounds. Do not ship only a renamed Swiss route as A. Hand the additive
   verified contract to the frontend; do not claim end-to-end migration before its
   separately owned reader acceptance.
2. **B: reported water-level display.** Validated source admission/persistence,
   provenance and null/ambiguous states, scoped station API and interim export, with
   evidence that alert state/behavior and ordinary station writes remain unchanged.
   Keep this independent of A's initial release.
3. **B completion: derived Q and reproducible provenance.** Date/installation/reference
   validation, in-range conversion only, safe frozen issue-time lineage exposure and
   historical reproducibility. API/export integration may extend slice2; do not drop
   this requirement merely because reported metre values are visible.

The implementing owner may split a slice further for reviewable changes, but must
retain complete observable acceptance and the priority order. This is a delivery
proposal, not permission for an unreviewed migration or live import.

Acceptance includes deterministic boundary tests for QC/raw/suspect/newer-failed,
nulls, late arrivals and corrections, UTC hourly edges, latest outside chart window,
source/parameter separation, current interval/earliest peak ties and same-time bands,
member versus quantile outputs, missing bands and multiple models/purposes. Use fake
clocks before/at/after06:00 and07:00 Nepal time, a replacement with unchanged
persisted00:00 UTC issue becoming visible before/at/after the delivery deadline,
failed replacement/current retry, stale historical retention and independent Swiss
issue-time behavior.

Exercise tenant/grant revocation, purpose denial, publication changes, RLS/native
lineage failures and no-data versus error/withheld states, including conditional304
and time-only invalidation. Prove existing endpoints' headers/semantics unchanged.
Measure21 and160+ bounded representative cohorts; reject oversized scopes explicitly.
No benchmark claim from point arithmetic or a tiny fake alone. Functional Nepal
acceptance must show usable authorized observation and forecast sections against
retained representative evidence/local fixtures, not merely an all-null HTTP200 or
fast empty benchmark. Live acceptance readback remains separately authorized and
read-only. Never relax protected DHM-delivery, currentness, unit or quarantine
exclusions to achieve display. Report unavailable data honestly; if the desired
cohort cannot be exposed through current permissions, identify that actual gap
rather than circumventing it.

For B, test the exact21-row missing/ambiguous cases, provenance and units round-trip,
identical replay/conflicting admission, no broad writes/alert activation, exact curve
validity boundaries, wrong installation/reference, out-of-range refusal, supported
endpoint conversion and changed later curves not rewriting earlier issue evidence.
No invalid source or missing lineage may silently become an apparently complete Q
threshold. Maintain current docs/OpenAPI/touchpoint maps and run required native
validation, independent review and full-suite gates after final code changes.

No delivery date is asserted here. Completion evidence is merged reviewed code,
verified contracts/tests/benchmarks and separately authorized operational readback
where a live rollout/import is requested—not a draft, source commit or successful
CSV parse alone.
