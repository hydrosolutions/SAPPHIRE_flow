# BAFU GraphQL observation path comparison
status: READY

## Decision to support
Determine whether BAFU GraphQL should complement LINDAS for observation gap recovery and historical input backfills. Keep the database lookup fix independent and first in execution order. No production source switch or automatic fallback in this investigation.

## Baseline
Existing operational LINDAS adapter fetches one whole-network snapshot and cannot honor historical since windows. BAFU documents unauthenticated GraphQL POST at https://data.bafu.admin.ch/api with historical aggregates and a live feed. Python examples are HTTP requests, not an SDK; reuse httpx if implemented.

## Bounded comparison
1. Independently review this plan before executing experiments. Inspect API schema and perform small read-only probes for Landquart 2150, Thun 2030, and 2009; add a lake/water-level case and a station currently missing live discharge. Report actual coverage, not presumed fleet completeness.
2. Compare overlapping timestamps, values, units, latency and missingness with LINDAS and our archive. Distinguish instantaneous measurements from aggregation intervals; never equate them by timestamp alone. Check recent outage recovery, one recent daily month and one historical month. Cap initial investigation at 30 sequential requests, bounded response sizes/timeouts, no load test.
3. Verify start-of-period aggregate stamps, daily interval/calendar semantics and conversion to model end-of-period convention. Preserve original timestamp, interval, source, units and release state in evidence. Source validation status does not substitute for SAP3 QC or unit authority.
4. Verify documented live retention discrepancy (intro seven days versus detailed twelve hours), historical station/parameter coverage and publication lag. Check pagination/window splitting, unordered output, GraphQL errors with HTTP 200, and bounded handling of HTTP 403 without assuming every 403 is throttling. Respect 10,000-row query cap and shared-IP 500 requests/five minutes limit.
5. Assess compressed monthly files for large backfills; avoid downloading fleet history in this comparison. Confirm licence attribution requirement and retain source/version provenance. Evaluate duplicate/revision handling against existing ingest contracts before recommending persistence.
6. Produce a concise decision: retain LINDAS only, complement it with GraphQL history, or propose replacement supported by evidence. Include expected request/storage costs, smallest integration boundary, unknowns and tests. Implementation requires a subsequent reviewed READY plan.

```json
{"steps":[{"id":"review","depends_on":[]},{"id":"probe","depends_on":["review"]},{"id":"compare","depends_on":["probe"]},{"id":"recommend","depends_on":["compare"]}]}
```

## Sources
- https://data.bafu.admin.ch/code-python
- https://data.bafu.admin.ch/dataproduct-water-observations
- https://data.bafu.admin.ch/rate-limits
- https://data.bafu.admin.ch/dateien-abrufen
- https://data.bafu.admin.ch/lizenz-und-quelle

No new Python dependency, Aquacast edits, production configuration changes or long-term dual-provider abstraction are assumed.

## Review disposition and execution order
Independent Claude review completed 2026-11-22; orchestrator resolved findings. Execute comparison probes after the lookup patch is prepared and focused verification passes. Internal archive comparisons use existing authorized, trusted read paths with bounded station/time filters and read-only transactions; never query raw authority-bypassing records. Do not intentionally trigger rate limits: exercise 403 and GraphQL error handling with local fixtures. Initial 30-request budget includes metadata and download requests; cap each response at 2 MiB and total fetched bytes at 10 MiB. Only inspect file listings/headers initially; do not decompress archives. Use normal identifying application headers without credentials. Broader archive extraction machinery is unnecessary because extraction is out of scope. Full review report remains local.
