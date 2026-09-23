---
status: DRAFT
created: 2026-09-23
revised: 2026-09-23
plan: 315
title: The onboarding loader must not let unchecked readings pass
scope: The observation-onboarding QC path's fail-open (`services/onboarding.py:772-841`) — re-measuring whether a zero-rule group can still occur there once the QC ladder has landed, and closing the fail-open together with the three in-run consumers that read its output. NOT the scheduled ingest path (Plan 272, shipped), NOT the network dimension of rule selection (264), NOT per-station threshold overrides (269), NOT `rate_of_change`'s arithmetic (313), NOT new `precipitation`/`temperature` rule rows (303), NOT the rollout controls (314), NOT re-QC over already-stored history (closed by D3).
depends_on: [264, 269, 303, 313]   # D1 — runs after every plan that changes the rule set's SHAPE
blocks: []
related: [264, 269, 272, 303, 313, 314]
open_decisions: []
reviews:
  - "codex 2026-09-23 r1 — NOT READY, 1 HIGH + 6 MEDIUM, all against the evidence; every one verified and folded"
  - "codex 2026-09-23 r2 — NOT READY, 3 MEDIUM + 1 LOW on the fold: unsound descope, hardcoded cadence set, central-risk test with no expected outcome"
  - "claude 2026-09-23 r2 — NOT READY, 6 MEDIUM + 2 LOW: the consumer exclusion is a NO-OP (store filters by equality), the hold mechanism already exists and was unnamed, census population contradiction, depends_on contradicted D1; VERIFIED claims 1-8 including the load-bearing per-day-of-year baseline argument"
  - "codex 2026-09-23 r3 — NOT READY, 3 MEDIUM: a fold bullet contradicted D1; the skill criterion and the narrowed-baseline outcome were still DEFERRED rather than decided"
  - "codex 2026-09-23 r4 (NARROW: plan vs the DECISION RECORD, not the code) — CONTRADICTIONS FOUND, 3 MEDIUM: an unconditional HOLD survived inside the flag-exempt baseline rule; the plan claimed to close 269's descope, which is about overrides not zero-rule marking; it assigned the DHM forecast targets to 268, which explicitly disclaims them"
  - "claude 2026-09-23 r3 — NOT READY, 2 MEDIUM + 2 LOW: the baseline trigger and its test were DIFFERENT tests and neither could fire on a first-ever onboarding; held_out_ids also skips model assignment and training. NO correction overshot into a false claim; verified the Step 8 ordering, the no-op, and every citation in the real control flow"
source: 2026-09-23 — owner, on reviewing PR #297: *"for now it's ok, we'll have to have a plan that follows the full QC implementation to check if this still happens. Once QC stands, we should not allow the loader to let unchecked readings pass."* Every claim below was measured on `feat/plan-272-qc-selection` at `54673d79` (v0.1.960), i.e. against the tree as it will be once #297 merges — not against today's `main`, which does not yet contain `QC_UNCHECKED`.
---

# Plan 315 — the onboarding loader must not let unchecked readings pass

⚠️ **The plan number 315 is PROVISIONAL until the owner grants it.** 302–305 were granted in
prose and never written, so the sequence is not a reliable allocator (Plan 314 records the same
caveat).

## Status

**DRAFT.** ⛔ Only the orchestrator sets READY.

⏸️ **This plan runs LAST — owner, 2026-09-23.** Its premise is that the QC ladder has landed and
the rule set has stopped changing shape. Scheduling it earlier measures a moving target. See D1.

## Why this plan exists

The same gap has now been descoped by two separate plans, each for a good local reason, and
neither left an owner behind:

- **Plan 272 D5** shipped `QC_UNCHECKED` on the *scheduled ingest* path and stated the onboarding
  exclusion in terms: `aggregate_qc_status([])` there still yields `QC_PASSED`. The reason given
  was that the marking lives in the flow rather than in `check`, and that onboarding's window is
  historical and wide, so the daily rules resolve. An independent Codex review of the
  implementation filed that exclusion as a HIGH; it was refused, with the reason written into
  `services/onboarding.py:811-817`, and Codex accepted the refusal as a policy question for the
  owner.
- **Plan 269 § What this deliberately does not do** descoped the same path — *"The onboarding QC
  path keeps `overrides=[]`"* (`269:236-238`) — and ends: *"If that becomes load-bearing, it is a
  follow-on that should be designed against the onboarding path's own gate, not bolted onto this
  one."*

⛔ **But 269's descope and this plan's defect are NOT the same thing, and an earlier draft said
they were.** 269 left onboarding judging history against **base thresholds only**, for want of
per-station overrides. This plan leaves overrides untouched (see `scope`) and fixes a different
defect on the same path: a group that selects **zero rules** reading as a clean pass. ⇒ **This
plan is a follow-on against the gate 269 named, not the closure of 269's own limitation** — that
one stays open and still belongs to whoever picks up per-station overrides for onboarding. It is also the thing that makes `docs/v1-scope.md` § QC posture
point 4 true rather than aspirational — that section already commits to *"Once QC is in place and
fine-tuned: unchecked data must NOT enter forecasting"*, and nothing currently delivers it on the
onboarding side.

## What is measured

Measured on `feat/plan-272-qc-selection` at `54673d79` unless stated otherwise.
⚠️ **Claims 4, 6 and 7 were each overstated in the first draft and are corrected here** — the
corrections are marked, because in this project a wrong claim has twice been cited back as review
evidence.

**1. The fail-open, exactly.** `services/onboarding.py:772-841` is Step 5. It fetches
`qc_status=RAW` rows only (`:780-782`), calls `Stage1QualityChecker.check` with `overrides=[]`
(`:796-801`), and feeds the result to `aggregate_qc_status` (`:819`, `types/domain.py:104-106`),
where **an empty flag list returns `QC_PASSED`**. It does not call `resolve_selection`, so it
cannot tell "every rule passed" from "no rule ran". The comment at `:811-817` says so.

**2. Onboarding QCs only the station's forecast target.** `station_target.get(station_id)`
(`:775`) — a station with no forecast target is skipped entirely (`:776-778`). In the deployed
config the targets are `discharge` and `water_level`, so `precipitation` and `temperature` never
reach this path. ⚠️ **This also means the six DHM gauges are skipped today**, because Plan 268
leaves their `forecast_targets` unset — Plan 269 measured the same thing.

**3. Only two cadences are declared anywhere.** Parsed from `config.toml` §`qc_rules.rules`
(26 rules): every rule declares either **600 s or 86400 s**, and no other value appears.

| parameter | 600 s | 86400 s |
|---|---|---|
| `discharge` | frozen_sensor, gross_outlier, range_check, rate_of_change, spike | gross_outlier, range_check, rate_of_change, spike |
| `water_level` | frozen_sensor, gross_outlier, range_check, rate_of_change, spike | gross_outlier, range_check, rate_of_change, spike |
| `precipitation` | — | gross_outlier, range_check |
| `temperature` | — | gross_outlier, range_check |
| `water_temperature` | frozen_sensor, gross_outlier, range_check, rate_of_change | — |

⇒ **A group whose inferred median is neither 600 s nor 86400 s selects zero rules, for every
parameter.** An hourly series is the obvious case and it is not hypothetical — it is what Plan
303 exists to declare rules for.

**4. The datum seam cannot empty the set on its own.** `OBS_DATUM_DEPENDENT_RULES =
{range_check, gross_outlier}` (`services/qc_datum.py:14`) and the skip applies only to
`water_level` (`:29-32`). Against the table above, a datum-less water-level station still keeps
`rate_of_change`, `frozen_sensor`, `spike` at 600 s and `rate_of_change`, `spike` at 86400 s.
⇒ **Never zero on the deployed rule set.**

⛔ **Corrected — the first draft blamed Plan 264 for this, and that was wrong.** 264 is
most-specific-wins *per `(rule_id, parameter, cadence)`*: a network rule **replaces the generic
rule of the same id** (`264:95-100`), and *"an override cannot suppress a rule"* (`264:382`). So
network selection does not remove the surviving non-datum rules and cannot thin the set to
datum-only. **What can** is configuration — *"a TOML set omitting the generic rules"*
(`264:266`), which is a property of what someone writes, not of 264's mechanism. ⇒ This is a
config-review concern, and it is not a reason to sequence behind 264.

**5. Onboarding infers ONE cadence for the whole history.** `Stage1QualityChecker.check` groups
by `(station_id, parameter)` and infers a single step per group (`groupby` at `services/qc.py:306`, `infer_time_step` at `:308`), and
Step 5 hands it the entire `start_utc → end_utc` window at once. ⚠️ **So "the window is wide"
cuts both ways.** It is what makes a homogeneous series resolve — and it is also what makes a
*mixed-cadence* history (daily before some date, 10-minute after) collapse to one median that may
match neither. D5's argument assumes homogeneity and does not say so.

**6. Three consumers read this path's output, in the same run, immediately after it.**

| site | what it computes | filter | its own minimum |
|---|---|---|---|
| `onboarding.py:258` | per-station skill scores + diagrams | `qc_status=QC_PASSED` | 🔴 **none** — `sample_size` is recorded (`services/skill/service.py:418`) but nothing gates on it |
| `onboarding.py:855` (Step 5b, `:843`) | climatological baselines | `qc_status=QC_PASSED` | `min_samples = 10` **per day-of-year window** (`services/baselines.py:21,46`) |
| `onboarding.py:902` (Step 5c, `:890`) | flow regimes | `qc_status=QC_PASSED` | `min_observations = 365` (`services/flow_regime.py:28,41`) |

🔴 **This is the whole risk, and it is a loop, not a leak.** Baselines feed `gross_outlier`, which
the *ingest* path then runs (`_apply_gross_outlier`, `services/qc.py:253`, dispatched at `:337`).
So marking onboarding rows `QC_UNCHECKED` shrinks the baseline population, which degrades a live
QC rule on a path this plan does not touch. Plan 269 found the same loop from the threshold side
and called it *"a real limitation, not a rounding error."*

⛔ **Corrected — "the station loses its baseline" was wrong, and the true behaviour is worse in
one direction and better in the other.** `onboarding.py:875-877` stores only `if clim:`, and
`store_baselines` upserts (`store/clim_baseline_store.py:16-42`). So:
- **A shrunken population that still clears `min_samples` writes a NARROWER baseline over the old
  one** — a silent quality change, not an outage. This is the one that matters.
- **A population that clears nothing writes nothing**, so an existing baseline SURVIVES, stale,
  and a first-ever onboarding simply produces none.
- 🔑 **And the upsert is keyed per day-of-year** (`station_id, parameter, day_of_year`), so the
  two mix: the days that still clear `min_samples` are overwritten, the days that no longer do
  keep their old rows. The stored baseline ends up **partly refreshed and partly stale, with
  nothing recording which is which** — a state neither of the two bullets above describes on its
  own, and the reason the hold criterion belongs at the station level rather than per row.

⇒ **Closing the fail-open without deciding the consumer policy in the same change is the way to
degrade the fleet quietly.** That is D2.

**7. Onboarding's Step 5 cannot overwrite an ingest verdict — but the onboarding RUN can.**
Step 5 fetches `RAW` only (`:780-782`), so re-QC never touches a row ingest has judged. ⛔
**Corrected: the first draft generalised that to the whole run, and it does not hold.** Step 3
(`onboarding.py:600-605`) calls `store_raw_observations`, which upserts on the natural key and
**resets `qc_status` to `RAW`, clearing `qc_flags` and `qc_rule_version`, whenever the value or
the rating-curve provenance changed** (`store/observation_store.py:97-117`, Plan 035 Task 2). A
restated row therefore re-enters Step 5 and is re-judged. ⇒ **The blast radius is backfilled
history *plus* any row a restatement touches** — not history alone.

**8. 🔴 The `-norules` sentinel that Plan 272 T0 specified did NOT ship.** T0's In-list requires a
sentinel `qc_rule_version` (`"1.0-norules"` and the datum variants) when the resolved rule set is
empty (`272:735-736`). Measured at `54673d79`: `obs_qc_rule_version`
(`services/qc_datum.py:23-27`) returns `"1.0"`, `"1.1-datum"` or `"1.1-datum-skip"` and has no
zero-rule variant; the flow writes that value unconditionally
(`flows/ingest_observations.py:378,388`). Nothing in `src/` or `scripts/` contains the string
`norules`. **Two consequences, both outside this plan's scope but owed to whoever owns them:**
- With `QC_UNCHECKED` stored, zero-rule rows *are* identifiable by `qc_status`, so the sentinel is
  arguably superseded rather than missing. **Someone has to say which.**
- ✅ **Plan 314's E1(a) rested on it, and has been CORRECTED** (`main` `9fcc9eb4`;
  `docs/plans/314-activating-the-qc-selection-fix.md:98` now says so in terms). Recorded here
  because the sentinel itself is still absent, and E1(a) cannot be used until someone answers
  whether it is superseded by `qc_status` or still owed.
- 🔑 **And the sentinel would not have survived anyway**, which strengthens the point rather than
  weakening it: `store_raw_observations` resets `qc_rule_version` to NULL on any value-changing
  upsert (`store/observation_store.py:104-108`), so a restated value wipes it — 272 records this
  at `:713-715`.

## Owner decisions — all three CLOSED 2026-09-23

### D1 — the trigger. **CLOSED: this plan runs last.**

Owner: *"yes, it runs last."*

⇒ It is scheduled after the plans that change the **shape** of the rule set — **303**
(`precipitation`/`temperature` rule rows, and the cadences they declare), **269** (per-station
thresholds), **313** (`rate_of_change`'s arithmetic) and **264** (the network dimension) — all
merged and deployed.

⚠️ **The reason is that T1's census expires whenever any of them lands**, not that any one of them
creates the defect. § What is measured (4) retracts the first draft's claim that 264 does. 264 is
included because it changes what `rules_for` returns, so the census must be taken against the
final selector — not because it thins the set.

### D2 — the consumer policy. **CLOSED: exclude always; hold BEHIND A FLAG, default permissive.**

Owner: *"yes to consumer action"* — the recommended option. Then, **refined 2026-09-23 on reading
this plan back**: *"that is true for operational deployment. for development and testing however,
we'll need to allow stations that may not pass quality control to go into training and operational
forecasting. if we can't allow that for development, we cannot test the stuff we're doing here."*

🔴 **The refinement is not a change of mind — it caught this plan contradicting a decision already
on record.** `docs/v1-scope.md:68-72` states the staged consumer policy in terms: *"Now, while QC
is being built: unchecked data may flow through, forecasting included. We are developing; a
blocked pipeline teaches us nothing."* Two drafts of this plan wrote the END state as though it
applied on landing.

⛔ **And the plan had collapsed two different things into one, which is what made it bite:**

| | what it does | policy |
|---|---|---|
| **Excluding the rows** | unchecked readings leave the baseline / flow-regime / skill populations | **Always.** Automatic — the store filters by equality; no code does it and no flag can turn it off. |
| **Holding the station** | no promotion, **no model assignment (`:937`), no training (`:1051`)** | **Behind a flag, DEFAULT OFF.** This is the one that would stop development dead. |

**⇒ The hold is gated by a deployment setting that defaults to permissive.** ⭐ *This is not a new
mechanism and not a new default: the hold being reused is ALREADY gated exactly this way —
`require_meteoswiss_backfill: bool = False` (`services/onboarding.py:367`, consulted at `:764`),
and `:757` records that the requirement is deliberately off by default. Two drafts reused the
mechanism and silently dropped its gate.*

- **Flag OFF (default — development, testing, and every deployment until the owner flips it):** the
  shortfall is **measured, logged and counted in the result**, and the station proceeds to
  promotion, model assignment and training exactly as today. The plan still delivers its whole
  measurement; only enforcement waits.
- **Flag ON (operational):** the station is held, with the reason recorded.

⚠️ **A strict default is rejected explicitly**, not merely not-chosen: it would darken stations on
the deploy that lands it — the precise failure Plan 264 T3 produced and that Plan 272 D5's consumer
split exists to prevent.

🔑 **What the flag does NOT gate: the baseline replace-never-merge rule below.** That is a
correctness fix — a stored baseline must not be built from two populations at once — not an
enforcement policy, and it applies in both postures. ⛔ *Gating it would leave the development
deployment, the one we actually run, with the defect this plan exists to remove.*

⚠️ **Two of the three minima already exist and must be used, not invented** — § What is measured
(6): `min_samples = 10` per day-of-year window for baselines, `min_observations = 365` for flow
regimes. ⛔ *The first draft said these "must be made explicit"; they already are.*

🔴 **Skill is the one that has none.** `services/skill/service.py:418` records `sample_size` and
gates on nothing. T2 must state a hold criterion for it — a small design decision inside the
task, not a new open decision.

### D3 — history already stamped `QC_PASSED`. **CLOSED: leave it.**

Owner: *"history is left. we'll onboard for a real deployment for a customer a-fresh. this here is
the test case for development."*

⇒ **No rewrite, and no boundary record either.** The first draft proposed recording the onboarding
run from which the loader stopped failing open; the owner's reasoning removes its purpose. A
mixed-era corpus only ever exists on *this* development deployment, because a customer deployment
is onboarded from scratch — so there is no later reader who needs to tell the eras apart.

⛔ **Two things the first draft got wrong here, corrected so the closure does not preserve them:**
- **The boundary record was not buildable as specified.** `observations` carries no QC timestamp,
  and a run timestamp cannot label a row's era anyway, because a `RAW` row can be checked at any
  later run. Dropping it is right for a second reason.
- **A `qc_status → RAW` reset is NOT a missing operation.** `update_qc`
  (`store/observation_store.py:143-157`) accepts any `QcStatus`, `RAW` included, and Step 3's
  upsert already performs exactly that reset (§ What is measured (7)). What does not exist is a
  re-QC *workflow*. ⛔ *The first draft said the operation itself was absent and used that as the
  argument against re-running QC. The real argument is the owner's: fresh onboarding for real
  deployments makes the question moot.*

## Tasks

### T1 — Measure whether a zero-rule group is still reachable at onboarding

**Outcome.** A count, per station and parameter, of onboarding groups that resolve zero rules,
with the reason for each — evidence about how OFTEN this happens, which may shrink T2's testing
surface.

🔴 **What this task may NOT conclude, stated because an earlier draft did.** *"may descope T2 and
T3 entirely"* was wrong twice over and is withdrawn:
- **A zero count means it does not happen on the corpus measured; it does not mean the fail-open
  is unreachable.** § What is measured (3), (5) and (7) each describe a reachable path — an
  undeclared cadence, a mixed-cadence history, a restated single row re-entering QC — and a census
  that happens to contain none of them refutes none of them. **T2 lands on the argument, not on
  the count.**

**In.**
- A read-only census using `resolve_selection` (`services/qc.py:70-100`), per
  `(station_id, forecast target)`, with the same skip set Step 5 would pass
  (`obs_skipped_rules(parameter, datum)`).
- 🔑 **Over the rows Step 5 WOULD group, not the rows it would fetch today.** ⛔ *An earlier draft
  said "the deployed observation history" and "the same rows Step 5 would pass" in one breath;
  those are different populations and the contradiction would have produced a near-empty result
  that read as "this never happens".* Step 5 fetches `qc_status=RAW` only
  (`services/onboarding.py:780-782`) and the staging corpus is already stamped, so a RAW-only
  census returns almost nothing. The census simulates selection over the station's history
  **regardless of stored status**, because what is being measured is which rules the cadence would
  select — a question the stored status does not enter.
- **Report the inferred cadence alongside the count**, because the reason separates two regimes
  with different fixes: a median outside the declared set is § (3), a group of fewer than two
  distinct timestamps is the cold case.
- **The mixed-cadence probe from § (5), as a test of DECLARED-SET MEMBERSHIP.** ⛔ *A first draft
  compared a whole-window median against a recent-window median and treated any difference as the
  finding — a non-sequitur, since both can be declared values.* The probe is: **the whole-window
  median is outside the declared set while a recent-window median is inside it.**
  ⚠️ **Read the declared set from the rule set in force when the census RUNS — do not hardcode
  `{600, 86400}`.** ⛔ *An earlier draft did, which contradicted D1: this plan is sequenced after
  Plan 303, whose whole purpose is to declare rules at cadences that do not exist today. A
  hardcoded set would classify exactly the groups 303 fixes as unsupported.*
- Run it on the staging host against the live corpus, not on a fixture.

**Out.** ⛔ Any write. ⛔ Any change to `services/onboarding.py`. ⛔ Concluding unreachability
from a zero count.

**Pre-change.** N/A — a measurement.

**Verification.** The census output, recorded in this plan, with the date, the commit, and **the
declared cadence set it read**. ⚠️ **It expires** — per D1, re-run it at the trigger and say so
next to the number.

### T2 — Close the fail-open, and apply D2's consumer policy in the same change

**Outcome.** A group that resolves zero rules at onboarding is stored `QC_UNCHECKED`, the three
consumers exclude it, and a station without enough checked data is held rather than promoted on a
quietly narrowed baseline.

**In.**
- `services/onboarding.py` Step 5 calls `resolve_selection` with the same rows and skip set it
  passes to `check`, and stores `QC_UNCHECKED` for a zero-rule group — the shape
  `flows/ingest_observations.py:358-388` already uses, so this is symmetry with the ingest path,
  not a second mechanism. ⚠️ **Do not re-invent it**: if the two paths need the same logic, the
  shared piece moves to one place rather than being copied. A hand-copied twin is exactly what
  Plan 272 had to delete from `scripts/dhm_precip/qc_mask.py`.
- 🔑 **The exclusion is AUTOMATIC — do not write it.** ⛔ *An earlier draft made "exclude at all
  three sites (`:258`, `:855`, `:902`)" the headline deliverable. It is a no-op:* all three already
  pass `qc_status=QcStatus.QC_PASSED`, and the store filters by **equality**
  (`store/observation_store.py:183-184`), so the moment this task stores `QC_UNCHECKED` those rows
  leave all three populations with no edit at all. **Assert it at each of the three sites; do not
  change them.** ⚠️ A redundant diff here would also hide that the hold below is the task's only
  real deliverable.
- 🔴 **The MEASUREMENT is the unconditional deliverable; the hold is the gated one (D2).** Both
  ship in this task, but they are not the same thing and must not be built as one:
  - **Always:** detect the shortfall, log it, and count it in the onboarding result — in both
    postures, so the development deployment produces the evidence that decides when to flip.
  - **Behind the flag, default OFF:** act on it by holding the station.
- 🔑 **The hold mechanism already exists — reuse it, and reuse its GATE too.**
  `services/onboarding.py:763-766` builds `held_out_ids` under `if require_meteoswiss_backfill:`
  (`:764`, parameter at `:367`, default `False`); Step 8 promotes via
  `update_station_status(... OPERATIONAL)` at `:1198` and `:1214`. ⭐ Ordering confirmed in the
  real control flow, not assumed: Step 5b `:843`, Step 5c `:890`, Step 7 `:1021`, Step 8 `:1177`
  — **every shortfall is computed before promotion**, so it can still block it in the same run.
  ⚠️ **Whether this reuses that same flag or adds a sibling is an implementation choice**, but the
  DEFAULT is not: off. *(The existing flag's name is about MeteoSwiss backfill; a QC-coverage hold
  riding on it would be a misnomer. Prefer a sibling with the same shape and the same default.)*
- ⚠️ **State the mechanism's FULL reach, because "held, not promoted" understates it.**
  `held_out_ids` also skips **model assignment** (`:937`) and **training** (`:1051`); a held
  station is deliberately not trainable (the comment at `:935-936` says so, from Plan 115b2 §2C).
  ⇒ **This is exactly why the flag defaults off.** Withholding a thin-QC station from training is
  right operationally and fatal in development, where a station that never trains is a station the
  pipeline can never be tested on.
- 🔑 **The hold criterion, DECIDED — and it introduces no new number.** ⛔ *Two earlier drafts
  deferred this ("a stated one for skill"), which left the verification uncheckable. Measured
  instead:* **all three consumers already refuse to produce an artifact when their own support is
  insufficient, and all three already record that support.** `compute_clim_baselines` emits only
  day-of-year windows clearing `min_samples = 10` and stores `sample_count` per row
  (`services/baselines.py:21,46,61`); `compute_flow_regime` returns `None` below
  `min_observations = 365` and stores `observation_count` (`services/flow_regime.py:28,41,59`);
  skill stores `sample_size` with its scores (`services/skill/service.py:418`).
  ⇒ **The hold condition is therefore: a consumer produced NOTHING for this station.** Baselines
  empty, or flow regime `None`, or no skill scores. No invented threshold, no new value, and each
  arm is an existing, defined condition.
  ⛔ **Skill needs no new minimum, and this is the reason, not an omission.** There is no
  standards basis to invent one from — `docs/standards/wmo.md:45` maps WMO-1364, which defines
  verification *dimensions and metrics* (CRPS, Brier, rank histograms) and states no sample-size
  threshold. Skill already ships its own `sample_size`, so a thin score is **visible** rather than
  hidden — which is exactly the property the baseline case lacks, and why that one needs a rule
  and this one does not.
- 🔴 **The narrowed-baseline case — the central risk. The outcome is PRESCRIBED: replace, never
  merge.** ⛔ *An earlier draft left it as a menu of three with an undefined trigger ("materially
  smaller"), and stated the trigger and the test differently — so the test could pass with the
  risk live, which is the same failure this plan already folded once.*

  The defect: `onboarding.py:875-877` stores only `if clim:`, and `store_baselines` upserts per
  `(station_id, parameter, day_of_year)` (`store/clim_baseline_store.py:34`), so a shrunken
  population overwrites the day-windows that still clear `min_samples` and **leaves the others
  stale** — one stored baseline built from two populations, with nothing recording which row came
  from which. ⚠️ A per-station minimum cannot see this at all: the shortfall is per day-of-year.

  **The rule, in full, and it needs no threshold:**
  1. When the recomputation produces rows, Step 5b calls
     **`delete_baselines(station_id, parameter)`** (`store/clim_baseline_store.py:44-53`, exists
     today, currently uncalled here) **before** `store_baselines`. The stored baseline then always
     comes from exactly one population.
  2. When it produces **nothing**, the stored baseline is **left untouched** — ⛔ *never
     delete-then-write-nothing, which would destroy a usable baseline and is strictly worse than
     today.*
  ⚠️ **Both arms above are unconditional; neither holds a station.** ⛔ *An earlier draft folded
  "and the station is HELD" into arm 2, which put enforcement inside the rule D2 exempts from the
  flag and quietly reinstated the staged-policy contradiction this plan had just removed.* The
  empty-baseline case **reports** under the default posture and **holds only with the flag ON**,
  exactly like every other shortfall.

  ⭐ **Why this and not the menu:** "materially smaller" needed a number nobody could source; this
  needs none. It behaves identically on a first-ever onboarding (nothing to delete), which the
  menu's trigger could never even fire on — the case D3 says is the normal one for a customer
  deployment. The cost, stated: a worse recomputation replaces a better stored baseline instead of
  silently keeping half of it. That is the right trade — a baseline whose support you can read off
  `sample_count` beats one you cannot characterise.
- The onboarding counters and the returned result gain the unchecked count, as
  `IngestResult.qc_unchecked` did.
- **Delete the refusal comment at `services/onboarding.py:811-817`** in the same change. A comment
  explaining why a gap is deliberate, left standing after the gap is closed, is a lie with a
  citation.

**Out.** ⛔ The scheduled ingest path. ⛔ Re-QC over stored history (D3). ⛔ Any threshold value.
⛔ `services/calculated_station_onboarding.py` — measured: it contains no `check` call and no
`aggregate_qc_status` call at `54673d79`, so it has no fail-open of its own to close.

**Pre-change.** A RED test proving the current behaviour: an onboarding run over a group whose
cadence matches no rule stores `QC_PASSED` today. ⚠️ **It must fail because the status is wrong,
not because a symbol is missing** — the standard Plan 272 § the same standard sets.

**Verification.**
- A zero-rule onboarding group stores `QC_UNCHECKED`; a group that resolves rules and passes them
  still stores `QC_PASSED`. The two are asserted separately.
- Each of the three consumers is asserted at its own site to no longer see the unchecked rows —
  **as an assertion about behaviour, with no edit to those call sites.**
- **With the hold flag OFF (the default): a station whose checked population falls short is
  PROMOTED, assigned a model and trained exactly as today — and the shortfall is still logged and
  counted in the result.** ⛔ *Asserted explicitly and first, because this is the posture every
  deployment runs in until the owner flips it, and because a plan whose only hold test is the
  strict one would ship a permissive path nobody exercised.*
- **With the flag ON: the same station is HELD** with the reason recorded, **and its status is
  unchanged by Step 8** — asserted on the stored status, not only on a log line.
- **The narrowed-baseline case, asserted as the SAME test the In-list prescribes** — ⛔ *an
  earlier draft stated the trigger and the test differently, so both could pass with the risk
  live:* after a recomputation that produces rows, **no stored day-of-year row survives from the
  previous population** (the delete-then-write leaves exactly one population behind); and after a
  recomputation that produces none, **the stored baseline is unchanged**. ⛔ *Both asserted in
  BOTH postures — an earlier draft appended "and the station is HELD" here, which made the
  acceptance test require enforcement the default posture does not perform.* Whether the station
  is then held is the flag's business, asserted separately above.
- **With the flag ON, a held station is also absent from model assignment and training**
  (`:937`, `:1051`) — the hold's full reach, asserted rather than left to be discovered.
  **With it OFF, it is present in both.**
- **A restated row re-enters Step 5 and is judged under the new rule** — § What is measured (7)
  makes this reachable, so it is asserted rather than assumed.

### T3 — Retire the records that still describe the old behaviour

**Outcome.** No document still says onboarding fails open, and the two plans that *argued* the
exclusion record that it was closed.

**In.**
- `docs/v1-scope.md` § QC posture point 4 — the staged policy gains the mechanism that makes it
  real: the "once QC is fine-tuned" half is now **a flag with a default**, not a promise. Name the
  setting there, and say that flipping it is an owner action on a deployment, not a release.
- ⚠️ **A sweep by VALUE, not by site**: every place that states the onboarding **fail-open**,
  including Plan 272 D5 and its § the second fail-open call site. ⛔ **Plan 269's § What this
  deliberately does not do is NOT such a place** — its descope is about `overrides=[]`, which this
  plan does not touch. Add a pointer there that the *fail-open* half is closed; **do not mark its
  own limitation closed.** *(Plan 272's own § Out-of-scope entry for re-QC stays true under D3
  and must not be edited to imply otherwise.)*

⛔ **Two items an earlier draft listed here are ALREADY DONE on `main` (`9fcc9eb4`) and are
removed, not carried:** the `docs/v1-scope.md` blockquote repair (the quote now reads whole at
`:37-40`, with the warning after it at `:42-48`) and the Plan 314 E1(a) correction
(`docs/plans/314-activating-the-qc-selection-fix.md:98`). *An implementer sent to fix a defect
that no longer exists either edits a correct passage or stalls.*

**Out.** ⛔ Any code change. ⛔ A boundary record of any kind (D3). ⛔ The two repairs above.

**Pre-change.** N/A.

**Verification.** A grep for the exclusion's wording returns only the records that say it was
closed.

## Explicitly out of scope

- **The scheduled ingest path** — Plan 272, shipped in PR #297. This plan makes onboarding match
  it; it does not revisit it.
- **The network dimension of selection** (264), **per-station thresholds** (269),
  **`precipitation`/`temperature` rule rows** (303) and **`rate_of_change`'s arithmetic** (313).
  This plan runs after them by D1 and inherits whatever shape they leave.
- **The rollout controls** — Plan 314, suspended. ✅ Its E1(a) sentence was falsified by § What is
  measured (8) and has since been corrected on `main`; the missing sentinel itself is still open
  and unowned.
- **A re-QC workflow over stored history** — closed by D3. ⚠️ *Not* because the reset operation is
  missing: it exists (D3). Because real deployments onboard afresh.
- **The six DHM gauges' onboarding** — they have no forecast target, so Step 5 skips them
  entirely (§ What is measured (2)). ⛔ **And giving them one is NOT Plan 268's**, which an earlier
  draft asserted: 268 states *"`forecast_targets` stays unset"* (`268:852`), *"no `station_status`
  promotion — these stay `onboarding`"* and *"Promotion … is not this plan's to trigger"*
  (`268:862-872`), and tests that condition. ⇒ **Nobody owns setting them.** Recorded here as an
  open gap rather than assigned away, because this plan's § (2) depends on it: until something
  does, Step 5 never runs on the DHM six at all and nothing in this plan reaches them.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1"] },
    { "id": "phase-2", "tasks": ["T2"], "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T3"], "depends_on": ["phase-2"] }
  ]
}
```
