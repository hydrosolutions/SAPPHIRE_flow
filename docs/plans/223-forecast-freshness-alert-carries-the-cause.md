---
status: DRAFT
created: 2026-08-31
plan: 223
title: The forecast-freshness alert says WHAT failed but never WHY
scope: A sanitised reason string threaded from the forecast-cycle failure paths into the existing FORECAST_FRESHNESS detail dict, and rendered in the watchdog's alert text. No schema change, no change to alert cadence or hysteresis.
depends_on: []
blocks: []
source: 2026-08-31 — ~50 identical CRITICAL Slack alerts over two days never carried the cause ("GRIB file count exceeded: 501 > 500"), so a systematic outage read as flapping
---

# Plan 223 — fifty alerts, none of them actionable

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ Proportionality is a binding constraint on this plan AND on its review

This adds **one optional string** to a dict that already exists, and renders it. It changes no
schema, no cadence, no hysteresis, and no alert channel.

**Rules binding every reviewer:**

1. **"No findings" is a complete and welcome review.** Do not manufacture findings.
2. **A finding must name a CONCRETE FAILURE** with `file:line`.
3. **Do not propose new apparatus** — no new check type, no new alert channel, no structured-event
   pipeline, no dedup service.
4. **Do not propose changing the alert cadence.** § D4 settles it.
5. **Adding length is a cost.**

## The defect — measured against real alerts

The watchdog **worked**. It sent ~50 CRITICAL alerts between 2026-08-29 22:01 and 2026-08-31 09:36.
Every one read:

```
[SAPPHIRE staging] forecast cycle stored ZERO forecasts — host: sapphire-staging.home,
time: …, status: critical
```

**The cause was never in them.** The real fault — `GRIB file count exceeded: 501 > 500`
(`adapters/meteoswiss_nwp.py:806`, message at `:807-809`) — existed only in the worker's container log. Diagnosing it
required SSH access to a host that is only reachable from one LAN.

`_emit_forecast_freshness_record` (`flows/run_forecast_cycle.py:706-714`) accepts
`cycle_time_param`, `resolved_cycle_time`, `forecasts_stored`, `checked_at`, `force_critical` —
**no reason parameter exists**, so no cause can reach the alert even in principle.

**Why that mattered more than the missing text suggests.** Three RECOVERED messages were
interleaved (08-30 06:12, 08-30 18:13, 08-31 09:56) because roughly half of cycles succeeded. The
resulting shape — critical, recovered, critical, recovered — reads as *transient flapping*. The
reality was a systematic ~50 % loss of forecasts lasting 36 hours. **A cause string is what
distinguishes those two readings**, and no amount of alert tuning substitutes for it.

## ⛔ BLOCKER found in review — the cause is thrown away one frame too early

`_fetch_nwp_task` ends with:

```python
except Exception as exc:
    log.error("nwp.fetch_failed", error=str(exc))
    return None
```

(`flows/run_forecast_cycle.py:1299-1301`). The abort site tests `if nwp_outcome is None`
(`:2615`) and therefore **knows only that NWP failed, never why**. T1 as first written was
unimplementable: there is no cause at the call site to pass.

**D5 settles it — the reason must survive that boundary, and the boundary is where it gets
constructed.** The `except` block is the only place that still holds the exception, so it is where
the safe code is derived (D2) and attached. Two shapes are acceptable; **the implementer picks one
and does not invent a third:**

1. Extend the existing `_NwpFetchOutcome` (`:232`) with an optional failure reason and return it
   instead of bare `None`, updating the `is None` test at `:2615` accordingly; or
2. Return `tuple[_NwpFetchOutcome | None, str | None]` from the task.

(1) is preferred — the type already exists and already carries outcome flags
(`nwp_unavailable`). **Whichever is chosen, no raw exception object may cross the boundary**, only
the constructed string.

**⛔ `None` is a CROSS-FILE contract — a second review caught a consumer outside this flow.**
`scripts/nepal_forcing_run.py:239` does `if outcome is None: raise RuntimeError("NWP fetch returned
None (flow-fatal condition)")`. Under option (1) that check silently goes false, so **the
unattended Nepal forcing job would treat a fatal fetch as a completed one** and classify existing
rows as success. T1 **must** update it. Full consumer list for option (1):

  - `run_forecast_cycle.py:2598` — the success-only pruning gate
  - `run_forecast_cycle.py:2615` — the fatal predicate
  - `scripts/nepal_forcing_run.py:239` — the Nepal job's fatal check

The `nwp_unavailable` outcomes (`:1203`, `:1235`) must keep flowing through **without** aborting —
they are a deliberate non-fatal path, not a failure. Downstream accesses at `:2643-2682` and
`:3564-3570` are safe once the fatal predicate returns early.

## Decisions

- **D1 — Reuse the `detail` dict; no schema change.** The record already writes
  `detail={"forecasts_stored": …}` (`run_forecast_cycle.py:760`) into JSONB, and the watchdog
  already parses that detail to obtain `forecasts_stored`. A `reason` key rides the same path. If a
  reviewer proposes a new column, check they have read this decision.

- **D2 — The reason MUST be sanitised, and this is a security requirement, not tidiness.**
  MeteoSwiss download hrefs are **presigned URLs carrying `AWSAccessKeyId` and `Signature`** (seen
  in `nwp.file_downloaded` log output), and the adapter wraps arbitrary exceptions verbatim —
  `raise AdapterError(f"NWP fetch failed: {exc}")` (`meteoswiss_nwp.py:670`), likewise `:580`. **A
  raw exception string could therefore publish working credentials into Slack and into the
  database.** The reason written to `detail` must be a **bounded, sanitised** string: no URLs, no
  query strings, length-capped. Prefer a short stable phrase plus safe numbers — e.g.
  `"nwp_fetch_failed: GRIB file count exceeded: 501 > 500"` — over `str(exc)`.
  **Construct the reason; do not filter one.** An earlier independent review reached this
  conclusion for DSNs and it generalises: *"do NOT log any part of the DSN —
  `split("@")[-1]` strips a userinfo password but leaks a query-parameter one (demonstrated)"*
  (`scripts/import_caravan_attributes.py:374-378`). Filtering an arbitrary string requires
  anticipating every leak vector, and upstream error text changes without notice. **This file
  already does it correctly**: `detail={"reason": "source_data_missing"}`
  (`flows/run_forecast_cycle.py:1293`) is a stable code the code itself chose. Follow that —
  map known failures to short constructed phrases plus numbers we computed, and **never** pass
  `str(exc)` through. There is no reusable sanitiser in the repo to lean on (checked), which is
  another reason not to need one.

  **A test must prove a URL-bearing exception cannot reach the alert or the stored detail.**

- **D3 — Only the failure paths carry a reason.** There are five call sites
  (`run_forecast_cycle.py:1715, 2338, 2617, 3448, 3548`). Normal completion passes nothing and its
  alert text is unchanged; a successful cycle must not grow a spurious `reason: null`.

- **D4 — Do NOT touch the hysteresis. It is correct.** `should_alert_health`
  (`ops/watchdog.py:1107-1119`) alerts on the 1st failure, then every 6th consecutive failure, plus
  recovery; the launchd agent ticks every 300 s
  (`scripts/launchd/ch.hydrosolutions.sapphire-watchdog.plist`), giving the observed 30-minute
  cadence. The volume was not the defect — **the emptiness of each message was.** Changing cadence
  here would trade a diagnosable outage for a quieter undiagnosable one.

- **D6 — the reason can only be COARSE unless the exception gains fields. Owner decision.**
  Both budget guards raise the same bare type: the byte cap (`meteoswiss_nwp.py:790`) and the
  file-count cap (`:806`) both raise `BudgetExceededError`, which carries **no structured fields**
  (`exceptions.py:42-43`). Under D2's construct-don't-filter rule the `except` block can therefore
  build only something like `"nwp_budget_exceeded"` — it **cannot** say *which* budget, and cannot
  recover `501 > 500`, without parsing `str(exc)` (forbidden) or changing the adapter.

  **That matters, because the coarse code would not have solved this outage.**
  `nwp_budget_exceeded` does not distinguish the file cap from the byte cap, so an operator would
  still have needed SSH to learn which — the exact failure this plan exists to remove.

  Two options, **owner picks**:
  1. **Accept the coarse code.** Zero adapter change, and still a large improvement on today's
     empty alert.
  2. **Add structured fields to `BudgetExceededError`** (e.g. `kind`, `observed`, `limit`) and
     construct the reason from them. Small and contained — one exception class plus two raise
     sites — and it makes the alert say `nwp_file_count_exceeded: 501 > 500` safely, because every
     component is a value we computed rather than text we parsed.

  **Recommended: (2)**, on the grounds that (1) leaves the diagnosis one SSH short. It widens scope
  beyond "one optional string", so it is explicitly the owner's call, not the implementer's.

## Task

**T1 — thread a sanitised cause into the record and the alert.**
*In:* an optional `reason` on `_emit_forecast_freshness_record`, written into `detail` (D1);
the NWP-abort call site (`:2617`) passing a constructed cause (D2), which requires D5's
boundary change in `_fetch_nwp_task` (`:1299-1301`); `probe_forecast_freshness`
(`ops/watchdog.py:653`) parsing it; and `_format_forecast_freshness_critical_alert`
(`ops/watchdog.py:1437-1443`) appending it when present.
*Out:* the other four call sites unless trivially free · alert cadence or hysteresis · any new
check type or channel · the GRIB cap itself (**Plan 221**) · the Prefect run reporting COMPLETED
while writing nothing.
*Exit:* an alert for an NWP-abort cycle contains the cause; a normal cycle's text is byte-identical
to today's; **a test proves a URL-bearing exception is sanitised out**; missing/legacy records with
no `reason` still render the old text (older rows exist and must not crash the probe);
`uv run pytest`, `ruff`, pyright ratchet pass.

## Non-goals

Changing cadence or hysteresis · a new alert channel or check type · fixing the GRIB cap (Plan 221)
· making the Prefect run report FAILED instead of COMPLETED (real, separate, and arguably wanted —
but not this plan) · backfilling reasons onto historical records.

## Exit gates

- An NWP-abort alert names the cause; a healthy cycle's alert text is unchanged.
- The cause is constructed in the `except` block and crosses the task boundary per D5; no
  exception object or `str(exc)` reaches the record.
- A test proves a presigned-URL exception cannot reach the alert or the stored detail.
- A record lacking `reason` renders exactly today's message.
