---
status: DRAFT
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

**DRAFT** (2026-08-18). Resolves **Plan 175 D5**, deferred there by owner decision and documented in
`docs/decisions/bafu-lindas-rate-limit.md` rather than fixed.

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
  mapper over shared query/parse code. That is a weaker structural guarantee. Say so if you think it is
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
| ~1000 (`docs/v0-scope.md` target) | ~67 min | structurally impossible |

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

Observation ingest costs **one LINDAS request per run, independent of station count**, with per-station
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

**D2 — share the QUERY and PARSE, not the adapter.** Plan 136 DC-2 forbids `BafuObservationAdapter`
constructing `RawObservation`/`StationId`, and that boundary is load-bearing: it is what keeps the
evaluation-only archive from silently becoming an operational input. **Locked:** extract the SPARQL text
and the subject-grouping parse into a shared module returning gauge-keyed rows, then keep **two thin
mappers** — the collector's to `BafuObservationRow` (quarantined, gauge-keyed) and ingest's to
`RawObservation` (station-keyed, requires the `StationConfig` mapping). Neither adapter imports the
other. The quarantine boundary moves from "separate adapter" to "separate mapper", and the plan should
say so explicitly rather than let it erode by accident.

**D3 — `fetch_observations` keeps its Protocol signature**, `since` included. It is `StationDataSource`
(`protocols/adapters.py:66-72`, authoritative spec `docs/spec/types-and-protocols.md`), implemented by
the replay adapter, the fakes and `tools/record_fixtures.py`. `since` stays accepted and unread, exactly
as today — narrowing the Protocol is a separate change with a much wider blast radius, and this plan
does not need it.

**D4 — per-station outcomes survive unchanged.** Plan 175's `StationFetchOutcome` taxonomy
(`RATE_LIMITED`, `HTTP_STATUS_ERROR`, `TRANSPORT_ERROR`, `MALFORMED_RESPONSE`, `NO_DATA`) and the
`OBSERVATION_INGEST_FETCH` health record are the contract, not an implementation detail. With one
request, transport/HTTP causes become **whole-batch** rather than per-station — that is a real semantic
change and must be stated: a 429 now fails *every* station at once instead of some. `NO_DATA` and
`MALFORMED_RESPONSE` stay genuinely per-station.

## Open questions for the owner

1. **Is the whole-batch failure mode acceptable?** Today a transport failure can drop 3 stations and
   keep 166; afterwards it drops all or none. Arguably better (no more silent partial coverage) but it
   is a change in kind, and at 2 stations it is indistinguishable.
2. **Should the per-station path be deleted or retained as a fallback?** Retaining it means two code
   paths and two sets of failure semantics; deleting it means a LINDAS whole-graph outage has no
   per-station fallback. My inclination is **delete** — the fallback would share the same endpoint and
   the same outage, so it buys nothing real.

## Tasks

### T1 — extract the shared query/parse layer (D2)

Move the SPARQL text and subject-grouping parse into a shared module returning gauge-keyed rows. The
collector's existing behaviour must be **byte-identical afterwards** — its unit tests are the guard, and
they should pass untouched.

### T2 — whole-graph batch fetch in `HydroScraperAdapter` (D1, D4)

Replace the per-station loop in `fetch_observations_batch` with one fetch + local filter. Build the
`(gauge_code, lindas_kind) -> StationConfig` index from the passed configs. Preserve the D4 taxonomy,
mapping whole-batch failures to every requested station.

**Tests:** one request for N stations (assert the transport was called exactly once, for N > 3 — this is
the whole point and must be locked); a station absent from the graph yields `NO_DATA`; a station missing
one parameter matches today's behaviour; a 429 marks all stations `RATE_LIMITED`; existing
`test_hydro_scraper.py` expectations reconciled deliberately, not broken.

### T3 — docs

`docs/decisions/bafu-lindas-rate-limit.md` records D5 as **resolved**, with the ceiling table above and
the date. `docs/v0-scope.md` drops the pointer that per-station polling caps the station count.
`docs/touchpoint-maps.md` gains the shared query/parse layer and the two-mapper quarantine boundary.

### T4 — re-measure the ceiling

Re-run the arithmetic that motivated this plan and record the result: one request per run means the
ingest cost is now flat in station count. Verify on the mini by onboarding into a scratch config (not
the live station set) or by asserting the request count in tests — **do not** onboard 169 basins to the
live deployment as a test.

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

- This plan is short because three measurements collapsed it. Had `since` been meaningful, or had the
  whole-graph query returned less than the per-station one, it would have been a much larger piece of
  work. Both were checked rather than assumed — the per-station/whole-graph comparison above took one
  request each.
- The quarantine boundary is the thing to watch in review. Plan 136 kept the archive honest by giving it
  its own adapter; D2 keeps it honest with its own mapper. That is a weaker structural guarantee and
  deserves a reviewer's attention.
