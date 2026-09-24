---
status: COMPLETE   # merged #299, 2026-09-24. ⛔ A merged plan left reading READY is a live order to an agent — the hazard this repo already booked.
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
  - "codex 2026-09-24 r4 (red tests) — one finding: T1's corrected red test asserted mere EXISTENCE of a health record, which already passes because the flow writes a fetch record every run"
  - "codex 2026-09-24 r3 (fold check) — one finding: T2's red test asserted the defect, so it would have passed before implementation"
  - "claude 2026-09-24 r2 — PROBLEMS FOUND; the watchdog probe polarity was INVERTED from every existing probe, the precedent cited was the wrong file (the helper is already in the edited file), the detail does not exist above the log line, and the severity citation pointed at C4b"
source: 2026-09-23 — the completeness audit of PR #297. T3 shipped its counter and its log line; the health record, the check type and the watchdog probe were not built.
---

# Plan 318 — a zero-rule group is only a log line

⚠️ **Plan number PROVISIONAL until the owner grants it.**

## Status

**READY** — set by the orchestrator 2026-09-24 on the owner's instruction, after the independent reviews recorded in the frontmatter. **Independent of Plans 316 and 317** — it observes
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
3. **The precedent is IN THE FILE T1 EDITS, and the store is already plumbed.**
   `_append_fetch_health_record` (`flows/ingest_observations.py:230-267`) already appends a
   `PipelineHealthRecord` best-effort — the `pipeline_health_store is None` no-op and the
   try/except included — and the store is already threaded into the flow (`:593`, `:612-613`).
   ⛔ *An earlier draft cited `flows/collect_bafu_forecasts.py:199-217`, which made the store look
   like new plumbing it is not.* ⇒ **T1 follows the local helper and introduces no new wiring.**
   The property that matters is unchanged: **telemetry must never fail the run it observes.**

4. 🔴 **The detail T1 must write does not exist above the log line.** `_run_qc_task` returns only
   `{"passed", "failed", "suspect", "unchecked"}` (`flows/ingest_observations.py:301`, `:412`);
   the station, parameter, inferred cadence and reason exist **only inside** the `log.warning` at
   `:365`. ⇒ **T1 must widen that return**, and its Out-list must not be read as forbidding it.

5. 🔴 **Every existing watchdog probe has the OPPOSITE polarity.** All three are *freshness*
   probes built as `…/health/detail?check_type=…&limit=1` (`ops/watchdog.py:141-155`), where a
   **missing** record is the alarm — *"A missing record (found=False) … reads as 'no heartbeat
   found'"* (`ops/watchdog.py:1513`). T1 writes **no record on a clean run**, so an implementer
   told to "ride the existing route" would **alert on every healthy run**. ⇒ T2 is a *presence*
   probe and must say so.
6. **One record per run, not per group.** 272's T3 says so in terms; a fleet-wide cadence problem
   would otherwise write hundreds of records for one cause.

## Owner decisions

**None.** ⛔ *An earlier draft opened one on severity and paging. Both were already settled by
Plan 272:*
- **`WARNING`, not `CRITICAL`** — T3 specifies it (`272:39`, `272:1962`). ⛔ *An earlier draft
  cited `272:1496-1500`, which is C4b — the passage the next bullet cites.*
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
- 🔴 **Widening `_run_qc_task`'s return so the detail is reachable** — it returns counts only
  today (`:301`, `:412`) and the station/parameter/cadence/reason live only inside the log call at
  `:365`. **Without this the record cannot be written at all.**
- 🔑 **Follow `_append_fetch_health_record` (`flows/ingest_observations.py:230-267`)** — the
  best-effort helper already in this file, no-op and try/except included; the store is already
  threaded in at `:593`, `:612-613`. ⛔ *A telemetry write must never fail the ingest run.*
- **No record at all on a clean run** — an always-written record is noise. ⚠️ **This is what makes
  T2 a presence probe; see § (5).**

**Out.** ⛔ A record per group. ⛔ Changing the *meaning* of the existing counter or the warning's
event name — ⚠️ *but the return type DOES change, per the first item; an earlier draft's blanket
"changing the existing counter or warning" could be read as forbidding what T1 needs.*

**Pre-change.** A RED test asserting the DESIRED behaviour, **on CONTENT**: after a run
containing a zero-rule group, the health store holds a record whose `detail` **identifies that
station and parameter and its inferred cadence**.

⚠️ **Assert the content, not the existence of a record** — ⛔ *"a run containing a zero-rule group
writes one health record" ALREADY PASSES: `_append_fetch_health_record` writes a fetch record on
**every** run (`flows/ingest_observations.py:230-267`, called at `:612-613`). Its `detail` carries
fetch outcomes, not zero-rule groups, so a content assertion fails while a bare existence
assertion does not.*

⚠️ **And express it over the EXISTING `PipelineHealthRecord` fields**, so the test compiles and
fails on *behaviour* — not on the new `PipelineCheckType` member being absent, which would make it
fail for the wrong reason.

⛔ *Two earlier drafts got this wrong in two different ways: first by stating the defect ("writes
no record today"), which would have passed; then by asserting mere existence, which also passes.
Both were caught in review; recorded so the third attempt is not made.*

**Verification.** A run with several affected groups writes **exactly one** record carrying all of
them; a clean run writes none; **and a store that raises on append does not fail the ingest run** —
asserted, because that is the property the precedent's `try/except` exists for.

### T2 — A watchdog probe

**Outcome.** The condition surfaces where the operator already looks.

**In.** A probe reading T1's records, reporting at `WARNING` per 272 T3, with a stated lookback.

🔴 **It is a PRESENCE probe, and this is the thing an implementer will get wrong.** The three
probes already in `ops/watchdog.py:141-155` are *freshness* probes: they fetch the latest record
of a type and **alert when none is found**. This one is the inverse — T1 writes nothing on a clean
run, so **no record is the healthy case and a record is the alarm.** ⛔ *Copying the freshness
pattern would alert on every healthy run.* Say so in the code, not only here.

**Out.** ⛔ Any threshold-based escalation to `CRITICAL`. ⛔ A new delivery channel — the probe
rides the watchdog's existing route, which is what makes it Slack (`272` C4b). ⛔ *An earlier
draft excluded "paging or alert delivery" outright, which contradicted C4b and left "reports"
with no operator destination.*

**Pre-change.** A RED test asserting the DESIRED behaviour: **given a zero-rule health record in
the store, the watchdog reports it** — which fails today because no probe reads that check type.
⛔ *An earlier draft wrote the red test as "the watchdog is silent when the records exist". That
states the DEFECT, so it would have PASSED before implementation and contradicted this task's own
verification — the exact trap of a red test that does not fail for the reason the bug exists.*

**Verification.** It reports the affected stations from the records, **and is silent when there
are no records at all** — asserted explicitly, because that is exactly the case the inherited
freshness pattern gets backwards.

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
