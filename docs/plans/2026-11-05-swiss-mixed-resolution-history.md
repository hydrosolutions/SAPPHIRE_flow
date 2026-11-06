---
status: READY
owner: Swiss observation-history implementation owner
base: 8857c8294772843a9a8eff5a1794071443291e7e
---
# Swiss mixed-resolution observation history

## Authority and scope

Implement the tracked [confirmed vision](../../planning/visions/2026-11-05-swiss-danger-levels-observation-history.md), not a new vision. This draft settles the engineering contract for review; it grants no merge, live-read, admission or deployment authority. Follow current `AGENTS.md` and the global implementation workflow. One named owner owns each implementation pass. Root commissions independent Claude and Codex reviews and the owner-commissioned scientific safety review before runtime implementation; repeat independent patch reviews before delivery. Full reports stay local.

Owner-confirmed scientific choices relayed on 2026-11-05: arithmetic sample mean of finite eligible unique readings; min/max from that cohort; count with unknown coverage without authoritative cadence; native timestamp markers without assumed continuity; exact window and clipped first UTC block; latest remains independent of history. No cadence metadata or ingestion expansion is authorized. Owner confirmation also approves retryable-failure retention until exactly twenty minutes from the original as-of, visibly stale on failure, numeric purge at expiry and immediate purge on access denial. Current overview and forecasts remain independent.

Only Swiss Danger levels history changes. Current and Forecast subviews share the same history state while `viewMode === "levels"`; their existing row rosters and forecast eligibility stay unchanged. Return every authorized manifest station (reference 134); absent coordinates do not remove server entries or create new map/list rows. Preserve other tabs, Nepal, detail/native charts, classifications, standing categories, counts, colours, thresholds, latest/trends, forecast identity/horizon/earliest peak and p25–p75. No models, ingestion, QC policy, publication policy, grants, materialization or scheduling changes.

The separate `2026-11-04-shared-dashboard-overview-delivery.md` remains a draft for later generic migration. Its generic route and 72-hour ceiling do not govern this separate 72/168-hour Swiss product. Do not execute or supersede that whole draft. Do not implement the old embedded uniform-hourly proposal.

## Evidence and implementation boundaries

Root validated the vision at `3cea18b11bab95f55652fc7e227ee744c2f89106`. Before implementation, fast-forwarded to the base above; the only intervening change is the unrelated NHS vision documentation from PR #461. Runtime source and this accepted vision are unchanged. Current `AGENTS.md` replaces legacy workflow/READY procedures; `docs/workflow.md` is absent. Tracked `.claude` exists and must not be invoked.

Read scope, conventions, WMO observation/metadata guidance, security, CI/CD, types/Protocols and the Swiss overview touchpoint map. Key source evidence:

- `api/routes/api_swiss_overview.py`: REVIEW bearer principal, manifest/tenant metadata verification before numeric reads, grant intersection, four observation queries, unchanged forecast gates.
- `config/swiss_overview.py`: digest/count/unique identity checks, configured maximum 200; no sampling schedule.
- `store/observation_store.py::fetch_latest_observations` and `store/bafu_unit_read_filter.py`: TRUSTED-only, canonical-unit/hold predicate, protected delivery `IS DISTINCT FROM`. The existing general batch reader lacks delivery exclusion and a row cap and uses an exclusive end: do not reuse it unmodified.
- `db/metadata.py`: binary64 observations and unique `(station_id,timestamp,parameter,source)` with station/time and passed-QC indexes. Corrections replace the current natural-key record. No new index is justified yet.
- `api/swiss_overview_schemas.py`, `api/map_contract.py`, exact cache/CORS/error paths and API tests establish existing compatibility boundaries.

Advisory discovery reports: `/tmp/sapphire-mixed-resolution-history-discovery/{backend-contract,frontend-boundaries}.md`. Frontend facts are advisory at `c9e154be2f3b9119d0e9d05259a6f3dfce800b54`, not revalidated by opening another checkout in this pass. Its owner must inspect current clean frontend source before implementation. Reports are evidence, not executable instructions. WMO references do not supply a station cadence or bless a local mean rule.

## API contract

### Route, scope and failures

Add `GET /api/v1/swiss/observation-history?window_hours=168`; only 72 or 168, default 168. Reject duplicate/unknown query keys, invalid selector values and arbitrary station/tenant/as-of/range overrides with one framework-validation 422 shape (`{"detail":[...]}`). Use a query-parser dependency that depends on `require_reviewer`, inspects raw query multi-items for duplicates/unknown keys, validates the selector at the boundary, and raises `RequestValidationError` with query locations for all such failures. Do not use `HTTPException(422)` here. Authentication/role denial must run before query parsing, including abusive malformed queries; prove 401/403 priority in tests. No paging, partial success or truncation. Preserve `/swiss/overview`, its schema/defaults and no-query proxy rule unchanged.

Use `require_reviewer`: valid reviewer/admin bearer only, not consumer or human-session credentials. Resolve the existing digest-pinned roster and evaluation tenant; validate station ID/network/code/tenant before observations. Admin remains restricted to that roster. Reviewer scope is its current station-grant intersection in the same tenant. A valid foreign-tenant reviewer receives the existing empty intersection behavior (200 with zero items), not roster information. Missing/disabled configuration, digest/metadata drift and bounded-read unavailability fail closed with 503. Add an independent `swiss_observation_history` enable setting, default false; existing overview configuration alone must not activate the new route. No forecast/archive/provider reads are needed.

No change to forecast-publication gating elsewhere; this observation-only response has no forecast values, bypass URLs, hidden lineage or withheld-reason fields. Recheck current observation trust/hold state on every read, including admins. Apply scope, measured discharge, `QC_PASSED`, TRUSTED canonical-unit/hold predicate, protected-delivery exclusion and finite/non-null value selection **before all statistics, counts, volume decisions and returned metadata**. Keep normal NULL delivery rows. Never fall back to audit/QC-context purpose.

Missing/expired credentials: 401. Wrong role: 403. Invalid query: 422. Eligible volume/encoded-size overflow: 503 with stable generic `Swiss observation history is unavailable` error, no counts, station identities or exclusion reasons. Unexpected faults remain sanitized 500, not disguised as no data. No-body partial response. Follow existing framework validation error shape and existing `error`/`detail` envelope for HTTP/unhandled errors; document both accurately. Every response, including 401/403/405/422/500/503 and OPTIONS preflights, is no-store. Rate-limit semantics remain existing infrastructure, not a new per-row limiter. Caddy supplies 429, not a nonexistent FastAPI limiter; the Worker forces `private, no-store` on proxied 429 responses. Qualify this through the actual Worker/Caddy path as well as proxy tests.

### Compact response (schema_version 1)

Use shared UTC timestamps encoded as RFC3339 with `Z`, preserving microseconds; ordinary JSON numbers retain binary64 round-trip precision. No decimal quantization or transport rounding. Proposed concrete field layout:

```text
{
  schema_version: 1,
  generation_id: UUID,
  generated_at: UTC timestamp,
  as_of: UTC timestamp,
  expires_at: UTC timestamp,
  window_hours: 72 | 168,
  window_start: UTC timestamp,
  native_start: UTC timestamp,
  parameter: "discharge", observation_source: "measured",
  observation_display_unit: "m3/s",
  coarse: {
    grid_start: UTC timestamp, step_seconds: 10800, block_count: integer,
    first_start: UTC timestamp, first_block_clipped: boolean
  },
  station_count: integer,
  items: [{
    station: {id: UUID string, network: string, code: string},
    coverage: {state: "unknown"},
    coarse: [[mean|null, min|null, max|null, eligible_count], ...],
    native: [[observed_at, value], ...]
  }]
}
```

Each station has exactly `block_count` coarse tuples, oldest first, including empty blocks `[null,null,null,0]`. Nonempty tuple: finite numbers, count positive, `min <= mean <= max`. Singletons have three equal numbers, never a completeness claim. Station coverage is inherited by every block/native support; no expected-count field or inferred denominator is sent. Derive per-block presentation state as `unknown` for positive count and `empty` for zero count. Every nonempty glyph visibly marks unknown support; empty blocks are gaps. No block is classified complete or incomplete in this release. Tooltips show that block's count and “Coverage unknown”; no percentage. Do not add dormant cadence tables or fictitious complete/incomplete values. A future authoritative cadence implementation needs its own reviewed contract. Clipped is a separate temporal property, not proof of cadence incompleteness. Known missing coverage cannot be certified in this release; unknown is never rendered as complete.

Native arrays contain only finite eligible unique timestamp/value pairs in strictly increasing timestamp order. Empty is `[]`, not zero or synthetic timestamp slots. Null coarse statistics describe no eligible readings, not hidden-row reasons. Station identity/order follows the validated roster intersection; station_count equals items length, including empty stations. Reject duplicate/out-of-scope identities and malformed arrays in both server invariants and frontend parser. A valid empty authorized roster is distinct from malformed input.

Keep schema semantic bounds explicit in OpenAPI and frontend parser (station <=200; block <=56 for 168h, <=24 for 72h). Row/native/byte caps below are proposals until qualification, not implicit array truncation. A wire boolean for clipping is boundary representation; internal domain state uses an enum. Frozen dataclasses with typed UTC time, station IDs, window selector and bounds encode internal invariants. Pydantic stays at HTTP/config boundaries. Inject clock and generation-ID source for deterministic tests. Cross-repository wire qualification uses a route-generated response with microsecond timestamps, signed zero, `2.0`, and nontrivial binary64 values (`0.12345678901234568` and `1.0000000000000002`); verify the exact artifact through the frontend parser. Golden JSON files add one terminal LF for repository hygiene; tests otherwise compare exact route bytes. Server invariants and frontend parsing must check exact derived equalities: `S=A-W`, `J=floor_to_3h_UTC(A-1h)`, `window_start=first_start=S`, `native_start=J`, `grid_start=floor_to_3h_UTC(S)`, `step_seconds=10800`, `block_count=(J-grid_start)/3h`, and `first_block_clipped=(S!=grid_start)`. Require exact tuple/array lengths, integer nonnegative counts, null/count consistency, finite statistics and `min<=mean<=max`; validate limits, station count, unique matching station identities and no mixed parameter/source/unit identity. Native timestamps must be strictly increasing and lie in `[J,A]`. Parse UTC timestamps to an exact microsecond representation (for example epoch microseconds as BigInt); JavaScript Date milliseconds alone cannot validate microsecond boundaries. Convert only for rendering after exact checks; reject precision loss rather than silently moving a point across J or A.

### Time ownership and generation

Capture one injected request cutoff `A = as_of`; `generated_at = A` denotes request-generation start, not source freshness or database commit time. Each successful request has a fresh opaque `generation_id`, even if values are unchanged; do not hash protected inputs. All observation values and eligibility predicates come from one SQL statement snapshot at read time, clipped to measurement timestamps <= A. This is not time-travel reconstruction of what was ingested at A. Full-window replacement on every successful refresh reflects later arrivals, replacements and removals.

Let `W` be 72/168 hours, `S = A-W`, and `J = floor_to_3h_UTC(A-1h)`. Coarse owns `[S,J)`; native owns `[J,A]`. Thus native spans at least one and less than four hours. The exact J reading occurs only in native; exact A is included; exact S is included. Compute UTC calendar boundaries without local-DST arithmetic.

`grid_start = floor_to_3h_UTC(S)`, `first_start = S`, `block_count = (J-grid_start)/3h`. For block i use `[max(S,grid_start+i*3h),grid_start+(i+1)*3h)`. Clip only the oldest block; no reads before S. `first_block_clipped = (S != grid_start)`.

Examples: A=2026-11-05T10:20Z, W=72: S=Nov 2 10:20Z, first interval [10:20,12:00), J=Nov 5 09:00Z. A=09:20Z: S=Nov 2 09:20Z, first [09:20,12:00), J=06:00Z. A=10:00Z gives J=09:00Z; at 09:00Z, J=06:00Z. Include microsecond boundary tests.

**Owner-approved retention:** `generated_at=as_of=A`, `expires_at=A+20 minutes` exactly. This is a maximum retention bound, not scientific freshness. Each uncached fresh API request binds injected A at backend request start. After the shared budget/cooldown wait, capture `request_start_monotonic` immediately **before the real fetch**, not at queue entry or response receipt. For that response set `deadline = request_start_monotonic + (expires_at-as_of)` in consistent units. All round-trip time counts against retention; discard numeric content on arrival when monotonic now >= deadline. Never compare server timestamps against browser wall time. Capture local wall time with the same pre-fetch monotonic start. Run the clock check on arrival, every fetch outcome (including failure, 429 and abort), lifecycle/visibility events, and a one-second local timer whenever numeric history is displayed; also check before retaining/rendering a generation. This timer makes no network request. After visible-tab system suspension with no arrival and a failed or absent refresh, purge at the first timer callback, within one second of event-loop resumption. Purge if wall elapsed reaches the twenty-minute duration or differs from monotonic elapsed by more than five seconds in either direction; also purge on a non-finite or backward clock reading. This local-to-local comparison can only shorten retention, never accept, extend or restart it. The five-second divergence tolerance is an engineering clock-consistency check, not extra grace. Detect system sleep even without visibilitychange via this check; purge on supported freeze/resume lifecycle events as well. Verify exact timestamp equality/duration, valid finite clock readings and nondecreasing monotonic time; inconsistent body age metadata or a backward clock fails closed and purges. No retry, local-state read, window/visibility change or timer restart can extend a generation's stored deadline.

This conservative mapping assumes a fresh no-store request through Worker to backend, no replay/cache/service-worker substitution, and monotonic elapsed time advancing across the relevant lifecycle. Backend A is bound after browser fetch starts, so starting the twenty-minute interval earlier cannot extend backend retention. It needs no Date/header epoch conversion. Verify the fresh-route and clock assumptions during qualification; if they cannot hold (including a platform clock that loses suspended elapsed time), clear state and fetch anew, never invent a wall-clock grace extension. Hidden/inactive transitions already purge state. This is not a global wall-clock revocation push guarantee.

Healthy polling replaces at ten minutes; any retryable failure immediately shows failure/stale status. Retain only the same authorized generation until its deadline, then remove numeric history even during a 429 pause or pending fetch. Without a last-good generation show unavailable. Denial, malformed response/scope/identity, explicit auth-context change, window change or newly observed scope change purges immediately and invalidates old completions. Silent server revocation is discovered on the next read. A missed ten-minute refresh can cause a brief blank at twenty minutes before the next successful arrival; document and test this, with no extra margin.

### Scientific aggregation and geometry

Mean is the equal-weight arithmetic **sample mean**, not a time average or volume. Dense sampling has greater weight; disclose this limitation. Compute min/max/count over exactly the same finite eligible distinct cohort. Return the correctly rounded binary64 value of the exact arithmetic mean of those binary64 inputs (round-to-nearest, ties-to-even). Use stdlib `statistics.mean` for finite float cohorts: its exact-ratio accumulation/division and final float conversion avoid intermediate sum rounding/overflow; do not substitute `fmean` or `fsum/n`. Verify the supported project Python implementation against an independent exact-rational reference. This preserves `min <= mean <= max` without tolerance, clamping or decimal quantization. Require exact red cases `[0.1]*3`, `[12.3]*18`, cancellation, extreme finite inputs and subnormals, plus representative aggregation-cost qualification. Preserve stored extrema/native numbers bit-for-bit through JSON round trip. Exclude non-finite values at the store predicate and defensively in pure aggregation before counts. Do not average archived versions. Identical duplicate natural keys in fake/input collections count once; conflicting duplicate current values fail deterministically, rather than picking order-dependent winners. PostgreSQL uniqueness is the normal authority.

No interpolation, carry-forward, invented native grid or observed-gap threshold. Without authoritative cadence, native is actual-timestamp markers only, never connected. Coarse means/envelopes are discrete block-summary glyphs: draw a capped vertical observed min–max range with a separate short mean tick, centred in the effective block interval, not at an asserted observation/peak time. Keep a visible horizontal separation between adjacent mean ticks even when means match; no connecting risers, lines or filled polygons between blocks or into native. Do not stretch a singleton or sparse glyph into a continuous-support band. This retains the visible observed min–max envelope intention without implying measured support across the interval. Label the marks as block sample summaries. Every nonempty glyph has a visible unknown-support mark explained by a legend, plus keyboard/touch tooltip; empty intervals remain gaps. Test singleton readings at both block edges, sparse irregular blocks, adjacent nonempty blocks, empty and native-only histories in geometry tests and normal-login browser screenshots. Do not imply measured extrema bound the unobserved portions of a block. The join is a shared time boundary, not an artificial blank or an extra overlapping summary. Do not connect the last coarse mean to the first native point.

Use a shared x-axis from exact S through the existing forecast horizon (or A if no forecast), even with leading/trailing empty history. Mark A as history cutoff, not the last observation timestamp. Time after A is unfetched history, not a measured-data gap. Keep current overview number/age and trends refreshing independently every minute; never append latest to this snapshot or fabricate a connecting marker. Display history as-of/refresh status. Forecast median and p25–p75 retain their own source and existing semantics; historical band is explicitly **Observed min–max**. Local labels use Swiss CET/CEST, UTC determines aggregation. Tooltips give effective block bounds, mean/min/max/count and unknown coverage, not an invented peak time.

## Bounded read and performance qualification

Add a narrow `fetch_observation_history` store capability rather than modifying general batch defaults. Typed bounded request includes station IDs, inclusive start/as_of, parameter/source/QC and required protected-delivery exclusion; require TRUSTED purpose. Implementation selects only needed columns with existing indexes, orders by station/time, uses one bounded SQL statement with all eligibility predicates inside the bounded selection. Fetch at most fleet cap+1 eligible rows to detect overflow; discard the entire result on overflow. This sentinel is a failure check, never a returned truncated success. Check per-station bound from that eligible result. Do not count excluded rows to choose an error. Avoid per-station SQL, separate pre-count races, unbounded `.all()`, or changing general observation readers. Scope checks precede this read; protected-state predicate joins must not duplicate rows. One station's overflow fails the whole batch, as required by no partial success/truncation; it does not immediately erase an authorized last-good client generation. A 503 shows failure/stale status and retains that generation only until its original deadline, or unavailable when none exists.

Set `SET LOCAL statement_timeout` in the existing request transaction for the bounded read. Map database query cancellation SQLSTATE `57014` to generic 503; other database faults remain sanitized 500. Roll back the failed transaction, including auth `last_used_at` updates; prove settings restore on transaction end/connection reuse and no global timeout changes. The SET is an additional SQL statement, not an additional observation query, and counts toward the proposed total query budget.

Follow `docs/standards/logging.md`: emit bounded structured `observation_history.read_failed` events with request context and a closed cause (`row_cap`, `byte_cap`, `timeout`, `config_drift`, `disabled`, or `database_fault`). Use WARNING for bounded unavailability and ERROR for config drift/unexpected faults. Whitelist diagnostic fields: applicable bound, capped eligible-row count or encoded bytes, duration, and at most one validated eligible in-scope station ID for a station overflow. Never log protected/excluded counts, observation values, raw SQL/errors, credentials, raw payloads or unvalidated configuration identities. No diagnostic counts/identities enter public error bodies; timeout/config failures need not have any counts. Do not run extra reads just for diagnostics.

Keep the response bounded before sending: encode once, verify raw UTF-8 length, then return only a complete compliant body. SQL cap bounds materialized eligible rows, not all physical database scan work: inspect plans/rows/buffers and statement duration to establish actual cost. Existing indexes first; if dense or excluded-tail scans are unacceptable, stop and review a targeted alternative. Stored summaries/indexes/materialization require evidence and a revised review, not automatic scope growth.

**Owner demo decision (2026-11-06):** several-second initial history loads are acceptable for the demo dashboard; further speed optimization is deferred. The proposed three-second response target is therefore not a blocker for delivering the disabled demo implementation. This is not approval of a measured p95, concurrent-load capacity, canonical-data performance, deployment or activation. Keep the five-second SQL timeout, eligibility/precision rules, whole-response failure, payload/row safeguards and existing overview performance gate unchanged. Seven auth-inclusive SQL statements are observed; the original six-statement proposal must not be pursued by removing authorization or timeout checks. No experimental query shape or JIT setting was adopted. Remaining load/reliability limits must be disclosed in the PR and reviewed before activation.

**All new numbers in this table are explicit PROPOSALS, not approved budgets.** Finalize them with representative measurements and independent review; owner accepts operational load before admission. Do not treat existing sparse fixtures or frontend 1 MiB safety checks as acceptance.

| Proposed bound | Candidate | Rationale / final gate |
|---|---:|---|
| Configured station scope | 200 (existing roster cap) | Reference 134 plus 200 boundary tests, never enumerate beyond manifest |
| Eligible rows per station/request | 2500 | Seven days at 5 min inclusive is 2017; headroom, not a sampling policy |
| Eligible fleet rows/request | 500000 | 200 ×2500; dense 5 min at 200 is 403400; benchmark memory/cost |
| Native points per station | Same 2500 eligible-row cap | Do not assume cadence to reject valid bursts; dense typical <4h is <=48 at 5 min; measure irregular worst allowed too |
| Raw response bytes | 2097152 | Candidate separate-route ceiling; dense full-precision tuple+timestamp proof required, plus over-limit error tests |
| SQL statements/request | <=6 total, exactly one observation statement for nonempty scope | Include auth/scope/metadata and SET LOCAL overhead; measure actual wiring |
| End-to-end p95 | <3.0 s | Candidate cold/warm representative target, measured with concurrent admitted readers |
| Backend read timeout | 5 s | Candidate request-local bounded DB read; no global timeout change |
| Browser request timeout | 20 s | Match existing page read discipline; not an API SLA |

A bursty but otherwise eligible dataset that exceeds caps fails explicitly; no decimation or selective drop. Size and row bounds are independent; the admitted sparse/dense fleet must fit both. Final cost specification must publish selected constants, rationale, query plans, memory/CPU/serialization cost and failure behavior. If 168h/full precision/native detail cannot meet acceptable cost, escalate the material tradeoff to the owner; do not shorten the window or loosen precision silently.

Benchmark both windows, 134 reference and configured 200 cap, dense 5/10/15-minute data, mixed/irregular/sparse/singleton/empty data, maximum native duration just below four hours, and earliest clipped block. Use non-round binary64 values, not small integers; realistic history/table cardinality and trusted-unit evidence. Include heavy excluded/held/nonfinite tails, near-cap/cap+1 and response-size overflow; excluded changes must not alter returned values, counts or eligibility-limit error decisions. Corrections must remove previous extrema on refresh. Measure actual PostgreSQL route calls, not just serializer or Python service timing: cold/warm p50/p95, query count, raw/gzip bytes (gzip diagnostic only), SQL rows/buffers, process memory, encode/parse/browser cost. Qualify both steady ten-minute reads and the worst permitted new-history load: three admitted visible pages, each repeatedly starting history at the ten-second minimum gap (six starts per rolling minute under a half-open boundary convention), with concurrent minute overview and other budgeted reads. Exercise re-entry, window changes, manual retries and visibility transitions, not just steady polling; shared 30/min remains enforced. Report the actual qualification dataset's maximum eligible rows per station and fleet against selected caps, without public/protected counts. No live-host work without separate permission.

Run old overview performance gate separately in its existing fresh process and sustained heavy process: **raw <=393216 bytes, p95 <1.25 s, queries <=12** remain enforced. New history budget does not replace or relax them. Preserve exactly-once CI shard partition coverage. Local disposable PostgreSQL and fixture/reference inputs first; deployed normal-login qualification is a later owner-authorized gate, not this pass.

## Frontend reader and lifecycle

One Swiss page-level `useSwissObservationHistory` reader, separate from overview state. Enabled only with a default-off frontend build-time flag using the existing Vite environment/build-flag mechanism (name and wiring confirmed against current frontend source), Swiss deployment, active `levels` view and visible document. Reader/proxy/schema land disabled first; changing the build-time flag requires a frontend rebuild/redeploy, not a runtime auth-policy change. Both Current/Forecast use it; no row/detail readers, `watchRequested` latch, panelCache, background prefetch or hidden duplicate requests. Full selected window request on entry; 600000 ms cadence from request start while active/visible, no overlap and no catch-up burst. A ten-minute boundary during a slow read schedules the next permitted read, not a parallel one. Entry/re-entry/window/visibility/manual requests all obey a ten-second minimum between **every actual history fetch start**, including automatic starts and aborted starts, as well as the shared request budget. Keep this last-start clock across hook teardown/re-entry within the page. Initial entry is immediate only when the gap permits. Coalesce competing intents into one pending latest eligible request; no overlap or catch-up queue. Visibility/inactive exit aborts queued and in-flight work and invalidates completions; clear history on leaving the view to prevent resurrection. Visible return requests anew. Window changes clear old chart immediately; no mixing windows.

Use singleton `budgetedFetcher`/page budget (30 starts per rolling minute), existing 429 page-wide pause and abortable queue. Recheck active view, generation, access epoch and cooldown after queue wait and before accept. Enforce the history ten-second start gap again after queue wait immediately before dispatch; never reserve an old start time at enqueue. Manual retries obey the same rule; no automatic immediate retry loop. Overview keeps its 60000 ms timer and existing gates. Response acceptance requires matching request epoch/window, valid identities, current access context and unexpired server metadata. Known scope changes from overview also invalidate history; do not infer authorization from map assets. A successful narrower history replaces, never merges with old items. Failure state distinguishes access denial/malformed response (purge), retryable unavailable (explicit status and bounded stale display), and expired numeric removal. Expiry runs independently of request cooldown/429 timers. No persistence in browser storage/Worker caches. A dedicated history response-size reader limit must match the eventual approved backend raw-byte ceiling before activation; do not reuse or enlarge the overview reader cap. The 2 MiB candidate is not approved until measured and reviewed.

Add an optional Swiss history-render input at `WaterLevelsView` instead of repurposing observed extrema as forecast quartiles or globally changing `WatchSeries`. Update Swiss hydrograph-column availability so singleton/empty explicit history can show its state even when old `observed.length > 1` is false. Dedicated `ObservationHistoryChart` geometry may be reusable; default other-tab/component path remains identical. Keep Swiss real/stub exports through `#swiss-overview` (or equivalently isolated alias) so Nepal bundles contain no new Swiss route reads.

## Exact touchpoints and affected docs

Backend paths below are relative to `src/sapphire_flow/` unless noted:

- New `types/observation_history.py`, `services/observation_history.py`, `api/swiss_observation_history_schemas.py`, `api/routes/api_swiss_observation_history.py`; register in `api/__init__.py` alongside the existing REVIEW-gated router composition. Stable responsibility names, no ticket/phase prefixes or vague Manager/Runner/Handler. No generic multi-tenant framework.
- `protocols/stores.py`, `store/observation_store.py`, `tests/fakes/fake_stores.py`, fake parity tests and `docs/spec/types-and-protocols.md` for the narrow bounded read. Keep old signatures/defaults unchanged. Reuse existing unit predicate rather than duplicate its policy.
- `config/deployment.py` for default-disabled activation and existing manifest config/tenant authority; config docs/tests. Extract shared manifest scope logic only if needed to avoid divergence; prove overview equivalence, no broad refactor.
- Exact new path in `api/cors.py` no-store preflight wrapping, `api/publication_cache.py`, `api/errors.py` unexpected-error path, and `api/map_contract.py` path allowlist/auth descriptions/errors/schema publishing. Preserve current overview consumer-origin GET-only CORS policy; do not accidentally grant the broader review POST/credential policy. Test allowed and disallowed origins/methods. Backend bearer and Worker browser-session responsibilities remain distinct.
- `tests/unit/api/test_security.py` auth matrix, `test_map_contract.py`, route/schema tests and new PostgreSQL boundary/performance tests. `docs/spec/api-v1-map.openapi.json`, `docs/spec/api-v1-review.md`, `docs/touchpoint-maps.md`, `docs/standards/security.md`, affected configuration/access docs. Additive map-contract minor version 2.7 from verified current 2.6 (recheck next available version before implementation); preserve overview payload schema_version 1.
- Frontend (separate repository, clean source verification required): `app/components/FlowMap.tsx`, `WaterLevelsView.tsx`, Swiss reader/parser/view seam, new isolated chart/hook, `vite.config.ts` real/stub exports; preserve shared `WatchSparkline` defaults and `riverWatch.ts` forecast types.
- Frontend `worker/apiProxy.ts`: exact `/api/sapphire/v1/swiss/observation-history` to upstream route allowlist, GET only, only single 72/168 selector, explicit `NEVER_CACHED`; no wildcard Swiss routes. `worker/index.ts` normal session guard remains before proxy; server-owned bearer/Access token, no browser credential forwarding, no redirects. Unauthorized fetch remains bare 401, never login HTML parsed as history. Responses `private, no-store`; upstream freshness and cache bypass. Add proxy/auth/path confusion and query smuggling tests.
- Mirror published OpenAPI byte-identically in frontend, update its access/contract docs and contract version assertions. Update CI shard/doc touchpoints if adding performance jobs; no deployment topology change by default.

## Phases, dependency graph and red tests

Each phase has one named implementing owner; a handoff is explicit. Independent reviewers are not co-implementers. No runtime implementation until the contract reviews are complete and material findings resolved. Cost qualification remains a prerequisite to activation, not a reason to claim these proposals approved.

```json
{"phases":[
  {"id":"contract-review","depends_on":[]},
  {"id":"backend","depends_on":["contract-review"]},
  {"id":"frontend-disabled","depends_on":["backend"]},
  {"id":"qualification","depends_on":["backend","frontend-disabled"]},
  {"id":"owner-rollout","depends_on":["qualification"]}
]}
```

1. **Contract review:** independent Claude/Codex and targeted scientific review; record verdicts locally, reconcile comments in this complete draft. Scientific focus: equal sample weighting, unknown support, block geometry, clipped edges/join and post-as-of distinction. Review cost proposals and the exact expiry algorithm/geometry; twenty-minute retention is now owner-approved, not a new cadence policy. No reviewers launched by this planning pass.
2. **Backend pass / PR:** add failing pure aggregation and route/store contract tests first, then minimal bounded read/domain/route/schema/docs. Demonstrate RED on missing route, excluded influence, join duplication and non-finite/overflow cases before GREEN. Preserve existing overview tests. Disposable PostgreSQL proves actual unique-key correction, current holds, protected NULL-delivery semantics, scopes and query count. Publish additive contract and preliminary measured cost evidence with feature default disabled; full tests and independent patch reviews. Owner alone approves/merges.
3. **Frontend reader-first pass / PR:** after contract stabilized, add RED parser/geometry/fake-clock/lifecycle/proxy/budget/isolation tests, then implement disabled reader/presentation. Land compatible reader/proxy/schema before any feature activation; both old backend (route absent) and unavailable new backend have explicit safe unavailable behavior. Backend PR may land first, still disabled; reader-first means consumer compatibility precedes enabling delivery. Revalidate frontend main/ownership, no dirty checkout edits. Full suite/builds and independent patch reviews before owner merge.
4. **Qualification:** run the full dense/sparse matrix, finalize reviewed row/byte/time/query limits and regression gates, browser normal-login tests and screenshots (light/dark, narrow rows, keyboard/touch tooltips, failure/expiry). Exact current deployed response qualification requires separately authorized staging route exposure and limited access; no general feature enablement yet. Record concrete contract versions, raw/gzip/latency/query receipts, access cohort and no-store evidence. If staging/read authority is absent, hold, do not substitute fixture sparsity. Any code change restarts relevant review and final full-suite gate.
5. **Owner rollout:** separate owner admission of the new fleet read under current three-reviewer/one-visible-tab exception, approved backend/frontend releases, authorized deployment and explicit feature enablement only after real qualification. Use current release receipt/rollback procedures, no ad-hoc image or secrets handling. Backend rollback disables the route and returns generic 503: clients show failure and may retain last-good history only to the original twenty-minute deadline, not immediate numeric removal. For immediate removal from newly served clients, disable the frontend build flag and redeploy; already-open old builds still follow their bounded grace unless reloaded. No push/global revocation claim. Retain all old overview/forecast functionality. No schema migration to reverse. Owner approves/merges every PR; no agent merges or treats this draft as deploy approval.

Required red/regression cases across phases:

- Correctly rounded exact mean (including `[0.1]*3`, `[12.3]*18`, subnormals), full-precision min/max/count, overflow/cancellation, empty/singleton/irregular/mixed cadence, duplicate identical/conflicting keys; all excluded sources/QC/held/noncanonical/protected/nonfinite rows inert before counts and cap decisions. No unknown denominator fabricated from observed gaps, polling or model time_step.
- Exact S/J/A and microseconds, 72/168 defaults and validation, clipped oldest block, UTC midnight/year/leap/DST boundaries; null remains gap, native marker-only and no join overlap. Corrected current key replaces extrema, late rows revise old blocks, QC/hold changes can empty them.
- Actual PostgreSQL authorization/scope/metadata failure before numeric reads; admin roster restriction, consumer/human/anonymous denial, foreign tenant empty intersection, access changes, hold changes, no hidden metadata leakage. CORS/error no-store through preflight and unexpected 500; Worker/Caddy 429 qualification. Auth-first malformed/duplicate/unknown query tests use one framework 422 shape. Verify SQLSTATE 57014 -> 503 versus other DB faults -> sanitized 500, rollback of auth last_used_at, local timeout restoration, total query count including SET, and bounded eligible-only failure logs.
- Client races: old generation completion after abort/exit/window/scope change; hide/show purge; queue delay before fetch clock capture; slow arrival; browser wall-clock jumps and visible-tab system suspension can only shorten retention, never extend it; test simultaneous and diverging clock advances, clock offsets, the five-second divergence boundary, and visible-tab suspension with no arrival and a failed or absent refresh: the local one-second timer must purge after event-loop resumption; invalid twenty-minute body duration/generated_at; backward monotonic clock; retry/expiry during 429; response expired on arrival; scope shrink; denial/malformed purge; no stale resurrection. Screenshot the possible twenty-minute blank before replacement. Prove ten-second gap across every start source and latest-intent coalescing, exact ten-minute polling plus unaffected one-minute overview; 30/min adversarial combined page starts.
- Parser rejection of every derived time equality, microsecond interval/order violation, tuple/count/finite/identity mismatch; singletons at both block edges, sparse/empty/native-only glyphs, unknown-support marks and unconnected means/native points in browser acceptance. All authorized 134 entries with existing mapped-row roster unchanged; Current/Forecast shared state but unchanged forecast eligibility. Chart labels, as-of axis, no missing-data shading after A, observed versus forecast bands, native timestamp markers and empty-history state.
- Existing `test_swiss_overview`, boundaries, unit readers, upsert/dedup, map/security/fake parity and production/sustained benchmark suites; frontend overview/read/view/forecast/guards/inputs/isolation, reported-level/threshold/trend, reference-live, API proxy, Nepal auth/page budget/bundle and no-UTC-rendered suites. Other tabs' requests, station detail, forecasts, trend/category outputs remain unchanged.

Each code commit follows repository patch-version policy and affected-doc updates. Run focused tests, Ruff format/check and changed-module type checks, then the **full suite after final code change locally or CI before merge**. Keep live tests outside ordinary PR checks; no live DB tests in this planning pass. Review reports and benchmark diagnostics stay local/bounded; concise outcomes and unresolved findings belong in PRs.

## Human gates and explicitly deferred work

No unresolved mean/cadence/cutoff/latest decision is reopened. Engineering schema/route decisions above are this draft's design, subject to independent review. Discrete range glyphs are the reviewers' proposed safe realization of the vision's mean line and envelope, not an already owner-approved visual substitution. Obtain owner visual confirmation from qualification screenshots before activation. Still held:

- Approve final new-route cost/admission bounds after measurements. If proposed limits cannot fit dense authorized traffic without changing scientific behavior, bring the concrete tradeoff to the owner.
- Twenty-minute retention is owner-approved. Re-review the corrected exact expiry algorithm and discrete block glyphs for implementation readiness; qualification must prove no-store freshness, monotonic timing, and stale/purge behavior. No cadence/mean decision is reopened. Backend-disable rollback retains bounded stale history; immediate removal from newly served clients needs the frontend-disabled build.
- Owner operational admission, release approvals and deployment authorization; no permission is inferred from publication of the vision or plan.

**Named follow-up: Nepal observation-history parity through reuse.** During implementation, add this to the maintained follow-up tracker with explicit links to this contract and the Swiss acceptance evidence. Reuse compact schema, aggregation geometry, refresh state machine and tests where sound; separately specify Nepal units/parameters/gauge datum, delayed readings, auth/eligibility and fleet admission. No Swiss thresholds/cadence assumptions or Nepal endpoint activation in this delivery; do not build a generic multi-deployment framework ahead of that scoped work.


## Backend progress (2026-11-05, not completion or admission)

- Domain/exact aggregation, bounded trusted PostgreSQL/fake read, auth-first default-off route and additive map contract 2.7 implemented in the working branch. Frontend and operational behavior remain unactivated.
- Initial pure/overview tests: 41 passed. Expanded map/auth/route set: 80 passed. Real PostgreSQL store boundaries: 4 passed. Real route/auth/local-timeout rollback tests: 5 passed. These are focused receipts, not the final full-suite gate.
- First representative 134-station, 5-minute, 168-hour PostgreSQL HTTP feasibility run used 270278 eligible rows (2017/station). Three responses succeeded (6.529, 4.835, 5.244 seconds), followed by four request-local 5-second SQL timeouts. Warm p95 across six attempts was 5.190 seconds. Seven SQL statements/request were measured for explicit nonempty grants, including the existing tenant-integrity check and SET LOCAL. This **fails the proposed cost envelope**; no candidate budget is approved or increased. Final error-body size is not a successful-body size measurement.
- Cost design is escalated. No index, materialization, ingestion or live-host change was made. Further representative matrix, heavy/concurrent overview regression, SQL-plan evidence, independent patch reviews, final full suite and browser/owner acceptance remain gates. Reports and diagnostic receipts stay local.
- Named Nepal parity follow-up recorded in the maintained [plan index](README.md), with no Nepal implementation.


## Bounded query experiment (2026-11-05; not adopted)

A disposable-fixture diagnostic tested a history-only direct EXISTS over the existing
safe read-state view, with redundant authorized station/time/parameter/source bounds
inside that view subquery. The production reader and latest-reader scalar wrapper
remain unchanged. Estimated plans confirmed an existing station/time index scan with
both window bounds, rather than a whole-history view scan. In one basic-case HTTP pair
with 270278 in-window rows plus 270278 older rows, baseline/candidate durations were
6.068/4.909 seconds; observation cursor execute was 3.111/2.246 seconds and unchanged
exact Python aggregation 1.561/1.549 seconds. Both successful bodies were 799353 raw
bytes. Candidate actual EXPLAIN completed in 2.749 seconds under the 5-second limit,
but retained per-row event/outer-observation lookups and disk sort spill.

This is a measured improvement, not cost acceptance: the candidate still exceeds
the proposed 3-second route target, and both requests use seven statements (four auth,
one metadata, one SET LOCAL, one observation), not the proposed six. No authorization
check or timeout statement is removed to meet that proposal. These are single-request
measurements on ordinary no-evidence-required fixtures, not independent cold/warm p95,
canonical-evidence parity, heavy excluded/held tails or concurrent qualification.
The candidate was not adopted and no budgets changed. Further strategy needs review;
all qualification and owner-admission gates above remain open. Full diagnostic reports
and the reproducible harness patch remain outside commits.


## Request-local aggregation optimization (2026-11-05)

Profiling found repeated calculations of immutable window boundaries inside the
reading loop. Aggregation now computes S, A, J, the coarse-grid origin and block
width once per request. Eligibility, duplicate handling, exact `statistics.mean`,
block membership and output precision are unchanged. A 270278-row pure-service
smoke comparison produced identical binary64-aware output hashes before and after;
uninstrumented aggregation took 1.338 and 0.470 seconds respectively. All 17 pure
service tests and focused Ruff checks passed. This is not HTTP latency or p95
qualification. The production SQL remains unchanged; no cost budget or admission
gate has changed.
