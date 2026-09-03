---
status: DRAFT
created: 2026-09-03
revised: 2026-09-03
plan: 237
title: One duplicated STAC asset aborts a whole forecast cycle — and the flow still reports COMPLETED
scope: Fix the identified cause (a duplicate STAC asset is appended twice to the GRIB file list, so the parse sees the same ensemble members twice and skips every parameter group) and stop a zero-forecast cycle reporting COMPLETED while the watchdog calls it critical. Two tasks. No retry logic, no completeness probe, no alert-channel work.
depends_on: []
blocks: []
source: Measured on the mac mini 2026-09-03 after 15 Slack alerts in one day; diagnosed to a duplicated STAC item, not a MeteoSwiss outage
---

# Plan 237 — a duplicated STAC asset aborts the cycle

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ Proportionality — two tasks, and the cause is KNOWN

An earlier revision of this plan said the cause was unidentified and authorised **observability
only**. That is superseded: the cause is now measured (below), so T1 is a real fix and it is small.

**Do not add:** retry-on-parse-failure, a re-fetch path, a completeness probe (that is Plan 213 D3c
for a *different* failure mode), STAC pagination rework, new alert channels, or a third task.
Reviewers: the temptation here is to harden the whole NWP fetch path. Don't. One duplicated list
entry is the bug.

## What is measured — this is NOT a MeteoSwiss outage

**The complete asset set for one ICON-CH2-EPS cycle is 484 files**: 121 hourly steps (0…120) x 2
allowlisted variables (`tot_prec`, `t_2m`) x 2 files each (`ctrl`, `perturb`).

| cycle (UTC) | files downloaded | distinct filenames | outcome |
|---|---|---|---|
| 2026-09-02T12 | 484 | 484 | ok |
| 2026-09-02T18 | **501** | — | **aborted** (17 surplus) |
| 2026-09-03T00 | 484 | 484 | ok |
| 2026-09-03T06 | 484 | 484 | ok |
| 2026-09-03T12 | **488** | **484** | **aborted** (4 surplus) |

The failing cycles downloaded **more** files than the successful ones, and the surplus is
**duplicates, not extra data**. For 2026-09-03T12 the duplicate is step **35**, returned twice for
all four file types:

```
2 icon-ch2-eps-202609030600-35-tot_prec-perturb.grib2
2 icon-ch2-eps-202609030600-35-tot_prec-ctrl.grib2
2 icon-ch2-eps-202609030600-35-t_2m-perturb.grib2
2 icon-ch2-eps-202609030600-35-t_2m-ctrl.grib2
   (every other step: 1)
```

488 downloads, 484 distinct files on disk. **Nothing from MeteoSwiss is missing.** The cycle's data
is complete; we were served one item twice.

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

**One duplicated list entry discards a complete cycle.** The blast radius is grossly out of
proportion to the fault.

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
faked STAC listing repeats one item across two pages, asserting (a) the returned list has no
duplicate paths, (b) the WARNING names the dropped asset, and (c) the parse succeeds. **RED first**
against today's code, which must fail with the duplicate-`number` alignment error — proving the test
reproduces the real fault rather than merely passing.

**T2 — A cycle that stores zero forecasts must not report COMPLETED.**
*Scope (in):* when the NWP fetch fails and the cycle aborts without writing any forecast, the flow
run must reach a non-successful terminal state, so Prefect agrees with the watchdog. Follow whatever
this repo already does elsewhere to signal an unproductive run rather than inventing a mechanism,
and check the pipeline-health path so the change does not double-report an event the watchdog
already covers.
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
