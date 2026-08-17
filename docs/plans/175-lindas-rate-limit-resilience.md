---
status: READY
created: 2026-08-17
plan: 175
title: LINDAS rate-limit resilience — stop a 3-request burst ceiling from looking like a dead collector
scope: Make every SAPPHIRE→LINDAS caller respect the endpoint's measured rate limit (burst 3, ~1 slot per 3-4s, HTTP 429 with no Retry-After), retry 429 instead of treating it as fatal, deconflict the two colliding crons, and stop the observation ingest from reporting success while silently dropping stations. Swiss/BAFU only. Diagnosis-complete — measured live on 2026-08-17. Archive COMPLETENESS (the 10-minute grid) was split out to Plan 176.
depends_on: []
blocks: [176]
supersedes: []
---

# Plan 175 — LINDAS rate-limit resilience

## Status

**READY** (owner flip 2026-08-17). Root cause **measured, not inferred** — the reproduction and the rate-limit
characterisation in § Evidence were run live against `lindas.admin.ch/query` from the development
machine on 2026-08-17 08:0x–08:2x UTC. Responds to a **live** incident: `[SAPPHIRE staging] BAFU
observation collector DEGRADED` Slack alerts from the mac-mini.

**All owner decisions are closed** (2026-08-17): **D1** — keep the collector and move its cron to `:37`
(it is not redundant with ingest; LINDAS has no history, so uncollected data is unrecoverable).
**D5** — the per-station fan-out's scaling ceiling is *deferred and documented*, not solved.
Everything else is settled by measurement. Owner owns the READY flip.

**Review history — `/plan` ESCALATED (stalled, 3 rounds, 2 blockers + 10 majors residual), and this
draft is the reconstruction, not the loop's output.** The loop introduced a Prefect **global-concurrency
rate-limit gate** (a cross-process Tier-1 limiter with server-side limit provisioning, a dual gate
Protocol, read-then-converge registration, a 3-reason acquisition-failure taxonomy and an `active=False`
Prefect-bypass regression test), then spent rounds 2–3 discovering bugs *in its own addition* — the
gate-acquisition-timeout blocker, the `ConcurrencySlotAcquisitionError` mislabelling major, the untested
gate-selector major. Its own proportionality reviewers named this correctly: the cross-process guarantee
"is not required to fix any of the three defects". Per the repo's standing lesson (memory
`feedback_plan_workflow_over_expands`, `feedback_independent_review_beats_automated_loop`) the plan was
reconstructed from the pre-review baseline with the *verified* findings folded in; the 838-line inflated
version is archived outside the repo. **Cross-process coordination is deliberately NOT built here** —
see D6.

Findings folded after verifying each against the code (per `feedback_verify_before_folding`): the
unbounded `Retry-After` blocker (**D7**), `PipelineHealthRecord` having no `stations_failed` field
(**D8**, verified at `types/pipeline.py:15-24`), the outcome enum's missing HTTP-status/no-data causes
(**D9**, verified against `tests/integration/adapters/test_hydro_scraper.py:197,206` which lock
`obs == []`), the unsound nested-retry test (T2), and real sleeps in the mock integration tests (T3).
One finding did not survive verification and was rejected: the AGENTS.md tagging objection (**D10**).
A second rejection — "`tools/record_fixtures.py` is not a LINDAS caller" — was itself **wrong and has
been withdrawn**; see the correction under D6.

**Confirming single independent Codex pass (2026-08-17, `codex exec --sandbox read-only`) —
CHANGES_REQUIRED: 1 blocker + 4 majors, all verified against the code and all folded in.** This pass
earned its keep where the 3-round loop had not:
1. **(blocker) T4 was a deployment no-op.** `docker-compose.yml:383` pins the cron, so editing only the
   Python fallback would have left the mini at `:05` with the alerts still firing — while a unit test
   went green. `tests/unit/test_compose_schedule_default.py` documents this exact trap for the sibling
   schedule. The "ship T4 first as a stopgap" recommendation would have silently done nothing.
2. **(major) T3 would have broken the `StationDataSource` Protocol** — `fetch_observations` is contracted
   to return `list[RawObservation]`.
3. **(major) T1's "429 and 5xx only" would have removed the collector's existing transport retry.**
4. **(major) the exhaustion test asserted `max_retries + 1` sleeps** — off by one; a correct
   implementation would have failed it.
5. **(major) the fetch health record had no check type, and "visible to Flow 4" was false** (Flow 4 is
   deferred).

**SCOPE EXTENSION, then SPLIT OUT (2026-08-17).** Validating the owner's "collect all observations …
I want to save the data" goal falsified Plan 136's locked `cadence = hourly` finding — LINDAS publishes
on a **10-minute grid**, so the archive collector had been discarding ~83% of observations. That work was
briefly folded in here, and independent review returned a blocker in the folded half **twice in two
rounds** (the second being work-pool starvation — an orchestration problem, not a cron one) while this
plan's rate-limit half stayed stable and approved. **It is now Plan 176**, which depends on this one.
All cadence, publish-lag and storage measurements moved there intact.

**This plan is therefore back at the scope the confirming pass APPROVED**, with one carry-over: the
`:05 → :37` cron move stays here as the collision fix (D4), because it is what stops the 429s.

## Problem

The mac-mini's watchdog is posting `BAFU observation collector DEGRADED`. The collector is fine and
the BAFU data is fine. The endpoint is rate-limiting us, and nothing in the repo knows what a 429 is.

### What "DEGRADED" narrows it to

`watchdog.py:922` raises DEGRADED when a heartbeat **was** found and its status is `warning`/`critical`.
That excludes the whole class of "collector never ran" faults (those produce STALE via
`BAFU_OBS_STALE_THRESHOLD`). So the collector ran and wrote a `CRITICAL`
`PipelineHealthRecord` itself. `flows/collect_bafu_observations.py` has exactly three CRITICAL paths:
`AdapterError` (`:358-370`), `empty_response` (`:372-384`), and `stale_measurement_time` (`:418-428`).

Measured live today: the graph is healthy (233 gauges, 495 rows, newest `measurement_time` 16 min old)
and the BAFU VoID descriptor is intact — so `empty_response` and `stale_measurement_time` are ruled out
**for the current probe**, and per `docs/decisions/bafu-lindas-monday-window.md` triage step 1 this is
**not** schema drift and not the Monday-republish transient (despite the alerts landing on a Monday
morning). That leaves **`AdapterError`**, and the only `AdapterError` the live endpoint produces right
now is HTTP **429** — reproduced below.

**Attribution caveat (folded minor).** A healthy graph *now* cannot prove which CRITICAL path fired
during the alert itself: the graph could have recovered in between. The 429 mechanism is reproduced and
the collision is arithmetic, but final attribution needs the `error_type` values already stored in
`pipeline_health` on the mini — which is exactly what **T0** reads. T0 confirms; it does not merely
quantify.

### The three defects that turn a 429 into a false outage alarm

1. **429 is handled nowhere in the codebase.** `grep -rn "429\|Retry-After"` over `src/` and `tests/`
   returns only unrelated hits. `BafuObservationAdapter._post_with_retries` retries **only**
   `status_code >= 500` (`adapters/bafu_observation.py:273`); a 429 falls through to
   `raise_for_status()` (`:284`) and becomes a fatal `AdapterError` on the **first** attempt, with
   `max_retries=2` never spent. `HydroScraperAdapter` has no retry logic at all.

2. **The two LINDAS consumers collide at :05, every hour.** `cli/register_deployments.py:44` schedules
   `ingest-observations` at `*/5 * * * *`; `:63` schedules `collect-bafu-observations` at `5 * * * *`.
   Minute 5 is in both. They sit on **different work pools** (`ingest` vs `default`), so the
   collector's `concurrency_limit=1` (`:150`) does not serialise them — two workers hit the same
   endpoint in the same second.

3. **The ingest fan-out spends the budget and hides the damage.**
   `HydroScraperAdapter.fetch_observations` (`adapters/hydro_scraper.py:64-112`) loops stations
   sequentially, one POST each, **no pacing, no retry**, and swallows every failure as a
   `observation.fetch_failed` warning (`:106-111`) before returning whatever it got. The flow then
   reports a successful run. `IngestResult` even has a `stations_failed` field — the adapter never
   gives the flow the information to populate it truthfully.

### Why it is intermittent

Against a burst ceiling of **3**, a :05 tick costs `N_polled + 1` requests. Plan 136 §Live-inventory
records the live mini reporting `stations_polled=2` on 2026-07-21, i.e. **2 + 1 = 3 — exactly the
ceiling, with zero headroom.** Any one of: a third onboarded station, a Prefect retry, an operator's
ad-hoc query, or a developer probing LINDAS from the same NAT egress IP, pushes the collector's single
request over the edge. Hence occasional alerts rather than a permanent red.

### Correction to an earlier claim in this investigation

Mid-investigation I stated that ingest was "~98% silently failing" on the basis of the **169**
`basin_ids` in `config.toml`. That figure is **not** an established fact about the mini: Plan 136
explicitly notes `config.toml`'s onboarding list is "a different, larger population" than the live
deployment, which polled **2**. The *mechanism* (paragraph 3 above) is confirmed by reproduction; the
current blast radius on the mini is **unverified** and needs T0. The mechanism is nonetheless a hard
scaling blocker — see D5.

## Evidence (live, 2026-08-17, dev machine)

Rate limit, measured from a cold bucket:

| Measurement | Result |
|---|---|
| Burst capacity from idle | **3 requests**, 4th returns 429 |
| `Retry-After` header on 429 | **absent** |
| Refill after drain | 429 at +2 s idle, **200 at +4 s** (≈1 slot per 3–4 s) |
| Full recovery after drain | ~15 s |
| Budget refill *during* a tight loop | **none** — 40 sequential station queries in 0.2 s → 4×200, 36×429, pattern `....XXXX…X` |
| Whole-graph query (collector) | 733 bindings → 495 rows / 233 gauges, ~0.1 s, single request |
| BAFU VoID descriptor | `dateModified 2026-08-17T08:04:02`, `hasPart` lists both `river` and `lake` → schema intact |

End-to-end reproduction of the incident:

```
ingest per-station statuses: [(2004,200),(2009,200),(2011,200),(2016,429),(2017,429),(2018,429),…]
COLLECTOR FAILED -> AdapterError: BAFU LINDAS whole-graph request to https://lindas.admin.ch/query
                                  failed with status 429: Client error '429 Too Many Requests'
```

`collect-bafu-forecasts` is **not** a LINDAS consumer (`adapters/bafu_forecast.py` targets
`hydrodaten.admin.ch`, zero `lindas` references) and therefore does not compete for the budget.

## Goal

1. A 429 costs us a bounded retry, never a false outage alert — and never an unbounded sleep (D7).
2. Every in-process production LINDAS request goes through one injectable limiter, so a single process's
   offered load is bounded by construction rather than by luck. **Scoped honestly:** the guarantee is
   per-process; cross-process safety comes from schedule separation (D6), not from this limiter.
3. The two crons stop colliding.
4. An ingest run that drops stations says so, loudly, instead of reporting success.
5. The collector stops losing hourly snapshots to 429s. (Making the archive *complete* — capturing the
   full 10-minute grid rather than one slot an hour — is **Plan 176**, which depends on this one.)

## Non-goals

- Changing the watchdog. It behaved correctly — it reported a genuinely CRITICAL heartbeat. No edits.
- Touching the BAFU **forecast** collector's retry logic (different host, not rate-limited, no evidence
  of a problem).
- Re-architecting the operational ingest onto the whole-graph query. That is the right end state but a
  larger review surface (it crosses Plan 136's DC-2 quarantine boundary). **D5: deferred by owner
  decision — documented as a scaling ceiling in T5, no follow-on plan drafted yet.**
- Any retention/backfill of observations already missed. LINDAS serves real time only; missed data is
  gone and this plan does not pretend otherwise.

## Decisions

**D1 (OWNER — RESOLVED 2026-08-17: keep it, move its cron to `:37`.)** The owner subsequently made
the collector *load-bearing*: "collect all observations available on LINDAS … I want to save the data."
It is no longer an evaluation-only nice-to-have whose failure is merely noisy — every failed run now
loses data the owner has said is needed. It stays quarantined (no `station_id`, no operational use), but
it is no longer optional, which is what justifies T1/T2's retry work rather than just moving the cron.
The owner asked whether the collector is redundant now that observation ingestion works. It is not —
they are not substitutes, and the owner confirmed keeping it once that was established:

| | `collect-bafu-observations` (Plan 136) | `ingest-observations` |
|---|---|---|
| Coverage | **all 233 BAFU gauges** on the graph | only **onboarded** stations (2 on the mini) |
| Keyed by | `(gauge_code, lindas_kind)` | `station_id` |
| Destination | quarantined parquet archive, evaluation-only | operational DB |
| Purpose | forward-only network-wide record for benchmarking (Plan 111) | operational forecasting inputs |

Plan 136's founding rationale is that **LINDAS carries no history** — it is real-time only. Whatever
the collector does not capture in its hourly snapshot is unrecoverable. Disabling it permanently
forfeits network-wide observation history for the 231 gauges ingest does not touch. If the owner still
ever wants it off, that is a one-line overlay change (drop
`[adapters.bafu_observation].archive_base_path` from `config/overlays/mac-mini.toml`, which re-gates the
flow to a no-op) — but T4 alone removes the alert without giving up the data, so "off" is a data-value
decision, not an alert-silencing one. **Not taken.**

**D2 — a 429 is retryable, with pacing, not merely retried.** Because the bucket refills at ~1 slot per
3–4 s and sends no `Retry-After`, blind immediate retries cannot succeed. Retry must be paced from a
measured floor. **Locked:** honour `Retry-After` when present; otherwise back off from a
`LINDAS_RETRY_FLOOR_S = 4.0` baseline.

**D3 — one shared limiter, injected, not a per-adapter sleep.** Both adapters take the same limiter
object. It carries an injected clock and sleeper (per CLAUDE.md § Testability — no bare `time.sleep`
in business logic), so unit tests are deterministic and never touch the network.

**D4 — the collector's minutes must not be divisible by 5.** `*/5` covers every minute divisible by
5, so :35 or :20 would collide exactly as :05 does. **Locked: `37 * * * *`** — clear of every `*/5`
tick and of the `0 * * * *` forecast collector. Plan 176 replaces this with a denser schedule that
preserves the same non-divisible-by-5 rule; the rule is what T4's test asserts, not the literal string.

**D5 (OWNER — RESOLVED 2026-08-17: DEFERRED, document only. No follow-on plan is drafted now.)**
The per-station fan-out does not scale. LINDAS's ceiling is 3 requests; `docs/v0-scope.md` targets
~1000 stations. One request per station per 5 minutes is structurally impossible at that scale — T3's
pacing buys correctness but at ~4 s/station a 1000-station poll takes over an hour, far past its own
5-minute cadence. The collector already demonstrates the answer: **one** whole-graph query returns all
233 gauges in ~0.1 s. Converting ingest to whole-graph-plus-local-filter is the real fix, but it must
not reuse the quarantined `BafuObservationAdapter` (Plan 136 DC-2 forbids that adapter constructing
`RawObservation`/`StationId`) — it would need a shared query/parse layer with two distinct mappers.

**Owner decision: record the limit, revisit when onboarding actually grows past a handful of stations.**
T5 therefore carries a documented **scaling ceiling** (with the arithmetic above) so the constraint is
discoverable at the moment someone onboards in bulk — the failure mode is silent station drops, not an
error, so a future reader must not have to rediscover it. T3's honest `stations_failed` reporting is
what converts that silence into a signal in the meantime, which is why T3 stays in scope.

**D6 — pacing is PROCESS-LOCAL, and the plan says so out loud.** The `/plan` loop's instinct was right
about the mechanism — two flows on different work pools are different *processes*, so a process-local
token bucket cannot enforce a shared budget of 3 between them — but wrong about the remedy's cost. What
actually removes the cross-process contention is **T4**: the collector's minute is non-divisible by 5
(D4), so its start never shares a minute with `ingest-observations` at `*/5`. The forecast collector is
not a LINDAS caller at all. Schedule separation is a $0 fix for the contention; a Prefect
global-concurrency limiter is a new piece of distributed infrastructure for the *residual* risk of a
late/overlapping run. **Locked: process-local pacing + schedule separation.** The limiter is written
behind a small Protocol so a cross-process implementation can be dropped in later without touching
callers, and the residual limitation is documented in T5 rather than hidden. Revisit with **D5** — the
station count is what makes a shared budget necessary, and it is 2 today.

The complete in-scope caller set — **corrected**, see the note below: `adapters/bafu_observation.py`,
`adapters/hydro_scraper.py`, `tools/record_fixtures.py` (its BAFU path constructs `HydroScraperAdapter`
at `:230,:254` and calls `fetch_observations` at `:111`), and the
`tests/integration/live/test_lindas_live_schema.py` live check. Goal 2 is scoped to **in-process
production calls**; the recorder and live test get the same limiter but no cross-process guarantee.

> **Correction — an earlier "verified" claim in this plan was wrong.** A previous revision asserted that
> `tools/record_fixtures.py` is *not* a LINDAS caller and used that to **reject** a review finding. That
> was a bad verification: the grep behind it searched for the literal `lindas` string, which the recorder
> never contains because it reads its endpoint from `config.toml` (`:239`). The reviewer was right and
> the rejection is withdrawn. Any future caller inventory must grep for the **adapter classes**, not the
> hostname.

**D7 — `Retry-After` is untrusted input and must be bounded** (folded blocker). Honouring the header
without a cap turns an upstream `Retry-After: 86400` into a day-long sleep inside a Prefect task.
**Locked:** parse both delta-seconds and HTTP-date forms (via the injected clock, so tests stay
deterministic); reject negative, malformed, and past values by falling back to the floor; clamp any
single delay to `LINDAS_MAX_DELAY_S = 60.0`; and enforce a `LINDAS_TOTAL_DEADLINE_S = 120.0` budget
across all attempts so bounded *attempts* also means bounded *wall-clock*. Nothing on the LINDAS path
may block longer than that deadline.

**D8 — health-record counts live in `detail`, and the schema is locked here** (folded major).
`PipelineHealthRecord` (`types/pipeline.py:15-24`) has fields `check_type`, `checked_at`, `status`,
`subject`, `detail: dict`, `cycle_time`, `created_at` — there is **no** `stations_failed` field, so
"ingest reports `stations_failed`" is only meaningful if the `detail` shape is pinned. **Locked detail
keys:** `stations_polled`, `stations_fetch_failed`, `failure_counts_by_cause`, `failed_station_ids`.
The record is written **immediately after fetch reconciliation**, not at end-of-flow, so a later storage
or QC failure cannot suppress the fetch signal — which is the whole point. The check type covers fetch
only; QC failures keep their existing path, and `IngestResult.stations_failed` remains the union.

**D9 — the fetch-outcome taxonomy must cover what the code actually does** (folded major). A four-cause
enum cannot represent the real failure set. **Locked causes:** `RATE_LIMITED` (429 after retry
exhaustion), `HTTP_STATUS_ERROR` (any other non-2xx, carrying the status code), `TRANSPORT_ERROR`,
`MALFORMED_RESPONSE`, and `NO_DATA` — the last two deliberately split, because today a bad timestamp and
an empty result are indistinguishable: `tests/integration/adapters/test_hydro_scraper.py:197,206` lock
`obs == []` for *both* `test_empty_bindings_returns_empty_list` and `test_malformed_timestamp_skipped`.
T3 must therefore reconcile those two existing tests deliberately (keep a legacy list façade, or update
them with the behaviour change stated) — not break them by accident.

**D10 — `AGENTS.md` and `CLAUDE.md` disagree about tagging; that is a repo bug, not a plan defect.**
A reviewer blocked the exit gate on `AGENTS.md:241,252` ("Always tag after every commit") contradicting
this plan's tag-after-merge gate. Verified: the contradiction is real. `AGENTS.md` (last touched
2026-07-16) still says tag every commit; `CLAUDE.md` was changed **2026-08-13** to tag only on `main`
after merge, with a recorded rationale — `v0.1.692`, `v0.1.697` and `v0.1.712` were each claimed twice
in one day because every worktree shares one tag namespace while each branch computes its next patch from
its own `pyproject.toml`. **Resolution: `CLAUDE.md` governs** (it is the newer rule, it carries the
evidence, and it is the designated project-instruction file); the reviewer's inference that a different
file outranks it is rejected. The genuine finding is that **`AGENTS.md` is stale and should be synced** —
a one-line doc fix, tracked as **T7**, deliberately kept separate so a policy edit does not ride in on a
rate-limit patch. This plan's exit gate stands unchanged.

## Tasks

### T0 — establish the real blast radius on the mini (requires mini access; blocks nothing)

Read-only triage, no code. On the mini: how many stations does ingest actually poll
(`ingest.starting stations=` in the flow logs), and what fraction of per-station fetches are
429-failing (`observation.fetch_failed` warning counts per run)? Also count `bafu_observation_freshness`
CRITICAL records and their `error_type` values in `pipeline_health` to confirm the 429 signature and
measure the alert rate.

**Also — archive-completeness audit.** Count the hourly parquet
snapshots actually present under the archive path against the expected count since deployment. Nobody
currently knows whether the 429s have been costing whole slots: the collector alerts on the *current*
run, never on gaps in the archive. This quantifies what is already unrecoverably lost, and gives a
before/after baseline for Plan 176's cadence change.

**Exit:** the numbers recorded in this plan's § Evidence. If the polled count is >3, T3's severity rises
from latent to active and D5 becomes urgent.

### T1 — shared LINDAS rate limiter + 429-aware retry (do first; T2/T3 depend on it)

New module, one home for the endpoint's contract, behind a small Protocol (D6) so a cross-process
implementation can replace it later without touching callers. Frozen-dataclass config, injected `clock`
and `sleeper`, no module-level state:

- Token-bucket pacing: capacity 3, refill 1 per `LINDAS_RETRY_FLOOR_S` (4.0 s), conservative vs the
  measured 3–4 s. Process-local by decision (D6).
- Retryable set: **429, 5xx, and transport failures** (`httpx.RequestError`/`httpx.HTTPError`). A 404 or
  other non-2xx fails immediately and does **not** spend the retry budget.
  **Do not narrow this to 429+5xx** (folded major): `adapters/bafu_observation.py:261-271` *already*
  retries `httpx.HTTPError` from `post()`, and `test_connect_error_after_retry_cap_raises_adapter_error`
  (`tests/unit/adapters/test_bafu_observation.py:254`) locks that behaviour with two retry sleeps.
  A literal "429 and 5xx only" reading would either break the suite or silently weaken live resilience.
  Transport failures classify as `TRANSPORT_ERROR` only **after** exhaustion.
- `Retry-After` handling per **D7**: delta-seconds and HTTP-date, clamped to `LINDAS_MAX_DELAY_S`
  (60 s), with `LINDAS_TOTAL_DEADLINE_S` (120 s) across all attempts; malformed/negative/past → floor.
- Bounded total attempts *and* bounded wall-clock; on exhaustion raise `AdapterError` carrying the
  status, attempt count, and which bound was hit.
- Distinguishable log events for `throttled` (429 seen, retrying) vs `exhausted` (giving up), so the
  mini's logs separate "endpoint busy" from "endpoint broken".

**Tests (fakes only, no network — a fake clock and a recording sleeper, never real time):** `429,429,200`
proves retry succeeds and the sleeper saw ≥ floor; `Retry-After: 7` proves 7 s honoured; **`Retry-After:
86400` proves the 60 s clamp** and **a slow sequence proves the 120 s total deadline aborts** (both are
the D7 blocker's regression tests); `Retry-After: <HTTP-date>` and a malformed value prove parsing and
fallback; a 404 proves zero retries and zero sleep; all-429 proves `AdapterError` after the cap; a
bucket-pacing test proves N calls request ≥ (N−3)×floor of sleep.

### T2 — collector consumes the limiter; 429 stops being fatal

`adapters/bafu_observation.py:_post_with_retries` — route through T1 so 429 is retried instead of
short-circuiting to `AdapterError` at `:284`. Preserve every existing behaviour the Plan 136 review
locked: all failures still normalise to `AdapterError` (so the flow's CRITICAL heartbeat path at
`:358-370` still fires when the endpoint is genuinely down), and the `_QUERY_LIMIT` truncation guard is
untouched.

The old `for attempt in range(self._max_retries + 1)` loop must be **removed**, not wrapped — leaving it
around the new helper would nest retry budgets (up to 3×3 attempts) and multiply the offered load.

**Tests:** the Plan 136 heartbeat tests must still pass unchanged; add one proving a transient 429
followed by a 200 yields rows and writes an **OK** heartbeat (the regression test for this incident).

**Test-soundness note (folded major).** A `5xx→429→200` success-path test canNOT detect a retained outer
loop — the helper succeeds on the first outer iteration, so a nested loop adds no attempts and no sleeps
and the test passes either way. The nested-retry lock must be an **exhaustion** test: feed enough
retriable failures to exhaust the helper, then assert the **exact HTTP attempt count**. A retained outer
loop exceeds it and fails RED. Prove it RED against a deliberately re-nested variant before accepting it.

**Mind the sleep/attempt off-by-one** (folded major): with `max_retries = N` there are `N + 1` HTTP
attempts but only `N` retry sleeps — no sleep follows the terminal failure, just as none follows success.
`tests/unit/adapters/test_bafu_observation.py:252,282` already lock 2 sleeps for 3 attempts. Asserting
`max_retries + 1` *sleeps* would reject a correct implementation. Assert `N + 1` attempts and `N` sleeps;
the attempt count alone is sufficient to catch nesting.

### T3 — ingest: pace the loop, and stop reporting success while dropping stations

`adapters/hydro_scraper.py`:
- Route the per-station POST through the T1 limiter.
- Stop swallowing failures into the void — but **do not change `fetch_observations`' signature**
  (folded major). `fetch_observations` is the `StationDataSource` **Protocol** method, contracted to
  return `list[RawObservation]` (`protocols/adapters.py:66-72`, authoritative spec
  `docs/spec/types-and-protocols.md:3371`). Returning a frozen result type from it would break the
  Protocol, the replay adapter, the test fakes, and `tools/record_fixtures.py:111`, which consumes the
  list directly.
  **Locked shape:** keep `fetch_observations` as the list façade, and add a HydroScraper-specific typed
  batch method (returning per-station outcomes with the **D9** failure-cause enum) that the ingest flow
  calls instead. The façade delegates through the same limiter, so no caller escapes pacing. Widening
  the Protocol itself is the explicit alternative — and would require updating the Protocol, the
  authoritative spec, the recorder, the replay adapter, every fake and every caller in one change.
- The two existing tests that lock `obs == []`
  (`tests/integration/adapters/test_hydro_scraper.py:197,206`) therefore keep passing unchanged via the
  façade; `NO_DATA` vs `MALFORMED_RESPONSE` (D9) is observable only on the new typed method.

`flows/ingest_observations.py`:
- Populate `stations_failed`/`errors` from those outcomes.
- Write the fetch `PipelineHealthRecord` with the **D8**-locked `detail` schema, **immediately after
  fetch reconciliation**, so a later storage/QC failure cannot suppress it. Status: `OK` when nothing
  failed, `WARNING`/`CRITICAL` past the failed-fraction threshold. Threshold value is a T3 design
  detail; "any failure is invisible" is the defect being fixed.
- **Add a dedicated `PipelineCheckType.OBSERVATION_INGEST_FETCH`** (folded major). No suitable member
  exists today (`types/enums.py:169-188`): reusing `BAFU_OBSERVATION_FRESHNESS` would contaminate the
  collector heartbeat the watchdog queries at `ops/watchdog.py:104`, and `OBSERVATION_FRESHNESS` is
  reserved for per-station staleness. Update the authoritative enum docs and tests with it.
- **Correct the visibility claim** (same major): this plan previously said the record makes a mass-429
  run "visible to Flow 4". That is false today — Flow 4 is **deferred** (`docs/v0-scope.md:34`), and the
  watchdog probes only its two dedicated BAFU check types. Actual visibility is `/api/v1/health/detail`
  plus the dashboard page. Wiring a watchdog probe for the new check type would be a deliberate,
  separately-scoped addition; **not claimed here**, and called out in § Residual forks.

**Tests:** a fake adapter returning mixed success/429 proves `stations_failed` is non-zero and the
record carries every D8 detail key; a fully-429 run proves the run does **not** report success; a
storage failure *after* fetch proves the fetch record still persisted.

**Do not leave real sleeps in the existing mock integration tests** (folded minor). The mock tests at
`tests/integration/adapters/test_hydro_scraper.py:169,:274` construct the adapter with default retry
settings and return 500/503 — once T1's pacing is wired in they would perform genuine multi-second
sleeps and slow the gate. Inject the no-op sleeper there too, not only in the unit helper.

### T4 — deconflict the crons

> **A code-only change here is a DEPLOYMENT NO-OP** (folded blocker). `docker-compose.yml:383` supplies
> `SCHEDULE_COLLECT_BAFU_OBSERVATIONS: ${SCHEDULE_COLLECT_BAFU_OBSERVATIONS:-5 * * * *}` to the init
> service, so `os.environ.get()` in `register_deployments.py` **never reaches its Python fallback** on
> the mini. Editing only the Python default would leave the deployed cron at `:05` and the alerts
> firing, while a registration-level unit test went green. The repo already knows this trap:
> `tests/unit/test_compose_schedule_default.py:1-11` exists for exactly this reason and says so —
> "A code-only change is a deployment no-op, so the compose fallback must also be …".

Change **both** defaults to `37 * * * *` per D4:
1. `docker-compose.yml:383` — the compose init fallback. **This is the one that actually deploys.**
2. `cli/register_deployments.py:63` — the Python fallback, kept in sync so a non-compose run agrees.
3. `config/overlays/mac-mini.toml` — its comment advertises the flow as "hourly-at-:05"; correct it to
   `:37`.
4. The `register_deployments.py` comment justifies `:05` by LINDAS's hourly refresh; the binding
   constraint is contention, not freshness. **Note for Plan 176:** that "hourly refresh" claim is itself
   falsified — LINDAS publishes 10-minutely — so do not re-derive a cadence from this comment.

**Tests:** a compose-level assertion mirroring `TestComposeScheduleDefault` (parse `docker-compose.yml`,
assert the init env fallback is `37 * * * *`) — this is the test that would have caught the
no-op; plus a registration-level assertion that **every** minute in the schedule is non-divisible by 5
and disjoint from the ingest and forecast schedules, a lock against someone "tidying" it back onto a
5-minute boundary.

### T5 — docs (mandatory; every code change updates affected docs)

- **New** `docs/decisions/bafu-lindas-rate-limit.md` — the § Evidence measurements as a durable record,
  cross-referenced from `docs/decisions/bafu-lindas-monday-window.md` so future 429s are not
  re-triaged as schema drift. Must include:
  - the shared-NAT hazard: a developer probing LINDAS from the office network spends the mini's budget;
  - the **D5 scaling ceiling** — the `stations_polled + 1 ≤ 3` budget arithmetic and the ~4 s/station
    pacing cost, stated as a hard limit on per-station polling, with the whole-graph query named as the
    known way out. Deferred, not solved.
- `docs/v0-scope.md` — the ~1000-station target needs a pointer to that ceiling; the current scope text
  assumes per-station polling scales, and it does not.
- `docs/standards/orchestration.md` — schedule table: the `*/5`-divisibility constraint, why different
  work pools do not serialise same-endpoint contention, and **that a cron default lives in TWO places
  (`docker-compose.yml` init env + the Python fallback) and compose is the one that deploys**.
- `docs/spec/types-and-protocols.md` — the new `PipelineCheckType.OBSERVATION_INGEST_FETCH` member and
  the HydroScraper typed batch method sitting alongside the unchanged `StationDataSource` façade.
- `docs/touchpoint-maps.md` — the LINDAS subsystem gains "all in-process production callers go through
  the shared limiter" as a must-not-change contract, naming the verified caller set (D6).
- `docs/plans/136-*.md` — **two corrections to a READY plan, stated as corrections, not quiet edits**:
note the `:05`→`:37` move and the 429 fix against its T8
  heartbeat design. (Its `cadence = hourly` finding is also falsified — that correction belongs to
  **Plan 176**, which carries the measurement.)
- **The process-local limitation is stated, not hidden** (D6): pacing binds within one process only;
  cross-process safety currently rests on schedule separation, and the escape hatch if that stops
  holding is a Prefect global concurrency limit behind the same Protocol.
- Plan 176 adds the cadence, publish-lag and storage evidence to the same decision record.

### T6 — live check (marked, not in the default gate)

Extend the existing `tests/integration/live/` LINDAS selection with a test that asserts the limiter
holds under a burst: >3 rapid whole-graph calls all eventually return rows, and the run's total elapsed
time shows pacing occurred. Marked `live` so it stays out of the fast gate; it is the only test that
would catch BAFU changing the ceiling. Because this test is itself a LINDAS caller (D6), it must go
through the limiter too — otherwise the gate becomes a source of the 429s it exists to prevent.


### T7 — sync `AGENTS.md`'s tagging rule with `CLAUDE.md` (separate, one-line doc fix)

Per **D10**: `AGENTS.md:241,252` still mandates tagging every commit; `CLAUDE.md` moved to tag-on-`main`-
after-merge on 2026-08-13 with recorded collision evidence. Update `AGENTS.md` to match. Kept as its own
task and its own commit so a repo-policy correction is never bundled into a rate-limit patch — and so a
future Codex reviewer reading `AGENTS.md` does not re-raise this against the next plan.

```json
{
  "phases": [
    {"id": "phase-0", "tasks": ["0"], "parallel": false},
    {"id": "phase-1", "tasks": ["1"], "parallel": false},
    {"id": "phase-2", "tasks": ["2", "3"], "parallel": true, "depends_on": ["phase-1"]},
    {"id": "phase-3", "tasks": ["4"], "parallel": false},
    {"id": "phase-4", "tasks": ["5", "6"], "parallel": true, "depends_on": ["phase-2", "phase-3"]},
    {"id": "phase-5", "tasks": ["7"], "parallel": false}
  ]
}
```

T0, T4 and T7 are independent of the rest. T4 is the smallest change that stops the alerts, but it is
**not** a same-day stopgap: `docker-compose.yml` pins the cron, so it needs a redeploy like everything
else. Ship the plan as one PR and one redeploy.

## Exit gates

- `uv run pytest` green; new tests proven RED against the pre-fix code (stash-fix/run/restore) — a
  429-retry test that passes against today's `>= 500`-only branch proves nothing.
- `uv run ruff format` + `uv run ruff check --fix` clean; `uv run pyright` ratchet not regressed.
- No new unit test touches the network.
- Patch version bump folded into each code commit; tag on `main` after merge, never on the branch.
- Hold at PR. Human owns merge.

## Host acceptance (requires mini access)

1. Redeploy; confirm `collect-bafu-observations` is registered at `37 * * * *`.
2. Watch one full hour: the collector's heartbeat is `OK`, no DEGRADED alert, and a snapshot is archived.
3. Confirm the watchdog posts the `RECOVERED` message once the CRITICAL streak breaks.
4. Confirm `ingest-observations` now reports a truthful `stations_failed` (0 if the polled count fits
   the budget), and that its fetch health record carries every **D8** `detail` key — `stations_polled`,
   `stations_fetch_failed`, `failure_counts_by_cause`, `failed_station_ids`. `PipelineHealthRecord` has
   no `stations_failed` column, so this is a `detail`-payload check, not a column query.

## Residual forks (owner decides; none block implementation)

1. **Should `OBSERVATION_INGEST_FETCH` get a watchdog probe?** Without one, a mass-429 ingest run is
   visible only via `/api/v1/health/detail` and the dashboard — nobody is paged. Adding a probe means a
   third BAFU-ish block in `ops/watchdog.py` and a fourth Slack alert path. Deferred here deliberately;
   worth deciding once T0 says how often ingest actually fails.
2. **Failed-fraction threshold for WARNING vs CRITICAL** (T3). With 2 stations, one failure is 50% —
   any percentage threshold is noise at this scale. An absolute-count rule may be the honest choice
   until the station count grows.

## Notes

- The watchdog is the hero here, not a defect: a quarantined, evaluation-only collector was the first
  thing to notice a shared-resource problem that the operational ingest had been hiding. Plan 163's
  dead-man's switch and this alert chain are working as designed.
- **Two independent reviews, two different kinds of value.** The 3-round automated loop over-expanded
  and stalled on bugs in its own invention; a single independent Codex pass over the reconstruction
  found a blocker that would have shipped a no-op stopgap. The lesson in
  `feedback_independent_review_beats_automated_loop` held again — and note that *both* reviews flagged
  `record_fixtures.py`, which was wrongly rejected the first time. A finding dismissed on a weak
  verification deserves re-checking when a second reviewer raises it independently.
- The 429 ceiling is undocumented by BAFU. If T6 ever shows the ceiling moved, the escalation contact
  is already on file: `abfragezentrale@bafu.admin.ch` (per `bafu-lindas-monday-window.md`).
