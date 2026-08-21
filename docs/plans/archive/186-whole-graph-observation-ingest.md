---
status: COMPLETE
created: 2026-08-18
plan: 186
title: Whole-graph observation ingest — one request for the network, not one per station
scope: Replace the per-station LINDAS fan-out in the operational observation ingest with a single whole-graph fetch plus a local filter to onboarded stations. Resolves Plan 175 D5, whose scaling ceiling is currently documented but unsolved — LINDAS's burst-3 rate limit makes one-request-per-station structurally impossible past a handful of stations, and `config.toml` already lists 169 basins against the 2 the mini polls. Swiss/BAFU only.
depends_on: [175, 176]
blocks: []
supersedes: []
---

# Plan 186 — Whole-graph observation ingest

## Status


**COMPLETE 2026-08-21.** Merged as **PR #188 (`4ae7cf8`, 2026-08-18)** with fixer round `63d936d`;
deployed in image `0.1.775`. **Resolves Plan 175 D5.**

`HydroScraperAdapter` now issues one whole-graph SPARQL request per call and filters to onboarded
stations locally (`adapters/hydro_scraper.py:106`, `build_whole_graph_query`). The archive collector and
its quarantine were not touched, as the plan required — only the query layer is shared.

**Deployed, not yet exercised at scale.** The mac-mini has **2 onboarded stations**, so the whole-graph
fetch currently filters the network down to 2. That is this plan's success condition rather than a
defect: the burst-3 structural ceiling is gone, and adding stations is now a config/onboarding task, not
an adapter one.

*The original READY narrative is retained below as the design record.*

**READY** (owner flip 2026-08-18). Resolves **Plan 175 D5**, deferred there by owner decision and documented in
`docs/decisions/bafu-lindas-rate-limit.md` rather than fixed.

**`/plan` ESCALATED (stalled, 3 rounds, 3 blockers + 3 majors residual); this is the reconstruction.**
Unlike the Plan 175 run, the loop did **not** invent machinery — the section structure came back
unchanged and every finding was a correction, with the headline blocker asking the plan to share *less*
code. The anti-over-engineering brief below appears to have worked. But it stalled with the findings
**unresolved** while the doc grew 218 → 411 lines, so the plan was rebuilt from the pre-review baseline
with each finding verified against the code first, per `feedback_plan_workflow_over_expands`.

Folded after verification: the grouping code is **not** error-neutral, so D2 now shares strictly less
(**blocker**); "exactly one transport request" contradicted the retained retry contract (**blocker**);
mixed-batch outcome precedence was undefined (**major**); the test matrix let a broadcast-failure mutant
through (**major**); two documents assert an incrementality that was never implemented (**major**);
`_QUERY_LIMIT` is read directly by a collector test (**minor**); `~1000 stations` is an architectural
ceiling, not the v0 target — v0 is ~170 (**minor**); tasks lacked the per-task verification commands
`docs/workflow.md:22` requires (**minor**).

**One finding was rejected as unsubstantiated:** that `tests/fixtures/reference/README.md` describes the
per-station fan-out and would contradict this plan. Its LINDAS section says LINDAS is real-time only,
which *agrees* with D3; no one-subject-per-request claim exists there. The cited line numbers pointed at
unrelated text. Not folded.

The three load-bearing facts below were **measured on 2026-08-18**, not assumed. This plan is small
because those measurements removed most of the complexity it looked like it would need.

## Review guidance — READ BEFORE REVIEWING THIS PLAN

**Do not over-engineer this plan. Findings that ADD scope are not wanted; findings that CUT it are.**

This is written into the plan doc deliberately, because workflow arguments are silently discarded
(`feedback_workflow_args_silently_ignored`) — the doc is the only channel that reaches a reviewer.

The instruction is not generic caution. Reviewing Plan 175, an automated planner/reviewer loop
**escalated by inflating it 306 -> 838 lines**: it invented a Prefect global-concurrency limiter, then
spent two further rounds finding bugs in its own invention, and its own proportionality reviewers
concluded the addition "is not required to fix any of the defects". The plan had to be reconstructed
from the pre-review baseline. Every subsequent round on Plans 175 and 176 that produced *real* value did
so by finding something **wrong**, never by proposing something **more**.

Concretely, for this plan:

- **"You could also build X" is not a finding.** Only report what is factually wrong, contradictory, or
  unimplementable as written.
- **No new subsystems, abstractions, protocols, frameworks, or infrastructure.** If a decision here can
  be made simpler while staying correct, say so — simplification findings are welcome and valuable.
- **Scale to reality: the live deployment polls 2 stations.** This plan removes a ceiling; it is not a
  platform.
- **Attack the evidence, not the ambition.** The three measurements in § Evidence are what make this
  plan small. If any of them is wrong — particularly the claim that `since` is never read, or that the
  whole-graph result is identical to the per-station one — that is the most valuable finding available,
  and it would make the plan bigger for a *reason*.
- **The quarantine boundary (D2) is the one design risk worth real scrutiny.** Plan 136 kept the
  evaluation-only archive honest by giving it a separate adapter; D2 downgrades that to a separate
  mapper over a shared **query builder and key helper only** — grouping, validation, parsing and mapping
  all stay adapter-local. That is still a weaker structural guarantee than a separate adapter. Say so if
  you think it is
  too weak — that is a correctness argument, not scope creep.
- An honest **"this is proportionate, no blockers"** is a valid and expected outcome.

## Problem — a ceiling that is armed, not hypothetical

`HydroScraperAdapter` issues **one SPARQL request per station**. LINDAS allows a **burst of 3** with
~1 slot refilling per 3–4 s (`docs/decisions/bafu-lindas-rate-limit.md`). Plan 175 made this survivable
— the shared limiter paces requests and 429s retry rather than fail — and made it *visible*, via honest
`stations_failed` reporting and the `OBSERVATION_INGEST_FETCH` health record. **It did not make it
scale.**

At D1's pacing, N stations cost roughly `N x 4 s`:

| Stations | Time for one ingest pass | Against a 5-min cadence |
|---|---|---|
| 2 (today's mini) | ~8 s | fine |
| 10 | ~40 s | fine |
| 60 | ~4 min | at the edge |
| **169** (`config.toml`'s basin list) | **~11 min** | **overlapping runs, permanently behind** |
| ~1000 (`docs/v0-scope.md:9` *architectural ceiling* across deployments, not the v0 target) | ~67 min | structurally impossible |

**This is armed, not theoretical.** The gap between 169 configured basins and 2 polled stations closes
the moment someone runs onboarding against the committed config — an ordinary action, not a project.
T0 (2026-08-18) confirmed the live count is 2, so the ceiling is latent *today*; nothing about it is
guarded.

## Evidence (measured 2026-08-18, dev machine)

**1. The whole-graph query returns byte-identical data to the per-station query.** Both filter the same
predicate set (`discharge`, `waterLevel`, `waterTemperature`, `measurementTime`); the per-station form
merely `BIND`s one subject. Compared live for two real gauges:

```
station 2009  per-station : discharge 239.985 | measurementTime 2026-08-18T15:10:00+01:00
                            waterLevel 375.826 | waterTemperature 9.740
              whole-graph : IDENTICAL

station 2011  per-station : discharge 156.439 | measurementTime 2026-08-18T15:10:00+01:00
                            waterLevel 484.054            (no waterTemperature)
              whole-graph : IDENTICAL
```

Station 2011 carries no `waterTemperature` and the whole-graph result omits it too — so the partial-data
shape that drives Plan 175's `NO_DATA` cause is preserved, not flattened.

**2. One whole-graph request covers the entire network**: 199 river + 34 lake = **233 gauges**, ~0.1 s,
~234 KB. That is *one* request regardless of station count — the ceiling disappears rather than moving.

**3. `since` is vestigial on this path.** `fetch_observations(station_configs, since)` accepts it, but
`since` appears in `adapters/hydro_scraper.py` **only in signatures** — never read. It cannot be
otherwise: LINDAS serves current values only, so there is no history to filter. De-duplication is the
store's `ON CONFLICT DO UPDATE` upsert (`store/observation_store.py:44,97`), which makes re-fetching an
unchanged value harmless.

This is what keeps the plan small: there is no per-station time-window semantics to preserve.

## Goal

Observation ingest costs **one logical batch fetch per run, independent of station count** — with retry
attempts constant in station count too, since the limiter still spends `max_retries + 1` on a persistent
429 (that is the retry contract, not a violation of this goal). Per-station
outcomes and health reporting unchanged from Plan 175.

## Non-goals

- Changing what ingest *stores*, its QC path, or `RawObservation`'s shape.
- Touching the Plan 136 collector or its quarantined archive. This plan reuses its *query*, never its
  adapter — see D2.
- Removing the shared rate limiter. One request still goes through it; the limiter stops being the
  bottleneck rather than stops being needed.
- Onboarding the 169 basins. This plan makes that *possible*; whether to do it is separate.

## Decisions

**D1 — one fetch per run, filtered locally to onboarded stations.** Fetch the whole graph once, index it
by `(gauge_code, lindas_kind)`, then map each onboarded `StationConfig` to its entry. A station absent
from the graph yields the same `NO_DATA` outcome it does today; a station present but missing a
parameter behaves exactly as the per-station path does (evidence 1).

**D2 — share the QUERY, not the parse. The grouping code is NOT error-neutral** (folded blocker; an
earlier draft of this decision was wrong and said the opposite).

Plan 136 DC-2 forbids `BafuObservationAdapter` constructing `RawObservation`/`StationId`, and that
boundary is load-bearing: it is what keeps the evaluation-only archive from silently becoming an
operational input.

The first draft proposed sharing the "group triples by subject" loop, calling it a pure
`setdefault`/`append`. **It is not.** `adapters/bafu_observation.py:203-209` builds a pydantic
`_SparqlTriple` inside that loop, dereferencing `raw["subject"]["value"]` and friends; a malformed
binding therefore *raises during grouping* and is converted to a collector-wide `AdapterError`
(`:166`). Reusing it operationally would make **one bad binding anywhere fail every station** —
destroying exactly the subject-local isolation D4 requires.

**Locked, deliberately sharing less than the first draft:**
- **Shared:** the SPARQL query text/constants and a **non-raising** subject-URI → `(gauge_code,
  lindas_kind)` key helper that returns `None` rather than raising on an unrecognised subject.
- **NOT shared:** grouping, validation and failure policy. The collector keeps its current validated
  `_SparqlTriple` grouping and its fail-the-whole-fetch semantics (correct for an archive snapshot).
  Ingest groups raw bindings by *usable subject only*, then validates predicate/object fields inside
  the extraction loop for each **requested** subject — so a malformed binding for a gauge we do not
  poll is simply never looked at.

The quarantine boundary thus moves from "separate adapter" to "separate parse + separate mapper", which
is a weaker structural guarantee than Plan 136 had. Stated plainly so a reviewer can weigh it rather
than discover it.

**D3 — `fetch_observations` keeps its Protocol signature**, `since` included. It is `StationDataSource`
(`protocols/adapters.py:66-72`, authoritative spec `docs/spec/types-and-protocols.md`), implemented by
the replay adapter, the fakes and `tools/record_fixtures.py`. `since` stays accepted and unread, exactly
as today — narrowing the Protocol is a separate change with a much wider blast radius, and this plan
does not need it.

**D4 — per-station outcomes survive, and the precedence order is defined** (folded major — the first
draft left mixed batches ambiguous).

Plan 175's `StationFetchOutcome` taxonomy (`RATE_LIMITED`, `HTTP_STATUS_ERROR`, `TRANSPORT_ERROR`,
`MALFORMED_RESPONSE`, `NO_DATA`) and the `OBSERVATION_INGEST_FETCH` health record are the contract, not
an implementation detail. **Locked precedence, evaluated in this order:**

1. **Weather configs are skipped entirely** — no outcome, no request. Today's behaviour
   (`hydro_scraper.py:105`), locked by an existing no-request test.
2. **A code that matches no gauge in the response yields `NO_DATA`** — see Q2. This *replaces* today's
   pre-request `MALFORMED_RESPONSE` from the injection guard (`hydro_scraper.py:126`), which no longer
   fires in this path because ingest stops interpolating the code into SPARQL. The guard survives in
   `_build_sparql_query` for `verify_gauge_reachable`.
3. **Only the remaining valid river/lake configs enter the index** and are eligible for whole-response
   faults.
4. **If no eligible config remains, make NO request at all.**

**What genuinely changes:** transport/HTTP causes become **whole-batch**. A 429 or 503 now fails every
*eligible* station at once rather than some — a real change in kind, worth stating rather than
discovering. `NO_DATA` and `MALFORMED_RESPONSE` stay strictly **subject-local**: a nonnumeric value or
unparseable timestamp for one gauge must not touch another. Exactly one outcome per eligible config,
always.

## Owner decisions (RESOLVED 2026-08-18)

**Q1 — the whole-batch failure mode is ACCEPTED.** A transport or HTTP fault now fails every eligible
station at once rather than some. Ratifies a consequence D4 already documents; arguably an improvement,
since partial coverage can no longer pass silently. At 2 stations the two behaviours are
indistinguishable anyway.

**Q2 — the per-station path is DELETED from ingest — but NOT deleted outright.** Checking the callers
before folding this changed its shape, and two consequences follow that the first draft would have got
wrong:

- **`verify_gauge_reachable` keeps its single-gauge query.** `_build_sparql_query` has two callers:
  `hydro_scraper.py:127` (the ingest fetch this plan replaces) and `:300` (`verify_gauge_reachable`, the
  onboarding-time existence probe). Probing one gauge is a genuinely different operation from bulk
  ingest — it runs once per station at onboarding, not every 5 minutes — so it stays per-station and
  keeps the builder alive. **The decision is "delete from the ingest path", not "delete the builder".**
- **D4's precedence step 2 changes, and this is the part worth review.** With ingest no longer
  interpolating `site_code` into SPARQL, **there is no injection surface left in that path** — the code
  becomes a lookup key into the whole-graph index. So the `_SITE_CODE_RE` guard no longer fires during
  ingest, and a malformed code becomes a **lookup miss ⇒ `NO_DATA`**, not the pre-request
  `MALFORMED_RESPONSE` it produces today. The guard stays where it is still needed:
  `_build_sparql_query`, i.e. `verify_gauge_reachable`.

  Deliberate and stated rather than discovered: `NO_DATA` is the honest cause here — the gauge is not in
  the response — where `MALFORMED_RESPONSE` would be describing a request that is no longer made.

**No fallback is retained.** A per-station fallback would hit the same endpoint and share the same
outage, so it buys nothing real while doubling the failure semantics.

## Tasks

### T1 — extract the shared query layer (D2)

**Scope.** Move the SPARQL query text/constants and add a **non-raising** subject-URI → `(gauge_code,
lindas_kind)` key helper into a shared module. Grouping, validation and failure policy stay where they
are — see D2. `_QUERY_LIMIT` must remain importable from `adapters/bafu_observation.py` (re-export if
moved): `tests/unit/adapters/test_bafu_observation.py:212` reads it directly, and the exit gate below
requires the collector's tests to pass untouched.

**Verification.** `uv run pytest tests/unit/adapters/test_bafu_observation.py tests/unit/flows/test_collect_bafu_observations.py -q`

### T2 — whole-graph batch fetch in `HydroScraperAdapter` (D1, D4)

**Scope.** Replace the per-station loop in `fetch_observations_batch` with one fetch plus a local
filter, building the `(gauge_code, lindas_kind) → StationConfig` index from the passed configs. Apply
D4's precedence exactly. Out of scope: `fetch_observations`' signature (D3), the QC path, the store.

**The request-count invariant, stated correctly** (folded blocker — the first draft said "exactly one
transport request", which contradicts the retained limiter): the guarantee is **one logical batch fetch
per run**, and **retry attempts constant in station count**. Persistent 429 still costs
`max_retries + 1` HTTP attempts (`hydro_scraper.py:79`, `lindas_rate_limiter.py:263`) — that is the
retry contract working, not a violation. Lock the invariant with `max_retries=0` for the count test, and
separately assert the real retry path still makes `max_retries + 1` attempts.

**Tests — the matrix, not one example per cause** (folded major: a mutant that broadcasts a
subject-local failure to every station, or emits a single outcome on 503, passes a thinner suite):
- **Flat in station count:** N > 3 stations ⇒ exactly one logical fetch (`max_retries=0`). This is the
  entire point of the plan and must be locked.
- **Whole-batch causes, multi-station:** parameterised over 403, exhausted 503, and transport
  exhaustion — every eligible station gets the same cause, and the count of outcomes equals the count of
  eligible configs.
- **Subject-local causes, multi-subject:** a malformed binding and a nonnumeric value for gauge A leave
  gauge B **successful**. This is the isolation D4 promises and the mutant most likely to survive.
- **`NO_DATA` shapes:** a subject absent from the graph; a subject present with `measurementTime` only.
- **Tolerated:** an onboarded subject carrying an unrecognised predicate alongside a valid parameter
  still succeeds.
- **Precedence (D4/Q2):** a mixed batch of weather + unmatched-code + valid configs under a 429 —
  weather gets no outcome, valid ones get `RATE_LIMITED`. An **all-weather** batch makes **zero**
  requests. Note an unmatched code no longer short-circuits pre-request: it is a lookup miss in the
  response, so a batch of only-unmatched codes still makes **one** fetch and returns `NO_DATA` each.
  Lock that explicitly — it is the behaviour change Q2 introduces, and the easiest thing to regress back
  to `MALFORMED_RESPONSE` by reflex.
- **The guard survives where it is still needed:** `verify_gauge_reachable` with a code failing
  `_SITE_CODE_RE` still raises/returns as today — deleting the per-station *ingest* path must not
  weaken the onboarding probe's injection guard.

**Verification.** `uv run pytest tests/unit/adapters/test_hydro_scraper.py tests/integration/adapters/test_hydro_scraper.py tests/unit/flows/test_ingest_observations_fetch_health.py -q`

### T3 — docs, including two that currently assert the opposite

**Scope.** Beyond the obvious updates, these actively contradict the implementation and were missed by
the first draft:
- **`docs/design/v0-flow2-observation-pipeline.md`** — states ingest is *incremental*: "only fetch since
  last-seen timestamp per station" and "Pass `since` timestamps to the SPARQL query (or filter
  client-side)". That incrementality was **never implemented** (D3's measurement), and is doubly wrong
  after this plan. Correct the whole LINDAS section rather than the two lines.
- **`tests/integration/live/test_lindas_live_schema.py:171,294`** — comments reasoning about "7 real
  requests" of rate-limit budget. That test calls `HydroScraperAdapter.fetch_observations` (`:190,196`),
  so after this plan it spends **one**. A pleasant side effect worth recording, and wrong if left.
- `docs/decisions/bafu-lindas-rate-limit.md` records D5 **resolved**, with the ceiling table and date.
- `docs/v0-scope.md:9` — its Plan 175 D5 caveat (per-station polling does not scale) can now point at
  the resolution instead of the gap.
- `docs/touchpoint-maps.md` — the shared query layer and the two-parse/two-mapper quarantine boundary.

**Verification.** `uv run pytest tests/integration/live/test_lindas_live_schema.py -q --collect-only` (the
comments are prose, but the file must still import and collect) plus `uv run ruff format --check docs` is
not applicable — review the doc diff by eye against D2/D3/D4.

### T4 — re-measure the ceiling

**Scope.** Record that ingest cost is now flat in station count, using the test from T2 rather than a
live experiment. **Do not** onboard 169 basins to the live deployment to prove it.

**Verification.** `uv run pytest tests/unit/adapters/test_hydro_scraper.py -k "flat_in_station_count" -q`

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["1"], "parallel": false},
    {"id": "phase-2", "tasks": ["2"], "parallel": false, "depends_on": ["phase-1"]},
    {"id": "phase-3", "tasks": ["3", "4"], "parallel": true, "depends_on": ["phase-2"]}
  ]
}
```

## Exit gates

- `uv run pytest` green; every new lock proven RED against the specific mutant it targets.
- The Plan 136 collector's tests pass **unchanged** — the proof T1 preserved its behaviour.
- `uv run ruff format` + `check` clean; `uv run pyright` ratchet not regressed.
- Patch version bump per code commit; tag on `main` after merge, never on a branch.
- Hold at PR. Human owns merge.

## Notes

- The review's most useful finding inverted one of mine: I asserted the grouping code was pure and
  shareable, having read the call site rather than the loop body. It builds a pydantic model per binding
  and raises. Checking the four lines would have cost less than the round did.
- This plan is short because three measurements collapsed it. Had `since` been meaningful, or had the
  whole-graph query returned less than the per-station one, it would have been a much larger piece of
  work. Both were checked rather than assumed — the per-station/whole-graph comparison above took one
  request each.
- The quarantine boundary is the thing to watch in review. Plan 136 kept the archive honest by giving it
  its own adapter; D2 keeps it honest with its own mapper. That is a weaker structural guarantee and
  deserves a reviewer's attention.
