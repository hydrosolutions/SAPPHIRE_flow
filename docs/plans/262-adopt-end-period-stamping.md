---
status: DRAFT
created: 2026-09-09
plan: 262
title: Our own aggregation stamps the start of a period; every source whose convention we have established stamps the end
scope: Adopt END-PERIOD stamping as the house convention for interval-valued data, convert it at the ingest boundary, change the internal bucket labelling that currently contradicts it, and rebuild the derived data whose meaning changes. Explicitly NOT the grid phase or the day boundary (Plan 252), NOT the per-station override (Plan 252 OD-12), NOT which series ARE intervals (Plan 258 — this plan CONSUMES that answer), NOT the Swiss boundary move itself (Plan 254 T6 — but this plan MUST ride the same cutover).
depends_on: [252, 258]
blocks: []
source: 2026-09-09 — a grill-me on the timezone reconciliation asked which end of a span its timestamp marks; measuring the answer found our own aggregation disagreeing with every source whose convention has been established
---

# Plan 262 — adopt end-period stamping

## Status

**DRAFT — not reviewed.** Split out of the timezone reconciliation on 2026-09-09 by owner decision:
*"we have to do end-period-stamping here. may need a separate plan to fix our current
implementation."*

## The problem, measured

A value that covers a span can be stamped at either end of it. **Every source whose convention we have
ESTABLISHED stamps the END. Our own aggregation stamps the START.** Nobody chose the second one.

⚠️ **Narrowed 2026-09-09.** An earlier revision said "every source we ingest", which this plan's own
open decisions refute: `camels-ch` and `meteoswiss_sreld` are unread (D2 and Plan 252 T6), and
instantaneous observations have no period end to stamp at all. The claim holds for what has been
checked — five of seven forcing sources — and it is a reason to finish the audit, not to generalise
ahead of it.

| | Convention | Evidence |
|---|---|---|
| DHM precipitation workbook | **period-ending** | `docs/design/dhm-precipitation-milestones.md:119` (M-D3, answered) |
| ERA5-Land | **period-ending** — hour *t* is the accumulation over *t−1 → t* | `dhm-precipitation-milestones.md:279`, recorded as **known**, not inferred |
| **our resampler** | **period-beginning** | `services/training_data.py:374` calls `group_by_dynamic("timestamp", every=…)` with no `label=`; polars 1.43.2 defaults `label='left'`, `closed='left'` — verified by running the signature 2026-09-09 |

⭐ **The start-labelling was never a decision.** It is a library default that was inherited and never
revisited. An earlier revision of the reconciliation treated it as a design choice worth preserving,
which framed the question wrongly: this is not a trade between two conventions, it is replacing an
unexamined default with a stated one that already matches every input whose convention we have
established. ⚠️ Two remain unread (D2, and Plan 252 T6).

## The defect this creates — mechanism PROVEN, currently LATENT

**Proven by execution 2026-09-09**, not by reading:

```
rows stamped 2026-09-01 00:00 .. 2026-09-01 23:00, hourly
group_by_dynamic(every="1d")  ->  bucket labelled 2026-09-01 holds exactly those 24 rows
```

Each of those rows, being END-stamped, covers the hour *ending* at its stamp. So the bucket labelled
`2026-09-01` actually spans **2026-08-31 23:00 → 2026-09-01 23:00**. It is labelled as one day and
contains another, displaced by one hour.

⛔ **It does not bite today, and the reason matters.** Measured on staging 2026-09-09: **all stored
forcing is ALREADY DAILY**, stamped `00:00`, exactly one distinct time-of-day across all **seven** stored
sources — `meteoswiss_rhiresd`, `meteoswiss_rprelimd`, `meteoswiss_tabsd`, `meteoswiss_tmind`,
`meteoswiss_tmaxd`, `meteoswiss_sreld`, `camels-ch`. *(An earlier revision said eight while listing
seven.)* **There is no hourly forcing in the store**, so
the hourly→daily bucketing never runs on real data. The trap is on the path we are about to walk
down, not a fire burning now.

🔴 **Nepal is an hourly source.** Sub-daily forcing arrives hourly and must be aggregated to 3-hourly
and daily. That is precisely the path that triggers this. **Fix it before the feed arrives, not
after.**

🔴 **CONFIRMED 2026-09-09, and it is larger than this plan's own defect.** The same investigation
established from MeteoSwiss's grid-product documentation that **both precipitation products run
06:00 UTC → 06:00 UTC** while **temperature runs midnight → midnight**. We store all of them stamped
`00:00` and treat all of them as midnight-to-midnight. So Swiss precipitation is displaced +6 h, and
our own two inputs disagree with each other by six hours. **That is Plan 252's to declare** (its
evidence section, and **OQ-6** on how differently-phased sources feed one model) and Plan 254 T6's to
correct — in the same retrain this plan rides.

## What changes, measured

**Code — five bucket-boundary functions and one call site**, all in `services/training_data.py`:
`expected_past_buckets` (`:187`), `expected_future_buckets` (`:200`), `floor_to_time_step` (`:244`),
`aligned_lookback_bounds` (`:261`), `resample_to_time_step` (`:286`), and the `group_by_dynamic` call
at `:374`. `:192` documents the left-labelling assumption explicitly and must change with them. Skill's
completeness path (`services/skill/service.py:305`) assumes the same convention and moves in the same
step — ⛔ **never separately**, or the scorer and the assembler disagree for the duration.

**Tests — six files lock the current convention** and must be re-proven, not merely updated:
`tests/unit/services/test_training_data.py`, `test_input_quality.py`, `test_run_station_forecast.py`,
`test_hindcast.py`, `test_run_group_forecast.py`, `tests/integration/test_e2e_pipeline.py`.
⚠️ Plan 228 D4's locking tests are among what pins today's behaviour; Plan 252 T8 is what makes
amending them legitimate. **This plan must not amend a locked test on its own authority.**

**Stored data — what actually has to move.** Measured on staging 2026-09-09:

| table | rows | affected? |
|---|---|---|
| `observations` | 4,329,109 | ⛔ **NO** — an instantaneous reading has no span, so no end to stamp |
| `historical_forcing` | 15,112,289 | YES for interval-valued series |
| `weather_forecasts` | 21,076,692 | YES for interval-valued series (accumulations); not for instantaneous ones |
| `hindcast_forecasts` | 715,103 | YES where the target is an interval statistic (a daily mean discharge is one) |
| `forecasts` | 9,746 | same as hindcasts |
| `skill_scores` | 139,712 | derived — rebuild, never edit |
| `model_artifacts` | 902 | trained under the old convention ⇒ **retrain** |

⛔ **Which series are intervals is Plan 258's answer, not this plan's.** This plan cannot begin
converting before 258 settles it — that is why 258 is a hard dependency and not a courtesy reference.

## Design

**Convert at the ingest boundary; store one convention.** (Owner decision, 2026-09-09.) Each adapter
knows its own source's convention, converts to end-stamping there, and records that it did. Every
consumer downstream then obeys one rule and no consumer ever has to ask. ⛔ The alternative —
recording the convention per source and having consumers adjust — was rejected: any consumer that
forgets is silently one interval out, which is the failure class this whole family exists to remove.

**Do NOT rewrite 41 million stored timestamps in place.** Re-ingest and rebuild instead:

1. Forcing and weather forecasts are **re-ingestable from source** — refetch under the new
   convention rather than shifting stored stamps.
2. Hindcasts, skill scores and forecasts are **derived** — rebuild them; a skill rebuild already has a
   mechanism (Plan 235 generations).
3. Old forecasts **may be discarded** — the mac-mini is a test deployment (owner, established for
   Plan 248 T2, which chose discard on the same ground).
4. Observations are untouched.

⭐ **This MUST ride Plan 254 T6's cutover, not open a second one.** Owner decision 2026-09-09 bundled
the Swiss boundary move with the retrain already owed for the live train/serve skew. This change has
the identical shape — it alters what a stored interval value means, invalidates every artifact, and
needs a coordinated switch — so doing it separately means **three migrations where one will do**.
The sequence is 254 T6's; this plan supplies one more thing that moves inside it.

## Open decisions

**D1 — do we convert on the way OUT as well?** External consumers may expect a convention we do not
use. Publishing the covering window alongside each value (Plan 258, owner decision 2026-09-09) makes
the stamp unambiguous regardless, which may make an output-side conversion unnecessary. Decide before
T4.

**D2 — what happens to `camels-ch`?** 2,162,280 rows of a published research dataset, whose
convention must be established from its documentation rather than assumed. If it is period-beginning
it needs converting; if the documentation does not say, it cannot be silently assumed either way.

**D3 — ANSWERED 2026-09-09: the MeteoSwiss daily-day question is RESOLVED for five of the seven
stored forcing sources, and the answer is worse than the question.** Read from the provider's grid-product
documentation: both precipitation products run **06:00 UTC → 06:00 UTC**, while the temperature
products run **midnight → midnight**. So this plan is not converting a single unknown convention — it
is converting sources that **disagree with each other by six hours**. The declaration belongs to
Plan 252 (see its evidence section and **OQ-6**); the correction rides Plan 254 T6 with this plan's
change. ⚠️ `meteoswiss_sreld` and `camels-ch` remain unread and still block T2 for those two sources.

## Non-goals

- Grid phase, the day boundary, the per-station override — Plan 252.
- Deciding which series are intervals and which are instants — Plan 258.
- The Swiss boundary move itself — Plan 254 T6. This plan rides its cutover; it does not own it.
- Rewriting stored timestamps in place. Explicitly rejected above.
- `observations` — instantaneous, unaffected.

## Tasks

Every code task carries the Task Exit Gate (`docs/workflow.md:198-210`).

### T1 — settle D1–D3, and answer the MeteoSwiss daily-day question first

**Outcome:** three recorded decisions, and the covering window of every MeteoSwiss daily product
established from their documentation. **In:** this document; Plan 252 T6's adapter audit, which is
where the finding belongs. **Out:** any code. **Verification:** N/A — decision task. Each answer
cites the source document that settles it; an unanswered product is marked unresolved, never assumed.

### T2 — convert at the ingest boundary

**Outcome:** every adapter emits end-stamped interval values and records the conversion it applied.
**In:** `src/sapphire_flow/adapters/`. Depends on T1 and on Plan 258's interval declaration.
**Out:** the internal bucket labelling (T3). **Pre-change:** sources disagree with each other and
with us, and nothing records which convention any of them uses.
**Verification:** a fixture from a period-beginning source round-trips to an end stamp; a
period-ending source is unchanged; an instantaneous series is untouched; the applied conversion is
recorded per series.

### T3 — change the internal bucket labelling, in one step with the scorer

**Outcome:** our own aggregation labels the END of each bucket, and the completeness path agrees.
**In:** the five functions and the call site listed above, plus `services/skill/service.py:305`.
Depends on T2, and on Plan 252 T8 for permission to amend Plan 228 D4's locking tests.
**Pre-change:** ⭐ **the red-first test is the mechanism proof above** — end-stamped hourly rows
aggregated to daily produce a bucket labelled `D` that spans `D-1 23:00 → D 23:00`. It must fail on
the LABEL, not on a signature.
**Verification:** `uv run pytest` — that bucket is labelled `D+1 00:00`; the six test files above are
re-proven rather than adjusted; and the assembler and the scorer are shown to agree, since a change
to one alone is the actual hazard.

### T5 — convert on the way OUT, IF D1 says we must (conditional)

**Outcome:** external consumers expecting the other convention get it, or this task is closed as
not-needed with the reason recorded.

⛔ **Conditional tasks still need to exist.** D1 asks whether we convert for external consumers; until
2026-09-10 a "yes" had no task to build it. If Plan 258 T5's published covering window makes the stamp
unambiguous, the answer is likely no — **close this explicitly rather than leaving it implied.**

**In:** the published API and export shapes. **Out:** internal storage. Depends on T1 (which answers
D1) and on Plan 258 T5.
**Verification:** either a consumer-facing value carries the converted stamp and a test locks it, or
this task is recorded CLOSED with D1's reasoning.

### T4 — rebuild what the change invalidates, inside Plan 254 T6's cutover

**Outcome:** forcing and weather forecasts re-ingested, artifacts retrained, hindcasts and skill
rebuilt, old forecasts discarded — as steps within the existing cutover, not a second one.
**In:** Plan 254 T6's sequence. Depends on T3. **Out:** opening a separate migration.
**Verification:** N/A — deployment task. This plan's steps appear inside 254 T6's sequence with its
rollback covering them, and no second cutover exists.

## Exit gates

```bash
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright src/
uv run pytest
uv run python scripts/check_readiness.py docs/plans/262-adopt-end-period-stamping.md
```

1. **The assembler and the scorer change together** — never one without the other.
2. **No stored timestamp is edited in place.** Re-ingest and rebuild only.
3. **Observations are untouched**, and a test proves an instantaneous series is not converted.
4. **One cutover, not two** — every step here appears inside Plan 254 T6's sequence.
5. **The MeteoSwiss daily-day question is answered** before any conversion runs (D3).

## Dependency graph

```json
{
  "plan": 262,
  "nodes": [
    {"id": "T1", "phase": 1, "depends_on": []},
    {"id": "T2", "phase": 2, "depends_on": ["T1"], "blocked_on": "Plan 258 must declare which series are intervals"},
    {"id": "T3", "phase": 3, "depends_on": ["T2"], "blocked_on": "Plan 252 T8 — amending Plan 228 D4's locking tests"},
    {"id": "T4", "phase": 4, "depends_on": ["T3"], "blocked_on": "rides Plan 254 T6's cutover"},
    {"id": "T5", "phase": 3, "depends_on": ["T1"], "note": "conditional on D1; close explicitly if not needed"}
  ]
}
```
