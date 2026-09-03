---
status: DRAFT
created: 2026-09-03
plan: 237
title: A third of forecast cycles abort on a GRIB parse error and report COMPLETED
scope: Make the existing failure diagnosable and visible. Two tasks: name the duplicate-member condition in the log and preserve the scratch dir when a parse fails, and stop a zero-output cycle reporting COMPLETED. NO fix for the underlying data anomaly is authorised here — it is not yet identified, and this plan exists so the next occurrence identifies it.
depends_on: []
blocks: []
source: Measured on the mac mini 2026-09-03 while diagnosing why 12:00/18:00 cycles finish in ~7 min
---

# Plan 237 — the NWP parse abort is invisible and undiagnosable

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ Proportionality — this plan does NOT fix the parse failure

The underlying data anomaly is **not identified**. One cheap probe was run and came back
*inconclusive* (below). It would be easy to write a speculative fix for the most plausible cause;
that is explicitly out of scope, because a wrong fix here silently degrades forecasts rather than
failing loudly.

**Two tasks. Both are observability.** Do not add a repair, a retry, a re-fetch, a de-duplication
pass, or a completeness probe. When the guard from T1 fires once with real files retained, the
cause will be known and *that* plan can fix it on evidence.

## What is measured

**Three of nine scheduled cycles aborted** producing zero forecasts (mac mini, log window
2026-09-01T18 → 2026-09-03T12):

| cycle | wall clock | outcome |
|---|---|---|
| 2026-09-01T18 | 6.0 min | **aborted** |
| 2026-09-02T00 / T06 / T12 | 10.7 / 12.0 / 12.7 min | ok |
| 2026-09-02T18 | 7.0 min | **aborted** |
| 2026-09-03T00 / T06 | 25.5 / 26.7 min | ok (148 stations) |
| 2026-09-03T12 | 7.0 min | **aborted** |

Intermittent, **not** a fixed time of day — 12:00 succeeded on the 1st and 2nd and failed on the
3rd. ~33 %, not the "roughly half" an earlier eyeball estimate suggested.

All three share one signature:

```
nwp.param_parse_skipped  error="cannot reindex or align along dimension 'number'
                                because the (pandas) index has duplicate values"  short_name=2t
nwp.fetch_failed         error='No parameter groups could be parsed from GRIB2 files'
forecast_cycle.nwp_fetch_failed_aborting
```

The **download succeeds**; the parse of what arrives fails, for both allowlisted variables, so
every parameter group is skipped and the cycle aborts.

### Not the NWP age guard, and not Plan 213

`nwp.cycle_fallback_used fallback_reason=too_recent` appears in the failing runs, which makes the
guard look implicated. It is not. At an on-grid cron instant the two candidate cycles are ~0 and
~360 min old, so **any** guard value in (0, 360) decides identically — Plan 213's own commit
message says exactly this about 105 vs 180, and it holds for the 210 now on `main`. The fallback
works; the parse of the fallback cycle is what fails.

*(Separately noted, not this plan's task: the mini still runs `nwp_cycle_min_age_minutes = 105`
while `main` has `210` — Plan 213 is merged and archived but **undeployed** there. Real latent
correctness, for the next deploy.)*

### The mechanism, as far as the code establishes it

`_combine_cfgrib_datasets` (`src/sapphire_flow/adapters/meteoswiss_nwp.py:275`) groups per-file
datasets by `valid_time` and concatenates along `number`. Its contract is a **docstring only**
(`:259-272`):

> Ctrl files expose `number` as a *scalar* coord (value 0). Perturb files expose `number` as a 1-D
> dim (length 20, values 1..20).

A duplicate `number` index can only arise if two files in one `valid_time` group claim the same
member. The pandas error is raised inside xarray's alignment, so **nothing in our logs names the
offending members, files, or valid_time** — only that alignment failed.

### The probe that did NOT settle it

Hypothesis tested: a perturb file carrying member 0, colliding with ctrl's 0. One perturb asset was
fetched from the live STAC catalogue and inspected:

```
icon-ch2-eps-202609021800-0-t_2m-perturb.grib2
number = [1 … 20]   count=20  min=1  max=20   contains 0 -> no
```

**Contract held.** The hypothesis is neither confirmed nor excluded: the sample was **1 step of
~121**, and not from a failing fetch (a failing run fetches the *fallback* cycle). Recorded so
nobody re-runs it expecting an answer.

🪤 Also seen and unexplained: the same probe found **no matching `t_2m` perturb asset** for the
2026-09-03T00 and T06 cycles within 6 STAC pages. That may be the probe's own pagination limit or a
real listing gap — three STAC pagination traps are already on record for this catalogue. Not
investigated. Do not treat it as a finding.

### Why nobody noticed

A cycle that produces **zero forecasts** reports **`COMPLETED`** at the flow level. Only the
`error`-level log lines reveal the abort. `forecast_cycle.nwp_fetch_failed_aborting` is logged and
then the flow returns normally, so no alert, no failed run, nothing in the Prefect UI to see. Three
aborted cycles across three days went unremarked until someone read the durations.

## Tasks

**T1 — Name the condition, and keep the evidence.** *(Observability only; no repair.)*
*Scope (in):* before concatenating a `valid_time` group in `_combine_cfgrib_datasets`, detect a
duplicate `number` across the group's datasets and raise/log a **named, specific** error carrying:
the offending member value(s), the `valid_time`, the contributing file paths, and each dataset's
`number` values. Separately, when a fetch fails with no parameter groups parsed, **retain the
scratch directory** (currently wiped — `/tmp/sapphire_nwp/` was empty within seconds of the
2026-09-03T12 failure, which is why this plan cannot say what went wrong) and log its path.
Retention must be bounded so a repeated failure cannot fill the disk.
*Scope (out):* de-duplicating, repairing, re-fetching, retrying, dropping the offending member, or
touching the age guard. If the guard finds a duplicate the fetch still fails — **loudly and
legibly** instead of opaquely.
*Verification:* `uv run pytest tests/unit/adapters/test_meteoswiss_nwp.py -q` — with a new test that
feeds `_combine_cfgrib_datasets` a ctrl(0) + perturb(0..20) group and asserts the named error names
the duplicate member and the files. Must be RED against today's code (which raises the opaque
pandas message).

**T2 — Stop a zero-output cycle reporting COMPLETED.** *(No change to forecast logic.)*
*Scope (in):* when the NWP fetch fails and the cycle aborts with no forecasts written, the flow run
must not end in `COMPLETED`. Decide between raising (Prefect marks it `FAILED`) and an explicit
`Failed`/`Crashed` state return, consistent with how other flows in this repo signal an
unproductive run — and with the pipeline-health/watchdog path, so the existing alerting notices.
*Scope (out):* new alert channels, new health metrics, changing what counts as a successful
forecast, retry policy.
*Verification:* `uv run pytest tests/unit/flows/test_run_forecast_cycle.py -q` — with a test
asserting the abort path does not yield a successful terminal state. RED first.

## Open question for the owner (T2)

**Is an aborted cycle a FAILURE or an expected no-op?** If MeteoSwiss data is legitimately absent,
`FAILED` four times a day is alert noise and the watchdog will cry wolf. If it is a defect — which
33 % with a parse error suggests — silent COMPLETED is worse. The answer decides T2's shape, and it
is a judgement about operational signal, not a code question.

## Separately worth knowing (not in scope)

- The mini runs `nwp_cycle_min_age_minutes = 105`; `main` has `210` (Plan 213, merged + archived,
  undeployed there). Next deploy.
- This adapter has been broken by upstream ICON schema drift before (Plan 160). The `1..20`
  contract living only in a docstring is why the second occurrence is this hard to read.
- A completeness probe — checking the items the fetch actually needs instead of inferring readiness
  from cycle age — is the recorded durable fix for a *different* failure mode (Plan 213 D3c). It is
  not this.

## Dependency graph

```json
{
  "plan": 237,
  "tasks": [
    {"id": "T1", "depends_on": [], "parallel": false},
    {"id": "T2", "depends_on": [], "parallel": true}
  ]
}
```
