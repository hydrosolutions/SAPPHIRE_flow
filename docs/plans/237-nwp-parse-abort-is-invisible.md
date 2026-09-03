---
status: DRAFT
created: 2026-09-03
revised: 2026-09-03
plan: 237
title: Duplicated STAC assets abort forecast cycles, and the flow reports COMPLETED anyway
scope: De-duplicate the fetched GRIB asset list on a collision-safe identity so a repeated STAC item cannot make the parse see the same ensemble members twice, and make the NWP-fetch abort path reach a non-successful terminal state. Two tasks. No retry logic, no completeness probe, no alert-channel work, and NO handling of the separate incomplete-cycle fault recorded below.
depends_on: []
blocks: []
source: Measured on the mac mini 2026-09-03 after 15 Slack alerts in one day. Two distinct faults found: 2026-09-03T12 was a complete cycle plus duplicate assets (our bug, in scope); 2026-09-02T18 was an incomplete cycle plus duplicates (partly upstream, out of scope).
---

# Plan 237 — a duplicated STAC asset aborts the cycle

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ Proportionality — two tasks, and the cause is KNOWN

An earlier revision of this plan said the cause was unidentified and authorised **observability
only**. That is superseded: the cause is now measured (below), so T1 is a real fix and it is small.

**Do not add:** retry-on-parse-failure, a re-fetch path, a completeness probe (that is Plan 213 D3c
for a *different* failure mode), STAC pagination rework, new alert channels, or a third task.
Reviewers: the temptation here is to harden the whole NWP fetch path. Don't. Duplicated list
entries are the bug in scope; the separate incomplete-cycle fault is recorded and deferred, not
solved here.

## What is measured — TWO different faults, not one

**The complete asset set for one ICON-CH2-EPS cycle is 484 files**: 121 hourly steps (0…120) x 2
allowlisted variables (`tot_prec`, `t_2m`) x 2 files each (`ctrl`, `perturb`). Consistent with
`PARAM_GROUPS` (`meteoswiss_nwp.py:56-62`) and the inclusive cycle→+120 h window the walk uses
(`:704-709`).

| cycle (UTC) | downloads | **distinct** | reading | outcome |
|---|---|---|---|---|
| 2026-09-02T12 | 484 | 484 | complete, no duplicates | ok |
| **2026-09-02T18** | **501** | **473** | **INCOMPLETE (11 short) + 28 duplicates** | **aborted** |
| 2026-09-03T00 | 484 | 484 | complete, no duplicates | ok |
| 2026-09-03T06 | 484 | 484 | complete, no duplicates | ok |
| **2026-09-03T12** | **488** | **484** | complete + 4 duplicates | **aborted** |

**These are not the same failure, and an earlier revision of this plan wrongly treated them as
one.** An independent review flagged that the 501 case had no distinct count behind it; measuring it
showed the generalisation was false.

- **2026-09-03T12 — surplus only.** 484 distinct: the cycle is **complete**. Step **35** was served
  twice for all four file types (`t_2m`/`tot_prec` x `ctrl`/`perturb`). Nothing is missing; we were
  handed items twice. **T1 fixes this.**
- **2026-09-02T18 — incomplete AND duplicated.** Only **473** distinct, i.e. **11 files short of a
  complete cycle**, plus 28 duplicate downloads (all `-perturb`, e.g. steps 68/71/78). So for that
  cycle MeteoSwiss data really was missing. **T1 does not fix this** — de-duplication would leave
  473 genuine files, and whether an 11-file-short cycle parses, yields a truncated forecast, or
  fails another way is **unmeasured**.

So the honest answer to "outage or our bug?" is **both, separately**: 09-03T12 is entirely our bug;
09-02T18 involves real upstream incompleteness that our duplicate handling then compounds. Only the
first is in scope here.

### The mechanism, end to end

1. `_fetch_grib_files` walks the STAC 120 h window and, for every matching asset, downloads it and
   appends the path to `grib_files` — **with no de-duplication**
   (`src/sapphire_flow/adapters/meteoswiss_nwp.py:816`).
2. A duplicated STAC item therefore yields the **same local path twice** in the returned list. The
   file on disk is simply overwritten (hence 484 distinct), so nothing looks wrong at the
   filesystem level.
3. `_parse_grib_files` opens every list entry, so step 35's dataset is opened **twice**.
4. `_combine_cfgrib_datasets` (`:275`) groups by `valid_time` and concatenates along `number`. The
   duplicated entry contributes the *same* member values again, so the group holds e.g. `{0}, {0}`
   or `{1..20}, {1..20}`.
5. xarray's alignment raises `cannot reindex or align along dimension 'number' because the (pandas)
   index has duplicate values`. The handler treats it as "this parameter group is unparseable" and
   `continue`s — for **both** variables.
6. No parameter groups survive: `nwp.fetch_failed error='No parameter groups could be parsed from
   GRIB2 files'` -> `forecast_cycle.nwp_fetch_failed_aborting`. **Zero forecasts from 484 good
   files.**

**How many duplicates it takes.** A single duplicated item does **not** abort the cycle: each STAC
item carries one variable, and `_parse_grib_files` isolates parameter groups, so one duplicate skips
only its own variable and the other still parses. Zero output requires duplicates spanning **both**
`PARAM_GROUPS` — which is exactly what 2026-09-03T12 had (step 35 duplicated for `t_2m` *and*
`tot_prec`). An earlier revision claimed one entry was enough; that was wrong, and it matters
because it dictates what T1's RED test must reproduce.

### Frequency

**3 of 9 scheduled cycles (~33 %)** in the log window 2026-09-01T18 → 2026-09-03T12: 09-01T18,
09-02T18, 09-03T12. Intermittent, **not** a fixed time of day — 12:00 succeeded on the 1st and 2nd
and failed on the 3rd, which fits an upstream listing artefact rather than anything on a schedule.

### Why the duplicate appears — NOT established, and not this plan's problem

Most likely the STAC pagination behaviour already on record for this catalogue (three traps
previously noted): an item appearing on two pages of the 120 h window walk. **Unverified** — the
listing was not captured at failure time. It does not matter for T1: whatever upstream does, being
handed the same asset twice must not destroy the cycle. If a later plan wants to chase the listing
itself, T1's new log line gives it the raw material.

## Where the alerting actually stands — correcting this plan's earlier claim

An earlier revision of this plan claimed the abort was invisible and that "nobody noticed". **That
was wrong**, and the owner's Slack history disproves it. The watchdog works and fires on precisely
the right condition:

```
[SAPPHIRE staging] forecast cycle stored ZERO forecasts — status: critical
[SAPPHIRE staging] forecast production RECOVERED
```

- **first burst** 2026-09-02T22:07Z → `RECOVERED` 2026-09-03T00:27Z, i.e. the moment the 00:00
  cycle stored forecasts;
- **second burst** from 2026-09-03T12:08Z, **still firing at 15:33Z** — it cannot recover until the
  18:00 cycle succeeds;
- **every ~30 minutes**: **8 alerts for one failed cycle**, 15 in the day.

So detection is not the gap. Two real defects remain:

1. **The flow run reports `COMPLETED`** while the watchdog calls the same event critical. Two
   subsystems disagree about whether a cycle that produced nothing succeeded. That contradiction is
   T2, and it outlives this particular bug: any future zero-forecast cause will again show green in
   Prefect.
2. **Alert repeat volume.** One incident becomes eight pages. With T1 landed the noise disappears
   *for this cause*, which is why it is an owner question below rather than a task — tuning a
   watchdog interval on the strength of a bug we are about to fix is the wrong order.

## Tasks

**T1 — De-duplicate the fetched asset list, and say what was dropped.**
*Scope (in):* de-duplicate the paths `_fetch_grib_files` returns, preserving first-seen order, so a
duplicated STAC asset cannot enter the parse twice. Emit one WARNING naming how many duplicates
were dropped and which asset keys/steps they were — this is the only record of how often upstream
repeats an item, and a later plan investigating the listing will need it. De-duplicate on the
identity that actually collides (the resolved local path and/or the asset key), **not** on the
signed `href`, whose query string carries a per-request signature and expiry and therefore differs
between two listings of the *same* asset.
*Scope (out):* retrying, re-fetching, changing pagination, the byte/file caps, the age guard, or
`_combine_cfgrib_datasets`' member contract.
*Exit:* a fetch handed a duplicated asset returns 484 paths, not 488, and the cycle parses.
*Verification:* `uv run pytest tests/unit/adapters/test_meteoswiss_nwp.py -q` — with a test whose
faked STAC listing repeats items **spanning BOTH `PARAM_GROUPS`** (as 2026-09-03T12 did), asserting
(a) the returned list has no duplicate paths, (b) the WARNING names the dropped assets, and (c) the
parse succeeds. A test duplicating a single item would **not** reproduce the whole-cycle abort — the
other variable survives and the cycle does not fail — so it must not be used as the RED case. A
second test must assert the collision guard raises when two different URL paths share a basename.
**RED first** against today's code, which must fail with the duplicate-`number` alignment error.

**T2 — The NWP-fetch abort path must not report COMPLETED.**
*Scope (in):* when the NWP fetch fails and the cycle aborts without writing any forecast, the flow
run must reach a non-successful terminal state, so Prefect agrees with the watchdog. Today
`_fetch_nwp_task` converts the adapter exception into a returned failure outcome
(`run_forecast_cycle.py:1340-1349`) and the flow returns `ForecastCycleResult(health=FAILED)`
*normally* (`:2667-2691`) — a domain-level FAILED inside a Prefect COMPLETED.
**Use the idiom this repo already has** — "emit CRITICAL exactly once, then re-raise"
(`run_forecast_cycle.py:1742-1771`, `:3512-3521`) — rather than inventing a mechanism. The
zero-forecast CRITICAL record is already written at `:2669-2680` and the watchdog probes that
independently of Prefect state (`ops/watchdog.py:1996-2024`), so the change must not double-report.

*Scope note — deliberately narrow:* this covers the **NWP-fetch abort path only**. Other zero-output
paths exist and are **out of scope**: no operational stations (`run_forecast_cycle.py:2384-2403`) and
an ordinary end-of-cycle zero-storage (`:3612-3658`) both still return normally. Widening to "every
zero-forecast cycle fails" is a bigger behavioural change and is not authorised here.
*Scope (out):* new alert channels, new health metrics, retry policy, redefining what a successful
forecast is, and the repeat-interval question below.
*Exit:* the abort path yields a non-successful terminal state, and the watchdog's existing signal is
unchanged (not duplicated).
*Verification:* `uv run pytest tests/unit/flows/test_run_forecast_cycle.py -q` — with a test
asserting the zero-forecast abort path does not end successfully. **RED first.**

## Open question for the owner — not guessed

**Should a zero-forecast cycle keep re-alerting every 30 minutes?** Today one failure produced eight
pages before the next cycle could clear it. Options: alert once per state transition (the watchdog
already emits a distinct `RECOVERED`, so the transition machinery exists); keep repeats but widen
the interval; or leave it, on the grounds that an unproductive forecast system *should* keep
shouting. This is an operational-signal judgement, it is **out of T2's scope**, and it should be
answered *after* T1 lands — the honest volume is unknown until the duplicate bug stops causing most
of it.

## Separately worth knowing (not in scope, do not fix here)

- The mini runs `nwp_cycle_min_age_minutes = 105`; `main` has `210` (Plan 213 — merged **and
  archived**, but undeployed on the mini). Real latent correctness, for the next deploy. It is
  **not** related to this failure: at an on-grid cron instant the two candidate cycles are ~0 and
  ~360 min old, so any guard value in (0, 360) decides identically.
- `nwp.cycle_fallback_used fallback_reason=too_recent` appears in every failing run and makes the
  guard look implicated. It is not — the fallback resolves correctly and the fetch of the fallback
  cycle then parses badly.
- This adapter has been broken by upstream ICON schema drift before (Plan 160). The
  `ctrl=0 / perturb=1..20` contract lives only in a docstring (`:259-272`), which is why the
  symptom surfaced as an opaque pandas message.
- A completeness probe (checking the items the fetch needs rather than inferring readiness from
  cycle age) is the recorded durable fix for Plan 213 D3c's failure mode, not for this one.
- **The 2026-09-02T18 incomplete cycle (473 of 484 files) is NOT addressed by this plan.** T1 removes
  the duplicate crash from it, but an 11-file-short cycle's behaviour is unmeasured — it may parse to
  a truncated forecast, or fail some other way. That is a real second fault, deliberately left for a
  separate plan rather than folded in here; the adapter source already records the 484-vs-501 gap as
  unexplained (`meteoswiss_nwp.py:73-80`). Do not let T1 grow to cover it.

## Dependency graph

```json
{
  "plan": 237,
  "tasks": [
    {"id": "T1", "depends_on": [], "parallel": true},
    {"id": "T2", "depends_on": [], "parallel": true}
  ]
}
```
