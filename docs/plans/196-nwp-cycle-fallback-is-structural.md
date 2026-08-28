---
status: DRAFT
created: 2026-08-21
plan: 196
title: Measure ICON-CH2-EPS publication latency before trusting the 105-minute guess
scope: One measurement of real ICON-CH2-EPS publication latency, and one paragraph in the docs recording that the forecast cron and nwp_cycle_min_age_minutes are coupled. No code change, no schedule change, no migration.
depends_on: []
blocks: []
source: Flow Map integration audit 2026-08-21 § B; cut down from the four-task draft after the diagnosis closed
---

# Plan 196 — measure ICON-CH2-EPS publication latency before trusting the 105-minute guess

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ PROPORTIONALITY IS A BINDING CONSTRAINT ON THIS PLAN AND ON ITS REVIEW

**This plan measures one number and writes one paragraph.** It changes no code, no schedule and no
schema. It exists because a load-bearing constant was never measured, and because the reason it is
load-bearing is written down nowhere.

An earlier draft of this plan carried three further tasks — re-cron the forecast cycle, persist
`fallback_reason`, correct the architecture doc. **They were cut deliberately** (see § What was cut
and why). Do not reinstate them here; T1's result decides whether any of them is worth drafting.

**Rules for reviewers:**

1. **"No findings" is a complete and welcome review.** Do not manufacture findings to justify the pass.
2. **A finding must name a CONCRETE FAILURE.**
3. **Do not propose new apparatus.** No probe harness, no scheduled latency monitor, no new script
   file — CLAUDE.md § Ad-hoc Analyses makes this a heredoc.
4. **Adding length is a cost.** Prefer deleting to adding.

## Why — measured, not hypothetical

Verified on the mac mini, 2026-08-21:

- **Every `fallback` forecast walked back exactly one 6-hour step.** Across 232 forecasts:
  `lag = 6h` on 202 rows, `7h` on 2, and **never 2 steps**, although `nwp_max_fallback_age_hours =
  12.0` permits two.
- **The reason is always `too_recent`, never `not_published`.** Worker logs, last 30 hours: 4 cycles,
  4 × `nwp.cycle_too_recent`, 4 × `fallback_reason=too_recent`, **0 × `nwp.cycle_not_published`**.
  Recorded age at the snapped slot: `0.0` minutes.
- **All 14 `primary` forecasts were issued off-cron** — 15:10, 10:28, 15:16, 08:29 — where the
  snapped cycle was already older than 105 minutes.

The arithmetic makes it unavoidable. `_CYCLE_HOURS = (0, 6, 12, 18)`
(`adapters/meteoswiss_nwp.py:44`); the forecast cron is `0 */6 * * *`
(`cli/register_deployments.py:45`). The flow fires at 06:00:0x, `_snap_to_cycle` returns 06:00,
`age_minutes ≈ 0`, and the `age_minutes < 105` guard (`nwp_cycle_min_age_minutes`, `config.toml:16`)
skips the slot **without probing STAC** and walks back to 00Z. A cron-scheduled run can never be
PRIMARY.

**This is not an outage and nothing is corrupted.** The 00Z cycle consumed at 06:00 is a real,
fully published ICON-CH2-EPS run, and input quality does not flag it (`nwp_age_partial_hours = 9.0`,
`config/deployment.py:65`, against an age of 6h). Two real costs remain:

1. **The walk-back burns one of three fallback attempts.** `max_fallback_steps = 2` gives
   `resolve_cycle` three candidates; at the 06:00 cron those are 06Z (guaranteed skip), 00Z, and
   18Z-previous-day. Effective depth is **2, not 3**, every single run.
2. **`nwp_cycle_min_age_minutes = 105` has never been measured.** `config.toml:15` calls it
   "~90-120 min = ICON-CH2-EPS publish latency" with no citation, and no measurement exists anywhere
   in the repository. It sets how much horizon every operational forecast gives up, and nobody knows
   whether it is right.

That second point is the whole plan. The constant is guessed, load-bearing, and cheap to check.

## Decisions

- **D1 — `resolve_cycle` is correct and is not touched.** Skipping a slot younger than the delivery
  delay is right: a partially published cycle passes `_cycle_is_published` and would silently yield
  short grids (Plan 090 D2c/D4). The interaction with the cron is a scheduling fact, not an adapter
  bug.

- **D2 — Measure, then stop.** T1 produces a latency distribution and a written finding. It does
  **not** change the constant, the schedule, or anything else, however clear the result looks. Acting
  on the number is a separate plan, drafted only if the number justifies one.

## Tasks

### T1 — measure the real publication latency
*In:* a throwaway heredoc against the MeteoSwiss STAC catalogue (CLAUDE.md § Ad-hoc Analyses —
**no new script file**, no addition to `scripts/`).

For every ICON-CH2-EPS cycle in the retained window, record `forecast:reference_datetime` against the
item's own publication timestamp. Report **min / median / p95 / max**, the sample size, and the window
covered. Reuse the pagination behaviour `_cycle_is_published` already relies on
(`adapters/meteoswiss_nwp.py:560`) — MeteoSwiss sorts items reference-datetime-ascending, so a
single-page read silently misses the newest cycles and would bias the result low.

**Exit:** the numbers and a one-paragraph finding, pasted into this plan under a new `## T1 result`
heading, stating which of these holds:

- **p95 < 105 min** — the guard is too conservative. Every forecast gives up horizon for nothing, and
  a re-cron plan is worth drafting.
- **p95 ≈ 105 min** — the guess was sound. Record that it is now measured, and close.
- **p95 > 105 min** — the guard is too small. The walk-back has been partly protective all along,
  and any future re-cron must use the measured figure, not 105.

### T2 — record the coupling
*In:* `docs/standards/orchestration.md`, plus a dated REVISIT note in `docs/plans/archive/090-*`.

One paragraph, where the next reader will meet it: **the forecast cron and
`nwp_cycle_min_age_minutes` are coupled, and changing either without the other silently pins NWP
provenance to FALLBACK.** Plan 090 introduced the guard without recording that the flow's own
schedule decides whether it ever fires; that omission is why this went unnoticed for a month.

Include the measured figure from T1 so the next person inherits evidence rather than the same guess.

## What was cut and why

Recorded so the reasoning is not re-litigated from scratch:

- **Re-cron the forecast cycle past the publication latency.** Real but modest: the operational
  models are daily-timestep regressions over basin-averaged precipitation and temperature, where one
  extra NWP cycle moves the aggregate very little, and the benefit is unquantified. Revisit only if
  T1 shows p95 < 105.
- **Persist `fallback_reason` onto the forecast row.** Its entire justification was the Flow Map
  surfacing `nwp_cycle_source` publicly. The cheaper answer is to **omit the field from the map
  snapshot until it means something**. Blocked behind the map contract decision, which may conclude
  the audience does not exist.
- **Correct `architecture-context.md`.** Folded into T2 rather than scheduled separately.

The monitoring blind spot that motivated the cut tasks is **one cycle deep, with working backstops**:
two missed publications put lag at 12h and fire an input-quality PARTIAL at the 9h threshold; three
raise `NoCycleAvailableError`. Only the single-miss case is invisible.

## Non-goals

Changing `nwp_cycle_min_age_minutes` · changing the forecast cron · touching `resolve_cycle` ·
adding a `NwpCycleSource` member · persisting `fallback_reason` · backfilling the 202 existing
rows (honest under the definition in force when written) · a recurring latency monitor · the
Recap/IFS gateway's separate `DEFAULT_MAX_CYCLE_AGE_HOURS` defect · anything about the Flow Map
contract.

## Exit gates

No code changes, so no test or type gate applies. The gate is the finding itself:

- `## T1 result` exists in this file, with sample size and the window measured.
- T2's paragraph carries the measured figure, not a restated guess.
- `git diff --stat` touches **only** `docs/`.
