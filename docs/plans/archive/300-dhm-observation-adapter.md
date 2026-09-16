---
status: COMPLETE
created: 2026-09-16
plan: 300
title: DHM water-level observation adapter from the confirmed API contract
depends_on: []
---

# Plan 300 — DHM water-level observation adapter

## Problem and outcome

SAPPHIRE's production observation-ingest setup constructs `HydroScraperAdapter`
for BAFU. DHM supplies water levels through an HTTP API whose requests and responses
are now available. Build a DHM adapter that can be tested offline before Nepal
network access is provisioned, then selected through deployment configuration.

This is the first, bounded observation-adapter slice of Plan 106 D5-2. Plan 047 is
a stale umbrella stub, not implementation authority. Plan 143 owns broader Nepal
station onboarding; rating-table conversion remains separate.

The owner selected plan number **300** on 2026-09-16. This replaces the uncommitted
274 draft; there is one active plan for this adapter.

## Confirmed evidence

- On 2026-09-16 the owner relayed BIPAD's technical contact's confirmation that
  the captured requests and responses exactly replicate the upstream DHM API.
  Treat the request/response contract as confirmed; do not request it again.
- [Working examples and raw captures](../../requirements/dhm-api-examples/README.md)
  include latest metadata, history, continuation and empty pages. The manifest
  preserves original public-host URLs and response hashes.
- Local HTTP tests exercised the BIPAD host without credentials, not DHM directly.
  The direct base URL, authentication and network provisioning remain deployment
  inputs. No claim of a successful direct-DHM test is made.
- Station 168 / series 21379 supplies `waterLevel` and offset-aware `waterLevelOn`.
  The portal labels metres. The official DHM-code mapping and gauge datum are not
  established by the number in the station title or its `elevation` field.
- Actual responses are paginated objects, despite the schema describing arrays.
  `count` is a placeholder, empty pages still have `next`, and the apparent
  `__gt` filter included its lower-bound timestamp in the sampled query.

## Repository integration points

- `adapters/hydro_scraper.py` is the existing operational BAFU adapter;
  `adapters/bafu_observation.py` is a separate archive collector and is not the
  replacement target.
- `protocols/adapters.py` defines both observation-fetch capabilities;
  `types/observation.py` forbids observations on a failed station outcome.
- `flows/ingest_observations.py::_load_adapter_endpoint` reads the merged
  `[adapters.river_stations]` table, including `SAPPHIRE_CONFIG_OVERLAY`, but
  production setup always constructs BAFU. Extend that selection rather than
  adding a competing config loader or deployment-wide source flag.
- The same flow's `_cursor_parameter_for_kind` selects `discharge` for all rivers.
  A DHM station needs the latest `water_level` timestamp; otherwise the flow can
  repeatedly use its default lookback or skip observations based on unrelated Q.
- `services/qc_datum.py` subtracts `water_level_datum_masl` when non-null. It does
  not itself distinguish relative gauge height from absolute elevation. The
  adapter must not enable an accidental second datum shift.
- `config/deployment.py::load_config` discards raw adapter tables; use the existing
  merged-TOML path for adapter selection/config. Injected clients/adapters must
  remain testable without environment reads or production credentials.
- `tools/record_fixtures.py::_run_bafu` also reads the river-adapter endpoint;
  guard its source type before constructing LINDAS against a DHM configuration.

## Scope and decisions

1. **One DHM adapter with a configurable API base URL.** Use the confirmed paths
   and query fields. Inject an `httpx.Client` and a clock. No automatic switch to
   BIPAD on DHM failure. A developer may explicitly select the public BIPAD host
   for a bounded smoke test; production selects the supplied DHM host.
2. **Explicit station bindings.** Parse a deployment-local mapping from SAPPHIRE
   station code to positive API station ID into typed immutable values. Reject
   malformed binding definitions and duplicate keys at config parsing; a supported
   station without a binding fails only its own fetch before any request. Do not
   parse IDs from station titles or replace official station codes with BIPAD IDs.
   Keep this transport
   mapping out of station-table migrations in this first slice.
   Key bindings by network plus station code, and resolve to internal `StationId`
   from the supplied `StationConfig`, avoiding code collisions between networks.
   The selected DHM adapter supports DHM river gauges with measured
   `water_level`; mixed-network dispatch and rainfall ingestion are out of scope.
   The flow nevertheless passes all eligible station kinds to this one adapter:
   structurally unsupported networks/kinds (including WEATHER and LAKE) are logged
   and skipped without requests or outcomes, following the existing adapter's
   WEATHER skip convention. An otherwise supported DHM river with missing `water_level`,
   a missing binding or incompatible metadata fails only its own fetch.
   A DHM river row must have `network = "dhm"`, `station_kind = RIVER`,
   `station_status = operational`, `gauging_status = GAUGED`, and
   `measured_parameters` containing `water_level` for a supported DHM fetch.
   The flow itself gates only status and gauging status; the adapter checks the
   network, kind and measured parameter after that existing eligibility filter.
   Here GAUGED means an observed level is available; it does not claim an active
   rating curve. This plan does not change eligibility or promote station rows.
3. **Boundary parsing.** Use Pydantic only for external JSON/config, then construct
   `RawObservation` with canonical `water_level`, UTC measurement time and
   `ObservationSource.MEASURED`. Require explicit timezone offsets and finite
   numeric measurements. Zero is a valid value. Null levels produce no fabricated
   observation; malformed non-null values fail that station's fetch. Extra display
   metadata is ignored. Preserve measured datum; never infer an offset from
   `elevation`, warning thresholds or the station title.
   Reject booleans as levels/IDs (Python otherwise treats them as numbers).
   An absent required field is malformed; an explicitly null measurement is
   missing data. Validate that each returned row belongs to the requested API
   station before constructing an internal observation.
4. **Units are explicit.** This contract is metre-valued. A null unit is rejected.
   `gauge_zero` and `unknown` require `water_level_unit == "m"`; `masl` permits
   `"m"` or `"m a.s.l."`. All other units or contradictory unit/reference pairs
   fail that station's fetch before a request, with a configuration failure outcome.
   Generic cm conversion and removal of Plan 101's guard are not included; that
   part of Plan 106 D5-2 remains a follow-on. Rating-curve eligibility still
   requires a separately confirmed datum and valid rating table.
   Parse each binding's reference as `Literal["gauge_zero", "masl", "unknown"]`.
   For `gauge_zero` or `unknown`, a non-null station QC datum is incompatible with
   the current unconditional subtraction: reject that configuration, rather than
   silently applying or clearing it. An unknown reference with a null datum may
   be stored unchanged and follow existing datum-dependent QC skips; it is not
   rating-ready. `masl` may use an independently confirmed station datum under
   existing QC. Preserve raw values in storage and do not change generic QC or
   forecast datum behaviour in this slice. Supporting a relative gauge with a
   populated absolute datum field requires a separate coherent datum design.
5. **Fetch history since the caller's watermark.** Request `/river/` by numeric
   station, bounded start/end timestamps, ascending measurement time and bounded
   page size. Capture the upper time once per batch using the injected clock;
   request one second outside both window boundaries and locally retain
   `since < timestamp <= end`. This protects endpoint-boundary readings regardless
   of observed inclusive/exclusive filter naming; URL-encode offsets using HTTPX
   query parameters. Divide longer ranges into consecutive bounded windows and
   deduplicate their overlaps. Never silently clamp away the oldest portion.
   Deduplicate identical records. Conflicting values for one station/time fail
   the station until correction precedence is defined; do not silently choose.
   **Recovered history must receive QC.** For each successfully fetched DHM
   station/parameter, extend the existing QC read interval to include its earliest
   fetched measurement minus the configured preceding context, with an exclusive
   upper bound strictly greater than its latest fetched measurement (at least
   one microsecond later). Retain the existing recent-window bounds when they
   are wider. Pass all context observations to the existing checker, but
   update only RAW rows; do not rewrite stored values or re-QC finalised rows.
   A six-hour outage must not leave its older recovered rows RAW simply because
   the default QC window is two hours. The Swiss and weather default windows stay
   unchanged. Existing QC exceptions remain station failures, never a successful
   claim of QC completion. Any pre-existing RAW rows inside the extended interval
   also receive QC; rows older than that interval are not swept. This does not
   guarantee recovery of all RAW history left by earlier failed runs.
6. **Do not trust pagination totals.** Stop on empty results or absent `next`;
   use bounded windows/page counts and detect repeated pages. Continuation must
   remain on the configured origin/path and preserve station/time filters; never
   forward credentials to a response-supplied foreign host. Reaching a bound or
   failing a later page fails the whole station fetch, so its watermark cannot
   advance past missing pages. Other stations can still succeed.
7. **Use both existing capabilities.** Implement `StationDataSource` and
   `BatchStationDataSource`; the latter returns existing `HydroScraperBatchResult`
   / `StationFetchOutcome` values. A failed outcome carries no observations, as
   enforced in `types/observation.py`. A completed empty window is a clean empty
   result (`failure_cause = None`). DHM never emits `NO_DATA` for an empty window:
   a five-minute poll can legitimately find no new ten-minute measurement; treating
   it as a failure would raise false fetch-health alarms. This intentional
   difference from BAFU's snapshot-empty `NO_DATA` policy leaves BAFU unchanged.
   This also means a silent DHM gauge can produce clean empty fetches indefinitely:
   fetch health measures request success, not measurement freshness. The forecast
   input-staleness checks are not an ingest-side substitute. DHM freshness
   monitoring is an explicit activation follow-on below; supervised offline work
   may proceed, but unattended activation requires that follow-on to be resolved.
   Map transport/HTTP and malformed-response failures to existing causes.
   Do not rename BAFU-derived shared types as incidental cleanup.
   Map HTTP 429 to `RATE_LIMITED`, other non-2xx responses (including redirects)
   to `HTTP_STATUS_ERROR`, HTTPX request errors to `TRANSPORT_ERROR`, and schema,
   contradictory data or pagination-completeness failures to `MALFORMED_RESPONSE`.
   Add the single `FetchOutcomeCause.CONFIGURATION_ERROR` value for a supported
   DHM river missing its measured parameter or binding, or having incompatible
   units/datum. Unsupported kinds/networks are skipped without an outcome.
   Use the existing outcome structure and health aggregation; never disguise
   configuration failures as clean empty polls or malformed
   HTTP responses. Global malformed adapter config (invalid endpoint, limits,
   duplicate binding keys) still raises `ConfigurationError` at construction.
   Real configuration failures remain WARNING (some supported gauges fail) or
   CRITICAL (all fail), with `failure_counts_by_cause` distinguishing them from
   transport outages. Unsupported stations do not contribute to these counts.
   Keep the existing `IngestResult.stations_polled = len(eligible)` convention;
   the fetch-health record counts returned outcomes, so those counts can differ.
   Do not retry inside this first adapter slice: report transient failures through
   the existing fetch-health path and let the next scheduled run retry from the
   stored cursor. Requests have finite timeouts and a configurable minimum spacing
   using injected time/sleep dependencies; no BAFU-specific rate limit is reused.
   Log only sanitised cause/status and station context, never headers, response
   bodies, credential-bearing URLs or raw HTTP exception strings.
8. **Integrate through configuration.** Keep BAFU as the default; add an explicit
   DHM source selection and source-specific config validation. The currently
   injectable ingest adapter remains supported. Apply the skip/configuration
   failure distinction in decisions 2 and 7 without changing health aggregation.
   Do not auto-onboard stations or import
   provider warning statuses as QC decisions.
   Make cursor selection station-aware: DHM river stations use `water_level`,
   while existing Swiss river/lake/weather choices remain as currently defined.
   This must also work when the adapter is injected, without relying on its class
   or reading its private fields. Read the cursor for the measured parameter,
   never the model's forecast target. An existing discharge cursor must neither
   suppress a DHM level nor cause repeated fallback-window fetches.
   Preserve the current `ConfigurationError` on an unhandled `StationKind`;
   do not introduce a catch-all cursor default.
9. **Keep production activation separate.** Injected HTTP-client configuration
   permits offline tests and later authentication without inventing a token
   endpoint. Deployment-secret wiring depends on the actual auth scheme and is
   outside this slice. No polling schedule, Docker, authentication service, live
   database, model or ForecastInterface changes are required.

## Configuration and operational limits

Proposed merged configuration (the official station code and direct host are
deployment placeholders, not inferred from our example):

```toml
[adapters.river_stations]
type = "dhm"
endpoint = "https://DHM-HOST/api/v1/"
timeout_s = 30
page_size = 500
window_hours = 24
max_pages_per_station = 100
min_request_interval_s = 1.0

[[adapters.river_stations.bindings]]
network = "dhm"
station_code = "CONFIRMED-DHM-CODE"
api_station_id = 168
level_reference = "unknown"
```

The numeric limits above are conservative implementation defaults, **not provider
service guarantees or permission to poll at that rate**. Validate positive finite
limits and integer IDs at the boundary; use typed frozen configuration internally.
The request limit is total across all windows for a station, and exhaustion is
an explicit failed fetch, not a truncated successful archive. A short page does
**not** establish exhaustion: the server may cap the requested page size. Follow
every validated non-null continuation until an empty result or absent `next`;
never stop solely because `len(results) < page_size`. Test both short nonterminal
pages and a full final page followed by an empty page. Retain repeated-page and
page-bound protection against BIPAD's fabricated continuations.

Use the API prefix exactly (preserve `/api/v1/` when joining `river/`). Require
HTTPS without embedded credentials, TLS verification and disabled redirects.
Respect `SAPPHIRE_CONFIG_OVERLAY`; an explicitly selected invalid DHM config must
not fall back to LINDAS. When no source type is configured, retain BAFU defaults.
HTTP clients constructed for the new DHM path must close on success and failure,
including adapter-construction failure and any intervening early exit. Injected
clients and adapters remain caller-owned. Existing BAFU-client and database
connection lifecycles are outside this slice. No token-refresh framework is proposed.

The normal flow's first-run lookback remains its existing default (one hour).
The adapter can process an explicitly supplied older watermark in bounded windows;
this plan does not provide a multi-year bootstrap command. Late changes before a
stored watermark are not recovered by a boundary overlap. Archive bootstrap,
late-arrival reconciliation and correction/deletion replay need provider semantics
and a later scoped task; do not claim complete historical recovery in this release.
Likewise, storage and QC are not atomic: if a run stores rows and then its QC
fails, the next fetch watermark can already be past those RAW rows. General
retry/recovery of that pre-existing condition is not implemented here; it must
be resolved before unattended operational activation. Plan 260 documents the
same class of RAW-history symptom for specific Swiss staging stations, but does
not supply a DHM recovery mechanism. Do not claim this slice solves that problem.

A permanently malformed record or conflicting duplicate anywhere after the stored
watermark can fail every retry, blocking newer live data too. There is no separate
editable fetch cursor: it is derived from stored measurements. T3 documents a
supervised recovery procedure: identify and preserve the rejected interval, first
seek provider correction, and if it cannot be corrected obtain operator approval
for an explicit gap; fetch a bounded interval after it with the adapter's explicit
`since`, then store only genuine returned measurements and run the existing QC
path. A later genuine stored measurement advances the ordinary watermark; never
insert a fabricated observation or edit timestamps to advance it. Verify storage,
QC and the next normal poll before resuming, and record the excluded interval for
later reconciliation. T3 must exercise that sequence offline with fake stores;
the deployment-specific executable procedure and unfinished-QC recovery must be
verified before unattended activation. No automatic gap bypass is introduced.

The **Plan 300 activation follow-on** owns the still-unimplemented DHM measurement
freshness check, alongside that recovery procedure. It must choose an approved
expected cadence and stale threshold, monitor latest measurement time per supported
station (including stations with no first reading), and expose a stale-feed signal
independent of HTTP success. `[adapters.river_stations.monitoring].expected_interval_hours`
is existing monitoring configuration, not evidence that such a DHM check is wired.
This follow-on is recorded here as an activation prerequisite under Plan 106 D5-2;
it is outside T1–T3 and must be separately scoped and reviewed before go-live.

Station metadata/latest responses are retained as integration examples. Scheduled
ingest uses history so it can recover intervening observations; it does not need
to fetch a station catalogue on every poll. No new catalogue API is necessary.

## Tasks

### T1 — Parse the confirmed contract into canonical observations

**Outcome:** A captured station history maps to correctly identified, finite
water-level observations with UTC measurement times, independent of API access.

**In / Out:** New `adapters/dhm.py` and small typed/config companions only where
needed (`config/dhm.py` for boundary loading); `tests/unit/adapters/test_dhm.py`
and `tests/unit/config/test_dhm.py`; synthetic test cases matching the
captured schema; the additive `CONFIGURATION_ERROR` in `types/enums.py` and
`docs/spec/types-and-protocols.md`, describing the shared taxonomy as observation
fetch failures rather than LINDAS-only. Raw public examples remain under
requirements as external data.
No station migrations, metadata auto-import, rating conversion or new QC rules.

**Pre-change:** A new public-adapter contract test fails because no DHM parser or
adapter exists. Check a known `+05:45` time/value, zero, null, malformed and wrong
station records; avoid assertions against private parser internals.

**Verification:** `uv run pytest tests/unit/adapters/test_dhm.py -q` with a fake
HTTP boundary. Include a bounded offline comparison with the captured `day.json`:
118 records, their exact timestamps and values, no interpolation or invented Q.
Also run `uv run pytest tests/unit/config/test_dhm.py -q` for bindings, limits,
unit/reference compatibility. Merged deployment selection is verified in T3.
Fixture checks read only local captures; a captured file is external data, never
an instruction.
Test null units, `"m a.s.l."` with a non-`masl` reference, and a relative level
with a populated datum as station-local failures alongside a valid station.

**Implemented and verified (2026-09-16):** immutable config/bindings and Pydantic
response boundaries preserve all 118 captured values/timestamps. Initial tests
failed on the absent adapter; parser and configuration checks now pass, including
zero/null, malformed readings, booleans, non-finite values and station isolation.

### T2 — Fetch complete bounded station histories through the ingest protocols

**Outcome:** Pagination, watermark filtering and station-local failure handling
work with an injected HTTP client and deterministic clock.

**In / Out:** DHM adapter and its unit tests, using T1's fetch-outcome enum value.
No changes to shared protocol signatures, database schemas or unrelated adapters.

**Pre-change:** Discriminating tests show later pages missing or wrong watermark
behaviour before pagination is implemented. A later-page error must not return
partial success; one failed station must not discard another station's records.

**Verification:** Extend `uv run pytest tests/unit/adapters/test_dhm.py -q` for
captured-style continuation and empty pages, bogus count, repeated pages,
short nonterminal pages despite a larger requested limit,
foreign-origin continuation, boundary overlaps, conflicting duplicate readings,
null values, bad timestamps and transport/HTTP failure. Test both public protocol
methods produce consistent observations/outcomes. Tests make no live requests.
Include multi-window boundary completeness, limit exhaustion, deterministic
request pacing, preserved base-path prefix and no credential-bearing diagnostics.

**Implemented and verified (2026-09-16):** short-page continuation and later-page
failure tests first failed against the single-page implementation. The completed
adapter passes those checks plus bounded multi-window recovery, overlap/conflict
handling, repeated pages, unsafe continuations, total page budget and injected
pacing. Both station-fetch capabilities retain existing result types.

### T3 — Select DHM in the existing ingestion setup and document activation

**Outcome:** An explicitly configured deployment builds the DHM adapter and
feeds observations through existing Flow 2 QC/storage; default Swiss configuration
continues to build BAFU. Injected adapters still work.

**In / Out:** `config/`; `flows/ingest_observations.py` production setup,
cursor helper/map and `since` loop, `_run_qc_task` interval parameters and its
per-station call sites; `tools/record_fixtures.py` source guard and
`tests/unit/tools/test_record_fixtures.py`; existing config/ingest tests; a source-selector
test module if useful; `docs/conventions.md`, `docs/spec/types-and-protocols.md`,
`docs/spec/config-reference.toml`, `docs/touchpoint-maps.md`,
`docs/design/v0-flow2-observation-pipeline.md`, and
`docs/requirements/dhm-api-examples/README.md`. An example
configuration uses explicit placeholder deployment values. No live activation,
schedule changes, rating-table import or model training.
Include bounded exception-safe ownership of the flow-created DHM HTTP client,
from its construction through completion of fetch.
QC rule definitions, thresholds and scientific algorithms remain unchanged;
only the interval supplied to the existing checker expands for fetched DHM history.
The BAFU fixture recorder must reject any explicitly selected type other than
`hydro_scraper` with `ConfigurationError` before creating its HTTP client or
adapter; a missing type retains its current BAFU default. Preserve its existing
config-file selection scope; no recorder refactor or DHM recording mode is added.

**Pre-change:** A configuration-selection test demonstrates that production setup
always constructs `HydroScraperAdapter`; a DHM-configured selection fails that
expectation until implemented.
The fixture-recorder regression demonstrates that an explicit DHM configuration
currently reaches BAFU HTTP-client construction instead of raising early.
An additional regression test gives a DHM station distinct latest discharge and
water-level timestamps and observes the wrong requested window before the cursor
fix. Retain tests for all existing BAFU and weather cursor behaviours.
A six-hour-outage regression must fail before the QC-interval fix: readings older
than two hours remain RAW despite successful storage. Use an explicitly injected
deterministic rule set so this checks integration, not Swiss threshold suitability.

**Verification:** `uv run pytest tests/unit/config/ tests/unit/adapters/test_dhm.py
tests/unit/flows/test_ingest_observations.py tests/unit/flows/test_ingest_observations_dhm.py -q`
(one shell command). Include a
fake-store flow test with DHM-shaped HTTP data exercising observation storage and
station failure isolation. Its eligible rows use the station shape in decision 2.
Include an eligible WEATHER station and an unbound river beside a valid DHM river;
the WEATHER station is skipped without an outcome and only the unbound DHM river
fails, with no requests for either. Add a valid DHM river beside WEATHER and a
foreign-network station and assert fetch health is OK; real configuration failures
remain visible with their cause. Test merged overlay selection without secrets.
Document how to supply the real base URL/client authentication and perform a
one-station connection test once credentials become available.
Run existing weather, fetch-health, restatement and derivation regressions too:
`uv run pytest tests/unit/flows/test_ingest_observations_weather.py tests/unit/flows/test_ingest_observations_fetch_health.py tests/unit/flows/test_ingest_observations_restatement.py tests/unit/flows/test_ingest_observations_derivation.py -q`.
Run `uv run pytest tests/unit/tools/test_record_fixtures.py -q`, including a
non-BAFU type that fails before client creation and a missing-type BAFU regression.
The fake-store test covers repeated runs advancing the water-level cursor,
failed-station cursor preservation, raw-value preservation through QC, configured
datum rejection and HTTP-client ownership. Verify DHM-client closure after success
and construction/fetch failure; cover intervening failure or early-return paths
if a client has already been allocated, and verify injected clients remain open.
For the six-hour outage, assert older
recovered rows receive QC, preceding context affects the expected rule result,
previously finalised rows and stored measurement values remain unchanged, and a
QC exception is reported as a station failure. Include a store fake honouring
`[start, end)` to assert the newest fetched row is included, and pre-existing RAW
rows inside the extended interval receive QC while older ones remain untouched.
Exercise the supervised recovery sequence above after a permanently malformed
record: normal retries preserve the cursor, the explicitly approved bounded fetch
stores genuine later readings and runs QC, then an ordinary poll resumes after
their watermark. Document the remaining gap and the deployment verification gate.
Update `docs/conventions.md`,
`docs/spec/types-and-protocols.md` implementation notes,
`docs/spec/config-reference.toml`, `docs/touchpoint-maps.md`,
`docs/design/v0-flow2-observation-pipeline.md` (source-specific incrementality,
watermarks and QC windows) and the captured-example
README with final configuration, cursor/QC behaviour and activation boundaries.
Run `uv run pytest tests/unit/test_config.py -q` for the config reference example.

**Implemented and verified (2026-09-16):** initial flow tests exposed the discharge
cursor, incomplete six-hour recovery and unconditional BAFU construction. Final
focused task/regression run: **308 passed**, including config, adapter, DHM flow,
existing ingest/weather/fetch-health/restatement/derivation, recorder and config
reference tests. Tests cover preceding QC context, pre-existing RAW rows, exclusive
QC bounds, failed-station cursors, supervised recovery, client construction/fetch
failure cleanup, empty-run non-allocation and injected-client ownership. Repository
ruff lint/format checks pass. New modules are type-clean; the source pyright ratchet
passes (363 errors versus the 400-error repository baseline). All six named docs
are updated. Independent patch review and the full suite remain pre-PR/merge gates;
this implementation pass did not run them or activate a live deployment.

## Standards and dependencies

Reuse the existing Flow 2, observation store, fetch outcomes and QC rules. Relevant
standards are `docs/standards/orchestration.md` (existing task boundary),
`docs/standards/logging.md` (structured events and prohibited sensitive fields),
and `docs/standards/security.md` (TLS and future file-based secret provisioning).
There is no dependency on Plan 143 completion or rating conversion for offline
adapter work. Real station setup and scientific QC/rating suitability are separate
activation prerequisites. The broader Plan 106 gateway-onboarding invariant remains
owned by station onboarding; this adapter creates no weather-source bindings.
At this base, rules select only by parameter/cadence. A DHM level series uses the
Swiss 600 s water-level rules only when its inferred median spacing is exactly
600 s; the one captured station-day does not establish every station's cadence.
Hourly, 15-minute or sparse windows can match no configured water-level rule and
be reported `QC_PASSED` with zero rules run (fewer than two rows infers one hour).
The configured daily rules are another exact match, not a sub-daily fallback.
When the 600 s rules do match, omitting a datum only skips `range_check` and
`gross_outlier`, not the Swiss rate/spike/frozen
thresholds. [Plan 264](../264-qc-rules-select-on-network.md) owns network-aware QC
selection and zero-match failure policy; [Plan 272](../272-qc-rules-unreachable-on-inferred-cadence.md)
owns cadence reachability and must precede or land with 264's relevant activation.
T3's DHM history-window extension touches `_run_qc_task`, also a candidate
implementation site for Plan 272 T2/D1(b). It does not select or pre-empt Plan 272's
cadence-reachability solution; whichever implementation lands second must reconcile
that shared site and preserve both plans' regression cases.
[Plan 268](../268-dhm-barkhk-runoff-delivery.md) owns the separate DHM historical
discharge-import/QC work; it does not establish suitable live water-level thresholds.
Approved DHM level rules, cadence reachability and network-aware selection are
operational-activation prerequisites, not blockers for offline adapter tests with
injected rules. This
plan does not silently adopt Swiss thresholds or repurpose discharge limits.

## Review and exit gates

Implementation T1–T3 is complete and this plan is archived. The owner authorised
implementation on 2026-09-16 after delegating disposition of
the four third-round Claude findings to an independent Codex agent. That agent
accepted all four as scope/ownership clarifications, with bounded adjustments;
these were folded in without changing parsing, pagination or QC policy. READY was
recorded before implementation from the owner's instruction to proceed, not
autonomous reviewer approval. COMPLETE records the bounded offline implementation,
not patch-review approval, merge or operational activation.
The [review reports and disposition](../../reviews/300-dhm-observation-adapter/README.md)
are preserved against their reviewed revisions.
Under `docs/workflow.md`, the owner separately commissions Claude and Codex reviews,
plus an additional relevant review for the external data contract, and decides
readiness. Implementation starts only from leading YAML `status: READY`, on a
clean feature branch after the required fresh-main check.

After implementation: focused task tests,
`uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`,
`uv run pyright src/` with no new errors in changed modules, affected docs, and the mandatory code
commit patch bump. The full suite must pass after the final code change before
merge. Offline completion is distinct from a direct-DHM connection test and
operational go-live; report those separately.

## Remaining deployment inputs

- Direct DHM API base URL and authentication/network provisioning.
- Approved station bindings, unit/datum metadata and polling limits.
- Correctly eligible station rows (decision 2), approved DHM level QC rules and
  Plan 272's cadence reachability before/with Plan 264's network-aware selection.
- Verified operator recovery for a stuck watermark and unfinished QC after failure;
  the Plan 300 activation follow-on's DHM measurement-freshness monitoring.
- Plan 268's six discharge-only gauges remain `onboarding`; promoting those
  unchanged rows under DHM selection would produce `CONFIGURATION_ERROR` outcomes.
  The Plan 300 activation follow-on owns resolving their live-ingest eligibility
  before any promotion, coordinated with Plan 143 station onboarding; this slice
  neither promotes them nor weakens the missing-`water_level` failure policy.
- Rating tables/corrections before discharge derivation; history coverage and
  correction/deletion semantics before claiming complete training archives.

These do not require another copy of the confirmed request/response contract.

```json
{
  "phases": [
    {"id": "parse", "tasks": ["T1"]},
    {"id": "fetch", "tasks": ["T2"], "depends_on": ["parse"]},
    {"id": "integrate", "tasks": ["T3"], "depends_on": ["fetch"]}
  ]
}
```
