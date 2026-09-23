---
status: DRAFT
created: 2026-09-11
plan: 272
title: Configured QC rules are unreachable when the inferred cadence matches nothing
scope: Diagnose and fix the observation-QC rule-selection path so that a configured rule cannot be silently unreachable because the cadence inferred from a short observation window fails to match its declared time step. NOT new rule kinds, NOT threshold values, NOT per-station overrides (Plan 269), NOT the network dimension (Plan 264) — though this plan and 264 T3 must land in the right order, see § Cross-plan.
blocks: [264, 269]
related: [264, 268, 269, 301, 302, 303, 304]
open_decisions: []
closed_decisions: [D1, D2, D3, D4, D5, D6, D7]
source: 2026-09-11 — found by the round-6 independent reviews of Plan 269 (both gates, independently) and verified directly against the repository. Plan 268 knew the single-row mechanism locally; nobody owned the systemic consequence.
---

# Plan 272 — configured QC rules are unreachable when the inferred cadence matches nothing

## Status

**DRAFT.** **Eight review rounds have run** — six Claude passes (an independent review
and five gates) and **three Codex passes** — each against the *commit that
preceded it*, so a green round is evidence about that commit, never about this one. Every set of
findings is folded below. Round 8 was the final precision fold of the ordinary pair.

🔴 **NOT READY — and the reason is new.** The **owner-commissioned high-risk review**
required by `docs/workflow.md` ran on 2026-09-20 and returned **"safe to implement, NOT
safe to deploy as specified"**, with nine conditions. Eight rounds had assessed this plan
as a *specification*; the high-risk pass was the first to assess it as **a change to a
running system**, and it reached three things none of the earlier rounds did — see
§ The operational picture. Documentation conditions are folded. **Three conditions are
real work that must be scoped before READY:**

1. **C6 — the paired pre-rollout gate has no owner.** The harness that runs the old and
   new selection paths over identical rows has no file, no task and no entry in any *In*
   surface. The plan leans on this control harder than on any other.
2. **C5 — the gate needs an operational dimension and a degraded-window sample.**
3. **C7 — T1b must become a ≥ 24 h loop**, because the population it measures is
   episodic, not steady-state.

**✅ All owner decisions D1–D7 are now CLOSED** (D1 on 2026-09-19; D2–D7 on 2026-09-20).
Two of them changed the plan's shape rather than confirming it:

- **D5 — a zero-rule group is recorded `QC_UNCHECKED`, not `QC_PASSED`.** This is a new
  stored status, a widened database constraint and a deliberate per-consumer policy;
  it created **T2b**, which no existing task covered.
- **D2 — that record REPLACES Plan 264 T3's fail-closed raise**, which never halted
  anything and instead stranded rows at `RAW` forever. 🧾 Debt recorded against 264.

Deferred with numbers granted, not promises: **Plan 303** (rain/temperature checking
rules, 🔴 must land before the Nepal precipitation feed goes live — and now recorded in
Plan 301 itself, not only here) and **Plan 304** (daily jump/spike checks). **D7** sets a
rollout abort threshold whose *number* T1's census still owes.

**Round 9/10 review state.** The closures above were folded, then put through a fresh
independent pair on 2026-09-20 (round 10). **Both returned NEEDS CHANGES**, and the fold
was substantially wrong in ways worth recording:

- **T2b as first written could not deliver its own consumer policy.**
  `fetch_observations` takes **one** status, not a set, so "accept `QC_PASSED` or
  `QC_UNCHECKED`" was inexpressible. T2b now owns the store/Protocol signature.
- **"Nothing goes dark" was false**, and the thirteen-site inventory was **incomplete by
  method** — it was built from one syntactic form. Three more readers exist, one of which
  publishes unchecked data through the API, and calculated stations go dark via a path
  no filter can reach.
- **Rollback would have broken**: the previous image raises on a `qc_status` value it does
  not know, so a compatibility release must ship first.
- **`QC_UNCHECKED` was terminal** on a population that is largely *transient*. ⚖️ Owner,
  2026-09-20: a later run re-examines and upgrades it, with a bounded attempt count.
- **The rollout controls had no owner** — now **T5**.
- The decision-gating paragraph still told an implementer that D5 "changes no code", and
  `T2b` **collided with the identifier of a rejected option**. Both fixed.

✅ **C6's paired old/new evaluation harness is now scoped as T6** (2026-09-20, at the
owner's instruction) — the last standing unowned condition. The objection that it needed a
second live implementation of what T2 deletes turned out to be tractable: **the old path is
about fifteen lines**, so T6 imports the new path from `src` and carries a *frozen snapshot*
of the old one, validated against T2's byte-identical fixture before it is used for anything.

⭐ **NEW TASK T0 — the measurement instrument** (2026-09-20). T1's census turned out to be
unanswerable from the database: nothing records which rules were selected, and an empty
`qc_flags` is identical for "zero rules" and "rules ran cleanly". T0 writes a sentinel
`qc_rule_version` suffix at the zero-selection branch — chosen over two rejected designs
because it needs no status, migration, compatibility release, consumer edit or flag, and two
adversarial reviews failed to break its inertness. **Phase order is now T0 → T1 → the rest.**

🔴 **Still NOT READY.** T6's scope needs a review pass; **D7's "share per station per cycle" is
not yet computable** from anything the plan builds (no cycle key on the row, fleet-wide
counters); and the census must be partitioned to exclude the structurally zero-rule weather
population or its headline is meaningless.

- Rounds 1-3 closed the unspecified sparse-window regime, T3's unbuildable *In* surface, and the
  existence of a mirrored implementation in `scripts/dhm_precip/qc_mask.py`.
- **Round 4** verified the core diagnosis, both corrected numbers and all eight
  recorded divergences, upheld all three design decisions, and raised four blockers — all on the
  mirror's re-expression and the newly in-scope inference widening: the matcher's contract was
  written against the *deployed* config's two cadences while T2 also governs a rule set declaring
  3600 s (B1 → the regime table in T2); the re-expressed guard silently gained one raise and lost
  another (B2 → § What the re-expressed guard does on each named case); the only stated protection
  for the DHM mask did not exist (B3 → T2's byte-identical-output deliverable); and the widened
  inference was asserted but never bounded or costed (B4 → § The inference lookback, bounded).
  Round 4 also required that the switch-on consequence be stated at decision level (→ **D5**).
- **Round 5 — a readiness gate that returned READY, plus four precision items, none a
  blocker.** The gate verified the load-bearing claim, the guard netting, the absent golden
  fixture, the bounds, D5's framing, internal coherence, the phase graph and every exit criterion.
  Folded: T2's post-deploy query bound was scoped to *all* statements against `observations` and so
  failed at baseline (→ the post-deploy checks); the guard-netting summary over-claimed a lost
  raise against the deployed DHM rule set (→ § What the re-expressed guard does); "contributes no
  mask keys, exactly as today" holds for a one-row *season* but not a one-row *station*
  (→ case (a) and the fixture's required contents); and D5 overstated its own exposure
  (→ **D5**, bounded at "until two rows exist for the group" — **itself corrected by round 6
  below**, because two rows buy inferability, not resolution). All four are recorded in
  § Review divergences.
- **🔴 Round 6 (this fold) — the first CODEX pass, and it found four gaps that five Claude passes
  did not, one of which would have shipped the defect with reassuring telemetry.** The five Claude
  rounds had converged; a sixth Claude round would have converged again. This is the multi-model
  rule earning its cost, and it is recorded that way rather than as one more round.
  1. **[HIGH] No route carried the widened inference into actual *checking*.** T3 froze
     `check`'s signature and resolved the cadence *alongside* it, but `check` infers from **its
     own** observations — the three-hour window. The telemetry would have resolved the daily rules
     from the 30-day lookback while `check` saw one row and ran nothing: **the original defect,
     now reporting success.** Folded as a **single resolution carrying per-rule identity**,
     consumed by both checking and reporting — which **does** force a `check` signature change
     (→ T3 § The resolution is one object, and `check` consumes it).
  2. **[HIGH] "The daily rules become reachable" overstated the repair.** Selection is necessary
     but not sufficient: daily `rate_of_change` and `spike` need a previous / a previous-and-next
     observation **inside the checked window**, which a three-hour window on a daily series never
     supplies. **4 of the 12 daily rules stay ineffective after the fix** (→ T2 § What the repair
     actually restores, and what it does not; the follow-on now has an owner decision, **D6**).
  3. **[MEDIUM] The SQL-bound test was vacuous against the dangerous implementation.** Asserting a
     *fake* store received `limit=50` passes unchanged for a real store that ignores the argument,
     fetches everything and slices in Python — the exact implementation the bound exists to forbid
     (→ T2's verification, now a production-store SQL assertion plus a DB check). Two claims
     corrected with it: `L`/`N` are **starting bounds, not measured fleet costs**, and the heap
     walk runs the full `L` for **any** group with fewer than `N` matching rows, not only for a
     group with none.
  4. **[MEDIUM] The Swiss regression gate compared different observations.** Requiring verdict
     counts from *successive live periods* to match is not a discriminating test: real
     measurements change, and a wrong classification can preserve a total (→ the post-deploy
     checks, now gated on a **paired old/new evaluation over identical inputs before rollout**).
  Plus a correction to the 264 sequencing, swept across D5, § Ordering consequence and T4:
  **two rows establish *inferability*, not *rule resolution*.** All five are recorded in
  § Review divergences.

- **🔴 Round 7 (this fold) — BOTH halves of a two-model gate, folded as one change. The Claude half
  returned READY; the Codex half returned NOT READY with three gaps.** Eight items in total, none
  of which reopens a design question — every one is a precision failure in *how* an accepted design
  was written down, which is why they fold rather than re-plan.
  1. **[Codex, HIGH] `rule_id` identifies a KIND, not a configured rule.** Round 6's new resolution
     object allowed the selected rules to be carried "*or their `rule_id`s*", and that parenthetical
     **reinstates the mixed-set defect the object exists to close**: `config.toml` carries four
     `range_check` rows and gives every one of its 26 rows `rule_version = "1.0.0"`, so id
     membership marks a same-kind sibling at a different cadence as selected. The existing mixed-set
     test cannot catch it — it mixes two different *kinds*. Folded as "the actual `QcRuleParams`
     objects", plus a required **same-kind-at-two-cadences** guard test
     (→ T3 § The resolution is ONE object).
  2. **[Codex, MEDIUM] The prescribed test migration silently removed coverage.** "A one-line change
     each" is false for the **twelve** single-observation `check` calls in
     `tests/unit/services/test_qc.py`, which rely on the fabricated 1 h. After migration the
     flag-asserting ones go red and the empty-asserting ones **pass without exercising their
     rules** (measured: six red, five green, one parametrised site split) — the
     override tests cannot test threshold merging against an empty selection at all. Folded as four
     binding requirements per site, with regime 3 kept as its own named tests
     (→ T3 § What keeps the diff from exploding).
  3. **[Codex, MEDIUM] The rollout gate contradicted the regimes the plan retains.** "Non-empty on
     T1b's zero-rule groups" fails a correct change on a group that legitimately stays unresolved
     under regimes 2 and 3, and passes a Swiss sample containing nothing repairable. Folded as an
     expected-outcome **partition by regime** plus a required, constructed repairable case
     (→ the rollout gate).
  4. **[Claude] The insufficient-context test could pass for the wrong reason.** A fixture whose
     daily value simply does not breach the threshold satisfies "produces no flag" identically.
     Folded as breaching values plus a positive control (→ T2's verification).
  5. **[Claude] The 1 h fabrication is killed at source**: `_infer_time_step` returns
     `timedelta | None`. Leaving the guard to the constructor lets `cadence = _infer_time_step(obs)`
     silently restore regime 3 as a fabricated hour (→ T2 § Regime 3 is the one that changes).
  6. **[Claude] The resolution is keyed on the group being CHECKED, not on the rows fetched.** A
     DHM catch-up older than `L` fetches zero inference rows, so a fetch-keyed constructor turns an
     ordinary catch-up into a raise, caught at `flows/ingest_observations.py:759-767`, leaving the
     rows stuck at `RAW` (→ T3 § The resolution is ONE object).
  7. **[Claude] `rule_set` and `skipped_rule_ids` in the new `check` had no stated fate.** `rule_set`
     is dropped (two sources of selected rules otherwise); `skipped_rule_ids` stays and is applied
     **after** resolution — which surfaces a **third** instance of selected-but-not-run, unstated
     until now (→ T3 § What happens to `check`'s OTHER two rule parameters; T4 (e)).
  8. **[Claude] `docs/standards/wmo.md` is UNCONDITIONAL in T4's *In*.** It cites three
     `services/qc.py` lines that all move and offers `_apply_rate_of_change` as evidence for a check
     T2's own analysis shows never fires on a daily series. A conditional *In* entry reads as "no"
     (→ T4's *In*).
  Plus five free wins and two cosmetics, all recorded in § Review divergences.

- **🔴 Round 8 (this fold) — the final gate. Claude READY; Codex NOT READY on one
  test-contract blocker, with the Protocol change explicitly cleared.** Codex cleared the
  tuple/object-equality identity, the `rule_set` removal (all four production invocations and the
  sole implementation), skips-after-resolution, the `_infer_time_step` caller sweep, the four-class
  rollout gate and the datum case. Four items folded, no design question reopened:
  1. **[Codex, BLOCKER] The four binding migration requirements contradicted the negative-selection
     tests.** Requirement 3 demanded "the intended rule IS selected" at all twelve sites, but
     `test_no_rules_for_parameter` (`:531`) must select **nothing** — its observation is
     `discharge` while its rule targets `temperature` — and
     `test_water_level_no_daily_rules_returns_empty_flags` (`:493`) is the same shape on cadence.
     Following the requirement literally destroys the contract each exists to state. Folded as the
     **expected** selection, *including an empty selection for a parameter or cadence mismatch*,
     with regime 3 kept as its own named tests (→ T3 § What keeps the diff from exploding,
     requirement 3). Folding it surfaced a consequence the finding did not name: because
     § The existing test that encodes the defect converts `:468`/`:493` into a **regime-3** test,
     which cannot cover a cadence mismatch, **`:493` must yield two tests** — the regime-3
     successor and a migrated mismatch test — or the file loses the contract its name states.
  2. **[Codex, measured count] The twelve-site split is 6 red / 5 green / 1 mixed, not 8/4.**
     Re-measured in this fold by running the file under a simulated regime 3; `:117` and `:377`
     assert an empty result and stay green, `:385` asserts a flag and goes red, and the
     parametrised `:102` splits two-and-two. The substance is unaffected — except that the
     silently-green half, which is the hazard, is **larger** than the earlier figure implied
     (→ T3 § What keeps the diff from exploding, *Method*).
  3. **[Codex, editorial] Selected-rule telemetry contradicted itself** — "required" in
     § What happens to `check`'s OTHER two rule parameters, "recommended, not required" at the
     rate-limit entry and in § Review divergences. Resolved as **recommended, not required**, with
     the rate-limit entry named as its single source.
  4. **[Codex, editorial] T4's introduction called regimes 2 and 3 "resolved".** They are not —
     that is precisely the distinction the three-regime contract exists to make, and an
     introduction that collapses it undoes the section beneath it. Corrected: T3's telemetry
     **marks** regimes 2 and 3 as zero-rule and reports only routes 2 and 3 as resolved.

This is a diagnosis plus a proposed fix to a live operational path. Round 8 is the last review
pass; the owner sets READY from this commit.

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
   *(An earlier review asserted the 86400 s outcome for the DHM feed specifically; that is not
   supported — see § Review divergences.)*

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

*An earlier revision of this section, written the same day, asserted that this plan must not
land before a new "Plan 305" carrying Nepali threshold values, on the grounds that the Swiss
`water_level` thresholds were "three to nine times too tight" and would mark every monsoon
rise suspect. **Both the prerequisite and its justification are withdrawn.** Three of its four
claims were wrong; the review pair found each one.*

**1. The arithmetic was inverted.** `_apply_rate_of_change` compares **consecutive readings**
(`services/qc.py:71-89`). The evidence quoted — largest one-day stage rises of 1.72–4.76 m —
is a **per-day** quantity. Spread over a day of ~10-minute readings that is **0.012–0.033 m
per reading**, against a `max_rate` of `0.5` — roughly an order of magnitude under it. The
Swiss value is **too LOOSE here, not too tight.** ⚠️ **The magnitude is NOT established**: that
is a *uniform-spread average* and a monsoon rise is not uniform. Tested on a real captured
station-day the largest consecutive-reading change was **0.066 m** and neither `rate_of_change`
nor `frozen_sensor` fired — but **the peak rate is unmeasured**, and that station-day comes
from a different basin than the six.

**2. The values are not unowned.** `docs/plans/268-dhm-barkhk-runoff-delivery.md` carries
`open_decisions: [D14]` — *"how the DHM daily QC thresholds are calibrated"* — with its own T7
waiting on it, and Plan 269's scope line defers to it explicitly. **Plan 305 forked a live
decision and is withdrawn** (owner, 2026-09-20).

**3. Plan 305 could not have been delivered anyway.** Per-station thresholds have no delivery
surface: `overrides=[]` is hard-coded at every caller. That surface is Plan 269 —
`status: BLOCKED`, `blocked_by: [272]`. So **272 → 305 → 269 → 272**.

**🔑 4. What the real exposure is, stated honestly.** The rate rule **never divides by elapsed
time**, so it is insensitive at a dense cadence and over-sensitive across a gap — which is
§ C3's hazard, already recorded here. A sharp rise observed either side of a feed gap can trip
it; an ordinary rise at full cadence will not. **How often is UNMEASURED** and needs sub-daily
data nobody has. ⭐ **That missing time normalisation is the defect worth naming an owner for
— not the threshold values.**

⇒ **This plan carries NO Plan 305 prerequisite.** What it does carry is the C3 hazard already
documented: the newly-reachable rules land on the sparse/jittered population, where gap-blind
thresholds are most likely to fire wrongly. `spike` (`max_delta = 1.0`) and `gross_outlier`
(`k_sigma = 5.0`) remain Swiss-calibrated and are part of 268 D14's answer, not this plan's.

## ⭐ The operational picture — added 2026-09-20 from the high-risk review

Eight review rounds assessed this plan as a *specification*. The owner-commissioned
high-risk review assessed it as **a change to a running system**, and reached three
things none of the earlier rounds did. All verified against `main` before folding.

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

**The plan never traced a changed verdict past the `observations` row.** Every rollout
control it specifies — the paired gate, verdict counts, the p95 latency gate, image
revert — operates on QC *outputs*. None operates on what consumes them.
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

**Consequence for the ordering prerequisite.** This plan's prerequisite — "T3's
zero-rule telemetry reports no affected group over a full poll cycle of every active
adapter" — is **a point-in-time check on an episodic condition**. The plan is right that
regime 2 is permanent and that a group entering it later is 264's intentional behaviour.
But on the live Swiss fleet a group enters regime 2 or 3 *transiently* whenever the feed
degrades — a lagging feed, a post-outage catch-up, a stretch of the known unhandled
LINDAS 429s (Plan 175) — and one clean poll cycle cannot see that. Landing 264 T3 on
this deployment would convert every LINDAS hiccup into a dark station.

**So 272-before-264 is correct and *insufficient*.** 264 T3 needs its own guard before
it is safe here: raise only after N consecutive zero-rule runs for the same group, or
write a distinguishable status rather than stranding rows at `RAW`, or exempt regime 3.
🧾 **That belongs in 264, not here** — added to the debt recorded above, which must be
swept before 264 T3 is made READY.

**The conclusion — 264 T3 must not land before this plan, or must land with it — stands. The
reason stated in the first draft does not.** That draft asserted a fleet-wide halt from "every
daily `(station, parameter)` group starts raising", while § What is NOT yet measured said the
blast radius was unmeasured. Both could not stand, and the mechanism was mis-attributed.

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

   *The superseded constraint, retained because its reasoning explains why the raise was wrong:*
   **264 T3 must not land while any live group is still resolving zero rules.** This holds
   *even after this plan lands*: T2's widened inference (see T2) can only resolve a cadence from
   history that exists, and a station with fewer than two stored rows has none. Landing 264 T3
   across a station's very first run would (before D2 removed its raise) have stranded that
   station's rows at `RAW` — the exact failure 264's own
   sequencing note exists to prevent, arriving by a different route.

   **🔴 The prerequisite is stated on resolution, not on row count.** An earlier draft wrote
   "after each new station has two rows in the inference lookback". That is the wrong test: two
   rows establish **inferability**, not **rule resolution**, and a group can leave regime 3
   straight into regime 2 and still resolve nothing (see the paragraph above). 264 T3's rollout
   prerequisite is therefore: **T3's zero-rule telemetry reports no affected group over a full
   poll cycle of every active adapter** — i.e. measure the thing 264 T3 raises on, not a proxy
   for it. That is checkable the day T3 ships, and it is strictly stronger than the row-count
   test, which it subsumes.

   **And it does not — cannot — cover every future group.** A station switched on *after* 264 T3
   lands re-enters regime 3 on its first run, and a parameter whose cadence the rule set does not
   declare enters regime 2 and stays there. Those groups hit 264 T3's raise. **That is 264 T3's
   intentional fail-closed behaviour, not a regression in this plan** *(⛔ superseded by D2:
   264 T3's raise is removed; such a group is now recorded `QC_UNCHECKED` by T2b)* — the point of the
   prerequisite is to avoid **darkening** the *existing* fleet on the day 264 T3 lands, not to
   promise that no group ever fails afterwards (264 T3 does not halt — § What 264 T3 actually
   does). 264's own runbook owns the switch-on procedure for a new
   station from then on.

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
condition an explicit, queryable state — ⛔ **NOT a terminal one: D5 requires it to be re-examined and upgraded under a bounded attempt count (T2b item 7), and a builder who reads "terminal" here will skip that** — keeps forecasts running on marked-degraded
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
   CAMELS-CH history was properly checked. The corrupted population is confined to rows
   ingested by the *scheduled* path since onboarding — which on Swiss are 10-minute rows
   that mostly resolved their 600 s rules anyway. **The contamination is real but
   bounded**, and its size is Q2-over-24 h (T1 § C7), not a doc claim.

**🔴 The rollback control is weaker than this repo's own standard.**
`docs/standards/cicd.md:202` requires a release to be rollback-safe for one version.
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


*(High-risk review, 2026-09-20: **D4 is Swiss-inert.** `adapters/hydro_scraper.py:124-131`
means no precipitation or temperature observation ever reaches this path on the live
fleet, so this is a **Plan 301 prerequisite**, not a live-deployment risk. That argues for
handling it in 301 rather than putting threshold values into a live QC change for no live
benefit — but it remains the owner's call, and it is the one open decision here that is
genuinely time-sensitive, because it must land before 301 ingests precipitation.)*
Verified at `9dc07915`: those two parameters have only 86400 s rules. None of the mechanisms D1
leaves open repairs that — widening the lookback on the feed that makes this live yields a **3600 s**
median, not a daily one; a tolerance match cannot reconcile a sub-daily observed cadence with
86400 s declared; and a declared cadence (now rejected by D1 anyway) would apply daily thresholds
to sub-daily rows. *(The cadence is **hourly**, not the "10-minute data" an earlier draft of this
decision named: `docs/plans/301-dhm-precipitation-adapter.md:37,43,102,125` establish the DHM
precipitation feed as an hourly stream. The conclusion is unchanged — 3600 s matches neither 600 s
nor 86400 s — but the premise was loose, and the corrected figure matters because it is the same
3600 s the DHM mask rule set declares.)* **The only fix is adding sub-daily
`precipitation` and `temperature` rule rows** — which § Explicitly out of scope currently forbids.
Two honest options:

- **(i) Bring the rule rows into this plan** as a scoped extra task *(⛔ REJECTED — and the identifier it once carried is deliberately removed: `T2b` now names the `QC_UNCHECKED` task, and a rejected option sharing that name sent one reviewer looking for two different tasks)*, and change the out-of-scope entry
  accordingly. Cheapest way to give the defect an owner; the cost is that this plan then touches
  threshold *values*, which it has so far deliberately avoided.
- **(ii) Defer the rule rows and record the constraint in Plan 301.** `docs/plans/301-dhm-precipitation-adapter.md`
  (DRAFT, `depends_on: [300]`) is the plan that makes the defect live. Deferring means adding a
  blocking prerequisite to 301: **sub-daily precipitation/temperature QC rules must exist before
  301 ingests precipitation**, or 301 ships a parameter that is structurally un-QC-able.

*Recommendation: (i).* It keeps the fix with the plan that found the defect, and (ii) leaves a
known defect owned only by a DRAFT plan's prerequisite list. Either way the answer must be written
down — this plan must not leave a documented defect with no owner. **If the owner grants a new plan
number instead, name it here; a vague promise is not a deferral.**

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
- ⚠️ `db/metadata.py:568` carries a **partial index** `postgresql_where=qc_status == "qc_passed"`.
  Rows moving out of `QC_PASSED` leave that index; confirm the planner still uses it for the
  thirteen filtered reads before and after (§ What a changed verdict does downstream).

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
| `api/routes/stations.py:698` | **exclusion polarity** — `qc_status != "qc_failed"` | **No**, or render visibly (T2b item 8) |
| `component_derivation.py:36-37` | `_USABLE_STATUSES`, applied **after** an unfiltered fetch | **No** — with a consequence, below |

⇒ **Nothing unchecked is published or learned from, and no directly-measured station goes dark.**

⚠️ **But calculated stations DO go dark, and the blanket claim "nothing goes dark" is WITHDRAWN.**
A component turning `QC_UNCHECKED` fails `derive_point`'s guard, which returns
`DerivedPoint(value=None, qc_status=MISSING)` (`services/component_derivation.py:115`, verified),
so the *derived* station has nothing usable and goes dark one hop downstream — where widening the
forecast read cannot recover it. Measured inert today (no calculated station is configured) and
accepted on that basis; C9's canary must re-check it against the live deployment. See T2b
§ Accepted exception.

**The two states this replaces** (kept because they explain *when* it fires):
*(This plan's D5. Not `scripts/dhm_precip/qc_mask.py`'s D5, which is the time-step guard.)*

This is currently derivable from T2 plus § Ordering consequence, and derivable is not the same as
accepted. Stated once, as one consequence, for the owner to accept or reject:

Two distinct states produce it, and an earlier draft of this decision named only the first:

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
- Nothing halts — and **nothing halts under 264 T3 either.** 264 T3's raise is caught per-group
  and strands the rows at `RAW`, which is a silently dark station, not a halt
  (§ What 264 T3 actually does). § Ordering consequence then *forbids* landing 264 T3 while a
  station is still cold — precisely because it would darken it.

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

So a newly switched-on station ingests unchecked observations — indistinguishable downstream from
checked ones — for the short window before its second row lands, **and a group whose cadence no
rule declares does so indefinitely**, and the mechanism that would make
that loud is the one we are deliberately holding back. That is coherent — guessing a cadence would
be worse (see T2's regime table) — but it is a decision, not a side effect, and the owner should
accept it at its **real** size rather than at the overstated one.

*Recommendation: accept, with the two mitigations that cost nothing.* (a) The T3 `WARNING` names
the affected groups, so **both** states are at least **auditable after the fact** — record in the
Plan 268 runbook that it must be read, since nothing reads it automatically. It is also the only
thing that distinguishes a transient regime 3 from a permanent regime 2: a group still named in
the `WARNING` after several runs is regime 2, and needs D4's answer, not patience. (b) Fix the
ordering by making **"T3's zero-rule telemetry names no group over a full poll cycle"** an
explicit, checkable exit criterion of the switch-on, rather than a condition someone has to
remember before making 264 T3 READY. *(An earlier draft made this "every new station has two rows
in the inference lookback" — the wrong quantity, per the paragraph above and § Ordering
consequence.)* **If the owner rejects, the alternative is to hold Plan 268's switch-on until 264 T3 can
land with it — which trades unchecked observations for no observations, and is a real option.**
Note what the corrected sizing does to that trade: it buys **one run's** worth of checking per
station, which makes holding the switch-on a considerably worse bargain than the original framing
implied.

**D6 — NEW (round 6). Who restores daily `rate_of_change` and `spike`, which selection alone
cannot reach?**
Measured at `9dc07915` (T2 § What the repair actually restores): T2 makes all twelve daily rules
*selected* for a daily series, but four of them — `rate_of_change` and `spike` on `discharge` and
`water_level` — **cannot fire**, because they read their neighbouring observations from the
**checked** group (`services/qc.py:267-268`) and a three-hour window on a daily series holds at
most one row. They run and return nothing, and T3's telemetry correctly reports the group as
*resolved*, so the shortfall is invisible from the outside.

This is D4's situation in a different place, and this plan's own standard applies: **a documented
defect must not be left with no owner.** Three options:

- **(i) A scoped extra task in this plan** *(⛔ REJECTED; identifier removed for the same reason as D4's option (i))*. Supply the missing context without widening what gets
  *flagged*: fetch the neighbouring rows for the checked window's observations and pass them to
  `check` as context, flagging only the window's own rows. Keeps the fix with the plan that found
  it; the cost is that this plan then touches `check`'s data flow a second time, on top of the
  signature change T3 already carries.
- **(ii) A named successor plan.** Cheap now, and honest **only if the number is granted here** —
  D4's entry already says a vague promise is not a deferral.
- **(iii) Accept permanently and document it as a known limit of sub-daily-window QC.** Defensible
  only if the owner judges daily `rate_of_change`/`spike` to be low-value checks. Note what it
  costs: `discharge` and `water_level` are the two parameters alerting actually depends on.

**⚖️ Owner decision, 2026-09-20: (ii) — a named successor, granted here as ⭐ PLAN 304.**
🔴 **As of 2026-09-22 no `docs/plans/304-*.md` exists and no other plan records 304 as a
prerequisite, so by this plan's OWN standard it is still a promise wearing a number.**
*(Independent review, 2026-09-22. An earlier line claimed granting the number was itself
sufficient — it is not, and 303 shows the difference: `docs/plans/301-...md:12` records it as a
gating prerequisite in the plan that is actually gated. 304 has no such receiving record.)*
⇒ **Either write 304, or have the gated plan record it.** Its
scope: supply `check` with the neighbouring observations a daily series needs, so daily
`rate_of_change` and `spike` can fire on `discharge` and `water_level` — the two parameters
alerting depends on — without widening what gets flagged. ⛔ Option (iii) was rejected: these are
not low-value checks.

*Original recommendation, retained for the reasoning: (ii), with the number granted in this fold.* (i) enlarges a change that round 6
has already grown once, and the four rules are inert **today** as well — this plan does not
regress them, it stops over-claiming about them. But it must not close as a dangling sentence.
**D6 was answered 2026-09-20: a named successor, ⭐ Plan 304, with the number granted in that
fold** — on the same terms as D4, which went to ⭐ Plan 303.

**Decision gating — ⭐ REWRITTEN 2026-09-20, because every clause of it was false after the
closures and it actively instructed an implementer to skip T2b.**

*The superseded text said implementation could proceed with D2–D6 open, that "D2 keeps the raise
in 264 T3", and that "**D5 changes no code here at all — it is an acceptance**". All three are
now wrong: D2 **removes** 264's raise, and D5 creates an enum member, a migration, a store
signature change and thirteen consumer decisions. An implementer following that paragraph would
have built T2 and T3 and shipped without T2b — the defect this plan exists to fix.*

**All seven decisions are CLOSED (D1 2026-09-19, D2–D7 2026-09-20). Nothing in this plan is
gated on an owner decision any more.** What gates implementation now is work, not answers:
**C5, C6 and C7** (§ The operational picture and § rollout controls) and the T2b scope named
below.
**D7 — NEW (2026-09-20, from the high-risk review). What rate of observations leaving
`QC_PASSED` is acceptable, and what happens to a station whose forecast is skipped
because they did?**

This is the decision the plan does not have, and the review ranks it as the one that
matters most for production. It is **not derivable from D2–D6**: those settle *which
rules run*, and this settles *what we accept when they do*. The fix's cost is that
correctly-flagged rows are **removed** from every consumer in § What a changed verdict
does downstream — including `operational_inputs`' `past_targets`, where enough dropped
rows empty a resampled bucket, `validate_time_step_cadence` raises, and **that station's
forecast is skipped for the cycle**. That is a silent per-station outage caused by a
correct QC verdict.

**⚖️ Owner decision, 2026-09-20: set the limit up front and abort the rollout if it is
exceeded.** Confirmed as recommended, and it is now a **rollout gate, not a monitoring
nicety**:

- The threshold is expressed as a **share of rows per station per cycle** that may leave
  `QC_PASSED` (to `QC_FAILED`, `QC_SUSPECT` **or** `QC_UNCHECKED` — all three are excluded
  by the alerting/scoring/training consumers under D5).
- Exceeding it **automatically aborts the canary** (C9) and reverts the flag (C4). It does
  not merely log.
- The owner set the policy; **the number itself must be proposed with T1's Q2 census in
  hand** — a limit chosen before we know the baseline is arbitrary. 🔴 T1 owes it.
- ⛔ *"Turn it on and watch"* was rejected for a measured reason: a skipped forecast raises
  no alarm today, so "watch" would mean a human remembering to look.

⭐ **The owner also flagged the deeper fix as the right eventual answer** (option 3 of the
question): a station whose forecast is skipped for missing input should say so. That is not
in this plan's scope and is **recorded as a follow-on for the watchdog**, alongside C4b's
zero-rule probe — the two are the same gap seen from two sides.

**D1–D7 are all CLOSED (D1 2026-09-19; D2–D7 2026-09-20).** ⛔ **Threshold rows are out of
scope UNCONDITIONALLY — nothing about D4 gates this plan.** The superseded text here quoted
T2's *Out* as "no threshold changes (unless D4 selects (i))" and called it "a gate on merging",
which implied threshold rows could still land here; D4 closed to Plan 303, T2's *Out* no longer
says that, and no part of this plan is gated on it.

## Tasks

### 🔴 T1b CANNOT BE MEASURED FIRST — the phase order is wrong (found 2026-09-20)

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

#### ⚖️ Owner decision, 2026-09-20: build the observability first, then measure

⛔ **The instrument is NOT `QC_UNCHECKED`, and T2b is NOT split. Both were proposed, both
were wrong, and the review pair killed them.** Recorded because the reasoning matters:

*The first proposal was to split T2b into a "stamp only" step — write `QC_UNCHECKED`, leave
every consumer accepting it — and a later consumer-policy step. It was claimed to be "near
inert". **It is the opposite.*** `fetch_observations` filters on **exact status equality**
(`store/observation_store.py:184`), so the instant a row carries the new status it
**disappears** from all thirteen `QC_PASSED` reads — alerting, skill, training, hindcast,
forecast inputs, the partner snapshot. That is the dark-station failure this plan exists to
prevent, shipped alone and ungated. And the only alternative reading — widen all thirteen
first — is not "the stamp only": it *is* T2b's item 3 and item 6, it would publish unchecked
rows under the forecast-lab schema's `qc_status: Literal["qc_passed"]` per-point assertion,
and **nine sites would be touched twice with opposite values**, which is more work than the
unsplit landing and a fresh chance to leave one site wrong each time.

**🔑 The instrument is `qc_rule_version` — a sentinel value at the zero-rule branch.**

Verified: `observations.qc_rule_version` is `sa.Text`, **nullable, with NO CHECK constraint**
(`db/metadata.py:535`), and **nothing in production branches on its value** — the only readers
are round-trip plumbing. So writing e.g. `"1.0-norules"` where zero rules resolve gives D7's
numerator, queryable, with:

- **no new `QcStatus` member** — so no migration, no widened CHECK constraint;
- **no compatibility release** — the previous image's `QcStatus(row["qc_status"])`
  (`store/observation_store.py:318`) never sees an unknown value;
- **no consumer edit, no filter change, no index change** — every read still filters on
  `qc_status`, which does not move;
- **no rollback exposure, and no flag needed**, which removes the gating problem entirely.

⭐ **It is genuinely inert, which the stamp only claimed to be** — and two independent
adversarial reviews failed to break it: 8 occurrences in `src/`, all round-trip plumbing; zero
in `api/`, `cli/`, `tools/`, `ops/`, `adapters/`; no comparison, parse, ordering, grouping,
index, constraint, natural key, upsert-comparison, archive, migration or fixture consumes it;
of 45 test occurrences only 6 assert a value and all 6 are rule-resolving scenarios.

🔴 **BUT THE SPELLING MUST BE A SUFFIX, NOT A WHOLE VALUE.** T5 bumps this same field as its
deploy-boundary marker, and `obs_qc_rule_version` already multiplexes datum provenance
(`"1.0"` / `"1.1-datum"` / `"1.1-datum-skip"`). A census written in phase 1b as whole-string
equality would **silently return zero** for every row written after T5 lands in phase 2.
⛔ *This is `feedback_bind_published_numbers_on_values` — a text guard silenced by a change
elsewhere.* **Define a stable `-norules` SUFFIX that survives the version bump, and write the
census against the suffix.** State also which population it marks: **zero rules SELECTED**, not
"zero rules ran" — the datum-skip case already has its own marker and must not double-encode.

⇒ **T2b stays whole and lands with T2, exactly as originally designed.** What moves into
phase 1 is only the sentinel write and the counter that reads it.

⚠️ **The numerator is a CUMULATIVE stamp-time count, not a status census.** T2b item 8
re-examines and upgrades rows, so `SELECT count(*) WHERE …` at read time systematically
undercounts — it sees only what was still unrepaired when you looked. That is C7's
snapshot-versus-loop error reappearing one layer down, inside the very instrument built to
avoid it. ⛔ **And the ingest counters must land with the sentinel** (T2b item 5): today
`flows/ingest_observations.py:368-373` buckets anything that is not passed-or-failed as
*suspect*, which would corrupt the signal the measurement itself reads.

🔴 **PARTITION THE CENSUS, or the number is meaningless.** `config.toml` declares
`precipitation` and `temperature` rules **only at 86400 s** (verified), so **sub-daily weather
ingest resolves zero rules BY CONSTRUCTION** — archived Plan 217 records exactly this:
*"weather observations … pass QC because no rule matches them"*. An unpartitioned headline
would be dominated by an **expected** class that T2 does not repair, and a D7 threshold derived
from it would mean nothing. **Partition by `station_kind` / `network` / `parameter`, and
exclude the structural weather population from the D7 baseline, naming it.**

⚠️ **Two erasure paths, so the count is a LOWER bound.** T2b item 8's re-examination overwrites
(already noted), **and `store_raw_observations` resets `qc_rule_version` to NULL on any
value-changing upsert** (`store/observation_store.py:104-108`, verified) — a restated value
wipes the sentinel.

⚠️ **And it is an UPPER bound on one component, not "D7's numerator".** D7 counts rows leaving
`QC_PASSED` to `QC_FAILED`, `QC_SUSPECT` **or** `QC_UNCHECKED`; the sentinel counts only
zero-selection rows. Defensible as a bound — ⛔ do not assert equality.

⚠️ **It is a PROSPECTIVE baseline and nothing more.** It measures the unfixed selection logic
going forward. It cannot recover D3's historical contamination, which stays permanently
ambiguous — **D7's threshold and D3's contamination figure are being asked of the same census
and only one of them can have it.**

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
- ⛔ **NO counter change. Dropped 2026-09-20, and the reason it was here was FALSE.**
  *An earlier revision pulled T2b item 5 forward, claiming "a zero-rule outcome is counted as
  suspect today". Measured: `_aggregate_qc_status([])` returns **`QC_PASSED`**
  (`flows/ingest_observations.py:145-150`), and T0 writes **no new status**, so a zero-rule
  outcome lands in `counts["passed"]` at `:369` — **not** in `suspect` at `:373`. That premise
  is true only for T2b, which introduces a status that falls through the `else`.*
  🔴 **And implementing it would have broken T0's own guarantee**: moving those rows out of the
  passed bucket changes `IngestResult.qc_passed` and the `ingest.qc_complete` emission — a
  behaviour change, in the one task whose entire purpose is to change nothing, **which T0's
  inertness test would not have caught** because that test is scoped to the observations table
  and the counters live outside it. The sentinel already sits on the row with `station_id`;
  no counter is needed. **Item 5 stays in T2b, where its premise is true.**

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

⚠️ **T0's surfacing shape is DELIBERATELY THROWAWAY.** T3 changes `check` again, in the
opposite direction, and declares its shape **binding**: the resolution computed *outside*
`check` and passed *in*, with `check` no longer calling `_infer_time_step`/`rules_for` at all.
T0 builds an out-flow that T3 deletes. The churn is unavoidable — computing this in the flow
instead is the route T3 rejects as self-defeating — **but say it, or two implementers build two
things and one of them defends the wrong one.**

**Out**: ⛔ **no `QcStatus` member, no migration, no CHECK-constraint change, no compatibility
release, no consumer or filter edit, no index change, no counter change, and no flag** — none is needed, which is
the entire point of choosing this carrier. `QC_UNCHECKED` and its consumer policy stay in T2b,
landing with T2.

**Why this carrier is safe, measured rather than assumed**: `observations.qc_rule_version` is
`sa.Text`, **nullable, with no CHECK constraint** (`db/metadata.py:535`), and **nothing in
production branches on its value** — the only readers are round-trip plumbing
(`types/observation.py:43`, `store/observation_store.py:50, 299, 320`,
`protocols/stores.py:118`).

**Verification**:
- A zero-rule group's rows carry the sentinel; a group that resolved rules carries the
  ordinary version. Asserted on the stored row.
- 🔴 **Inertness, asserted not claimed — as a DETERMINISTIC REPLAY, not a live before/after.**
  ⛔ *An earlier revision specified "a run over the current fleet before and after T0". That is
  invalidated by this plan's own argument three paragraphs up: the QC window is **run-dependent**
  and the zero-rule condition is transient, so two successive live runs are not comparable —
  new rows arrive, windows shift, `id` is a fresh `uuid4()` and `created_at` is `now()`.*
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
four at 1500 s and one at 2400 s — and therefore select zero rules and report `QC_PASSED`.

*Caveat, and it matters.* All five sit in the **first five** windows, where `now − 2 h` precedes
the start of the captured file, so the window sees only 3-5 rows. Restricting to the **126**
windows whose full three hours lie inside the captured span gives **0** zero-rule windows: with a
full-width window the 104 ten-minute gaps dominate the median and it resolves to 600 s.
*(126, not 132, as an earlier draft of this task said: 132 trims only the leading 12 grid points,
but the 6 trailing points also have `now + 1 h` running past the last captured row. Re-measured at
`9dc07915` — 144 grid points, 12 trimmed at the head, 6 at the tail. The result is 0 either way.)*
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

**T1b (live).** For each `(station, parameter)` with observations in the last 30 days, report the
median inter-row gap over a three-hour window, the cadence that would be inferred, whether any
configured rule matches it, and the count of observations marked `QC_PASSED` with an empty
`qc_flags`. **The headline number this plan needs is the proportion of QC-passed observations
that had zero rules run.** T1b also settles the § Cross-plan hypothesis: how many live groups are
genuinely daily, and whether BAFU's sub-daily parameters resolve their 600 s rules today.

🔴 **REWRITTEN SCOPE, 2026-09-20 — this task now CONSUMES T0's sentinel.** *An earlier
revision left T1 untouched while the preamble above disqualified its method, so the plan
instructed the implementer to set D7's threshold from the very census it had just ruled
unfaithful. Worse, the same edit **deleted** the guard that said so rather than reconciling it.*

- **Q2's sliding-window loop is the measurement contract for this task** — shape and scale of
  the zero-rule population, read-only, no code change. It is what T1 actually delivers.
- 🛑 **D7's threshold does NOT come from T0's sentinel.** *(Circular requirement removed
  2026-09-22 after independent review: the sentinel is a per-row `qc_rule_version` string that
  re-examination and the upsert NULL-reset overwrite, so counting sentinel rows IS a
  `SELECT count(*)` census — the very method the same bullet forbade — while `**In**` forbids
  the code change that was the only other route named. Three requirements, none of which could
  hold together.)* It comes from **Q2**, consistently with D7's own closure and T5.
- 🔑 **The distinction that makes Q2 sufficient and the sentinel insufficient:** Q2 RECOMPUTES
  the regime from observation timestamps over the real window, per `(station, parameter)`, every
  5 minutes for ≥ 24 h. It never reads back a stored QC verdict, so neither the NULL-reset nor
  re-examination touches it. **D7's denominator is therefore Q2's per-station, per-window
  share** — stated here because "per cycle" invited the stored-row reading that does not work.
- 🔴 **What genuinely is NOT recoverable, and why it is not needed:** a per-cycle share read
  back from STORED rows. `update_qc` writes no QC timestamp, `created_at` is insert time, so no
  row carries a cycle key; and the flow sums per-`(station, parameter)` counts into fleet totals
  (`:754-757`), emitting one `ingest.qc_complete`. ⇒ **Do not attempt to derive D7 from stored
  rows.** Q2's recomputation is the source.

**In**: a read-only query against the staging database (T1b) and a local analysis over the checked-in
fixture (T1a); both outputs recorded in this plan; no code change.
**Verification**: both measurements recorded here with their method and the SHA measured against.
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
timestamps carry stray seconds, `docs/plans/301-dhm-precipitation-adapter.md:43`), it must be small
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
  one, and all of a daily one inside `L`. 49 gaps is far more than a median needs. Also chosen from
  that arithmetic alone.
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

**The moment T2 relaxes exact equality, that docstring becomes false and the guard raises on rules
the new matcher would select.** Worse, it fails *quietly* in review: the mirror and its tests are
self-consistent, so `tests/unit/scripts/test_dhm_precip_mask.py` keeps passing while diverging from
production. `:176-183` `test_a_time_step_mismatch_raises_the_typed_error` builds a 30-minute series
against the 3600 s rule set and asserts a raise — under a tolerant matcher 1800 s would resolve to
the 3600 s rules and the raise would be wrong, yet the test would still pass because it exercises
the mirror. This feeds the Dudh Koshi handover mask, so a divergence here is a divergence in a
delivered artefact.

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

An earlier draft claimed the re-expression "preserves D5's intent verbatim". It preserves the
*wording*; without the inferability clause above it does not preserve the *effect*, and the round-4
gate is right that the difference is a build break in one direction and a silent hole in the other.
Both outcomes are therefore stated, not left to the implementer.

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

**🔴 That test only earns its keep if it can fail for the right reason — and the obvious way to
write it cannot.** A fixture whose single daily value simply does not breach `max_rate` or
`max_delta` satisfies "produces no flag" **identically**, and would stay green even if `prev` and
`nxt` *were* supplied. It would then be a passing test that proves nothing, and the day someone
widens the checked window it would keep passing — which is the one event it exists to catch. This
is `feedback_red_first_must_prove_the_fault`: a red-first (or regression-pinning) test must fail
for the reason the behaviour exists. Required, therefore:

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

T1b measures the *pre*-state. What gates the rollout, and what merely watches it afterwards, are
two different things, and an earlier draft conflated them.

**🔴 The rollout gate is a PAIRED evaluation on identical inputs — not a comparison of successive
live periods.** An earlier draft made the gate "per-verdict counts over the N runs after the deploy
must match the counts over the N runs before it, outside T1b's groups". **That is not a
discriminating test, and it must not be the gate**, for two reasons that point in opposite
directions:

- It compares **different observations**. The pre-window and the post-window are different river
  levels at different times. A rising stage, a storm, an instrument swap — any of these moves the
  `QC_SUSPECT`/`QC_FAILED` counts on a group T1b never named, with nothing wrong in the code, and
  the gate fails the deploy.
- It is **insensitive in the direction that matters**. Verdict *counts* are aggregates: the new
  matcher could classify a different set of observations and preserve the totals exactly — one
  spurious `QC_FAILED` where one real one disappeared nets to zero. The gate would pass a
  reclassification.

**And the cost of getting it wrong is not symmetric: reverting the image does not undo the
verdicts already written.** See § Rollback — observations QC'd under the new matcher keep their
flags, by D3. A gate that only fires *after* rows have been persisted is therefore the wrong
instrument regardless of how sensitive it is.

**Required before rollout — the gate:**

- **A paired old/new evaluation over identical Swiss inputs.** Take a fixed set of real stored
  observations (T1b's export, or a staging replay over a pinned time range covering every active
  adapter), run **both** the old and the new selection path over **the same rows**, and diff the
  result **per observation**: the **selected rules** (by the identity the resolution carries — see
  T3 § The resolution is ONE object; **not by `rule_id`**, which cannot tell a 600 s `range_check`
  from an 86400 s one), the resulting flags, and the aggregated status.
  **This runs before any image is deployed and writes no verdicts.**

  **🔴 The expected outcome is PARTITIONED BY REGIME — a blanket "non-empty on T1b's zero-rule
  groups" both over- and under-demands.** An earlier draft made the gate "the diff must be empty
  except on T1b's zero-rule groups, where it must be non-empty". That contradicts the regimes this
  plan spent three rounds retaining: **a group that resolves zero rules today because its cadence
  is one no rule declares (regime 2) or because it has fewer than two rows in the lookback
  (regime 3) still resolves zero rules after the fix** — correctly, by the regime table — so its
  diff is legitimately **empty** and the gate would fail a correct change. Conversely, a Swiss
  input set whose zero-rule groups are *all* regime 2 or 3 satisfies "non-empty somewhere" never,
  or satisfies a weakened version of the gate while demonstrating **no repair at all**. Classify
  every group in the input set and state the expected outcome per class:

  | Class of group (in the pre-state) | Expected diff | Failure |
  |---|---|---|
  | **Repairable** — resolves zero rules today, and the widened lookback yields ≥ 2 rows at a cadence the rule set declares (regime 1 after the fix) | **non-empty**, and the newly selected rules are **exactly** those the regime table predicts for that cadence | empty diff, or a selection other than the predicted one |
  | **Unresolved, regime 2** — ≥ 2 rows in the lookback, cadence declared nowhere | ⭐ **NOT empty — corrected 2026-09-20.** Selection and flags are unchanged (still zero rules), but the **aggregated status must change `QC_PASSED` → `QC_UNCHECKED`** (D5). The gate diffs status, so an empty diff here now means T2b did not run. | any rule selected (that is nearest-cadence matching, which T2 forbids) |
  | **Unresolved, regime 3** — fewer than 2 rows in the whole lookback | **empty** on flags; the group is marked zero-rule | any rule selected |
  | **Previously working** — resolves rules today (e.g. BAFU's 600 s groups) | **empty**, per observation — ⚠️ **unless the widened lookback legitimately infers a DIFFERENT cadence**, in which case the expectation is whatever the regime table predicts for the cadence inferred over the WIDENED lookback, stated per group in advance | a change not predicted by the regime table for the widened-lookback cadence |

  ⛔ **The unconditional "any change at all" failure was deleted 2026-09-22 after independent
  review: it would reject a CORRECT implementation.** Widening the inference lookback can change
  an already-resolving cadence, legitimately. Worked example: 32 rows at 1200 s followed by 18 at
  600 s — the three-hour window sees only the recent rows and infers 600 s (a rule resolves),
  while the widened lookback takes the median of all 49 gaps and infers 1200 s, which no rule
  declares, so the correct new outcome is regime 2 / `QC_UNCHECKED`. That is the fix working, and
  the old gate called it a regression. **Compare against the widened-lookback prediction, not
  against the pre-state.**

  A per-observation diff on identical inputs is discriminating in both directions the count
  comparison is not: no legitimate measurement change can enter it, and no reclassification can
  cancel out inside it. The partition is what makes it discriminating about *repair* as well as
  about *regression*.

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

  **🔴 Why that count is scoped to fetches, and not to every statement against `observations`.** An
  earlier draft of this bullet bounded "the count of `observations` queries per run (from
  `pg_stat_statements`)" by the same arithmetic. That measurement **fails on the *unchanged*
  system, before any deploy**, so it would gate the deploy on a threshold nothing has ever met:
  `_run_qc_task` calls `obs_store.update_qc` once **per observation**
  (`flows/ingest_observations.py:356`), and each call issues one `UPDATE` against `observations`
  (`store/observation_store.py:143-158`), on top of the per-run `INSERT` from
  `store_raw_observations` (`store/observation_store.py:64-96`, reached via `_store_raw_task` at
  `flows/ingest_observations.py:711`). A single group carrying 18 rows therefore contributes 19
  statements by itself. The arithmetic intent — one existing fetch plus one bounded inference fetch
  per ingesting group — was right; only the measurement scope was wrong. `pg_stat_statements` may
  still be the instrument, but only with the QC-path `SELECT` isolated by `queryid`; counting the
  writes says nothing about where the cap is.
- N ≥ 12 (one hour of runs) covering at least one full poll cycle of every active adapter.

**Rollback.** ⚠️ **Code-only rollback holds ONLY until the first `qc_unchecked` row exists.**
*(Corrected 2026-09-22 after independent review — this paragraph still described the pre-T2b
world and contradicted T2b item 10.)* There is no migration (T3 confirms `check_type` is
`sa.Text`), no schema change and no data rewrite, so **before** any `QC_UNCHECKED` is written
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

From the owner-commissioned high-risk operations review. The paired gate above is the
**right instrument** and round 6 was right to force it. These are the gaps around it.

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
proves the mechanism; it proves nothing about the fleet. **Required:** the pinned range
must deliberately include a **known degraded window** — a LINDAS outage/429 stretch per
Plan 175, or an ingest gap identified by Q2 below.

**C6 — 🔴 the gate is not a deliverable of any task.** T2's *In* surface lists
`types/domain.py`, `services/qc.py`, `flows/ingest_observations.py`,
`store/observation_store.py`, `protocols/stores.py`, the DHM mask and the byte-identical
fixture. **The harness that runs old-and-new over identical rows has no file, no task and
no owner.** It also needs the *old* selection path to still exist at evaluation time — a
second implementation of the very thing T2 spends a section deleting. ✅ **Resolved
2026-09-20 — see T6**, which measures that "second implementation" at about fifteen lines
and freezes it as a validated snapshot rather than keeping it alive in `src`.

**C4 — ship behind a `DeploymentConfig` flag defaulting `False`, and bump
`qc_rule_version`.** The plan specifies image-revert as rollback. **This repo already has
the right pattern for exactly this shape of change:**
`config/deployment.py:146` `enable_skill_generations: bool = False` is a Plan 235
two-release flag whose own comment explains it exists so "a rollback during the rollout
window always lands on an image that already understands the rows on disk"
(`docs/standards/cicd.md`'s one-release rollback rule). Adopt it:

1. New selection path behind a flag, default `False` — the image deploys byte-identical
   in behaviour to today.
2. Enable it in `config/overlays/mac-mini.toml`, which is a **host bind mount** into
   `prefect-worker`, `prefect-worker-ingest` and `api`
   (`docker-compose.macmini.yml:41, 47, 71`, `:ro`). **Rollback becomes a host file edit
   plus a container restart — seconds, and reversible mid-cycle — instead of a rebuild.**
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
the mini has **no image registry** (`docs/standards/cicd.md:274`; images are local-only and
the Sunday prune protects only `^rollback($|-)` tags). Image revert on this host is a
procedure, not a button, and it needs both overlays plus the exported tokens.

**C4b — wire the zero-rule check into the watchdog.** `types/enums.py`'s
`PipelineCheckType` has no zero-rule member and the watchdog probes exactly three check
types (`ops/watchdog.py:142-178`). Without one more probe, T3's `WARNING` is pull-only via
`/api/v1/health/detail`, and D5's mitigation (a) — "the Plan 268 runbook reader must read
it" — reduces to a human remembering to look. One more probe URL makes it Slack.

**⛔ A true shadow mode is better and is NOT recommended.** Computing both and writing the
old would make the paired gate continuous rather than one-shot, but it costs the double
computation T3 explicitly forbids. The flag buys most of the safety for a fraction of the
design.

### T2b — Record an unchecked outcome as `QC_UNCHECKED` (D5, D2)

*Added 2026-09-20 because D5's closure named a new stored status that no task owned.*
⭐ **Rewritten the same day after the review pair: the first version was insufficient for exactly
the half this plan calls "the half a status change alone would get wrong."** Four things it
missed are now in scope, each verified against `main`.

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
4. **The write site** in `services/qc.py` / `flows/ingest_observations.py`: zero resolved rules ⇒
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
   ⛔ **Bound the attempts** (a counter or an age cut-off) so a permanently-regime-2 group cannot
   be re-examined on every run forever — silent unbounded retry is the 264 T3 failure in a new
   costume. ⚠️ This does **not** reopen D3: no *historical* row is re-examined, because historical
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
   `docs/standards/cicd.md:202`'s one-release rollback rule. **Ship a compatibility release
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
  permanently-unresolvable group stops being retried after the bound.
- Ingest counters report `unchecked` separately from `suspect`.
- The **compatibility release** is proven: an image without the write path reads a database
  containing `qc_unchecked` rows without raising.
- ⚠️ **The partial index** `postgresql_where=qc_status == "qc_passed"` (`db/metadata.py:568`):
  confirm the planner still uses it on the reads that still demand `QC_PASSED`.

**Pre-change**: a RED test proving that today a zero-rule group stores `QC_PASSED` with empty
flags and is indistinguishable, in the stored row, from a group that genuinely passed.

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

**An earlier draft of this task froze `check`'s signature and had the flow call a resolution method
*alongside* it. That design reproduces the defect with reassuring telemetry, and it must not be
built.** The mechanism, measured at `9dc07915`:

`Stage1QualityChecker.check` infers the cadence from **the observations it was handed** — it groups
its own argument (`services/qc.py:243-251`) and calls `time_step = _infer_time_step(group)` then
`rule_set.rules_for(parameter, time_step)` (`:252-253`). The flow hands it the **three-hour checked
window** (`flows/ingest_observations.py:334`, fed by the fetch at `:311-316`). T2 introduces a
**second, wider** fetch for inference. If the resolution is computed from the wide fetch while
`check` keeps inferring from the narrow one, then for a daily series **the telemetry resolves the
daily rules from the 30-day lookback and reports the group as resolved, while `check` sees one row,
infers 1 h, matches nothing and runs zero rules.** That is the original defect, now with a
`pipeline_health` record asserting it did not happen — strictly worse than today, because today at
least nothing claims otherwise.

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

**🔴 NOT `rule_id`s, and not `rule_version`s either — `rule_id` identifies a KIND, not a
configured rule.** An earlier draft of this bullet wrote "the actual `QcRuleParams` *(or their
`rule_id`s)*", and the parenthetical reintroduces the defect the object exists to close. Measured
at `9dc07915`:

- `QcRuleId` is a `Literal` (`types/domain.py:137`) — a closed set of *kinds*. `config.toml`
  carries **seven** rows with `rule_id = "range_check"` — measured by parsing the file at
  `9dc07915`: `discharge` at 600 s and 86400 s, `water_level` at 600 s and 86400 s,
  `water_temperature` at 600 s, `precipitation` at 86400 s, `temperature` at 86400 s. Since
  `rules_for` also filters on `parameter` (`types/domain.py:163-167`), the collision that matters
  is the **within-parameter** one: `discharge` and `water_level` each carry two `range_check` rows
  differing **only** in cadence. And **every one of the file's 26 rows declares
  `rule_version = "1.0.0"`** (`config.toml:212` onward) — also measured, not read off one row. So
  neither field, alone or together, names a configured rule.
- The repo already knows this and says so where it bit: `rule_subset`'s docstring
  (`scripts/dhm_precip/qc_ruleset.py:111-114`) filters by `rule_version` explicitly because
  `skipped_rule_ids` "cannot distinguish two `frozen_sensor` instances sharing one `rule_id`".
  In the DHM mask rule set `rule_version` *is* the distinguishing field; in `config.toml` it is
  not. There is no single scalar field that works for both.

**The consequence if the parenthetical is built.** Take an hourly series against a rule set
carrying `range_check` at 3600 s (selected) and `range_check` at 1800 s (not selected). The two
rows share a `rule_id`. Asking "is this rule's id in the resolution's id set?" answers **yes for
both**, so the 1800 s rule reads as selected and T2's re-expressed guard does not raise on it —
**the mixed-set defect `_raise_on_time_step_mismatch` exists to catch survives the fix.** The
existing mixed-set test cannot catch this: `tests/unit/scripts/test_dhm_precip_mask.py:185-216`
mixes an hourly `range_check` with a 30-minute `frozen_sensor` — **two different kinds**, so id
membership happens to give the right answer there and the test stays green.

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

**✅ One adjacent hazard, CHECKED and cleared — recorded so nobody re-derives it.** The flow passes
`check` the **datum-shifted** observations (`qc_observations`,
`flows/ingest_observations.py:328-340`) while the inference fetch would read **raw** stored rows,
so the two row sets are not the same objects and a key mismatch would produce exactly the raise
above. It does not: `shift_observations_for_water_level_datum` (`services/qc_datum.py:41-52`)
rebuilds each observation with `replace(obs, value=obs.value - datum)`, touching **`value` only** —
`station_id`, `parameter`, `timestamp` and `id` are all preserved — so the `(station_id,
parameter)` key set is identical either way. No spurious raise, and **no requirement to build the
resolution from the shifted rows**. Recorded as a verified non-hazard, not as a task.

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
These are the sites that move, verified at `9dc07915`:

- **The Protocol**: `protocols/stores.py:1029-1037` (`QualityChecker.check`). Untouched:
  `ForecastQualityChecker` at `:1041+`, a different protocol.
- **The `check(...)` invocations**: `flows/ingest_observations.py:334`, `services/onboarding.py:796`,
  and `scripts/dhm_precip/qc_mask.py:203` and `:208`.
- **The spec**: `docs/spec/types-and-protocols.md:697-714` (the `QualityChecker` Protocol block) —
  T4 owns it, and it is **already** one parameter behind the code (see T4's *In*).
- **Not in T4's surface, but do not be misled by it**: `docs/design/dhm-precipitation-milestones.md:1641-1642`
  states that `Stage1QualityChecker` "has two call sites: `flows/ingest_observations.py:188` and
  `services/onboarding.py:637`". Both line numbers are stale at `9dc07915` (the real sites are
  `:333`/`:334` and `:773`/`:796`) and the count omits the two DHM-mask calls. That document is a
  milestone record, not a contract, so this plan leaves it alone — **awareness only**, recorded
  so the next reader does not take it as a competing call-site inventory.
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
  `services/qc.py:235` — so a zero-rule group returns an empty list, not a `KeyError`, and these
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

*Method, measured at `b361eb22` on 2026-09-19:* regime 3 was simulated for the whole file by
monkeypatching `QcRuleSet.rules_for` to return `()` on every call — exactly what the
same-observations constructor produces for a one-row group — and running `uv run pytest
tests/unit/services/test_qc.py`. Of the twelve sites the run reports six failures, five passes, and
`:102` split two-and-two. **An earlier fold recorded this split as 8/4.** That figure was read off
the assertions rather than run, and it mis-sorted three sites — `:117` and `:377` assert an *empty*
result and therefore stay green rather than going red, `:385` asserts a flag and therefore goes red
rather than staying green — and counted the parametrised `:102` as wholly red. **The substance is
unchanged either way: the silently-green half is the hazard, and correcting the split makes it
larger, not smaller.**

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

**⚠️ What is NOT accepted any more: computing the resolution twice.** An earlier draft accepted
re-running `groupby` and `_infer_time_step` alongside `check` as "the price of leaving `check`'s
return type alone". With `check` consuming the resolution that price is not paid and the
duplication does not exist — **and it must not be reintroduced**, because two computations over two
different row sets *is* the defect above. Compute it once, pass it down.

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

**⚠️ And that test starts from ZERO coverage, so budget it as a new test rather than an
amendment.** Measured at `9dc07915`: **nothing under `tests/` imports
`build_dudh_koshi_handover`** — grep returns only `docs/plans/**` references — so
`attribute_mask_by_rule` and `_empty_rule_set` (`build_dudh_koshi_handover.py:154-191`) have **no
test anywhere**, despite `attribute_mask_by_rule` calling `qc_mask._station_mask` once per rule
and feeding the Dudh Koshi handover's per-rule attribution (`:1080`). The path this constraint
protects is therefore protected today by nothing at all, and a regression in it would surface in
a delivered artefact rather than in CI. T3's test is the first coverage it gets.

**In**: `services/qc.py` (the resolution value type, its constructor, and `check` consuming it —
per (b) above and § The resolution is ONE object); `protocols/stores.py:1029-1037`
(`QualityChecker.check`, whose signature moves with it); the three non-flow `check(...)`
invocations (`services/onboarding.py:796`, `scripts/dhm_precip/qc_mask.py:203`, `:208`) and their
tests, all mechanical via the same-observations constructor;
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

⭐ **Rewritten 2026-09-20: D5's closure changed this answer.** An earlier revision said the
fail-open stays open after this plan and that "the closure is 264 T3's raise". **Both halves are
now wrong** — D5 closes it here, and D2 removed 264's raise.

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

⭐ **Reconciled with D5, 2026-09-20 — and D5's guarantee is hereby QUALIFIED.** D5 is written as
an unqualified statement ("a zero-rule group is recorded `QC_UNCHECKED`"). It is not unqualified:
onboarding's `:812` feeds `aggregate_qc_status([])`, which returns `QC_PASSED`, so **the onboarding
path would still store `QC_PASSED` over zero rules.** The correct scope of D5 and T2b is
**the scheduled ingest path**.

**Accepted, for a measured reason rather than by omission**: onboarding's window is historical and
wide, so the daily rules *do* resolve there (§ Problem) — which is also why the pre-existing
contamination is bounded (D3). ⛔ **But it must be written into D5 and T2b as a stated boundary**,
because an unqualified guarantee that a stored status carries a meaning is exactly the kind of
claim a later consumer will rely on. If onboarding is ever given a narrow window, this exception
becomes a defect and must be revisited.

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

*Added 2026-09-20. The high-risk review's rollout conditions and D7's abort gate were folded as
prose in § rollout controls and § D7, and **no task's In surface owned any of them** — the second
instance in this plan of a closure naming work with no builder. C6's paired-gate harness is still
unowned and is called out separately below.*

**Outcome**: the change can be enabled per station, reverted in seconds without a rebuild, aborts
itself if it removes too much data, and announces a zero-rule group to an operator rather than
waiting to be asked.

**In**:
- **A `DeploymentConfig` flag defaulting `False`**, gating **both** the selection change and
  T2b's `QC_UNCHECKED` write (the write must be gated too — see T2b item 9, rollback). Follow the
  Plan 235 precedent at `config/deployment.py:146`.
- **Per-station scoping** for the canary: `stations.network` exists and the QC loop already
  iterates per `(station_id, parameter)`.
- **The enable path**: `config/overlays/mac-mini.toml`, a host bind mount into `prefect-worker`,
  `prefect-worker-ingest` and `api` (`docker-compose.macmini.yml:41, 47, 71`, `:ro`).
  ⚠️ Edit it **in place** — a new inode leaves the container reading the old content.
- **D7's automatic abort**: the threshold, **its denominator and window** — Q2's per-station,
  per-window share, per T1; ⛔ NOT a per-cycle share read back from stored rows, which no row
  carries a key for — the comparison, and the revert action. It reads T2b's new `unchecked`
  counter **separately** from `suspect`, or it measures the wrong thing. 🔴 **The number itself
  comes from T1's Q2 census** and is not set here.
- **Retained-row behaviour after the flag is disabled** — rows already written `QC_UNCHECKED` stay
  written; say so, and say what re-enables them (T2b item 7's re-examination).
- **`qc_rule_version`** bump in `services/qc_datum.py:23-26`, making the deploy boundary a
  queryable column rather than a timestamp in a document.
- **A new `PipelineCheckType` member and a watchdog probe** (`types/enums.py:193-213`,
  `ops/watchdog.py:142-178`) so T3's `WARNING` reaches Slack rather than waiting for a human to
  query the health endpoint. *(This was described as a follow-on at C4b; it is in scope here.)*

**Also in T5's surface, because T6 builds the harness but does not decide when it matters:**
- **Running T6 on a degraded pinned range is an activation precondition** — the flag is not
  enabled until that gate has passed.
- **Deleting T6 and its frozen snapshot when the rollout completes** is an activation-checklist
  item. ⛔ A frozen copy of a deleted code path that outlives its rollout becomes a second
  implementation nobody remembers is there — the exact thing T2 exists to remove.

**Out**: the harness itself (T6 builds it).

**Verification**:
- 🔑 **With the flag `False`: no `QC_UNCHECKED` row is written and no new inference fetch is
  issued.** ⛔ *NOT "an identical observation table" — that requirement was deleted 2026-09-22
  after independent review, because nothing in this plan can satisfy it.* T2 deletes the
  `< 2 rows ⟹ 1 h` fallback outright and T6 **explicitly rejects** "a flag keeping the old path
  in `src`" as the duplication T2 exists to remove. So the selection arithmetic ships
  UNCONDITIONALLY with T2 and the flag cannot restore the old verdicts; disabling the fetch and
  the status write cannot undo a deleted branch.
  ⚖️ **OWNER — this narrows what the flag promises, and you should see that rather than find
  it.** The flag is a *status-write* control, not a *behaviour* rollback: it bounds the new
  `QC_UNCHECKED` population, not the changed selection. What covers the selection change is the
  compatibility release plus T6's paired harness, which is what they are for. If a true
  behaviour rollback is wanted instead, T6's rejected alternative has to be re-opened and the
  old path kept in `src` — a decision, not an implementation detail.
- Enabling for one station changes that station and no other.
- A simulated loss above the threshold aborts and reverts without human action.
- The rollback anchor is tagged before the upgrade (`docker tag … rollback-backup`) — the mini has
  **no image registry** (`docs/standards/cicd.md:274`) and the weekly prune protects only
  `^rollback($|-)` tags.

### T6 — The paired old/new evaluation harness (C6)

*Scoped 2026-09-20 at the owner's instruction. This was the last standing unowned condition
and the one the plan leaned on hardest: every rollout control it specifies depends on this
gate, and it had no file, no task and no owner through three review rounds.*

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
T2 already specifies a byte-identical-output test over a pinned hourly series; the frozen
pair must reproduce that fixture's output **exactly**. If the snapshot has drifted, every
diff the gate produces is measuring the wrong baseline and the gate is worse than no gate.
**Assert this before the harness is used for anything.**

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

#### The pass criteria, partitioned by regime

| regime | selection & flags | aggregated status |
|---|---|---|
| **1** — resolves rules today | **unchanged** | **unchanged** — this is the Swiss no-change guarantee |
| **2** — cadence declared nowhere | unchanged (still zero rules) | 🔴 **MUST change `QC_PASSED` → `QC_UNCHECKED`** (D5) |
| **3** — fewer than 2 rows | unchanged (still zero rules) | 🔴 **MUST change** as regime 2 |
| **repairable** | rules newly selected; flags may appear | may change |

⛔ **An empty diff in regimes 2 and 3 now means T2b did not run** — it is a failure, not a pass.

#### The input range — and why a healthy one is worthless

🔴 **The pinned range MUST contain a known degraded window**: a LINDAS outage or 429 stretch
(Plan 175), or an ingest gap identified by T1's Q2 census. The repairable population on Swiss
is *episodic*. A range taken from a healthy period yields an empty diff everywhere, which is
indistinguishable from "nothing was repaired" and from "the new code never ran" — and then
forces the constructed-case fallback, which proves the mechanism and nothing about the fleet.

#### Out

Writes of any kind. The flag, the canary and the abort (T5's). Anything under `src/`.

#### Verification

- **The frozen snapshot reproduces T2's byte-identical fixture exactly** — run first; nothing
  else in this task means anything until it passes.
- ⛔ *Deleted 2026-09-22 after independent review: a criterion requiring a healthy and a
  degraded range to produce DIFFERENT diffs, on the reasoning that identical diffs prove the
  range was not degraded. It does not. Missing readings or a rate-limit incident need not move
  the median off 600 s, so both implementations can legitimately agree throughout a genuinely
  degraded period, and the gate would reject a correct implementation. The per-observation
  expectations and the required repairable case below already test what this was reaching for.*
- A run against a database containing **zero** regime-2 groups **says so explicitly** rather
  than reporting success. *(The "0 findings" / "never ran" ambiguity is the failure mode this
  whole task exists to avoid; it must not reproduce it in its own output.)*
- **It writes nothing**: the `observations` table is byte-identical before and after, asserted
  on a checksum, not inspected by eye.

#### Sequencing

Lands in **phase 2** with T2/T2b/T3/T5 — it needs T2's new path to compare against. **Running
it is a T5 activation precondition**: the flag is not enabled until this gate has passed on a
degraded range.

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
**and record that leaving regime 3 is not the same as resolving a rule**, which is why 264 T3's
rollout prerequisite is stated on T3's telemetry rather than on a row count (§ Ordering
consequence).

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
they are one landing.** ⭐ **T5 joins phase 2 as well, corrected 2026-09-20** — an earlier graph put it in a later phase, which would have made phase 2 **a complete, mergeable, UNFLAGGED behaviour change with no abort control and no rollback-compatible release.** T2b item 9 requires the flag to gate the **status write**, and D7's auto-abort reverts that same flag; both presuppose the flag exists *in the release that first writes `QC_UNCHECKED`*. A safety control that lands after the thing it controls is not a safety control. T2b joins them for the same reason: the selection repair (T2) and the
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

## Review divergences

Recorded so a later reader does not re-derive them.

- **Catch-up does not make the daily rules fire.** The 2026-09-19 review stated that on a DHM
  catch-up run the widened window's median "lands on 86400 s and the twelve daily rules DO fire",
  and that the headline should become "non-deterministically reachable on catch-up runs". Checked
  against `adapters/dhm.py:206-220`: recovery returns the station's native ~10-minute rows, so a
  multi-day catch-up yields a 600 s median. The window *is* variable and history-dependent (a real
  defect, recorded above), but the daily rules stay unreachable. The headline was restated
  accordingly, not as the review proposed.
- **The 3.5 % figure reproduces; its attribution does not.** See T1a — the five windows are a
  capture-boundary effect (sparse window), not ordinary mid-series gaps. Recorded with the caveat
  rather than as a steady-state rate.
- **`types/enums.py:187-190` had not drifted.** The review reported it should be `:186-189`; at
  `9dc07915` the original citation is correct.
- **The "132 fully-inside windows" figure was this plan's own error, and it is 126.** Re-measured
  at `9dc07915` by re-running the T1a slide: 144 grid points over
  `2026-05-04T18:15Z … 2026-05-05T18:05Z`, 12 trimmed at the head (`now − 2 h` before the first
  row) and 6 at the tail (`now + 1 h` after the last), leaving 126. The gap histogram
  (600 s ×104, 1200 s ×6, 1800 s ×3, 2400 s ×3, 3600 s ×1) and the 5/144 zero-rule count
  reproduce exactly, and the fully-inside count is 0 under either definition. Corrected in T1a.
- **The five zero-rule windows sit in the first *five*, not the first twelve.** Measured:
  18:15, 18:25, 18:35, 18:45, 18:55 — grid indices 0-4. The original statement was true but loose
  about a published number; tightened in T1a.
- **The final gate's five "call sites" for `Stage1QualityChecker.check` are five *construction*
  sites, and the distinction matters for T3.** Verified at `9dc07915`:
  `flows/ingest_observations.py:333`, `services/onboarding.py:773`,
  `scripts/dhm_precip/qc_mask.py:232` and `:338`, and
  `scripts/dhm_precip/build_dudh_koshi_handover.py:175` all instantiate the checker; the
  `check(...)` **invocations** that a return-type change would actually break are
  `flows/ingest_observations.py:334`, `services/onboarding.py:796`, and
  `scripts/dhm_precip/qc_mask.py:203` and `:208` — the handover build never calls `check` itself,
  it passes its checker into `qc_mask._station_mask`. Recorded as a refinement, not a dispute:
  the gate's set of five entry points is right, and T3 now names both layers.
- **The `qc_checker.check(...)` calls on the forecast side are a different class and are not in
  scope.** `services/forecast_combination.py:332`, `services/run_station_forecast.py:549,557` and
  `services/run_group_forecast.py:285,293` call `ForecastOutputQualityChecker.check`, not
  `Stage1QualityChecker.check`. Checked so that a future signature change is not scoped against
  the wrong nine sites.
- **`ix_observations_station_timestamp` is not the *only* index on `observations` — there are
  three, and the gate's conclusion survives all of them.** Round 4 wrote "the only observations
  index is `ix_observations_station_timestamp` on `(station_id, timestamp)` — `parameter` is not in
  it". Verified at `9dc07915`: `alembic/versions/0001_v0_schema.py:253-263` creates **two**
  (`ix_observations_station_timestamp` and the partial `ix_observations_station_timestamp_qc_passed`,
  both on `(station_id, timestamp)`) and
  `alembic/versions/0035_observation_forecast_rating_curve_binding.py:80-84` adds a third,
  `ix_observations_station_source_ts` on `(station_id, source, timestamp)`. **None contains
  `parameter`**, so the heap-filter conclusion is right and is folded as stated; only the premise
  was loose. Recorded because "the only index" is the kind of claim a later reader re-derives a
  plan from.
- **"A widened per-group fetch scans the station's whole range" is true only of an *unbounded*
  widening.** With the `L`/`N` bounds T2 now states, the backwards index walk is bounded by the
  `LIMIT` in the ordinary case and by `L` otherwise. **⚠️ "Otherwise" was written as "a group with
  no rows in `L`"; round 6 corrects it to any group with fewer than `N` matching rows — 0-49 at
  `N` = 50 — which is an ordinary sparse group, not a pathology.** Folded as the bounded form,
  with the corrected worst case named, rather than as the unbounded claim — which would have
  argued against a widening the gate itself agreed is within D1.
- **The guard's new raise is on an *exactly-one-row* group, not a "≤ 1-row" one.** Round 4's B2
  said ≤ 1. Measured: zero rows is already guarded twice — `_raise_on_time_step_mismatch:152-153`
  returns early on an empty list, and `_station_mask:197-198` returns `frozenset()` on an empty
  station — so the build-breaking case is exactly one row, where the guard actually runs. The
  finding stands; the boundary is tightened in T2 § What the re-expressed guard does, because an
  implementer testing the "≤ 1" case with an empty list would see no raise and conclude the
  blocker was imaginary.
- **`tests/unit/scripts/test_dhm_precip_mask.py` has 18 test functions, not 24.** Counted at
  `9dc07915`: 18 `def test_`. The substance of B3 is unaffected and is folded in full — the file is
  entirely behavioural and there is **no** golden fixture, which is the finding. Recorded only
  because the count appears in T2's backstop section.
- **The post-deploy query bound was mis-scoped, and would have failed at baseline.** Round 5.
  Measured at `9dc07915`: `_run_qc_task` calls `update_qc` once per observation
  (`flows/ingest_observations.py:356`) and each call issues one `UPDATE` against `observations`
  (`store/observation_store.py:143-158`), on top of the per-run `INSERT`
  (`store/observation_store.py:64-96`, via `_store_raw_task` at `flows/ingest_observations.py:711`),
  so the statement count against that table already exceeds `2 × |station_params| + 1` on the
  unchanged system. The arithmetic intent was right; the scope was not. Re-scoped to the QC path's
  *fetch* count, bounded at `2 × |station_params|`.
- **"Loses one raise" is true only against a rule set that mixes cadences.** Round 5. All three
  deployed DHM mask rules declare 3600 s (`scripts/dhm_precip/qc_ruleset.py:38`, carried at `:59`,
  `:72`, `:86`), so a one-row group's fabricated 1 h matches everything and does **not** raise
  today either — the net against deployed behaviour is *zero raises changed*. The summary line
  contradicted its own case (a) ("builds fine") one paragraph above. The lost raise survives only
  for a mixed set such as `tests/unit/scripts/test_dhm_precip_mask.py:185-216`. The operative
  constraint is unchanged.
- **A one-row *station* does lose a mask key; a one-row *season* does not.** Round 5. Pass B's
  `frozen_sensor` needs 168 consecutive rows (`scripts/dhm_precip/params.py:148`, counted as rows
  at `services/qc.py:133-134`), so a one-row season cannot be flagged today and the "no mask keys,
  exactly as today" claim holds there. Pass A's `range_check` has no run-length requirement and
  today matches the fabricated 3600 s, so a one-row station with an out-of-range value **is**
  flagged today and is not after T2. Real, tiny, and now a required element of the byte-identical
  fixture — with that one key named as the fixture's single allowed delta, since asserting bare
  equality would make it fail for the reason it was added.
- **D5 overstated its own exposure; the bound is "until two rows exist for the group".** Round 5.
  **⚠️ Superseded in part by round 6 below: the bound is right for *regime 3* and wrong as a bound
  on "zero QC", because regime 2 also resolves nothing and no row count exits it.**
  Regime 3 is fewer than 2 rows in the whole lookback, not in the checked window, so a group leaves
  it at its second stored row. Verified at `9dc07915`: `_store_raw_task`
  (`flows/ingest_observations.py:711`) precedes the QC loop (`:743`), so the inference fetch sees
  this run's rows; and a cold station's watermark is `now − 1 h` (`:558`, `:662`, `:666-667`),
  which the DHM adapter recovers at native cadence (`adapters/dhm.py:174-204`) — ~6 rows on the
  ~10-minute river feed. The six Plan 268 stations therefore plausibly exit regime 3 on their first
  ingest. D5's conclusion survives; its size does not. The outage claim narrows with it: an outage
  shorter than `L` = 30 days leaves rows in the lookback and never enters regime 3.
- **The catch-up rebuttal was qualified at *both* sites, not only in this section.** The gate
  asked for a qualifying clause on the § Review divergences entry below. The same claim also
  stands in § What the widening does and does not do, point 3, which is the primary site — a
  correction that leaves the uncorrected sentence standing elsewhere is the failure mode this repo
  has already booked. Both now carry it.

### Round 6 — the first Codex pass

- **🔴 The wider inference had no route into actual checking, and five Claude passes missed it.**
  Codex, round 6. Verified at `9dc07915` and upheld in full: `Stage1QualityChecker.check` infers
  from its own argument (`services/qc.py:243-253`), which is the three-hour window
  (`flows/ingest_observations.py:311-316`, `:334`). T3's frozen-signature design resolved the
  cadence from a *different* fetch, so the telemetry and the checking would have disagreed by
  construction — the defect, plus a health record saying it did not happen. **Folded as a single
  resolution object consumed by both, which forces the `check` signature change T3 had been
  written to avoid.** Recorded because the frozen signature had survived four rounds as a stated
  virtue; it was buying a smaller diff at the price of the bug.
- **🔴 The resolution shape had to carry per-rule identity anyway — a second, independent reason.**
  Codex, round 6. Verified: `_raise_on_time_step_mismatch` enumerates *each* mismatched rule
  (`scripts/dhm_precip/qc_mask.py:155-158`) precisely because `rules_for` filters per rule, so a
  mixed rule set can have some rules run and others silently dropped. "A cadence plus *any* rule
  resolved" is true in exactly that case, so the boolean shape could not have served T2's guard
  even if the fetch question had never arisen. Both reasons are recorded; either alone settles it.
- **🔴 "The daily rules become reachable" overstated the repair by 4 of 12.** Codex, round 6.
  Verified by reading each rule body: `_apply_rate_of_change` returns `None` when `prev is None`
  (`services/qc.py:77-78`) and `_apply_spike` when `prev` or `nxt` is `None` (`:157-158`), and both
  take their neighbours from the checked group (`:267-268`). A daily series puts at most one row in
  three hours, so daily `rate_of_change` and `spike` — 2 + 2 rules, on `discharge` and
  `water_level` — are **selected and inert**. `range_check` and `gross_outlier` (4 + 4) are
  per-observation and are genuinely repaired. Folded as a delimited coverage claim plus a required
  insufficient-context test, **not** as a fix, because supplying the context means widening the
  checked window, which the plan forbids for `frozen_sensor` reasons. Refinement on top of the
  finding: `precipitation` and `temperature` declare only the two per-observation rules, so for
  the D4 parameters the repair is complete — the shortfall is confined to `discharge` and
  `water_level`.
- **🔴 The `limit=50` assertion was vacuous against the implementation it existed to forbid.**
  Codex, round 6. Upheld without qualification: a store that accepts `limit`, ignores it, fetches
  30 days and slices in Python satisfies "the fake recorded `limit=50`". Folded as a
  production-store SQL assertion (`LIMIT` + `ORDER BY timestamp DESC`) plus a real-Postgres check
  (`tests/integration/conftest.py:16,47`) on row count, recency and chronological return order —
  the last because `_infer_time_step` medians *signed* gaps (`services/qc.py:43-47`), so a
  descending list yields a negative `timedelta`.
- **🔴 `L` = 30 days and `N` = 50 are starting bounds, not measured fleet costs.** Codex, round 6.
  Upheld: no query has been run at either value. Relabelled in place, with the cost question
  routed to the pre-rollout latency measurement.
- **🔴 The full-`L` heap walk happens for 1-49 matching rows, not only for zero.** Codex, round 6.
  Upheld and slightly widened: the walk reaches the end of `L` whenever fewer than `N` rows match,
  i.e. **0-49** at `N` = 50 — zero was already named, and the correction is that every count below
  the cap behaves the same way. A sparse parameter beside dense siblings is an ordinary
  configuration, so this is the common case for exactly the groups the widening was added for.
- **🔴 The Swiss regression gate compared different observations.** Codex, round 6. Upheld:
  successive live periods differ in the water as well as in the code, so the comparison fails on
  legitimate change and passes on a reclassification that preserves totals. Folded as a **paired
  old/new evaluation over identical inputs, run before any deploy**, with the live counts demoted
  to operational monitoring and the latency bound kept as a hard gate (latency is a property of
  the system, so successive periods *are* a fair comparison for it). The asymmetry Codex named —
  **reverting the image does not un-write the verdicts** — was already in § Rollback but was not
  connected to the gate; it is now the reason the gate runs pre-rollout.
- **🔴 Two rows establish inferability, not rule resolution.** Codex, round 6. Upheld and swept:
  a group leaving regime 3 lands in regime 1 **or** regime 2, and regime 2 resolves zero rules
  permanently. 264 T3's rollout prerequisite is restated on **T3's zero-rule telemetry reporting
  no group over a full poll cycle**, not on a row count. Codex's second clause is also recorded:
  later cold or unsupported groups will still meet 264 T3's raise, and **that is its intentional
  fail-closed behaviour, not a regression in this plan**. Corrected at all four sites — D5,
  § Ordering consequence, T4 (d), and the round-5 entry above.

**What round 6 confirmed, and which later rounds should not re-litigate:**

- **The stored-`QC_PASSED` limitation is honestly and prominently disclosed, not buried.** Codex
  checked T3's "🔓 What this does NOT close" and D3 specifically. Leave the placement and the
  emphasis alone.
- **The existing defect-encoding test is addressed correctly.**
  `tests/unit/services/test_qc.py:468` and the "repairing it by asserting the bare empty list
  reinstates the defect" warning were judged right as written.
- **Declining an unsupported cadence is preferable to unrestricted nearest matching.** The regime
  table's refusal to resolve the nearest rules was independently endorsed. Do not reopen it.
- **The rows-versus-hours objection to a wide tolerance is valid** — `_apply_frozen_sensor` counts
  rows while the DHM rule set expresses its thresholds in hours. Codex noted the cost the
  ±10 % bound carries: **a sparse or jittered series goes unchecked rather than approximately
  checked**, and **a small bounded tolerance remains a defensible separate choice** if that cost
  is judged too high. Recorded as an open trade the implementer may raise with the owner, not as
  something this plan has closed against.

### Round 7 — both halves of the two-model gate

Every item below was re-verified against the worktree before folding; where the finding
understated or overstated what the code shows, the correction is recorded with it.

- **🔴 `rule_id` cannot serve as a configured-rule identity — and neither can `rule_version`.**
  Codex, round 7. Upheld and **widened**. Codex named `rule_id`; measured at `9dc07915`,
  `config.toml` also gives **every one of its 26 rule rows `rule_version = "1.0.0"`**
  (`config.toml:212` onward), so the obvious substitute fails too, while in the DHM mask rule set
  `rule_version` is the *only* distinguishing field (`rule_subset`,
  `scripts/dhm_precip/qc_ruleset.py:111-118`). There is no single scalar that works for both rule
  sets, which is why the resolution carries the `QcRuleParams` objects and membership is object
  equality. Codex's test point is upheld exactly as stated:
  `tests/unit/scripts/test_dhm_precip_mask.py:185-216` mixes `range_check` (hourly) with
  `frozen_sensor` (30-min) — **different kinds** — so id membership gives the right answer there
  by luck and the test stays green over the defect. A same-kind guard test is now required.
- **🔴 "A one-line change each" was false, and the direction of the failure is asymmetric.**
  Codex, round 7. Upheld. Measured: **twelve** single-observation `check` calls in
  `tests/unit/services/test_qc.py` (`:102`, `:117`, `:124`, `:134`, `:377`, `:385`, `:396`,
  `:413`, `:430`, `:462`, `:493`, `:531`), all resting on `_STEP = timedelta(hours=1)` (`:22`)
  matching the fabricated fallback. **Six go red, five stay green while exercising nothing, and the
  parametrised `:102` splits two-and-two** — corrected in the final fold from the 8/4 this log
  originally recorded, by running the file under a simulated regime 3 (§ What keeps the diff from
  exploding, *Method*); the silently-green half is the one the finding is really about, and it is
  larger than first recorded. `TestOverrideMerging` (`:401-435`) is
  the sharpest case: threshold merging is unobservable against an empty selection. Folded as four
  binding per-site requirements.
- **🔴 The rollout gate contradicted regimes 2 and 3.** Codex, round 7. Upheld. A blanket
  "non-empty on T1b's zero-rule groups" fails a correct change on any group that legitimately
  stays unresolved, and — the direction that matters more — a Swiss input set containing nothing
  repairable passes a weakened version of the gate while demonstrating no repair. Folded as a
  four-class expected-outcome partition plus a **required repairable case**, constructed with a
  narrow checked-row set and a separate inference history if the live fleet supplies none.
- **🔴 The insufficient-context test could pass for the wrong reason.** Claude, round 7. Upheld:
  "produces no flag because the window supplies no `prev`/`nxt`" is satisfied identically by a
  value that simply does not breach `max_rate`/`max_delta` (`services/qc.py:79-80`, `:161-165`),
  and such a test would stay green the day someone widens the checked window — the one event it
  exists to catch. Folded as breaching consecutive values plus a positive control over a group
  containing the neighbours.
- **🔴 `_infer_time_step` returns `timedelta | None`; the fabrication dies at source.** Claude,
  round 7. Upheld. A constructor-level guard leaves `cadence = _infer_time_step(obs)` as a
  one-line way to reinstate regime 3 as a fabricated hour. **Caller sweep, measured:** the
  function has exactly two references — its definition (`services/qc.py:40`) and one call
  (`:252`, which T3 removes) — plus the mirror `_inferred_time_step`
  (`scripts/dhm_precip/qc_mask.py:127`, called at `:154`), which T2 deletes. **No third consumer
  exists**, so the signature change reaches nothing beyond the two sites the plan already owns.
- **🔴 The resolution is keyed on the checked groups, not on the fetched rows.** Claude, round 7.
  Upheld and traced: `station_params` comes from `raw_obs`
  (`flows/ingest_observations.py:716-718`), the checked window is widened to span `fetched_times`
  (`:297-309`) but the inference fetch is bounded by `L`, so a DHM catch-up older than 30 days
  yields zero inference rows for a group that has observations to check. A fetch-keyed constructor
  omits its key, `check` raises, and the blanket `except Exception` at `:759-767` swallows it into
  `ingest.qc_failed` — **leaving the rows at `RAW` permanently**. Folded as "fewer than two rows,
  **including zero**, ⟹ a regime 3 entry".
- **🔴 `rule_set` and `skipped_rule_ids` had no stated fate in the new `check`, and the second one
  hides a third defect instance.** Claude, round 7. Upheld. `rule_set` is dropped (it would be a
  second, unreconciled source of selected rules). `skipped_rule_ids` stays, applied **after**
  resolution — verified: `rules_for` at `services/qc.py:253`, the skip filter at `:256` — which is
  required for the live datum path (`obs_skipped_rules`, `services/qc_datum.py:29-32`, passed at
  `flows/ingest_observations.py:339` and `services/onboarding.py:801`). **Consequence, previously
  unstated:** a daily `water_level` group with no datum resolves exactly four daily rules, of which
  `range_check` and `gross_outlier` are datum-skipped and `rate_of_change`/`spike` are inert — so
  **every selected rule is skipped or inert and the group still reports as resolved**. Disclosed,
  not fixed; routed into T4 (e) as the third of three routes to selected-but-not-run.
- **🔴 `docs/standards/wmo.md` is unconditional in T4's *In*.** Claude, round 7. Upheld on two
  independent grounds. (1) `wmo.md:187` cites `services/qc.py:50`, `:71` and `:225` — verified
  **accurate today** (`_apply_range_check`, `_apply_rate_of_change`, `class
  Stage1QualityChecker`) and **all three move** under T2 + T3, as does its runnable evidence,
  `tests/unit/services/test_qc.py`. (2) The row offers `_apply_rate_of_change` as the evidence for
  WMO-168's *temporal-consistency checks*, which T2 § What the repair actually restores measures as
  never firing on a daily series through the scheduled path. The row's existing "Scope of this
  evidence" note is what keeps it honest; T4 must re-date and re-scope it rather than repointing
  line numbers. Recorded with the repo's own precedent — `023-degraded-forecast-input-quality.md`
  and five months of false WMO compliance from a plausible-looking evidence row.

**Free wins folded in the same round (Claude, round 7):**

- **T3's health record names *which* rules resolved, in `detail`.** The resolution already carries
  the objects, so it costs a field and no computation, and it makes "the group resolved" checkable
  against "and `rate_of_change` was among them" from telemetry rather than from a doc. Recorded as
  **recommended, not required** — as the finding proposed.
- **✅ Verified non-hazard: the shifted-versus-raw observation question.** The flow passes `check`
  datum-**shifted** observations while the inference fetch reads raw stored rows, which would
  produce a spurious key mismatch. It does not:
  `shift_observations_for_water_level_datum` (`services/qc_datum.py:41-52`) rewrites **only**
  `value` via `replace(...)`, preserving `station_id`, `parameter`, `timestamp` and `id`. Recorded
  so it is not re-derived as a blocker.
- **`attribute_mask_by_rule` has no test anywhere.** Verified: nothing under `tests/` imports
  `build_dudh_koshi_handover`, so `attribute_mask_by_rule` and `_empty_rule_set`
  (`build_dudh_koshi_handover.py:154-191`, used at `:1080`) are uncovered. T3's `_empty_rule_set`
  test therefore starts from zero, not from an existing suite.
- **The `_empty_rule_set` shape is not regime 3's shape.** `rule_subset(..., frozenset())` yields a
  `QcRuleSet` with an empty `rules` tuple (`qc_ruleset.py:115-118`), so its resolution has a
  **normally inferred cadence** (3600 s on a dense hourly station) with an empty selection, while
  regime 3 is cadence `None` with an empty selection. The plan said "the same shape"; harmless,
  but the distinction is what lets `check` tell a deliberate empty request from an uninferable
  series. Corrected in place.
- **`docs/spec/types-and-protocols.md:697-714` is already stale.** It omits `skipped_rule_ids`,
  which `protocols/stores.py:1035` has carried for some time. T4 fixes it incidentally; recorded
  so the correction is not mistaken for something T3 introduced.

**Cosmetics folded:**

- The `def check` block is `protocols/stores.py:**1029**-1037` — `:1028` is the `class` line.
  Corrected at all three citation sites.
- `docs/design/dhm-precipitation-milestones.md:1641-1642` claims `Stage1QualityChecker` has two
  call sites at `flows/ingest_observations.py:188` and `services/onboarding.py:637`. Both stale,
  and the count omits the two DHM-mask calls. **Outside T4's surface — awareness only**, noted in
  T3's moved-sites list so it is not read as a competing inventory.

### Round 9 — the owner-commissioned high-risk review (2026-09-20)

Required by `docs/workflow.md` before READY. Deliberately scoped to the plan as an
**operational change to the running Mac-mini Swiss deployment**, with an explicit
instruction not to re-review the specification. Verdict: **safe to implement, not safe to
deploy as specified — conditional go on nine conditions.** The orchestrator verified every
repository-side claim against `main` before folding; several were sharpened in the process.

**The one structural finding, from which the rest follow:** the plan never traces a
changed QC verdict past the `observations` row. Every rollout control it specifies
operates on QC outputs; none operates on what consumes them. → § What a changed verdict
does downstream (thirteen `QC_PASSED` call sites, verified by grep).

**Three things eight prior rounds did not reach:**

1. **The blast-radius framing was wrong for the Swiss fleet — safe direction for the
   headline, unsafe direction for the gate.** No live Swiss group is daily, so the twelve
   daily rules do not become reachable on this deployment at all; the population that
   actually changes verdict is the sparse/jittered sub-daily set (C1).
2. **That population is also the one most likely to be flagged wrongly** (C3).
   Sharpened while folding: `config.toml` pairs `time_step_seconds = 600` with
   `max_rate = 50.0` for discharge and `86400` with `500.0` — the thresholds are
   *explicitly* step-sized, and `_apply_rate_of_change` never divides by elapsed time.
3. **264 T3 does not halt.** Its raise is caught per-group and the rows strand at `RAW`,
   producing a permanently dark station — worse than a halt, because a halt is visible
   (C8). Every description of 264 T3 in this plan, in 264's own note and in the memory
   corpus said "halt". → § What 264 T3 actually does.

**Corrected while folding, against the review:** the review described
`qc_rule_version` as "write-only in production". It is read back into `Observation` and
hydrated at `store/observation_store.py:320`; the accurate claim is that **no production
code branches on its value**, which is what makes the C4 bump free.

**Not recommended, and recorded so it is not re-proposed:** true shadow mode (compute
both, write the old). Better than the flag, but it costs the double computation T3
forbids, and the flag buys most of the safety.

**What only a live database can settle**, now specified as C7's Q1–Q4 in T1: whether any
live Swiss group is genuinely daily; how many groups sit in regime 2 or 3 and how that
varies over a day; **whether `station_thresholds` is populated** — which re-prices the
alerting risk from highest-consequence to latent; and the fleet shape.

## Explicitly out of scope

- ⛔ ~~**The fail-closed policy for a resolve-nothing outcome** — Plan 264 T3 owns it (D2).~~ **NO LONGER OUT OF SCOPE.** D2 (closed 2026-09-20) moved it here: T2b writes `QC_UNCHECKED` and 264's raise is removed.
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
  `Stage1QualityChecker`; not touched (see § Review divergences).
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
