---
status: DRAFT
created: 2026-09-05
plan: 254
title: Phase-aware execution — seven call sites, the fetch-bound helpers, and the Swiss rollout
scope: Make every assembly path honour a declared `TimeGrid` rather than assuming phase zero, carry resampling provenance so degradation is reportable, record each artifact's training grid so a mismatch fails closed, and sequence the Swiss retrain and cutover. Explicitly NOT the conventions or types (Plan 252), NOT Plan 226's anchoring, NOT Plan 234's aggregation declaration, NOT the Forecast Lab v3 format (Plan 251).
depends_on: [252, 234]
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
| `services/training_data.py:546`, `:560`, `:600` | training (three) |
| `services/operational_inputs.py:165`, `:591`, `:707` | operational assembly (three) |
| `services/hindcast.py:238`, `:302`, `:320` | hindcast (three) |
| `services/track_assembly.py:282`, `:372` | track assembly (two) |
| `services/skill/service.py:293` | skill |

🔴 **Every line number in the previous table was also stale**, because Plan 239 T1a edited all four
assemblers. The inventory is not a fact to cite; it is a measurement to retake. **Plan 239 is now
halted** (owner decision, 2026-09-08) partly so this list stops moving underneath this plan.

**And the resampler is not the whole of it.** `floor_to_time_step` (`training_data.py:183`) and
`aligned_lookback_bounds` (`:200`) are separately phase-zero. Changing only `group_by_dynamic`'s
`offset` would lose Plan 228 D4's exactly-N-complete-buckets guarantee — the thing D4 exists to
protect. Polars also labels bucket **starts** by default (`training_data.py:299`) while skill's
completeness check assumes start labels by adding one step (`skill/service.py:261`), so
period-ending labelling is a behavioural change to both.

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

- **D1 — is phase part of the ForecastInterface contract, or SAP3 preprocessing provenance?** The FI
  adapter selects requirements solely by `timedelta` (`adapters/forecast_interface.py:496`, `:1310`).
  If a model must be able to *declare* a phase, that is an FI change and
  `CLAUDE.md` § ForecastInterface Adherence requires an upstream issue, **not** a SAP3-side
  workaround. If phase is purely our preprocessing concern, the adapter stays as it is and we
  guarantee the grid before `predict()`. **This decision gates T1 and T4.**
- **D2 — what carries resampling provenance?** A typed resampling result (data plus quality) is the
  obvious shape, but training, hindcast and skill cannot reuse operational persistence without new
  contracts. Decide per consumer whether degradation is persisted, logged, or gates the run.
- **D3 — does the no-imputation contract stand?** `docs/touchpoint-maps.md:228` says missing
  operational values are **gated, never interpolated**. Plan 252 OD-6 interpolates. One of them must
  formally supersede the other; today they contradict.
- **D4 — Swiss cutover shape.** Atomic flip, or per-station migration? Artifacts, hindcasts and skill
  generations must move together, and a config flip alone would feed 23:00Z days to midnight-trained
  artifacts.

## Non-goals

- Plan 252's conventions, types and CF ingest.
- Plan 226's daily-model anchoring, and Plan 234's aggregation declaration — this plan **consumes**
  both.
- The Forecast Lab v3 format change, which Plan 251 already owns; interval bounds must fold into
  that single version transition rather than opening a second one.
- Re-opening Plan 228 D1-D3 or its shipped P1/P2 fix.

## Tasks

Every code task carries the Task Exit Gate (`docs/workflow.md:378-390`).

### T1 — settle the four open decisions

**Outcome:** D1-D4 are answered in this plan with rationale, and D1's answer states explicitly
whether an upstream ForecastInterface issue is required.

**In:** this plan; the FI protocol and `adapters/forecast_interface.py`; `docs/touchpoint-maps.md:228`.

**Out:** any code change. The decisions exist so they are made once, in the open, rather than inside
an implementation diff.

**Pre-change:** N/A — decision task. The four decisions are recorded as open above, and D3 is a live contradiction between `touchpoint-maps.md:228` and Plan 252 OD-6.

**Verification:** N/A — decision task. Each answer cites the code or contract it rests on, and D1 either files the FI issue or records why none is needed.

### T2 — make the fetch-bound helpers grid-aware

**Outcome:** `floor_to_time_step` and `aligned_lookback_bounds` take a `TimeGrid` and preserve
exactly-N-complete-buckets at a non-zero phase.

**In:** `services/training_data.py:183`, `:200`. Depends on T1.

**Out:** the resampler itself (T3); any call-site change (T4).

**Pre-change:** `uv run pytest tests/unit/services/test_training_data.py -k bounds` against a non-zero phase yields buckets aligned to epoch, not to the declared grid, so the window has a partial bucket at both ends — the failure Plan 228 D4 documents at `228:139`.

**Verification:** `uv run pytest tests/unit/services/test_training_data.py` — for a declared phase of 64800 s, the fetch bounds yield exactly N complete buckets with no partial bucket at either end, and phase zero reproduces today's behaviour byte for byte.

### T3 — make the resampler phase-aware and provenance-carrying

**Outcome:** the resampler honours a declared `TimeGrid`, applies the OD-6 method for the channel's
CF temporal support, refuses upsampling, and returns provenance alongside the data.

**In:** `services/training_data.py:225`, including `closed` and `label` for period-ending —
polars defaults label bucket starts, which skill's completeness check assumes (`skill/service.py:261`),
so both change together. The return type changes per D2. Depends on T1, T2.

**Out:** call sites (T4). Changing `AggregationMethod`.

**Pre-change:** `grep -n "group_by_dynamic" services/training_data.py` shows `every=` with no `offset`, `closed` or `label`, so a declared-phase target is silently re-bucketed onto epoch marks — a shift presented as a resample.

**Verification:** `uv run pytest tests/unit/services/test_training_data.py` — a 15-minute source on quarter-hour marks maps onto both a UTC-hourly and a Nepali-hourly target with zero apportionment; an instantaneous (`time: point`) channel is interpolated and NOT treated as period-ending; an accumulation straddling a boundary is apportioned and flagged above 15 minutes; and an upsample is **REFUSED**, with the refusal locked by a test.

### T4 — thread the declared grid through all seven call sites

**Outcome:** every assembly path resolves its target grid from the deployment declaration rather than
assuming phase zero, and the downstream UTC-day assumptions are corrected.

**In:** the seven call sites listed above, plus the four downstream assumptions:
`services/forecast_lab/snapshot.py:551`, `:856`; `models/nwp_regression.py:598`; and the issue-time
filter at `services/operational_inputs.py:225` whose meaning changes under period-ending labels.
Depends on T3.

**Out:** the Forecast Lab format change (Plan 251). The Swiss cutover (T6).

**Pre-change:** each call site passes a bare `time_step`; `grep -n "resample_to_time_step" -r src/` shows seven sites and no grid is threaded to any of them.

**Verification:** `uv run pytest` — no call site constructs a grid implicitly, a phase-zero deployment is unchanged end to end, and the NWP antecedent window and issue-time filter are tested under a non-zero phase.

### T5 — record each artifact's training grid and fail closed on mismatch

**Outcome:** an artifact carries the `TimeGrid` it was trained on, and activating or predicting with a
mismatched grid is refused rather than silently wrong.

**In:** `db/metadata.py:907` (model artifacts) plus an additive migration, the artifact import path,
and the activation and prediction gates. Depends on T3.

**Out:** retraining anything (T6).

**Pre-change:** `grep -n "time_grid\\|grid_phase" src/sapphire_flow/db/metadata.py` returns nothing for artifacts, so an artifact trained on midnight days can be activated against an 18:00Z deployment with nothing detecting it — Plan 252 OD-7's silent substitution.

**Verification:** `uv run pytest tests/unit/services/test_model_registry.py tests/integration/db/` — an artifact whose recorded grid differs from the deployment's is refused at activation with a typed error, and the refusal is locked by a test.

### T6 — sequence the Swiss retrain and cutover

**Outcome:** Switzerland moves from phase 0 to 23:00Z with artifacts, hindcasts, skill generations and
configuration moving together, and a rollback.

**In:** the rollout sequence — persist training grids (T5), retrain, rerun phase-correct hindcasts,
publish a new skill generation via Plan 235's mechanism, promote, flip configuration. Coordinates with
Plan 226 (anchoring) and Plan 235 (generations); both plans' text and dependencies need amending,
since 226 is anchoring-only and states the UTC-midnight NWP grid remains authoritative (`226:91`) and
235's post-plan action is Plan 228's recompute (`235:355`). Depends on T4, T5.

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

**In:** the daily models' valid_time construction and the anchor computation. **Out:** everything
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
to quarantine, not repair, the rows that predate it. Writing a *correct* phase requires the declared
grid that T4 threads through the call sites; inferring it back from the timestamps at write time is
precisely the read-time inference Plan 252's corollary forbids. So the column cannot be written
honestly before T4, which puts it here.

🔴 **This is live, not anticipated.** Measured on staging 2026-09-08 after `0.1.889`: of the 377
`forecasts` rows the system has populated `time_step_seconds` on, all store `86400` and **209 (55%)
sit on a non-zero clock-derived phase** — 139 `climatology_fallback` rows at 26658 s (07:24:18 UTC)
among them. Every one of those rows currently reads as "daily grid" and none of them says where the
day starts.

**In:** `db/metadata.py` (`forecasts.phase_offset_seconds`, nullable, additive migration),
`store/forecast_store.py` write and read paths, `types/forecast.py`. Mirror the existing precedent
exactly — `skill_scores.phase_offset_seconds` / `skill_diagrams.phase_offset_seconds`
(`db/metadata.py:1540`, `:1611`; written at `store/skill_store.py:520`) — including its nullability.

⚠️ **Correct reason for nullability.** An earlier draft of this task justified it as "an ensemble
with no `valid_time` stays representable" — that is false: `ForecastEnsemble.from_members` rejects an
empty frame (`types/ensemble.py:54-63`), so such an ensemble cannot exist. The real reason is
historical: rows written before this column exists have an unrecorded phase, and NULL is how an
unknown is represented. Depends on T4.

**Out:** backfilling historical rows (Plan 248 T2 decided: quarantine — do not repair, do not
delete); `VALIDATE`-ing 248 T3's constraint; any change to `time_step_seconds` itself.

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
    {"id": "T3", "phase": 2, "depends_on": ["T1", "T2"]},
    {"id": "T4", "phase": 3, "depends_on": ["T3"]},
    {"id": "T5", "phase": 3, "depends_on": ["T3"]},
    {"id": "T8", "phase": 2, "depends_on": ["T1"]},
    {"id": "T6", "phase": 4, "depends_on": ["T4", "T5", "T8"]},
    {"id": "T7", "phase": 4, "depends_on": ["T4"]}
  ]
}
```
