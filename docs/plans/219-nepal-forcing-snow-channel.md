---
status: DRAFT
created: 2026-08-31
plan: 219
title: Add the snow channel to the 12300 operational fetch test
scope: Extend the Plan 192 Stage B feed (scripts/nepal_forcing_run.py + its launchd wrapper) to also fetch and store the Gateway snow forecast for HRU 12300. Adapter fetch code already exists; this wires it into the test feed and settles units, health thresholds and partial-success semantics. No change to the Swiss stack or to the production forecast cycle.
depends_on: [192]
blocks: []
source: 2026-08-31 — owner asked to cover snow in the operational fetch test after a measured sweep showed the Gateway serves hs/rof/swe for 12300 (241 rows each) while our feed stores only precipitation + temperature
---

# Plan 219 — the 12300 fetch test ignores the snow channel

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ DO NOT OVER-ENGINEER — a binding constraint on this plan AND on every reviewer of it

**Owner directive, 2026-08-31, stated directly: do not over-engineer this.** This section binds every
review agent (Claude and Codex alike). It is not advisory framing — a review that violates it has
failed, however well-argued its suggestions are.

The adapter can **already** fetch snow (`fetch_snow_forecast`, Plan 145). This plan wires an existing
capability into an existing **one-station test feed**. It is a **script + config change**, not a
subsystem. The deliverable is roughly: one extra call in `scripts/nepal_forcing_run.py`, per-channel
health expectations, an additive JSONL key set, tests, and a runbook paragraph.

**Rules binding every reviewer:**

1. **"No findings" is a complete and welcome review.** Returning zero findings is a success, not a
   failure to try. Do not manufacture findings to justify the round.
2. **A finding must name a CONCRETE FAILURE** with `file:line` and the input/state that triggers it.
   "Consider extracting…", "for consistency…", "it would be cleaner…" are **not** findings.
3. **Do not propose new apparatus.** No snow subsystem, no abstraction layer over the two channels,
   no per-HRU subscription config, no new store or table, no model onboarding, no registry, no
   feature-flag framework, no retry/backoff engine. D2 is a *documented divergence*, **not** an
   invitation to build the gate it diverges from.
4. **Do not widen scope.** The production forecast cycle, Swiss stations, snow reanalysis, and
   backfilling the 9 stored cycles are all in § Deferred and must stay there. Proposing them is a
   scope violation, not a finding.
5. **Adding length is a cost.** A longer plan is a worse plan unless the added lines remove a
   concrete failure. Do not restructure prose you merely dislike.
6. **Prefer deleting to adding.** If a task, gate or decision here is not load-bearing, say so —
   cutting is the most valuable finding this review can produce.

**The one thing worth being hard on:** D1 (unconfirmed snow units). Silently-wrong stored values are
the real risk in this plan. Scrutiny spent there is well spent; scrutiny spent on structure is not.

## What we measured (2026-08-31, not inferred)

**The Gateway serves snow for 12300.** Same client, same key, same deployed image as the feed:

| run_date | `hs` | `rof` | `swe` |
|---|---|---|---|
| 2026-08-25 | OK 241 rows | OK 241 | OK 241 |
| 2026-08-28 | OK 241 rows | OK 241 | OK 241 |
| 2026-08-29 → 08-31 | ✗ no dataset | ✗ | ✗ |

**We store none of it.** The 12300 store holds exactly two parameters — `precipitation` 38,556 rows
and `temperature` 38,556 rows across 9 cycles. `scripts/nepal_forcing_run.py` calls
`_fetch_nwp_task.fn(...)`, which routes to `fetch_forecasts` (the IFS path) and never reaches
`fetch_snow_forecast`.

(The 08-29+ blanks are the concurrent Gateway publication stall — IFS *and* JSNOW both stopped after
2026-08-28. That is not this plan's problem, but it **gates this plan's live verification**; see § Exit.)

## The five real constraints

### D1 — 🔴 BLOCKING: snow units are UNCONFIRMED, and the code says so

`RECAP_VARIABLES` declares `snow_depth` (cm), `snowmelt` (mm) and `swe` (mm) with **`convert=None`**
(`src/sapphire_flow/adapters/recap_gateway.py:93-110`). That is a deliberate sentinel, not an
oversight — the docstring states the Gateway source-unit magnitudes are UNCONFIRMED and defers the
factor to a Plan 082 live-smoke item **that was never done**.

**Storing snow values before this is settled writes silently-wrong numbers into
`weather_forecasts.value`** — the worst failure mode available here, because it is invisible and
persists. T1 therefore *measures* the magnitudes before any storing task is written.

**This plan must not "just set convert=1.0" to get moving.** If the sweep cannot ground the units,
the honest outcome is to store nothing and report the gap upstream (§ Open Q1).

### D2 — the production snow trigger is model-driven; this feed has no model

The forecast cycle decides whether to use the snow channel by intersecting a model's
`future_dynamic_features` with `SNOW_CANONICAL_PARAMETERS` (`recap_gateway.py:113-120`, Plan 145
D3.1) — there is no per-HRU "JSNOW subscribed" flag. Stage B is deliberately model-less (Plan 192),
so the feed must call `fetch_snow_forecast` **explicitly**.

That is a **deliberate divergence from the production path and must be documented as such** in the
runbook, so nobody later reads this feed as evidence that the production gate works.

### D3 — snow is deterministic; IFS is a 51-member ensemble

`SnowApi.forecast` takes no `ifs_type`/`member` argument — snow rows carry **`member_id = NULL`**.
The store already supports this: `uq_weather_forecasts_natural_key` COALESCEs `member_id` to `-1`.

Consequence: the feed's health assertions are IFS-shaped and **must not be applied to snow**.
`members=51` and `_MIN_EXPECTED_HORIZON_DAYS = 14.0` (`scripts/nepal_forcing_run.py:51`) are
IFS expectations; snow returned 241 rows/variable, consistent with a shorter, finer-resolution
horizon. **Per-channel expectations, measured in T1 — not guessed.**

### D4 — partial success must not blind the dead-man

Today `ok=false` ⇒ the wrapper exits 1 ⇒ Healthchecks `/fail`. With two channels there are four
outcomes, and the naive `ok = ifs_ok and snow_ok` **regresses monitoring**: a snow gap would page as
loudly as a total IFS outage, and after a few false alarms the check stops being believed.

Proposed (owner confirms — § Open Q2):

| IFS | snow | `ok` | exit | rationale |
|---|---|---|---|---|
| ok | ok | true | 0 | nominal |
| ok | missing | **true** | 0 | record `snow_degraded_reason`; IFS is the feed's purpose |
| missing | ok | false | 1 | unchanged from today |
| missing | missing | false | 1 | unchanged from today |

The JSONL record carries per-channel detail either way, so a snow-only gap is still *visible* in the
log and in the Healthchecks body — just not *paging*.

### D5 — the JSONL record is a consumed contract

The record is read by the analysis and is posted verbatim as the Healthchecks ping body (PR #224).
**Extend it additively** — keep every existing key with its current meaning, add `snow_*` alongside.
Do not restructure `rows`/`members`/`parameters` to mean "both channels"; that silently changes the
meaning of 12 historical records.

## Open questions the owner owns

- **Q1 (blocks T2):** if T1 cannot ground the snow units against a reference, do we (a) store raw
  Gateway values with `unit="unknown"` and a provenance note, (b) store nothing and file a Gateway
  issue, or (c) pause the plan? **Recommendation: (b)** — consistent with the FI/adherence rule that
  we fix the contract rather than paper over it.
- **Q2:** confirm the D4 severity table, in particular that a snow-only gap does **not** page.
- **Q3:** all three variables (`hs`/`rof`/`swe`) or only the two believed subscribed (`hs`+`rof`)?
  The sweep returned all three, so subscription is not the limiter it was once assumed to be.

## Phases

### T1 — ground the units and measure the shape (read-only, no storing)

Extend the throwaway sweep into `scripts/` as a real probe run against 12300 for `hs`/`rof`/`swe`:
record value ranges, row counts, step spacing, horizon, and null/member structure. Compare magnitudes
against an independent reference for the basin to ground D1. **Output is a measurement note in this
plan, not code that stores anything.** Settles D1 and D3.

### T2 — fetch + store the snow channel (gated on Q1)

`scripts/nepal_forcing_run.py`: call `fetch_snow_forecast` after the IFS fetch, store via the same
`WeatherForecastStore`, apply the D4 severity table, extend the JSONL additively per D5. Set the
`convert` factors established in T1.

### T3 — tests

Red-first. Cover: snow-missing-while-IFS-ok ⇒ `ok=true`/exit 0 (the D4 regression guard);
IFS-missing ⇒ exit 1 unchanged; JSONL keeps every legacy key; `member_id IS NULL` rows upsert
idempotently under the natural-key index (re-run stores no duplicates).

### T4 — docs

`docs/operations/nepal-forcing-runbook.md`: the snow channel, the D2 divergence, the D4 table, the
new JSONL keys, and per-channel healthy-record shapes.

```json
{
  "phases": [
    {"id": "T1", "parallel": false, "depends_on": []},
    {"id": "T2", "parallel": false, "depends_on": ["T1"]},
    {"id": "T3", "parallel": false, "depends_on": ["T2"]},
    {"id": "T4", "parallel": true,  "depends_on": ["T2"]}
  ]
}
```

## Exit gates

1. T1's measurement note is in this plan, with the grounded `convert` factors (or Q1 answered (b)/(c)).
2. A live run stores snow rows for 12300 and a **re-run stores zero additional rows** (idempotency,
   as proven for IFS at 77,112).
3. A forced snow-only gap leaves the dead-man green; a forced IFS gap turns it red.
4. Runbook updated; unit suite green.

**⏳ Gate 2 is blocked while the Gateway publication stall lasts** — no snow cycle has existed since
2026-08-28. Do not start T2 expecting to verify it the same day; T1 can proceed against the
still-served 08-25..08-28 cycles, which is exactly why T1 is separated out.

## Deferred (explicitly not this plan)

Snow in the **production** forecast cycle or for Swiss stations; a per-HRU subscription config; snow
reanalysis (`fetch_snow_reanalysis`, Plan 146) — the ask was the *operational fetch* test; any model
consuming snow features; backfilling snow for the 9 cycles already stored.
