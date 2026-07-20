---
status: READY
created: 2026-07-19
plan: 129
title: Continuous precipitation knit (RhiresD → RprelimD → NWP) + a regression model that consumes it (Swiss staging test)
scope: Prove the RprelimD live-tail knits a CONTINUOUS precipitation series from the definitive past (RhiresD) through the preliminary recent past (RprelimD) into the NWP forecast, and that a model consumes that continuum end-to-end on the mac-mini Swiss deployment. Adds an FI-native regression model (past discharge + season + continuous precip) that is onboarded/trained/promoted, and a coarse unit-error seam gate run as a T1 diagnostic over raw provenance-bearing rows. Temporal seam only — no value-consistency / bias correction (deferred); RprelimD→RhiresD supersession deferred.
depends_on: [128]
---

# Plan 129 — the continuous precipitation knit + a consuming model

## What this is

RprelimD is the **temporal knit** that makes precipitation *continuous* across the timeline a model sees:

```
 RhiresD (definitive, deep past)  →  RprelimD (recent past — fills RhiresD's ~45-day lag up to issue-time)  →  NWP (forecast, future)
 └──────────────── reanalysis (past_dynamic precip) ───────────────┘                                          └──── NWP (future precip) ────┘
```

The point to prove is that a model receives **one continuous precipitation series from deep past through the
forecast horizon, with no temporal gap at the two seams** (RhiresD→RprelimD, RprelimD→NWP), and forecasts
from it — the classic rainfall-runoff need for continuous antecedent precipitation.

**Design premise (established — the whole design rests on it).** For this to exercise RprelimD, the model's
precipitation input must include the **past/recent** portion — the reanalysis series RprelimD gap-fills up to
issue-time — **not just the future NWP forecast.** A model consuming only forecast rainfall never touches
RprelimD (that is exactly `nwp_regression` today, `models/nwp_regression.py:126-143`). So the model's precip
is **one continuous covariate spanning past-reanalysis → future-NWP, with the past channel being the
RprelimD-consuming one.**

## Owner decisions (grill-me, confirmed 2026-07-19)

1. **Seam first, no value-consistency handling.** Temporal knit only — no cross-product bias correction
   between preliminary RprelimD, definitive RhiresD, and NWP forecast in v1. The FI already hands the model a
   past array (reanalysis) and a future array (NWP) joined at issue-time; the "seam" is that RprelimD closes
   the temporal gap so the past array reaches issue-time. **No new stitching engine.**
2. **Coarse, high-tolerance seam gate as a T1-only DIAGNOSTIC (not a production safeguard).** It runs during
   the staging test over the **raw provenance-bearing rows** and is **not** wired into operational assembly.
   It is a units/scale sanity check — catch a unit error (mm vs m ≈ 1000×; per-hour vs per-day ≈ 24×), never
   fail on a legitimate meteorological difference (a forecast rain event RprelimD lacks). Wiring it into every
   forecast is a **follow-up**, out of scope.
3. **Model = a regression forecast** on **past runoff (discharge lags) + season + continuous precip** (past
   reanalysis RhiresD/RprelimD-filled + future NWP), extending the existing FI regression base. See Design.
4. **RprelimD→RhiresD supersession OUT OF SCOPE** (preliminary superseded by definitive ~45 days later via
   `historical_forcing.version`) — a follow-up.
5. **Train + promote in the test.** Run-only is not viable — `_run_single_model` fetches an **active
   artifact** before predicting and skips otherwise (`services/run_station_forecast.py:152`). So T1 onboards,
   trains, and promotes the model before the forecast.

## Design

### The consuming model (FI-compliant regression) — task M1

A new FI model (working name `seasonal_precip_runoff_regression`) that **extends the existing
`_NwpRegressionBase`** (`models/nwp_regression.py`), predicting discharge. Its declared inputs:

- **`past_known` `obs/discharge`** — the target's antecedent history (autoregressive "past runoff"), as the
  base already declares. (The FI adapter strips the *target's* history from `past_dynamic_features`,
  `adapters/forecast_interface.py:505-511` — so this does not drive a reanalysis fetch; it is the model's own
  lag feature.)
- **`future_known` `nwp/precipitation` + `nwp/temperature`** — inherited from the base
  (`models/nwp_regression.py:126,163,213`). The base is **precip + temp + discharge lags**, not precip-only —
  the new model keeps both future NWP channels.
- **NEW — `past_known` `reanalysis/precipitation`** (non-target) → the FI projects it into
  **`past_dynamic_features`** (`forecast_interface.py:505-511`), which drives
  `fetch_reanalysis(parameters=["precipitation"], …)` in training (`services/training_data.py:186-191`) and
  operational assembly (`services/operational_inputs.py:404-410`) → the **hybrid RhiresD/RprelimD chain**.
  **This is the RprelimD-consuming channel.**
- **Season** — a derived temporal feature from `valid_time` (encoding TBD — small residual); no data fetch.

The model therefore sees precipitation as past reanalysis (RhiresD deep + **RprelimD** recent, up to
issue-time) concatenated with future NWP precip — continuous. Its lookback must overlap the ~45-day RprelimD
tail so the past-precip fetch is genuinely RprelimD-served (lookback length is a small residual).

**FI anticipated-failure contract (split per the two distinct code paths — CLAUDE.md §FI):**
- **SHORT / short-shaped past-precip window** (fewer rows than the declared lookback, no explicit NaN):
  reaches `predict()` via `_raw_forcing_to_dataframe`'s row-count-only pivot
  (`training_data.py:114-133`, which does not pad missing days) → the model **must length/shape-check and
  return `ModelFailure`** (mirroring `nwp_regression.py:218-232`), **not raise**.
- **Explicit NaN in returned rows** → caught **upstream** by the SAP3 `max_nan` gate
  (`forecast_interface.py:617-626,646-666`), which **raises `ModelOutputError` before the model's `predict()`
  is entered**. This is the pre-existing adapter-level gate — **NOT** a behaviour M1 implements or M2 asserts
  `predict()` handles by returning. The plan documents it as the existing path; it does not build it.

**Registration (do not skip — `onboard-model` only accepts discovered models):** add the model's
`pyproject.toml` entry point (so `discover_models()` finds it, `flows/onboard_model.py:610`,
`services/model_registry.py:78`, `pyproject.toml:137`), plus its tier / alert-eligibility classification
(class attrs / registry, `types/ids.py:28`) and config-reference priority.

### The seam-continuity gate — task S1 (T1-only diagnostic, on RAW rows)

A coarse units/scale check (owner decision 2) invoked **only by the T1 staging check**, over the **raw
`RawHistoricalForcing` rows** (which carry `.source`) and the raw NWP rows — **before** any pivot, because
`_raw_forcing_to_dataframe` keeps only timestamp + values and **drops `source`**
(`operational_inputs.py:236`, `training_data.py:114`), so the seams are not locatable in the assembled frame.

- **RhiresD → RprelimD** (within the reanalysis rows): both are MeteoSwiss daily precip in mm/day — the gate
  guards against a unit/scale regression at the product handoff, not value agreement.
- **RprelimD → NWP** (reanalysis vs NWP rows): the last RprelimD day and first NWP day must be in the same
  mm/day scale (catch an ICON precip unit/accumulation error), tolerating event differences.

Implementation: a units/scale sanity function over a window around each seam (plausible mm/day bounds + no
systematic order-of-magnitude offset in window magnitude) → pass / unit-error-flag. **Not** a per-day
value-agreement or statistical test. **Not wired into operational assembly** (owner decision 2 — follow-up).

### What is NOT changed (already correct — a change here would be a regression)

- **Read path** — per-day RhiresD→RprelimD fallback (`hybrid_reanalysis.py:87-99`) already returns RprelimD
  for recent days once its rows exist.
- **Operational forecast fetch window** — `assemble_station_operational_inputs` already fetches
  `[issue−lookback, issue)` up to ~now (`operational_inputs.py:346,403-410`); it fires once
  `past_dynamic_features` is non-empty (which M1 provides).
- **RprelimD write path** — fixed in Plan 128 (this plan depends on it).

## Prerequisites / gates

- **Plan 128 landed + RprelimD rows present** on staging (this plan is meaningless without them).
- **Station binding + basin geometry** — the two staging stations (Porte_du_Scex, Rheinfelden) need the
  MeteoSwiss reanalysis binding + valid basins (write and read both skip stations lacking them) and at least
  one must carry the new model.
- **Training uses definitive RhiresD** — RprelimD's ~2-month retention means historical training windows are
  covered by RhiresD; RprelimD's consumption is specifically the **operational recent-lookback** precip.

## Tasks

- **M1 — the consuming FI model** (`seasonal_precip_runoff_regression`): extend `_NwpRegressionBase`; keep
  future NWP precip+temp + discharge lags; **add `past_known reanalysis/precipitation`** (→
  `past_dynamic_features`); add a season feature; SHORT-window `predict()` returns `ModelFailure` (NaN path is
  the pre-existing upstream `max_nan` raise — not built here). **Register**: `pyproject.toml` entry point +
  tier/alert classification + config priority.
- **M2 — model tests:** assert **`precipitation ∈ adapter.data_requirements.past_dynamic_features`** AND the
  assembled `StationTrainingData.past_dynamic` (and operational `past_dynamic`) **contains precipitation**
  (not merely "a fetch happened" — NWP-only models already fetch precip for future teacher-forcing). Plus:
  the SHORT-window `predict()` returns `ModelFailure` (the NaN path is out of M1/M2 scope — it is the existing
  adapter gate). *Soundness: fails against a model whose `past_dynamic_features` is empty (today's NWP-only
  behaviour) and against a short-window `predict()` that raises instead of returning.*
- **S1 — the seam gate** (coarse units/scale check, T1-diagnostic) over **raw** reanalysis + NWP rows at the
  RhiresD→RprelimD and RprelimD→NWP seams. Not wired into operational assembly.
- **S2 — seam-gate tests:** a mm-vs-m (1000×) and per-hour-vs-per-day (24×) unit error at a seam is FLAGGED;
  a legitimate forecast rain event RprelimD lacks is **NOT** flagged. *Soundness: the unit-error cases fail
  against a no-op gate; the met-difference case fails against a point-value-agreement gate.*
- **T1 — staging consumption test (mac-mini):** deploy → verify bindings/basins → **onboard + train + promote**
  the model (owner decision 5; run-only not viable) → confirm the recent RprelimD tail is present (Plan 128)
  → run `forecast-cycle` → **verify via DB, not debug logs**: `historical_forcing` rows with
  `source='meteoswiss_rprelimd'` (the correct literal, `types/forcing_sources.py:26`) are present in the
  consumed recent window and the `resolution_completed` `source_counts` (INFO-level) show RprelimD; the
  past-precip array reaches issue-time and meets the NWP future precip (no gap); the **S1 seam gate passes on
  the raw rows**; the forecast completes. Record RprelimD counts + per-source counts. *(If a per-day
  `winning_source` breakdown is wanted, set a targeted DEBUG override for the run — the default is INFO,
  `logging.py:56`.)*
- **D1 — doc sync:** the continuous-precip knit + the new model + the T1-diagnostic seam gate in the
  model/forcing docs + the weather-track references.

## Grill-me — resolved 2026-07-19

- Seam-gate home → **T1-only diagnostic on raw rows** (owner). Train/run → **train + promote** (owner;
  forced by the active-artifact requirement). Seam-first / no bias correction; supersession deferred (owner).
- **Residual (small, implementation-level — not blocking the design):** the seam-gate threshold value; the
  model's lookback length + season encoding. Fixed at implementation.

## Tests

- **M2 model projection + past-precip routing** — `precipitation ∈ past_dynamic_features` and assembled
  `past_dynamic` contains precipitation (training + operational). SHORT-window `predict()` returns
  `ModelFailure`.
- **S2 seam-gate coarseness** — unit errors flagged, met differences tolerated.
- **Continuous-series assembly** — with RprelimD rows present, the operational past-precip fetch reaches
  issue-time (no gap before the NWP future precip). *Soundness: fails if the past array stops at the RhiresD
  boundary (RprelimD not consumed).*
- **T1 staging consumption gate** — RprelimD consumed on the mini (DB `source='meteoswiss_rprelimd'` +
  INFO `source_counts`), seam gate passes on raw rows, trained model forecasts complete. *Must fail against a
  model that consumes only future NWP precip (RprelimD untouched).*

## Exit gates

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run pyright src/            # ratchet vs baseline
uv run pytest                  # incl. M2 (past-precip routing + short-window ModelFailure) + S2 + assembly
```
Plus the T1 staging consumption gate on the mini.

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1",
      "name": "Consuming model + seam-gate diagnostic",
      "tasks": ["M1-consuming-model", "M2-model-tests", "S1-seam-gate", "S2-seam-gate-tests", "D1-doc-sync"],
      "parallel": false,
      "task_depends_on": {
        "M2-model-tests": ["M1-consuming-model"],
        "S2-seam-gate-tests": ["S1-seam-gate"],
        "D1-doc-sync": ["M1-consuming-model", "S1-seam-gate"]
      },
      "note": "M1 extends _NwpRegressionBase (keeps future NWP precip+temp+lags) and ADDS reanalysis/precipitation as a non-target past_known (FI-native) — the RprelimD channel; also registers the entry point + tier/alert classification. S1 is a T1-only diagnostic over RAW provenance-bearing rows (the pivot drops source). FI NaN path is the pre-existing upstream max_nan raise, not built here. No read-path / forecast-fetch changes.",
      "depends_on": []
    },
    {
      "id": "phase-2",
      "name": "Staging consumption test (mac-mini) — onboard + train + promote + forecast",
      "tasks": ["T1-staging-consumption"],
      "parallel": false,
      "depends_on": ["phase-1"]
    }
  ]
}
```

## Provenance

Split from Plan 128 on the owner reframe (2026-07-19): RprelimD is the temporal knit giving a continuous
precip series past→forecast. Grill-me owner-decided 2026-07-19 (seam-first/no bias; coarse high-tolerance
unit-error seam gate as a **T1-only diagnostic on raw rows**; a past-runoff + season + continuous-rainfall
regression **onboarded/trained/promoted**; supersession deferred). A `plan`-workflow run (2026-07-19)
escalated with **legitimate, code-grounded findings** (unlike the 128 run's bloat) — all folded: the seam
gate must read raw provenance rows (the pivot drops `source`); the FI NaN path raises upstream (only the
SHORT-window path is the model's `ModelFailure` to return); M2 must assert past-precip routing specifically;
registration needs the entry point + tier/alert classification; T1 must verify via DB not debug logs and use
the `meteoswiss_rprelimd` literal; run-only is not viable (train+promote required); the base already declares
temperature. A confirming independent Codex review (2026-07-19) then verified every fold sound + citations
accurate — clean, no blockers/majors/minors. **READY (owner + confirming review, 2026-07-19).** Build via
`implement` after Plan 128 lands; hold-at-PR. Depends on Plan 128. Relates to the 115b weather-identity track
and the FI adherence contract.

**Post-implementation review fixes (2026-07-20):** an independent Codex pass over the committed diff found
two residual gaps, both fixed and locking-test-proven. (1) The "no gap before NWP future precip" continuity
test asserted only that `past_dynamic` reached issue-time, never that the first `future_dynamic` precip
bucket actually abuts it — for the test's midnight issue-time fixture, `_filter_and_cap_daily_records`
(`services/operational_inputs.py`) dropped the whole issue-day NWP bucket via a strict `valid_time >
issue_time`, silently opening a one-day seam gap. Fixed to `>=`: a non-midnight cycle still backdates (and
correctly drops) the issue-day bucket, but a midnight-exact issue-time's issue-day bucket is genuinely all
future and is now kept. The test now asserts `future_dynamic["timestamp"].min() - latest_past_precip_ts ==
time_step` directly, proving the seam rather than a same-step proxy. (2) `seam_gate.py`'s window builders
(`seam_window_from_forcing_rows`/`seam_window_from_nwp_rows`) filtered raw rows only by source/parameter,
not by `station_id` (or, for NWP, `nwp_source`/`cycle_time`) — a T1 query spanning both staging stations, or
an NWP fetch spanning multiple cycles, could silently mix another station's or run's rows into the seam
window. Both builders now take explicit `station_id`/`nwp_source`/`cycle_time` and filter to them before
windowing.
