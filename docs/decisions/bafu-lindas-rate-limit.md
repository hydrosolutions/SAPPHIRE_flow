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

## Fixer-round hardening (post-implementation review)

A multi-model review of the Plan 175 diff (Claude design + independent Codex
pass) found the deadline, cross-call pacing, and failure-taxonomy guarantees
above were each true in the common case but not enforced/tested at the edges.
Fixed in the same PR:

- **The 120 s deadline now covers bucket acquisition, not just retries.**
  `TokenBucketLindasLimiter.call` previously started its clock AFTER
  `_acquire_token()` returned, so a starved bucket's wait was free real time
  the deadline never saw. `start` now precedes token acquisition, and the
  remaining budget is checked before every attempt (including the first) and
  handed to `send` so a slow HTTP call can bound its own `timeout=`.
- **A 429 now drains the local bucket to zero.** A `call()` that retried
  (429 → sleep → 200) had only ever charged the bucket one token for two real
  HTTP attempts — the next independent `call()` could see stale token credit
  and fire immediately, recreating the exact cascade this module exists to
  prevent. A 429 response is treated as upstream proof the bucket is actually
  empty right now and drains the local count accordingly.
- **`Retry-After` below the measured floor is now clamped UP, not honoured.**
  `Retry-After: 0` or `1` used to produce a sub-floor sleep; the floor clamp
  now applies symmetrically (never below `LINDAS_RETRY_FLOOR_S`, never above
  `LINDAS_MAX_DELAY_S`).
- **`HydroScraperAdapter.verify_gauge_reachable` now routes through the shared
  limiter.** It previously POSTed directly, so a transient 429 was reported
  straight back as "unreachable" instead of being paced/retried — the one gap
  in the "every method goes through the limiter" claim above.
- **Malformed LINDAS bindings can no longer escape `_parse_bindings_typed` as
  an uncaught exception.** A non-list `bindings`, a binding missing
  `predicate`/`object`, a non-dict binding item, or a non-numeric parameter
  value now all resolve to a typed `MALFORMED_RESPONSE` outcome (with an
  outer try/except backstop in `_fetch_one` for anything still unanticipated)
  instead of aborting the whole batch before that station's health record is
  written.
- **Exhausted failures now carry the last HTTP status in `failure_detail`.**
  An exhausted 503 used to produce a generic "exhausted after N attempts"
  message with no status code; both exhaustion messages in
  `lindas_rate_limiter.py` now include `last_status`.
- **`tests/integration/live/test_lindas_live_schema.py`'s burst test now
  asserts on actual response statuses (zero 429s), not elapsed time alone** —
  elapsed time can look identical whether proactive pacing is working or
  BAFU's own 429-retry-and-recover is doing the work, so it could not by
  itself prove pacing was still in effect.

None of this changes the measured contract table above or the architecture
(still process-local, D6). See `lindas_rate_limiter.py`'s module docstring for
the authoritative up-to-date behavior.

## Second fixer-round hardening (independent Codex pass over the fixer-round diff)

A further review of the round-1 fixer diff (Claude design + an independent
Codex pass) found the deadline enforcement was CHECKED but not truly
ENFORCED, the cross-call bucket sync still leaked one call's worth of
credit, and two more untrusted-input/shape edges were unguarded. Fixed in
the same round:

- **The 120 s deadline is now actually enforced, not just checked.**
  Passing `timeout=remaining_s` to an HTTPX request does NOT bound that
  request's wall-clock time to `remaining_s` — HTTPX 0.28 applies a single
  float independently to each of connect/read/write/pool, so a request slow
  across more than one phase could still overrun the deadline (and it
  replaced each caller's own, stricter, already-configured client timeout
  with up to 120 s per phase). `TokenBucketLindasLimiter.call` now runs
  `send` through `_run_with_deadline` (or an injected `deadline_runner`),
  which executes it on a background thread and joins with a hard
  `remaining_s` timeout — the calling thread returns within budget
  regardless of which phase `send` is blocked in. `BafuObservationAdapter`
  and `HydroScraperAdapter` no longer pass `timeout=remaining_s` at all; the
  HTTP client's own configured timeout governs each phase, and the deadline
  runner bounds the aggregate. Bucket-starvation waits are capped the same
  way (`_acquire_token(deadline_remaining_s=...)`).
- **A token is now acquired before EVERY attempt, not just the call's
  first.** A retried HTTP request also spends real upstream capacity; only
  charging the call's first attempt meant a `call()` that retried (429 →
  sleep → 200) left the local bucket believing a full extra token had
  accrued that upstream never actually had spare — the VERY NEXT independent
  `call()` would send unpaced and could hit another preventable 429. The
  per-attempt acquisition normally finds the token already refilled by the
  retry backoff (which already waits at or above one refill period) and
  costs no extra wait; it exists to charge the token, not to gate the retry
  a second time.
- **A few-hundred-to-several-thousand-digit `Retry-After` now clamps to
  `LINDAS_MAX_DELAY_S` instead of raising.** `float(int(header))` raises
  `OverflowError` once the (arbitrary-precision) Python int exceeds float's
  ~308-digit magnitude; a string past CPython's int-string-conversion digit
  limit (default 4300) raises `ValueError` out of `int()` itself before
  `float()` is even reached. Both are untrusted-input shapes (D7) that must
  clamp, not escape past this boundary and suppress the collector's
  heartbeat.
- **`HydroScraperAdapter`'s two malformed-envelope guards now catch
  `TypeError` too.** `response.json()["results"]["bindings"]` raises
  `TypeError` (not `KeyError`/`ValueError`) for a wrong-SHAPED but
  valid-JSON envelope — a top-level list, `{"results": null}`, or
  `{"results": []}` — since indexing a list/`None` with a string key is a
  `TypeError`. Both `_fetch_one` and `verify_gauge_reachable` had this gap;
  both now resolve to `MALFORMED_RESPONSE` / `reachable=False` instead of
  aborting the batch before a health record is written.
- **The live burst test no longer starts from a phantom-full bucket.**
  `TestLiveLindasSchema` and `TestLiveLindasRateLimit` used to each build
  their own default (fresh, capacity-3) limiter — so the rate-limit test's
  zero-429 assertion started immediately after the schema test had already
  spent the REAL upstream bucket on 7 live requests, with the local model
  having no idea. Both test classes now share one `TokenBucketLindasLimiter`
  instance (a module-scoped fixture) injected via each adapter's `limiter=`
  parameter, so the local model tracks the real upstream state across every
  request the module makes.
- **`ReplayStationAdapter` now implements `fetch_observations_batch`.**
  `ingest_observations_flow` calls that method exclusively (T3); any
  `StationDataSource`-conforming adapter that only implements the base
  Protocol's `fetch_observations` — `ReplayStationAdapter`, used across
  replay/reference-fixture tooling (Plans 019/020/021/045) — would raise
  `AttributeError` if ever handed to the flow as `adapter=`. A new narrow
  `BatchStationDataSource` Protocol (`protocols/adapters.py`) documents the
  capability without widening `StationDataSource` itself (that widening was
  already the explicitly rejected alternative — see
  `docs/spec/types-and-protocols.md` § StationDataSource / touchpoint-maps.md).

None of this changes the measured contract table above or the architecture
(still process-local, D6). See `lindas_rate_limiter.py`'s module docstring
for the authoritative up-to-date behavior.

## Escalation

The 429 ceiling is undocumented by BAFU. If it is ever observed to have
moved, the escalation contact is on file:
`abfragezentrale@bafu.admin.ch` (per `bafu-lindas-monday-window.md`).
