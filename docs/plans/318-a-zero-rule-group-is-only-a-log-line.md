---
status: DRAFT
created: 2026-09-24
plan: 318
title: A zero-rule group is only a log line
scope: Make the zero-rule condition visible outside container logs — Plan 272's T3 telemetry, decided there and only half built. A `PipelineHealthRecord` per affected run, a `PipelineCheckType` member for it, and a watchdog probe. NOT the consumer policy (316), NOT re-examination (317), NOT the selection fix or the status (272, shipped), NOT a dashboard.
depends_on: []
blocks: []
related: [272, 315, 316, 317]
open_decisions: []
reviews:
  - "codex 2026-09-24 r1 — PROBLEMS FOUND; severity and Slack delivery were already settled by 272 T3/C4b, and the enum inventory was truncated"
source: 2026-09-23 — the completeness audit of PR #297. T3 shipped its counter and its log line; the health record, the check type and the watchdog probe were not built.
---

# Plan 318 — a zero-rule group is only a log line

⚠️ **Plan number PROVISIONAL until the owner grants it.**

## Status

**DRAFT.** ⛔ Only the orchestrator sets READY. **Independent of Plans 316 and 317** — it observes
the condition rather than changing it, so it can run in any order relative to them. ⭐ **Running it
FIRST has a specific value**: it is the only plan of the three that produces the station counts
nobody has, and those counts are what tell the owner whether 316 and 317 are urgent or academic.

## Why this plan exists

Plan 272 shipped the part of T3 that counts — `IngestResult.qc_unchecked` and a
`qc.no_rules_selected` warning (`flows/ingest_observations.py:365`). It did not ship the part that
**tells anyone**. Measured on `main` at `dbb9105e`: no `PipelineHealthRecord` is written for the
condition, `PipelineCheckType` has no member for it (`types/enums.py:197-203`), and the watchdog
contains no probe for it.

⇒ **A station whose readings are going unchecked is discoverable only by reading container logs.**
On a host where the logs are UTC and the operator is not, that is not discoverable in practice.

🔴 **And the absence has already cost something concrete.** Through a day of analysis nobody —
assistant or reviewer — could say how many stations are affected, because the tree cannot answer
it and Plan 272's census task was never run and is now unbuildable (it was a *pre-change*
measurement of logic that has since changed). **This plan is how that question becomes answerable
at all**, and it answers it continuously rather than once.

## What is measured

Measured on `main` at `dbb9105e`.

1. **What shipped**: the counter (`IngestResult.qc_unchecked`, `:63`) and one warning per affected
   group (`:365`), carrying the station, the parameter, the inferred cadence and a reason.
2. **What did not**: no health record for it anywhere; **`PipelineCheckType` has 16 members**
   (`types/enums.py:197-221`) and **none of them is a zero-rule member**; no watchdog probe.
   ⛔ *An earlier draft said six — the citation stopped mid-enum. The absence of a zero-rule
   member is unaffected, but the count was wrong and is the kind of truncation this project has
   been bitten by repeatedly.*
3. **The precedent to follow, exactly.** `flows/collect_bafu_forecasts.py:199-217` appends a
   `PipelineHealthRecord(check_type, checked_at, status, subject, detail, cycle_time, created_at)`
   and — ⭐ **importantly** — wraps it in `try/except` that logs a warning on failure. **Telemetry
   must not be able to fail the run it observes.**
4. **One record per run, not per group.** 272's T3 says so in terms; a fleet-wide cadence problem
   would otherwise write hundreds of records for one cause.

## Owner decisions

**None.** ⛔ *An earlier draft opened one on severity and paging. Both were already settled by
Plan 272:*
- **`WARNING`, not `CRITICAL`** — T3 specifies it (`272:1496-1500`).
- **It reaches Slack via the watchdog probe** — C4b says so in terms: without one more probe
  T3's warning is *"pull-only via `/api/v1/health/detail`"*, and D5's mitigation *"reduces to a
  human remembering to look. One more probe URL makes it Slack."* ⇒ **Delivery is the point of
  T2, not out of its scope.**

⚠️ **A threshold-based escalation to `CRITICAL` stays deliberately unbuilt** — it needs a number
nobody can source, and **this plan exists because those numbers do not exist yet**. Revisit once
it has produced a few weeks of counts. *(That is a stated deferral, not an open decision.)*

## Tasks

### T1 — A health record and a check type

**Outcome.** One `PipelineHealthRecord` per ingest run that had any zero-rule group, queryable
without reading logs.

**In.**
- A `PipelineCheckType` member for the condition.
- **One record per run**, with a `detail` carrying the affected station/parameter groups, each
  group's inferred cadence, the reason (`no_cadence_inferable` / `no_rule_declares_it`), and the
  run's totals. ⚠️ **Counts, not a boolean** — a `WARNING` that is easy to ignore is only useful if the detail says how much.
- 🔑 **Wrapped in `try/except` with a warning on failure**, exactly as
  `collect_bafu_forecasts.py:199-217` does. ⛔ *A telemetry write must never fail the ingest run.*
- **No record at all on a clean run** — an always-written record is noise.

**Out.** ⛔ Changing the existing counter or warning. ⛔ A record per group.

**Pre-change.** A RED test: a run with zero-rule groups writes no health record today.

**Verification.** A run with several affected groups writes **exactly one** record carrying all of
them; a clean run writes none; **and a store that raises on append does not fail the ingest run** —
asserted, because that is the property the precedent's `try/except` exists for.

### T2 — A watchdog probe

**Outcome.** The condition surfaces where the operator already looks.

**In.** A probe reading T1's records, reporting at `WARNING` per 272 T3. State the lookback it uses.

**Out.** ⛔ Any threshold-based escalation to `CRITICAL`. ⛔ A new delivery channel — the probe
rides the watchdog's existing route, which is what makes it Slack (`272` C4b). ⛔ *An earlier
draft excluded "paging or alert delivery" outright, which contradicted C4b and left "reports"
with no operator destination.*

**Pre-change.** A RED test: the watchdog is silent when the records exist.

**Verification.** It reports the affected stations from the records, and is silent when there are
none.

## Explicitly out of scope

- **The consumer policy** (316) and **re-examination** (317). This plan changes no verdict and no
  consumer.
- **Plan 272's T1 census** — ⛔ **unbuildable**, and this plan is the replacement: T1 was a
  *pre-change* measurement of logic that has since changed, so its baseline can no longer be
  taken. Continuous telemetry answers the same question going forward.
- **A threshold for escalation to `CRITICAL`** — deliberately deferred until counts exist.
- **A dashboard.**

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1"] },
    { "id": "phase-2", "tasks": ["T2"], "depends_on": ["phase-1"] }
  ]
}
```
