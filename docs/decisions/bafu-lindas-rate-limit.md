# Decision Record — BAFU LINDAS rate-limit resilience (Plan 175)

**Date**: 2026-08-17
**Status**: Active, implemented (Plan 175)
**Owners**: Bea (orchestrator), IT specialist (review)
**Cross-reference**: see `docs/decisions/bafu-lindas-monday-window.md` — a 429
burst is a **separate** failure mode from the Monday-republish transient and
must not be re-triaged as schema drift (BAFU VoID descriptor stays intact
during a 429 burst; a schema-drift alert would show a broken descriptor or
missing predicates instead).

## Context

The mac-mini's watchdog posted `[SAPPHIRE staging] BAFU observation collector
DEGRADED`. The collector and the BAFU data were both fine — the endpoint was
rate-limiting the collector, and nothing in the repo understood what an HTTP
429 was.

## Measured contract (live, 2026-08-17, dev machine, `lindas.admin.ch/query`)

| Measurement | Result |
|---|---|
| Burst capacity from idle | **3 requests**, 4th returns 429 |
| `Retry-After` header on 429 | **absent** |
| Refill after drain | 429 at +2 s idle, **200 at +4 s** (≈1 slot per 3–4 s) |
| Full recovery after drain | ~15 s |
| Budget refill *during* a tight loop | **none** — 40 sequential station queries in 0.2 s → 4×200, 36×429 |
| Whole-graph query (collector) | 733 bindings → 495 rows / 233 gauges, ~0.1 s, single request |

## The three defects (fixed by Plan 175)

1. **429 was handled nowhere.** `BafuObservationAdapter._post_with_retries`
   retried only `status_code >= 500`; a 429 fell through to
   `raise_for_status()` and became a fatal `AdapterError` on the first
   attempt. `HydroScraperAdapter` had no retry logic at all.
2. **The two LINDAS consumers collided at `:05`, every hour.**
   `ingest-observations` runs `*/5 * * * *`; `collect-bafu-observations` ran
   `5 * * * *`. Minute 5 is in both, on different work pools, so the
   collector's `concurrency_limit=1` never serialised them.
3. **The ingest fan-out spent the budget and hid the damage.**
   `HydroScraperAdapter.fetch_observations` polled stations sequentially, one
   POST each, no pacing, no retry, and swallowed every failure into a
   `observation.fetch_failed` warning before returning whatever it got. The
   flow then reported a successful run.

## Fix (Plan 175 T1–T4)

- `adapters/lindas_rate_limiter.py` — one shared, process-local token-bucket
  limiter (capacity 3, refill 1 per 4 s) plus bounded 429/5xx/transport retry
  behind a small `LindasRateLimiter` Protocol. `Retry-After` is honoured when
  present (delta-seconds and HTTP-date forms) but treated as **untrusted
  input**: clamped to 60 s per wait, with a 120 s total wall-clock deadline
  across all attempts of one call. Malformed/negative/past values fall back
  to the 4 s floor.
- `BafuObservationAdapter` and `HydroScraperAdapter` both route every POST
  through this limiter. `tools/record_fixtures.py` and
  `tests/integration/live/test_lindas_live_schema.py` get the same limiter by
  construction (they build the adapters directly, with no override).
- `collect-bafu-observations`'s cron moved from `5 * * * *` to `37 * * * *` —
  in both `docker-compose.yml` (the fallback that actually deploys) and
  `cli/register_deployments.py` (the Python fallback for a non-compose run).
  `37` is clear of every `*/5` boundary and of the `0 * * * *` BAFU-forecast
  collector.
- `ingest_observations_flow` now calls `HydroScraperAdapter.
  fetch_observations_batch` (typed per-station outcomes — see D9's
  `FetchOutcomeCause`), writes a `PipelineCheckType.OBSERVATION_INGEST_FETCH`
  health record immediately after fetch reconciliation (before store/QC, so
  a later failure can never suppress the fetch signal), and reports
  `IngestResult.stations_failed` as the union of fetch failures and QC-task
  exceptions.

## Hazards for a future reader

**Shared-NAT hazard.** A developer probing LINDAS from the office network (or
any shared egress IP) spends the SAME budget the mini's collector draws
from — the bucket is per-endpoint-per-source-IP, not per-process. An ad-hoc
`curl`/notebook query against `lindas.admin.ch` from the office can trip the
mini's next scheduled poll into a 429, even though the two processes share no
code.

**The per-station fan-out does not scale (D5, deferred by owner decision).**
LINDAS's ceiling is 3 requests; `docs/v0-scope.md` targets ~1000 stations.
One request per station per poll is structurally impossible at that scale:
against the burst-3 ceiling, a poll of `N` stations costs `N` requests, and
once `N` exceeds the budget the excess stations pay the ~4 s/station retry
floor — at ~4 s/station a 1000-station poll takes over an hour, far past its
own 5-minute cadence. The collector already demonstrates the way out: **one**
whole-graph SPARQL query returns all 233 gauges in ~0.1 s. Converting the
operational ingest path to whole-graph-plus-local-filter is the real fix, but
it must NOT reuse the quarantined `BafuObservationAdapter` (Plan 136 DC-2
forbids that adapter constructing `RawObservation`/`StationId`) — it needs a
shared query/parse layer with two distinct mappers. **Not solved here.**
Revisit once onboarding grows past a handful of stations (2 on the mini as of
2026-08-17) — T0's live station/failure count is the trigger to re-open this.

**Pacing is process-local, not cluster-wide (D6).** A token bucket inside one
Python process cannot enforce a shared budget across two different Prefect
work-pool processes. What actually removes the cross-process contention here
is schedule separation (the `:37` cron move), not the limiter. The limiter is
written behind a `LindasRateLimiter` Protocol so a cross-process
implementation (e.g. a Prefect global concurrency limit) can be substituted
later without touching any caller — but that is not built now; the residual
risk is a late/overlapping run landing on the same minute as another LINDAS
caller by coincidence, which schedule separation makes rare, not impossible.

## Escalation

The 429 ceiling is undocumented by BAFU. If it is ever observed to have
moved, the escalation contact is on file:
`abfragezentrale@bafu.admin.ch` (per `bafu-lindas-monday-window.md`).
