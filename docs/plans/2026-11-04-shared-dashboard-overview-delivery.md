---
status: DRAFT
owner: Dashboard API delivery
---
# Shared dashboard overview delivery — second priority

## Outcome / gate
One scoped overview pathway, Worker proxy and reader for Swiss and Nepal dashboards,
without changing the accepted displays. Start only after owner acceptance of
[Swiss danger-level restoration](2026-11-04-restore-swiss-danger-level-display.md).
This is a planning draft, not an implementation-ready API specification. Resolve T4
and T5 before an implementation pass is authorized; update and independently review
this complete plan or create a separately reviewed implementation plan. No code now.

## Direction
- Generic route, provisionally `GET /api/v1/overview`, with parameter and bounded
  `hourly_hours` (default 3, requested maximum 72). No region-specific reader.
- Credential scope is applied before reads and aggregation. Parameter/capabilities
  explain legitimate regional differences. Retain the existing Swiss route during
  migration; version additive and semantic changes through the maintained contract.
- Station identity, display name, river and coordinates remove duplicate station files
  only when their API coverage is proven. Unknown metadata stays explicitly unknown.
- Explicit observation units and timestamps; hourly gaps never zero or interpolated
  values. Define bucket semantics, missingness and any compact time axis once.
- Parameter-specific thresholds with provenance and units; datum with its reference
  system and threshold scale. Never compare gauge height with absolute elevation
  without an explicit valid conversion. Keep inferred research bounds separate from
  operational alert thresholds; exposing them must not activate alerts or ingestion QC.
- Exact forecast headline plus bounded median series. Current scheduled authority and
  latest eligible historical display remain distinct. Historical selection: CMAL first;
  any permitted alternate model is labelled, never silently substituted. Keep QC,
  input degradation, issue/valid times, not-current state and publication controls.
- Reported danger level and flow class retain separate sources and times, freshness
  and categorical meanings. Not-applicable differs from unavailable/not-onboarded;
  determine applicability from capabilities, not merely a country name.
- API requests read stored/prepared data, not LINDAS or FOEN on demand. Onboarding
  configures source/mapping; scheduled Prefect ingestion later refreshes dynamic values.
  Existing staged sources stay visibly identified until replacement fields have parity.
  A new temporary ingestion platform is not required for delivery work.
- One refresh request, no automatic fleet snapshot or per-station fan-out, no Worker
  caching. Existing scoped selected-station requests remain separate. Bound server-side
  database work too; do not conceal N+1 queries inside a single HTTP request.

## Tasks
### T4 — Verify deployment and source reality
- **Outcome:** evidence-based answers to frontend questions, not assumptions about Nepal.
- **In / Out:** read-only inspection of Swiss and Nepal deployed revision/contract,
  `/api/v1/observations/latest` support, actual credential roster (reported 21 versus
  six-station deployment context), parameter units and source coverage. Investigate
  November 2 lag by separating source observation time, collection time, ingest schedule,
  stored availability and API display. Do not declare a four-hour lag expected without
  checking each stage. No scheduling or credential changes.
- **Verification:** bounded authenticated endpoint/status inspection and stored run/source
  timestamps; report schedule and observed delay separately. Inventory existing threshold,
  datum, station metadata and display-source stores against requested fields. Verify
  frontend-reported counts (125 levels + 7 undefined leaves two of 134 unresolved) rather
  than encoding them as permanent facts. No secrets in reports.
- **Pre-change:** N/A, discovery only. Record unsupported fields as explicit gaps.

### T5 — Specify and benchmark the smallest complete contract
- **Outcome:** reviewed field/state examples and measured payload/latency budget for
  both scopes at 3, 24, 48 and 72 hours; agreed implementation scope and delivery estimate.
- **In / Out:** contract design, bounded benchmark work and query analysis in the native
  project environment. No live schema writes or dashboard cutover. Confirm how each
  metadata field is backed today; serving unavailable is honest but does not qualify
  for removing a still-needed working frontend source.
- **Verification:** measure raw/gzip bytes, p50/p95 latency, query count and row work with
  representative full roster/missingness, cold and warm paths and concurrent refreshes.
  Use unit/read-hold and tenant isolation cases. Retain the existing Swiss default budget;
  negotiate explicit larger-history budgets before implementation, not by silently
  raising limits. Define behavior for scopes beyond tested bounds without silent truncation.
  Include authorized/denied, stale/historical, undefined/unclassified/not-applicable and
  datum-scale mismatch examples. Set no delivery date until these dependencies are known.
- **Pre-change:** demonstrate current Swiss gaps and Nepal request count under the actual
  deployed versions. The current contract cannot satisfy the proposed examples unchanged.

### T6 — Deliver and migrate only after complete display parity
- **Outcome:** both deployments use the agreed route and shared reader without lost
  categories, trends, curves, units, dates, quality flags or source labels.
- **In / Out:** separately reviewed implementation detail after T5; additive backend,
  maintained OpenAPI/examples, Worker and reader. No ingestion overhaul, model changes,
  ledger repair or threshold activation bundled into the API migration.
- **Verification:** contract tests in both repositories, scoped batch/store tests, both
  regional build/isolation suites and full suites after final code changes; before/after
  fixed-fixture and browser comparisons for all views and network budgets. Run old/new
  contract comparison offline or in tests, not duplicate fleet calls on dashboard load.
  Keep the old route until migration acceptance; removal is separately approved.
- **Pre-change:** add field/state and display-parity tests before implementation; exact
  command/node list must be finalized in the implementation-ready revision after T5.

## Deferred ingestion track
Later planning covers LINDAS `hydro:dangerLevel` tied to its own observation and FOEN
`ch.bafu.hydroweb-messstationen_zustand` flow-class data with `creation_time`. Confirm
station mapping, timestamps, undefined/unclassified semantics and update cadence before
scheduling. Static threshold/reference onboarding and recurring reported-value ingestion
are different responsibilities. Do not treat this draft as permission to import the
private inferred reference or publish new metadata.

## Governance
DRAFT until discovery, owner decisions and required independent review. The owner
commissions Claude, Codex and relevant high-risk review, decides findings and approves
PRs/merges; only the orchestrator sets READY. Keep review reports local. Respect current
release ownership and deployment permissions. This draft does not supersede or execute
other history-extension drafts; reconcile them explicitly at T5.

```json
{"phases":[{"id":"discovery-after-restoration-acceptance","tasks":["T4"],"parallel":false},{"id":"contract","tasks":["T5"],"depends_on":["discovery-after-restoration-acceptance"],"parallel":false},{"id":"delivery-after-reviewed-implementation-plan","tasks":["T6"],"depends_on":["contract"],"parallel":false}]}
```
