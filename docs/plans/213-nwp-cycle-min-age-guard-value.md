---
status: DRAFT
created: 2026-08-28
plan: 213
title: Raise nwp_cycle_min_age_minutes above the measured publication latency
scope: One config value, two regression tests, one pinning the on-time cron path as unchanged, and one doc line. No code change, no schedule change, no schema change.
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

**The ON-TIME cron path is unaffected by this change, and that is not a side note.** When a
scheduled run starts on the grid instant, the candidate cycles are either ~0 or ~360 minutes old,
so a guard of 105 and a guard of 180 make **identical** decisions:

| candidate age (on-time run) | guard 105 | guard 180 |
|---|---|---|
| 0 min (the just-stamped cycle) | SKIP | SKIP |
| 360 min (one step back) | ACCEPT | ACCEPT |

So this plan does **not** improve an on-time scheduled forecast.

**But "cron run" and "on-time run" are not the same thing, and the first draft of this plan
conflated them.** When no explicit `cycle_time` is passed, `_resolve_cycle_time` falls back to
`clock()`, which defaults to `datetime.now(UTC)` at **execution** time
(`flows/run_forecast_cycle.py:671`, `:1873`) — not to Prefect's scheduled start. Pickup lateness
on a shared work pool is real and documented
(`docs/standards/orchestration.md` § "A cron INTERVAL is not a poll-INTERVAL guarantee on a
shared pool"). A scheduled run picked up 150 minutes late therefore sees a 150-minute-old
candidate, and **that is exactly the hazard window**.

**The affected set is therefore: any run with no explicit `cycle_time` that executes when the
snapped cycle is 105-180 minutes old** — manual triggers, ad-hoc reruns, *and* late-picked-up
scheduled runs. An on-grid backfill that passes an explicit timestamp stays a zero-age case and
is unaffected.

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

- **D3 — re-measure first, then pick a value with margin; 180 is the expected answer, not a
  given.** Plan 196 T1 and `docs/standards/orchestration.md` both say: do not reuse that
  sample's maximum. Deriving 180 straight from 168.4 would violate this plan's own parent
  standard. T1 therefore **re-runs the Plan 196 measurement once** before choosing — same
  heredoc method, no new apparatus — and the value is chosen from the combined sample with
  explicit margin. If the fresh measurement agrees with 160-168, 180 ships. **If it does not,
  stop and report — do not improvise a value.**

- **D3b — why 180 and not 170.** Plan 196 T1 explicitly refused to license 170 as a safe floor: it clears
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
- One regression test asserting the **new** behaviour, **anchored to `config.toml` itself**. Every
  mechanism this change touches is already generic and already tested for arbitrary values — the
  guard branch (`adapters/meteoswiss_nwp.py:512`) compares against whatever it was given, the
  constructor takes `cycle_min_age_minutes` as a free parameter (`:361`), and `load_config`
  parsing of this field is covered for both 105 and an explicit 120
  (`tests/unit/config/test_deployment.py:205-224`). **The only thing T1 changes is the literal in
  `config.toml:17`**, so a test written in the file's existing idiom — a bare
  `min_age_minutes=180` threaded into `_make_delay_adapter`
  (`tests/unit/adapters/test_meteoswiss_nwp.py:368-378`) — would pass on today's repo with none of
  T1 applied and gate nothing. The test must therefore:
  1. assert `load_config(_REPO_ROOT / "config.toml").nwp_cycle_min_age_minutes == 180` — with
     `monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)` first, since `load_config`
     always applies an overlay if that variable is set (`config/_overlay.py:27`), and with `Path`
     imported at runtime (the adapter test module imports it only under `TYPE_CHECKING`) — RED today
     (the file carries 105), GREEN after the edit. Use the established repo-root idiom
     `_REPO_ROOT = Path(__file__).resolve().parents[3]` (`tests/unit/config/test_qc_rules.py:16`);
  2. drive `resolve_cycle` with **that loaded value**, not a literal, so the 150-minute candidate
     is skipped because the shipped config says so.
- **Not a task item, a recorded fact:** `nwp_max_wait_hours` is defined at
  `config/deployment.py:90` and read **nowhere else in `src/`**, so it cannot interact with this
  guard today — and it measures from expected delivery, not cycle time, so it would not collide
  even when implemented. One line in § Deferred; no investigation in T1.
- **Noted trade-off:** the dataclass default stays 105 (`config/deployment.py:98`) — only the
  shipped `config.toml` moves. That keeps `tests/unit/config/test_deployment.py:211` (default =
  105) green; a deployment that omits the key still gets the old guard. Changing the code default
  is a wider blast radius than this plan wants and is deliberately left out.

*Exit:* both tests green; **RED proof is required for the shipped-config/150-minute test only** —
the cron-invariance test passes under both 105 and 180 by design, which is the property it pins.
`uv run pytest`, `ruff`, pyright ratchet pass; the deployed config change noted for the next mini deploy.

## Deferred (not drafted)

**Replace the age proxy with a real completeness probe.** `_cycle_is_published`
(`adapters/meteoswiss_nwp.py:560`) returns `True` on *any* item matching the reference datetime,
while the fetch needs the allowlisted variables out to +120 h (`:689`, `:746`, `:774`). A probe that
checked for those specific items would make `nwp_cycle_min_age_minutes` redundant and would survive
a change in MeteoSwiss publication behaviour — and would transfer to Nepal/IFS, where the latency is
different and unmeasured. Worth drafting if the off-cron path ever becomes routine, or at v1 when
the NWP source changes.

**When the lateness wait stage is built, budget for this guard.** `nwp_max_wait_hours` is dormant
— `config/deployment.py:90` is its only occurrence in `src/`. It is *not* in collision with the
guard: the wait is defined as starting past **expected delivery**
(`docs/architecture-context.md:171`), not at cycle reference time, so the two measure from
different origins. Noted only so whoever implements the stage checks rather than assumes.

## Non-goals

Changing the forecast cron · touching `resolve_cycle` · rewriting `_cycle_is_published` ·
`max_fallback_steps` · `nwp_max_fallback_age_hours` · changing the dataclass default at
`config/deployment.py:98` · implementing or re-tuning `nwp_max_wait_hours` · re-issuing the 2
forecasts already issued inside the window · a recurring latency monitor · anything about the
Recap/IFS gateway. (Single exclusion list — T1 previously repeated most of these inline.)

## Exit gates

- `config.toml` carries 180 with a comment citing the measurement, its date and its `n`,
  `nwp_max_wait_hours` collision warning.
- A test pins the cron path as unchanged; a test reads `nwp_cycle_min_age_minutes` **from the
  shipped `config.toml`** and pins the 150-minute candidate as now skipped (RED against 105).
- `git diff --stat` touches `config.toml`, one test file, the doc surfaces named in T1, and the
  version-bump files — and nothing else.
