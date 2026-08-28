---
status: DRAFT
created: 2026-08-28
plan: 213
title: Raise nwp_cycle_min_age_minutes above the measured publication latency
scope: One config value, one regression test that pins the cron path as unchanged, and one doc line. No code change, no schedule change, no schema change.
depends_on: [196]
blocks: []
source: Plan 196 T1 — measured publication latency 160.0-168.4 min against a guard of 105
---

# Plan 213 — raise `nwp_cycle_min_age_minutes` above the measured publication latency

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ Proportionality is a binding constraint on this plan AND on its review

**This plan changes one integer in `config.toml`.** Plan 196 D2 deliberately separated *measuring*
from *acting*; this is the acting half, and it is small on purpose.

**Rules binding every reviewer:**

1. **"No findings" is a complete and welcome review.** Do not manufacture findings.
2. **A finding must name a CONCRETE FAILURE**, with `file:line`.
3. **Do not propose new apparatus** — no monitor, no probe harness, no new script.
4. **Do not reinstate the re-cron.** Plan 196 T1 killed it (§ Non-goals).
5. **Adding length is a cost.** Prefer deleting to adding.

## The honest case — read this before agreeing

**The cron path is completely unaffected by this change, and that is not a side note.** At the
`0 */6 * * *` cron the candidate cycles are either ~0 or ~360 minutes old, so a guard of 105 and a
guard of 180 make **identical** decisions on every scheduled run. Verified by enumeration:

| candidate age | guard 105 | guard 180 |
|---|---|---|
| 0 min (the just-stamped cycle) | SKIP | SKIP |
| 360 min (one step back) | ACCEPT | ACCEPT |

So this plan does **not** improve any scheduled forecast. It closes a window that only **off-cron**
runs can enter — manual triggers, backfills, ad-hoc reruns.

**That window is real.** Plan 196 T1 measured that the variables the fetch allowlists (`tot_prec`,
`t_2m` at +120 h) appear in the STAC catalogue **160.0-168.4 minutes** after reference time
(n = 4, 2026-08-28). The guard is **105**. Between those two figures the guard says "old enough"
while the cycle is still publishing — and `resolve_cycle` skips *without* probing STAC
(`adapters/meteoswiss_nwp.py:512`), so nothing else stands in the way. This is precisely the
partial-publication hazard Plan 090 D2c/D4 exists to prevent.

**Two forecasts have already entered that window.** On the mac mini, of 319 forecasts carrying an
NWP cycle reference:

| `nwp_cycle_source` | n | issued at cycle age 105-175 min | min age | max age |
|---|---|---|---|---|
| fallback | 305 | **0** | 360 | 472 |
| primary | 14 | **2** | 150 | 269 |

**But there is no evidence of harm, and this plan must not pretend otherwise.** All seven primary
forecasts with stored values produced **5 daily steps**, including the 150-minute one
(2026-08-18 08:29). Either the cycle was complete sooner that day — latency was measured on
2026-08-27/28, not on 2026-08-18 — or the daily aggregation absorbed missing tail hours silently.
**We did not determine which.** The case for this change is *latent correctness*, not a
demonstrated defect.

## Decisions

- **D1 — Do not touch the cron.** Plan 196 T1 established that a re-cron computed from the old
  figure would have fired before the data existed. The cron is correct and stays `0 */6 * * *`.

- **D2 — Raise the constant; do not replace the mechanism.** The structurally better fix is to make
  the publication probe check for *the items the fetch actually needs* at the maximum horizon,
  which would retire the constant's load-bearing role entirely. **That is deliberately NOT in this
  plan.** It is a code change to `_cycle_is_published` plus tests, it benefits only the same rare
  off-cron path, and a one-integer change captures most of the value at a fraction of the risk.
  Recorded as a follow-on, undrafted, in § Deferred.

- **D3 — 180, not 170.** Plan 196 T1 explicitly refused to license 170 as a safe floor: it clears
  the observed maximum of 168.4 by 1.6 minutes on n = 4 from a single August window. 180 is a round
  number with ~12 minutes of margin, and it costs at most one extra walk-back step on a manual run
  launched in the 105-180 minute window — that run gets the previous cycle instead of an
  incompletely published one, which is the trade this plan wants.

- **D4 — The value stays a guess with a citation, and the config comment must say so.** n = 4, one
  August window, `created` as a proxy for availability rather than a verified download. A future
  reader must not mistake 180 for a validated bound.

## Task

**T1 — raise the guard, pin the cron path, cite the measurement.**

*In:*
- `config.toml:17` — `nwp_cycle_min_age_minutes` 105 → 180, and replace the "~90-120 min" comment
  (`:15-16`) with the measured range, its date, its `n`, and a pointer to Plan 196 § T1 result.
- One regression test asserting **the cron path is unchanged** by the new value: at a `0 */6` cron
  instant, `resolve_cycle` still skips the zero-age candidate and still resolves to the
  one-step-back cycle under both 105 and 180. This is the test that would catch someone later
  "fixing" the guard into a value that changes scheduled behaviour.
- One regression test asserting the **new** behaviour: a candidate aged 150 minutes is now skipped
  where it was previously accepted.
- Check and record whether `nwp_max_wait_hours = 3.0` (`config/deployment.py:90`, also 180 minutes)
  interacts with a 180-minute guard. **If it does, stop and report — do not resolve it here.**

*Scope (out):* the cron · `resolve_cycle` logic · `_cycle_is_published` · `max_fallback_steps` ·
`nwp_max_fallback_age_hours` · the Recap/IFS gateway's separate `DEFAULT_MAX_CYCLE_AGE_HOURS` ·
backfilling or re-issuing the 2 affected forecasts.

*Exit:* both tests green and RED-proved against the old value where applicable; `uv run pytest`,
`ruff`, pyright ratchet pass; the deployed config change noted for the next mini deploy.

## Deferred (not drafted)

**Replace the age proxy with a real completeness probe.** `_cycle_is_published`
(`adapters/meteoswiss_nwp.py:560`) returns `True` on *any* item matching the reference datetime,
while the fetch needs the allowlisted variables out to +120 h (`:689`, `:746`, `:774`). A probe that
checked for those specific items would make `nwp_cycle_min_age_minutes` redundant and would survive
a change in MeteoSwiss publication behaviour — and would transfer to Nepal/IFS, where the latency is
different and unmeasured. Worth drafting if the off-cron path ever becomes routine, or at v1 when
the NWP source changes.

## Non-goals

Changing the forecast cron · touching `resolve_cycle` · rewriting `_cycle_is_published` ·
re-issuing the 2 forecasts already issued inside the window · a recurring latency monitor ·
anything about the Recap/IFS gateway.

## Exit gates

- `config.toml` carries 180 with a comment citing the measurement, its date and its `n`.
- A test pins the cron path as unchanged; a test pins the 150-minute candidate as now skipped.
- `git diff --stat` touches only `config.toml`, one test file, and the version-bump files.
