---
status: DRAFT
created: 2026-09-11
plan: 272
title: Configured QC rules are unreachable when the inferred cadence matches nothing
scope: Diagnose and fix the observation-QC rule-selection path so that a configured rule cannot be silently unreachable because the cadence inferred from a short observation window fails to match its declared time step. NOT new rule kinds, NOT threshold values, NOT per-station overrides (Plan 269), NOT the network dimension (Plan 264) — though this plan and 264 T3 must land in the right order, see § Cross-plan.
blocks: [264, 269]
related: [264, 268, 269, 301, 303, 304, 313]
open_decisions: []
closed_decisions: [D1, D2, D3, D4, D5, D6, D7]
source: 2026-09-11 — found by the round-6 independent reviews of Plan 269 (both gates, independently) and verified directly against the repository. Plan 268 knew the single-row mechanism locally; nobody owned the systemic consequence.
---

# Plan 272 — configured QC rules are unreachable when the inferred cadence matches nothing

## Status

**DRAFT.** ⛔ Only the orchestrator sets READY.

⚠️ **High-risk work** (`docs/workflow.md` § High-risk work): this changes scientific behaviour
on the live Swiss deployment, so it needs a relevant independent review in ADDITION to the
ordinary Claude/Codex pair — and that review must be of the state that will land, not of an
earlier fold.

Opened at the owner's direction on 2026-09-11, after the round-6 reviews of Plan 269 surfaced it.
**Plan 269 is paused (`BLOCKED`) pending this plan**, because the fix determines whether the path
269 wires can apply a daily ceiling at all.

## Problem

Observation QC selects rules by a cadence **inferred from the observations in front of it**, and
matches that cadence for **exact equality** against each rule's declared time step. On the
scheduled ingest path the observation window is short, and — except for one conditional widening —
fixed. The facts together make configured rules unreachable, silently.

**Measured at `9dc07915` (2026-09-19).** The original diagnosis was measured at `3889c816`, 18
commits earlier; every citation below has been re-pinned against `9dc07915` and the ones that had
drifted are corrected here.

- **The window is three hours, and is widened only on one narrow path.** `_run_qc_task` fetches
  `now - context_window_hours … now + 1h` (`flows/ingest_observations.py:295-296`) with
  `context_window_hours: float = 2.0` at both the task (`:292`) and the flow (`:557`); the Prefect
  deployment passes no parameters (`cli/register_deployments.py:93-99`), and no caller anywhere in
  `src/` or `scripts/` overrides it. **But `:297-309` widens that window** when `fetched_times` is
  non-empty:

  ```python
  window_start = min(window_start, min(fetched_times) - context_window_hours)
  window_end   = max(window_end,   max(fetched_times) + 1µs)
  ```

  `fetched_times` is populated **only for DHM river `water_level`** — the loop at `:727-741`
  gates on `network == "dhm"`, `station_kind == RIVER` and `parameter == "water_level"`, and the
  value is passed at `:754`. See § What the widening does and does not do.
- **Fewer than two rows infers one hour.** `_infer_time_step` returns `timedelta(hours=1)` for a
  group of fewer than two observations, and otherwise the **median** inter-row gap
  (`services/qc.py:40-47`, correct as originally cited). The group is `(station_id, parameter)`
  (`services/qc.py:247-252`).
- **Selection is exact.** `QcRuleSet.rules_for` filters `r.time_step == time_step`
  (`types/domain.py:160-167`, correct as originally cited); `services/qc.py:252-253` calls it with
  the inferred value.
- **The deployed rule set declares only two cadences.** `config.toml` carries 26 rules:
  **14 at 600 s and 12 at 86400 s**. There is no 3600 s rule. Re-counted at `9dc07915`:
  `discharge` 5×600 s + 4×86400 s, `water_level` 5×600 s + 4×86400 s, `water_temperature`
  4×600 s, `precipitation` 2×86400 s, `temperature` 2×86400 s.
- **A miss is silent and reads as success.** No matching rule ⇒ no flags ⇒
  `_aggregate_qc_status([])` returns `QC_PASSED` (`flows/ingest_observations.py:145-150` — the
  original `:130-132` had drifted).

### What the widening does and does not do

The widening at `:297-309` matters to the diagnosis in three ways, and it was missed in the
first pass:

1. **Swiss/BAFU is unaffected.** `fetched_times` is empty for every non-DHM station, so the
   fixed three-hour model is exactly right there. Everything this plan says about the Swiss fleet
   stands unchanged.
2. **On the DHM river `water_level` path the window is variable and depends on ingest history.**
   A catch-up run after an outage spans whatever the adapter recovered, so *which rules run is a
   function of when the last run happened*. A selection rule whose output depends on ingest
   history rather than on the series is itself a defect, independently of which rules it picks.
3. **It does not make the daily rules fire on that path — for this feed.** This was checked
   rather than assumed: `adapters/dhm.py:206-220` recovers history at the station's native
   cadence, so a multi-day catch-up on the ~10-minute DHM river feed returns dense 10-minute rows
   and the median stays 600 s. The twelve daily rules do not become reachable by catching up.
   **This is a property of the feed's cadence, not a general law.** For a genuinely
   daily-reporting station a multi-day catch-up *would* put the median on 86400 s and the daily
   rules *would* fire — which is precisely the history-dependence booked as a defect in point 2
   above: the same station gets different rules on a catch-up run than on a steady-state run.

So the headline claim is: **the twelve daily rules are unreachable on the scheduled ingest path,
in steady state and on catch-up alike; and on the one path where the window is widened, rule
selection is additionally non-deterministic with respect to ingest history.**

### What that adds up to

1. **All 12 daily rules are unreachable on the scheduled ingest flow.** A daily series puts at
   most one row in a three-hour window, so the inferred step is 1 h, which matches neither 600 s
   nor 86400 s. The affected rules are `range_check`, `rate_of_change`, `spike` and
   `gross_outlier` for `discharge` and `water_level`, and `range_check` + `gross_outlier` for
   `precipitation` and `temperature`. **Unreachable is a statement about the defect; it is not the
   inverse of what the fix restores.** Selection is necessary but not sufficient — four of these
   twelve stay *ineffective* after T2 because the checked window cannot supply the neighbouring
   observations they need. See T2 § What the repair actually restores, and what it does not.
2. **`precipitation` and `temperature` have *only* daily rules** — verified by re-parsing
   `config.toml` at `9dc07915`: 2 rules each, both at 86400 s, no sub-daily rule at any cadence.
   For those two parameters the scheduled path can therefore match nothing at any cadence, and
   **no change to window width or matching tolerance repairs it** (see D4).
3. **The failure presents as a clean pass**, which is why it has not been noticed: the flow
   reports observations as QC-passed, having run zero rules.
4. **An existing unit test encodes the fail-open as correct behaviour.**
   `tests/unit/services/test_qc.py:468` — `test_water_level_no_daily_rules_returns_empty_flags` —
   builds one observation and a 10-minute rule, and asserts `result[obs.id] == []` with the
   comment *"No rules match the daily (1-hour step) obs — all pass"*. It passes today. Any fix
   breaks it. T2 owns it.
5. **The suite could not have caught this.** `tests/unit/services/test_qc.py:22` sets
   `_STEP = timedelta(hours=1)` and the file hand-builds every `QcRuleSet` at that cadence —
   which is exactly `_infer_time_step`'s `< 2 rows` fallback. Every test's ruleset is therefore
   guaranteed to match its own fixture, whatever the production ruleset declares. This is why T2's
   red-first test must load the *deployed* ruleset (see T2).

Daily QC does still happen at **onboarding**, whose window is historical and wide enough for the
median to land on 86400 s (`services/onboarding.py` Step 5). So the defect is specific to the
scheduled path — which is the only one that runs after deployment. **Onboarding is fail-open on
identical terms** though: `services/onboarding.py:812` calls `aggregate_qc_status`
(`types/domain.py:104-109`), a second copy of the same empty-list ⇒ `QC_PASSED` rule. T4 must
document both call sites.

### What is NOT yet measured

**How many production stations are affected today.** That needs the live database: the counts of
`(station, parameter)` groups whose real cadence is daily, and how many observations each puts in
a three-hour window. T1 owns that measurement. T1 also owns a **DB-independent** measurement that
can be run today (see T1), so the live query is no longer the only gate on understanding the
mechanism.

## ⚠️ Nepali QC thresholds — ⛔ THE PLAN 305 PREREQUISITE IS WITHDRAWN (2026-09-20)


⇒ **This plan carries NO Plan 305 prerequisite.** What it does carry is the C3 hazard already
documented: the newly-reachable rules land on the sparse/jittered population, where gap-blind
thresholds are most likely to fire wrongly. `spike` (`max_delta = 1.0`) and `gross_outlier`
(`k_sigma = 5.0`) remain Swiss-calibrated: their **values** are 268 D14's answer. 🔑 **The
gap-blindness itself is Plan 313's** (`docs/plans/313-rate-of-change-never-divides-by-elapsed-time.md`)
— `_apply_rate_of_change` compares `|Δvalue|` against a flat `max_rate` without dividing by
elapsed time (`services/qc.py:71-89`), which is exactly why the newly-reachable rules over-flag a
sparse window. **313 mitigates C3; neither plan depends on the other and either may land first.**

## ⭐ The operational picture — added 2026-09-20 from the high-risk review


### C1 — the blast radius, re-framed: no live Swiss group is daily

The headline "twelve daily QC rules, dormant since deployment, become reachable" is true
of the **code path** and **false of the live Swiss fleet**.

One deployment writes observations — `ingest-observations`, `*/5 * * * *` — and its
adapter constrains the surface hard. `HydroScraperAdapter` serves `discharge`,
`water_level` and `water_temperature` only (`adapters/hydro_scraper.py:52-66`), skips
every `WEATHER` config unconditionally (`:124-131`), and LINDAS serves only the **current
value** (`:124` does `del since`) on a ~10-minute publish grid. CAMELS-CH daily data is
loaded at *onboarding* only, where QC already runs at 86400 s over a wide historical
window and the daily rules resolve correctly.

⇒ **Nothing on the live Swiss scheduled path is a daily series**, and 4 of the 26
configured rules (precipitation ×2, temperature ×2) are structurally unreachable on Swiss
regardless of this fix — which makes **D4 a Nepal/Plan-301 question, not a Swiss one**.

**The population that actually changes verdict is the sparse/jittered sub-daily group:**
one whose three-hour window today yields a median that is neither 600 s nor 86400 s (zero
rules, `QC_PASSED`) and whose widened lookback yields 600 s. That is a lagging feed, a
post-outage catch-up, a feed degraded by the known unhandled LINDAS 429s, a station with
intermittent gaps. This does not weaken the case for the fix; it **relocates** it, and
T1's census must be read as measuring this population, not a daily one.

### C3 — 🔴 the fix's beneficiary population and its spurious-flag population are the same set

`_apply_rate_of_change` (`services/qc.py:71-89`) compares `|Δvalue|` between **consecutive
stored rows against a flat `max_rate`** — it does **not** divide by elapsed time.
`_apply_spike` (`:150-174`) is the same shape on `max_delta`. And the deployed thresholds
are explicitly step-sized: `config.toml` pairs `time_step_seconds = 600` with
`max_rate = 50.0` for discharge, while the *daily* rule for the same parameter carries
`max_rate = 500.0` — a 10× difference that exists precisely because the step differs.

Apply the 600 s thresholds to rows 20–120 minutes apart — which is what "sparse window"
means — and **a legitimate flood rise or an ordinary diurnal warming exceeds them.** So
the groups this fix repairs are, by construction, the groups most likely to be flagged
wrongly by the rules it newly applies.

Risk by rule: `range_check` and `gross_outlier` are per-observation and safe;
`frozen_sensor` is low risk (`min_consecutive = 12` rows, unreachable in a sparse
window); **`rate_of_change` and `spike` are the hazard.** This must be named in T4 and
watched in the rollout; it is not a reason not to fix the fail-open.

### C2 — What a changed verdict does downstream

`PgObservationStore.fetch_observations` takes a `qc_status` filter
(`store/observation_store.py:159-187`), and **thirteen call sites pass
`QcStatus.QC_PASSED`** (verified by grep at `9dc07915`):

| Consumer | Sites | Effect of a row leaving `QC_PASSED` |
|---|---|---|
| **Live forecast inputs** | `operational_inputs.py:890, 940`; `track_assembly.py:285, 338` | Row vanishes from the model's autoregressive input; enough dropped rows empty a resampled bucket, `validate_time_step_cadence` raises, and **that station's forecast is skipped for the cycle** |
| **Observation alerts** | `observation_alert_checker.py:58` | Latest `QC_PASSED` row in a 24 h lookback; a flagged peak falls back silently to an older, lower value, and an active alert can **auto-resolve**. `enable_observation_alerts = true` on the mini |
| **Published skill scores** | `compute_skills.py:98` | The verification truth series changes |
| **Training artifacts** | `training_data.py:445` | Baked in until retrain |
| **Hindcasts** | `hindcast.py:473, 688` | Inputs *and* truth |
| **Baselines / flow regimes / onboarding** | `onboarding.py:258, 848, 895` | Feeds back into `gross_outlier` and per-regime skill |
| **Partner forecast-lab export** | `forecast_lab/db_sources.py:125` | Points disappear; the published schema asserts `qc_status: "qc_passed"` per point |

⚠️ **`QC_SUSPECT` is excluded exactly as completely as `QC_FAILED`.** Of the five rules,
only `range_check` emits `QC_FAILED` (`services/qc.py:65`); the other four emit
`QC_SUSPECT` (`:84`, `:141`, `:170`, `:215`). An implementer reading "suspect" as "soft"
would be wrong.

**Both directions are real and they are not symmetric:** a wrong `QC_PASSED` today
corrupts all of the above — that is the defect. A newly-correct `QC_SUSPECT` tomorrow
*removes* data from all of the above — that is the fix's **cost**, and it was unpriced.

🔎 **One mitigating fact to confirm, not assume:** `store_thresholds` has **no production
caller** anywhere in `src/` or `scripts/` (verified — only the Protocol at
`protocols/stores.py:626` and the implementation at `store/station_store.py:189`). If
`station_thresholds` is empty on the mini, `observation_alert_checker` returns at
`if not thresholds: continue` for every station and the alert path is **inert today** —
which would demote the highest-consequence row of that table to latent. It becomes live
the moment thresholds are populated. Query Q3 in T1 settles it.

## 🔴 Cross-plan: this is a blocker for Plan 264 T3

**Plan 264 T3 makes "a non-empty rule set that resolves nothing for a series" an error.** That is
the correct policy, and it was accepted precisely to stop fail-open QC. 264 already carries the
sequencing note (`docs/plans/264-qc-rules-select-on-network.md:252-260`).

**🧾 Debt recorded against Plan 264 — not discharged here.** That note is outside this branch's
scope and is deliberately left unedited, but it is stale in two ways and someone must sweep it
before 264 T3 can be made READY:

1. It cites the QC window as `flows/ingest_observations.py:277-280`; at `9dc07915` the bounds are
   at `:295-296`, and the conditional widening at `:297-309` is not mentioned at all — so 264 still
   describes the window as unconditionally fixed.
2. It still frames the consequence as a **fleet-wide halt on every scheduled run**, driven by
   "every daily `(station, parameter)` group resolves zero rules today". This plan withdrew that
   mechanism (§ What the widening does and does not do, and the bullets below). The conclusion —
   264 T3 lands after or with 272 — survives; the stated reason does not.

### ⭐ What 264 T3 actually does — it does not halt, and the real failure is worse

*Added 2026-09-20 from the owner-commissioned high-risk operations review; verified
against `main` before folding.* Every description of 264 T3 in this plan, in Plan 264's
own sequencing note, and in the memory corpus says a zero-rule group **halts** the
ingest run. **It does not.** 264 T3's raise lands inside the per-group
`except Exception` at `flows/ingest_observations.py:759-767`, which logs
`ingest.qc_failed`, appends to `errors`, adds the station to `qc_failed_station_ids`,
and **continues**. The flow returns a successful Prefect state.

The rows therefore stay at `QcStatus.RAW` — and `RAW` is excluded from every
`qc_status=QC_PASSED` consumer (§ What a changed verdict does downstream). So 264 T3 on
a zero-rule group produces **a silently dark station**: raw data keeps arriving and is
stored, no forecast input sees it, no alert sees it, no skill score sees it, and nothing
re-QCs it, because both QC entry points pick up only `RAW`
(`flows/ingest_observations.py:320`, `services/onboarding.py:781`) — which it already
is, so it is retried every run and fails again, indefinitely.

🔴 **This is worse than a halt, because a halt is visible.** A fleet-wide halt pages
someone; a dark station looks like a quiet station.


Stated as a **hypothesis pending T1** — and note that after D2 nothing *halts* at all; read "halt" throughout this subsection as **"is recorded `QC_UNCHECKED`"** (§ What 264 T3 actually does) — the dominant zero-rule sources on the live fleet are expected
to be, in order:

- **`precipitation` and `temperature`, once Plan 301 lands.** Only daily rules exist for them, so
  every ingested row resolves zero rules. This is latent today — `adapters/hydro_scraper.py:125-131`
  skips `WEATHER` stations, so no precip/temp row reaches this path at all — and goes live with
  Plan 301 (DHM precipitation observations).
- **Sparse windows — a new station, a post-outage catch-up, or a lagging feed.** A window holding
  only a handful of rows puts the median on a value no rule declares, and the `< 2`-row case
  fabricates 3600 s, which the **deployed** rule set declares nowhere either. *(That fabrication is
  not inertly safe — it is safe only against a rule set with no 3600 s row. The DHM mask's rule set
  declares 3600 s throughout, so there the same fabrication silently **matches**. See T2.)*
  **No percentage is attached to this bullet**:
  T1a's five zero-rule windows are a capture-boundary artefact, and the steady-state count in the
  same fixture is 0, so neither number is a fleet rate. What makes this a *structural* source
  rather than an incidental one is not a rate but a certainty — *every* station is sparse on its
  first runs. See § Ordering consequence below.
- **Daily groups are probably a near-empty set live.** BAFU `discharge`, `water_level` and
  `water_temperature` arrive on LINDAS's 10-minute grid (the poll cadence in
  `cli/register_deployments.py:60-92` is sized against that measured grid), so a three-hour window
  holds ~18 rows, the median lands on 600 s, and the 600 s rules resolve today. If that holds, the
  original "every daily group starts raising" describes very few live groups. **T1 must confirm or
  refute it before the exposure is re-costed.**

### Ordering consequence — the reframing inverts the exposure

Once the zero-rule outcome is understood as a **sparse-window** case rather than a **daily-series**
case, Plan 264 T3's fail-closed raise stops being a rare event on a near-empty set of daily groups.
It becomes a *certainty* on **every station's first window**: a station just switched on has fewer
than two rows in the window, `_infer_time_step` fabricates 3600 s, and nothing matches. That is the
state all six DHM stations of Plan 268 occupy on their first run, and the state every station
re-enters after an outage. **The bound on *regime 3* is narrow and is stated precisely in D5** —
that regime lasts only until two rows exist for the group, which on the DHM feed is plausibly the
first ingest itself.

**🔴 But a group leaving regime 3 has not necessarily started resolving rules.** Two rows buy an
*inferred cadence*; whether any rule is selected at it is regime 1 versus regime 2, and regime 2
resolves **zero rules and fails under 264 T3 exactly as regime 3 does** — though *not*
by halting; see § What 264 T3 actually does, below. A group whose real
cadence is one the rule set in use declares nowhere — hourly `precipitation` against
`config.toml`'s daily-only rows (D4), a 1500 s jittered median — sits in regime 2 **permanently**,
not transiently. So the row count is the wrong quantity to reason about here, and the correction
runs through D5, the prerequisite below, and T4.

Two consequences, both binding:

1. **It strengthens the 272-before-264 ordering.** The ordering was previously argued from a halt
   whose size was explicitly unmeasured (§ What is NOT yet measured), which made it an argument
   waiting on T1b. The sparse-window reading gives it a floor that does not depend on T1b at all.
2. **⭐ RECONCILED 2026-09-20 — D2's closure largely dissolves this prerequisite, and the plan
   asserted both without joining them.** The constraint below was written against 264 T3's
   **raise**. D2 removes that raise, so there is no longer a failure to sequence around: a
   zero-rule group is recorded `QC_UNCHECKED` by T2b whenever it occurs, on a cold station's
   first run or in a transient outage, and that is a designed outcome rather than a hazard.
   **What survives is far weaker and far cheaper: T2b's write site must land before, or with,
   264 T3** — otherwise 264 lands against code that still writes `QC_PASSED` over zero rules.
   The "no affected group over a full poll cycle" gate is **withdrawn**; it was a point-in-time
   check on an episodic condition and its object is gone.


**➡️ The owner-facing consequence of those two is D5**, and it is stated there rather than left to
be assembled from here plus T2: while a group resolves no rules — whether because no cadence is
inferable (regime 3, transient) or because the inferred one is declared nowhere (regime 2, which
can be permanent) — that group gets **zero QC**, and (before D5) the only trace was a
`pipeline_health` `WARNING` nothing consumes.

The same mechanism also explains a limitation in **Plan 269**: the ingest path it wires cannot
apply a daily per-station ceiling, because no daily rule is ever selected there for an override to
merge onto. That is why 269 is paused rather than folded again.

## Owner decisions

**D1 — CLOSED 2026-09-19: keep inferring the cadence; fix the matching.**
The owner decided that **the observation cadence stays inferred from the data. No per-station
declared-cadence field is added for v1.** The fix therefore targets the brittle part of inference —
the exact-equality match on an inferred median — and the implementer chooses the mechanism. The
plan does not re-litigate declared-versus-inferred.

Owner's reasoning, recorded: nothing extra has to be captured per station, and inference copes with
a station that genuinely changes reporting rate. The declared-metadata route was rejected partly
because it is one more thing DHM would have to tell us correctly for all six stations, and a wrong
declaration would silently disable checks in exactly the same way the current bug does.

**Binding constraint on any fix:** whatever replaces exact equality, **a cadence that matches no
rule must not read as "no rules apply, therefore pass."** The silent disable is the core of the
defect and it survives any matching strategy if the fail-open is left in place. This is why T2 and
T3 land in the same change (see § Tasks).

*Context, not an argument to reopen:* the **forecast** side already selects on a declared step —
`services/forecast_qc.py:240` calls `ForecastQcRuleSet.rules_for(ensemble.parameter,
ensemble.time_step)` with the ensemble's own declared `time_step`, never an inferred one, which is
why the forecast path is not affected by this defect. The two paths now differ **deliberately**:
a forecast ensemble carries its step as data; an observation series does not, and per D1 will not.

**D2 — CLOSED 2026-09-20. The `UNCHECKED` record REPLACES Plan 264 T3's fail-closed raise.**

**⚖️ Owner decision:** D5's closure supersedes the error. This plan records a zero-rule outcome as
`QC_UNCHECKED`; **264 T3 must not also raise on it.**

**Why this is strictly better than the raise it replaces**, and the reason is § What 264 T3
actually does: the raise never halted anything. It was caught per-group, the rows stranded at
`RAW`, and because both QC entry points pick up only `RAW` they were retried and failed forever —
a station invisible to every consumer with nothing raising an alarm. `QC_UNCHECKED` gives the same
condition an explicit, queryable state — ⛔ **NOT a terminal one: D5 requires it to be re-examined and upgraded while it stays in the checked window (T2b item 8), and a builder who reads "terminal" here will skip that** — keeps forecasts running on marked-degraded
input, and keeps the data out of alerts, scores and training. It converts a silent failure into a
recorded fact.

🧾 **Debt recorded against Plan 264 — not discharged here** (added to the two items above):
**264 T3's fail-closed raise is superseded and must be removed or rewritten** before 264 T3 is made
READY. If it is kept alongside this plan's status write, the two mechanisms fight over one
condition and the raise wins by stranding the row at `RAW` before the status is ever written.

*Interaction with D1's binding constraint:* D1 requires that a zero-rule outcome stop being
recorded as an unmarked clean pass. **D5's `QC_UNCHECKED` discharges that directly**, in the stored
row rather than in aggregated telemetry — so T3's observability is now a *second* signal on top of
a corrected record, not the only thing standing between the defect and an operator.

**D3 — CLOSED 2026-09-20: do NOT backfill; write the limitation down.**
**⚖️ Owner decision — confirmed as recommended**, with the review's three corrections below
attached to it (they *strengthen* the recommendation rather than qualify it). ⛔ The limitation
must be stated in the operator runbook and beside any published figure, **not only in this plan** —
"leave it silently" was explicitly rejected.


Observations already marked `QC_PASSED` having run zero rules are indistinguishable, in the
stored row, from observations that genuinely passed. *Recommendation: do not backfill; record the
limitation.* A re-QC over history is a much larger operation, and `docs/standards/wmo.md` treats a
QC verdict as a record of what was known at judgement time. But the owner should decide whether
any published figure rests on those verdicts.

**⭐ Answered 2026-09-20 by the high-risk review — the recommendation stands and is
stronger than it reads.** Three things the decision did not say:

1. **"Record the limitation" FORECLOSES re-QC; it does not defer it.** There is **no
   re-QC path in the repository.** Both QC entry points self-limit to `RAW`
   (`flows/ingest_observations.py:320`, `services/onboarding.py:781`), and
   `PgObservationStore` exposes only per-row `update_qc`
   (`store/observation_store.py:143`) — **there is no bulk reset** (verified). The only
   mechanism returning a historical row to `RAW` is a *value restatement* through
   `store_raw_observations`' `ON CONFLICT DO UPDATE`. Choosing "do not backfill" therefore
   closes the door until someone builds a `qc_status → RAW` reset — and onboarding's
   Step 5 would additionally have to lift its single-target-parameter restriction.
2. **Yes, published figures rest on those verdicts** — that was the open half of the
   question. Skill scores, the forecast-lab partner snapshot (whose published schema
   asserts `qc_status: "qc_passed"` per point), training artifacts, baselines and flow
   regimes all consume the `QC_PASSED` set with no secondary guard. See § What a changed
   verdict does downstream.
3. **One mitigating asymmetry, not previously recorded: onboarding QC is NOT affected by
   this defect.** Its window is historical and wide, so the daily rules resolve there and
   ⚠️ **but 4 of the 12 do not FIRE there**: `services/onboarding.py:796-802` calls `check`
   with `baselines=[]`, so `_apply_gross_outlier` returns `None` for every row
   (`services/qc.py:205-208`) — the same selected-but-cannot-fire class this plan books
   three times elsewhere. D5's onboarding scope boundary rests on this sentence.
   CAMELS-CH history was properly checked. The corrupted population is confined to rows
   ingested by the *scheduled* path since onboarding — which on Swiss are 10-minute rows
   that mostly resolved their 600 s rules anyway. **The contamination is real but
   bounded.**

**🔴 The rollback control is weaker than this repo's own standard.**
`docs/standards/cicd.md:245` requires a release to be rollback-safe for one version.
`services/qc_datum.py:23-26` stamps `qc_rule_version` as hard-coded `"1.0"` /
`"1.1-datum"` / `"1.1-datum-skip"`, so new verdicts land **indistinguishable from the old
ones** and "record the deploy and rollback timestamps" puts the only boundary marker in a
document. C4 fixes this by bumping the stamp — see T2 § rollout controls.

**D4 — CLOSED 2026-09-20: fixed by the Nepal precipitation work, as ⭐ PLAN 303.**
**⚖️ Owner decision:** add checking rules at the cadence the Nepal feed actually delivers, as part
of bringing that feed in — **not** here. The review's *Swiss-inert* finding is the reason: no
precipitation or temperature observation reaches this path on the live Swiss fleet at all, so
adding threshold values here would change live Swiss QC for no Swiss benefit.

🔴 **This is the one time-sensitive item in this plan. Plan 303 must land BEFORE Plan 301's
precipitation feed goes live**, or every ingested precipitation row resolves zero rules and — after
D5 — is recorded `QC_UNCHECKED`, which by D5's consumer policy means **no precipitation reaches
alerting, scoring or training at all**. The number is granted here so this is a deferral and not a
promise; ⛔ *"a vague promise is not a deferral"* is this plan's own standard.


**D5 — CLOSED 2026-09-20. A group that resolves no rules is recorded as UNCHECKED, not passed.**

**⚖️ Owner decision:** *"'Passed' should mean checks ran and found nothing. A reading nothing could
be run against should say so."* The residual exposure described below is therefore **not**
accepted as a silent `QC_PASSED` — it gets its own status.

**The change: a fifth `QcStatus` member, `QC_UNCHECKED = "qc_unchecked"`.** Written when a group
resolves zero rules, in either regime, **on the scheduled ingest path**.
⛔ **Scope boundary, stated rather than implied:** the *onboarding* path is NOT covered — its
`aggregate_qc_status([])` still yields `QC_PASSED` — and that is accepted because onboarding's
window is historical and wide, so the daily rules resolve there. See T4 § the second fail-open
call site. Implementation facts, measured:
- `types/enums.py::QcStatus` gains the member (currently `RAW`, `QC_PASSED`, `QC_FAILED`,
  `QC_SUSPECT`, `MISSING`).
- `db/metadata.py:526-529` carries a **CHECK constraint enumerating the allowed strings**, so this
  needs a migration to widen it. ⭐ **Exact precedent: `alembic/versions/0002_add_qc_missing_status.py`
  did this for `missing`** — drop the old constraint by both possible names, recreate it widened.
- The `(qc_status = 'missing') = (value IS NULL)` pairing constraint (`:543`) is unaffected: an
  unchecked reading still has a value.

**🔴 And the consumer policy, which is the half a status change alone would get wrong.** All
thirteen consumers filter on `QC_PASSED`, so introducing `QC_UNCHECKED` *without* this clause
would make every unchecked station **go dark** — precisely the 264 T3 failure this plan documents.

**⚖️ Owner decision on that: forecasts YES, alerting NO.**

**Per call site, because T2b's verification says each is touched deliberately** — a
consumer-*category* table cannot be checked off against the code:

| Site | Accepts `QC_UNCHECKED`? |
|---|---|
| `operational_inputs.py:890` (model input) | **Yes** + `input_quality = DEGRADED` |
| `operational_inputs.py:940` (freshness only) | **Yes** — staleness probe; does not by itself degrade |
| `track_assembly.py:285` (model input) | **Yes** + `DEGRADED` |
| `track_assembly.py:338` (freshness only) | **Yes** — as above |
| `observation_alert_checker.py:58` | **No** |
| `compute_skills.py:98` | **No** |
| `training_data.py:445` | **No** |
| `hindcast.py:473`, `:688` | **No** |
| `onboarding.py:258`, `:848`, `:895` | **No** |
| `forecast_lab/db_sources.py:125` | **No** — its schema asserts `qc_passed` per point |

⭐ **Three further readers the `qc_status=QcStatus.QC_PASSED` grep structurally could not reach.**
The thirteen-site inventory was built from one syntactic form and presented as complete. It was
not — recorded here because the *method* was the error, not the count:

| Site | What the grep missed | Policy |
|---|---|---|
| `hindcast.py:201` | in-memory `o.qc_status == QcStatus.QC_PASSED`, not a store filter | **No** |
| `api/routes/stations.py:698` | **exclusion polarity** — `qc_status != "qc_failed"` | **No** — exclude (T2b item 9) |
| `component_derivation.py:36-37` | `_USABLE_STATUSES`, applied **after** an unfiltered fetch | **No** — with a consequence, below |

⇒ **Nothing unchecked is published or learned from, and no directly-measured station goes dark.**

⚠️ **But calculated stations DO go dark, and the blanket claim "nothing goes dark" is WITHDRAWN.**
A component turning `QC_UNCHECKED` fails `derive_point`'s guard, which returns
`DerivedPoint(value=None, qc_status=MISSING)` (`services/component_derivation.py:115`, verified),
so the *derived* station has nothing usable and goes dark one hop downstream — where widening the
forecast read cannot recover it. Measured inert today (no calculated station is configured) and
accepted on that basis; C9's canary must re-check it against the live deployment. See T2b
§ Accepted exception.


- **Regime 3 — transient.** A station with **fewer than two rows in the inference lookback** has
  no cadence to infer (§ The inference lookback, bounded), so **no rule runs on any observation it
  produces** until a second row exists for that `(station, parameter)` group. Measured below: for
  the Plan 268 six that is plausibly one run.
- **🔴 Regime 2 — which can be permanent, and is the part that was missing.** A group with a
  perfectly well-determined cadence that the rule set in use **declares nowhere** also resolves
  zero rules, and no number of further rows changes that. `config.toml`'s daily-only
  `precipitation` and `temperature` rows against an hourly DHM feed are exactly this (D4), and so
  is any station whose real cadence is not 600 s or 86400 s. **Two rows establish inferability,
  not rule resolution** — so the bound below sizes regime 3 only, and the owner is accepting the
  regime 2 exposure as well, which D4 is the fix for.

In either state:

- The stored row **used to read `QC_PASSED` with empty `qc_flags`** — the defect. **T2b now writes
  `QC_UNCHECKED` instead**, so the stored row itself carries the distinction and T3's aggregated
  `WARNING` in `pipeline_health` is a second signal rather than the only one.

**🔴 How large the *regime 3* window is — measured, because an earlier draft of this decision said
"their whole switch-on window" and that overstates it. This bound does not apply to regime 2,
which no row count exits.** Regime 3 is "fewer than 2 rows **in the whole lookback**", not "fewer
than 2 rows in the three-hour checked window", so a group leaves it at its **second stored row**.
Three measurements at `9dc07915` bound it for the Plan 268 six:

1. **The inference fetch reads the store *after* this run's rows are written.**
   `_store_raw_task` runs at `flows/ingest_observations.py:711`; the QC loop that calls
   `_run_qc_task` begins at `:743`. So rows fetched on a given run are already stored when that
   run's inference reads them.
2. **A cold station's first fetch is not one row.** The flow's watermark is
   `now − default_lookback_hours` with `default_lookback_hours: float = 1.0` (`:558`, `:662`) when
   `fetch_latest_timestamp` returns `None` (`:666-667`) — which it does for a station with no
   history — and the DHM adapter then recovers that span at the station's native cadence
   (`adapters/dhm.py:174-204`, windowed by `_window_url` at `:206-220`). On the ~10-minute DHM
   river feed one hour is ~6 rows.
3. **So the six stations plausibly exit regime 3 on their *first* ingest**, and certainly by their
   second. The unchecked window is the first run at most — not the switch-on period. **Exiting
   regime 3 lands them in regime 1 *or* regime 2**; on the ~10-minute river feed the median is
   600 s, which `config.toml` declares, so regime 1 — but that is a measured property of *that*
   feed, not a consequence of having two rows.

The same correction narrows the outage case: with `L` = 30 days, a station returning from an outage
**shorter than `L`** still has rows in the lookback and never enters regime 3 at all. Only an
outage longer than `L`, or a genuinely new group, does.


*Recommendation: accept.* Record in the Plan 268 runbook that T3's `WARNING` names the
affected groups and must be read, since nothing reads it automatically.

**D6 — NEW (round 6). Who restores daily `rate_of_change` and `spike`, which selection alone
cannot reach?**
Measured at `9dc07915` (T2 § What the repair actually restores): T2 makes all twelve daily rules
*selected* for a daily series, but four of them — `rate_of_change` and `spike` on `discharge` and
`water_level` — **cannot fire**, because they read their neighbouring observations from the
**checked** group (`services/qc.py:267-268`) and a three-hour window on a daily series holds at
most one row. They run and return nothing, and T3's telemetry correctly reports the group as
*resolved*, so the shortfall is invisible from the outside.

**⚖️ Owner decision, 2026-09-20: (ii) — a named successor, granted here as ⭐ PLAN 304.**
🔴 **As of 2026-09-23 no `docs/plans/304-*.md` exists**, so by this plan's OWN standard it is
still a promise wearing a number — though `docs/plans/313-...md:362` now records it.
⇒ **Either write 304, or have the gated plan record it.** Its
scope: supply `check` with the neighbouring observations a daily series needs, so daily
`rate_of_change` and `spike` can fire on `discharge` and `water_level` — the two parameters
alerting depends on — without widening what gets flagged. ⛔ Option (iii) was rejected: these are
not low-value checks.

**All seven decisions are CLOSED (D1 2026-09-19, D2–D7 2026-09-20). Nothing in this plan is
gated on an owner decision any more.** What gates implementation now is work, not answers:
**C5a/C5b and C7** (§ The operational picture and § rollout controls) and the T2b scope named
below.
**D7 — NEW (2026-09-20, from the high-risk review). What rate of observations leaving
`QC_PASSED` is acceptable, and what happens to a station whose forecast is skipped
because they did?**


**⚖️ Owner decision, 2026-09-20: set the limit up front and abort the rollout if it is
exceeded.** Confirmed as recommended, and it is now a **rollout gate, not a monitoring
nicety**:

- The threshold is expressed as a **share of rows per station per cycle** that may leave
  `QC_PASSED` **to `QC_FAILED` or `QC_SUSPECT`**. ⛔ **`QC_UNCHECKED` is NOT in the numerator.**
  A permanently-regime-2 group loses 100 % of its rows to `QC_UNCHECKED` *by design*, every
  cycle, so including it forces the threshold above 100 % to avoid aborting on the intended
  outcome — at which point it can no longer catch the C3 spurious-flag hazard it exists for.
  This is also what T2b item 5's separate `unchecked` bucket is for.
- Exceeding it **automatically aborts the canary** (C9): it reverts the flag (C4) **and
  redeploys the compatibility image**. ⛔ **The flag alone does not stop it.** The flag gates
  only the `QC_UNCHECKED` write; the selection change ships unconditionally with T2, so the
  newly-selected rules keep producing `QC_FAILED`/`QC_SUSPECT` after a flag revert — which is
  the very population D7 measures. The compatibility release is already a prerequisite (T2b
  item 10), so this adds no artefact. It does not merely log.
- The owner set the policy; **the number itself must be proposed with T1's measured
  `QC_FAILED`/`QC_SUSPECT` baseline in hand** — a limit chosen before we know the baseline is
  arbitrary. 🔴 T1 owes it.
- ⛔ *"Turn it on and watch"* was rejected for a measured reason: a skipped forecast raises
  no alarm today, so "watch" would mean a human remembering to look.

⭐ **The owner also flagged the deeper fix as the right eventual answer** (option 3 of the
question): a station whose forecast is skipped for missing input should say so. That is not
in this plan's scope and is **recorded as a follow-on for the watchdog**, alongside C4b's
zero-rule probe — the two are the same gap seen from two sides.


## Tasks

#### 🔴 T1b CANNOT BE MEASURED FIRST — the phase order is wrong (found 2026-09-20)

*Discovered by building T1b's census and putting it through two independent reviews before
running it. Both stopped it; the second identified why the task is unanswerable as written.*

**The number T1b asks for — "the proportion of QC-passed observations that had zero rules
run" — is not recoverable from the database.** Verified: `observations` stores `qc_status`,
`qc_flags` and `qc_rule_version`, and `update_qc` (`store/observation_store.py:143-158`)
persists exactly those three. **Nothing records which rules were selected, or how many ran.**

🔑 **An empty `qc_flags` is produced identically by "zero rules resolved" and by "rules
resolved and all passed".** That is the defect's own signature — *the defect erases the
evidence of itself.* This plan already names the same ambiguity one layer up, at T3: *"'Rules
ran and found nothing' and 'no rule resolved' both come back as an empty list"*. It holds at
the storage layer too.

**And a reconstruction is unfaithful in BOTH directions — and worse, the window is
RUN-DEPENDENT.** `_run_qc_task` **expands** `[now−2h, now+1h]` from whatever that run happened
to fetch (`flows/ingest_observations.py:288-309`), so the judgment window was a function of one
cycle's fetch and is not reconstructible from any stored artifact **even with a perfect clock**.
`check` is also handed the whole window including already-QC'd rows while only `RAW` rows are
updated, so a faithful replay would additionally need to know which rows were `RAW` at that
instant. Today's inferred cadence is therefore not the cadence inferred when a stored row was
judged. A row checked cleanly looks zero-rule once its neighbours age out; a genuine
zero-rule row looks healthy once new neighbours arrive. ⇒ **A snapshot can say what the flow
WOULD do now. It cannot say what it DID.**

**🔑 The instrument is `qc_rule_version` — a sentinel value at the zero-rule branch.**

Verified: `observations.qc_rule_version` is `sa.Text`, **nullable, with NO CHECK constraint**
(`db/metadata.py:535`), and **nothing in production branches on its value** — the only readers
are round-trip plumbing. So writing e.g. `"1.0-norules"` where zero rules resolve gives a
queryable marker of zero-selection rows — ⛔ **not D7's numerator** (T1, D7) — with:

- **no new `QcStatus` member** — so no migration, no widened CHECK constraint;
- **no compatibility release** — the previous image's `QcStatus(row["qc_status"])`
  (`store/observation_store.py:318`) never sees an unknown value;
- **no consumer edit, no filter change, no index change** — every read still filters on
  `qc_status`, which does not move;
- **no rollback exposure, and no flag needed**, which removes the gating problem entirely.


🔴 **BUT THE SPELLING MUST BE A SUFFIX, NOT A WHOLE VALUE.** T5 bumps this same field as its
deploy-boundary marker, and `obs_qc_rule_version` already multiplexes datum provenance
(`"1.0"` / `"1.1-datum"` / `"1.1-datum-skip"`). A census written in phase 1b as whole-string
equality would **silently return zero** for every row written after T5 lands in phase 2.
⛔ *This is `feedback_bind_published_numbers_on_values` — a text guard silenced by a change
elsewhere.* **Define a stable `-norules` SUFFIX that survives the version bump, and write the
census against the suffix.** State also which population it marks: **zero rules SELECTED**, not
"zero rules ran" — the datum-skip case already has its own marker and must not double-encode.


🔴 **PARTITION THE CENSUS, or the number is meaningless.** `config.toml` declares
`precipitation` and `temperature` rules **only at 86400 s** (verified), so **sub-daily weather
ingest resolves zero rules BY CONSTRUCTION** — archived Plan 217 records exactly this:
*"weather observations … pass QC because no rule matches them"*. An unpartitioned headline
would be dominated by an **expected** class that T2 does not repair, and a D7 threshold derived
from it would mean nothing. **Partition by `station_kind` / `network` / `parameter`, and
exclude the structural weather population from the zero-selection census, naming it.**

⚠️ **Two erasure paths, so the count is a LOWER bound.** T2b item 8's re-examination overwrites
(already noted), **and `store_raw_observations` resets `qc_rule_version` to NULL on any
value-changing upsert** (`store/observation_store.py:104-108`, verified) — a restated value
wipes the sentinel.


### T0 — The zero-rule sentinel: make the defect countable before changing it

*Added 2026-09-20. The measurement instrument, and the whole of phase 1. It exists because
T1's census is unanswerable without it (§ T1b cannot be measured first) — and because the two
earlier proposals for it, a `QC_UNCHECKED` stamp and a split T2b, were both killed in review
for changing behaviour while claiming not to.*

**Outcome**: a row judged by a group that resolved **zero rules** is identifiable by query,
with **no other behaviour changed anywhere** — same statuses, same flags, same reads, same
plan cache, same rollback surface.

**In**:
- **`services/qc.py`** — `check` computes the selection at `:252-253` and **discards it**.
  Surface it. ⛔ **Behaviour-preserving**: keep the existing window, the exact-equality match
  and the `< 2 rows ⟹ 1 h` fallback exactly as they are. T2 changes those; T0 must not, or the
  baseline measures something other than the unfixed logic.
- **The write site** (`flows/ingest_observations.py:350-356`) and
  **`services/qc_datum.py:23-26`** — write a **sentinel `qc_rule_version`** (e.g.
  `"1.0-norules"`, and the datum variants likewise) when the resolved rule set is empty.

- 🔴 **The signature move, in full.** T0 moves the resolution outside `check` (see the binding
  note below), so it owns **every** site that shape touches — not only `services/qc.py`:
  **`protocols/stores.py:1028-1037`** (the `QualityChecker` Protocol, whose `check` signature
  moves with it), the flow invocation at **`flows/ingest_observations.py:334`**, and the two
  mask invocations at **`scripts/dhm_precip/qc_mask.py:203`, `:208`**, and **every test that
  calls `check`** — measured 2026-09-23: `tests/unit/services/test_qc.py` (**30 calls**),
  `tests/unit/config/test_qc_rules.py`, `tests/unit/scripts/test_dhm_precip_ruleset.py` (4) and
  the mask tests — all mechanical via the same-observations constructor. ⛔ *"and their tests"
  named only the mask tests, leaving 35 calls that break in phase 1 in no task's surface.* ⛔ *These were listed by T3 one phase later
  while T0's binding already forced them, so phase 1 could not be built from its own surface.*
- 🔴 **`services/onboarding.py` — the SECOND call site of BOTH changed functions**, omitted
  from an earlier revision of this list. It calls `checker.check(...)` at `:796`,
  `obs_qc_rule_version(...)` at `:810` and `update_qc(..., qc_rule_version=...)` at `:813-818`.
  Leaving it out gives one of two bad outcomes: changing `check`'s shape **breaks onboarding at
  compile time**, or `obs_qc_rule_version` gains an argument onboarding does not pass and
  **onboarding-backfilled history stamps the ordinary version on zero-rule rows** — a hole in
  the instrument, on exactly the historical population this plan cares about. **Decide which
  behaviour onboarding gets and say so.** *(`calculated_station_onboarding.py:355` and
  `flows/ingest_observations.py:501` write `DERIVATION_RULE_VERSION` on a separate path —
  decide whether derived rows are in or out of the census.)*

🔑 **T0 builds T3's FINAL resolution shape, not a throwaway one.** *(Simplified 2026-09-22
after independent review, which found the churn avoidable and the claim of inevitability
false.)* T3's shape is binding — the resolution computed *outside* `check` and passed *in*,
with `check` no longer calling `_infer_time_step`/`rules_for` itself — so **T0 introduces that
shape directly**, initially computing it from the existing checked window with exact matching
and the `< 2 rows ⟹ 1 h` fallback, which reproduces today's behaviour exactly. T2 then changes
only what feeds it: the inference lookback and the fallback. ⇒ **One interface, introduced
once.** ⛔ *An earlier revision required T0 to build an outward-facing selection result that T3
would later delete, and told the implementer the resulting churn plus a second caller migration
was unavoidable. Both are removed: there is no temporary interface and no second migration.*

**Out**: ⛔ **no `QcStatus` member, no migration, no CHECK-constraint change, no compatibility
release, no consumer or filter edit, no index change, no counter change, and no flag** — none is needed, which is
the entire point of choosing this carrier. `QC_UNCHECKED` and its consumer policy stay in T2b,
landing with T2.


**Verification**:
- A zero-rule group's rows carry the sentinel; a group that resolved rules carries the
  ordinary version. Asserted on the stored row.
- 🔴 **Inertness, asserted not claimed — as a DETERMINISTIC REPLAY, not a live before/after.**
  **Run both implementations from an identical database snapshot with a fixed clock, fixed
  fetched rows and fixed config** — T6's paired-harness shape — and require the resulting rows
  **and the existing counters** to be identical except for the sentinel.

**Pre-change**: a RED test proving that today a zero-rule group and a clean pass are
**indistinguishable in the stored row** — identical `qc_status`, identical empty `qc_flags`,
identical `qc_rule_version`. That indistinguishability is the defect; this task ends it.

### T1 — Measure the blast radius

**Outcome**: a measured count of how many `(station, parameter)` groups currently resolve zero
rules on the scheduled path, and why — **and, from the same census, a proposed value for D7's
loss threshold** (the share of rows per station per cycle that may leave `QC_PASSED` before the
canary aborts). ⭐ **Added 2026-09-20: D7's prose said "the number comes from T1" and T5's said
"not set here", but T1 owed no such deliverable — the number belonged to nobody.** It is a T1
deliverable and a T1 verification item; a threshold chosen without the baseline is arbitrary.
Two deliverables — one runnable today, one needing the DB.

**T1a (DB-independent — run this first).** Slide the real `[now − 2 h, now + 1 h]` window across
the captured DHM day `docs/requirements/dhm-api-examples/day.json` on a 10-minute grid, apply
`_infer_time_step`'s own rule (median gap; 1 h for `< 2` rows), and count the windows whose
inferred cadence matches no configured `water_level` rule (600 s or 86400 s).

*Measured at `9dc07915`, 2026-09-19:* the file holds 118 unique timestamps spanning
`2026-05-04T18:15Z … 2026-05-05T18:05Z`, with inter-row gaps `600 s ×104, 1200 s ×6, 1800 s ×3,
2400 s ×3, 3600 s ×1`. Over the 144 windows, **5 (3.5 %) infer a cadence matching nothing** —
⚠️ **Re-running the slide under the store's own half-open `[start, end)`
(`store/observation_store.py:178-179`) gives 6 (4.2 %), five at 1500 s and one at 2400 s; the 5
holds only under a closed or open-start interval. T1b must state which convention it uses.**
four at 1500 s and one at 2400 s — and therefore select zero rules and report `QC_PASSED`.

*Caveat, and it matters.* All five sit in the **first five** windows, where `now − 2 h` precedes
the start of the captured file, so the window sees only 3-5 rows. Restricting to the **126**
windows whose full three hours lie inside the captured span gives **0** zero-rule windows: with a
full-width window the 104 ten-minute gaps dominate the median and it resolves to 600 s.
**What this
establishes is that a *sparse* window mis-infers — which happens in production on a new station,
after an outage, or when the feed lags — not that 3.5 % of steady-state windows mis-infer.** The
implementer must re-run the same slide against real stored history (T1b) to separate the two.

This is still decisive for the fix: a median over few rows lands on values no rule declares, and
exact equality then selects nothing.

**⭐ C7 (added 2026-09-20) — T1b must be a LOOP, not a snapshot.** The high-risk review's
central operational point: the repairable population on Swiss is a *transient degraded
state*, so a single census is close to worthless. Four read-only queries, runnable inside
the ingest container; **Q2 must run on a ≥ 24 h loop**, ideally over a window containing a
known LINDAS incident.

- **Q1 — is any live group genuinely daily?** Median inter-row gap per `(network,
  parameter)` over 30 days. This settles C1's claim that nothing on the live Swiss path is
  daily. If Q1 finds a daily group, C1 is wrong and the blast radius is larger.
- **Q2 — the regime census as the scheduled path sees it.** Per `(station, parameter)`
  over the real `[now − 2 h, now + 1 h]` window: `< 2 rows ⇒ regime 3`; median ∈ {600,
  86400} ⇒ regime 1; else regime 2. **Run every 5 min for ≥ 24 h.** This is what tells the
  owner whether the repairable population is ten groups or zero.
- **Q3 — `SELECT count(*) FROM station_thresholds;`** Settles whether observation alerting
  is armed or inert, and therefore **re-prices the highest-consequence row** of
  § What a changed verdict does downstream.
- **Q4 — fleet shape:** `SELECT network, station_status, count(*) FROM stations GROUP BY 1,2;`

⛔ **Do not treat Q1–Q4 as optional pre-reading.** T1b as previously written is a single
snapshot of an episodic condition, and the plan's rollout gate inherits that weakness
(C5b).

**One distinction T2 depends on, so do not read this task as an argument against widening.**
Widening the **QC window** — the rows that get checked — does not help a genuinely gappy series,
because the gaps come with it. Widening the **inference lookback** — the rows used only to decide
what cadence the series reports at — is a different change and is exactly what T2 does. T1a
measures the first and says nothing against the second.

- 🔑 **Q2 does NOT supply D7's threshold.** Q2 classifies cadence regimes — the zero-selection
  population, which D7's numerator **excludes by design** (§ D7). ⛔ *Two folds in a row tied
  D7's number to Q2; both were wrong, for opposite reasons.*
- 🔴 **D7's baseline is the CURRENT per-station, per-cycle `QC_FAILED`/`QC_SUSPECT` fraction, and
  Q1–Q4 do not measure it.** T1 must also read it — a read-only query over the existing counters
  the flow already emits (`flows/ingest_observations.py:320`, `:352-373`, summed at `:756-758`);
  no new mechanism, and it is the same shape the canary compares against.

**In**: a read-only query against the staging database (T1b) and a local analysis over the checked-in
fixture (T1a); both outputs recorded in this plan; no code change.
**Out**: ⛔ any code change, any write, any schema or counter addition, and any attempt to
reconstruct the historical contamination — § What is NOT yet measured explains why it cannot be.
**Verification**: both measurements recorded here with their method and the SHA measured against.
**and** the proposed D7 threshold, with the measured `QC_FAILED`/`QC_SUSPECT` baseline it was
derived from — the deliverable T1's Outcome promises.
**Pre-change**: N/A — measurement.

### T2 — The fix

**Outcome**: a configured rule is selected for the series it was written for, and a series for
which nothing can be selected stops reading as a clean pass.

#### The three regimes, and what each must produce

This is the part two implementers would otherwise build in opposite directions, so it is settled
here rather than left to the mechanism.

**🔴 Matching is relative to the rule set in use — not to 600 s and 86400 s.** An earlier draft of
this table named the deployed config's two cadences literally and filed "the fabricated 3600 s"
under *Sparse*. That is wrong twice over, because **T2 also governs a second rule set**:
`scripts/dhm_precip/qc_ruleset.py:38` declares `QC_MASK_TIME_STEP = timedelta(seconds=3600)` and
all three of its rules carry it (`:59`, `:72`, `:86`), and it is run against a **dense hourly**
series. Read literally, the old table made a clean hourly series "sparse" and silently emptied the
Dudh Koshi mask; read tolerantly, a matcher wide enough to rescue it would also fold 1800 s and
7200 s onto those 3600 s rules. The third regime is therefore **not a row-count property** and is
renamed accordingly.

| Regime | What the inference lookback establishes | Required outcome |
|---|---|---|
| **1 — a declared cadence matches** | ≥ 2 rows, and the inferred cadence is one the **rule set in use** declares (600 s or 86400 s under `config.toml`; 3600 s under the DHM mask rule set) | the rules at that declared cadence are selected |
| **2 — no declared cadence matches** | ≥ 2 rows and a well-determined cadence, but the rule set in use declares no rule at it (1500 s or 2400 s against `config.toml`; 1800 s against the DHM mask's 3600 s; **hourly `precipitation` against `config.toml`'s daily-only rows** — see D4) | **the marked zero-rule outcome from T3 — never the nearest declared cadence** |
| **3 — no cadence is inferable** | fewer than 2 rows in the whole lookback | **the marked zero-rule outcome from T3** |

**Regime 3 is the one that changes behaviour, and it must.** `_infer_time_step` today *fabricates*
`timedelta(hours=1)` for a group of fewer than two rows (`services/qc.py:40-42`). Against
`config.toml` that fabrication matches nothing, so the bug presents as a fail-open; against the DHM
mask's 3600 s rule set **the same fabrication matches every rule**, and one row is handed the
hourly thresholds. The fabricated value must therefore stop being treated as an inferred cadence at
all: fewer than two rows means we do not know the cadence, whatever any rule set happens to
declare. Regime 3 is *that* statement, not a statement about sparseness.

**🔴 Kill the fabrication AT SOURCE, not at the call site: `_infer_time_step` returns
`timedelta | None`.** This is a required part of T2, not an implementer's choice of style. Today
the function is `def _infer_time_step(obs: list[Observation]) -> timedelta` with
`if len(obs) < 2: return timedelta(hours=1)` (`services/qc.py:40-42`). **Drop that branch and
widen the return type to `timedelta | None`**, returning `None` for fewer than two rows. The
reason is mechanical, not aesthetic: the resolution's constructor is the obvious place to write
`cadence = _infer_time_step(obs)`, and while the function keeps its total `timedelta` return type
that one line **silently restores regime 3 as a fabricated 1 h** — the exact defect, now inside
the object built to represent regime 3 as `None`. A constructor-level guard (`if len(obs) < 2`)
leaves the trap armed for the next caller; changing the signature makes the type checker refuse
the mistake, which is this repo's stated preference for making invalid states unrepresentable.

**Sweep of what consumes the fallback, measured at `9dc07915`.** `_infer_time_step` has exactly
**two** references in the repo: its definition (`services/qc.py:40`) and one call
(`services/qc.py:252`, inside `check`, which T3 removes in favour of the resolution). The only
*other* consumer of the 1 h fallback is the mirror `_inferred_time_step`
(`scripts/dhm_precip/qc_mask.py:127`, called at `:154`), which this task **deletes** (§ The second
implementation), so the fallback has no surviving consumer once the mirror is gone. Confirm this
by grep in the diff rather than by assumption; if a consumer appears, it is a caller this plan did
not know about and it changes the *In* surface.

**Why regimes 2 and 3 do not get the nearest declared rules.** From inside a three-hour window a genuinely
daily series and a sparse 10-minute series are *the same observation* — both yield a cadence no
rule declares. A matcher that always resolves *some* rule therefore cannot tell them apart, and
would hand a 10-minute station rules calibrated for a 24-hour step: a `rate_of_change` or `spike`
threshold sized for a day applied to ten-minute rows is so loose it passes almost anything, and a
daily `precipitation` accumulation ceiling applied to one ten-minute row is meaningless. That is
not a weaker check — it is **a silent mis-QC dressed as a passing check**, which is the exact class
of defect this plan exists to remove. Guessing is worse than declining. A matcher that always
finds something would also make T3 dead code.

**How the clean-daily regime is then satisfiable at all — the inference fetch widens.** Given the
above, a three-hour window alone *cannot* distinguish a genuinely daily series (regime 1) from a
sparse sub-daily one (regimes 2 and 3), so no matching strategy confined to that window can put a
daily series in regime 1 at all. The resolution is to
**decouple the rows used to infer the cadence from the rows being checked**: infer the series'
cadence from a longer lookback, then apply the selected rules to the three-hour window's rows as
today.

**This is in scope.** Owner decision D1 closed on *"keep inferring the cadence from the data"*; it
did not say the data must be only the three hours in front of the checker. Widening the inference
lookback is squarely inference, not declaration, and it is the only resolution that does not
require reopening D1.

The residual case stays honest: a station whose history is too short even for the widened lookback
(genuinely new, or long dark) has no inferable cadence, and gets T3's marked zero-rule outcome. We
do not know its cadence yet, so we do not guess one. That is regime 3, and it is the whole content
of **D5** — see § Ordering consequence for what it means for Plan 264 T3.

**⚠️ Nearest-absolute matching is the wrong first reach.** It is the obvious mechanism and it is
ruled out by the table above, so it is named explicitly: |3600 − 600| = 3000 s versus
|3600 − 86400| = 82800 s, so nearest-absolute maps the fabricated 3600 s fallback — and 1500 s and
2400 s with it — onto **600 s, not 86400 s**. A daily station with one row in the window would be
handed the ten-minute thresholds. T2's verification clause already forbids this outcome; this
sentence exists so an implementer does not have to rediscover it.

**⚠️ And a bound on any tolerance that *is* introduced.** Note what the widened lookback does to the
premise: over a lookback long enough to hold tens of rows, a clean series' median gap lands *on*
its true cadence, not near it — so **exact equality already satisfies regimes 1-3, and a tolerance
is optional, not required**. If the implementer adds one anyway (for jitter — the DHM feed's
timestamps carry stray seconds, `docs/plans/301-dhm-precipitation-adapter.md:62-64`), it must be small
enough to keep every row of the table true. **≤ ±10 % relative** does, and the worked numbers are:
1800 s vs 3600 s is 50 % — excluded, so the DHM mask's 30-minute case still raises (below);
1500 s vs 600 s is 150 % and 2400 s vs 600 s is 300 % — both still regime 2; a jittered 601 s still
resolves to 600 s. **A tolerance that lets 1800 s resolve onto 3600 s is out of contract**, because
`_apply_frozen_sensor` counts **rows** (`services/qc.py:133`, `run_length = i - run_start + 1`,
compared at `:134` against `min_consecutive` from `:98`) while the DHM rule set expresses that
threshold in **hours** (`minimum_run_duration_hours` = 12, `qc_mask_long_zero_run_min_consecutive_hours`
= 168, `scripts/dhm_precip/params.py:110,148`). Rows equal hours **only because the series is
hourly** — which is exactly what the guard enforces today. Applying those rules to a 30-minute
series would halve every run-length threshold in real time, silently.

#### The inference lookback, bounded

The widening is in scope (above) but it was asserted without a size, and it lands on a flow that
runs `*/5 * * * *` (`cli/register_deployments.py:44`) over the whole fleet. Unbounded, it is a
sequential full-history scan per group per run. The bounds are therefore part of the contract, not
an implementation detail:

**🔴 `L` and `N` below are STARTING BOUNDS derived from what a median needs — they are not
measured fleet costs.** No query has been run against the live database at either value, and
nothing here establishes what the extra fetch costs on the real fleet. They are stated as contract
so the implementation is bounded *by construction* rather than left open; the *cost* is settled by
the paired pre-rollout evaluation and the operational latency monitoring below, and either may send
them back for resizing. An implementer must not cite them as evidence that the change is cheap.

- **Lookback `L` = 30 days.** The binding case is a genuinely daily series: it must yield enough
  rows for a stable median, and 30 days yields ~30 rows / 29 gaps. Shorter windows get thin fast
  (7 days is 6 gaps, and a single missed day moves the median). Chosen from that arithmetic alone.
- **Row cap `N` = 50 most recent rows.** `N` rows give `N − 1` gaps *at any cadence*, so the cap is
  cadence-independent and self-scaling: 50 rows is ~8.2 h of a 10-minute series, ~2 d of an hourly
  one, and all of a daily one inside `L`. ⚠️ **`N` also bounds the repairable DURATION of a
  degradation episode, and this is not a cost knob:** a group is repaired only while the off-grid
  gaps stay a minority of the most recent `N`, so once an episode has run ~`N` × its degraded
  cadence (≈8 h at 1200 s — the median flips once 25 of the 49 gaps are degraded, not 50) the
  widened lookback returns the same off-grid median and the group
  stays regime 2. The paired evaluation defines "Repairable" against the widened lookback, so it
  cannot surface that shortfall either.
- **🔴 The cap must be a SQL `LIMIT` over `ORDER BY timestamp DESC`, not a client-side slice.**
  Truncating in Python bounds the median's input and nothing else — the database still reads the
  whole 30 days. This is the difference between a bounded change and an unbounded one.
- **Measured context the implementer must not rediscover.** `PgObservationStore.fetch_observations`
  (`store/observation_store.py:160-188`) filters `station_id`, `parameter` and a `timestamp` range.
  **No index on `observations` contains `parameter`** — verified at `9dc07915`, there are three:
  `ix_observations_station_timestamp` and the partial `ix_observations_station_timestamp_qc_passed`
  on `(station_id, timestamp)` (`alembic/versions/0001_v0_schema.py:253-263`) and
  `ix_observations_station_source_ts` on `(station_id, source, timestamp)`
  (`alembic/versions/0035_observation_forecast_rating_curve_binding.py:80-84`). So `parameter` is a
  **heap filter**: a backwards index walk reads every parameter's rows in the range and discards
  the others. With the `LIMIT`, the walk stops after `N` matches — **but only if `N` matches
  exist.** 🔴 **An earlier draft named only the zero-row case; the full-`L` walk happens for *any*
  group with fewer than `N` matching rows in `L` — that is 0-49 rows at `N` = 50, not 0.** A group
  with 40 daily rows in 30 days never reaches the `LIMIT` and walks the whole range; so does a
  parameter that reports sparsely beside dense siblings, which is an ordinary configuration, not a
  pathology. `L` is what bounds that walk: ~30 d × 144 rows/d × 3 parameters ≈ 13 k index entries
  per such group per run. **That is the number the pre-rollout latency measurement has to confirm
  is affordable** — it is arithmetic, not a measurement, and a fleet with many sparse groups pays
  it on every one of them every five minutes.
- **The store needs a bounded entry point.** `fetch_observations` takes no `limit` and orders
  ascending (`store/observation_store.py:181`), so it cannot express the cap. T2's *In* surface
  therefore includes `store/observation_store.py` and `protocols/stores.py` — an **additive**
  method (a `fetch_recent_observations(station_id, parameter, not_before, limit)` returning
  chronological order is the obvious shape), not a change to `fetch_observations`, whose ordering
  guarantee is load-bearing elsewhere (see the Plan 228 comment at `:175-180`).

**🔴 The checked window is a separate, narrower fetch — this is two fetches, not one widened one.**
`flows/ingest_observations.py:295-309` computes `[now − context_window_hours, now + 1 h]` (plus the
DHM widening) and `:311-316` issues the **single** `fetch_observations` that feeds inference *and*
`checker.check` (`:334-340`) alike. The prose above says the checked window stays at three hours;
that is only true if the inference lookback is a **second** fetch. **An implementer who widens the
existing fetch instead changes what gets checked**, and the damage is specific: `frozen_sensor` is
a row-run rule and is present at 600 s for `discharge`, `water_level` and `water_temperature`, so a
30-day window would start finding runs that a 3-hour window never could — changing verdicts on
groups T1b never named, which the paired pre-rollout evaluation below would correctly surface as a
regression.
State in the diff that the two fetches are deliberately separate and why.

**Query count and duration — bounded by what actually ingested.** The extra fetch is one per
`(station, parameter)` per run, and that set is `station_params` at
`flows/ingest_observations.py:716-718`, derived from `raw_obs` — **only groups that received rows
this run**, not the whole fleet. The loop at `:743-755` is sequential (no `task.map`), so the added
latency is additive and must be measured, not assumed: see the post-deploy checks below.


#### What the repair actually restores, and what it does not

**🔴 Selecting a rule is not the same as that rule being able to fire.** T2 makes the daily rules
*selected* for a daily series; whether a selected rule can produce a verdict depends on the rows in
the **checked** window, which this plan deliberately leaves at three hours (previous paragraph).
For a daily series that window holds **at most one row**, and `Stage1QualityChecker.check` derives
each rule's neighbours from that same group (`services/qc.py:267-268` —
`prev = group[i - 1] if i > 0 else None`, `nxt = group[i + 1] if i < len(group) - 1 else None`).
Measured at `9dc07915` by re-parsing `config.toml`'s 12 daily rules and reading each rule body:

| Daily rule | Count | Context it needs in the checked group | Effective after T2 on a daily series? |
|---|---|---|---|
| `range_check` | 4 (`discharge`, `water_level`, `precipitation`, `temperature`) | the observation alone (`services/qc.py:50-68`) | ✅ **yes** |
| `gross_outlier` | 4 (same four parameters) | the observation plus a `ClimBaseline` row for its day-of-year (`services/qc.py:197-222`; the baseline lookup is `:205-208`, fetched by the flow at `flows/ingest_observations.py:326`) | ✅ **yes, where a baseline exists** — a pre-existing precondition, not a context shortfall |
| `rate_of_change` | 2 (`discharge`, `water_level`) | the **previous** observation; returns `None` when `prev is None` (`services/qc.py:77-78`) | ❌ **no — never fires** |
| `spike` | 2 (`discharge`, `water_level`) | the **previous and the next** observation; returns `None` when either is `None` (`services/qc.py:157-158`) | ❌ **no — never fires** |

*(`frozen_sensor` does not appear: `config.toml` declares it only at 600 s, so it is not one of the
twelve. It would be ineffective for the same reason — it counts rows, `services/qc.py:133-134`.)*

**So the honest claim is: T2 restores 8 of the 12 daily rules, not 12.** The remaining four are
selected, run, and return nothing — and **T3's telemetry would report the group as resolved**,
because it resolved. That is a *second* variety of "reassuring telemetry over an unchecked series",
narrower than the one round 6 found in T3 but the same shape, and it is disclosed here rather than
discovered later.

**Disclosed, not fixed — and why.** The alternative is to supply the missing context: widen the
checked window for a daily series, or fetch neighbouring rows separately and pass them as context
while flagging only the window's own rows. Both are real designs and **both are out of scope
here**, because widening what gets checked is exactly the change the previous paragraph forbids —
`frozen_sensor` is a row-run rule present at 600 s for three parameters, so a wider checked window
changes verdicts on groups T1b never named. Restoring the temporal daily rules is therefore a
**follow-on**, and **it has an owner decision: D6**, added in this fold on D4's precedent — name
the owner or do not claim the coverage.

**This does not weaken the case for T2.** `precipitation` and `temperature` — the two parameters
that go structurally un-QC-able when Plan 301 lands (D4) — declare *only* `range_check` and
`gross_outlier`, both per-observation, so for them the repair is **complete**. The shortfall is
confined to `discharge` and `water_level`, which already have their 600 s rules resolving today on
the BAFU feed (§ Cross-plan).

**In**: per D1 — the exact-equality match. `types/domain.py:160-167` (`QcRuleSet.rules_for`) and
its caller `services/qc.py:252-253` (`_infer_time_step` at `:40-47`, **whose signature becomes
`-> timedelta | None` with the `< 2 rows ⟹ 1 h` branch at `:40-42` deleted** — see § Regime 3 is
the one that changes behaviour), **which T3 moves out of
`check` into the shared resolution object** (§ The resolution is ONE object — T2 and T3 are one
landing, so this is one edit, not two); **plus a second, bounded
inference fetch in `_run_qc_task` alongside the existing one at
`flows/ingest_observations.py:295-316`**, and the additive bounded store method it needs
(`store/observation_store.py`, `protocols/stores.py`) — see § The inference lookback, bounded;
**plus the mirrored implementation in `scripts/dhm_precip/qc_mask.py`** (see § The second
implementation, below). **Plus a byte-identical-output test over a pinned hourly series** (see
§ The backstop the out-of-scope entry assumed).
**Out**: no new rule kinds; no threshold changes (D4 closed 2026-09-20 to Plan 303, so none here); no change to what a
*selected* rule does; no per-station override behaviour (Plan 269); no declared-cadence field
(D1, closed); **no change to `ForecastQcRuleSet.rules_for` (`types/domain.py:260-267`)** — it is
byte-identical to the observation one, but it is called with the ensemble's *declared* step
(`services/forecast_qc.py:240`) and is not affected. State in the diff which of the two is being
changed, so the identical twin is not edited by accident.

#### The second implementation of the thing being fixed

`scripts/dhm_precip/qc_mask.py:127-141` defines `_inferred_time_step`, a **deliberate mirror** of
`services.qc._infer_time_step`. Its docstring asserts the equivalence in terms: it *"Mirrors
`services.qc._infer_time_step` exactly — same fallback (1h when fewer than 2 observations), same
median-of-diffs formula — so the guard raises precisely when, and only when, the production checker
would have silently matched no rules."* `:143-163` `_raise_on_time_step_mismatch` uses it to raise
`TimeStepMismatchError` on **every** declared rule whose `time_step` differs from the inferred one
(D5 in that script), and it is called at `:202` and `:207` from `_station_mask`. The rule set it
guards declares 3600 s throughout (`scripts/dhm_precip/qc_ruleset.py:38`,
`QC_MASK_TIME_STEP = timedelta(seconds=3600)`).


**Decision: update, by deletion of the duplicate — not by re-synchronising it.**
- `_inferred_time_step` is **deleted**; the script imports the production cadence function instead.
  A mirror whose correctness argument is "it matches the original exactly" has no defensible
  version other than being the original.
- `_raise_on_time_step_mismatch` is **kept** and re-expressed against the production *selector*
  rather than against `==`: **a rule is mismatched iff a cadence was inferable for this series and
  that rule is absent from the set the production matcher selects for it.** The leading clause is
  load-bearing and is not decoration — see below. So re-expressed, the guard preserves D5's intent
  — reject every rule that would be silently skipped by a *mismatch* — under any matching strategy
  T2 chooses, and it cannot drift again because there is nothing left to drift from.
- Leaving it alone is rejected: the guard would raise on valid rule sets and break the handover
  mask, and it would do so *after* a green suite had reported otherwise.
- `tests/unit/scripts/test_dhm_precip_mask.py:176-183` and `:185-216` must be re-pointed to the new
  contract in the same change; a green run of that file is **not** evidence the mirror is fine.
  **"Re-point" here means what the next section states, case by case — not "delete the assertion".**

#### What the re-expressed guard does on each named case


**(a) A station — or a single JJAS season — with exactly one observation. Required outcome: NO
raise; the build completes. On mask keys the two cases differ — a one-row *season* contributes
none either way, a one-row *station* can lose one. See the qualification below.**

Measured at `9dc07915`: `_raise_on_time_step_mismatch` returns early on an *empty* list (`:152-153`)
and `_station_mask` returns `frozenset()` on an empty station (`:197-198`), so **zero** rows is
already guarded twice. The exposed case is **exactly one** row, where the guard runs, today infers
the fabricated 1 h, matches the 3600 s rules, and builds fine. Under a naive re-expression — "fires
iff the selector declines" — regime 3 makes the selector decline, every rule reads as absent, and
`TimeStepMismatchError` **hard-fails the handover build**. That is not hypothetical: neither
`build_mask` (`:216-241`) nor `iter_station_results` (`:322-358`) applies any minimum-length filter,
and `_jjas_seasons` (`:166-180`) emits one entry per year that has *any* row — a one-row season is
ordinary in patchy DHM precipitation. The inferability clause is what prevents it: when no cadence
is inferable, no rule can have been "silently skipped by a mismatch", because there was no cadence
to mismatch. The group is simply too short to check — the mask's correct answer is the one it
already gives for an empty station.

**⚠️ The qualification on "no mask keys, exactly as today" — true for a season, not for a station.**
An earlier draft of this case claimed both. Measured at `9dc07915`:

- A one-row **JJAS season** contributes no keys either way, exactly as claimed. Pass B is the
  long-zero-run `frozen_sensor` alone (`PASS_B_RULE_VERSIONS`, `scripts/dhm_precip/qc_ruleset.py:49`),
  whose `min_consecutive` is `qc_mask_long_zero_run_min_consecutive_hours` = 168
  (`scripts/dhm_precip/params.py:148`), and `_apply_frozen_sensor` counts **rows**
  (`services/qc.py:133-134`). One row cannot reach 168, so pass B cannot fire today and cannot fire
  after T2.
- A one-row **station** is different. Pass A carries `range_check`
  (`PASS_A_RULE_VERSIONS`, `scripts/dhm_precip/qc_ruleset.py:44-46`), which today matches the
  fabricated 3600 s and **can** flag a single out-of-range value, producing a mask key. After T2
  that row is regime 3, no rule runs, and such a station **loses its key**. (Pass A's other rule,
  the stuck-value `frozen_sensor`, needs `minimum_run_duration_hours` = 12 consecutive rows,
  `scripts/dhm_precip/params.py:110`, so it is unreachable at one row.)

Real, and tiny — it needs a station whose *entire* record is one row and whose single value is out
of range. But it is a change in mask **content**, not a no-op, so it must be seen rather than
assumed: the byte-identical-output fixture below carries a one-row station for exactly this reason,
and this key is the one documented delta that fixture is allowed to show.

**(b) The 30-minute series at `tests/unit/scripts/test_dhm_precip_mask.py:176-183`. Required
outcome: the raise STANDS, and the test's assertion is unchanged.**

30 rows at 1800 s is regime 2 against a rule set declaring only 3600 s: a cadence *is* inferable,
and all three rules are absent from the selection, so every one of them would be silently skipped —
exactly D5's failure mode. The guard must still raise. **This makes the test a constraint on T2's
matcher rather than a casualty of it**: if a tolerance is ever introduced wide enough to fold
1800 s onto 3600 s, this test goes green for the wrong reason and the run-length-in-rows-versus-hours
hazard above goes live. The ±10 % bound exists to keep this assertion true. `:185-216`
(`test_a_single_mismatched_rule_among_matching_rules_still_raises`) likewise stands: an hourly
series against a rule set mixing a 1 h rule and a 30-min rule still leaves the 30-min rule absent
from the selection, so it still raises with `match="mismatched-30min"`.

**The net contract change, stated so a reviewer can check it in one line:** against the **deployed
DHM mask rule set** the guard **loses no raise and gains none**; it loses one raise **only where a
rule set mixes cadences**. The deployed set declares 3600 s throughout
(`scripts/dhm_precip/qc_ruleset.py:38`, carried at `:59`, `:72`, `:86`), so a one-row group's
fabricated 1 h matches *every* rule today and does not raise either — which is exactly what case
(a) above records as "builds fine". The lost raise is real but conditional, and the mixed set at
`tests/unit/scripts/test_dhm_precip_mask.py:185-216` (a 1 h `range_check` plus a 30-min
`frozen_sensor`) is where it shows: a **one-row** group against *that* set raises today, because
the fabricated 1 h leaves the 30-min rule mismatched, and after T2 it is regime 3 and does not.
*(An earlier draft said flatly "loses one raise and gains none", which contradicted its own case
(a) one paragraph above.)* **Any implementation that gains a raise, or loses the 30-minute one at
`:176-183`, is wrong** — that operative constraint is unchanged.

#### The backstop the out-of-scope entry assumed

§ Explicitly out of scope requires the mask's output for an hourly series to be byte-identical
before and after, and an earlier draft said to "assert it against the existing golden fixture".
**There is no golden fixture.** Verified at `9dc07915`: `tests/unit/scripts/test_dhm_precip_mask.py`
is entirely behavioural — 18 `def test_` functions, no snapshot, no fixture file read — and no
`dhm_precip` golden artefact exists anywhere under `tests/` (the only snapshots in the repo are
`tests/fixtures/plan151_t8b_canonical_snapshot.json` and
`tests/fixtures/forecast_lab/forecast_lab_snapshot_example.json`, both unrelated).

**Chosen: create one, as an explicit T2 deliverable.** A byte-identical-output test over a pinned
hourly series — construct a multi-station, multi-season hourly fixture in code (it must exercise
both passes: a stuck-high run above `stuck_high_min_value_mm`, a ≥168 h zero run inside JJAS, an
out-of-range value, and at least one clean station), capture `build_mask`'s `frozenset[MaskKey]`
and `build_removal_accounting`'s rows as a checked-in artefact, and assert equality. Generate the
artefact from the **pre-change** code and commit it *before* the T2 edit, so it pins today's
behaviour rather than tomorrow's. This is the only backstop the mask has against both (a) and (b)
above, and against T2 quietly changing what a *selected* rule does; without it the out-of-scope
entry asserts a property nothing checks.

**🔴 Two required contents that "a pinned hourly series" does not imply, and one allowed delta.**
The fixture must additionally carry **(i) a station whose entire record is one row, that row out of
range**, and **(ii) a one-row JJAS season on an otherwise multi-season station**. Neither arises
from a pinned hourly series, and (i) is the only case in which the T2 edit changes mask *content*
(§ What the re-expressed guard does, case (a)). The assertion is therefore **"identical except for
the one-row station's `range_check` key, which T2 removes"** — a single, named, reviewed delta, and
a difference anywhere else fails the test. Stating it this way is what keeps the out-of-scope
entry's byte-identical claim honest instead of quietly false; asserting bare equality would make
the fixture fail for the one reason it was added to catch.

**The existing test that encodes the defect.** `tests/unit/services/test_qc.py:468`,
`test_water_level_no_daily_rules_returns_empty_flags`, asserts `result[obs.id] == []` for a
single observation against a 10-minute rule. **The regime table settles what it becomes**: a
one-row group is **regime 3** — no cadence is inferable from a single observation at any lookback,
whatever cadence the rule set declares — so the required outcome is the **marked** zero-rule
outcome from T3, not a match. The
flags list may still be empty; what must change is that the group is now *reported* as having
resolved nothing, instead of being indistinguishable from a clean pass. **⚠️ "Repairing" this test
by asserting the bare empty list reinstates the defect under a green suite** — the assertion has
to be on the resolution, not on the flags. Rename it so the name states the new contract
(e.g. `test_a_single_observation_resolves_no_rules_and_is_reported_as_such`).

**Verification**: a daily series presented through the scheduled path's window selects the daily
rules and flags a value that violates them — the discriminating case, since the current code
marks it passed with zero rules. **🔴 That case must be a `range_check` or `gross_outlier`
violation, and the test must say in its name why**: those are the only daily rules a three-hour
window can make fire (§ What the repair actually restores). **A passing `range_check` test is not
evidence that `rate_of_change` and `spike` were repaired, and the verification must not be read
that way.** So, paired with it: **a test for the insufficient-context behaviour** — a daily series
for which the daily `rate_of_change` and `spike` rules ARE selected, asserting that they produce
no flag because the checked window supplies no `prev`/`nxt`, **and that the group is nonetheless
reported as resolved by T3's telemetry.** That test pins the disclosed shortfall so it cannot drift
back into an unqualified coverage claim, and it fails the day someone widens the checked window
without saying so.


- **The fixture's consecutive daily values must BREACH the thresholds, and it must use the
  daily `water_level` rules.** ⚠️ *Corrected 2026-09-22 after independent review — an earlier
  wording said "the daily `spike` `max_delta`" as though every daily spike rule had one.*
  Measured in the deployed set: daily **`water_level`** spike declares `max_delta = 5.0`
  (`config.toml:327`), but daily **`discharge`** spike declares `tolerance = 0.5`
  (`config.toml:299`), which `_apply_spike` implements as a **separate relative branch**
  (`services/qc.py:161-165` is the `max_delta` branch; the `tolerance` branch is a different
  comparison). ⇒ A fixture told to breach "the daily spike `max_delta`" **cannot be built for
  discharge at all**. Build it on daily `water_level`: adjacent values differing by more than
  that rule's `rate_of_change` `max_rate`, and a middle value deviating from both neighbours by
  more than `max_delta = 5.0`. Take the values from the deployed rule set, not a hand-built one.
- **Assert the positive control in the same test file: the SAME rule DOES flag when run over a
  group that contains the neighbours.** Hand `check` the whole daily series (so `prev`/`nxt` exist
  at `services/qc.py:267-268`) with the same resolution, and assert the `rate_of_change` and
  `spike` flags appear. Without this half, "no flag" is unattributed.
- The pair then reads as one contract: *these rules are selected, they are capable of firing on
  these values, and they do not fire only because the checked window holds one row.* Widen the
  checked window and the negative half goes red — which is exactly the alarm the shortfall needs. A 10-minute series continues to select the 600 s rules, asserted
against the same fixture so the fix cannot silently broaden selection. **Regime 3** (a group with
one row, and separately a group with none in the whole lookback) and **regime 2** (a 1500 s median
against `config.toml`) each produce T3's marked zero-rule outcome and **not** a nearest-cadence
selection — the regime table above, asserted, not assumed. **Plus the two guard cases named in
§ What the re-expressed guard does**: a one-row station *and* a one-row JJAS season build without
raising, and the 30-minute series at `tests/unit/scripts/test_dhm_precip_mask.py:176-183` still
raises. **Plus the byte-identical-output test** over a pinned hourly series (§ The backstop the
out-of-scope entry assumed) — the mask's `frozenset[MaskKey]` and accounting rows unchanged across
the T2 edit **except for the single named delta, the one-row station's `range_check` key**.
**Plus the bounds — and 🔴 NOT against a fake store alone.** An earlier draft asserted only that
a fake store "recorded the `limit` it was given". **That assertion is vacuous against the exact
implementation the bound exists to forbid**: a real store that accepts `limit`, ignores it, fetches
the whole 30 days and slices in Python passes it unchanged. The bound is a property of the SQL, so
it is asserted on the SQL:

- **Production-store assertion (required).** Compile the statement
  `PgObservationStore`'s new bounded method emits (SQLAlchemy `str(stmt.compile(...))` or an
  `after_cursor_execute` capture) and assert it carries **`LIMIT`** and
  **`ORDER BY … timestamp DESC`**. A client-side slice emits neither and fails here.
- **Database check (required).** Against a real Postgres — the existing testcontainers fixtures
  `db_engine` / `db_connection` (`tests/integration/conftest.py:16,47`), not a fake — seed a group
  with more than `N` rows spanning more than `L` and assert the method
  returns **exactly `N` rows**, that they are the **`N` most recent**, and that they come back in
  **chronological (ascending) order** despite the `DESC` selection. That last clause is the one a
  naive `ORDER BY DESC LIMIT N` gets wrong, and `_infer_time_step` takes the median of *signed*
  consecutive differences (`services/qc.py:43-47`), so a reversed list yields a negative median —
  a `timedelta` no rule declares. Reversal after the fetch is the implementation; the test is what
  makes it non-optional.
- **Call-count assertion (kept, now clearly insufficient on its own).** The inference fetch issues
  at most one query per `(station, parameter)` per run, asserted against a fake that counts calls.
  This bounds the *number* of fetches; the two assertions above bound each fetch's *cost*. Neither
  substitutes for the other.

#### Rollout gating and post-deploy monitoring


**Required before rollout — the gate:**

- **A paired old/new evaluation over identical Swiss inputs.** Take a fixed set of real stored
  observations (T1b's export, or a staging replay over a pinned time range covering every active
  adapter), run **both** the old and the new selection path over **the same rows**, and diff the
  result **per observation**: the **selected rules** (by the identity the resolution carries — see
  T3 § The resolution is ONE object; **not by `rule_id`**, which cannot tell a 600 s `range_check`
  from an 86400 s one), the resulting flags, and the aggregated status.
  **This runs before any image is deployed and writes no verdicts.**


  | Class of group (in the pre-state) | Expected diff | Failure |
  |---|---|---|
  | **Repairable** — resolves zero rules today, and the widened lookback yields ≥ 2 rows at a cadence the rule set declares (regime 1 after the fix) | **non-empty**, and the newly selected rules are **exactly** those the regime table predicts for that cadence | empty diff, or a selection other than the predicted one |
  | **Unresolved, regime 2** — ≥ 2 rows in the lookback, cadence declared nowhere | ⭐ **NOT empty — corrected 2026-09-20.** Selection and flags are unchanged (still zero rules), but the **aggregated status must change `QC_PASSED` → `QC_UNCHECKED`** (D5). The gate diffs status, so an empty diff here now means T2b did not run. | any rule selected (that is nearest-cadence matching, which T2 forbids) |
  | **Unresolved, regime 3** — fewer than 2 rows in the whole lookback | **empty** on flags; the group is marked zero-rule | any rule selected |
  | **Previously working** — resolves rules today (e.g. BAFU's 600 s groups) | **empty**, per observation — ⚠️ **unless the widened lookback legitimately infers a DIFFERENT cadence**, in which case the expectation is whatever the regime table predicts for the cadence inferred over the WIDENED lookback, stated per group in advance | a change not predicted by the regime table for the widened-lookback cadence |


- **🔴 The input set must contain at least one REPAIRABLE group, and the gate fails if it does
  not.** "The diff was empty everywhere" is the same observation as "nothing was repaired" and as
  "the new code never ran", and the Swiss fleet may legitimately contain no repairable group at
  all (§ Cross-plan's hypothesis is that BAFU's groups all resolve their 600 s rules today). So:
  - If T1b finds a repairable live group, the paired evaluation must include it.
  - **If it does not, the repairable case is constructed** — a seeded `(station, parameter)` group
    with a **daily** series, given (i) a **narrow checked-window row set** matching what the
    scheduled path would fetch, and (ii) a **separate, wider inference history** of ≥ 2 daily
    rows. The two must be supplied separately, because supplying one wide row set would silently
    test a widened *checked* window — the change § The inference lookback, bounded forbids — and
    would pass for the wrong reason.
  - The expected result for that group is stated in advance from the regime table. For a daily
    `discharge` or `water_level` group that is **all four** of its daily rules selected, of which
    `range_check` can fire, `gross_outlier` can fire **where a baseline exists** for the
    day-of-year, and `rate_of_change` and `spike` are selected and **inert**
    (§ What the repair actually restores). **A gate that expects every selected rule to produce a
    flag is wrong**, and the first person it fails will "fix" it by weakening it — which is why
    the expectation is written down before the run, not read off the result.
- **Report the diff, not just its size** — the affected groups, their class from the table above,
  the rules newly selected, and for each group whether the new selection is the one the regime
  table predicts.

**Operational monitoring after the deploy — no longer the gate, but still required:**

- Capture the per-verdict counts (`QC_PASSED` / `QC_SUSPECT` / `QC_FAILED`, and the count of
  `QC_PASSED`-with-empty-flags) over the last N scheduled runs before the deploy and the first N
  after it. A large divergence outside T1b's groups is a **signal to investigate**, not a
  pass/fail criterion — it can move for legitimate reasons and can stay still for illegitimate
  ones. Its job is to catch what the paired evaluation's input set did not cover.
- **Flow duration and query count, both captured pre and post — this part IS a hard gate.** Unlike
  the verdict counts, latency is a property of the system rather than of the water, so successive
  live periods are a fair comparison for it. The
  widened inference adds one bounded query per ingesting `(station, parameter)` to a sequential
  loop on a five-minute schedule, and verdict counts are blind to it: a run that gets the QC right
  and takes six minutes has failed. Record (a) the flow's wall-clock duration distribution over the
  N pre-deploy runs and the N post-deploy ones, and (b) **the QC path's `observations` *fetch*
  count per run** — the `SELECT`s `_run_qc_task` issues, namely the existing context-window
  `fetch_observations` (`flows/ingest_observations.py:311-316`) plus the new bounded inference
  fetch — **measured by instrumenting the store, not by totalling statements against the table.**
  **Fail the deploy if p95 duration rises above 150 s** — half the schedule interval, so a slow run
  cannot overlap its successor — **or if the QC-path fetch count exceeds `2 × |station_params|`**,
  which is the arithmetic bound of "one existing fetch plus one bounded inference fetch per
  ingesting group". A count above that means the cap is not where the plan says it is.

- N ≥ 12 (one hour of runs) covering at least one full poll cycle of every active adapter.

**Rollback.** ⚠️ **Code-only rollback holds ONLY until the first `qc_unchecked` row exists.**
*(Corrected 2026-09-22 after independent review — this paragraph still described the pre-T2b
world and contradicted T2b item 10.)* **Before** any `QC_UNCHECKED` is written
rollback is a redeploy of the previous image. **After** it, the previous image raises on the
value it does not know and cannot read those rows — which is precisely why T2b item 10 requires
a compatibility release to be deployed and verified FIRST. ⇒ **The compatibility release, not
the pre-272 image, is the rollback floor once the flag has been on.** Stored verdicts
written in between are **not** reverted: observations QC'd under the new matcher keep their flags,
which is correct (they record what was known at judgement time, per D3) but means the rollback is
asymmetric and the post-deploy counts above will show a mixed population across the boundary.
Record the deploy and rollback timestamps so that boundary is identifiable. **This asymmetry is
exactly why the gate is the pre-rollout paired evaluation and not the live counts**: a gate that
can only fire after rows are written cannot prevent the damage it detects, and rolling back the
image does not un-write them.

**Pre-change**: a RED test proving that today a daily series in a three-hour window selects **no**
rules and is reported `QC_PASSED` — failing on the flag/status outcome, not on a missing symbol.
**The red-first test MUST load the real deployed ruleset** — via
`sapphire_flow.config.qc_rules.load_qc_rules` against `config.toml`, or a fixture pinned to it —
**not a hand-built `QcRuleSet`.** Every existing test in that file hand-builds a ruleset at
`_STEP = timedelta(hours=1)`, which is `_infer_time_step`'s own fallback, so a hand-built ruleset
is guaranteed to match its own fixture and can go red and green for reasons unrelated to the
production failure. This repo's rule is that a red-first test must fail for the reason the bug
exists.

#### ⭐ Added 2026-09-20 — rollout controls the gate does not provide


**C5a — the gate has no operational dimension.** It diffs selected rules, flags and
aggregated status. A *correct* diff — repairable group, predicted rules selected, flags
correctly produced — is **precisely** the case that removes rows from
`operational_inputs`' `past_targets` and from the alert checker's `QC_PASSED` set
(§ What a changed verdict does downstream). The gate as written reports success.
**Required extension:** for every observation whose status changes away from
`QC_PASSED`, report the station, the parameter, and whether the loss opens a gap in that
station's resampled `past_targets` window under `validate_time_step_cadence`.

**C5b — the gate samples the wrong period.** The repairable population on Swiss is
**episodic**, appearing during degraded feed conditions. A pinned input set "covering
every active adapter" taken from a healthy period yields an empty diff everywhere —
which the gate's own text warns is indistinguishable from "nothing was repaired" and
"the new code never ran", forcing the constructed-case fallback. Constructing the case
proves the mechanism; it proves nothing about the fleet. **Required where one exists:** the
pinned range must deliberately include a **known degraded window** — a LINDAS outage/429 stretch
per Plan 175, or an ingest gap identified by Q2 below. ⚖️ **Where the retained history holds
none, T2's constructed repairable case governs and activation may proceed on it**, with the
limitation recorded — the alternative is a precondition nothing can satisfy.


**C4 — ship behind a `DeploymentConfig` flag defaulting `False`, and bump
`qc_rule_version`.** The plan specifies image-revert as rollback. **This repo already has
the right pattern for exactly this shape of change:**
`config/deployment.py:146` `enable_skill_generations: bool = False` is a Plan 235
two-release flag whose own comment explains it exists so "a rollback during the rollout
window always lands on an image that already understands the rows on disk"
(`docs/standards/cicd.md`'s one-release rollback rule). Adopt it:

1. The `QC_UNCHECKED` write behind a flag, default `False`. ⛔ **The selection change is NOT
   behind it and the image does not deploy byte-identical in behaviour** — T2 deletes the
   `< 2 rows ⟹ 1 h` fallback and T6 rejects keeping the old path in `src`, so no flag can
   restore the old verdicts (§ T5 Verification).
2. Enable it in `config/overlays/mac-mini.toml`, which is a **host bind mount** into
   `prefect-worker`, `prefect-worker-ingest` and `api`
   (`docker-compose.macmini.yml:46, 52, 76`, `:ro`). **Disabling the flag stops
   `QC_UNCHECKED` writes; selection rollback requires the compatibility image.**
   ⚠️ Edit it **in place**: this is a single-file bind mount, and an editor that writes a
   new inode leaves the container reading the old content until it is recreated
   (the known `git pull` trap).
3. **Bump `qc_rule_version`** in `services/qc_datum.py:23-26`. Verified: it round-trips
   into `Observation` but **no production code branches on its value** — so the bump is
   free, and it converts D3's "record the deploy timestamp in a document" into a
   **queryable column**, which is what makes the limitation auditable and a future re-QC
   scopeable.

**C9 — canary by station, and tag the rollback anchor first.** The plan's gate is binary
and fleet-wide. `stations.network` exists and the QC loop already iterates per
`(station_id, parameter)`, so the flag can be scoped cheaply: enable on 2–3 BAFU stations,
watch one full forecast cycle (`0 */6 * * *`), then widen. Before the upgrade, tag the
rollback anchor — `docker tag sapphire-flow:${OLD} sapphire-flow:rollback-backup` — because
the mini has **no image registry** (`docs/standards/cicd.md:506`; images are local-only and
the Sunday prune protects only `^rollback($|-)` tags). Image revert on this host is a
procedure, not a button, and it needs both overlays plus the exported tokens.

**C4b — wire the zero-rule check into the watchdog.** `types/enums.py`'s
`PipelineCheckType` has no zero-rule member and the watchdog probes exactly three check
types (`ops/watchdog.py:142-178`). Without one more probe, T3's `WARNING` is pull-only via
`/api/v1/health/detail`, and D5's mitigation (a) — "the Plan 268 runbook reader must read
it" — reduces to a human remembering to look. One more probe URL makes it Slack.


### T2b — Record an unchecked outcome as `QC_UNCHECKED` (D5, D2)


**Outcome**: a `(station, parameter)` group that resolves zero rules stores `QC_UNCHECKED`, not
`QC_PASSED`; forecasts still run on those rows with `input_quality = DEGRADED` **and that
provenance actually reaches the forecast record**; alerting, skill, training, hindcast, baselines
and the published API exclude them; and a row can be **re-examined later**.

**In**:

1. **`types/enums.py::QcStatus`** — add `QC_UNCHECKED = "qc_unchecked"`.
2. **A migration widening the CHECK constraint** at `db/metadata.py:526-529`. ⭐ Precedent:
   `alembic/versions/0002_add_qc_missing_status.py`, which dropped the old constraint by **both**
   possible auto-generated names and recreated it widened.
3. 🔴 **The store filter cannot express the policy today — widen it.**
   `PgObservationStore.fetch_observations` takes `qc_status: QcStatus | None`
   (`store/observation_store.py:160-166`) — **one status, not a set** (verified). "Accept
   `QC_PASSED` **or** `QC_UNCHECKED`" is therefore inexpressible. **Decision: widen the parameter
   to accept a collection of statuses**, in `protocols/stores.py` (the Protocol),
   `store/observation_store.py` (the implementation) and **every fake in the test suite**. The
   alternative — drop the filter and re-filter in Python at four call sites — is rejected: it
   moves a correctness rule out of one place into four and loses the index.
4. **The write site** in `flows/ingest_observations.py`: zero resolved rules ⇒
   `QC_UNCHECKED`, empty `qc_flags`.
5. 🔴 **The ingest counters, which today would silently mis-bucket it.**
   `flows/ingest_observations.py:368-373` is `if PASSED … elif FAILED … else: suspect` (verified),
   so `QC_UNCHECKED` lands in `counts["suspect"]` and flows into `IngestResult.qc_suspect` and the
   `ingest.qc_complete` log. **That corrupts the exact signal D7's automatic abort reads** — the
   C3 spurious-flag hazard and the unchecked population must be distinguishable. Add an
   `unchecked` bucket and an `IngestResult` field.
6. 🔴 **The COMPLETE consumer filter policy — every accepting read and every excluding read.**
   ⭐ *Added 2026-09-20: item 6 owned the `DEGRADED` path but no item owned the filters
   themselves, so hindcast, baselines/onboarding and the forecast-lab export had a stated policy
   and no builder.* Touch each site named in D5's per-site table deliberately and **name every
   one in the implementation report**: the four accepting reads (two model-input, two
   freshness-only) and the nine excluding ones, plus the three the grep could not reach
   (`hindcast.py:201`, `api/routes/stations.py:698`, `component_derivation.py:36-37` — the last
   stays unchanged, preserving the calculated-station exclusion). ⛔ A blanket widening of the
   filter would put unchecked data into published skill scores and the partner snapshot.
7. 🔴 **The `DEGRADED` provenance path, end to end — an existing route does NOT suffice.**
   Observation status is dropped during dataframe conversion, and `OperationalInputMetadata`,
   `ModelRunContext` and `ReadyContext` carry no unchecked provenance. Quality is computed later
   in **both** `services/run_station_forecast.py` and `services/run_group_forecast.py`. All of
   these are in scope, and the legacy, per-track and group routes must each be covered.
   ⚠️ Distinguish the **model-input** reads from the two **freshness-only** reads
   (`operational_inputs.py:940`, `track_assembly.py:338`) — a staleness probe that sees an
   unchecked row should not by itself degrade a forecast built from checked data.
8. ⭐ **Re-examination — owner decision, 2026-09-20.** `QC_UNCHECKED` must **not** be terminal.
   D3 records that no re-QC path exists and `_run_qc_task` picks up only `RAW`
   (`flows/ingest_observations.py:320`), so without this a row stamped during a transient LINDAS
   outage would be excluded from alerting, skill, training and hindcast **forever**, even after
   the feed densified and the rules would resolve. **Widen the QC pick-up set to
   `{RAW, QC_UNCHECKED}`**, so a later run with a fuller window re-checks and upgrades it.
   🔑 **The checked window already bounds this — no attempt counter is needed or possible.**
   `_run_qc_task` fetches `[now − 2 h, now + 1 h]` (`flows/ingest_observations.py:295-296`,
   `context_window_hours: float = 2.0`, no deployment override), so a row leaves the pick-up
   window ~2 h after its own timestamp on the ordinary path. ⚠️ **NOT on the DHM river path**:
   `:297-309` widens `window_start` to `min(fetched_times) − context_window_hours`, so a
   catch-up run re-picks rows days old — the widening this plan documents at § Problem. **The
   bound is the FETCHED checked window, not a flat 2 h**, and on the one feed this plan is
   being landed for it is the wider one.
   earlier revision demanded a bounded attempt count; `observations` carries no attempt column
   (`db/metadata.py:509-545`), so it would have needed an unscoped migration — and the retry it
   feared cannot occur.* **That 2 h ceiling is also the honest bound on D2's "not terminal".** ⚠️ This does **not** reopen D3: no *historical* row is re-examined, because historical
   rows are `QC_PASSED`, not `QC_UNCHECKED`.
9. 🔴 **The published API surface the thirteen-site inventory missed.**
   `api/routes/stations.py:698` filters `obs.c.qc_status != "qc_failed"` — **exclusion polarity**,
   which is why a grep for `qc_status=QcStatus.QC_PASSED` could not find it (verified). Unchecked
   values would be returned as the observed truth series in the hindcast-vs-observed endpoint,
   unmarked. ⭐ **EXCLUDE them. Decided 2026-09-20** — the earlier "either exclude or render the status visibly" left a correctness rule as an implementer's choice, and the response does not carry the status anyway, so "render visibly" would have required a schema change nobody scoped. Exclusion matches every other publishing consumer under D5.
10. 🔴 **Rollback compatibility — the flag alone does not buy it.**
   `store/observation_store.py:318` executes `QcStatus(row["qc_status"])` (verified), which
   **raises on a value it does not know**, and ingest reads those rows without a status filter. So
   the moment one `qc_unchecked` row exists, the previous image cannot read it — violating
   `docs/standards/cicd.md:245`'s one-release rollback rule. **Ship a compatibility release
   first**: an image that *understands* `QC_UNCHECKED` but never writes it, deployed and verified,
   before the release that writes it. 🔑 **C4's flag gates the STATUS WRITE.** *(Restated
   2026-09-22: it previously read "gates the selection change; it must gate the status write as
   well". T6 explicitly rejects keeping the old path in `src`, so no flag can restore the old
   selection — see T5 Verification. The selection change ships unconditionally with T2.)*

11. **Two further sites that depend on the SET of `QcStatus` values, not on a filter.**
    `services/run_station_forecast.py:143-149` `worst_qc_status` enumerates all five members in a
    `priority` dict and falls back via `.get(f.status, 0)`, so a sixth member silently ranks with
    `RAW`/`MISSING` — unreachable today because T2b writes no flags, but it must be made explicit
    rather than left to that coincidence. `api/routes/api_stations.py:259` `_parse_enum` widens a
    **public query filter** as a side effect of the enum change; decide whether that is intended.
    ✅ Stated positively, because it is the question a reader will ask: `api/schemas.py:84,97` are
    `str`-typed, so **no API schema break** — the only `Literal` is the forecast-lab one at
    `api/forecast_lab_schemas.py:160`.

**Out**: no re-QC of rows already stored as `QC_PASSED` (D3). No new rule kinds. No change to
`_USABLE_STATUSES` in `services/component_derivation.py:36-37` — see the accepted exception below.

**⚠️ Accepted exception — calculated stations DO go dark, and "nothing goes dark" was too strong.**
`_USABLE_STATUSES` is applied *after* an unfiltered fetch, so it is not one of the thirteen sites.
A component whose rows become `QC_UNCHECKED` fails `derive_point`'s guard, which returns
`DerivedPoint(value=None, qc_status=MISSING)` (`services/component_derivation.py:115`, verified) —
so the **derived** station has no usable observations and goes dark one hop downstream. Widening
the forecast read cannot recover it. Today those rows are wrongly `QC_PASSED` and derivation
proceeds on unchecked data, which is the defect.
🔎 **Measured inert today**: no calculated station is configured in `config.toml`, and
`_derive_calculated` returns immediately when no formulas exist. **Accepted on that basis, and the
canary (C9) must re-check it against the deployment** — if any calculated station exists, its
dependent stations join the blast-radius assessment before the flag is enabled.

**Verification**:
- A zero-rule group stores `QC_UNCHECKED`; a group resolving rules stores an unchanged verdict —
  asserted on the stored row, not on a log line.
- 🔴 **A station whose every row is `QC_UNCHECKED` still produces a forecast, and that forecast
  reports `DEGRADED`** — asserted on the forecast record, for the legacy, track **and** group
  routes. This is the anti-dark-station test and it is the point of D5's consumer split.
- The same station contributes **nothing** to an alert evaluation, a skill computation, a training
  frame or the hindcast-vs-observed endpoint.
- A row stored `QC_UNCHECKED` is **re-examined and upgraded** once its window densifies, and a
  permanently-unresolvable group stops being retried once its rows leave the fetched checked
  window — on the DHM river path, the widened one (item 8).
- Ingest counters report `unchecked` separately from `suspect`.
- The **compatibility release** is proven: an image without the write path reads a database
  containing `qc_unchecked` rows without raising.
- ⚠️ **The partial index** `postgresql_where=qc_status == "qc_passed"` (`db/metadata.py:568`):
  confirm the planner still uses it on the reads that still demand `QC_PASSED`.

**Pre-change**: a RED test proving that today a zero-rule group stores `QC_PASSED` with empty
flags. ⛔ *Not "indistinguishable in the stored row" — that was T0's Pre-change, and by phase 2
T0's `-norules` sentinel has already made the two distinguishable. Assert on `qc_status`.*

### T3 — Make a zero-rule outcome observable

**Outcome**: a group for which no rule resolved is visible without reading the code. Lands in the
same change as T2 (D1's binding constraint).

#### 🔴 The flow cannot see a zero-rule group today — name the route first

`Stage1QualityChecker.check` returns `dict[ObservationId, list[QcFlag]]`
(`services/qc.py:226-233`). **"Rules ran and found nothing" and "no rule resolved" both come back
as an empty list**, and the inferred cadence never leaves the method — `_infer_time_step` and
`rules_for` are called inside the group loop at `services/qc.py:252-253` and their results are
discarded. So T3 **cannot be built from `flows/ingest_observations.py` and `types/enums.py`
alone**, as the first draft's *In* surface implied. Two routes exist:

- **(a) Re-derive grouping + `_infer_time_step` + `rules_for` inside the flow.** ❌ **Rejected.**
  This is precisely how `scripts/dhm_precip/qc_mask.py` acquired the mirror that T2 now has to
  delete (§ The second implementation). Building a second copy of the selection logic, in the same
  change that removes the first one, would be self-defeating.
- **(b) Compute the per-group resolution once, in `services/qc.py`, and let both the checker and
  the flow consume it.** ✅ **Chosen.** The checker already computes everything needed; it simply
  throws it away — and, critically, it computes it from the *wrong rows* once T2 splits the
  fetches. The next section is where that is settled, and it is the part round 6 rewrote.

#### 🔴 The resolution is ONE object, and `check` consumes it — this DOES change `check`'s signature


**So: one resolution, computed once from the inference lookback, consumed by BOTH the checking and
the reporting.** There is no design in which `check` re-derives it and the two stay in agreement,
because they are looking at different rows *by construction* — that is the entire point of the two
fetches.

**🔴 And the resolution must carry per-rule IDENTITY, not a cadence plus a boolean.** The earlier
shape — "the inferred `time_step` and whether any rule resolved" — cannot serve its second
consumer. T2's re-expressed `_raise_on_time_step_mismatch` must name **each** rule it rejects: the
existing guard builds `mismatched = [r for r in rule_set.rules if r.time_step != inferred]`
(`scripts/dhm_precip/qc_mask.py:155`) and puts the offending rules in the error message
(`:156-158`), and its whole reason for existing is that `rules_for` filters **per rule**, so a
mixed rule set can have some rules selected and others silently dropped (the docstring at
`:146-151` says exactly this).
"A cadence, and at least one rule resolved" is true of precisely that dangerous case. The shape has
to carry identity regardless of the first point above.

**Chosen shape.** A frozen value type — one entry per `(station_id, parameter)` — carrying:

- the **inferred cadence** as `timedelta | None`, where `None` is regime 3 ("no cadence is
  inferable"), so regime 3 is unrepresentable as a fabricated `timedelta` rather than merely
  discouraged; and
- the **selected rules**, as the actual `QcRuleParams` objects that `rules_for` returned — a
  `tuple[QcRuleParams, ...]`, empty for regimes 2 and 3.


**Therefore, binding:** the resolution carries the rule objects, and membership is decided by
object equality (`QcRuleParams` is a frozen dataclass with the default `eq=True`, so this is
well-defined; it is **not** hashable — its `thresholds` field is a `dict` — which is why the field
is a `tuple`, not a `frozenset`). **If an implementer substitutes some other identity key, it must
distinguish two rows of the same kind at different cadences**, and the guard test below must be
written against it.

**Required guard test (T2, in the same change): the same rule KIND at two cadences.** A rule set
carrying `range_check` at 1 h *and* `range_check` at 30 min, checked against an hourly series: the
1 h rule resolves, the 30-min rule does not, and `_raise_on_time_step_mismatch` **must raise naming
the 30-minute rule**. This is precisely the case `:185-216` does not cover, and it is what makes
the identity representation non-optional rather than a matter of taste. Keep `:185-216` as well —
it pins the cross-kind case.

**How `check` consumes it.** `check` takes the resolution as a parameter and uses it instead of
calling `_infer_time_step` / `rules_for` itself. A group present in `check`'s observations but absent from the resolution is a
**programming error and must raise**, not silently fall back to inferring — a fallback would
restore exactly the divergence this section exists to prevent.

**🔴 Which makes the resolution's KEY SET a contract: it is keyed on the groups being CHECKED, not
on the rows the inference fetch returned.** The raise above is correct, and it is a trap for the
flow unless this is stated. The mechanism, measured at `9dc07915`:

- The flow's groups come from `station_params` (`flows/ingest_observations.py:716-718`), derived
  from `raw_obs` — what ingested **this run**.
- The inference fetch is bounded by `L` (30 days). A DHM catch-up after an outage **longer than
  `L`** delivers rows whose timestamps are older than `now − L`, so the inference fetch returns
  **zero** rows for that group even though the group very much has observations to check. The
  checked-window fetch still sees them: `:297-309` widens the window to span `fetched_times`
  (§ What the widening does and does not do), which is exactly the DHM river `water_level` path.
- A constructor that emits an entry only for groups the inference fetch returned rows for would
  therefore omit that group, `check` would raise on it, and the raise is caught by the blanket
  `except Exception` at `flows/ingest_observations.py:759-767` — logged as `ingest.qc_failed`,
  appended to `errors`, and **the rows stay at `QcStatus.RAW` forever**, since nothing re-QCs
  them. A stuck-`RAW` row is worse than an unchecked `QC_PASSED` one, and it would arrive on the
  one feed this plan is being landed for.

**So, binding:** the flow builds **one resolution entry per `(station_id, parameter)` in
`station_params`**, and a group whose inference fetch yields **fewer than two rows — including
zero** — gets a **regime 3 entry** (cadence `None`, empty rule tuple), not a missing one. Regime 3
is a legitimate data condition with a defined outcome; a missing key is a caller bug. Keeping
those two distinguishable is the whole point of the raise, and collapsing them makes the raise
fire on ordinary operational data.

**Verification (T3):** a group with **zero** rows in the inference lookback but non-empty checked
observations produces the marked zero-rule outcome and **does not raise** — asserted directly,
because the failure is invisible in any test whose fixture happens to store its rows inside `L`.


#### 🔴 What happens to `check`'s OTHER two rule parameters — say it, or two implementers build two things

`check`'s current signature carries `rule_set: QcRuleSet` and
`skipped_rule_ids: frozenset[str] = frozenset()` (`services/qc.py:226-233`,
`protocols/stores.py:1029-1037`). The sections above add a third source of rule information and
say nothing about either of these. Both need an answer.

**`rule_set` — it goes.** If `rule_set` stays alongside the resolution, `check` has **two sources
of selected rules** and nothing makes them agree: a caller could pass rule set A and a resolution
built from rule set B, and the more defensive an implementer is, the more likely they are to "use
the rule set as well" and reintroduce the divergence the single resolution exists to remove.
The resolution already carries the selected `QcRuleParams` objects — everything `check` iterates
at `services/qc.py:255` (`for rule in rules:`) — so `rule_set` has no remaining consumer inside
`check`. **Drop it from
`check` and from the `QualityChecker` Protocol**; the rule set becomes an argument to the
*resolution constructor* (which is where `rules_for` is actually called), not to `check`. That
keeps exactly one place where a rule set turns into a selection. *(This is what makes the DHM
mask's two calls natural rather than awkward: `:203` and `:208` already pass different rule sets —
`pass_a_rules` and `pass_b_rules` — so each builds its own resolution from its own rule set and
its own observations, and `_empty_rule_set`'s empty selection is just a resolution with an empty
rule tuple.)*

**`skipped_rule_ids` — it stays, and it is applied AFTER resolution.** Measured at `9dc07915`:
`check` filters it inside the rule loop (`services/qc.py:256`, `if rule.rule_id in
skipped_rule_ids: continue`) — i.e. **after** `rules_for` has already selected the rule at `:253`.
That ordering is deliberate and must not change: it is what lets the datum path skip
`range_check` and `gross_outlier` on a `water_level` series with no datum
(`obs_skipped_rules`, `services/qc_datum.py:29-32`), which is **live today** — the flow passes it
at `flows/ingest_observations.py:339` and onboarding at `services/onboarding.py:801`.

**🔴 But that ordering creates a THIRD instance of selected-but-not-run, and this plan has not
named it.** A `water_level` group with no datum on a **daily** series resolves exactly the four
daily rules — `range_check`, `rate_of_change`, `spike`, `gross_outlier` — of which
`range_check` and `gross_outlier` are datum-skipped and `rate_of_change`/`spike` are inert for
want of neighbours (§ What the repair actually restores). **Every selected rule is then either
skipped or inert, and T3's telemetry reports the group as "resolved"** — a group under zero
effective QC, reported as checked. It is the same shape as the defect round 6 found in T3's
original design and the same shape as D6's, in a third place.

*Scope: disclose, do not fix here.* Stated because it must not be discovered after the merge as a
surprise, and because it bounds what "resolved" means in T3's telemetry: **resolved means rules
were selected, not that any rule ran.** Two consequences, one required and one recommended:

- **Required** — T4 records this alongside (e), the coverage the fix does not restore, as the third
  route to a selected-but-not-run rule, after regime-2/3 and the insufficient-context case.
  Whoever answers D6 inherits it. It costs a paragraph in a doc T4 already edits.
- **Recommended, not required** — T3's health-record `detail` names the **selected rules**
  (see **Recommended (not required): `detail` also names WHICH rules resolved, per group** below
  in this same section, which states the recommendation in full and is its single source),
  so "resolved" becomes checkable against "and which", rather than being taken on trust. It is the
  cheapest way to make the distinction visible, but it widens T3's record shape, so it is not made
  a gate on this plan.

**🔴 This IS a signature change on `Stage1QualityChecker.check` and on the `QualityChecker`
Protocol. Stating it plainly is the point: the frozen-signature claim was not worth the defect.**
These are the sites that move — **all of them T0's, per its `In`**, which names the test files
explicitly:

- **The Protocol**: `protocols/stores.py:1029-1037` (`QualityChecker.check`). Untouched:
  `ForecastQualityChecker` at `:1041+`, a different protocol.
- **The `check(...)` invocations**: `flows/ingest_observations.py:334`, `services/onboarding.py:796`,
  and `scripts/dhm_precip/qc_mask.py:203` and `:208`.
- **The spec**: `docs/spec/types-and-protocols.md:697-714` (the `QualityChecker` Protocol block) —
  T4 owns it, and it is **already** one parameter behind the code (see T4's *In*).
- **Test call sites**: `tests/unit/services/test_qc.py` (the bulk — 30 checker constructions),
  `tests/unit/config/test_qc_rules.py:165`,
  `tests/unit/scripts/test_dhm_precip_ruleset.py:72,91,100,107`, and
  `tests/unit/scripts/test_dhm_precip_mask.py:233-235`. `tests/fakes/test_fakes.py:267` asserts
  `isinstance(Stage1QualityChecker(), QualityChecker)`; `QualityChecker` is `runtime_checkable`, so
  that assertion checks method *presence* only and survives unchanged — **which means a green run
  of it is not evidence that the Protocol and the implementation still agree.** Pyright is.

**What keeps the diff from exploding: a constructor that reproduces today's behaviour.** The three
callers that legitimately have no separate lookback — onboarding (`services/onboarding.py:796`,
whose window is already historical and wide, § Problem) and the two DHM-mask calls
(`scripts/dhm_precip/qc_mask.py:203`, `:208`, which hold the station's whole record in memory) —
build the resolution **from the same observations they pass to `check`**, via a classmethod or
module function on the resolution type. Their behaviour is then identical to today's except for the
regime 3 change the plan already books (§ What the re-expressed guard does, case (a)), and their
edit is mechanical. Only the ingest flow builds the resolution from a *different* (wider) fetch.
*(The two mask calls take **different** observation sets — `:203` gets the station's whole ordered
record, `:208` gets one JJAS season — so each builds its own resolution from its own argument.
That is what makes a one-row *season* regime 3 for pass B independently of pass A, which is case
(a)'s distinction.)*

**🔴 But the test call sites are NOT a one-line change, and calling them one would silently remove
coverage.** An earlier draft said "the sites in `tests/unit/services/test_qc.py` are a one-line
change each rather than a rewrite". Measured at `9dc07915`: **twelve** `check` calls in that file
pass a **single observation** — `:102`, `:117`, `:124`, `:134` (range checks), `:377`, `:385`,
`:396` (gross outlier), `:413`, `:430` (override merging), `:462`, `:493` (water level), `:531`
(`test_no_rules_for_parameter`) — and every one of them relies on `_infer_time_step`'s fabricated
1 h matching the file's `_STEP = timedelta(hours=1)` rule set (`:22`). Feed that same single
observation to the same-observations constructor and the group is **regime 3**: no rules resolve,
so

- **six go red** — `:124`, `:134`, `:385`, `:413`, `:430`, `:462`, each asserting
  `len(flags) == 1` against an empty list, which at least fails loudly;
- **five stay green while exercising nothing** — `:117`, `:377`, `:396`, `:493`, `:531`, each
  asserting an *empty* result, which an empty selection satisfies for the wrong reason. (`check`
  pre-populates `result` with `{obs.id: []}` for every observation before selecting anything,
  `services/qc.py:237` — so a zero-rule group returns an empty list, not a `KeyError`, and these
  five cannot fail loudly.) `test_no_rules_for_parameter` (`:531`) would pass because no cadence
  was inferable, not because the rule targets a different parameter; `test_missing_baseline_skips`
  (`:396`) would pass because `gross_outlier` never ran, not because the baseline was absent;
  `test_water_level_no_daily_rules_returns_empty_flags` (`:493`) would pass because no cadence was
  inferable, not because the hourly observation misses its rule's 600 s step — the one thing it is
  named for; `test_value_in_range_passes` (`:117`) and `TestGrossOutlier::test_normal_value_passes`
  (`:377`) would pass without a rule ever being reached. Each one stops testing the thing it is named for,
  under a green suite. **That is the same class of defect as the one this plan exists to remove**,
  arriving through the test migration;
- **one is mixed** — `:102` (`test_range_check_realistic`) is parametrised over four cases: the two
  `expected_pass=True` cases stay green vacuously, the two `expected_pass=False` cases go red.
- **`TestOverrideMerging` (`:401-435`) is the sharpest case**: threshold merging can only be
  observed through a rule that actually runs, so against an empty selection those two tests cannot
  test merging at all — they would have to be deleted or rewritten, and a "one-line migration"
  invites the first.


**Required instead.** For each of those twelve sites, the implementer must:

1. **Construct the resolution from sufficient inference history** — at minimum two observations at
   the intended cadence, which for this file's `_STEP` means two hourly rows. The fixture helper
   `_make_obs(value, hours)` (`:29`) already takes an hour offset, so supplying a neighbour is
   cheap; a shared helper that builds a resolution at a named cadence keeps the diff small without
   making it vacuous.
2. **Preserve the original behavioural assertion** — the same flag count, status and `rule_id`.
   Widening an assertion to accommodate the migration is not a migration.
3. **Assert the EXPECTED selection — and for a mismatch test the expected selection is EMPTY.**
   The test must pin *what the resolution selects*, so that a future change altering the selection
   fails loudly instead of passing quietly. For the ten sites whose rule is meant to apply, that
   means asserting the intended rule IS selected. For the **two mismatch tests** it means asserting
   the selection is **empty**:
   - `test_no_rules_for_parameter` (`:531`) — observation `discharge`, rule `temperature`;
   - `test_water_level_no_daily_rules_returns_empty_flags` (`:493`) — observation hourly, rule
     declaring 600 s.

   **Requiring "the intended rule is selected" at those two would destroy the contract each exists
   to state**: an empty selection *is* their expected outcome. What must not survive is an empty
   selection reached for the *unintended* reason — so both get a resolution built at their intended
   cadence under requirement 1, and the emptiness is then the parameter mismatch and the cadence
   mismatch respectively, **never regime 3**. This is the clause that stops the silently-green half
   above from recurring.

   **🔴 `:493` therefore yields TWO tests, not one, and dropping either loses a contract.**
   § The existing test that encodes the defect converts `:468`/`:493` into a **regime-3** test —
   one observation, renamed, asserting the marked zero-rule outcome — and requirement 4 counts it
   among the named regime-3 tests. That successor does **not** cover the cadence mismatch the
   original name states, because in regime 3 no cadence is inferred to mismatch. So the implementer
   writes both: the regime-3 successor (single observation), **and** a migrated cadence-mismatch
   test (two hourly rows against the 600 s rule) asserting an empty selection. Keeping the
   dedicated regime-3 test separate is what the plan already requires; this clause only states that
   separation does not excuse losing the mismatch coverage.
4. **Keep the single-observation behaviour as its own tests.** A one-row group is regime 3 and
   that is a *contract*, so it gets named tests asserting the marked zero-rule outcome —
   `tests/unit/services/test_qc.py:468`'s renamed successor is one of them (§ The existing test
   that encodes the defect). It must not be reached by accident through twelve tests that meant to
   test something else. **This is additive to requirement 3, not an alternative to it**: the
   regime-3 successor stands *alongside* the migrated cadence-mismatch test, per requirement 3's
   `:493` note.

Note that supplying a neighbour is not always inert: the file's rules include `rate_of_change` and
`spike`, which fire on neighbour *differences*. Where a second row would change the expected flag
set, the second row's value is part of the fixture and must be chosen — another reason this is not
a one-line edit. The non-test call sites (onboarding and the two mask calls) **are** mechanical;
only the tests are not.

This resolution object is also exactly what T2's re-expressed `_raise_on_time_step_mismatch`
consumes — the guard asks "which rules would the production matcher select for this series, and was
a cadence inferable at all", which is the two fields above. That is why T2 and T3 are one landing,
not two (see the phase graph note below).


**The five *construction* sites, recorded by an earlier round and still the right set to sweep.**
Constructions: `flows/ingest_observations.py:333`,
`services/onboarding.py:773`, `scripts/dhm_precip/qc_mask.py:232`, `scripts/dhm_precip/qc_mask.py:338`,
`scripts/dhm_precip/build_dudh_koshi_handover.py:175`. The actual `check(...)` **invocations** —
the ones a return-type change breaks — are fewer and sit elsewhere:
`flows/ingest_observations.py:334`, `services/onboarding.py:796`, and
`scripts/dhm_precip/qc_mask.py:203` and `:208` inside `_station_mask` (reached from both script
constructions and from the handover build, which never calls `check` directly). Plus the test call
sites in `tests/unit/services/test_qc.py`, `tests/unit/config/test_qc_rules.py:165`,
`tests/unit/scripts/test_dhm_precip_ruleset.py` and `tests/unit/scripts/test_dhm_precip_mask.py:234-235`.
The `qc_checker.check(...)` calls in `services/forecast_combination.py:332`,
`services/run_station_forecast.py:549,557` and `services/run_group_forecast.py:285,293` are
**not** affected — those are `ForecastOutputQualityChecker`, a different class.

**🔒 Constraint this imposes on where the marking lives.**
`scripts/dhm_precip/build_dudh_koshi_handover.py:154` `_empty_rule_set()` builds a deliberately
empty rule set and passes it through `_station_mask` so that per-rule attribution can isolate one
rule at a time; **that path must keep returning empty flags without any zero-rule marking or
error.** It is a legitimate zero-rule call. This is safe only while T3's marking lives **in the
flow** rather than inside `check` itself — which is this plan's intent, but was not stated as a
constraint, and a later change that moves the marking down into `check` would break the Dudh Koshi
handover. **State it in the diff**: the checker reports resolution, the flow decides it is
noteworthy.

**🔴 The signature change sharpens this constraint rather than relaxing it.** With `check`
consuming a resolution, `_empty_rule_set()` produces a resolution whose selected-rule list is
**empty for every group**. `check` must treat that as an
ordinary zero-rule call and return empty flags, **not** raise and **not** mark.
*(Precisely: it is **not** the same shape as regime 3. `_empty_rule_set` is
`rule_subset(build_precipitation_qc_rule_set(params), frozenset())`
(`build_dudh_koshi_handover.py:154-155`), which returns a `QcRuleSet` with an **empty `rules`
tuple** (`qc_ruleset.py:115-118`) — so on a dense hourly station the cadence is inferred normally,
3600 s, and only the selection is empty. Regime 3 is cadence `None` **and** an empty selection.
An earlier draft said "the same shape"; harmless but imprecise, and the distinction is exactly
what lets `check` tell a legitimate empty-rule-set request from an uninferable series — both
return empty flags, but only one of them is ever worth marking.)* The only thing
`check` may raise on is a group in its observations that the resolution does not mention at all
(§ The resolution is ONE object), which is a caller bug, not a data condition. Assert the
`_empty_rule_set` path explicitly in T3's tests; it is the one caller for which "no rules
resolved" is the intended request rather than a defect.


**In**: ⛔ *The resolution value type, the `QualityChecker` Protocol and the non-flow `check(...)`
invocations are **T0's**, not T3's — T0's binding already forces them and claiming them twice put
the same edits in two phases.* T3 owns only what is new here:
`flows/ingest_observations.py` — a counter on `IngestResult` and a `PipelineHealthRecord`
at `WARNING` (`types/enums.py:187-190` — correct as originally cited; the enum has only
`OK`/`WARNING`/`CRITICAL`). Also `types/enums.py:193+` — **T3 needs a new `PipelineCheckType`
member**, which the first draft did not mention. **No migration is required**: `check_type` is
`sa.Text` in `alembic/versions/0001_v0_schema.py:751` and no later migration alters it, so a new
member is a code-only change. Do not plan one.

**Rate limit — required, not optional.** The ingest deployment runs `*/5 * * * *`
(`cli/register_deployments.py:44`). One `WARNING` record per affected group per run is a row every
five minutes, per group, indefinitely — `pipeline_health` would be flooded. **Follow the shape this
same flow already uses**: `flows/ingest_observations.py:238-256` writes **one aggregated record per
run**, `subject="ingest_observations"`, with per-station counts inside `detail`. T3 emits one
aggregated zero-rule record per run on the same pattern, naming the affected groups in `detail`,
rather than one record per group.

**Recommended (not required): `detail` also names WHICH rules resolved, per group.** The
resolution already carries the selected `QcRuleParams` objects (§ The resolution is ONE object), so
this costs one more field and no extra computation. It is worth taking because "the group
resolved" is a weaker statement than it sounds, in three separate ways this plan has now
booked — regime 2/3 resolving nothing, the insufficient-context rules that resolve and cannot fire
(D6), and the datum-skipped rules that resolve and are skipped (§ What happens to `check`'s OTHER
two rule parameters). With the rule identities in `detail`, "`rate_of_change` was among the rules
selected for this group" is checkable **from telemetry** rather than from a doc claiming it; the
Plan 268 runbook reader — who has to read this record anyway, per D5 — can then distinguish a
healthy group from a resolved-but-inert one without opening `config.toml`. Name the rules by the
identity the resolution carries, **not by `rule_id`**, which cannot tell a 600 s `range_check`
from an 86400 s one (§ The resolution is ONE object).

**Align with the existing precedent rather than re-deriving it.**
`services/forecast_combination.py:491-498` already implements exactly this check on the forecast
side — `if not qc_rules.rules_for(...)` → `log.warning("forecast_combination.no_qc_rules_for_step_not_persisted", …)`
→ skip — with a comment (`:480-490`) explaining the identical fail-open reasoning
(`worst_qc_status([])` returning `QC_PASSED` for an unchecked combination). Match its event-naming
and `detail` shape.

**🔓 What this does and does NOT close — say it in the merge, not after it.**


**What T2 + T2b + T3 now close.** An observation whose group resolved zero rules stores
`QC_UNCHECKED`, not `QC_PASSED`. The stored row carries the distinction, the nine
publishing/learning consumers exclude it, and forecasts that use it report `DEGRADED`. The
fail-open is **closed in the record**, not merely made visible.

**What they still do NOT close**, and it must be said plainly:
- `_aggregate_qc_status([])` (`flows/ingest_observations.py:145-150`) and `aggregate_qc_status([])`
  (`types/domain.py:104-109`) still return `QC_PASSED` for an empty flag list. T2b writes
  `QC_UNCHECKED` at the **zero-rule** branch; these helpers keep their behaviour for the ordinary
  "rules ran, nothing fired" case, which is correct. ⚠️ An implementer must not conflate the two —
  *no rules ran* and *rules ran and found nothing* are different facts, and that difference is the
  whole point of D5.
- **Rows already stored** keep their historical `QC_PASSED` (D3 — no backfill). Nothing in this
  plan distinguishes a pre-merge clean pass from a pre-merge unchecked one, and no published figure
  may treat pre-merge `QC_PASSED` as "rules ran".
- The condition persists: a cold group until its second row lands, and **a group whose cadence the
  rule set declares nowhere, indefinitely** (regime 2 — Plan 303's case). What changed is that it
  is now *recorded*, not that it cannot happen.

**🔓 And the second fail-open call site gets no marking either — stated, not overlooked.**
`services/onboarding.py:796` calls `Stage1QualityChecker.check` and `:812` feeds the result to
`aggregate_qc_status` (`types/domain.py:104-109`), the same empty-list ⇒ `QC_PASSED` rule, with no
zero-rule marking added by this plan. That is **deliberate and in line with T3's chosen shape**:
the marking lives in the *flow*, not in `check` (see the `_empty_rule_set` constraint above), so a
caller that is not the ingest flow builds its resolution from the observations it already holds and
gets no marking, and onboarding is not being wired to the telemetry here. It is also the lower-risk of the two sites — onboarding's window is
historical and wide, which is why the daily rules *do* resolve there (§ Problem). **Write this
sentence into the diff.** Left unsaid, the asymmetry reads as an oversight to the next reviewer,
who will either file it or fix it; T4 records it as the known limitation it is.


**Out**: **not** a raise — D2 (closed 2026-09-20) replaced Plan 264 T3's raise with T2b's
`QC_UNCHECKED` record, so neither this task nor 264 raises on a zero-rule group. Not the stored
status itself, which is **T2b's**; this task is the aggregated telemetry on top of it.
⭐ **And this task owns RECORDING the cross-plan debt, added 2026-09-20** — D2 requires 264 T3's
raise to be removed or rewritten before 264 is made READY, and until now that requirement lived
only in this plan's decision prose, owned by nobody. Write it into
`docs/plans/264-qc-rules-select-on-network.md` as part of this task, alongside the two stale
citations already booked against it. Not a
zero-rule marking on the onboarding path (`services/onboarding.py:796`) — see just above.
**Verification**: a run containing one zero-rule group reports it in the flow result and in a
health record, while a run where every group resolves rules reports neither. **Plus**: a run with
many zero-rule groups writes one record, not one per group.
**Pre-change**: a RED test proving that today such a run is indistinguishable from a clean one.

### T5 — The rollout controls (C4, C9, C4b, D7)


**Outcome**: the `QC_UNCHECKED` write can be enabled per station, reverted in seconds without a
rebuild, aborts itself if it removes too much data, and announces a zero-rule group to an
operator rather than waiting to be asked.

**In**:
- **A `DeploymentConfig` flag defaulting `False`, gating T2b's `QC_UNCHECKED` WRITE** (T2b item
  10, rollback). ⛔ *It does not gate the selection change — see T5 Verification.* Follow the
  Plan 235 precedent at `config/deployment.py:146`.
- **Per-station scoping** for the canary: `stations.network` exists and the QC loop already
  iterates per `(station_id, parameter)`.
- **The enable path**: `config/overlays/mac-mini.toml`, a host bind mount into `prefect-worker`,
  `prefect-worker-ingest` and `api` (`docker-compose.macmini.yml:46, 52, 76`, `:ro`).
  ⚠️ Edit it **in place** — a new inode leaves the container reading the old content.
- **D7's automatic abort**: D7 compares the per-station, per-cycle `QC_FAILED`/`QC_SUSPECT`
  fraction from the ingest counters against T1's proposed threshold; on breach it reverts the
  flag **and redeploys the compatibility image**, because the flag does not gate selection
  (§ D7). Count `unchecked` separately from `suspect`, or it measures the wrong thing.
- **Retained-row behaviour after the flag is disabled** — rows already written `QC_UNCHECKED` stay
  written; say so, and say what re-enables them (T2b item 8's re-examination).
- **`qc_rule_version`** bump — the VALUES at `services/qc_datum.py:18-19`
  (`DATUM_QC_RULE_VERSION`, `DATUM_SKIP_QC_RULE_VERSION`) and the `"1.0"` literal at `:25`, not
  the `obs_qc_rule_version` function that returns them. Makes the deploy boundary a queryable
  column rather than a timestamp in a document.
- **The watchdog probe** for the `PipelineCheckType` member **T3 adds** (`types/enums.py:193-213`
  — the same member; T5 does not add a second),
  `ops/watchdog.py:142-178`) so T3's `WARNING` reaches Slack rather than waiting for a human to
  query the health endpoint. *(This was described as a follow-on at C4b; it is in scope here.)*

**Also in T5's surface, because T6 builds the harness but does not decide when it matters:**
- **Running T6 is an activation precondition** — the flag is not enabled until that gate has
  passed, on a degraded pinned range where the retained history holds one and on T2's
  constructed repairable case where it does not (§ C5b).
- **Deleting T6 and its frozen snapshot when the rollout completes** is an activation-checklist
  item. ⛔ A frozen copy of a deleted code path that outlives its rollout becomes a second
  implementation nobody remembers is there — the exact thing T2 exists to remove.

**Out**: the harness itself (T6 builds it).

**Pre-change**: a RED test proving the flag actually gates the write — with it `False`, a
zero-rule group stores no `QC_UNCHECKED` row; with it `True`, the same group does.

**Verification**:
- 🔑 **With the flag `False`: no `QC_UNCHECKED` row is written.** ⛔ *NOT "an identical observation table" — that requirement was deleted 2026-09-22
  after independent review, because nothing in this plan can satisfy it.* T2 deletes the
  `< 2 rows ⟹ 1 h` fallback outright and T6 **explicitly rejects** "a flag keeping the old path
  in `src`" as the duplication T2 exists to remove. So the selection arithmetic ships
  UNCONDITIONALLY with T2.
  ⚖️ **OWNER — this narrows what the flag promises, and you should see that rather than find
  it.** The flag is a *status-write* control, not a *behaviour* rollback: it bounds the new
  `QC_UNCHECKED` population, not the changed selection. What covers the selection change is the
  compatibility release plus T6's paired harness, which is what they are for.
- Enabling for one station changes that station and no other.
- A simulated loss above the threshold aborts and reverts without human action.
- The rollback anchor is tagged before the upgrade (`docker tag … rollback-backup`) — the mini has
  **no image registry** (`docs/standards/cicd.md:506`) and the weekly prune protects only
  `^rollback($|-)` tags.

### T6 — The paired old/new evaluation harness


**Outcome**: a **read-only** report that, over a pinned set of real stored observations,
shows per observation exactly what the selection change does — partitioned by regime, and
including the operational consequence — while writing **nothing**.

#### How it holds both selection paths at once — the part that looked architectural and is not

The obvious objection is that the harness needs the *old* path alive at evaluation time,
which is a second implementation of precisely what T2 deletes. **Measured, the old path is
about fifteen lines**:

- `QcRuleSet.rules_for` (`types/domain.py:160-167`) is a six-line exact-equality filter.
- `_infer_time_step` (`services/qc.py:40-47`) is the median gap plus the `< 2 rows ⟹ 1 h`
  fallback that T2 deletes.

**So: the NEW path is imported from `src` (one source of truth), and the OLD path is a
FROZEN SNAPSHOT of those two functions inside the harness module**, each carrying the commit
SHA it was taken from.

⛔ **Rejected alternatives, recorded so they are not re-proposed**: a flag keeping the old
path in `src` (that *is* the duplication T2 removes, and it would outlive the rollout); two
deployed images (faithful but cannot diff per observation cheaply, and the mini has no
registry).

🔴 **The snapshot is VALIDATED, not trusted — this is the harness's own red-first test.**
The frozen pair must reproduce **the pre-change SELECTION** — inferred cadence and selected
rules — over a few pinned series. ⛔ *Not T2's byte-identical mask artefact: that is
`build_mask`'s output through the whole `scripts/dhm_precip/qc_mask.py` pipeline, while the
frozen pair is only `rules_for` and `_infer_time_step`, so reproducing it would need a mask seam
neither T2 nor T6 scopes.* If the snapshot has drifted, every diff the gate produces is
measuring the wrong baseline and the gate is worse than no gate. **Assert this before the
harness is used for anything.**

⏳ **It has an expiry.** The harness and its snapshot are deleted when the rollout completes;
T5's activation checklist carries the deletion.

#### In

- **`scripts/qc_selection_paired_eval.py`** — new, and **added to the Dockerfile's curated
  script list** (Plan 218 ships named scripts, not the directory). ⚠️ Invoked through the
  entrypoint — `docker compose exec -T <service> /entrypoint.sh python
  /app/scripts/qc_selection_paired_eval.py …` — because `DATABASE_URL` is assembled there
  and the process drops to `gosu app` there; `docker exec` bypasses both.
- Read-only use of the existing observation store. **No new store method**; if one is needed
  the harness is wrong.
- `Dockerfile` (the `COPY` list) and its tests.

#### What it reports, per observation

Selected rules **by the identity the resolution carries — not by `rule_id`**, which cannot
distinguish a 600 s `range_check` from an 86400 s one; the resulting flags; the aggregated
status; and the regime (1/2/3) under **each** path.

🔴 **Plus the operational delta, which the verdict diff alone does not capture.** For every
observation whose status changes away from `QC_PASSED`: the station, the parameter, and
**whether the loss opens a gap in that station's resampled `past_targets` window**. ⚠️
`validate_time_step_cadence` (`services/training_data.py:133`) **raises** rather than
returning — call it inside a `try/except` and report the exception as a **finding**, never
let it abort the run.

#### The pass criteria

**Use T2's paired-evaluation pass criteria** (§ T2 — the regime-partitioned table).

#### The input range — and why a healthy one is worthless

🔴 **Prefer a pinned range containing a known degraded window** — and where the retained
history holds none, T2's constructed repairable case governs, which is the only stated escape:
a LINDAS outage or 429 stretch
(Plan 175), or an ingest gap identified by T1's Q2 census. The repairable population on Swiss
is *episodic*. A range taken from a healthy period yields an empty diff everywhere, which is
indistinguishable from "nothing was repaired" and from "the new code never ran" — and then
forces the constructed-case fallback, which proves the mechanism and nothing about the fleet.

#### Out

Writes of any kind. The flag, the canary and the abort (T5's). Anything under `src/`.

#### Pre-change

N/A — this task adds `scripts/qc_selection_paired_eval.py`, changes no production behaviour, and
its own first verification item (the frozen snapshot reproducing the pre-change selection) is
what stands in for a red test.

#### Verification

- **The frozen snapshot reproduces the pre-change SELECTION over a few pinned series** — the
  inferred cadence and the selected rules, run first; nothing else in this task means anything
  until it passes. ⛔ *Not T2's byte-identical mask artefact: that is `build_mask`'s output run
  through the whole `scripts/dhm_precip/qc_mask.py` pipeline, while T6's snapshot is only
  `rules_for` and `_infer_time_step`. Reproducing it would need a mask seam neither T2 nor T6
  scopes, and T6's `In` lists no mask code.*
- A run against a database containing **zero** regime-2 groups **says so explicitly** rather
  than reporting success. *(The "0 findings" / "never ran" ambiguity is the failure mode this
  whole task exists to avoid; it must not reproduce it in its own output.)*
- **It writes nothing**: the `observations` table is byte-identical before and after, asserted
  on a checksum, not inspected by eye.

#### Sequencing

Lands in **phase 2** with T2/T2b/T3/T5 — it needs T2's new path to compare against. **Running
it is a T5 activation precondition**: the flag is not enabled until this gate has passed, on the
input § C5b prescribes.

### T4 — Documentation

**Outcome**: the selection contract is written down, including the failure mode that produced this
plan.
**In**: ⭐ **Added 2026-09-20 — the destinations D3 and D5 actually name, which no task owned:** **the Plan 268 operator runbook** (D3's limitation, D5's mitigation (a), the Plan 303 prerequisite, and 264's superseded raise) and **the published-figure sites** — at minimum the forecast-lab snapshot, whose schema asserts `qc_status: "qc_passed"` per point. ⛔ D3 closed on *"write the limitation into the runbook and beside published figures, not only into the plan"*; a documentation task whose *In* lists only specs cannot discharge that. Plus `docs/spec/types-and-protocols.md` — **both** the `rules_for` contract / cadence source
**and the `QualityChecker` Protocol block at `:697-714`, whose `check` signature T3 changes**
(a spec that still shows the old signature is a documented contract the code no longer honours).
**That block is ALREADY stale, independently of this plan** — measured at `9dc07915`, it shows
four parameters and omits `skipped_rule_ids: frozenset[str] = frozenset()`, which
`protocols/stores.py:1035` has carried for some time. T4 fixes that drift incidentally; say so in
the diff, so the correction is not read as introduced by T3;
**`docs/standards/wmo.md` — UNCONDITIONALLY, not "if"**; `docs/touchpoint-maps.md`.

**🔴 Why the conditional had to go.** An earlier draft wrote "`docs/standards/wmo.md` *if* it
states the QC selection contract", and an implementer reads a conditional *In* entry as "no".
Measured at `9dc07915`, the file needs editing on two independent grounds, so the condition is
already met and stating it as a condition only invites it to be skipped:

1. **Every line it cites moves.** `docs/standards/wmo.md:187` — the *Verified against the running
   system* row for **"Automated range + temporal-consistency checks" (WMO-168 Vol I)** — cites
   `services/qc.py:50`, `:71` and `:225`. All three are accurate today (`_apply_range_check` at
   `:50`, `_apply_rate_of_change` at `:71`, `class Stage1QualityChecker` at `:225`) and **all
   three move** under T2 + T3, which delete `_infer_time_step`'s fallback branch above them and
   change `check`'s signature. Its *Runnable* evidence, `uv run pytest
   tests/unit/services/test_qc.py`, is the file T2 rewrites.
2. **The claim behind the row is over-stated in the same way this plan exists to correct.** The
   row offers `_apply_rate_of_change` as the evidence for *temporal-consistency checks*. T2
   § What the repair actually restores measures that daily `rate_of_change` **never fires on a
   daily series through the scheduled path** — before this plan because it is never selected,
   after it because the checked window supplies no `prev`. The row's own scope note already
   limits it to "checker behaviour", which is what keeps it honest; T4 must **not** silently widen
   it, and must record that a `rate_of_change` rule can be configured, selected and still never
   evaluate. **This repo has already paid five months of false WMO compliance for exactly this
   class of stale-but-plausible evidence row**
   (`docs/plans/archive/023-degraded-forecast-input-quality.md`). Re-date the row
   and re-state its scope in the same edit — do not merely repoint the line numbers.
**Out**: no code change.
**Verification**: bounded inspection — the cadence source is named, the matching rule is stated,
the historical limitation from D3 is recorded, and **both fail-open call sites are covered**: the
flow's `_aggregate_qc_status` (`flows/ingest_observations.py:145-150`) and onboarding's
`aggregate_qc_status` (`types/domain.py:104-109`, called at `services/onboarding.py:812`). Record
that the forecast path selects on a declared step (`services/forecast_qc.py:240`) and the
observation path on an inferred one, and that per D1 this divergence is deliberate. **Also
record**: (a) the three-regime contract from T2 — **including that matching is relative to the
cadences declared by the rule set in use, not to 600 s and 86400 s**, that regime 2 (no declared
cadence matches) gets no rules rather than the nearest ones, and that the cadence is inferred from
a **wider, separately fetched lookback than the checked window**, with `L` and `N` named;
(b) that the marking lives in the flow, not in `Stage1QualityChecker.check`, and why
(the `_empty_rule_set` constraint in T3) — **and that `services/onboarding.py:796` therefore keeps
its unmarked fail-open, by decision**; (c) that a `QC_PASSED` verdict still does not by
itself mean rules ran **for rows stored before this plan** (D3 — no backfill) and for the onboarding path (D5's stated scope boundary); after this plan, a zero-rule group on the scheduled path stores `QC_UNCHECKED`. ⛔ *The superseded wording said "until Plan 264 T3 lands" — D2 removed that raise, so it documented a closure that would never arrive*; and (d) the D5 consequence, **stated on resolution
rather than on row count** — a group that resolves no rules is unchecked and silent, which happens
in **two** ways: regime 3 (fewer than two rows in the inference lookback) **until its second row
lands**, entered by every new group and by any station whose outage exceeded `L`; and regime 2 (a
well-determined cadence the rule set declares nowhere) **indefinitely**, which no number of rows
exits and which D4 is the fix for. **Record the bound, not "the switch-on window"**: a station
returning from an outage shorter than `L` keeps rows in the lookback and never enters regime 3 —
**and record that leaving regime 3 is not the same as resolving a rule**. ⛔ *Corrected
2026-09-22: this previously read "which is why 264 T3's rollout prerequisite is stated on T3's
telemetry rather than on a row count" — but D2 WITHDREW that telemetry gate (§ Cross-plan), so
T4 was instructing an operator doc to explain a prerequisite that no longer exists.* What
survives is an **ordering constraint, not a gate**: T2b's write site must land before, or with,
264 T3 (§ Ordering consequence).

**Also record (e) — the coverage the fix does NOT restore.** T2 makes the daily rules *selected*;
it does not make all of them *effective*. Daily `rate_of_change` and `spike` cannot fire on a
three-hour checked window because it supplies no neighbouring observation, so **8 of the 12 daily
rules are repaired and 4 are not** (T2 § What the repair actually restores). The doc must say this
where it states the selection contract, and must name whoever owns the follow-on — otherwise the
next reader takes "the daily rules are reachable again" at face value, which is how this defect
became invisible in the first place.

**🔴 And record, in the same place, that "resolved" means SELECTED — not run.** There are **three**
routes to a group under zero *effective* QC after this plan lands, and they are **not** equally
visible — which is exactly why the doc must state them together. T3's telemetry **marks the first
as zero-rule** and reports the other two as **resolved**:

1. **Regimes 2 and 3** — nothing is selected at all, so these are *not* resolved; the group is
   **marked zero-rule**, and this is the one route T3's telemetry makes visible (covered by (d)).
   Do not let the shared heading collapse it into routes 2 and 3: the three-regime contract exists
   to keep "no cadence matched" distinguishable from "rules matched and did nothing".
2. **Insufficient context** — daily `rate_of_change` and `spike` are selected and inert
   (this entry; D6 owns the follow-on).
3. **Datum-skipped rules** — `skipped_rule_ids` is applied **after** resolution
   (`services/qc.py:253` then `:256`), so a `water_level` group with no datum has `range_check`
   and `gross_outlier` removed post-selection (`obs_skipped_rules`, `services/qc_datum.py:29-32`,
   live at `flows/ingest_observations.py:339` and `services/onboarding.py:801`). On a **daily**
   `water_level` series that leaves only `rate_of_change` and `spike`, which route 2 makes inert:
   **every selected rule is then skipped or inert, and the group still reports as resolved.**
   (§ What happens to `check`'s OTHER two rule parameters.)

The doc must state all three, because a reader who knows only the first will read "resolved" as
"checked". Whoever answers D6 inherits routes 2 and 3 together.

**And (f) — that one resolution serves both checking and reporting, by construction.** The cadence
is resolved once, from the wider lookback, and passed into `check`; `check` does not re-infer. Say
why: a second inference over the narrower checked window would report rules that never ran
(T3 § The resolution is ONE object). This is the kind of invariant a later "simplification"
removes if nothing wrote down what it was protecting.
**Pre-change**: N/A — documentation.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T0"] },
    { "id": "phase-1b", "tasks": ["T1"], "depends_on": ["phase-1"] },
    { "id": "phase-2", "tasks": ["T2", "T2b", "T3", "T5", "T6"], "depends_on": ["phase-1b"] },
    { "id": "phase-3", "tasks": ["T4"], "depends_on": ["phase-2"] }
  ]
}
```

**T0 runs first** — the instrument must exist before T1 can measure anything (§ T1b cannot be
measured first). T1a needs neither the database nor T0 and may run alongside it; it is already
measured and recorded, so it is not work to schedule. **T2, T2b and T3 are one phase because
they are one landing.** ⭐ **T5 joins phase 2 as well, corrected 2026-09-20** — an earlier graph put it in a later phase, which would have made phase 2 **a complete, mergeable, UNFLAGGED behaviour change with no abort control and no rollback-compatible release.** T2b item 10 requires the flag to gate the **status write**, and D7's auto-abort reverts that same flag; both presuppose the flag exists *in the release that first writes `QC_UNCHECKED`*. A safety control that lands after the thing it controls is not a safety control. T2b joins them for the same reason: the selection repair (T2) and the
corrected stored status (T2b) are two halves of one behaviour change — shipping T2 without T2b
would leave zero-rule groups still writing `QC_PASSED`, which is the defect itself, and shipping
T2b without T2 would move groups to `QC_UNCHECKED` that T2 would have repaired into a real verdict. An earlier graph put T3 in its own phase depending on T2, which contradicted the
sentence beneath it and was wrong in both directions: D1's binding constraint is that a zero-rule
outcome stops reading as a clean pass, which T2 alone does not discharge — *and* T2's rewrite of
`_raise_on_time_step_mismatch` consumes the resolution object T3 defines, so T3 does not strictly
follow T2 either. There is no edge between them; there is one change. **Round 6 makes this
tighter still**: T2's second inference fetch and T3's resolution object are the *same* mechanism
seen from two ends — building one without the other is the defect-with-telemetry failure
(§ The resolution is ONE object). They cannot be sliced apart even in principle.


## Explicitly out of scope

- **Per-station threshold overrides** — Plan 269, which is paused pending this plan.
- **Re-QC over historical observations** (D3).
- **New rule kinds.**
- **New `precipitation`/`temperature` rule rows and their threshold values** — **out of scope, unconditionally. D4 closed 2026-09-20 to ⭐ Plan 303**; the former "contingent on D4" wording is withdrawn, as is the sentence that followed it
  ("if D4 selects (i) … the rows land as T2b"), which both contradicted the withdrawal and
  re-created the `T2b` identifier collision this plan removed elsewhere.
- **A declared-cadence field on `StationConfig`** — rejected by D1 (closed 2026-09-19).
- **`ForecastQcRuleSet.rules_for`** (`types/domain.py:260-267`) — unaffected; selects on a declared
  step (`services/forecast_qc.py:240`).
- **`ForecastOutputQualityChecker`** and its five call sites — a different class from
  `Stage1QualityChecker`; not touched.
- **The threshold *values* in `scripts/dhm_precip/qc_ruleset.py`** — T2 changes only how
  `_raise_on_time_step_mismatch` decides a rule is mismatched, never what the DHM precipitation
  mask rules declare or do. The mask's output for an hourly series must be byte-identical before
  and after — **asserted against the byte-identical-output test T2 creates for the purpose. No
  such artefact exists today** (verified at `9dc07915`); creating it is a T2 deliverable, not an
  assumption this entry may rest on. See T2 § The backstop the out-of-scope entry assumed.
  **One delta is allowed and is named there**: a station whose entire record is a single
  out-of-range row loses its `range_check` key, because one row is regime 3 after T2 (§ What the
  re-expressed guard does, case (a)). That is a consequence of the regime contract, not a change
  to what the mask rules declare, and it is the only difference the fixture may show.

**NOT out of scope, despite living under `scripts/`:** `scripts/dhm_precip/qc_mask.py`'s mirrored
cadence inference and its guard. It is a second implementation of the exact behaviour T2 changes,
and leaving it would break the Dudh Koshi handover mask under a green test suite — see T2
§ The second implementation.
