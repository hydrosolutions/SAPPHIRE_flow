---
status: READY
created: 2026-08-19
plan: 189
title: Two small follow-ons from Plan 176's first night — audit window edge, and D1's poll bound
scope: Two independent, small corrections surfaced by running Plan 176 in production overnight. T1 stops the completeness audit reporting trailing slots as missing when they cannot yet have been published. T2 tightens D1's poll bound after T7 measured a 7.0 min minimum publish gap against a bound sized for 10.8. No new subsystems; both are parameter/boundary fixes with tests. Swiss/BAFU only.
depends_on: [176]
blocks: []
supersedes: []
---

# Plan 189 — audit window edge + poll bound

## Status

**READY** (owner flip 2026-08-19). Both items come from **measurements**, not review opinion: Plan 176's first
overnight run (`docs/decisions/bafu-lindas-rate-limit.md` § First overnight run) and Plan 176 T7's
extended lag measurement. Deliberately two small tasks in one plan because both are single-parameter
corrections to the same subsystem, each with an obvious test; splitting them would cost more ceremony
than the changes are worth.

## Review guidance

**Do not over-engineer.** This plan exists because two numbers are wrong, not because the design is.
"You could also build X" is not a finding; simplification findings are welcome. The live deployment
polls 2 stations and archives ~144 slots/day. An honest "this is proportionate" is the expected outcome.

## T1 — the audit must not count slots that cannot exist yet

**Problem, observed.** The first overnight audit reported `05:50` missing at `06:00`. It was not missing:
LINDAS publishes a slot **14–17 min after its timestamp**, so `05:50` could not have been archived before
~`06:05`. `_expected_slots` (`src/sapphire_flow/cli/bafu_observation_audit.py:60-70`) enumerates every
grid slot in `[start, end)` with no regard for publishability, so **the trailing one or two slots of any
window always read as missing**.

That is worse than cosmetic. The audit is the instrument for deciding whether the archive is healthy
(Plan 176 D7); an instrument that always shows one or two phantom gaps trains its reader to discount real
ones.

**Fix.** Exclude slots younger than a publish-lag horizon from the *expected* set. **Locked:
`_PUBLISH_LAG_HORIZON = 30 min`** — comfortably above the measured 14–17 min lag, and the same figure as
`_STALE_MEASUREMENT_THRESHOLD` (Plan 176 D4), which was derived from the same lag. Reuse rather than
introduce a second, silently-diverging constant.

Report the exclusion rather than hiding it: the summary must state how many slots were skipped as
too-recent, so a reader can tell "complete" from "complete so far as we can yet know".

**Scope.** `_expected_slots` and the report summary only. Out of scope: the slot-derivation logic, the
legacy clock-keyed handling, and anything about scheduling or alerting.

**Verification.** `uv run pytest tests/unit/cli/test_bafu_observation_audit.py -q`

**Tests.** A window ending *now* excludes the trailing slots and reports them as skipped, not missing —
proven RED against today's code, which reports them missing. A window ending well in the past is
unchanged (no slots excluded), so historical audits keep their existing meaning. Boundary: a slot exactly
at the horizon.

## T2 — tighten D1's poll bound to match the measured publish gap

**Problem, measured.** D1's `≤4 min` maximum inter-poll gap was sized as a ~2.7× margin on a **single**
clean publish gap of 10.8 min. Plan 176 T7's 45-minute run measured four transitions:

```
gaps_minutes  = [7.0, 10.0, 10.0]        -> minimum 7.0 min
lags_minutes  = [17.6, 14.6, 14.6, 14.6] -> ~3 min of jitter
```

Against 7.0 min the margin is **1.75×**, not 2.7×. Plan 176 D1 pre-committed the response: *"if T7's
longer measurement ever shows a publish gap under ~8 min, tighten the bound rather than accept the
risk."* 7.0 < 8.0, and the test emitted its own `bafu_lindas_lag.gap_under_margin` warning.

**Not urgent, and say so.** 4 min < 7 min, so no slot can currently slip between polls — the guarantee
holds; the headroom shrank. This is a margin restoration, not an incident.

**Fix.** Max cyclic inter-poll gap **≤3 min** (2.33× on 7.0), every minute still non-divisible by 5, and
the minimum gap relaxed from ≥3 to **≥2 min**. A satisfying list:
`1,3,6,8,11,13,16,18,21,23,26,28,31,33,36,38,41,43,46,48,51,53,56,58` — 24 runs/hour, gaps alternating
2 and 3 min, wrap `:58 → :01` = 3 min, no minute divisible by 5, and Prefect parses it.

**Why the minimum gap must be relaxed — it is not a preference** (folded review finding; the first draft
kept `≥3` and proposed a list that violated it, which the reviewer caught arithmetically). Requiring
`max ≤ 3` *and* `min ≥ 3` forces **every** gap to be exactly 3, i.e. a single residue class mod 3 — and
every such class contains exactly four multiples of 5:

```
minutes ≡ 0 (mod 3) -> multiples of 5:  0, 15, 30, 45
minutes ≡ 1 (mod 3) -> multiples of 5: 10, 25, 40, 55
minutes ≡ 2 (mod 3) -> multiples of 5:  5, 20, 35, 50
```

So `max ≤ 3` + `min ≥ 3` + "no minute divisible by 5" (D4, which keeps the collector off
`ingest-observations`' `*/5` tick) is **arithmetically impossible**. One of the three must yield, and it
is the minimum: D4 protects against a measured rate-limit collision, while the maximum is the whole point
of this task. **Do not "restore" `≥3` — it cannot be satisfied.**

**What the ≥3 guard was for, and the residual.** Plan 176 D1 locked it "so a valid-but-clustered list
cannot interact badly with Plan 175's 120 s total retry deadline". At a 2-minute minimum the gap now
*equals* that deadline: under sustained upstream failure a run exhausting its full retry budget finishes
exactly as the next is scheduled. Accepted, because (a) a healthy run takes ~0.1 s, so this arises only
when LINDAS is already failing, (b) the deployment's `concurrency_limit=1` serialises rather than
overlaps them, and (c) a feed failing that long trips the D4 freshness alert regardless. Stated rather
than left for someone to rediscover.

Cost is requests only: still one per run, ~24/hour against a measured ~45/min capacity, and D2's
data-derived key means storage tracks the grid (~144 slots/day), not the poll rate.

**Change BOTH defaults** — `docker-compose.yml` *and* `cli/register_deployments.py`. Never the mini's
`.env` alone: Plan 175's blocker was exactly this divergence, where compose pins the value the Python
fallback never reaches.

**This does NOT address the `23:20` gap.** That slot was lost because the network's *bulk* never sat
there during a BAFU republish — faster polling cannot capture a state that never existed. Keeping the two
separate matters, or the next reader will credit this change with a fix it does not deliver.

**Scope.** The two cron defaults and their property tests. Out of scope: D1's *properties* (unchanged in
kind), D2's keying, the pool assignment.

**Verification.** `uv run pytest tests/unit/cli/test_register_deployments.py tests/unit/test_compose_schedule_default.py -q`

**Tests.** The existing property assertions (cyclic max gap, min gap, non-divisibility, every-hour) with
the max-gap bound tightened to **3** and the min-gap bound relaxed to **2**; compose and Python defaults
still identical. Proven RED against the current 4-minute list. Add an assertion that the schedule is
accepted by Prefect's `CronSchedule` — the reviewer validated the proposed list that way, and a property
test that never parses the string would miss a malformed one.

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["1", "2"], "parallel": true}
  ]
}
```

T1 and T2 are genuinely independent — different files, different tests — and may ship together or apart.

## Exit gates

- `uv run pytest` green; each new lock proven RED against the specific behaviour it changes.
- `uv run ruff format` + `check` clean; `uv run pyright` ratchet not regressed.
- Patch version bump per code commit; tag on `main` after merge, never on a branch.
- Hold at PR. Human owns merge.

## Host acceptance

Re-run the audit over a window ending **at least 30 min in the past** and confirm zero missing slots
where none are genuinely missing; then over a window ending *now* and confirm the trailing slots are
reported as **skipped**, not missing. After T2, confirm the registered cron matches the new list and that
snapshot count per hour is unchanged (~6) — the poll rate rises, the archive rate must not.

## Notes

- Both numbers were wrong in the same direction: derived from too little evidence and then trusted. The
  audit horizon did not exist at all, and the poll bound rested on one clean sample. Production supplied
  the missing evidence within a single night.
