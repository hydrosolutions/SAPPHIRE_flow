---
status: COMPLETE   # merged #303, 2026-09-24. ⛔ A merged plan left reading READY is a live order to an agent — the hazard this repo already booked.
created: 2026-09-24
revised: 2026-09-24
plan: 317
title: An unchecked reading is never re-examined
scope: Widen the QC pick-up set to `{RAW, QC_UNCHECKED}` so a reading stored unchecked is re-judged once the window holds enough context — Plan 272 T2b item 8, an owner decision of 2026-09-20 that its implementation did not build. NOT the consumer policy (316), NOT the telemetry (318), NOT re-QC of rows stored `QC_PASSED` before Plan 272 (272 D3, no-backfill), NOT any change to what the QC step FETCHES.
depends_on: []
blocks: []
related: [272, 315, 316, 318]
open_decisions: []
reviews:
  - "codex 2026-09-24 r1 — PROBLEMS FOUND; the draft's premise was wrong (the fetch is not RAW-only) and its two 'open decisions' were already closed by 272 T2b item 8 and 272:847"
  - "claude 2026-09-24 r2 — no findings against this plan; the corrected premise (unfiltered fetch at :318-323, pick-up at :327, widening at :302-315) verified independently"
source: 2026-09-23 — the completeness audit of PR #297. An independent pass then named it: *"unchecked rows are never retried because ingestion selects only RAW."*
---

# Plan 317 — an unchecked reading is never re-examined

⚠️ **Plan number PROVISIONAL until the owner grants it.**

## Status

**READY** — set by the orchestrator 2026-09-24 on the owner's instruction, after the independent reviews recorded in the frontmatter.

⭐ **Independent of Plans 316 and 318 — and SMALLER than the first draft claimed.** ⛔ *That draft
said this depends on 316's store-signature change. It does not: the fix is in memory, not in the
query.*

## Why this plan exists

`QC_UNCHECKED` means *"no rule could be selected for this group"*, and that is usually a
**transient** condition — a station's first row of the day, a feed catching up after an outage, a
window that straddled a gap. One cycle later the group is ordinary again.

But the run only ever re-judges rows that are `RAW`. A row written `QC_UNCHECKED` is never
re-judged, so a passing cause becomes a permanent verdict — and under Plan 316 that verdict
follows it into every consumer for the life of the row.

**Plan 272's owner decision already says what to do** (T2b item 8, 2026-09-20): *"`QC_UNCHECKED`
must **not** be terminal … Widen the QC pick-up set to `{RAW, QC_UNCHECKED}`, so a later run with
a fuller window re-checks and upgrades it."* It was not built. This plan builds exactly that.

## What is measured

Measured on `main` at `dbb9105e`.

1. 🔴 **The fetch is NOT status-filtered — the PICK-UP is.** `_run_qc_task` fetches **every**
   status in the window (`flows/ingest_observations.py:318-323`, no `qc_status` argument), then
   selects update targets in memory: `raw_obs = [o for o in all_obs if o.qc_status ==
   QcStatus.RAW]` (`:327`).
   ⛔ **This corrects the first draft, which said the fetch was `RAW`-only and proposed filtering
   it to `{RAW, QC_UNCHECKED}`. That would have been actively harmful**: the unfiltered fetch is
   what supplies the already-checked neighbours that cadence inference and the temporal rules
   need. ⇒ **The change is to the in-memory pick-up set at `:327`, and the fetch must not be
   touched.**
2. **The window is not fixed.** `:302-315` starts from `now - context_window_hours` and then
   **widens to cover the fetched timestamps** for catch-up deliveries — preserved deliberately
   (`272:1556-1563`). ⇒ An older row **can** re-enter a later window, so the residual is narrower
   than "anything older is lost for ever".
3. **A second reset path exists and is not general.** `store_raw_observations` upserts and resets
   `qc_status` to `RAW`, clearing flags and rule version, **when the value or rating-curve
   provenance changed** (`store/observation_store.py:97-117`). A *restated* reading already
   re-enters QC; a merely *early* one does not.
4. **No station count exists.** The tree cannot produce one and 272's census was never run and is
   now unbuildable. **Plan 318 is what makes this measurable** — worth running first for that
   reason, though this plan does not depend on it.

## Owner decisions

**None.** ⛔ *The first draft offered two, and both were already closed:* the trigger is settled by
**272 T2b item 8** (2026-09-20, quoted above), and overwrite-on-re-judgement by **`272:847`**. A
plan does not reopen a decision to re-recommend the option the owner already chose.

## Tasks

### T1 — Widen the QC pick-up set

**Outcome.** A reading that could not be checked for want of context is checked once the context
arrives, in the ordinary cycle.

**In.**
- `flows/ingest_observations.py:327` selects `{RAW, QC_UNCHECKED}` as update targets.
  ⛔ **Do not touch the fetch at `:318-323`** — § What is measured (1).
- Re-judged rows take whatever the rules now say, overwriting the prior verdict (`272:847`).
- The run result and the existing telemetry **distinguish newly-checked from re-checked**, so the
  effect is visible rather than inferred from a total.

**Out.** ⛔ Rows stored `QC_PASSED` — 272 D3, no-backfill. ⛔ Any change to the fetched window or
its widening. ⛔ Any sweep outside the window.

**Pre-change.** A RED test asserting the DESIRED behaviour: **a row stored `QC_UNCHECKED` in one
cycle IS re-judged in the next, when that cycle's window holds ample context** — which fails today
because the pick-up excludes it. ⚠️ **It must fail because the row was not re-judged**, not because
a symbol is missing. ⛔ *Stated this way deliberately: "the row stays `QC_UNCHECKED`" describes the
DEFECT, and a test asserting that would PASS before implementation.*

**Verification.**
- That row becomes `QC_PASSED` / `QC_SUSPECT` / `QC_FAILED` as the rules dictate.
- A row that still resolves zero rules stays `QC_UNCHECKED`.
- A row already `QC_PASSED` is untouched.
- **The already-checked neighbours are still present in the group** — asserted, because the
  rejected approach would have removed them and the suite would not otherwise notice.
- **The catch-up widening still re-admits an older row**, per § (2) — asserted rather than
  assumed, since the residual is stated in terms of the *actual* fetched window.

## Explicitly out of scope

- **The consumer policy** (316) and **the telemetry** (318) — independent; no ordering required.
- **Re-QC of pre-272 `QC_PASSED` rows** — 272 D3, closed.
- **A verdict-history table** — `272:847` chose overwrite.
- **The onboarding path** — Plan 315.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1"] }
  ]
}
```
