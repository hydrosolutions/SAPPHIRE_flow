---
status: DRAFT
created: 2026-09-05
plan: 254
title: Phase-aware execution — the resampler call sites, the fetch-bound helpers, the daily-model anchoring, and the Swiss rollout
scope: Make every assembly path honour a declared `TimeGrid` rather than assuming phase zero, carry resampling provenance so degradation is reportable, record each artifact's training grid so a mismatch fails closed, sequence the Swiss retrain and cutover, and anchor the daily models (T8, ABSORBED from Plan 226 on 2026-09-08 — 226 is SUPERSEDED). Explicitly NOT the conventions or types (Plan 252), NOT temporal support / CF `cell_methods` (Plan 258), NOT Plan 234's aggregation declaration, NOT the Forecast Lab v3 format (Plan 251).
depends_on: [252, 234, 258]
blocks: []
source: 2026-09-05 — split from Plan 252 after a review returned 20 blockers, most of them integration and contract failures rather than defects in the time-zone reasoning
---

# Plan 254 — phase-aware execution

## Status

**DRAFT — not reviewed, and deliberately not yet fully scoped.** Several tasks below name a decision
that must be taken before they can be written as contracts. This plan exists so that Plan 252's
conventions have a named destination and the review's structural blockers are not lost.

## Why this is separate from Plan 252

Plan 252 declares what a time grid is and what conventions bind it. It changes almost no behaviour.
This plan changes behaviour on **every** assembly path, retrains the Swiss artifacts, and cuts over a
live deployment. Reviewing those together produced 20 blockers, most of which were about this half.

**Nothing here may land before Plan 252**, and OD-9's parity precondition binds: a fixed non-zero
phase preserves Plan 228 D4's invariant **only once training, operational assembly, scoring, NWP
handling, fetch bounds and artifacts all use the same declared grid**. Until then, phase zero remains
correct, and a partial rollout is worse than none.

## What the review established

**The resampler has TWELVE production call sites.** Plan 252's draft said three and called T4 "a
parameter change"; this plan's review said seven. Both are now wrong, and the count is *moving*:

| when | sites | what changed |
|---|---|---|
| 2026-08-26 | 6 | baseline |
| 2026-09-03 | 8 | Plan 228 landed |
| **2026-09-08** | **12** | **Plan 239 T1a (`57aac024`, PR #263) — five new sites in one morning** |

Re-inventoried against `origin/main` 2026-09-08 (⛔ re-run this before writing T4; it has moved twice
in a fortnight):

| Call site | |
|---|---|
| `services/training_data.py:553`, `:567`, `:607` | training (three) — ⚠️ drifted TWICE today as main moved; cite the enclosing function and re-measure before use |
| `services/operational_inputs.py:165`, `:591`, `:707` | operational assembly (three) |
| `services/hindcast.py:238`, `:302`, `:320` | hindcast (three) |
| `services/track_assembly.py:282`, `:372` | track assembly (two) |
| `services/skill/service.py:293` | skill |

🔴 **Line numbers move; the inventory is not a fact to cite but a measurement to retake.**
Re-measured 2026-09-09 against `origin/main` `071b62e3`: the **count of twelve is CONFIRMED**, and so
are nine of the twelve locations — but the three `training_data.py` sites moved again, and every
helper line number this plan cited was stale (corrected in T2/T3 below).

⚠️ **"Plan 239 is now halted" no longer describes the baseline.** Plan 239 T1b merged as PR #268 on
2026-09-09 and is in `origin/main`, which is what shifted the training call sites and helpers. Re-run
the inventory against the current tip before writing T4, not against this table.

**And the resampler is not the whole of it.** `floor_to_time_step` (`training_data.py:244`) and
`aligned_lookback_bounds` (`:261`) are separately phase-zero. Changing only `group_by_dynamic`'s
`offset` would lose Plan 228 D4's exactly-N-complete-buckets guarantee — the thing D4 exists to
protect. Polars also labels bucket **starts** by default (the call is at `training_data.py:374`) while skill's
completeness path assumes start labels (`skill/service.py:305`), so period-ending labelling is a
behavioural change to both.

**The degradation channel does not reach.** Plan 252 assumed Plan 253's `InputQualityFlag` could
carry resampling provenance. It cannot as it stands: `assess_input_quality` emits only observation
staleness, NWP age and warm-up flags; only `OperationalForecast` persists the pair;
`HindcastForecast` has no such fields and Plan 253 explicitly excluded hindcasts; and the resampler
returns a bare DataFrame with nowhere to put provenance.

**Artifacts record no training grid** (`db/metadata.py:907`), so nothing can fail closed against a
phase-mismatched artifact — which is precisely the silent substitution Plan 252 OD-7 says a model
cannot detect.

**Downstream UTC-day assumptions were found in at least four more places**: Forecast Lab
(`services/forecast_lab/snapshot.py:551`, `:856`), NWP antecedent-window validation
(`models/nwp_regression.py:598`), and the issue-time filter whose meaning changes under
period-ending labels (`services/operational_inputs.py:225`).

## Open decisions — needed before the tasks below can be written as contracts

- ✅ **D1 — ANSWERED 2026-09-09: SAP3 preprocessing. No contract change, no upstream issue.**
  The model states its interval; **we** guarantee the data sits on the declared grid before
  `predict()`. Models are deliberately timezone-agnostic (Plan 252 OD-7) and cannot know a
  deployment's day boundary, so declaring a phase would ask them to know a deployment-specific fact.
  A mismatch is caught on our side by T5, which records each artifact's training grid and refuses a
  differing one. The FI adapter stays as it is.

  *(Original framing:)* is phase part of the ForecastInterface contract, or SAP3 preprocessing
  provenance? The FI
  adapter selects requirements solely by `timedelta` (`adapters/forecast_interface.py:496`, `:1310`).
  If a model must be able to *declare* a phase, that is an FI change and
  `CLAUDE.md` § ForecastInterface Adherence requires an upstream issue, **not** a SAP3-side
  workaround. If phase is purely our preprocessing concern, the adapter stays as it is and we
  guarantee the grid before `predict()`. **This decision gates T4.** ⚠️ It does NOT gate T1 — T1 is
  the task that TAKES it. An earlier revision said D1 gates T1, which is circular and contradicted
  the dependency graph, where T1 has no dependencies.
- **D2 — what carries resampling provenance?** A typed resampling result (data plus quality) is the
  obvious shape, but training, hindcast and skill cannot reuse operational persistence without new
  contracts. Decide per consumer whether degradation is persisted, logged, or gates the run.
- ✅ **D3 — ANSWERED 2026-09-09: the no-imputation contract STANDS.** `docs/touchpoint-maps.md:247-248`
  wins; Plan 252's interpolation and apportionment rows are withdrawn (now Plan 252 **OD-13**). We
  never invent a value. Where readings do not fit a grid we combine onto a coarser one, and the bucket
  EDGE moves to the nearest reading while the readings themselves never move (Plan 252 **OD-14**),
  bounded by a configurable limit beyond which the bucket is refused. **T3 now has authoritative
  behaviour**, and it is simpler than the original design: there is no interpolation path to build.
- ✅ **D4 — ANSWERED 2026-09-09: move Switzerland, bundled with the retrain already owed.** The
  boundary move rides the retrain required to clear the live train/serve skew, so it costs one retrain
  rather than two, and it proves the declared-boundary mechanism on a deployment we control before
  Nepal depends on it. Switzerland stays at phase 0 until that retrain is ready.
  ⭐ **Plan 262's end-period-stamping change rides the SAME cutover** — identical shape (it changes
  what a stored interval value means, invalidates every artifact, needs a coordinated switch). Three
  migrations collapse into one. ⚠️ **The atomicity question is now D7, not D4** — an earlier revision used D4 for two different
  decisions, so the second had no name and no owner.

- **D7 — atomic flip or per-station migration?** Open. The group-phase invariant (Plan 252 OD-12)
  makes a mixed-phase interval hazardous for any cross-station product, which points hard at atomic;
  per-station would need a per-station grid record and a period during which two cuts coexist.
  **Gates T6.**
- ✅ **D5 — ANSWERED 2026-09-09: end-period stamping is the house convention, and Plan 262 owns
  adopting it.** Adapters convert at ingest; our own bucket labelling changes to match, in one step
  with skill's completeness path. ⛔ **This plan must not change either independently** — Plan 262
  sequences them and rides T6's cutover.

  ⚙️ **The read-side change is T2's, and it is now specified rather than left as a choice.** ⛔ The
  earlier text cited `ObservationStore` as the example — **wrong**: observations are instantaneous and
  Plan 262 leaves them untouched. The stores that actually carry interval values and are half-open,
  measured 2026-09-09:
  `historical_forcing_store.py:71-72` and `weather_forecast_store.py:79-80`, both
  `valid_time >= start AND valid_time < end`.
  **Decision: change the BOUNDS for interval-valued series to `(start, end]`**, not the resampler's
  `closed` behaviour — the bounds are what decide which rows are fetched at all, and a resampler
  cannot recover a row the query never returned. Instantaneous series keep `[start, end)`.

  *(The measurement that produced it:)* how does a half-open fetch window meet closing-boundary
  stamps? Plan 258 defines
  interval-valued data as stamped on its CLOSING boundary, while `fetch_observations` filters
  `timestamp >= start AND timestamp < end` (`store/observation_store.py:173-174`, verified
  2026-09-09). A window meant to cover N complete intervals therefore excludes the stamp of its last
  one. Either the fetch bounds become `(start, end]` for interval-valued series, or T2/T3 specify the
  equivalent `closed`/`label` behaviour. **This gates T2 and T3**, and it is a live off-by-one-interval
  risk at every window edge, not a theoretical one.
- ⛔ **NOT a decision here: whether a model OUTPUT declares its temporal support.** T8 needs the
  answer — it cannot decide whether a daily forecast bucket is stamped on its closing boundary
  without it — but the question belongs to Plan 258, which owns temporal support, and is recorded
  there as **258 D5**. This plan CONSUMES that answer and must not take it. *(Stated once, here, after
  a 2026-09-09 pass briefly recorded it in both plans — the duplication this reconciliation exists to
  remove.)*

## Non-goals

- Plan 252's grid conventions and types, and **Plan 258's temporal support / CF ingest** — this plan
  CONSUMES both and now declares 258 as a dependency; it must not proceed past T3 without the
  temporal-support contract T3 reads.
- Plan 234's aggregation declaration — this plan **consumes** it.
  ⛔ **Plan 226 is NOT consumed: it is SUPERSEDED and its scope was ABSORBED into T8** (2026-09-08).
  It must not be cited as a live plan anywhere. An earlier revision said "consumes", contradicting
  this plan's own frontmatter and T8.
- The Forecast Lab v3 format change, which Plan 251 already owns.
  ⛔ **Interval bounds (`period_start`/`period_end`) are an ORPHAN, not Plan 251's.** Verified
  2026-09-09: Plan 252 assigns them to Plan 258, this plan previously assigned them to Plan 251,
  Plan 258's task ledger (T0–T4) contains no bounds task, and Plan 251 contains no `period_start`,
  `period_end` or interval-bound work at all. ✅ **CLOSED 2026-09-09: Plan 258 T5 owns them**, as a
  real task. ⛔ Do not describe them as orphaned.
- Re-opening Plan 228 D1-D3 or its shipped P1/P2 fix.

## Tasks

Every code task carries the Task Exit Gate (`docs/workflow.md:198-210`).

### T1 — settle the four open decisions

**Outcome:** every open decision in this plan is answered with rationale — **D2 and D7**, plus
**the six anchoring questions absorbed with Plan 226**, which T8 says moved "verbatim into T1's
decision set" and which an earlier revision never actually put here.

⛔ **The six, carried in explicitly** (they are T8's prerequisites and had no owner): whether P1 is
fixed in the hindcast path or in the model; whether the daily-vs-instantaneous comparison is
acceptable; what step *k* predicts and how the anchor is computed (by truncation, never by reading a
`past_targets` row); what happens when every step is backdated; whether the boundary is `<` or `<=`
(it must agree with the NWP path's existing convention at
`services/operational_inputs.py:225-236`); and how "the last observation" is defined for a
multi-parameter fallback.

D1, D3, D4 and D5 are already answered above.

**In:** this plan; the FI protocol and `adapters/forecast_interface.py`; `docs/touchpoint-maps.md:228`.

**Out:** any code change. The decisions exist so they are made once, in the open, rather than inside
an implementation diff.

**Pre-change:** N/A — decision task. **D2 and D7, plus the six absorbed anchoring questions**, are
recorded as open above; D1, D3, D4 and D5 are answered. ⛔ An earlier revision said "the four
decisions" and called D3 a live contradiction — D3 was answered on 2026-09-09 (the no-imputation rule
stands), and it cited `touchpoint-maps.md:228`, the wrong line: the rule is at `:247-248`.

**Verification:** N/A — decision task. Each answer cites the code or contract it rests on. ⛔ D1 is
already answered (SAP3 preprocessing; no FI issue needed) — this task does not re-take it.

### T2 — make the fetch-bound helpers grid-aware

**Outcome:** `floor_to_time_step` and `aligned_lookback_bounds` take a `TimeGrid` and preserve
exactly-N-complete-buckets at a non-zero phase.

**In:** `floor_to_time_step` (`services/training_data.py:244`), `aligned_lookback_bounds` (`:261`),
**and EVERY interval-valued read bound.** Re-measured 2026-09-10 — there are **four**, not two:
`historical_forcing_store.py:71-72`, `:177-178`, `:201-202`, and `weather_forecast_store.py:79-80`.
All move to `(start, end]` per D5; instantaneous reads stay `[start, end)`
(`observation_store.py:173-174`, `:218-219`). ⛔ An earlier revision inventoried only two of the four —
**re-run the grep rather than trusting this list**:
`grep -rn "valid_time >= start" src/sapphire_flow/store/`. Depends on T1.

**Out:** the resampler itself (T3); any call-site change (T4).

**Pre-change:** `uv run pytest tests/unit/services/test_training_data.py -k bounds` against a non-zero phase yields buckets aligned to epoch, not to the declared grid, so the window has a partial bucket at both ends — the failure Plan 228 D4 documents at `228:139`.

**Verification:** `uv run pytest tests/unit/services/test_training_data.py` — for a declared phase of 64800 s, the fetch bounds yield exactly N complete buckets with no partial bucket at either end, and phase zero reproduces today's behaviour byte for byte.

### T3 — make the resampler phase-aware and provenance-carrying

**Outcome:** the resampler honours a declared `TimeGrid`, **invents nothing**, refuses upsampling, and
returns provenance alongside the data.

🔴 **Rewritten 2026-09-09 — this task previously required the two operations Plan 252 OD-13 now
FORBIDS.** T3 is the only task that touches the resampler, so it carries all of OD-13 and OD-14; if it
does not, nothing does. The contract:

| Rule | Source |
|---|---|
| ⛔ **No interpolation** between readings, ever | 252 OD-13 |
| ⛔ **No apportionment** of a total across a boundary, ever | 252 OD-13 |
| Coarsen with the parameter's declared `AggregationMethod` | 252 OD-6 (surviving half) |
| **Refuse** a target finer than the source's median spacing | 252 OD-6 (surviving half) |
| A bucket runs from the reading **closest to its nominal start** to the reading closest to its nominal end — readings never move | 252 OD-14 |
| A **configurable limit** bounds how far a chosen edge may sit from nominal; beyond it the bucket is **REFUSED**, not built | 252 OD-14 |
| **Ties** (two readings equidistant from a boundary) resolve deterministically — take the EARLIER, and lock it by test | this task |
| **How far the chosen edge actually sat** is recorded on the value | 252 OD-14 |

⚠️ Temporal support (Plan 258) is still consumed — it says whether a value is a moment or a span, which
determines which bucket it falls in. It no longer selects an interpolation or apportionment METHOD,
because neither exists any more.

**In:** `resample_to_time_step` (`services/training_data.py:286`) and its `group_by_dynamic` call
(`:374`), which supplies only `every=`; installed polars 1.43.2 defaults to `closed='left'`,
`label='left'`, which skill's completeness path assumes (`services/skill/service.py:305`), so both
change together. ⚠️ **Period-ending labelling itself is Plan 262 T3, not this task** — T3 makes the
resampler grid- and edge-aware; 262 changes which end it labels. They touch the same function and
must be sequenced, not merged. The return type changes per D2. Depends on T1, T2.

🔑 **Cite the SYMBOL, not the line.** These numbers were correct on `dc442d57`, wrong after rebasing
onto `071b62e3`, and re-measured on 2026-09-09. They will drift again.

**Out:** call sites (T4). Changing `AggregationMethod`.

**Pre-change:** `grep -n "group_by_dynamic" services/training_data.py` shows `every=` with no `offset`, `closed` or `label`, so a declared-phase target is silently re-bucketed onto epoch marks — a shift presented as a resample.

**Verification:** `uv run pytest tests/unit/services/test_training_data.py` — a 15-minute source on
quarter-hour marks maps onto both a UTC-hourly and a Nepali-hourly target with **zero** apportionment;
readings at `00:03`/`00:13`/`00:23` aggregate into a 3-hourly bucket whose edges are the readings
nearest the nominal boundaries, with **every reading's own timestamp unchanged**; a boundary whose
nearest reading exceeds the configured limit produces **NO value** and says why; two equidistant
readings resolve to the earlier one; an upsample is **REFUSED**; and ⛔ **a test asserts that NO output
value is absent from the input** — the lock that proves nothing was invented.

### T4 — thread the declared grid through EVERY resampler call site (re-inventory first — 12 as of 2026-09-08, and it has moved twice in a fortnight)

**Outcome:** every assembly path resolves its target grid from the deployment declaration rather than
assuming phase zero, and the downstream UTC-day assumptions are corrected.

**In (also):** the group-phase invariant Plan 252 OD-12 assigns to this task.
`_assert_consistent_station_inputs` (`services/run_group_forecast.py:99-112`) already asserts that a
group's stations share `issue_time`, `forecast_horizon_steps` and `time_step` — **extend it to the
phase.** Without that, two stations on different phases stack into one timestamp column
(`_stack_station_frames`, `:89-92`) and the group artifact trains on two interleaved grids. ⚠️ An earlier revision of Plan 252
said this was "listed in T4's scope" when it was not; it is now.

**In:** the **twelve** call sites listed above, plus the four downstream assumptions:
`services/forecast_lab/snapshot.py:551`, `:856`; `models/nwp_regression.py:598`; and the issue-time
filter at `services/operational_inputs.py:225` whose meaning changes under period-ending labels.
Depends on T3.

**Out:** the Forecast Lab format change (Plan 251). The Swiss cutover (T6).

**Pre-change:** each call site passes a bare `time_step`; `grep -rn "resample_to_time_step(" src/` shows **twelve** invocation sites (verified 2026-09-09) and no grid is threaded to any of them.

**Verification:** `uv run pytest` — no call site constructs a grid implicitly, a phase-zero deployment is unchanged end to end, and the NWP antecedent window and issue-time filter are tested under a non-zero phase.

### T5 — record each artifact's training grid and fail closed on mismatch

**Outcome:** an artifact carries the `TimeGrid` it was trained on, and activating or predicting with a
mismatched grid is refused rather than silently wrong.

**In:** `db/metadata.py:907` (model artifacts) plus an additive migration, the artifact import path,
and the activation and prediction gates. Depends on T3.

**Out:** retraining anything (T6).

**Pre-change:** `grep -n "time_grid\\|grid_phase" src/sapphire_flow/db/metadata.py` returns nothing for artifacts, so an artifact trained on midnight days can be activated against an 18:00Z deployment with nothing detecting it — Plan 252 OD-7's silent substitution.

**Verification:** `uv run pytest tests/unit/services/test_model_registry.py tests/integration/db/` — an artifact whose recorded grid differs from the deployment's is refused at activation with a typed error, and the refusal is locked by a test.

### T6 — sequence the Swiss retrain and cutover (THREE corrections ride it, not one)

⭐ **Scope widened 2026-09-09 by owner decision.** One retrain, carrying everything that changes what
a stored daily value means:

| # | Correction | Owner |
|---|---|---|
| 1 | Swiss day boundary moves from phase 0 to 23:00Z | this task |
| 2 | End-period stamping adopted; our bucket labelling changes to match | **Plan 262** |
| 3 | **MeteoSwiss precipitation is a 06:00→06:00 day, not midnight→midnight** — measured from the provider's own grid-product documentation; our temperature is midnight→midnight, so our two inputs disagree by six hours | **Plan 252** declares it; corrected here |

⛔ **All three invalidate every Swiss artifact, so they must land together.** Doing them separately
means three retrains and three cutovers. ⚠️ **Correction 3 is not yet decided, only measured** — Plan
252 **OQ-6** asks how two differently-phased sources feed one model, and its answer changes what this
retrain bakes in. **Do not start T6 before OQ-6 is settled.**

**Outcome:** Switzerland moves from phase 0 to 23:00Z with artifacts, hindcasts, skill generations and
configuration moving together, and a rollback.

⛔ **This outcome is not writable until D7 is answered.** D7 (atomic flip vs per-station migration) is
still open, and "moving together" means different things under each: an atomic flip needs one
coordinated switch with a single rollback point; a per-station migration needs a per-station grid
record and a mixed-phase interval during which two cuts coexist. Do not draft the sequence before D7.
⚠️ **An earlier revision called this D4**, which names a different and already-answered decision.

⛔ **Blocked on three things the graph now names:** Plan 252 **T10** (which boundary Switzerland
actually adopts — OQ-6), Plan 262 **T3** (the end-stamping change rides this cutover, so its code must
land first), and **D7** (atomic versus per-station). None of these were declared as blockers before
2026-09-09.

⚠️ **Correction 3 needs an ADAPTER change and this sequence did not contain one.** The MeteoSwiss
06:00 precipitation day is corrected where the data is read, not in the rollout: the adapter must
record each product's native boundary and the assembly must stop treating them as identical. That work
belongs to **Plan 252 T6** (the audit that establishes it) and to whichever task acts on OQ-6's answer
— it is named here so the cutover does not silently assume someone else did it.

**In:** the rollout sequence — persist training grids (T5), retrain, rerun phase-correct hindcasts,
publish a new skill generation via Plan 235's mechanism, promote, flip configuration. Coordinates with
**T8** (anchoring, absorbed from the superseded Plan 226) and Plan 235 (generations); 235's text and
dependencies need amending, since its post-plan action is Plan 228's recompute (`235:435-436`).
⚠️ Plan 226 needs no amendment — it is superseded. Depends on T4, T5 **and T8** (matching the
dependency graph, which an earlier revision's prose omitted).

**Out:** Nepal, which has no artifacts to retrain and no cutover.

**Pre-change:** N/A — deployment sequencing task. No plan currently owns this: `226:216` excludes recomputation and `235:355` points elsewhere, so the Swiss retrain has no home until this task creates one.

**Verification:** N/A — deployment task. The sequence is written with a rollback, and the atomicity requirement (config and artifacts move together) is stated as a gate rather than a hope.

### T8 — anchor the daily models to the calendar day they predict (ABSORBED from Plan 226)

**Outcome:** a daily model's `valid_time` labels the calendar day it actually predicts, on the
deployment's declared boundary, instead of the wall-clock instant the cycle happened to start.

📌 **Absorbed by owner decision 2026-09-08.** Plan 226 is superseded and its scope moves here intact.
Two reasons: 254's cutover (T6) cannot work without this — moving the declared boundary changes a
setting the daily models do not read, because they build timestamps relative to the cycle start
(`models/linear_regression_daily.py:164-166`) — and 226 was halted onto this track anyway, so
carrying it as a second halted plan that must land in lockstep was strictly harder than carrying it
as a task.

🔴 **This is live and continuous on staging, measured 2026-09-08.** Each forecast cycle emits exactly
one valid_time phase, and that phase is the cycle's start time: 09-07 00:00Z→`8 s`,
06:00Z→`21602 s`, 12:00Z→`43202 s`, 18:00Z→`64802 s`, 09-08 00:00Z→`3 s`, 06:00Z→`21605 s`,
07:24Z→`26658 s`. `nwp_rainfall_runoff` sits at `0` throughout — the defect is specific to the daily
models. It is unaffected by Plan 239 T1a, which changed inputs, not labelling.

⛔ **Plan 226's BINDING Proportionality constraint travels with it.** A finding that GROWS this task
is worse than one that shrinks it. Absorbing 226 must not be read as licence to expand it: the scope
is anchoring, and nothing else 226 excluded (pooling semantics, member-id collision, the alert-path
union, BMA, backfill, recomputation, performance work) becomes in-scope by moving house.

⛔ **226's six open design questions are NOT answered by absorption and must not be answered
casually.** They are carried verbatim into T1's decision set: whether P1 is fixed in the hindcast
path or in the model; whether the daily-vs-instantaneous comparison is acceptable; what step *k*
predicts and how the anchor is computed (by truncation, never by reading a `past_targets` row);
what happens when every step is backdated; whether the boundary is `<` or `<=` (it must agree with
the NWP path's existing convention at `services/operational_inputs.py:225-236`); and how "the last
observation" is defined for a multi-parameter fallback. 226's T-M measurements are **done and valid**
— 604 s cadence, the ~70-minute hindcast lookback, the 6.4 % median skill-join error, zero
observation staleness — and must not be re-measured.

⚠️ **Consequence carried forward:** while this was halted, the map consumer re-based its national
danger classification onto the primary individual model, because 34 stations have no combined
forecast. This task is what restores the combined forecast Plan 222 takes dark; that re-basing
stands until it lands.

**In:** the valid_time construction and anchor computation of **all three** clock-anchored models.

🔴 **T8's scope was incomplete, corrected 2026-09-09.** An earlier revision named only
`models/linear_regression_daily.py:165`. The identical `issue_time + (step + 1) * time_step`
construction is also in `models/persistence_fallback.py:92` and
`models/climatology_fallback.py:145` — and **this plan's own census proves they dominate it**:
`climatology_fallback` contributed 2637 of the 4182 off-phase rows and `persistence_fallback` 961,
against `linear_regression_daily`'s 515. Fixing only the daily model would leave 86% of the measured
defect in place. `nwp_regression` is NOT affected — it uses the delivered `future_times`
(`models/nwp_regression.py:469-473`), which is why it sits at phase 0 throughout. **Out:** everything
226 listed as a non-goal; the boundary VALUE itself (that is 252's declaration); the retrain (T6).
Depends on T1, because five of the six questions above are its decisions.

**Pre-change:** the per-cycle phase measurement above — a daily forecast issued at 07:24Z labels its
first step at 07:24:18, not at the declared boundary. A red-first test must fail on the *label*, not
on a signature.

**Verification:** `uv run pytest tests/unit/models/ tests/unit/services/test_run_station_forecast.py`
— a daily forecast issued at an arbitrary wall-clock instant labels its steps on the declared
boundary. ⛔ **A test asserting only "all timestamps are midnight" locks nothing** (226's own
warning): it passes for an issue-day-anchored forecast too. Assert which day each step names.

### T7 — record the phase on `forecasts`, not just the step

**Outcome:** a stored forecast records the whole grid it sits on, so `forecasts` stops asserting half
of one. Assigned here 2026-09-08 by the time-grid track owner; Plan 252 states the convention (a step
without a phase is not a grid) and Plan 248 owns the pre-existing rows — neither owns the column.

**Why it belongs in this plan and not in 248:** 248 tightens the column that exists and has decided
to DISCARD the rows that predate it (not quarantine — see T7's Out below). Writing a *correct* phase requires the declared
grid that T4 threads through the call sites; inferring it back from the timestamps at write time is
precisely the read-time inference Plan 252's corollary forbids. So the column cannot be written
honestly before T4, which puts it here.

🔴 **This is live, not anticipated.** Measured on staging 2026-09-08 after `0.1.889`: of the 377
`forecasts` rows the system has populated `time_step_seconds` on, all store `86400` and **209 (55%)
sit on a non-zero clock-derived phase** — 139 `climatology_fallback` rows at 26658 s (07:24:18 UTC)
among them. Every one of those rows currently reads as "daily grid" and none of them says where the
day starts.

**In:** `db/metadata.py` (`forecasts.phase_offset_seconds`, nullable, additive migration)
**and `hindcast_forecasts.phase_offset_seconds` on the same terms**,
`store/forecast_store.py` write and read paths, `types/forecast.py`.

⚠️ **`hindcast_forecasts` added 2026-09-09** (Plan 252 OQ-5). It stores `time_step_seconds` `NOT NULL`
with a positive check and no phase (`db/metadata.py:1281-1294`) — the same half-grid, and it was
unowned by every plan in this family. It matters more than `forecasts`: hindcasts are what skill is
computed from, and skill is the one path that already partitions by phase, so a phase-blind hindcast
store feeds a phase-aware scorer. Mirror the existing precedent
exactly — `skill_scores.phase_offset_seconds` / `skill_diagrams.phase_offset_seconds`
(`db/metadata.py:1540`, `:1611`; written at `store/skill_store.py:520`) — including its nullability.

⚠️ **Correct reason for nullability.** An earlier draft of this task justified it as "an ensemble
with no `valid_time` stays representable" — that is false: `ForecastEnsemble.from_members` rejects an
empty frame (`types/ensemble.py:54-63`), so such an ensemble cannot exist. The real reason is
historical: rows written before this column exists have an unrecorded phase, and NULL is how an
unknown is represented. Depends on T4.

**Out:** backfilling historical rows. ⚠️ **Plan 248 T2 decided DISCARD, not quarantine** — corrected
2026-09-09 against `248:584-590`, `:637-640`; the quarantine design was superseded the same day it was
written, because a CHECK constraint is re-evaluated on UPDATE and would have frozen those rows. With
no NULL rows left, 248 T3 uses a plain `SET NOT NULL`, so there is no `NOT VALID` constraint to
`VALIDATE`. Also out: any change to `time_step_seconds` itself.

**Pre-change:** insert a forecast whose `valid_time`s sit at a non-zero offset, read it back, and show
that nothing distinguishes it from a midnight-anchored one. The red-first test must fail because the
phase is *absent*, not because a signature changed.

**Verification:** `uv run pytest tests/integration/store/test_forecast_store.py tests/unit/store/` —
a forecast written on a non-zero-phase grid reads back carrying that phase by value, not by
dataclass default; a NULL stays NULL rather than defaulting to 0. ⛔ **0 must never be the default
for an unknown phase** — that is the same false-`FULL` failure Plan 253 had to undo on
`input_quality`, and it is worse here because 0 is a legitimate value.

## Exit gates

```bash
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright src/
uv run pytest
uv run python scripts/check_readiness.py docs/plans/254-phase-aware-execution.md
```

Five conditions hold in addition:

1. **A phase-zero deployment is byte-for-byte unchanged.** This plan must be a no-op for Switzerland
   until T6 deliberately moves it.
2. **OD-9's parity precondition is satisfied before any non-zero phase goes live** — training,
   assembly, scoring, NWP, fetch bounds and artifacts on one declared grid, or none of them.
3. **No silent upsampling.** The refusal is locked by a test, not only the success path.
4. **A grid-mismatched artifact is refused**, not used.
5. **Plan 252 has landed**, and Plan 234's declaration is available to consume.
6. **A forecast stored on a non-zero-phase grid reads back carrying that phase**, and an unknown
   phase reads back NULL — never 0.

## Dependency graph

```json
{
  "nodes": [
    {"id": "T1", "phase": 1, "depends_on": []},
    {"id": "T2", "phase": 2, "depends_on": ["T1"]},
    {"id": "T3", "phase": 2, "depends_on": ["T1", "T2"], "note": "Plan 262 T3 edits the SAME function (labelling); 254 T3 lands first, then 262 T3"},
    {"id": "T4", "phase": 3, "depends_on": ["T3"]},
    {"id": "T5", "phase": 3, "depends_on": ["T3"]},
    {"id": "T8", "phase": 2, "depends_on": ["T1"]},
    {"id": "T6", "phase": 5, "depends_on": ["T4", "T5", "T7", "T8"], "blocked_on": "Plan 252 T10 (settles OQ-6, which boundary Switzerland adopts); Plan 262 T3 (end-stamping lands in the same cutover); D7 (atomic vs per-station)"},
    {"id": "T7", "phase": 4, "depends_on": ["T4"], "note": "must land BEFORE T6 — rebuilt hindcasts would otherwise be written without a phase"}
  ]
}
```
