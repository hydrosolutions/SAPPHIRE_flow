---
status: DRAFT
created: 2026-08-17
plan: 176
title: LINDAS archive completeness — capture the 10-minute grid we have been sampling hourly
scope: The BAFU LINDAS observation archive collector polls hourly against a feed that publishes every 10 minutes, so it captures ~1 observation in 6 and the rest are lost permanently (LINDAS serves no history). Fix the cadence, the dedup key, the archive encoding, the staleness thresholds derived from the old cadence, and the worker-pool starvation that would defeat all of it. Swiss/BAFU only. Split out of Plan 175.
depends_on: [175]
blocks: []
supersedes: []
---

# Plan 176 — LINDAS archive completeness

## Status

**DRAFT** (2026-08-17). Split out of Plan 175 after review escalation — see § Why this is its own plan.
All measurements below are live, from the development machine on 2026-08-17.

**The execution-isolation question is CLOSED** (owner, 2026-08-17): the collector moves onto the
existing `ingest` pool — see § Execution isolation. The plan is unblocked; it still needs its own
review round before READY, since three of its decisions reverse Plan 136 properties.

## Why this is its own plan

Plan 175 fixes a LINDAS **rate-limit** incident (a 3-request burst ceiling making a healthy collector
look dead). Mid-investigation, checking the owner's "collect all observations, we'll need them soon"
goal falsified Plan 136's locked `cadence = hourly` finding, and the completeness work was folded into
175. Independent review then returned a blocker in that folded half **twice in two rounds**, while 175's
rate-limit half had been stable and APPROVED since its second round.

The second blocker is why the split is right rather than merely tidy: it is an **orchestration** problem
(work-pool starvation), not a cron or dedup problem. It changes worker topology in `docker-compose.yml`,
which is a different class of change — and a different review surface — from a retry-logic patch. Rather
than let a four-round plan keep widening, the completeness work gets its own plan, its own decisions and
its own review.

Nothing measured is lost in the handoff; all evidence carries over below.

## Problem

**LINDAS publishes on a 10-minute grid. The collector polls hourly. ~83% of published observations are
discarded, permanently** — LINDAS serves real-time only, so an uncollected slot is unrecoverable
(`project_bafu_lindas_realtime_only`; Plan 136 § Context).

This matters now because the owner has made the archive load-bearing: *"collect all observations
available on LINDAS for now. We'll need them soon … I want to save the data."* The collector is no longer
an evaluation-only nice-to-have whose failure is merely noisy.

Four defects stand between the current state and that goal, and the fourth defeats the other three:

1. **Cadence.** `5 * * * *` samples a 10-minute grid once an hour.
2. **Dedup key.** `cycle_at` is derived from the **clock**, truncated to the hour
   (`collect_bafu_observations.py:330`), and `_already_archived` (`:218-221`) short-circuits on it
   **before the fetch** (`:346-356`). Any faster schedule silently no-ops: every run in the same clock
   bucket resolves to one `cycle_at`, logs `dedup_skip`, and archives nothing new — six green runs an
   hour, one snapshot.
3. **Staleness thresholds.** Both 3-hour constants are justified by "three missed *hourly* cycles"
   (`collect_bafu_observations.py:66-73`, `ops/watchdog.py:129`). At a 10-minute cadence that is ~18
   missed snapshots before anything alarms.
4. **Worker starvation — the blocker.** The collector's `DeploymentSpec`
   (`cli/register_deployments.py:145-151`) sets no `work_pool_name`, so it lands on
   `WORK_POOL = "default"` (`:20`). **Plan 098 measured 25–60 min lateness on that pool** during
   forecast-cycle windows (`docs/plans/archive/098-guaranteed-cadence-observation-ingest.md:141-144`) —
   runs complete fast once they start; they simply are not picked up. Plan 098 created the `ingest` pool
   precisely to escape this. A cron interval is therefore **not** a poll-interval guarantee, and
   `concurrency_limit=1` does not help (it prevents same-deployment overlap only, and enqueues rather
   than drops). A starved collector polling at `:01` and then `:45` loses every slot in between.

## Evidence (live, 2026-08-17)

### The grid is 10-minutely (falsifies Plan 136 §81-86)

Four whole-graph snapshots 6 min apart, tracking the modal `measurement_time` across 233 gauges (216 move
together as one bulk):

```
12:12:35Z  bulk = 11:50     modal_minutes=[(50, 216), (0, 12), (20, 2), (30, 2)]
12:18:35Z  bulk = 12:00     modal_minutes=[( 0, 228), (20,  2), (40, 2), (35, 1)]
12:24:35Z  bulk = 12:10     modal_minutes=[(10, 216), (0, 12), (20, 2), (40, 2)]
12:30:35Z  bulk = 12:10     (12:20 not yet published)
```

The bulk advances **exactly 10 minutes per slot**; an hourly grid would hold it at `:00` all hour. Plan
136 probed once, saw the bulk at `15:00`, and read that as an hourly grid — but the bulk always sits on
the most recently *published* slot, which is `:00` only during the ~15 min after `:00` lands.

### Publish lag — measured at 1-minute resolution, and deliberately under-measured

```
slot 13:00  first seen 13:21:38Z   lag <= 21.6 min   <-- probe-START artifact, NOT a measurement
slot 13:10  first seen 13:24:08Z   lag <= 14.1 min
slot 13:20  first seen 13:34:59Z   lag <= 15.0 min
gaps between consecutive first-sightings: 2.5 min, 10.8 min
```

**Two of these numbers are traps.** The probe started at 13:21:38, so `13:00` was already visible and its
true first-sighting is unobserved — the 21.6 min figure is an artifact, not a lag excursion, and the
2.5 min gap is the same artifact. An earlier draft of Plan 175 cited 21.6 min as evidence of a wide lag
band; **that was wrong and is retracted here.**

The only clean data: lag **≤14.1** and **≤15.0** min, one clean publish gap of **10.8 min** — mutually
consistent, implying jitter of roughly **a minute**. BAFU's publishing looks steady, not erratic.

### Snapshot size and storage

| Per snapshot | Size |
|---|---|
| raw SPARQL JSON | **234.0 KB** |
| raw JSON, gzipped | **5.6 KB** (**41.8×**) |
| parsed parquet | 5.2 KB |

| Cadence | plain | gzipped raw |
|---|---|---|
| hourly (today) | 2.15 GB/yr | 0.10 GB/yr |
| every 10 min | 12.87 GB/yr | **0.58 GB/yr** |

Capturing 6× the data with gzip costs **~4× less disk than today's hourly plain archive.**

## Goal

The archive contains **every 10-minute slot LINDAS publishes**, proven by the archive's own contents
rather than by run states — and without a 6× disk bill.

Stated precisely, because the difference matters: this plan delivers **best-effort prevention plus
reliable detection**, not an absolute guarantee. Prevention is over-polling (D1) on an uncontended
worker (§ Execution isolation). Detection is a **gap audit over the archive** — a first-class
deliverable (**T8**), not a nice-to-have, because Plan 098's residual mechanism (b) means no schedule or
worker topology can *guarantee* pickup. A missed slot must be **known**, not silent. That is the same
failure that hid the original incident: the collector alerted on the current run and never on gaps.

## Non-goals

- The LINDAS rate-limit work (429 retry, the shared limiter, ingest honesty). That is **Plan 175**, which
  this plan depends on: the limiter it introduces is what makes over-polling safe.
- Landing archived observations in the operational DB. The archive stays quarantined (keyed by
  `(gauge_code, lindas_kind)`, no `station_id`). Whether that changes is a separate decision.
- Backfilling what has already been lost. It is unrecoverable; this plan stops the bleeding only.
- Per-station operational polling / whole-graph ingest (Plan 175 D5) — still deferred.

## Execution isolation — RESOLVED (owner, 2026-08-17)

**Decided: option 1 — route the collector onto the existing `ingest` pool**
(`prefect-worker-ingest`), adding `bafu_observation_archive:/data/bafu_observations:rw` to that
worker's volumes (it currently mounts only `config.toml:ro`). The owner confirmed the mini's storage
pressure is resolved, so resources were no longer the deciding factor; option 1 was chosen because the
`ingest` worker is nearly idle and already provides everything a new worker would.

**Why this is sufficient, from Plan 098's own mechanism analysis** (`archive/098-…md:150-212`):

- **(a) worker-side poll-cycle starvation** — the `ProcessWorker` poll loop is a single async event
  loop; saturated by *managing* a CPU-pegging forecast-cycle subprocess it skips polls (25–60 min ≈
  150–360 missed 10 s polls). Plan 098 judged this dominant. **A dedicated worker with its own event
  loop resolves it outright — and `prefect-worker-ingest` already is one.**
- **(b) server-side dequeue latency** — mitigated by having a separate *pool*, which option 1 also
  provides, but Plan 098 is explicit that it "does NOT help if the slowness is a global server
  throttle."

A brand-new fourth worker would therefore have bought isolation only from `ingest-observations` itself —
12 runs/hour of a 2-station poll, seconds each — not from the forecast-cycle load that caused the
starvation. That delta did not justify a fourth container.

**Load check.** The collector adds ~18 runs/hour to the ingest worker's 12, so ~30 short subprocess
spawns/hour on a 512 m worker. These are *short, network-bound* runs — categorically unlike the
long CPU-pegging forecast-cycle subprocess whose management starved the `default` worker's event loop.
Risk is low but it is the same mechanism, so host acceptance measures it rather than assuming.

**Residual risk (b) is real and is why detection is a first-class deliverable — see § Goal.** No worker
topology can guarantee against a global server throttle, so this plan must be able to *tell us* when a
slot was missed, not merely try not to miss one.

### Validation (folds into Plan 175's T0 — needs mini access, gates nothing)

Plan 098's Phase 0 root-cause test is recorded as **partially done**: mechanism (a) vs (b) was never
fully separated. The experiment that settles it has, however, **already been running for months** —
`ingest-observations` has been on the dedicated `ingest` worker since Plan 098 shipped. So the question
is answered by reading a result, not by running a new test:

> Has `ingest-observations` held its 5-minute cadence during forecast-cycle windows since Plan 098?

Measure `Scheduled`→`Running` deltas (`prefect flow-run ls`) for `ingest-observations` across a
forecast-cycle window. **Cadence held** ⇒ (a) was dominant, a dedicated worker works, and this plan's
prevention story is sound. **Still late** ⇒ (b) is live, no worker topology fixes it, and this plan's
guarantee must be restated as best-effort prevention plus reliable detection.

## Decisions

**D1 — over-poll; do not match the poll rate to the grid.** A grid-matched schedule with a fixed offset
(e.g. `7,17,27,37,47,57`) cannot deliver completeness: the lag is a band, not a constant, so a late slot
and an early next slot can both become visible **between** two polls; the poll sees only the newer, and
the older is gone. **Locked: max inter-poll gap ≤4 min** (a ~2.7× margin on the only clean 10.8 min
publish gap), **every scheduled minute non-divisible by 5** so the collector never shares a minute with
`ingest-observations` at `*/5`, and the gap bound must hold **cyclically** (across the `:59 → :01` wrap),
not just within the hour. Properties are locked, **not a literal cron string**; a test asserts them. A
satisfying example: `1,4,7,11,14,17,21,24,27,31,34,37,41,44,47,51,54,57` — cyclic gaps 3–4 min, none
divisible by 5. Also lock a **≥3-minute minimum** gap so a valid-but-clustered list cannot interact badly
with Plan 175's 120 s total retry deadline.

*Honest justification.* The clean evidence suggests jitter of ~1 minute, i.e. small. Over-polling is
chosen not because BAFU is erratic but because **two clean data points cannot bound jitter**, and the
failure is asymmetric: one unnoticed excursion destroys a slot permanently and undetectably, while the
insurance costs a rounding error against a measured ~45 req/min capacity. This makes the jitter question
moot rather than answering it from thin evidence. Load: one request per run, ~20/hour.

**D2 — `cycle_at` is derived from the DATA, not the clock.** Once poll rate is decoupled from the grid, a
clock-derived key is actively wrong: polls at `12:22` and `12:24` both bucket to `12:20`, so the second
dedup-skips even if it carried a genuinely new slot. **Locked: `cycle_at` = the response's newest
`measurement_time`, truncated to the 10-minute grid.** The archive is keyed by *what the data is*, not
*when we asked*. Each slot is archived exactly once, at first sighting, at whatever lag — fully
jitter-immune, which is what makes D1's over-polling sufficient rather than merely likely. Redundant
polls resolve to an existing path and write nothing, so ~6 snapshots/hour are written regardless of poll
rate. Still **path-existence dedup, no content hash** — Plan 136's principle is preserved; only the input
to the path changes. `_cycle_stem` already renders minutes, so the layout is unchanged.

**Consequence: the flow must FETCH BEFORE it can dedup**, deliberately reversing Plan 136's pre-fetch
short-circuit. Cheap at ~0.1 s / 234 KB per request. The old short-circuit must be **removed**, not left
in place, or it will dedup on the clock before the data is ever seen.

**D3 — a dedup skip must still write a heartbeat.** (Folded review major — this is the trap inside D2.)
If the implementation merely moves `_already_archived` below the fetch and returns there, a **frozen
graph resolves forever to its existing parquet path**, bypassing the measurement-age freshness check
(`:386-428`) entirely. The `stale_measurement_time` CRITICAL would become unreachable after the first
archived copy, and the watchdog would see only a much later missing-heartbeat failure. **Locked: every
successful fetch — dedup skip included — computes freshness and appends an `OK`/`CRITICAL` heartbeat
using `run_at`. Only the archive *writes* are skipped.** For a new slot, keep parquet-last and append
health after the writes succeed. The adapter-error and empty-response CRITICAL paths need no `cycle_at`
and are unaffected. `run_at` stays the heartbeat's `checked_at`; only the *archive key* becomes
data-derived — conflating the two is how the freshness gate would start lying.

**D4 — re-derive both staleness thresholds, and fix the formatter.** `_STALE_MEASUREMENT_THRESHOLD`
(`collect_bafu_observations.py:66-73`) and `BAFU_OBS_STALE_THRESHOLD` (`ops/watchdog.py:129`) are both
3 h "≈ three missed hourly cycles". **Locked: derive each independently from the 10-minute grid and the
measured ~14–15 min normal lag** — they are different quantities and should stop being coincidentally
equal. The measurement-age gate must tolerate normal lag plus margin so a healthy feed never trips it;
the heartbeat gate should tolerate a small number of missed *polls*. Concrete values are set in T4 and
recorded here before READY. **`_format_bafu_obs_stale_alert` (`ops/watchdog.py:649-659`) renders
`hours = int(threshold // 3600)`**, so any sub-hour threshold prints **"threshold: 0h"** — the formatter
and its tests move to minutes-aware rendering. *This means Plan 175's "no watchdog edits" non-goal does
not apply to this plan; the watchdog is in scope here.*

**D5 — RESOLVED: the collector runs on the `ingest` pool** (§ Execution isolation). Without cadence
isolation D1 would be a schedule that reality need not honour; with it, mechanism (a) — the dominant
cause of the 25–60 min starvation — is removed. Mechanism (b) residual is covered by D7's detection.

**D6 — gzip the archived raw payload, reversing a Plan 136 decision.** Plan 136 locked "plain `.json`, no
gzip, no retention knob" — reasonable at 2.15 GB/yr, decided under the hourly assumption D1 falsifies. At
the new cadence plain JSON costs **12.87 GB/yr** on a host that has already hit 94% disk. SPARQL JSON
compresses **41.8×**, bringing the denser archive to **0.58 GB/yr**. **Locked:** write `.json.gz` through
the existing `_atomic_write` (`:191-195`) — **the gzip stream must close before `_atomic_write` returns**,
or the atomic rename can publish a truncated file. Still no retention knob; compression removes the need.
Existing plain `.json` snapshots stay readable; no migration, readers tolerate both extensions.

**D7 — a missed slot must be detectable, not just unlikely.** Because residual mechanism (b) cannot be
designed away, completeness is *verified* rather than assumed. **Locked:** an audit over the archive that
reports, for a given window, which 10-minute slots are present and which are missing — derived from the
archived `cycle_at` values, not from Prefect run states (a run can succeed having fetched a slot already
held, and a run that never started leaves no state at all). This is what turns "we think we catch
everything" into a number. It also gives the before/after measurement for this plan's own effect.

## Tasks

### T1 — cadence-isolated execution (do first; everything else is theatre without it)

Per **D5**: route the collector's `DeploymentSpec` to `INGEST_POOL`, and add
`bafu_observation_archive:/data/bafu_observations:rw` to `prefect-worker-ingest` in `docker-compose.yml`
— it currently mounts only `./config.toml:ro`. **Verify the mount before shipping:** a pool move with a
missing volume fails at *write* time, after a successful fetch, which reads as a collector bug rather
than a deploy bug. A test asserting the deployment's `work_pool_name` keeps a future edit from silently
returning it to `default`.

**Test:** the registered deployment's `work_pool_name` is asserted, so a future edit cannot silently
return it to `default`.

### T2 — the schedule (D1)

Both cron defaults per Plan 175's blocker — **`docker-compose.yml` is the one that deploys**, the Python
fallback in `cli/register_deployments.py` must be kept in sync, and `config/overlays/mac-mini.toml`'s
"hourly-at-:05" comment corrected.

**Tests:** assert the D1 *properties*, not a literal string — max **cyclic** gap ≤4 min (including the
`:59 → :01` wrap), min gap ≥3 min, and every minute non-divisible by 5. Plus a compose-level assertion
mirroring `tests/unit/test_compose_schedule_default.py`.

### T3 — data-derived `cycle_at`, fetch-then-dedup, heartbeat-on-skip (D2 + D3)

In `flows/collect_bafu_observations.py`: `cycle_at` from the response's newest `measurement_time`
truncated to the grid; remove the pre-fetch short-circuit (`:346-356`); dedup after the fetch, before the
writes; **heartbeat on every successful fetch including a skip** (D3). Rewrite the module docstring
(`:17-25`), which documents the clock-derived hourly contract.

**Tests — assert exact values and prove RED against three mutants:**
- Exact archive filename and exact parquet `cycle_at` (e.g. `12:07` data → `12:00`; `12:17` data →
  `12:10`), not merely "two paths differ".
- RED against (a) current hourly truncation, (b) a 5-minute-truncation mutant — the weaker test list
  passed both — and (c) any clock-derived implementation.
- The clock-derived kill needs **pinned clocks**: identical `run_at` with `12:07` then `12:17` *data*
  must produce **two** archives; `run_at` values in **different hours** with identical newest data must
  produce **one**. ("Different clock times" alone passes hourly code when both fall in one hour.)
- D3: a later same-slot **fresh** poll writes no files but refreshes the heartbeat; a later same-slot
  **frozen** poll writes no files and emits `stale_measurement_time`.
- Existing tests to reconcile deliberately: `tests/unit/flows/test_collect_bafu_observations.py:113`
  (encodes the clock-based dedup contract) and `:208` (reads the raw companion as plain text).

### T4 — staleness thresholds and the alert formatter (D4)

Both constants, their comments, boundary tests, and the minutes-aware formatter with an **exact** alert
string assertion. Update the watchdog tests.

### T5 — gzip the raw companion (D6)

`_write_raw_payload` / `_raw_payload_path` → `.json.gz`, stream closed before the atomic rename, parquet
still written last as the dedup marker. Test round-trip fidelity byte-for-byte.

### T6 — docs

Plan 136 gets **two explicit corrections, stated as corrections**: §81-86 `cadence = hourly` is
falsified, and "no gzip" is reversed — both with the measurements. `docs/standards/orchestration.md`
gains the pool-starvation constraint (a cron interval is not a poll-interval guarantee on a shared pool)
and the two-places-for-a-cron-default trap. `docs/v0-scope.md` records that a 10-minutely BAFU archive
exists from this plan forward and not retrospectively — relevant to v0b sub-daily R&D. The Plan 175
decision record gains the cadence, lag and storage evidence.

### T7 — extend the lag measurement (`live`-marked)

D1's ≤4 min sizing rests on a **single** clean publish gap. A `live` check that records slot
first-sighting lag over a longer window turns that assumption into evidence, reports the observed minimum
gap so the margin can be re-derived rather than re-guessed, and is what would detect BAFU changing the
grid or lag instead of us assuming it stable.

### T8 — the completeness audit (D7)

A script/CLI that walks the archive for a window and reports present vs missing 10-minute slots from the
archived `cycle_at` values. Run it before and after this plan lands: the "before" number quantifies what
the hourly cadence has already cost, the "after" number is the acceptance evidence. Keep it cheap enough
to run regularly — a silent hole is the failure mode this plan exists to end.

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["1"], "parallel": false},
    {"id": "phase-2", "tasks": ["3"], "parallel": false, "depends_on": ["phase-1"]},
    {"id": "phase-3", "tasks": ["2", "4", "5"], "parallel": true, "depends_on": ["phase-2"]},
    {"id": "phase-4", "tasks": ["6", "7", "8"], "parallel": true, "depends_on": ["phase-3"]}
  ]
}
```

T2 must not ship before T3 — the new schedule without data-derived `cycle_at` polls many times an hour
and archives once per clock bucket, which is worse than today because it *looks* fixed.

## Exit gates

- `uv run pytest` green; every new lock proven RED against the specific mutant it targets
  (stash-fix/run/restore). A test that passes against the pre-fix code proves nothing.
- `uv run ruff format` + `uv run ruff check --fix` clean; `uv run pyright` ratchet not regressed.
- No new unit test touches the network.
- Patch version bump per code commit; tag on `main` after merge, never on a branch.
- Hold at PR. Human owns merge.

## Host acceptance (requires mini access)

1. Confirm the collector is registered on the D5 pool with the archive volume mounted.
2. **Over one hour: ~6 snapshots** — one per 10-minute slot. *Not* one (dedup collapsed the slots), *not*
   one per poll (dedup not working). Check the archive listing, not run states.
3. **`cycle_at` values are consecutive slots with no gaps** — the end-to-end proof the 83% loss is closed.
4. **Exercise a forecast-cycle overlap** and verify actual start/fetch spacing stays within the D1 bound.
   Watching an arbitrary quiet hour proves nothing about the starvation this plan exists to survive.
5. Raw companions are `.json.gz`, ~5.6 KB not 234 KB.
6. The stale alert renders the new D4 threshold correctly — not "threshold: 0h".

## Notes

- The completeness problem was invisible for as long as it was because nothing measured it: the collector
  alerts on the *current run*, never on gaps in the archive. Plan 175's T0 adds the archive-completeness
  audit that quantifies what has already been lost; this plan stops the loss.
- Three of this plan's decisions (D2, D3, D6) are deliberate reversals of Plan 136 properties. Each is
  recorded as a reversal with its measurement, because Plan 136 is a READY plan whose reasoning was sound
  under an assumption that has since been falsified — not a plan that was careless.
