---
status: DRAFT
created: 2026-07-14
plan: 116
title: Silent-success detection — flows must assert they did work
scope: Green flows that do nothing. Re-scoped 2026-07-14 after its original host-reliability premise was falsified; RE-SCOPED AGAIN 2026-08-17 to a single buildable slice — the forecast cycle must report unhealthy when it stores no forecasts — with the general audit demoted to a follow-on. The Plan 158 branch's T1b is the salvageable implementation.
depends_on: []
blocks: []
---

# Plan 116 — Silent-success detection

## ⚠️ Re-scoped 2026-08-17 — read this before the July text below

**This plan sat DRAFT for a month while the exact failures it predicted happened twice.** It is now cut down to
**one buildable slice**, because the general "audit every deployment" framing is why it never got built.

**BUILD SCOPE: the forecast cycle must report unhealthy when it produced nothing.** Everything else in this
document — the full deployment audit, Flow 6 (owned by 115b), the watchdog items — is **follow-on**, retained
below as the reasoning.

**§4 is now FALSIFIED and DELEGATED.** It reads: *"the watchdog cannot watch itself … a **theoretical** flaw,
not an evidenced one: the host has 22 days of uptime and has never actually failed. **Priority: low.**"* Since
then the host failed on **29 July** (14 days undetected) and the watchdog itself died again on **16 August**. It
is now evidenced, and already answered: **Plan 163** (dead-man's switch, merged) and **Plan 164** (LaunchDaemon,
READY). Nothing about the watchdog remains in scope here.

**§5 is still true and still five minutes' work** — the mini's randomized Private Wi-Fi Address means its DHCP
address drifts. Unrelated to this slice; just do it.

**Its closing warning stands and is now doubly earned:** *"Do not over-correct into infrastructure we have no
evidence of needing."* That is exactly why this re-scope removes rather than adds.

## The slice — forecast production freshness

**The gap, corrected 2026-08-17 after review.** The watchdog checks API health, backup staleness and the two
**BAFU collector** freshness checks. **No forecast-production heartbeat is consumed by the watchdog**, so if
`run_forecast_cycle` stopped producing, the API would stay healthy, BAFU collectors would keep running, backups
would succeed, the dead-man would keep pinging — and the product would be silently dead.

*My earlier description of this gap was wrong and is corrected here:* I claimed `grep forecast_freshness src/`
returns nothing on main. **It does not** — `PipelineCheckType.FORECAST_FRESHNESS` **already exists**
(`types/enums.py:172`). I had grepped two specific files and generalised to the whole tree without running that
command. **Only the emitter and the watchdog probe are missing**, which makes this slice smaller than stated.

**Salvage, do not rewrite.** The implementation exists on the `docs/plan-158-session-independence` branch (T1b),
built and reviewed, but stranded **82** commits behind: `PipelineCheckType.FORECAST_FRESHNESS` (`types/enums.py:154`),
the emitter in `flows/run_forecast_cycle.py`, and the watchdog probe. Port it onto current `main` the way Plan
163 ported the dead-man ping — and **fit the watchdog side into Plan 162/163's current notification handling
rather than copying it wholesale**, since both rewrote that file after the branch forked.

**⛔ Port the SMALL version. The 158 branch's coverage ledger is NOT in scope.** Its
`(station, model, parameter)` identity tracking drew **five blockers across two reviews** and is the single
biggest source of complexity in that branch. The minimal contract is:

> A cycle that persisted **zero** forecasts must **not** report healthy.

That closes the observed failure — three days of green while the feed was dark — without the identity ledger.
Per-product completeness is a **separate, later decision**, and if it is ever taken it should get a
remove-mandate review *first*, not after three rounds of additions.

**Two things from the 158 work that must come along**, because they were review findings, not decoration:
- **`FORECAST_FRESHNESS` is a SEPARATE contract from `ForecastCycleHealth`.** Existing tests deliberately lock
  `DEGRADED` for snow loss, partial NWP and fallback drift *even when forecasts were stored*; mapping those onto
  a freshness alarm would turn every degraded-but-working cycle into a page.
- **⛔ Backfill/replay runs must NOT emit the heartbeat at all** (corrected — "judge it on the cycle's own time"
  was **not sufficient**). `PipelineHealthStore.fetch_recent` orders by **`checked_at`**, not `cycle_time`
  (`store/pipeline_health_store.py:33`), and the probe asks for `limit=1`. So a backfill written *now* becomes
  the "latest" record and would report **stale** even while current production is perfectly healthy — a false
  alarm manufactured by a maintenance task. Scheduled/current cycles still age by their own `cycle_time`; runs
  with an explicit `cycle_time` simply do not emit this record.

**⛔ Acceptance — REPLACED. The July version is stale and would prove nothing.** I praised it as "better than
anything I would have written"; it is also **invalid on current main**. There is no separate `-nwp` overlay any
more — NWP lives in the consolidated Mac-mini overlay with `SAPPHIRE_REQUIRE_NWP: "1"`
(`docker-compose.macmini.yml:26`), so disabling it either trips the configuration guard or permits a
runoff-only fallback. **Zero forecasts is not guaranteed**, so the test could pass or fail for reasons unrelated
to this check.

**Replacement:** induce a **total `store_forecast` failure**, then assert (a) a **fresh `CRITICAL`
`FORECAST_FRESHNESS` record** is written, and (b) **the watchdog treats it as failed**. Note precisely what must
go red: **the watchdog signal** — not Prefect's flow state, and not `ForecastCycleHealth`.

## Follow-ons (explicitly out of the build slice)

The general per-deployment audit (§3), `ingest-observations` poll-starvation, the BAFU collector and NWP prune
checks. Worth doing; not worth blocking this slice on.


> ## ⚠️ This plan was originally "Staging host reliability". That premise was FALSE and is withdrawn.
>
> **What it claimed:** the mac-mini staging host slept, silently dropped off the network, took the
> scheduled flows down with it, and nobody noticed — with power management and an off-host heartbeat
> as the fix.
>
> **What the host actually reports (2026-07-14, once it could finally be reached):**
>
> ```
> 15:13  up 22 days,  4:21
> sapphire_flow-api-1               Up 27 hours (healthy)
> sapphire_flow-postgres-1          Up 27 hours (healthy)
> sapphire_flow-prefect-server-1    Up 27 hours (healthy)
> sapphire_flow-prefect-worker-1    Up 27 hours
> ...
> ```
>
> **22 days of uptime. Every container healthy.** The host never slept, never left the network, and
> never stopped serving. It was fine the entire time.
>
> **The machine that was broken was the one doing the investigating.** A macOS Tahoe 26.5.2 update
> (13.07.2026, the evening after SSH was set up) reset the **Local Network privacy permission** for the
> terminal app, so *this* machine's LAN traffic was silently dropped — gateway still reachable,
> printing still working (system services are exempt), every LAN peer invisible. Every "the host is
> unreachable" observation was an artefact of that. Three successive theories (asleep → Wi-Fi client
> isolation → a Cisco socket filter) were each built on an instrument nobody had validated.
>
> **The control that would have caught it in one minute: "can I reach ANY peer at all?"** One ping at
> the printer. It was never run. A blind observer and a dead host produce identical evidence.
>
> Power management, wake-on-LAN, auto-restart and the off-host heartbeat are therefore **solutions to a
> problem that does not exist**, and are withdrawn. This plan is re-scoped to the failure that is
> *actually* real — and it is real, and it is worse.

## The real problem

**Flows report success while doing nothing, and this has been happening in production, undetected,
for the entire life of the deployment.** Three independent instances, all confirmed:

1. **Flow 6 has never ingested a single row.** The audit (Plan 115, 2026-07-14) found
   `historical_forcing` holds exactly one source — `camels-ch`, frozen at **2020-12-31**. The
   scheduled `ingest-weather-history` deployment matches **zero** stations, logs
   `weather_history.no_stations`, returns `0/0/0` — and reports **SUCCESS**
   (`ingest_weather_history.py:309`). It has done this every day since it shipped. Nobody knew.
2. **Plan 100 — the 3-day NWP blackout.** A launchd restart silently dropped the `-nwp` overlay. NWP
   went off, the forecast feed went dark, and **every flow stayed green** for three days.
3. **The reanalysis rows are unreadable anyway** — written under product tags, read by binding name
   (Plan 115b). Even a working feed would have produced nothing a consumer could see.

The common shape: **a flow that runs, does no work, and cannot tell the difference.** Success is
being inferred from *"the code executed without raising"* rather than from *"the thing it exists to
do actually happened."*

This is a far better-evidenced problem than the host-reliability story it replaces — and unlike that
story, it has already cost us: a forcing archive five years stale, and a three-day forecast blackout.

## Objective

**No scheduled flow may report success without asserting that it did work.** Absence of an exception
is not evidence of an effect.

## Scope

### 1. The principle: assert the effect, not the execution

Every scheduled flow declares what a *successful run must have produced*, and fails — loudly, and
into pipeline monitoring — when it did not. Concretely, for each flow:

- What is the minimum work a healthy run performs? (rows ingested, forecasts stored, cycles fetched)
- What does **zero** mean? Is it a legitimate quiet period, or is it a fault?
- If zero can ever be legitimate, how do we distinguish the two? *(For Flow 6: "no stations bound to
  this feed" is **always** a fault. "Bound, but no new rows this window" may be legitimate — but not
  for 60 days running.)*

### 2. Flow 6 is the worked example — owned by 115b

`115b §4` already specifies it: a `WEATHER_HISTORY_INGEST` check type, `pipeline_health_store` threaded
into the flow, UNHEALTHY when `stations_targeted == 0` (a configuration fault — the feed *cannot* be
working) and when `stations_targeted > 0 and rows_stored == 0` over a full window (bound, but silent).

**This plan generalises that pattern to every scheduled flow.** Do not duplicate 115b; extend it.

### 3. Audit every scheduled deployment for the same disease

For each registered deployment (`cli/register_deployments.py`), ask the two questions above and record
the answer. The known-suspicious ones:

- `ingest-weather-history` — **confirmed dead** (115b owns the fix).
- `run-forecast-cycle` — Plan 100's blackout: NWP off, still green. Does a cycle that produces zero
  forecasts, or that silently falls back to runoff-only, report healthy? **It did for three days.**
- `ingest-observations` — Plan 098's poll-starvation: the worker existed but picked up no work.
  "Process alive" was not "work happening".
- The BAFU collector, the NWP archive prune, the watchdog itself.

### 4. The watchdog cannot watch itself

`ch.hydrosolutions.sapphire-watchdog` runs **on the mini, as a LaunchAgent**, i.e. inside the failure
domain it monitors. That remains a genuine design flaw — but note it is now a *theoretical* one, not an
evidenced one: the host has 22 days of uptime and has never actually failed. **Priority accordingly:
low.** Fix it if it is cheap; do not build a monitoring cathedral for a host that has never gone down.

*(The tempting lesson from today was "the host is fragile." The true lesson is "we could not see it."
Do not over-correct into infrastructure we have no evidence of needing.)*

### 5. Minor, real, cheap

The mini's Wi-Fi MAC is a **randomized Private Wi-Fi Address** (`9a:a3:…`). A rotating MAC means a DHCP
reservation cannot hold, so its address drifts and the runbook's SSH address goes stale — which it has.
Disable Private Wi-Fi Address for that network, and add a DHCP reservation. Five minutes; no plan
needed; just do it.

## Verification

The exit gate is an **induced silent failure** — not a passing test suite:

- Unbind every station from a feed → the flow must go **red**, not green-with-zeros.
- Drop the `-nwp` overlay (the Plan 100 regression) → the forecast cycle must go **red**. If it stays
  green, this plan has failed its only purpose.
- Point a flow at an empty source → red.

Each check must be one that would have caught a **real, already-observed** incident. A check that
cannot fail is not a check.

## Relationship to other plans

- **115b §4** — owns the Flow 6 instance. This plan generalises it; it does not duplicate it.
- **Plan 100** — the NWP blackout. Its detection gap is instance #2 here.
- **Plan 098** — poll-starvation. The reason "the process is running" is not a health signal.
- **Plan 091** — stale; claims `mac-mini.toml` disables NWP (it does not, `mac-mini.toml:10`).

## Open question (owner)

Is this plan worth its own build, or should the principle simply be folded into **115b** (which already
implements it for Flow 6) plus a checklist item on each future flow? **The evidence supports the
principle strongly; it does not obviously support a large standalone build.** Given how much
speculative scope the original version of this plan accumulated on a false premise, the honest
recommendation is: **start with 115b, then re-evaluate whether anything is left.**
