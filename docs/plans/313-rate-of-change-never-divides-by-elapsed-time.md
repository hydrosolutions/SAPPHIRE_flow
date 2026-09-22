---
status: DRAFT
created: 2026-09-22
plan: 313
title: rate_of_change never divides by elapsed time — it is a step check wearing a rate's name
scope: Make the observation QC rule `rate_of_change` account for the elapsed time between the two readings it compares, so a threshold declared for one cadence means the same physical limit when the readings arrive at another. ONLY that rule's own arithmetic. NOT which rules get selected (272), NOT the network dimension of selection (264), NOT per-station threshold overrides (269), NOT the Nepali threshold VALUES (268 D14), NOT supplying daily series with neighbouring context rows (304), NOT `_apply_spike`, NOT forecast QC's `temporal_consistency`.
depends_on: []
blocks: []
related: [264, 268, 269, 272, 304]
open_decisions: [D1, D2, D3, D4]
source: 2026-09-22 — `docs/v1-scope.md:448-450` records the defect and states in as many words that nobody owns it. Every claim below was re-measured against `main` at `37ec3e95` (v0.1.957) on 2026-09-22; each says how, and the three claims that need the staging host say so and are T1.
---

# Plan 313 — `rate_of_change` never divides by elapsed time

⚠️ **The plan number 313 is PROVISIONAL until the owner grants it.** 312 is the highest
number in `docs/plans/`; 302–305 are referenced by Plan 272 but no file for any of them
exists, so the sequence is not a reliable allocator. Nothing else in this plan depends on
the number.

## Status

**DRAFT.** No review has run. ⛔ Only the orchestrator may set READY.

⚠️ **This is high-risk work under `docs/workflow.md` § High-risk work** on two of its named
grounds: *user-visible behaviour* and *scientific behaviour with material operational
consequences*, on a rule that runs today against the live Swiss deployment. It therefore
needs one relevant independent review **in addition to** the ordinary Claude/Codex pair
before READY, and again before its implementation PR.

## Problem

### What is MEASURED

**1. The rule does not reference either timestamp.** `services/qc.py:71-89`. The whole
comparison is `abs(obs.value - prev.value) > thresholds["max_rate"]` (`:80`). `obs.timestamp`
and `prev.timestamp` appear nowhere in the function. ⇒ It is a **step check between two
consecutive stored rows**, not a rate. The `detail` string it emits calls the difference a
"rate" (`:86`), which is part of how this stayed invisible.

**2. The two consequences follow directly from (1), and are arithmetic, not opinion.**
At half the declared cadence a change of `max_rate` per declared step arrives as two
half-steps and never trips; across a gap of six declared steps a legitimate slow change
arrives as one difference of six steps' worth and does trip. `docs/v1-scope.md:448-450`
states both, and names the defect as unowned.

**3. The rule already holds everything it needs to fix this.** `QcRuleParams.time_step`
(`types/domain.py`, read at `services/qc.py:32` and selected on at `:252-253`) is the
observation time step the rule's thresholds are declared for, and `rule` is already a
parameter of `_apply_rate_of_change`. **No new data has to reach the function.**

**4. The deployed thresholds are explicitly step-sized — the step is the unit.** Measured
by grep: `max_rate` appears at **five** places in `config.toml` (`:222`, `:257`, `:292`,
`:320`, `:348`), **five** in the built-in Swiss defaults (`config/qc_rules.py:57`, `:93`,
`:129`, `:165`, `:201`), and **five** in `docs/spec/config-reference.toml`. Discharge
carries `max_rate = 50.0` at `time_step_seconds = 600` and `500.0` at `86400` — a 10×
difference that exists only because the step differs. 🔑 **The configured numbers are
already "units per declared time_step". The code is what fails to honour that.**

**5. 🔴 The code matches none of its three specifications, and the three specifications
do not match each other.** All four surfaces read on 2026-09-22:

| surface | what it says `max_rate` is |
|---|---|
| `services/qc.py:79-80` | units per **reading-to-reading difference**, whatever the spacing |
| `docs/spec/types-and-protocols.md:587` | "`max_rate` (units **per second**)" |
| `docs/design/v0-flow2-observation-pipeline.md:245` | `abs(value - prev) / dt > max_rate` — **divided by dt** |
| `docs/architecture-context.md:288` | "a fixed **cm/min** limit ... portable across stations" |
| `docs/spec/config-reference.toml:299,335,371,407,443` | "m³/s **per 10 min**", "m³/s **per day**", "m **per 10 min**", … |

⇒ **Three of the five assert a time-normalised quantity; the code implements none of them.**
This is not a plan proposing new semantics — it is a plan making the code do what the
design documents already say, and making the five documents agree on which.

**6. Where the over-sensitivity can actually bite on the live Swiss path — bounded.**
`_run_qc_task` (`flows/ingest_observations.py:283-313`) fetches a context window from
`now - 2h` (`context_window_hours = 2.0`), widened to span `fetched_times`, and hands the
**stored rows** to `check`. So `prev` is a real neighbour and `Δt` is whatever gap the feed
actually had. A gap **shorter** than the window leaves a pair with an inflated `Δt` inside
the window — the over-sensitive case. A gap **longer** than the window leaves the first row
with no `prev` at all and the rule stays silent. The onboarding path
(`services/onboarding.py:780-801`) fetches a wide historical window, where a multi-day hole
in a daily series produces exactly the inflated-`Δt` pair.

**7. 🔴 `Δt` can be ZERO, and the fix must not divide by it.** The observations natural key
is `(station_id, timestamp, parameter, source)` — `db/metadata.py:570-577`. The repo states
the consequence itself, at `services/component_derivation.py:56-60`: *"A component may carry
several rows for the same timestamp (e.g. `measured` plus a `rating_curve_derived` backfill)
because `source` is part of the observations natural key."* `fetch_observations`
(`store/observation_store.py:160-187`) takes an optional `source` filter and **neither QC
caller passes it**. `check` sorts on `(station_id, parameter, timestamp)` only
(`services/qc.py:237-239`), so two such rows become a consecutive pair with `Δt = 0`.
⚠️ No `rating_curve_derived` row is written by ingest today (grep: the enum member is
referenced at `enums.py:235`, `component_derivation.py:22`, `observation_version_store.py:46`
and tests — never written by `flows/ingest_observations.py`), so this is a **live structural
possibility, not an observed occurrence**. `manual_import` alongside `measured` reaches it
without any new code.

**8. Every existing test that asserts a `rate_of_change` verdict is at EXACTLY the declared
step, so the backwards-compatible option leaves them all green.** Verified by reading, not
by running:

- `tests/unit/services/test_qc.py` — `_STEP = timedelta(hours=1)` (`:22`), `_make_obs(value,
  hours)` (`:30-50`); all three `TestRateOfChange` cases (`:141-165`) and the integration
  case (`:500-511`) use consecutive integer hours ⇒ `Δt == time_step`.
- `tests/unit/flows/test_ingest_observations.py` — rule `time_step=600 s` (`:49-54`),
  fixtures `offset_minutes=10` ⇒ nominal. `test_qc_context_window_detects_rate_of_change`
  (`:269`) and `test_null_water_level_datum_skips_datum_dependent_rules_only` (`:322-345`)
  both stay green.
- `tests/unit/flows/test_ingest_observations_derivation.py:94-95` — prior point at
  `offset_minutes=10` against `time_step=600 s` ⇒ nominal.
- `tests/unit/flows/test_ingest_observations_dhm.py` —
  `test_six_hour_recovery_qcs_old_rows_with_preceding_context` (`:112-165`) is the one case
  that looked at risk. Traced: the asserted row `recovered[0]` is at −360 min, its `prev` in
  the window is the QC_PASSED context row at −370 min (`:115`) ⇒ `Δt = 10 min`, exactly the
  rule's step (`:52`), and `|Δv| = 2.0` against `max_rate = 0.5`. **Still SUSPECT.** The
  off-nominal pair in that fixture (−420 → −370, `Δt = 50 min`) is not asserted on.

**9. Threshold keys are not validated anywhere.** `config/qc_rules.py:14-30` validates
`rule_id` against `_VALID_RULE_IDS` and then takes `thresholds` verbatim
(`dict(raw["thresholds"])`). There is no key allow-list to update. (Plan 269 § 406 proposes
one; it is not built.)

### What is DEDUCED, not measured

- That the insensitivity at dense cadence has ever hidden a real event, or that the
  over-sensitivity across a gap has ever produced a spurious `QC_SUSPECT` **on the live
  Swiss fleet**. Both are entailed by the arithmetic; **neither has been observed**.
  Plan 272 reached the same wall — *"How often is UNMEASURED and needs sub-daily data
  nobody has"* (`docs/plans/272-...md:365`).

### What I could NOT measure, and why

⛔ **No access to the staging host while this was written.** Three things need it and are
**T1**, not claims: the distribution of consecutive-row `Δt` per `(station, parameter)`; how
many consecutive pairs in a recent window sit at a spacing other than the rule's declared
step; and how many pairs would change verdict under the fix, in each direction. **No number
for any of these appears anywhere in this plan.**

## Backwards compatibility — stated precisely, not hand-waved

The rule runs today against a live Swiss deployment, and **fifteen configured `max_rate`
values exist** (five in `config.toml`, five in the built-in defaults, five in the reference
TOML — measured, § 4).

**Under the recommended option (D1a — scale the allowance by `Δt / rule.time_step`):**

- **At `Δt == rule.time_step` the comparison is arithmetically identical to today.** The
  allowance is `max_rate × 1.0`. ⇒ Every one of the fifteen configured values keeps its
  exact present meaning, which is also the meaning `config-reference.toml`'s own comments
  already give it. **No migration. No new threshold key. No config edit. No enum, no
  migration file, nothing added to the configuration surface.**
- **Off-nominal spacing is where and only where behaviour changes**, and the direction is
  known: denser than declared ⇒ the allowance shrinks ⇒ **more** flags; sparser ⇒ the
  allowance grows ⇒ **fewer** flags. Both are the defect being corrected, in the direction
  `v1-scope.md:448-450` names.
- **It is therefore backwards-compatible in configuration and deliberately not in
  behaviour** — behaviour changing at off-nominal spacing is the entire point. The magnitude
  of that change on the live fleet is T1.

**Under D1b (true per-second `max_rate`, as `types-and-protocols.md:587` claims today):**
every one of the fifteen values must be divided by its step — by 600 or by 86400. A single
missed value is wrong by two to five orders of magnitude, silently, in a rule that emits
`QC_SUSPECT`. **This buys no behavioural difference whatsoever** over D1a: `max_rate_per_sec
× Δt` and `max_rate_per_step × Δt / step` are the same number. ⇒ Recommended against.

**Under D1c (a new key, `max_rate` retained):** two keys mean two code paths, two documented
semantics and a deprecation to run later, for the same arithmetic. ⇒ Recommended against as
over-engineering; it exists here only because the owner is owed the option.

## Owner decisions

### D1 — normalise in place, or introduce a second threshold key?

| | option | cost |
|---|---|---|
| **(a)** ⭐ | Scale the existing allowance: flag when `abs(Δv) > max_rate × (Δt / rule.time_step)` | Behaviour changes at off-nominal spacing only. No config change, no new key, no migration. `max_rate` keeps its present numeric meaning and gains an honest name for it. |
| (b) | Redefine `max_rate` as units **per second** | Requires migrating 15 values by hand across 3 files; a missed value is wrong by 600× or 86400×. Zero behavioural gain over (a). |
| (c) | Add `max_rate_per_second` beside `max_rate` | Two code paths and two documented meanings for one comparison; a deprecation to run later. |

**Recommendation: (a).** It is the smallest change that makes the rule a rate, and it is the
only one of the three that requires no config migration on a live system.

### D2 — what happens when `Δt <= 0`?

Reachable per § 7 (two sources at one timestamp). Options: **(a) ⭐ return `None`** — the
pair carries no elapsed time, so it carries no rate; **(b)** flag it as a data-integrity
problem — but `rate_of_change` is the wrong rule to report that from; **(c)** raise — the
caller's `except Exception` (`flows/ingest_observations.py:759-767`) would swallow it and
strand the rows at `RAW`, which Plan 272 § C8 records as worse than a halt.

**Recommendation: (a).** Note what it changes: today such a pair *can* produce
`QC_SUSPECT`; after (a) it never does. That is a deliberate, stated behaviour change.

### D3 — cap the allowance across a long gap?

With no cap, a 30-day hole in a daily series yields an allowance of `30 × max_rate`, so the
rule is **effectively inert across large gaps**. Options: **(a) ⭐ no cap** — that is what
"rate" means, and it is precisely the over-sensitivity `v1-scope.md` asks to remove;
**(b)** cap the factor at *N* — adds a configuration knob, a default to justify and a value
per deployment; **(c)** skip the pair entirely beyond *N* steps — same new knob.

**Recommendation: (a), with the residual stated rather than engineered away:** a genuine
step change that happens to straddle a long gap will not be flagged by this rule. It is
still reachable by `range_check` and `gross_outlier`, which are per-observation. ⚠️ T1's
`Δt` distribution is what tells the owner whether (a) is comfortable in practice — which is
why T1 runs first.

### D4 — should the flag's `rule_version` change? (gates nothing)

`_apply_rate_of_change` stamps the module-level `_RULE_VERSION = "1.0"` (`services/qc.py:22`,
`:83`), shared with `range_check`, `spike` and `gross_outlier`; `_apply_frozen_sensor` uses
`rule.rule_version` instead (`:132`). Options: **(a) ⭐ leave it** — Plan 272 verified that no
production code branches on the value, and flags are already discriminable by write time;
**(b)** bump `_RULE_VERSION` — changes the version stamped on three other rules that did not
change; **(c)** switch this rule to `rule.rule_version` — makes the stamp configurable but
changes the stored string from `"1.0"` to `"1.0.0"` for every future rate flag.

**Recommendation: (a).** **This decision gates no task.**

## Tasks

### T1 — Measure the blast radius on staging

**Outcome.** For a recent window on the live staging database, three counts recorded in this
plan under a new `## Measured on staging` section: (i) the distribution of consecutive-row
`Δt` per `(station_id, parameter)`; (ii) the share of consecutive pairs whose `Δt` differs
from the declared `time_step` of the `rate_of_change` rule that selects for them; (iii) among
those pairs, how many change verdict under D1a — separately for *flag added* and *flag
removed*.

**In.** One read-only SQL query against staging `observations`, run by the owner or by an
agent with host access. The results, written into this plan.

**Out.** ⛔ No evaluation harness, no paired old/new runner, no script committed to the repo,
no monitoring. Plan 272 T5/T6 already own rollout controls for this module and this task must
not build a second set. ⛔ No writes of any kind.

**Pre-change.** N/A — measurement only; no behaviour changes.

**Verification.** The query text and its per-class counts appear in `## Measured on staging`
with the date and the database version. A run returning zero off-nominal pairs is a valid and
informative result, not a failure.

### T2 — Divide by elapsed time

**Outcome.** `_apply_rate_of_change` compares the observed change against an allowance scaled
by the elapsed time between the two readings, expressed in units of the rule's own declared
`time_step`; at exactly that step the verdict is unchanged from today. The `detail` string
names the elapsed interval and the scaled allowance, so a flag can be read without knowing
this plan exists.

**In.** `src/sapphire_flow/services/qc.py` — the body of `_apply_rate_of_change` (`:71-89`)
only. `tests/unit/services/test_qc.py` — the four cases below plus the minimal helper
widening they need (`_make_obs` currently takes whole `hours` and hard-codes
`source=ObservationSource.MEASURED`). The patch version bump in `pyproject.toml` required by
`docs/workflow.md` § Hard boundaries.

**Out.** ⛔ `_apply_spike` and its `max_delta` (§ Out of scope). ⛔ `forecast_qc.py`'s
`temporal_consistency`. ⛔ `check`'s signature, the cadence inference, rule selection, the
skip set, `merge_thresholds`, any threshold value, any config file. ⛔ Any new threshold key
(under D1a).

**Pre-change — the discriminating evidence.** ⚠️ **Three cases must go RED; the fourth is a
GREEN control and must not.** *(Corrected 2026-09-22 after independent review — the earlier
wording required all four to fail, which case 3 cannot do by construction.)*

🔑 **Every case must carry NOMINAL CONTEXT ROWS, and this is not optional padding.** The
existing tests drive the rule through `Stage1QualityChecker.check`, which INFERS the cadence
from the rows it is given — `_infer_time_step` takes the **median** of consecutive differences
(`services/qc.py:40-47`) — and `rules_for` then selects on **exact equality**
(`types/domain.py:163-166`). A two-row fixture spaced 15 minutes apart therefore infers a
900 s cadence, matches no hourly rule, and **runs nothing at all**. Measured consequence of the
naive fixture: cases 2 and 4 would pass without the fix, and case 1 would stay red *after* it —
a test that proves the opposite of what it claims.

⇒ **Each case supplies at least five consecutive differences at the rule's declared step, with
the exceptional pair as a minority, so the median stays at the declared step and the rule is
actually selected.** For an hourly rule: rows at 0 h, 1 h, 2 h, then the exceptional pair, then
two more hourly rows — differences `[3600, 3600, X, 3600, 3600]`, median `3600`. ⛔ No change to
selection, inference or `check`'s signature is needed or permitted to make this work.

1. **Dense cadence must now flag — RED.** Hourly rule, `max_rate = 5.0`; the exceptional pair
   **15 minutes** apart differing by `3.0`, inside hourly context. Today: `3.0 ≤ 5.0` ⇒ no flag.
   Required: allowance `5.0 × 0.25 = 1.25` ⇒ `QC_SUSPECT`.
2. **A gap must now absorb a proportionate change — RED.** ⚠️ **This case uses the deployed
   600 s discharge rule, NOT an hourly one**, and that is deliberate: every other case here is
   hourly, so an implementation that divides by a hard-coded one hour instead of by
   `rule.time_step` would satisfy all of them while mis-scaling the 600 s and daily rules that
   most of the live fleet actually runs. *(Added 2026-09-22 — independent review found the
   whole set blind to that implementation.)* Rule `time_step = 600 s`, `max_rate = 50.0`; the
   exceptional pair **60 minutes** apart differing by `200.0`, inside 600 s context. Today:
   `200.0 > 50.0` ⇒ `QC_SUSPECT`. Required: allowance `50.0 × 6 = 300.0` ⇒ no flag.
3. **The backwards-compatibility control — GREEN, before and after.** At exactly
   `Δt == time_step`, a pair just under and a pair just over `max_rate` keep today's verdicts.
   ⚠️ **This case passes against `main` and must keep passing; it is not RED evidence and
   proves nothing about the fix** — nominal spacing is the one place the two implementations
   agree by construction. Cases 1, 2 and 4 are what prove the fix.
4. **Zero elapsed time (D2) — RED.** Two rows at one timestamp with different `source` (§ 7),
   differing by more than `max_rate`, inside nominal context. Today: `QC_SUSPECT`. Required
   under D2a: no flag, and **no `ZeroDivisionError`**.

**Verification.**
`uv run pytest tests/unit/services/test_qc.py tests/unit/flows/test_ingest_observations.py
tests/unit/flows/test_ingest_observations_dhm.py
tests/unit/flows/test_ingest_observations_derivation.py` — all pass, and **every pre-existing
`rate_of_change` assertion passes unmodified** (§ 8 predicts this; if any of them has to be
edited, that is a finding about this plan, not about the test, and the implementer stops).
Then `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`,
`uv run pyright src/`.

### T3 — Make the five documents say one thing, and the right one

**Outcome.** The four surfaces that state `max_rate`'s semantics agree with each other and
with the code: `max_rate` is the change permitted **per the rule's declared `time_step`**,
and the comparison scales with elapsed time.

**In.**
- `docs/spec/types-and-protocols.md:587` — replace "units per second" with the declared-step
  wording.
- `docs/design/v0-flow2-observation-pipeline.md:245` — restate the formula as
  `abs(Δvalue) > max_rate × (Δt / time_step)`.
- `docs/architecture-context.md:288` — replace "a fixed cm/min limit" with the declared-step
  wording; the sentence's substantive point (that a rate limit is portable on level and
  stage-dependent on discharge) is correct and stays.
- `docs/spec/config-reference.toml` comments at `:299`, `:335`, `:371`, `:407`, `:443` —
  verify, do not rewrite: they already read "per 10 min" / "per day" and become **correct**
  under D1a. Recording this as a verification step, not an edit.
- `docs/standards/wmo.md:187` — **line citations only.** T2 adds lines inside
  `_apply_rate_of_change`, which shifts the row's `services/qc.py:225` citation for
  `Stage1QualityChecker`.

**Out.** ⛔ The "Scope of this evidence" sentence and the `Verified` date on `wmo.md:187`.
Plan 272 T4 explicitly owns re-dating and re-scoping that row (272 § Round 7). Touching it
here would contradict a closed decision in another plan. ⛔ `docs/v1-scope.md` — its § 4 risk
entry becomes owned rather than unowned only once this plan is granted a number and lands;
the owner updates it then.

**Pre-change.** N/A — documentation, no behaviour change. The RED evidence for the claim
these documents were wrong is § 5 of this plan, measured.

**Verification.** Bounded inspection: each of the five surfaces states the same semantics as
`services/qc.py` after T2, and `wmo.md:187`'s three line citations resolve to
`_apply_range_check`, `_apply_rate_of_change` and `class Stage1QualityChecker` respectively.

## Explicitly out of scope

- **`_apply_spike` has the same shape** — `abs(Δv) > max_delta` against consecutive rows
  (`services/qc.py:150-174`), named by Plan 272 § C3 alongside this one. ⛔ Not fixed here,
  and for a reason, not only for size: a spike is an *excursion that returns*, which is a
  claim about shape rather than about rate, so whether its threshold should scale with
  spacing is a different question with a different answer. 🔴 **It is, like this one was,
  unowned. Flagging it to the owner as a separate decision, not adopting it.**
- **`forecast_qc.py:184-200` `temporal_consistency`** — forecast steps sit on a regular grid
  by construction, so the defect does not arise. Untouched.
- **Threshold VALUES** — Plan 268 D14 (needs a hydrologist). This plan changes what a value
  *means at off-nominal spacing* and changes no value.
- **Per-station overrides** — Plan 269. `merge_thresholds` is untouched; a scaled allowance
  applies identically to a merged threshold.
- **Which rules are selected, and `QC_UNCHECKED`** — Plan 272. **Plan 264** for the network
  dimension.
- **Supplying a daily series with neighbouring context rows** — Plan 304.

## Cross-plan

- **272 and 313 both edit `services/qc.py`, in disjoint functions.** 272 T2/T3 change rule
  *selection*, `check`'s signature and `_infer_time_step`; 313 T2 changes only the body of
  `_apply_rate_of_change`. No logical dependency either way — `rule.time_step` is the rule's
  own declared step and is unaffected by how cadence is inferred — but **whichever lands
  second rebases**, and 272's `wmo.md` row is shared (see T3 *Out*). `depends_on: []` is
  deliberate: this does not need 272, and 272 does not need this.
- **🔑 313 mitigates 272 § C3 rather than aggravating it.** C3's hazard is that 272 newly
  applies the 600 s rules to rows 20–120 minutes apart, where *"a legitimate flood rise or an
  ordinary diurnal warming exceeds them"*. Under D1a those same pairs get an allowance scaled
  by 2×–12×. ⚠️ Stated as a consequence, **not** as a claim to own any part of C3 or to
  relieve 272 of its rollout controls.
- **269 is `blocked_by: [272]`** and proposes a rule→key validator (§ 406). Under D1a no key
  is added, so that validator is unaffected.

## Review history

**Round 1 — independent Codex pass, 2026-09-22. PROBLEMS FOUND (2 MEDIUM, 1 LOW), all three
in T2's pre-change evidence, all three folded.** The arithmetic and the no-migration claim
were NOT challenged, and no new mechanism was proposed — the fixes were "supply context rows"
and "change one case to a non-hourly rule", explicitly *"rather than adding a harness"*.

1. **The fixtures would not have exercised the rule at all** (MEDIUM). Two-row fixtures drive
   `check`, which infers the cadence by MEDIAN and selects rules by EXACT equality, so a
   15-minute pair matches no hourly rule. Cases 2 and 4 would have passed without the fix and
   case 1 would have stayed red after it. ⇒ every case now carries nominal context rows, and
   the mechanism is written down so the implementer cannot drop them as padding.
2. **Nothing discriminated against a hard-coded hourly denominator** (MEDIUM). Every positive
   case used `time_step = 1 h`, so an implementation dividing by a literal hour would pass the
   whole set while mis-scaling the deployed 600 s and daily rules. ⇒ case 2 now uses the
   deployed 600 s discharge rule.
3. **The blanket "all four must fail first" contradicted case 3** (LOW), which is a
   compatibility control and passes by construction. ⇒ three RED, one GREEN, each labelled.

⚠️ Still owed before READY: the Claude half of the ordinary pair, plus the extra independent
review this plan's high-risk classification requires.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1"], "parallel": false },
    { "id": "phase-2", "tasks": ["T2"], "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T3"], "depends_on": ["phase-2"] }
  ]
}
```
