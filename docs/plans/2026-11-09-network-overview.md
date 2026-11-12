# Network overview — implementation plan

Status: DRAFT for root-commissioned independent review; no code execution authority
is claimed by this file. Owner merges; root commissions Claude/Codex plan and patch
reviews and the required relevant high-risk review. No live operations.

## Accepted intent, baseline and reconstruction

Implement slice A of
`planning/visions/2026-11-09-network-overview-and-reported-water-level-thresholds.md`
(exact SHA256 `9dbe42b232057d60290c5d68f5d73583ae2eb4654278ea80c4a7c5668c544828`).
PR485 is merged into `main` at `87bc5e711e0270526c7f38f58fb2aeaeab9fb967`;
fresh fetch/regular100644 target-copy equality verified. That is the accepted intent baseline. On10November the owned branch was
rebased to fresh main ecea3e64 (merged history PR487, migration0083, map2.10,
release target1122); no implementation PR or code changes existed. The untracked
original plan was preserved outside the checkout before this focused revision. No open PR existed at reconstruction. Refresh main/PR evidence before
integration and release-target bump; parallel Aquacast dependency adoption belongs
to its owner. No pyproject/lock/dependency changes are planned here.

Five-way reconstruction: merged = accepted vision plus existing Swiss overview,
history, scoped latest observations and explicit provisional reads; open = no
implementation PR discovered; abandoned = no relevant delivery path established;
incomplete = this local plan only; remaining = all A implementation and acceptance,
then B reported-WL display/import/export, then derived-Q/frozen provenance. Historical
generic-overview/23-installation threshold drafts are context, not execution plans.

Owned clone: `.worktrees/visions/network-overview-implementation`, branch
`feat/network-overview`. The former clean documentation clone was removed only after
PR merge, exact target-content and equal-tree/no-local-change proof. Canonical and
other agents' checkouts remain untouched.

## Scope and vertical delivery

One additive route provides complete authorized network rows, latest measured WL/Q,
72 completed UTC-hour samples, per-model/purpose forecast headlines and compact
median/bands, freshness/quality, safe conditional polling and bounded performance.
Keep existing routes unchanged. No frontend code, threshold admission, conversion,
alert activation, model runs, ingestion, schema migration, new grants or live writes.
B remains a separate second/third delivery. Existing configured thresholds may remain
out of A rather than adding unrelated query or provenance work.

Implement A in one coherent feature PR unless review requires a smaller vertical
split. The plan phases below are sequential development gates, not independently
complete releases. A is not delivered by adding an empty endpoint or schema alone.

## Source facts driving the design

- `api/routes/api_swiss_overview.py` and `config/swiss_overview.py` are fixed-roster,
  discharge-only, QC-passed/daily-authority projection, with BAFU comparison. No copy
  of its region-specific settings can satisfy this route.
- `store/observation_store.py::fetch_latest_observations` uses one batched lateral
  latest query and explicit right-closed hour bounds; `unit_read_predicate` composes
  unit/quarantine eligibility. Single `qc_status` cannot express the new cohort.
- `types/enums.py::QcStatus` includes BOTH `RAW` and `QC_UNCHECKED`; these are not
  synonyms for passed. The new numeric cohort is finite RAW/QC_UNCHECKED/QC_SUSPECT/
  QC_PASSED, not MISSING/QC_FAILED. No generic nonnegative filter: negative
  datum-relative WL remains valid. Apply parameter-aware checks to forecast values
  as well as observations; never change stored QC/science.
- `api/security.py::Principal` has consumer/reviewer/admin. Existing scopes are
  materialized and auth is resolved on each request using the same request connection.
- `docs/standards/security.md:85–90` records the 31 October owner permission for
  scoped reviewers to read protected delivery discharge through station detail.
  Lines2190–2200 separately describe the deliberately narrower old latest projection.
  There is no universal ban on private reviewer aggregation. Root accepted a NEW
  route contract equivalent to already-authorized detail history, not alteration of
  the old latest route. This distinction must be explicit in docs and review.
- `api/provisional_forecasts.py` selects safe scoped headers before native lineage
  and values, validates artifact/issue/parent agreement and hides curve provenance.
  Keep that ordering. `DailyClaimKey` rejects nonstandard purposes: NEVER route Nepal
  provisional forecasts through the Swiss daily ledger or weaken that type invariant.
- Standard publication selection is keyed by tenant/station/parameter/issue, not
  model. `PgForecastPublicationStore.lock_read_snapshot` prevents withdrawal commit
  while a selected projection is assembled. Preserve selection priority over naive
  newest-by-model rows. Current-generation filtering excludes superseded outputs.
- `api/publication_cache.py` forces existing routes to no-store. CORS currently does
  not admit If-None-Match or expose ETag. Only the new route gets a conditional policy.
- Current map contract version is2.10; this additive route requires the documented
  minor increment and regenerated committed schema, not changes to old shapes.

## Proposed request and wire contract

`GET /api/v1/networks/{network}/overview`

Exact network identity, bounded string parsed once; no fuzzy matching or implicit
country mapping. Default forecast `data_use=standard`; allow a repeated constrained
`data_use` query to request standard and/or provisional_rating explicitly (at most
two unique values). A consumer asking for provisional gets403 before forecast reads;
we do not silently omit a requested unauthorized section. Reject unknown/duplicate
unsupported query fields after authentication. No retrospective as_of, client-supplied
station roster or unbounded history query is introduced.

Both observation parameters and exactly72 slots are fixed by v1. Latest-reading
window is168h, disclosed as latest_lookback_seconds plus generated_at (no moving
start/end wire fields); not the chart window. Network roster is
all authorized stations, sorted by stable station ID. No limit/offset pagination.
Empty authorized scope returns the same404 as an unknown network, no hidden total.

Response contains:
- schema version, generated_at (assembly time of this full200 representation),
  selection bounds (latest lookback duration relative to generated_at, and hourly
  grid end/step/count), network and
  authorized station_count only;
- station ID, code/network, display name/river/coordinates where stored, and measured
  parameter metadata; no new station inference;
- separate water_level/discharge observation objects: canonical unit, source,
  latest nullable timestamp/value/QC, latest_failed_at, newer_failed nullable when
  no accepted latest exists, and72 compact samples with actual timestamp/value/QC
  or null. States explain absent eligible data without disclosing denied history;
- forecast models/output-purpose entries with IDs, parameter/unit, actual issue,
  original horizon, freshness/expected product issue/deadline where declared,
  provisional and input-quality labels/codes, remaining peak window and median/time,
  same-time p25/p75, compact timestamp/median/p25/p75 series with reduction metadata,
  and existing full-detail link. Availability and freshness are separate enums;
- no_forecast at station level where no applicable output exists; per-model no-data,
  expired, withheld/ineligible, unavailable-band and unknown-freshness states remain
  distinct. Infrastructure/evidence failure is503, not an all-empty200.

Boundary Pydantic schemas convert to frozen typed domain request/projection values.
No internal Pydantic domain logic, bool-as-domain-state or unbounded generic dict API.
Reuse canonical parameter units (m and m³/s) without rewriting stored measurements.

### Entitlement matrix (new route only)

| Section | Consumer | Reviewer | Admin |
|---|---|---|---|
| Station roster/metadata | own tenant+granted stations | same | existing authorized admin scope |
| Measured WL/Q | same scope; exclude protected delivery | same scope; existing detail-history permission includes protected delivery | existing detail-history permission |
| Standard forecasts, publication gate off | authorized, eligible ordinary reads | same | existing standard authority |
| Standard forecasts, gate on | selected published authority only | same; no review-value bypass | no new aggregate override of selected authority |
| Provisional forecasts | explicit request403; no presence lookup | explicit request, scoped safe headers+native lineage | explicit request, existing admin scope+lineage |

No grant changes, credential substitution, human-session auth or permission union.
Observation trusted-unit/quarantine/currentness/native-custody guards apply to every
role. Protected delivery entitlement is applied INSIDE the SQL cohort before latest,
hourly, failed-marker, counts and limits. No raw bypass or QC_CONTEXT/SOURCE_AUDIT read.
Keeping the old latest/Swiss exclusions byte-compatible is a separate regression.
The private reviewer permission is not public redistribution approval.

## Observation batch design

Add `fetch_overview_observations` on the observation store/protocol with typed scoped
station IDs, both parameters, as_of,168h latest start and the role-derived protected-
delivery visibility policy. Do not accept a caller boolean claiming trusted data.
Keep unit/currentness composition at this store boundary. The admitted typed ObservationSource cohort is MEASURED (label measured) plus
authorized MANUAL_IMPORT (label supplied). RATING_CURVE_DERIVED and COMPONENT_DERIVED are
excluded. Preserve source on each selected sample. Equal-time cross-source winners
are ambiguous even when their values agree; do not hide a conflict by choosing an
older sample. Test measured/manual-import ties and protected manual-import Q for
reviewers versus consumers. The new method must use
the same scientific-read predicates as supported ordinary reads, not a copied trust
subquery in an API handler.

Use one bounded SQL projection for the station×parameter cohort and windows:
- latest eligible finite numeric sample within[start,as_of];
- latest eligible sample per72 grid windows (end-1h,end], with timestamp/version ordering within each admitted source; conflicting
  equal-time source winners return an explicit ambiguous_source null sample,
  never an invented measured-versus-supplied precedence;
- latest QC_FAILED timestamp in the same permitted scope/window, independent of
  accepted-status filtering. It can be newer than selected accepted data; missing
  accepted data leaves comparison unknown but retains a failed timestamp if visible.

A lateral indexed top1/window query or DISTINCT ON group projection can be selected
by local EXPLAIN/benchmarks; no station-loop SQL or72 queries per station. Project
only returned sample/quality fields, not full raw history or unnecessary QC details.
An explicit statement timeout bounds pathological range work. Do not claim returned
row caps alone bound all database scan work. Keep exactly72 null slots even if no
eligible samples. No averaging, filling, interpolation or unit-derived Q.

## Forecast authority, batching and freshness

Batch the active station and group-model assignments for scoped roster first. Include
assigned models with no outputs, without creating a new model registry or instantiating
models. Output identity is station/model/parameter/purpose; purpose availability is
not inferred from another section. Known declared output parameters come from existing
contracts where available; absent output declarations can use an explicit model-level
no_forecast rather than inventing a WL output. Cap assignment/output scope explicitly.

Three separate paths feed one projection:
1. Configured standard daily models: preserve whole-summary daily authority and its
   per-member references. Reuse/equivalently batch request daily readers and pure
   candidate validation. Add a bounded multi-key daily snapshot read instead of
   invoking read_day once per group/day; preserve its snapshot validation and authority
   semantics. Reject/missing reference integrity fails closed. Do not fall
   back to newer unreferenced rows. A DailyOutputSource.REJECTED reference always
   yields withheld/ineligible with no numeric read, decode or fallback. Accepted daily
   references require QC_PASSED or QC_SUSPECT. For gate-on tenants, intersect each
   accepted reference with the selected publication ID for its station/parameter/issue
   BEFORE loading or decoding values; a different selected model also withholds it.
   This applies to every role, including admin; no Swiss admin override is reused.
2. Other standard outputs: select nonsuperseded eligible ordinary rows per output
   key; if publication gate active, intersect with selected publication authority
   under its existing read lock BEFORE values. No numbers from other models merely
   because a model has an assignment. Return safe generic withheld/no-eligible
   state without probing hidden candidate counts or identities. Preserve existing
   standard-purpose eligibility. Existing ordinary and provisional readers impose
   no forecast qc_status filter: retain RAW, QC_UNCHECKED, QC_PASSED, QC_SUSPECT,
   QC_FAILED and MISSING as existing visible forecast QC labels, not observation
   eligibility or stored rejection status. Do not invent a PASSED/SUSPECT-only
   filter for these paths. Numeric frames must still satisfy representation/finite
   checks. Storage/publication rejection and DailyOutputSource.REJECTED remain
   distinct from a forecast QC flag; no new rejection predicate is inferred.
3. Provisional: safe headers joined to authorized station/tenant, requested purpose,
   nonsuperseded state and issue<=as_of, then materialize authorized IDs. Batched value
   decoding uses existing purpose-safe forecast_columns/native_lineage boundary.
   Apply the existing provisional parent/artifact/issue/lineage agreement checks per
   selected output. Do not query native lineage on unscoped IDs, add daily claims,
   or expose current rating_curves/raw snapshots. None of this bypasses publication
   for STANDARD; provisional remains explicitly not_applicable to publication.

Use bounded batched header/value queries, not the existing per-station summaries
method in a loop. Set an initial gate of at most20 application data SQL statements
per request (authentication/last_used bookkeeping counted separately); query count
must not scale with stations, models or hour slots. Record the exact count in tests.
Use a local per-statement timeout (proposed2seconds) as a safety bound, not as evidence
of the1second p95 target. Extend appropriate existing stores/readers with narrow batch methods
and reuse decoding/validation instead of duplicating evidence logic. A selected
candidate missing after read or failing identity/lineage is unavailable503, not null.
All three paths use the exact sa.Connection yielded by api.deps.get_connection
(engine.begin, one request transaction). The bounded daily reader uses
PgDailyForecastStore(lambda: nullcontext(conn)), matching _RequestDailyReader; it
never invokes an engine transaction factory or opens a second connection. Acquire
PgForecastPublicationStore.lock_read_snapshot on that same connection before daily
or ordinary publication intersection and hold it through projection serialization.
Publication locks retain existing transaction scope; no global isolation-level change
or second API connection is planned. Revalidate selected identity/currentness when
selection and value reads can race; tests cover supersession/publication races. The
response is a current authorized request projection, not a claim of historic MVCC
snapshot reconstruction at generated_at.

Select newest eligible issue per applicable model/parameter/purpose with deterministic
issued_at/version/ID ordering. Expired support is explicitly expired, not silently
replaced by an older issue pretending current. Bound header lookback to168h initially,
disclose it, and prove daily plus declared non-daily cases; missing policy cannot be
called current merely because a long horizon remains. Retain original detail/history
APIs even when the overview no longer includes old candidates.

Add a small typed overview freshness-policy config at the deployment boundary,
keyed by tenant/network/model/parameter/purpose with ambiguity rejected. It specifies
product timezone, actual issue-time convention and expected delivery/issue boundary;
it does NOT schedule a run or change issued_at. Reuse current evaluation daily
settings for standard Swiss issue-time authority. Nepal provisional policy is an
explicit configured daily product: scheduled runtime06NST, actual persisted issue
00UTC/05:45NST, deadline07NST. Provide a reviewed config example, no live overlay edit.
A model without an authoritative applicable freshness policy returns freshness_unknown,
not a hardcoded Nepal/CMAL rule. An eligible current product-day output visible in the
request wins early; prior output stays current before deadline, STALE at/after it
until replacement becomes visible. Test midnight-issued replacement visibility,
not invented00:15 issued_at values. No changes to forecast execution or daily tables.

Peak-window metadata reports the first/last retained support bounds, not a moving
wall-clock start that would change the ETag every poll. Remaining support uses actual
valid-time/cadence intervals; no inferred interval for
missing/ambiguous new-contract metadata. Keep current interval if end>as_of. Compute
median maximum and earliest tie, retrieve p25/p75 at that instant. Exact stored
quantiles use level lookup; actual members use documented type7 quartiles with the
existing minimum-member policy. Reject nonfinite/malformed frames; parameter-specific
physical constraints never reject valid negative WL using a generic Q filter.

## Compact series and explicit resource admission

Initial review/measurement limits: up to200 authorized stations; up to4 output entries
per station across model/parameter/requested purpose; up to512 native valid times per
selected output and250,000 selected forecast value rows overall. Candidate/header
bounds are checked before loading all values; unauthorized rows never count. Add a
bounded number of daily groups/issue candidates (proposed16 groups and7issue days)
so daily-ledger work cannot escape the limits. Select only latest and authoritative
attempts plus a scalar recovery-existence result per key, never materialize all retry
attempts. Before loading frozen summaries, bound selected attempts to224, aggregate
frozen/outcome octet lengths to8MiB and nested member/reference counts to25,000;
check byte lengths in a separate query before parsing/decoding. These provisional
caps must pass representative fixtures. Validate complete selected summaries without
truncation; group members outside grants must never cause forecast-value loads or
expose candidate counts. Test retry-heavy keys and groups spanning caller grants. Fail503 safely on an exceeded bound,
not partial roster/model success. These are admission proposals to validate offline,
not measured guarantees; any limit change is explicit in docs/tests/review.

Compact series budget24 points per output. Keep all points when<=24. Otherwise retain
first/last, exact peak and both sides of each genuine gap; fill remaining budget by
deterministic time-distributed original points. No numeric interpolation. Include
original/returned point counts, reduction method and explicit segment/gap boundaries
so the renderer cannot join across missing support. If mandatory anchors alone exceed
24, return explicit compact-series-unavailable with full-detail link and valid exact
headline, not a misleading truncated chart. This is a documented supported shape,
not hidden omission. Benchmark full admitted model/output counts including this case.

Targets: p95 about1second and gzip response<1MiB at21 and160 stations, and a200station
admitted-bound case. Report cold/warm plus concurrent60s-consumer workload. Count
queries, examined/returned rows, raw/gzip bytes, machine/Postgres/native shapes; do
not turn point arithmetic into performance evidence. Use realistic differing numeric
values/missingness/quality flags, not all-zero highly compressible data. If targets
fail, report/fix bounded query/design causes before adding indexes/materialization or
raising limits; those need separately reviewed justification. No staging benchmarks. Report offline gzip estimates separately from actual HTTP
Content-Encoding/gzip delivery measurements; the existing API has no gzip middleware.
Do not describe an offline compressed byte count as negotiated wire compression.

## Conditional response and CORS contract

Recompute the authorized bounded projection on every request; no server/shared cache
of personalized bodies. New route200/304 use `Cache-Control: private, no-cache,
max-age=0, must-revalidate`, `Vary: Authorization, Origin, Accept-Encoding`, and a weak
ETag over canonical semantic projection plus token identity/role/tenant/granted scope,
requested purpose/network and representation version. Never include bearer secrets.
Errors, authorization failures, redirects and OPTIONS are no-store. Preserve existing
PublicationCacheMiddleware route behavior; do not remove old paths from it.

Exclude only full-body assembly `generated_at` and purely descriptive instantaneous
check time from the semantic ETag. Do not emit dynamically computed numeric age that
would invalidate every poll; clients compute age from unchanged actual timestamps.
Hourly selection bounds, latest search cutoff eligibility, remaining forecast support,
selected IDs/values/QC, failed markers, policy/deadline/freshness, publication and scope
must affect semantic equality. Do not include the moving168h end timestamp as a
meaningful data-change surrogate: expose latest lookback duration and the full200
as-of, while recomputing selection against current time before deciding304; a sample
aging out changes the projection. Use stable hour bounds and policy issue/deadline
instants in the validator. Time-only transitions at hour/deadline/support end must
invalidate even with no inserted rows.

304 follows successful fresh auth, scope/purpose/publication eligibility checks and
complete projection computation. It retains the client's previous body and that
body's truthful generated_at; current HTTP Date is check time, not a new observation
or body timestamp. No304 on read/lineage/policy/limit failure. A new token cannot reuse
another token's validator; grant removal changes scope and denied authorization
returns401/403/404 rather than304. Test wildcard/list weak ETag behavior explicitly.

Route-only CORS permits If-None-Match and exposes ETag/Date for existing configured
consumer origins, GET only. It does not add origins, credentials, methods or headers
to other endpoints; preserve Vary Origin and error/304 headers through middleware.
Update static OpenAPI304/headers/examples and the security standard with this exact
exception, not a claim existing no-store routes are cacheable.

## Concrete touchpoints and protection boundaries

New stable responsibility modules (names subject to ordinary code review):
`types/network_overview.py`, `config/network_overview.py`,
`services/network_overview.py`, `api/network_overview_schemas.py`,
`api/routes/api_network_overview.py`; forecast batching belongs with existing
forecast/provisional/publication readers, not a new Manager/framework. Add narrow
store/protocol methods for roster/assignments/observations/selected IDs as needed.

Wire route through existing app/router and principal dependency; update deployment
config, route-only CORS/cache policy, map contract/snapshot examples. Update
`docs/spec/api-v1-review.md`, security, relevant touchpoint map and bounded consumer
handoff. No new table/schema, RLS grant, operational writer or provider client.

Concurrent453r14: coordinate every added direct observation SQL/read seam with its
owner. It plans composing preparation currentness with unit/quarantine at store reads
and shared `ordinary_forecast_rows` (tables/dashboard aggregates). No helper name is
allocated; do not depend on its unmerged private schema or copy its predicate. Check
stale old winner, held, valid nonwinning alternative, approved current winner and
unmarked legacy behavior; lower-priority arrival must not hide unchanged winner,
collection-permission expiry must not hide valid reviewed history. A cannot implement
453 or weaken its future boundary; rebase integration must include both test sets.
Preserve existing native17/legacy21/FI/weather/frozen ledgers and OFF hooks.

## Test and execution gates (offline only)

P1 — Contract/selection red tests: explicitly retain provisional v2 QC_UNCHECKED,
hide DailyOutputSource.REJECTED values with no fallback, and exclude observation
QC_FAILED values while retaining the independently authorized failed marker. new domain/config/service tests and API fixture
contracts. Test all six QC enum cases, finite invalids/valid negative WL, newer failed,
no accepted sample, old latest outside72h, exact UTC slot edges and late corrections.
Test each output/purpose, authority-first selection, variable cadence/current interval,
earliest peak ties, same-time bands, missing/duplicate quantiles, members, reduction
anchors/gaps/bounds, unknown freshness and Nepal/Swiss independent boundary clocks.

P2 — Stores and scoped API: implement batched projection and request pipeline against
native isolated PostGIS fixtures. New integration tests use actual sapphire_api role,
consumer/reviewer/admin principals and native provisional lineage guards. Assert no
value/lineage read before scoped header materialization. Consumer protected rows and
unrequested provisional metadata never influence counts/markers/timing diagnostics.
Preserve other tenant denial, currentness/unit holds and publication withdrawal races.

Functional Nepal fixtures mirror retained proof, not a fabricated all-pass story:
recovery receipt recorded5251newrows/14stations/13same-dayfresh, and Bimalnagar detail
had1092 measuredWL samples (1090passed,1suspect,1unchecked), no measuredQ in that window.
Two current retained provisional examples have10 valid times and seven levels
.05/.1/.25/.5/.75/.9/.95 with complete p25/p50/p75. Reproduce representative shape,
role and actual absence semantics with safe local test fixtures; do not commit private
raw observations or claim a fixture is a fresh live read. Assert nonempty authorized
WL+forecast sections for the representative reviewer; absent measuredQ stays null,
not substituted from forecast/derived inputs. Separate fixture checks already-
authorized protected historicalQ and its consumer exclusion. Failed/all-null200 alone
cannot satisfy A. All future live acceptance remains separately authorized/read-only.

P3 — Conditional/auth/OpenAPI: fresh request before304, late insert/QC correction,
unit hold, scope revoke, token change, purpose denial, publication withdraw, hour/
deadline/horizon time-only transitions and no-store errors. At least one native fixture uses real bearer token authentication on the request
connection, with no require_principal override: disable/expire the token and remove
persisted grants between200 and conditional requests. Assert denial/no-store and no
reused ETag, plus daily-reference publication mismatch and withdrawal races on the
same request transaction for each role. Existing route/header
regressions, CORS preflight/304/Origin isolation, auth-before-input-error and static
schema drift/version. Run full security/map-contract regressions, not only new tests.

P4 — Real offline benchmark and integration: PostGIS native query/route end-to-end
21/160/200 station shapes, four output keys and declared maxima, warm/cold/concurrent
refresh, quantified query/row/gzip budgets. Borrow diagnostic safety patterns from
`tests/integration/store/swiss_overview_benchmark.py`; no full payload/secrets in logs.
Retain benchmark receipts outside commits and concise outcomes in PR. Scope/limit
error proof and nonempty functional assertions accompany speed numbers.

Planned native checks (final names follow files):
- `uv run pytest tests/unit/services/test_network_overview.py tests/unit/api/test_network_overview.py tests/unit/config/test_network_overview.py`
- `uv run pytest tests/integration/store/test_network_overview.py tests/integration/api/test_network_overview.py tests/integration/store/test_network_overview_performance.py`
- Existing `tests/unit/api/test_map_contract.py`, security/CORS/publication tests,
  `tests/integration/api/test_provisional_forecast_reads.py`,
  `tests/integration/api/test_forecast_reader_isolation.py`,
  `tests/integration/store/test_overview_exact_batches.py`, Swiss overview/history
  boundary tests, and forecast current-boundary tests.
- Ruff check/format check and changed-module type checks in this clone's uv/native
  environment. Use testcontainers' isolated PostGIS, never operational DATABASE_URL.
- Full suite after final code change locally or CI before owner merge. Missing Docker,
  dependency or other environment capability is a reported limitation, not a skip
  presented as success. No kernel imports to substitute for native tests.

Before any code, root dispositions the independent plan findings. Before PR, review
full patch independently with both required models and owner-commissioned relevant
security/scientific review; validate against currentmain and concurrent453/Aquacast
changes. Bump RELEASE_TARGET once using current native tooling against testedbase;
no dependency/version churn. Owner holds merge. After merge, verify target effects
and leave B reportedWL and derivedQ completion explicitly remaining. Deployment,
live activation and imports are not part of this implementation authorization.
