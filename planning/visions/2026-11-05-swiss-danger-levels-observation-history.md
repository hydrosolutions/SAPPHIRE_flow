# Swiss Danger levels observation history

## Outcome

Every row of the Swiss Danger levels page shows observed discharge alongside the existing forecast. Users can see several days of context without losing recent rapid changes or historical peaks. Deliver history for the authorized station roster in one batched request, not one request per station.

This is the owner's confirmed vision, not an implementation or deployment approval. Follow current AGENTS.md and the global implementation workflow. The owner approves and merges PRs. Implementation involving the external API contract, auth/scope and user-visible scientific display is high-risk under AGENTS.md: require independent Claude and Codex passes plus an owner-commissioned relevant review.

## Confirmed display and measurement semantics

- Show seven days by default, with a three-day option.
- Older history consists of completed three-hour UTC blocks, displayed as a mean line with a light observed minimum–maximum envelope. Calculate all three statistics from the same eligible measured readings. Preserve native records in storage; this is a display summary, not a replacement observation product.
- Show the recent segment at native temporal resolution and actual observation timestamps, not on a manufactured ten-minute grid. Sensors can have different cadences, including five to fifteen minutes, and gaps.
- Join the segments at the latest completed three-hour UTC boundary that is at least one hour before the response's as-of time. Native history therefore covers at least one and less than four hours. For example, at 10:20 UTC join at 09:00; at 09:20 join at 06:00. Define interval ownership so no measurement is counted in both segments and none is lost at the join.
- The chart has no artificial gap or overlapping summary at that join. Actual missing observations remain visible gaps. Neither interpolation nor carry-forward is allowed. Do not visually bridge missing periods as though they were measured.
- Summarize available eligible readings in incomplete blocks, visibly mark the block incomplete, and expose coverage in the tooltip. An empty block is a gap, never zero. Coverage must reflect the expected native cadence, not assume all sensors sample every ten minutes. An observed envelope does not assert discharge stayed within those bounds during missing periods. A single reading cannot imply complete coverage merely because its minimum, mean and maximum coincide.
- Retain full binary64 input precision; do not round to an assumed instrument resolution. Aggregation necessarily computes a mean, but must not additionally quantize inputs or output for transport. Exclude non-finite values rather than emit invalid JSON or manufacture a measurement.
- Use QC-passed, measured discharge with the same trusted-unit and protected-delivery exclusions as the current Swiss overview. Scope, authorization, tenant and publication rules remain authoritative. Do not mix derived or withheld data into the history.
- Label the historical band as **Observed min–max**, and distinguish it clearly from the forecast's **p25–p75** band. The first describes measured variability; the second describes forecast uncertainty. A three-hour envelope locates an extreme only within that block; exact timing belongs in the unchanged station-detail chart.
- Keep danger classifications, standing categories, flow classes, counts, colours, threshold authority, latest-reading selection and trend rules independent of these aggregated display values. Preserve the forecast median/quartile curves and their exact headline identity, horizon and earliest-maximum semantics.

## Delivery, freshness and corrections

Use a separate, bounded batch history route for the Swiss roster. A sibling of `/api/v1/swiss/overview` is a candidate; the final route location and wire schema belong in the implementation contract. Reconcile this with `docs/plans/2026-11-04-shared-dashboard-overview-delivery.md`, whose generic-route proposal and 72-hour maximum differ. That separate draft is not executed or superseded wholesale here; neither it nor route placement may silently reduce this confirmed seven-day requirement or expand this delivery to Nepal. Keep the compact representation: shared time-grid metadata for coarse blocks, compact numeric summaries and explicit timestamps for native readings. The earlier uniform hourly `start/step_seconds/values` proposal alone cannot express this mixed-resolution product.

Fetch history on entering the Danger levels page and refresh it every ten minutes while that view is active. Refresh the full requested history so late observations and QC corrections can revise old blocks. The existing overview retains its sixty-second refresh and `latest`/`hourly` behavior. There must be no per-row request fan-out or hidden duplicate history requests. Remain within the existing thirty-requests-per-minute page budget, including selection, retries and other existing reads.

A closed time block is not immutable. A late eligible reading can change its mean or extrema; a QC or eligibility change can remove or replace a value, including turning a block into a gap. Include response-level generation/as-of information. Show history refresh failure or staleness explicitly; do not silently present an old response as current or reuse data after an access denial or scope change.

Start with `no-store` on the history route as well as the existing overview. Private HTTP/Worker caching is not approved by this vision. Scope, window or reading volume beyond the agreed bounds must fail explicitly, never silently truncate. Keep compatible defaults for existing callers; no history is added to the existing overview by default. Coordinate an additive contract and frontend reader-first rollout, enabling the feature only after backend availability and measured real-response verification.

First evaluate an on-request, bounded batch database read. Do not introduce ingestion-time materialization, new ingestion schedules, indexes or stored summaries merely by assumption. If measured cost requires a stored summary, account for late arrivals, QC corrections and eligibility changes before adopting it. No ingest, QC or publication policy change is authorized here. The existing `fetch_observations_batch` cannot express the required protected-delivery exclusion and must not be reused unmodified as the history authority. Apply exclusions before aggregation; prove in store and route tests that excluded readings cannot affect extrema, means, coverage, native points or error metadata.

## Contract details to settle before implementation

The display intent above is confirmed. The following scientific and compatibility details are deliberately not invented as approved decisions. Resolve them in the reviewed implementation contract, and obtain owner confirmation where the choice changes observable scientific meaning:

- **Coverage and mean:** current station/roster/observation records do not provide an authoritative expected sampling cadence. Identify a reviewed cadence source, expected-versus-eligible denominator, duplicate/phase/interval-edge treatment and completeness criterion. Unknown or ambiguous cadence must be shown as unknown coverage, never complete; do not infer completeness from the same sparse rows being assessed. A new cadence-metadata policy requires owner approval. Specify the mean definition for irregular or mixed-cadence readings, without interpolating missing observations.
- **Oldest block:** define whether the three-/seven-day cutoff is exact or aligned to the UTC grid, and the treatment of the oldest intersected block. Do not silently label a clipped block as a full three-hour period. Include exact-cutoff examples and use the same rule in size/latency measurements.
- **Right edge:** distinguish time after the history as-of from a missing-data gap within the fetched window. Specify whether and how the newer minute-refreshed latest reading appears alongside the older history; never silently merge different as-of snapshots or change latest/trend authority.

These are contract-readiness gates, not permission to implement an arbitrary interpretation. They do not reopen the agreed default depth, three-hour mean/min/max display, native recent segment or refresh cadences.

## Protected scope: other tabs do not change

The change is confined to Swiss Danger levels history delivery and presentation. **Other tabs must not change**, including their behavior, requests, refresh cadence, detailed native-resolution charts and existing forecast/reference products. Do not add history fetching to inactive or unrelated tabs. Shared-component changes are allowed only with proof that these protected views retain their existing behavior.

Do not change Nepal runtime behavior in this delivery. Do not modify model execution, reimport forecasts, rerun historical evaluations, publish private thresholds, change alert definitions, or revive deferred ingestion/input-degradation work. This is not a broad dashboard or backend refactor.

## Clear follow-up: Nepal parity through reuse

After the Swiss work, deliver the same observation-history display and interaction principles on the Nepal dashboard as a separately scoped follow-up. The owner's goal is for the two dashboards to behave the same way in principle, not to maintain divergent implementations.

Reuse the Swiss compact contract shape, aggregation/display semantics, chart components, refresh handling and tests as far as practical. Keep the Swiss implementation suitable for reuse without prematurely building a generalized multi-deployment framework. The follow-up must explicitly preserve Nepal's parameter-specific units, gauge-height/water-level semantics, authorization and eligibility rules, and frequently delayed readings. Missing hours must remain gaps and later arrivals must revise past summaries. Do not assume Swiss discharge thresholds or timing apply to Nepal. Record this follow-up in the project's maintained follow-up tracking when implementing; it is not implicitly included in this Swiss release.

## Evidence and repository entry points

Backend release 0.1.1105 restored the forecast curves. The frontend reported dashboard acceptance on 5 November: 132 forecast series, exact horizon/peak/quartile checks, approximately 299 kB raw, and a byte-identical contract. Curves, bands and arrows were confirmed live. This is a baseline, not acceptance of the new history.

Relevant backend boundaries are `src/sapphire_flow/api/routes/api_swiss_overview.py`, `api/swiss_overview_schemas.py`, `api/map_contract.py`, `protocols/stores.py` and `store/observation_store.py` under `src/sapphire_flow/`, plus `docs/spec/api-v1-map.openapi.json` and the Swiss overview benchmark tests. Consult current touchpoint maps and security/WMO standards before implementation.

In the separate `hydrosolutions/SAPPHIRE-flow-map` frontend, inspect `app/components/FlowMap.tsx`, `WaterLevelsView.tsx`, the Swiss overview readers/views and shared chart components. The Operational station read (`app/lib/referenceLiveRead.ts`) requests 168 hours for only the selected station. It is not a fleet-history source. Its backend reader does not apply the overview's identical protected-delivery exclusion, so blindly reusing it would also change eligibility.

The earlier local DRAFT `docs/plans/2026-11-05-compact-overview-observation-history.md` proposed uniform hourly latest samples embedded in the overview. This confirmed vision replaces those proposed delivery, resolution and sampling choices; it does not authorize executing that old draft. The earlier draft may not be present on the target branch. Its measurements remain diagnostic context only:

| Earlier combined overview prototype | Raw UTF-8 bytes | Gzip bytes |
| --- | ---: | ---: |
| 72 hourly samples | 501665 | 45422 |
| 168 hourly samples | 676198 | 85042 |

Those results are not measurements of the new route, three-hour mean/min/max arrays, native recent segment, or a dense-history worst case. Earlier serialization and sparse-reader timings are not end-to-end latency acceptance. A band carries three values per block, so three-hour aggregation must not be assumed to divide total response size by three.

## Observable acceptance

- All authorized Swiss rows have seven-day or three-day history as selected, using one batch response. No unauthorized stations or protected readings are disclosed, including in errors or cached frontend state.
- Prove mean/min/max correctness, native timestamps, full precision, incomplete/empty blocks, finite values, cadence-aware coverage and revised past periods. Test late arrivals and QC removal, irregular timestamps, interval edges and the coarse/native join, exact refresh boundaries, UTC/DST/year transitions and sparse histories. Document the averaging and coverage definitions so frontend and backend agree.
- Preserve the existing overview contract and its **393216 raw UTF-8 byte cap**, **p95 below one second**, and **at most twelve queries**. The frontend's suggested 1 MiB allowance is not approval to raise these limits.
- Measure the new batch route separately for both 72 and 168 hours and the full 134-station reference roster: dense full-precision native data, differing cadences, sparse/null data, maximum recent-segment length, query count, database and end-to-end p50/p95, raw UTF-8 and gzip bytes. Establish and review explicit bounded history-route budgets before rollout; do not invent an already-approved SLA or claim gzip satisfies a raw-byte cap. Escalate if the selected representation cannot meet acceptable size/latency without changing agreed behavior.
- Verify visible history refresh every ten minutes and existing overview refresh every minute; no fan-out, no request-budget regression, and explicit failed/stale-history behavior.
- Verify observed min–max and forecast p25–p75 are distinguishable, incomplete coverage is discoverable, and aggregated values cannot alter classifications or trend authority.
- Regression-check other tabs, station-detail views, existing forecast curves and Nepal behavior. Nothing outside Swiss Danger levels changes.
- Coordinate backend/frontend schema compatibility and normal-login browser acceptance against a measured deployed response. Record operational admission for the new fleet route under the existing access exception (currently three reviewers, one visible tab each); the Nepal follow-up needs its own fleet-read admission. Keep full review reports local and concise outcomes in PRs. Publication of this vision alone does not authorize implementation, merge or deployment.
