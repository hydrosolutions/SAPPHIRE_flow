---
status: DRAFT
created: 2026-09-07
plan: 248
title: Backfill and tighten forecasts.time_step_seconds — the half Plan 241 deliberately deferred
scope: Finish the two-release column tightening Plan 241 T4 started. Backfill the MEASURED uniform-daily rows, decide what a truthful cadence is for rows that have none, then tighten the column to NOT NULL. Explicitly NOT the phase/interleaving defect (Plans 252/254), NOT any change to how a cadence is derived at write time.
depends_on: [241]
blocks: []
source: 2026-09-07 — required by Plan 241's own exit gate ("the deferred NOT NULL tightening has a named follow-on plan, or this plan does not close"), and by a live measurement of the staging database taken the same day
---

# Plan 248 — backfill and tighten the forecast cadence column

## Status

**DRAFT.** Created to satisfy Plan 241's exit gate. Awaiting owner READY.

✅ **The deploy blocker is CLEARED.** Plan 241 was deployed to the mac-mini on 2026-09-08 06:31 UTC
(`0.1.884`, checkout `ceb6876f`, `alembic_version` 0054). `forecasts.time_step_seconds` exists —
integer, nullable — and every one of the 7793 rows is NULL. T1 is now executable on the owner's word.

T1 is scoped, authored and REHEARSED against live staging (below). T2 and T3 remain sketched.

## Why this exists

Plan 241 T4 added `forecasts.time_step_seconds` NULLABLE-FIRST, per `docs/standards/cicd.md`
§Rollback ("additive only: new columns nullable"), following the `station_weather_sources.role`
115a/115c precedent. That is deliberately half of a two-release change. This is the other half.

⛔ Plan 241 explicitly forbids closing while this plan does not exist, precisely because a deferred
tightening with no owner is how a nullable column stays nullable forever.

## What is measured (staging, read-only — re-measured 2026-09-08 post-deploy)

🪤 **The 2026-09-07 numbers below are SUPERSEDED — the cycle keeps running (~1336 forecasts/day),
so any literal count is stale the moment it is written. This is why T1's statement asserts STRUCTURE
and computes its target in-transaction rather than pinning a number.**

| | 2026-09-07 | **2026-09-08 (current)** |
|---|---|---|
| forecasts total | 6457 | **7793** |
| single-timestamp | 0 | **0** |
| uniform, gap = 86400 s | 6388 | **7724** |
| uniform, gap ≠ 86400 s | 0 | **0** |
| non-uniform | 69 (ALL `_pooled`) | **69 (unchanged)** |
| `time_step_seconds` NULL | n/a | **7793 (all)** |

⭐ **The non-uniform population did NOT move while the total grew by 1336.** No `_pooled` row has
been written since 2026-09-04, so that class is frozen — which is what makes it the one count T1 can
honestly pin. (Why pooled stopped is undiagnosed and owned elsewhere; it is NOT this plan's scope.)

Sanity checks run alongside: no forecast headers without values, no NULL `valid_time`s, no duplicate
`(forecast, valid_time, member)` rows, no multi-parameter forecasts. The census denominator is sound.

🔴 **The 2026-09-07 approved statement NO LONGER EXISTS.** It was drafted in a session whose context
is gone and was never committed. What T1 carries now is a **re-authoring, not a recovery, and it has
NOT been independently reviewed** — the 09-07 approval does not transfer to it.

It also differs from the approved design on purpose: that version asserted a hardcoded target count
(6388), which is precisely the drift trap above. The re-authored statement asserts structural
invariants and derives its target inside the same transaction instead. That is a material change and
needs a fresh independent pass (one Claude, one Codex, per `docs/workflow.md`).

## The open question this plan must answer

**What is a truthful cadence for a row that has none?** The 69 non-uniform rows are not daily
forecasts with a blemish — they are TWO INTERLEAVED DAILY SERIES, one at exact midnight and one at
`06:00:02.858976`:

```
2026-09-05 00:00:00+00
2026-09-05 06:00:02.858976+00
2026-09-06 00:00:00+00
2026-09-06 06:00:02.858976+00
```

No single scalar describes that. `NOT NULL` can therefore only be reached by inventing a value,
deleting the rows, or quarantining them — and inventing one is the defect Plan 241 exists to remove.

🔴 **This is a symptom, not the disease.** 4182 of 6457 forecasts carry a non-zero PHASE
(microsecond-bearing `valid_time`s from `climatology_fallback`, `persistence_fallback`,
`linear_regression_daily` and `_pooled`). They stay uniform in isolation — a constant offset
preserves the gap — and only collide when pooled across contributors on different phases. **Plans
252 (a time grid is a step AND a phase) and 254 (phase-aware execution) already own that**; this
plan must not duplicate them, and should probably WAIT for them, since what they decide determines
whether these 69 rows get repaired or discarded.

## ⛔ Do not start before

1. ✅ **SATISFIED 2026-09-08** — Plan 241 is merged AND deployed; the column exists in staging.
2. ✅ **SATISFIED 2026-09-08 — T1, T2 and T3 are ALL independent of 252/254.** T1's carve-out is
   argued below and survives. T2's dependency was **vacuous** and has been discharged: 252 hands the
   question off in its own Non-goals, 254 never mentions stored rows, and 226 excludes backfill
   twice. All three verified against `origin/main` 2026-09-08. **T2's decision is now recorded in
   T2 itself, on this plan's authority.** Nothing here waits on 252/254.

### Why T1 does not depend on Plans 252/254

The 4182 phase-bearing rows (microsecond `valid_time`s) are **uniform** — a constant offset preserves
the gap — so their step genuinely IS 86400 s. Nothing 252/254 can decide changes that number:

- if 252 records a grid as step AND phase, adding a phase column, `time_step_seconds = 86400` remains
  correct for these rows;
- if 254 re-aligns those timestamps onto midnight, the step is still 86400;
- if either decides phase-bearing rows should be deleted or re-issued, deleting a row deletes its
  stamp — no stale value survives.

The only rows whose cadence *looked* like it depended on what 252/254 decide are the 69 non-uniform
ones, and **T1 leaves those NULL by construction.** That was T2's question — and T2 has since
established that none of 252, 254 or 226 will answer it, and decided it here instead (quarantine).
So the dependency binds nothing: **not T1, and no longer T2 or T3 either.**

🪤 **The column records a STEP, not a GRID — and this is already true in staging, not a future
risk.** Plan 252's thesis is that a time grid is a step AND a phase. Stamping `86400` on a
phase-bearing row is TRUE BUT INCOMPLETE, and a reader will be tempted to read it as "this forecast
sits on the daily grid at midnight". It does not say that and must not be used to claim it.

**Measured 2026-09-08 14:0x, after the mini was redeployed to `0.1.889`** (which carries Plan 241's
write path, so the running system now populates the column itself):

| | rows |
|---|---|
| forecasts total | **8170** |
| `time_step_seconds IS NULL` (T1's universe) | **7793** |
| ├ uniform-daily → T1 backfills | **7724** |
| └ non-uniform `_pooled` → T2 | **69** |
| `time_step_seconds` populated by the live system | **377** |
| ├ genuinely at phase 0 | 168 |
| └ ⛔ **non-zero phase, recorded as bare `86400`** | **209** |

Every populated row stores `86400` and none is multi-phase. **209 of the 377 rows the column has
recorded so far — 55% — already carry a clock-derived phase that the column does not record**
(e.g. 139 `climatology_fallback` rows at phase 26658 s = 07:24:18). The half-a-grid problem is
therefore live and in the majority, which is why the durable statement of it belongs in Plan 252 as
part of defining `TimeGrid`, and the store-side remedy (a phase column on `forecasts`, mirroring
`skill_scores.phase_offset_seconds` / `skill_diagrams.phase_offset_seconds`, which already exist and
are written) belongs in Plan 254. Neither is in scope here.

⭐ **Consequence for T1: its target set is now FROZEN.** New writes populate the column, so the NULL
population can only shrink, never grow. The 7724 figure held across a re-measurement that moved the
total from 7793 to 8170 — but it held by arithmetic (7793 NULL − 69 non-uniform), not because the
earlier framing "NULL on all 7793" is still true. It is not. T1 must keep deriving its target
in-transaction; do not pin 7724 either.

## ✅ Cross-checked against ForecastInterface and aquacast (2026-09-08)

Per `CLAUDE.md` §ForecastInterface Adherence, checked before implementing. **No FI violation; T1 stands.**

### The column has a declared source, and the backfill agrees with it

FI DOES carry a cadence — `VariableMetadata.timedelta`, declared per variable
(`forecast_interface/output/metadata.py`). Our adapter reads it at
`adapters/forecast_interface.py:221` (`time_step = var_output.metadata.timedelta`) and passes it into
every ensemble it builds. So Plan 241 T4 persists a **DECLARED** value, not a derived one.

T1 backfills legacy rows with a **MEASURED** value. Those are different provenances for one column, so
they must agree. **Measured on staging, they do — exactly:**

| model_id | forecasts | uniform | non-uniform | single-step | measured gap |
|---|---|---|---|---|---|
| climatology_fallback | 3193 | 3193 | 0 | 0 | `1 day` |
| nwp_rainfall_runoff | 2176 | 2176 | 0 | 0 | `1 day` |
| persistence_fallback | 1101 | 1101 | 0 | 0 | `1 day` |
| linear_regression_daily | 655 | 655 | 0 | 0 | `1 day` |
| nwp_regression | 599 | 599 | 0 | 0 | `1 day` |
| `_pooled` | 69 | 0 | **69** | 0 | — |

Every real model emits exactly one uniform gap of `1 day`, with no exceptions and no single-step rows.
aquacast declares the matching value (`aquacast/operational/outputs.py`, `timedelta=step` from
`resolution_timedelta(resolution, window)`). **So the 86400 T1 writes is the same number those models
declare** — the backfill records what the model would have said, not a competing guess.

### ⭐ `_pooled` has no FI declaration at all — which is a BETTER reason to leave it NULL

`_pooled` is a SAP3-side combination (`services/forecast_combination.py`), not an FI model output. No
`VariableMetadata.timedelta` exists for it, from any model. So the 69 rows are not merely "unmeasurable"
— **no model ever declared a cadence for them.** T2 should rest on that, rather than on the weaker
"no single scalar describes an interleaved grid".

### 🔒 The pinned `non_uniform = 69` is STRUCTURALLY guaranteed, not merely frozen

`build_combined_forecasts` refuses to persist a pooled forecast whose grid is non-uniform —
`_derive_uniform_time_step()` returns `None` and the row is skipped with
`forecast_combination.pooled_non_uniform_spacing_not_persisted` (line 461; Plan 222, commit
`928b3093`, shipped in `v0.1.869`, deployed 2026-09-04). A single-timestamp pooled row is likewise refused by
`_MIN_PERSISTED_TIMESTAMPS`. **No new non-uniform pooled row can be written**, so T1's one pinned
count rests on an enforced invariant rather than on luck.

Note the same function REBUILDS the ensemble with the derived step when it disagrees with the ref
contributor's declared step (`forecast_combination.py:385-386`). So for pooled rows SAP3 already
persists a measured value post-0053 — the same provenance T1 uses. Consistent, not novel.

### 🔴 An FI GAP worth filing (not a blocker, and NOT to be worked around here)

**FI never validates that a model's output `datetime` column is actually spaced at its declared
`metadata.timedelta`.** `validate_temporal_columns` (`forecast_interface/output/_validators.py`)
checks only column presence and dtype. A model may therefore declare `1 day` and emit hourly rows, and
FI will accept it — after which our store persists the DECLARED value while the timestamps say
otherwise, and the two disagree permanently with nothing to detect it.

That is an FI expressiveness/validation gap, so per `CLAUDE.md` it belongs upstream as a
ForecastInterface issue, **not** as a SAP3-side check bolted on here. It does not block T1: staging
shows declared and measured agreeing for all five models today. Recording it so the next person does
not "fix" it locally.

✅ **DRAFTED 2026-09-08** — `docs/fi-issues/003-declared-timedelta-is-never-validated.md`, ready to
file at `hydrosolutions/ForecastInterface`, with a runnable demonstration. It carries a second,
separable gap found in the same six-line function: `validate_temporal_columns` also accepts
**timezone-naive** datetimes, because `isinstance(dtype, pl.Datetime)` is true with or without a time
zone. That one matters directly to the Nepal work — a naive timestamp read as UTC is off by 345
minutes at UTC+05:45, which lands in the wrong day. SAP3 is protected inbound by `ensure_utc()`;
other FI consumers are not. **Filing is an owner action, not this plan's.**

⚠️ Minor, same area: `store/forecast_store.py:69` writes
`int(forecast.ensemble.time_step.total_seconds())`. FI permits any positive `timedelta`, so a
sub-second declaration truncates to `0` and violates 0053's `time_step_seconds > 0` check — an insert
failure, not silent corruption. Irrelevant for hydrology today; relevant to T3, which makes the column
mandatory.

## 🔴 Cross-checked against the time-step / time-grid plan family (2026-09-08)

### 1. ✅ 252 and 254 are now ON MAIN — the dangling dependency is closed

Both were invisible to the corpus until 2026-09-08 (unpushed branch `docs/time-grid-conventions`),
while this plan, `README.md:419` and Plan 257 all chained onto them. **PR #265 merged them to main at
07:26 UTC**, so the claims below are now checkable by any reviewer:

- **Plan 252** (DRAFT, `blocks: [254]`) — CONVENTIONS AND TYPES ONLY: adopt CF `cell_methods`, define
  `TimeGrid(step, phase)`, declare the operational boundary per deployment. Motivated by NPT
  (UTC+05:45): an hourly NPT grid and an hourly UTC grid NEVER share a timestamp.
- **Plan 254** (DRAFT, `depends_on: [252, 234]`) — phase-aware execution across seven call sites,
  artifact grid provenance, the Swiss retrain and cutover.

⭐ **Plan 252's evidence section IS this line of work's measurement** — the 6457/4182/4113/69 census
taken while implementing Plan 241. The two plans are already in sync with Plan 241: 252 lines 80-83
record that `store/forecast_store.py` no longer derives `native_step_seconds` for a row with a
persisted non-NULL cadence.

Plan **226** ("The daily models label calendar-day quantities with wall-clock instants", DRAFT)
owns the anchoring.

🔴 **CORRECTION — 226 is NOT sequenced behind 252/254, and nobody owns the interaction.** Its
`depends_on` is `[222, 228, 235]`; it does not reference 252 or 254. And BOTH of those exclude it —
252's and 254's scope lines each say *"NOT Plan 226's anchoring"*. The "sequenced behind" claim comes
from `257:179` and is wrong; an earlier revision of this section repeated it. Verified against the
frontmatter, not the prose.

⚠️ **The gap bites immediately (sapphire-flow-f2, verified here): a DOUBLE RETRAIN.** 226 is written
throughout in terms of MIDNIGHT buckets, while 252 sets the Swiss daily grid to `(86400 s, 82800 s)`
— 23:00Z→23:00Z, explicitly NOT UTC midnight (`252:119`) — and states that changing the boundary
**requires retraining**, owner-confirmed (`252:128`). Land 226 first and the models are anchored to
midnight and retrained, then moved to 23:00Z and retrained AGAIN.

🔴 **And the two plans CONTRADICT each other on who owns that retrain:** `252:135` says *"Sequencing
the retrain belongs with Plan 226, which already owns"* it, while `254:182` says *"No plan currently
owns this: `226:216` excludes recomputation ... so the Swiss retrain has no home until this task
creates one."* Both cannot be true. Whoever owns the time-grid family must settle it.

**Suggested order (f2's, and it avoids the double retrain):** file the FI issue (longest lead time,
external) → review and READY 252 → 226 anchors ONCE, to the settled boundary → 254 execution plus the
Swiss retrain.

### 2. ⛔ T2's DEPENDENCY IS VACUOUS — verified against all three plans, not inferred

This plan says the 69 rows "should probably WAIT" for 252/254, because "what they decide determines
whether these 69 rows get repaired or discarded". **They decide nothing of the sort. All three
candidate owners have explicitly declined the question:**

| plan | stance on stored rows |
|---|---|
| **252** | Non-goals: *"Retrofitting phase onto historical stored rows. New writes declare it; **a backfill is a separate decision with its own cost**."* |
| **254** | No mention of stored forecasts, backfill, `time_step_seconds` or the 69 — anywhere. |
| **226** | Out of scope, stated TWICE (lines 62 and 224): *"Backfill, recomputation, or migration of stored forecasts or hindcasts."* |

🔴 **So T2 is not blocked — it IS the separate decision Plan 252 hands off.** Waiting for 252/254 to
settle the 69 would wait forever. T2 should drop the dependency and decide: repair, quarantine, or
delete, on this plan's own authority. ✅ **Both follow-ups are done (2026-09-08):** the
`⛔ Do not start before` gate above is amended, and the `docs/plans/README.md` line that read
"disposition of the 69 non-uniform rows depends on 252/254" is corrected. T2 records the decision:
**quarantine.**

📌 Precedent worth using in T2: phase is ALREADY persisted alongside step elsewhere — the skill store
writes `(time_step, phase)` at `services/skill/service.py:110` (252 lines 326, 421). So "record the
phase too" is an established pattern here, not a novel design.

### 3. ✅ T1's carve-out SURVIVES the cross-check, and is reinforced

Plan 226 anchors daily `valid_time`s onto the calendar day. That REMOVES the sub-second phase while
leaving the spacing at one day, so a stamped `86400` stays correct after 226 lands. And because 226
will not rewrite stored rows, **T1's stamps cannot be invalidated by it.** Same conclusion as the
FI cross-check, reached from the opposite direction.

Plan 113 (forecast schedule vs NWP cycle alignment, DRAFT) changes WHEN cycles run, which moves the
wall-clock phase of clock-derived `valid_time`s — but not their spacing. T1 unaffected.
Plan 099 (dashboard display timezone, DRAFT) is display-only, no storage. T1 unaffected.

### 4. ⭐ The pooled stoppage was PREDICTED, and is already owned

The 69 rows are frozen because `_pooled` stopped on 2026-09-04 — the day `v0.1.869` deployed Plan
222's guard (commit `928b3093`). Plan 226 called this in advance on 2026-08-31: *"the intersection is
empty under today's grids and the invariant alone takes the combined forecast dark"*, and its scope
line reads *"Restores the combined forecast that Plan 222 takes dark."*

Measured confirmation — no `valid_time` is shared by all contributors:

```
CONTRIBUTORS (combinable_results):
  nwp_rainfall_runoff      00:00:00                                grid-aligned
  nwp_regression           00:00:00                                grid-aligned
  linear_regression_daily  06:00:03.56636, 00:00:02.592992, ...    clock-derived  <-- the odd one

NOT contributors — excluded by FALLBACK_MODEL_IDS:
  climatology_fallback     clock-derived phases
  persistence_fallback     clock-derived phases
```

📌 **Correction (sapphire-flow-f2, verified here 2026-09-08):** the two fallback models are NOT
combination contributors — `MultiModelForecastResult.combinable_results`
(`services/run_station_forecast.py:127-131`) excludes `FALLBACK_MODEL_IDS`, which is exactly the
`ModelTier.FALLBACK` set (`types/ids.py:53`): climatology and persistence. Their clock-derived phases
are real but irrelevant to pooling. **The contributor set is three models, and
`linear_regression_daily` alone sits off-phase from the midnight-anchored NWP pair** — max overlap
across the three is 2, so the intersection is empty. The conclusion is unchanged; the cast is smaller.

#### Measured live 2026-09-08 07:49Z — TWO populations, and only one of them changed

```
forecast_cycle.combined_forecast_skipped:
    5  n_models=0
  101  n_models=1     <- never reach the combiner: insufficient-lookback population
   34  n_models=3     <- reach it with 3 contributors, dropped on empty intersection

forecast_combination.pooled_empty_intersection:  34  (contributor_count=3)
```

Both populations ARE logged at the FLOW level — `forecast_cycle.combined_forecast_skipped` fires for
each, carrying `n_models`. They differ at the SERVICE level:

- the **101** return early at `services/forecast_combination.py:397`
  (`if len(combinable_results) < 2: return []`) BEFORE `combine_ensembles_pooled` is entered, so no
  `forecast_combination.*` event exists for them at all;
- the **34** get all the way in and emit `forecast_combination.pooled_empty_intersection` (line 148).

So: flow-visible, service-silent (sapphire-flow-f2's refinement — my earlier "invisible by
construction" was half right). 📌 `pooled_insufficient_contributors` (:104) is therefore never seen
for the 101, because the outer return fires first. It is NOT dead code, though: it sits inside
`combine_ensembles_pooled`, which `services/skill/combined_skill.py:111` also calls WITHOUT that outer
gate, and its own `eligible` filter can drop contributors below the floor even when 3 arrive.

⚠️ **Line numbers re-verified against `4999c79c` (Plan 253, #264), which MODIFIED this file.** All
three gates survive with their logic unchanged — only the line numbers moved, and the earlier
citations (`:308-310`, `:130`, `:86-92`) are now stale. What #264 did change is `qc_status`: the
combination is no longer hard-coded `RAW` but computed via `_qc_combined_ensemble`, and a QC-failed
combination is still STORED as an approved exception (OD-1). So the persistence decision is untouched
and this diagnosis holds.

🪤 **But the 34/101 split was measured on `0.1.884`, which does NOT contain #264** — the mini is
behind main again. Re-measure after the next deploy before treating those numbers as current.

🔴 **Neither population survives a redeploy** — all of this lives in container logs, while the DB
records only the ABSENCE of rows, which looks identical for all three gates. That, rather than "a
product went dark", is the real argument for Plan 257 existing, and it is why this was re-derived
twice.

🔴 **Do NOT call the lookback shortfall a cause of the STOPPAGE** (sapphire-flow-f2's correction,
accepted). Those 101 stations were never pooling — before 09-04 only 2-3 stations pooled at all,
because `linear_regression_daily`/`nwp_regression` were already failing there with
`Insufficient lookback: need 7 rows, got 6` / `INPUT_DATA: insufficient lag history: got 6, need 7`.
That is the pre-existing and CORRECT `<2 contributors` gate. What changed on 09-04 is only the 34:
the morning's onboarding gave them working artifacts (`lr_daily` 3→37, `nwp_regression` 3→36 at the
06:00 cycle, so pooled jumped 3→36) and the guard landed hours later and took exactly that new
population dark. **The lookback bug explains the small denominator; the guard explains the stoppage.
Different defects, different fixes.**

⛔ **This also kills the original dead end for good:** "no `forecast_combination.*` events appear, so
combination is not running" — they appear **34x per cycle**. That conclusion came from logs a
redeploy had already destroyed. It has now been re-derived twice; do not do it a third time.

⚠️ **Scope is 34 stations, not the 2 Plan 222 D7 priced.** Station onboarding on 2026-09-04 multiplied
the accepted cost ~17x in the same window the guard landed, and `forecast_freshness` reads `ok`
throughout — which is what Plan 257 exists to fix.

So this is **expected behaviour of a READY plan, not an incident**: Plan 222 takes it dark, Plan 226
restores it, Plan 257 (DRAFT) adds the missing monitoring. Nothing here is undiagnosed. For THIS plan
the consequence is narrow and good: the non-uniform population cannot grow while 226 is unlanded,
which is what makes T1's pinned count safe.

## Tasks

⚠️ Sketched, NOT scoped. Each needs its In/Out and Pre-change ledger before this leaves DRAFT.

### T1 — backfill the rows whose cadence is measured, not guessed
**Outcome:** every uniform-daily forecast carries its cadence in the column; the non-uniform rows
remain NULL and untouched.
**In:** the one-off repair statement below (NOT an amendment to migration 0053, which is applied).
**Out:** the 69 non-uniform rows; any change to 0053; any tightening; why pooled stopped.
**Pre-change:** the column is NULL for ALL 7793 rows (measured 2026-09-08), so every read still goes
through the reader's gap-inference. For a uniform row that inference returns exactly what this
statement stores, so **T1 changes no published number** — it replaces a derived value with the same
value recorded.
**Verification:** the statement's own preflight and post-conditions, all inside one transaction; plus
the live rehearsal and negative control recorded below.

#### Matching the reader exactly

`_row_to_forecast` (`store/forecast_store.py`) dedups members with `.unique()` before measuring the
gap, and takes the gap between the FIRST TWO distinct `valid_time`s. So the census must group by
`(forecast_id, valid_time)` — counting `forecast_values` rows directly inflates every class by the
member dimension. The statement's `vt` CTE does this.

#### ✅ Rehearsed on live staging 2026-09-08, and the guard proven to fire

Run inside a transaction terminated by `ROLLBACK`, against the real 7793 rows:

```
NOTICE: census: total=7793 classified=7793 already_stamped=0 single=0
        uniform_daily=7724 uniform_non_daily=0 non_uniform=69
NOTICE: OK: updated 7724 rows; 69 remain NULL (all non-uniform)
ROLLBACK
```

Confirmed written afterwards: `7793 total | 0 stamped | 7793 still NULL` — the rehearsal left nothing
behind.

🔬 **Negative control, because a green run where no assertion trips proves nothing.** The pooled-population
guard was deliberately set to expect 70 against a real 69:

```
ERROR: non-uniform population moved: expected 70, found 69 — pooled writes may have resumed;
       STOP and re-measure
```

The guard fires and aborts the transaction. That control also caught a real defect: the expected
value had been duplicated between the comparison and the message, so the first run printed
"expected 69, found 69". It is now a single `CONSTANT`.

#### The statement

```sql
-- Plan 248 T1 — backfill forecasts.time_step_seconds for MEASURED uniform-daily rows.
--
-- Stores exactly what the reader's inference already returns for these rows, so it
-- changes no published number. Non-uniform rows are left NULL deliberately (T2).
--
-- Run as a single transaction. Every assertion RAISES, which aborts and rolls back.

BEGIN;

-- Match the reader exactly: it dedups members via .unique() before measuring the gap,
-- so the census must group by (forecast_id, valid_time), not count member rows.
CREATE TEMP TABLE t_cadence ON COMMIT DROP AS
WITH vt AS (
    SELECT forecast_id, valid_time
    FROM forecast_values
    GROUP BY forecast_id, valid_time
),
gaps AS (
    SELECT forecast_id,
           valid_time - lag(valid_time) OVER (PARTITION BY forecast_id ORDER BY valid_time) AS gap
    FROM vt
)
SELECT f.id,
       count(g.gap)          AS n_gaps,
       count(DISTINCT g.gap) AS n_distinct_gaps,
       max(g.gap)            AS gap
FROM forecasts f
LEFT JOIN gaps g ON g.forecast_id = f.id AND g.gap IS NOT NULL
GROUP BY f.id;

CREATE INDEX ON t_cadence (id);
ANALYZE t_cadence;

DO $$
DECLARE
    -- Single source of truth: comparison AND message read this, so a future
    -- change cannot leave the error text disagreeing with the guard.
    c_expected_non_uniform CONSTANT int := 69;
    v_total             int;
    v_classified        int;
    v_already_stamped   int;
    v_single            int;
    v_uniform_daily     int;
    v_uniform_non_daily int;
    v_non_uniform       int;
    v_updated           int;
    v_null_after        int;
    v_nonuniform_stamped int;
BEGIN
    SELECT count(*) INTO v_total FROM forecasts;
    SELECT count(*) INTO v_classified FROM t_cadence;
    SELECT count(*) INTO v_already_stamped FROM forecasts WHERE time_step_seconds IS NOT NULL;

    SELECT count(*) INTO v_single            FROM t_cadence WHERE n_gaps = 0;
    SELECT count(*) INTO v_uniform_daily     FROM t_cadence WHERE n_distinct_gaps = 1 AND gap =  interval '1 day';
    SELECT count(*) INTO v_uniform_non_daily FROM t_cadence WHERE n_distinct_gaps = 1 AND gap <> interval '1 day';
    SELECT count(*) INTO v_non_uniform       FROM t_cadence WHERE n_distinct_gaps > 1;

    RAISE NOTICE 'census: total=% classified=% already_stamped=% single=% uniform_daily=% uniform_non_daily=% non_uniform=%',
        v_total, v_classified, v_already_stamped, v_single, v_uniform_daily, v_uniform_non_daily, v_non_uniform;

    -- STRUCTURAL preflight. Deliberately NOT a hardcoded row count: the cycle keeps
    -- running, so any literal total is stale the moment it is written. These hold
    -- regardless of how many forecasts exist.
    IF v_classified <> v_total THEN
        RAISE EXCEPTION 'classification lost rows: % classified vs % forecasts', v_classified, v_total;
    END IF;
    IF v_single <> 0 THEN
        RAISE EXCEPTION 'single-timestamp forecasts appeared (%); they have no derivable cadence — re-scope before running', v_single;
    END IF;
    IF v_uniform_non_daily <> 0 THEN
        RAISE EXCEPTION 'uniform non-daily forecasts appeared (%); this statement only backfills daily — re-scope', v_uniform_non_daily;
    END IF;
    IF v_single + v_uniform_daily + v_uniform_non_daily + v_non_uniform <> v_total THEN
        RAISE EXCEPTION 'classes do not partition the table';
    END IF;

    -- The ONE count assertion worth pinning, and it is drift-proof for the right
    -- reason: pooled writes stopped 2026-09-04, so this population is frozen at 69.
    -- If pooled resumes, this aborts — which is the correct outcome, because T2 has
    -- not yet decided what those rows should carry.
    IF v_non_uniform <> c_expected_non_uniform THEN
        RAISE EXCEPTION 'non-uniform population moved: expected %, found % — pooled writes may have resumed; STOP and re-measure',
            c_expected_non_uniform, v_non_uniform;
    END IF;

    UPDATE forecasts f
       SET time_step_seconds = extract(epoch FROM c.gap)::int
      FROM t_cadence c
     WHERE c.id = f.id
       AND c.n_distinct_gaps = 1
       AND c.gap = interval '1 day'
       AND f.time_step_seconds IS NULL;
    GET DIAGNOSTICS v_updated = ROW_COUNT;

    -- Target computed in the SAME transaction from the SAME temp table, so it cannot
    -- drift between measuring and writing.
    IF v_updated <> v_uniform_daily - v_already_stamped THEN
        RAISE EXCEPTION 'updated % rows, expected % (uniform_daily % minus already_stamped %)',
            v_updated, v_uniform_daily - v_already_stamped, v_uniform_daily, v_already_stamped;
    END IF;

    -- No non-uniform row may carry a stamp.
    SELECT count(*) INTO v_nonuniform_stamped
      FROM forecasts f JOIN t_cadence c ON c.id = f.id
     WHERE c.n_distinct_gaps > 1 AND f.time_step_seconds IS NOT NULL;
    IF v_nonuniform_stamped <> 0 THEN
        RAISE EXCEPTION 'stamped % non-uniform rows — must remain NULL', v_nonuniform_stamped;
    END IF;

    -- Every remaining NULL must be non-uniform, and nothing else.
    SELECT count(*) INTO v_null_after FROM forecasts WHERE time_step_seconds IS NULL;
    IF v_null_after <> v_non_uniform THEN
        RAISE EXCEPTION 'after backfill % rows remain NULL but only % are non-uniform', v_null_after, v_non_uniform;
    END IF;

    -- Every stamped value must satisfy 0053's check constraint and be a real cadence.
    IF EXISTS (SELECT 1 FROM forecasts WHERE time_step_seconds IS NOT NULL AND time_step_seconds <> 86400) THEN
        RAISE EXCEPTION 'a stamped value is not 86400';
    END IF;

    RAISE NOTICE 'OK: updated % rows; % remain NULL (all non-uniform)', v_updated, v_null_after;
END $$;

COMMIT;
```


### T2 — decide what a truthful cadence is for a row that has none
**Outcome:** a recorded decision for the 69 rows. ✅ **DECIDED 2026-09-08 — QUARANTINE. Do not
repair, do not delete.** Recorded here on this plan's own authority, because no other plan will take
it (see § 2 of the cross-check above).

**The decision, and why the other two options are wrong:**

- ⛔ **Repair is not available.** These 69 rows are *multi-phase*: a single ensemble whose
  `valid_time`s do not share one offset. There is no step to recover, because they do not sit on one
  grid. Writing `86400` would not be an approximation, it would be a fabrication — and the codebase
  already treats this exact shape as an error elsewhere:
  `validate_homogeneous_time_step_and_phase` raises `ConfigurationError` on internally mixed phase
  (`services/skill/service.py`, Plan 228 D2). The 69 are rows that violate an invariant this repo
  asserts everywhere it checks; they predate the guard that would now reject them.
- ⛔ **Delete is wrong for the reason Plan 257 exists.** The database retains only the *absence* of a
  row, which is identical across all three ways a combined forecast can fail to appear. These 69 are
  the only surviving empirical record of what `_pooled` wrote before Plan 222's guard (`928b3093`,
  shipped in `0.1.869`) took it dark on 2026-09-04. Deleting them destroys the evidence on the one
  host where it is observable, to tidy a column.
- ⛔ **Do NOT mark them `QC_FAILED` either.** This looks attractive now that Plan 253 stores a
  rejected combination marked failed and hides it from the Forecast Lab — but it would be false.
  QC did not run on these rows and reject them; they were stored *unchecked* (`qc_status = RAW`
  hardcoded at the old `forecast_combination.py:404`, which is precisely what 253 fixed). Stamping a
  QC verdict that was never reached is the same class of error as fabricating a step.

✅ **Quarantine, concretely:** the 69 keep `time_step_seconds = NULL`, permanently, and the NULL is
made to *mean* "this ensemble does not sit on one grid" rather than "not yet backfilled". T3 supplies
the mechanism that makes that distinction enforceable, so the NULL is a closed set rather than an
open one.

🔒 **The set is closed and cannot grow.** `_pooled` has produced nothing since 2026-09-04 and Plan
226 changes how pooling anchors before it resumes, so no further multi-phase row can be written by
that path. Re-measured 2026-09-08: still exactly 69, all `_pooled`, all NULL, while the total moved
7793 → 8170.

**In:** this plan document. **Out:** any code change (that is T3); the phase defect itself
(252/254); backfilling or re-issuing the 69.
**Pre-change:** N/A — decision task, no behaviour to fail.
**Verification:** the decision and its rationale are written here. It names no dependency on 252/254
because, verified against `origin/main`, it has none.

### T3 — tighten the column, and retire the legacy branch
**Outcome:** every NEW write must declare a step, while T2's 69 quarantined rows survive untouched.

⛔ **`SET NOT NULL` is therefore the wrong instrument** — the original phrasing of this task assumed
T2 would leave zero NULLs, and T2 decided otherwise. Use instead:

```sql
ALTER TABLE forecasts
  ADD CONSTRAINT ck_forecasts_time_step_seconds_declared
  CHECK (time_step_seconds IS NOT NULL) NOT VALID;
```

A `NOT VALID` CHECK **is enforced on every INSERT and UPDATE from the moment it is added**, and is
simply not back-checked against existing rows. That is exactly the required semantics: new writes
cannot omit a step; the 69 are grandfathered and stay visible as the anomaly they are. It is one
statement, instantly reversible with `DROP CONSTRAINT`, and it takes no table rewrite. Never run
`VALIDATE CONSTRAINT` on it while the 69 exist — that is the step that would fail, and failing is
correct.

⛔ **The reader's fabricated-hour branch still CANNOT be deleted**, because the 69 remain readable
and NULL. Retiring it requires those rows to be gone, which T2 declined; so **the branch stays, and
the Plan 241 regression guard `test_legacy_one_step_row_still_returns_the_fabricated_hour` stays with
it.** Deleting either is a separate decision that must first dispose of the 69 — it is not this
plan's, and it is not blocked on 252/254 either. Record it as an explicit, unowned follow-on rather
than smuggling it in here.

**In:** a new migration; `db/metadata.py`. **Out:** T1's backfill; T2's decision; deleting the
fabricated-hour reader branch; `VALIDATE CONSTRAINT`.
**Pre-change:** an INSERT with `time_step_seconds = NULL` succeeds today; after the migration it must
raise `IntegrityError`, and a red-first test must fail for *that* reason, not on a signature error.
**Verification:** the constraint rejects a NULL insert; the 69 existing NULL rows are still present
and still readable afterwards (assert the count, in-transaction, do not pin 69); no table rewrite.

## Exit gates

```bash
uv run pytest tests/unit
uv run pytest tests/integration
uv run ruff check src tests && uv run ruff format --check src tests
```

- T1's preflight aborted on no surprise, and the rows it updated equalled the uniform-daily count
  measured in the SAME transaction (no hardcoded literal).
- T1 received a fresh independent review — the 2026-09-07 approval does NOT carry over to the
  re-authored statement.
- T2's decision is recorded here with its rationale. ✅ done — quarantine.
- T3's `NOT VALID` CHECK rejects a NULL insert, and the 69 quarantined rows are still present and
  still readable after the migration (count asserted in-transaction, not pinned).
- ⛔ **NOT an exit gate any more:** "no NULL rows" and "no fabricated cadence in the reader". T2
  decided to keep the 69, so both are now false by design. The fabricated-hour branch and its Plan
  241 regression guard both survive this plan; retiring them is an unowned follow-on.

## Dependency graph

```json
{
  "plan": 248,
  "tasks": [
    {"id": "T1", "depends_on": [], "parallel": false},
    {"id": "T2", "depends_on": [], "parallel": true},
    {"id": "T3", "depends_on": ["T1", "T2"], "parallel": false}
  ]
}
```
