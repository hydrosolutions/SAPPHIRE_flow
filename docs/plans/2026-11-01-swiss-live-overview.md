---
status: READY
owner: Swiss dashboard delivery
---
# Swiss live overview — minimum operational delivery

## Outcome and scope
Replace staged Swiss observations, forecast map/counts/outlook and danger lists
with current scoped API data. Preserve the working station panel and Nepal.
Owner requested normal independent review, simplicity and operational delivery;
water levels are explicitly deferred. This is a proposal, not implementation approval.

Baseline: backend0.1.1093 /76f61d8130881d83505096d02924420a24f3598f.
134 approved stations; exclusions remain. No normalization activation, historical
repair, alert activation, model/science change, backup redesign or fleet browser reads.
Live means latest ingested source data, not a new forecast every minute.

## Evidence
- Selected station snapshot/evaluation/skill work. Swiss overview still uses
  staged assets; periodic refresh rereads files. Source reviewed at frontend
  b2bdabf95837c9a4dee8cc1d3d45e011d72751aa, not independently matched to deployment.
- GET /api/v1/observations/latest exists in maintained OpenAPI2.3 but frontend
  proxy/client do not use it. It is scoped before pagination, max200 items, no-store.
- Existing station_thresholds primary key excludes source; unrestricted consumers
  include forecast alerts, observation alerts and skill. Blind import is unsafe.
- Connector is installed/running. Reviewer has134 grants, enabled, expires
  2027-01-28T10:38:00.185963Z. Continuous use remains bounded to3 reviewers,
  one visible tab each,30 requests/min/page including retries.

## Keep it simple
One GET /api/v1/swiss/overview, one response for up to200 authorized Swiss stations.
Explicit failure if scope exceeds the supported bound; never silently truncate.
No Redis, new queue, global distributed cache, WebSockets or generic projection
framework. Existing station metadata/geometry remains; remove staged dynamic
inputs only after replacement acceptance.

First implement bounded batched reads of existing stores, projecting only required response
fields after full eligibility validation. Do not call HTTP routes internally or run134 full snapshots.
Reuse authority/visibility selection logic, not a second definition of validity.
Benchmark under normal ingest/forecast load before deciding any precomputation.
This deliberately simplifies the frontend's proposed stored projection: compact
on-demand reads avoid cache invalidation machinery for QC/holds/permissions.
If p95 cannot meet1s for134 stations, stop and review the smallest measured fix;
prefer a small source-specific forecast headline index to a fleet snapshot cache.
No speculative background materialization or unreviewed performance guarantee.

## Concrete scope, authority and batching
Bearer reviewer/admin only; consumer and human-session access are outside v1.
Roster is the intersection of principal grants, configured Swiss evaluation tenant,
and a server-owned versioned approved134-station manifest. Introduce
src/sapphire_flow/config/swiss_display_roster.json with UUID, network and code, seeded from
the previously approved134 roster and independently checked against the existing
reviewer grants and exclusion evidence. Ship it as package data via pyproject.toml [tool.setuptools.package-data]
(test built-wheel and container inclusion); pin SHA256
and cardinality in the reviewed [swiss_overview] deployment overlay; absent section
keeps the route disabled. No extra volume mount. Validate metadata before forecast/
observation access, not before the station-metadata validation query itself; validate duplicate IDs/codes, station existence, tenant and metadata.
Missing config, mismatch or drift fails503 before data access. Frontend assets and
mutable group membership are never runtime authorization authorities. Roster
changes require a reviewed manifest/digest change, not automatic expansion. Admin
effective scope is configured tenant intersect manifest, regardless of generic
admin station_in_scope bypass. Reviewer further intersects explicit grants.
Tests cover mixed-country grants, granted-but-unlisted, listed-but-ungranted,
admin, tenant mismatch, missing manifest and digest/duplicate corruption.
Admin never bypasses tenant/approved
roster restrictions. Map via existing UUID-to-code roster, not numeric-code guessing.
No configuration/roster means503; an approved configured roster above200 means503
configuration error before data reads, never truncation or mixed-country enumeration.

Follow api_evaluation_forecasts.py, not generic latest forecasts or the ordinary
human-publication selected-ID route. Extract a shared pure DAILY-CMAL selection
step from evaluation_forecasts.py. Read whole day summaries once per group/day;
batch exact accepted/rejected IDs and publication metadata, retaining the WHOLE current-issue branch (evaluation_forecasts.py:128–249):
first-group-with-member ordering, PRODUCED gate, parameter filter, _day_available,
_daily_candidate, _usable, supersession and _covers. Skip assignment resolution
for overview, not checks from the current branch. Parity tests require identical
output_id to evaluation parameters[discharge].selected when selection=current;
fallback/absent implies unavailable overview. Batch hydration must retain full
ensemble/artifact validation where _usable needs it; only the RESPONSE is compact. Existing station evaluation
keeps its fallback/history/diagnostics behavior; overview requests only today's
selected daily CMAL candidate. No134 service calls. An integrity error in ANY configured group for today makes CMAL unavailable for
ALL returned stations with authority_integrity_error; never select through a later
group after such an error. Observations and BAFU remain usable. Use a typed
DailyAuthorityIntegrityError subclass to distinguish this from unrelated errors. Shared roster/auth failure fails the entire request.
Do not suppress unrelated programmer/DB exceptions under an integrity-error label. Today's issue pending means
unavailable/pending; yesterday is not silently substituted. A rejected-store
reference can be a headline only if the existing daily selector deems it usable;
storage name alone is neither authority nor rejection permission.

Apply HTTP-layer publication redaction too. If gate active, reviewer CMAL is
withheld, no numeric peak/level/provenance leakage; BAFU follows Forecast Lab's
active-tenant gate (withheld for this route, including admins). Admin CMAL follows
existing evaluation behavior, not an invented universal bypass. Test exact parity.
Do not change gate configuration to make the map look populated. Verify Swiss
gate state before build. If active, correct withholding passes safety tests but
does NOT satisfy the owner goal of visible forecasts; escalate access separately.
For withheld CMAL retain only ID, model/source, issue time and QC status permitted
by evaluation; peak values/times, levels, derived provenance and input flag details
are null/absent. BAFU numeric values are absent for both roles when gated.
The midnight pending window is intentional: map can be pending while panel shows
a separately labelled fallback. Measure this window and explain it in acceptance.

BAFU reads are filesystem-backed: enumerate archive once per request, choose latest
eligible files for scoped covered stations only, read bounded selected files; no
per-station directory listing. Verify API archive mount and file counts. Keep
source issue time distinct from archive produced_at; do not invent collection time.
Missing/unverified unit is unavailable, not an assumed m3/s fallback. For a daily value, coverage
includes its interval: valid_time+time_step > as_of. Peak uses the0.5 quantile or
per-time member median; unsupported representation is unavailable.

Record bounded query counts: roster/auth, four observation queries, group/day reads
independent of station count, batched exact forecast/visibility reads and display
metadata. Assert no per-station query fan-out and one archive enumeration. Measure
at134 stations in isolated real-PG/archive fixtures before live admission.

## First-release contract
Schema version1. UTC timestamps on wire; frontend displays Europe/Zurich including
DST. No change to forecast aggregation days. Every authorized station appears,
including missing-data stations; no other roster/member details leak.
Header: schema_version, generated_at (one injected as-of used by all reads, not source freshness),
station_count, items. Per-station source times are authoritative; no aggregate
source maximum masquerades as fleet freshness.

Each item:
- station: id, network, code.
- observation: availability available/unavailable and reason; latest nullable {observed_at,value,qc_status}; hourly exactly3 {hour_end,sample}, oldest first, sample nullable and
  retaining observed_at,value,qc_status. Discharge/measured and the canonical display
  unit convention are declared once; no claim of per-record source-unit certification.
- reported_level: availability/reason, state level/upstream_undefined/unavailable, level1..5 or null, observed_at
  nullable if upstream supplies none, collected_at, source/publisher provenance.
  upstream_undefined preserves an explicit source statement; unavailable means no
  usable information. Both have null level; neither means level1.
- flow_class: availability/reason, authoritative class code/label, source time
  when supplied, collected_at, source and classification scheme. Not a danger level.
- forecast: authoritative daily CMAL headline, withheld, or unavailable with reason;
  model/source, output_id, output_source (forecast/rejected), qc_status, issued_at,
  horizon_start/end, peak_value/unit, peak_valid_time,
  peak statistic ensemble_median, input degradation, inferred_level nullable,
  threshold_source and threshold profile provenance when classified.
- bafu_comparison: independently available/withheld/unavailable, issued_at, source,
  peak_value/unit, peak_valid_time,horizon_start/end, statistic median;
  inferred_level/provenance under the same explicit threshold rules if available.
- thresholds: [] when unknown; otherwise level2..5, discharge,m3/s,value,
  source inferred, source reference and import provenance. Display-only by construction.

Missing required data never becomes level1. Below-level2 classification is only
valid with a complete usable threshold profile. Do not call inferred classes an
issued warning. Peak is max median over the still-valid displayed forecast horizon;
earliest valid time breaks ties. Do not use maximum ensemble member. All-expired
or invalid forecasts are unavailable; no ordinary/staged substitution for missing
authoritative CMAL. Preserve finalized-whole-summary checks (COMPLETE or INCOMPLETE as supported),
STANDARD purpose, role/gate publication redaction and input-degradation labels
from evaluation service.
BAFU remains comparison, never a replacement for failed CMAL publication.

### Exact forecast wire states and selector handoff
Each forecast component is a discriminated object with state available, withheld,
or unavailable. Available requires reason=null and the full headline fields.
CMAL available includes output_id, output_source, qc_status and model_id.
Withheld requires reason=publication_gate; CMAL may retain only output_id,
output_source, qc_status, model_id and issued_at. All horizon, peak, inferred-level,
profile/provenance and input-detail fields are null/absent. BAFU withheld retains
only state, reason and constant source=bafu. This is deliberately a stricter
subset of existing redaction, not field-for-field equivalence of whole responses.
Unavailable has reason in {pending_current_issue,no_current_candidate,
authority_integrity_error,expired_horizon,missing_archive,invalid_source_data,
source_read_failed,not_onboarded}; all headline/value fields are null/absent.
An eligible rejected-store candidate can be available with output_source=rejected;
rejected storage is not itself an unavailable reason. Shared auth/config failures
remain HTTP errors. Authority-integrity failure is item component state, not HTTP503.

Use EvaluationReadSettings from config.evaluation_forecasts.to_domain(), plus
the contracts frozen in each day snapshot (claim.frozen.output_contracts), not
the deployment fallback-contract mapping; preserve the existing UUID-string-sorted
precedence among configured groups (not configuration-list order), current-day
snapshots and exact hydrated candidate maps as inputs to the extracted
pure current selector. No second overview authority-settings type or station-name
lookup. Group/tenant/parameter/PRODUCED/coverage checks stay inside shared logic.
Only assignment resolution used for ordinary fallback/history/diagnostics is
omitted. Parity compares the extracted current branch with existing route outputs
when that route succeeds; yesterday-only diagnostic corruption can still fail the
old route while overview current-day selection works. Document/test that narrow
exception rather than adding yesterday's diagnostics to the compact overview.

Examples (illustrative, not production payloads):
```json
{"state":"unavailable","reason":"pending_current_issue"}
```
```json
{"state":"withheld","reason":"publication_gate","source":"bafu"}
```
```json
{"state":"unavailable","reason":"authority_integrity_error"}
```
All stations receive the final example for CMAL when any current-day group is
integrity-invalid; their observation and BAFU objects are independently populated.

## Observation and time rules
Read through existing purpose/unit/hold/delivery predicates. Numeric overview
samples are qc_passed only; no hidden fallback to a failed/suspect/raw reading.
Latest means latest eligible qc_passed observation within the168h window, not newest
raw capture. Preserve its actual age. If no such row exists, return unavailable;
do not claim a known QC failure unless an independently authorized diagnostic
supports it. Unit trust is not a substitute for QC. Existing latest route keeps
its current semantics; add an explicit QC option/internal helper for overview.
Do not activate unit normalization or invent per-record source units.

For response time T, H=floor(T to UTC hour): hour_end is H-2h,H-1h,H. Sample is
last eligible reading in (hour_end-1h,hour_end]; never carry forward across that
window. Use four bounded lateral batch queries (latest plus three windows), sharing
the existing scalar-correlated trust predicate. Add/test exclusive lower bounds
for hourly reads; do not use the history-scanning batch reader. Future times are
excluded by query bounds. Trend remains the frontend's explicitly provisional
percent rule, with missing-data and zero-denominator guards, not a new science rule.

No new backend freshness enum. Frontend derives display age from actual source time
and injected as-of, separately from qc_status. Proposed display cutoffs30min/2h
require measured feed cadence/lag before acceptance; these are not QC thresholds.
Do not invent current ingest-failure telemetry for discharge: timestamp age is the
available evidence. Network/API failure clears primary live fields; show last successful update time without keeping prior numeric fields in the live view. New status components persist
fetch/parse health so known failures are unavailable even if old facts remain.

## Ingest reported levels and flow classes
After verifying representative public source records, extend the operational
batch LINDAS ingest to parse reported danger levels without extra
per-station calls. First inspect representative source records and verify whether
levels are measurement-timed or station-status-timed. Preserve supplied timestamps;
otherwise record collection time and leave observation time absent.
For flow classes add one bounded upstream GET within the existing ingest run,
AFTER discharge persistence, with short timeout and component-only failure health.
This is a new backend source read, not a new scheduler. Use conditional HTTP when
available and do not rewrite unchanged creation_time; never carry forward on known
fetch/parse failure. Reuse the authoritative FOEN/BAFU feed already used by frontend
fetch tooling where feasible. Code0 means no classification. Preserve authoritative numeric codes; labels come
from the reviewed legend mapping, not a claimed official numeric-label mapping.
A feed-wide creation_time is not an individual measurement time. Verify scheme, source URLs, attribution, coverage,
update cadence and payload bounds before wiring it into existing collection.
Never manufacture seasonal classes from absolute discharge or danger thresholds.
Use a small station-status store/domain type for source-timed status facts where
these do not belong to individual measurement rows. Upsert atomically per station;
retain source identity and collected/source timestamps, plus fetch health.
Do not expand the shared predicate list used by the strict archive collector.
Keep its query/parser contract unchanged; operational status selection is separate.
Parse discharge and statuses independently BEFORE store writes, including the
unit-aware capture path: malformed status cannot fail/quarantine valid discharge;
malformed discharge cannot fabricate a status failure. Test both directions.
No history warehouse, new scheduler or duplicate upstream collector. A status-feed
failure must not roll back valid discharge ingestion; mark its component unavailable.
Unmatched, excluded or unapproved stations are not newly onboarded by ingestion.
Absent classes/levels are explicit unknowns and do not delay live discharge/forecast
release. Their ingestion is in this delivery, not a promise hidden in UI defaults.

## Threshold onboarding, database first
Use a separate small station_display_thresholds table, not station_thresholds.
Key by station, BAFU integer level2..5 and parameter; enforce tenant ownership; persist value, unit,
source, source reference, verification metadata, import ID and content digest.
Do not reuse operational Low..VeryHigh names or the operational level1 threshold.
Existing alert/forecast/skill readers and writers cannot see these rows, including
old images during rollout/rollback. No operational-reader refactor or use enum.

Owner requested the already-used inferred bounds for private reviewer display,
not public publication or warnings. Preserve those restrictions in API/UI and
provenance; no scientific promotion. Validate the supplied37-station private JSON
against archived raw chart evidence and record source hashes/references. Missing
evidence blocks that station's import; do not silently waive verification.
No private reference payloads in Git or static serving.

Small import service/CLI, dry-run default, explicit apply/replace, one all-or-nothing transaction (any invalid entry aborts the entire run),
validated station ownership/approved roster, discharge/m3/s, finite increasing full
level2..5 profiles, and digest-based idempotence. Only inferred profiles are supported in this release; operational authority
profiles stay in their existing table and are never read/written by this importer. Missing entries never delete other stations.
Station onboarding includes a documented metadata step using this same CLI,
not a code hook into the full AUTOCOMMIT onboarding pipeline: establish station
identity and evidence, review/admit its manifest entry, then run the transactional
metadata import. Missing evidence leaves metadata unavailable without failing
station creation. This is the minimal onboarding integration for this delivery;
automatic hook is deferred. Existing-station metadata import never invokes full
onboarding, migrations, history loading, training or assignments.
Test zero unrelated writes, atomic conflict rollback and old-reader isolation.

No separate display-metadata route. Overview contains small profiles and the panel
reuses them within the three-request station-open budget. If byte target is
exceeded, deduplicate profiles in a header lookup, not extra per-station calls.
Consumer station detail remains unchanged and cannot read new profiles.
Private inferred data is already tracked/staged in the frontend; PhaseD removes
that dynamic asset/build dependency. Do not add it to backend Git. History cleanup
is a separate owner decision, not an automatic destructive rewrite. Keep private
access controls while the old panel source is replaced and test behavior parity.

New status/display tables: API role SELECT only; shared sapphire_worker role gets writes
to status/component-health tables (ingest/forecast/skill use that same role); metadata CLI uses the existing migration/
bootstrap administrator credential explicitly, never the API/reviewer token.
No new service credential. Exclude station_display_thresholds from bootstrap broad public-table grants;
explicitly grant API SELECT, not sapphire_worker. This is per-table isolation,
not an imaginary role separation between ingest and forecast/skill.
Protect every new display/status/component-health table from the generic read-only table-browser shortcut using
existing protected-table policy; explicit scoped overview/import are the only paths.
Bootstrap grants are part of additive migration rollout and tested with real roles.

## HTTP and frontend
Success, errors and preflight are no-store. Explicit TTL0/bypass of existing Worker in-memory cache for this route;
60s visible-tab polling is accepted within existing reviewer/page budgets. No claim
of one global origin request per minute. Do not cache errors or use stale-on-error.
Authenticate before reads; scope/hold/publication checks apply on every request.
Explicit scoped GET-only proxy/CORS entry, not broad prefix access; preserve Access
and session checks and response-size/timeout limits. Prefer503 for unavailable
service; do not claim429/Retry-After enforcement that is not implemented. Back off
on failures within request budget. Sleep/restart is an outage, not permission for
stale data to look current. User LaunchAgent is not an unattended boot guarantee.
Update map_contract.py route/auth sets and version2.3→2.4, publication_cache.py,
errors.py, cors.py and auth-matrix tests. No ETag/shared-cache feature in first delivery. Compression may use existing
infrastructure if correctly supported and verified; not a prerequisite for correctness.
Target p95<1s for134 stations under representative load. The owner approved a
PhaseB limit of <=256KiB raw JSON after reviewing the 238728-byte representative
response, replacing the original150KiB target. The isolated archive-history
benchmark measured p95 0.718499s; this is not live/load admission. Its
`raw_payload_target_met` field still records the original150KiB comparison, not
the revised approval. Measure compressed bytes if enabled; measured compression
is not proof of deployed compression. Reassess the budget before PhaseC metadata
is added. Further adjustments require explicit review of evidence.

Frontend consumes only this route for dynamic overview; exactly3 station-open
reads remain unchanged. Remove Swiss staged dynamic dependencies after passing
live checks; keep geometry, roster and unrelated Nepal/static assets. Show source
ages, missing/quality states and independent component failures in every view.

## Phases and exits
A — Contract/examples and source evidence. Supply maintained OpenAPI2.3/latest
shape; agree fields, timing, trend and peak semantics. Capture source samples
without secrets. No live writes. Independent reviews before implementation. Record deployed evaluation config/CMAL
allowlist, tenant gate, archive mount, ingest cadence, source payload timing and
threshold evidence. Source reconnaissance is read-only. If reported levels have
no verified existing source, escalate that field to owner rather than invent a
collector or silently omit agreed work. Unresolved source questions block C, not B.
B — Live discharge/trend and authoritative/BAFU headlines: bounded store queries,
route, scoped proxy and frontend adapter. Unknown status/threshold fields explicit.
Exit: live map/forecast values update without build; no staged fallback.
C — Reported levels/classes and threshold onboarding: separate display-table migration/import,
existing ingestion integration, API detail/overview display reads. Exit: source
coverage and unknowns measured; idempotent import; alerts/skill unchanged.
D — Finish display removal and operational acceptance; B can ship before C while
missing metadata is honest. Full delivery requires both B and C, except documented
upstream absence for particular stations. Do not hold live B for optional metadata.

```json
{"A":[],"B":["A"],"C":["A"],"D":["B","C"]}
```

Tests: synthetic-token real-PG scoped134/excluded cases; no scope leakage; future/held/protected
and changed-QC visibility; exact hourly boundary/gap cases; combined stale+QC;
CMAL missing/rejected/partial publication and BAFU-only availability; median peak,
ties and elapsed horizon; source undefined/absent/failure; threshold authority
conflicts, idempotence, partial failure, unit mismatch and alert/skill noninterference.
Use existing fakes and real-PG boundary tests; no full-fleet production benchmark
without explicit bounded admission. Use representative isolated fixtures first.
Native lint/format/types, focused tests, full suite after final code changes,
independent Claude+Codex patch reviews and extra boundary review for high risk.
Affected canonical API/types/security/ingest/onboarding docs updated with code.
Rollout order: additive migrations plus bootstrap grants; backend route disabled
until configuration validated; verify scope/gate/hold tests; enable scoped route;
frontend integration; threshold import last with digest/import IDs and tested
delete/replay of that import. Do not downgrade across existing migration barriers.
Owner approves/merges PRs; guarded release/version workflow, receipt verification,
minimal staged rollout with no active-run interruption. Migrations require explicit
recovery/rollback assessment; prior observation-only backup is not full DB recovery.
Acceptance: deployed revision, sanitized browser trace, live values changing across
two ingest cycles without rebuild, correct issue times/bands, unavailable on failure,
unchanged station panel/Nepal. Report source freshness separately from render speed.

## Review and decisions
DRAFT revision4; no approval claimed. Codex/Claude/boundary reviews received;
material revisions require another full review. Do not call an observations-only
release completion: B explicitly includes CMAL and BAFU headlines. Missing C fields
are returned as unavailable/not_onboarded with null values and thresholds[]. Review simplicity, feasibility of bounded reads,
forecast authority reuse, status timing, display-only threshold isolation, and
migration/access safety. Resolve material findings before code. First-pass timing
and payload targets are proposals, not measured service guarantees.
Deferred: water-level/stage ingestion (unit+datum), Swiss local-day aggregation,
new warning science, unlimited viewer capacity, global cache, backup redesign.

## Independent design review outcome
Claude and Codex completed full revision4 reviews with no remaining design
blockers. An additional independent boundary review also found no blockers after
its interrupted run was retried. Corrected selector type/frozen-contract naming
and explicit package-data inclusion per review. Full reports stay local.
This records design review, not implementation approval or fulfilled PhaseA
source evidence. No live services or production data were changed by planning.

## Phase B implementation admission
Owner merged PR433 and explicitly authorized implementation after coordinating
with the Nepal deployment session. Root orchestration admits PhaseB following
independent Claude/Codex/boundary reviews with no design blockers and read-only
PhaseA checks:134 roster entries exactly match approved reviewer grants and one
Swiss tenant; evaluation is STANDARD CMAL, publication gate inactive, archive
mounted; ingest5min and BAFU collection hourly. PhaseC source/threshold evidence
conditions remain required; this is not permission to import or activate them.

Implementation base is f46ef1310fdef76b5023e6b5b059ded6ca678384 (merged PR434),
including native rating migration0079 and OpenAPI2.4. Preserve discharge and
water_level latest reads, provisional lineage isolation and all existing metadata.
The overview's additive export version will be2.5, superseding the old2.3→2.4
number in the reviewed plan. No PhaseB migration is needed. Before PR publication,
the branch was refreshed onto merged Aquacast PR435 at
c05fe409f3d0b6c2842fb4f798214c40fa74a59f, preserving its dependency and lockfile;
Swiss PhaseB uses release target0.1.1098 after Nepal's0.1.1097.
Run both benchmark shapes together as a regression check against the shared
committed test database, with disjoint station codes and no benchmark/profile
output environment variables. Capture timing/payload evidence in separate runs
selected by `-k production_quantiles` or `-k stress_members`, using distinct output
paths; profiling applies only to `stress_members`. Keep production timing isolated
from the stress case's committed rows.
No Infomaniak operations; Nepal session retains exclusive ownership of its
sapphire-nepal21-test runtime and active catch-up ingest worker. Coordinate shared
contract changes and target/migration allocation before committing them.
