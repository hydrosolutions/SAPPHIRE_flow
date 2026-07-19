---
status: DRAFT
created: 2026-07-19
plan: 129
title: Continuous precipitation knit (RhiresD → RprelimD → NWP) + a regression model that consumes it (Swiss staging test)
scope: Prove the RprelimD live-tail knits a CONTINUOUS precipitation series from the definitive past (RhiresD) through the preliminary recent past (RprelimD) into the NWP forecast, and that a model can consume that continuum. Adds an FI-native regression model (past discharge + season + continuous precip) and a coarse unit-error seam gate; tests end-to-end on the mac-mini Swiss deployment. Temporal seam only — no value-consistency / bias correction yet (deferred).
depends_on: [128]
---

# Plan 129 — the continuous precipitation knit + a consuming model

## What this is

RprelimD is the **temporal knit** that makes precipitation *continuous* across the whole timeline a model
sees:

```
 RhiresD (definitive, deep past)  →  RprelimD (recent past — fills RhiresD's ~45-day lag up to issue-time)  →  NWP (forecast, future)
 └──────────────── reanalysis (past_dynamic precip) ───────────────┘                                          └──── NWP (future precip) ────┘
```

The point to prove is **not** "RprelimD gets read" (Plan 128 makes the rows land) but that a model receives
**one continuous precipitation series from deep past through the forecast horizon, with no temporal gap at
the two seams** (RhiresD→RprelimD and RprelimD→NWP), and forecasts from it. This is the classic
rainfall-runoff need: continuous antecedent precipitation feeding the forecast.

**Load-bearing design point (owner: confirm).** For this to exercise RprelimD at all, the model's
precipitation input must include the **past/recent** portion — the reanalysis series RprelimD gap-fills up to
issue-time — **not just the future NWP forecast.** A model consuming only forecast rainfall never touches
RprelimD (that is exactly what `nwp_regression` does today, `models/nwp_regression.py:126-143`). So the
model's precip is designed as **one continuous covariate spanning past-reanalysis → future-NWP, with the past
channel being the RprelimD-consuming one.**

## Owner decisions (grill-me, confirmed 2026-07-19)

1. **Seam first, no value-consistency handling.** Build the temporal knit only. **No** cross-product bias
   correction / reconciliation between preliminary RprelimD, definitive RhiresD, and NWP forecast in v1 —
   that is a later addition. The FI already hands the model a past array (reanalysis) and a future array
   (NWP) joined at issue-time; the "seam" is that RprelimD closes the temporal gap so the past array reaches
   issue-time and meets the NWP future. **No new stitching engine — continuity is achieved by RprelimD
   coverage and verified/gated, not reconciled.**
2. **Coarse, high-tolerance seam gate (unit-error catcher, NOT statistical).** With ~3 forecast days there is
   nothing to do statistics on. The gate is a **units/scale sanity check** at each seam — it must catch a
   unit error (mm vs m ≈ 1000×; per-hour vs per-day ≈ 24×) but must **never** fail on a legitimate
   meteorological difference (a forecast rain event that RprelimD does not have). So: check both sides are in
   a plausible **mm/day scale** (window magnitude, e.g. medians/maxima — not point values), flag only an
   order-of-magnitude / systematic offset. Threshold set high; exact value is a small remaining decision.
3. **Model = a regression forecast** taking **past runoff (discharge, autoregressive) + season + rainfall**,
   where *rainfall* is the **continuous precip series** (past reanalysis RhiresD/RprelimD-filled + future NWP
   forecast). FI-native. See Design.
4. **RprelimD→RhiresD supersession is OUT OF SCOPE** (RprelimD is preliminary and gets superseded by
   definitive RhiresD ~45 days later via the `historical_forcing.version` column) — a **follow-up plan**, not
   tested here.

## Design

### The consuming model (FI-compliant regression)

A new FI model (working name `seasonal_precip_runoff_regression`) predicting discharge, declaring:

- **`past_known` `obs/discharge`** — the target's own antecedent history (autoregressive "past runoff").
  Available to the model even though the FI adapter strips the *target's* history from `past_dynamic_features`
  (`adapters/forecast_interface.py:505-511`).
- **`past_known` `reanalysis/precipitation`** (non-target) → the FI projects it into **`past_dynamic_features`**
  (`forecast_interface.py:505-511`), which drives `fetch_reanalysis(parameters=["precipitation"], …)` in both
  training (`services/training_data.py:186-191`) and operational assembly
  (`services/operational_inputs.py:404-410`) → the **hybrid RhiresD/RprelimD chain**. **This is the
  RprelimD-consuming channel.**
- **`future_known` `nwp/precipitation`** — the NWP forecast precip over the horizon (as `nwp_regression`
  already declares, `models/nwp_regression.py:126-143`).
- **Season** — a derived temporal feature the model computes from `valid_time` (e.g. day-of-year / a season
  encoding); no data fetch.

At forecast time the model therefore sees precipitation as: past reanalysis precip up to issue-time
(RhiresD deep + **RprelimD** recent) **concatenated with** future NWP precip — a continuous series. The
lookback must reach into the RhiresD-lag window so the past-precip fetch is genuinely RprelimD-served
(lookback length is a small remaining decision; it must overlap the ~45-day RprelimD tail).

### The seam-continuity gate

A coarse check (per owner decision 2) run over the assembled continuous series at the two boundaries:

- **RhiresD → RprelimD** (inside the past reanalysis series): both are MeteoSwiss daily precip in mm/day —
  the gate guards against a future unit/scale regression at the product handoff, not value agreement.
- **RprelimD → NWP** (past reanalysis → future forecast): the last RprelimD day and first NWP day must be in
  the same mm/day scale (catch an ICON precip unit/accumulation error), tolerating event differences.

Implementation: a units/scale sanity function over a window around each seam (plausible mm/day bounds + no
systematic order-of-magnitude offset in window magnitude), returning pass / unit-error-flag. **Not** a
per-day value-agreement or statistical-consistency test. Where it lives (a small validation util reused by
the staging check vs a model-internal guard) is a remaining decision.

### What is NOT changed (already correct — a change here would be a regression)

- **Read path** — per-day RhiresD→RprelimD fallback in `HybridForcingSource.fetch_reanalysis`
  (`hybrid_reanalysis.py:87-99`) already returns RprelimD for recent days once its rows exist.
- **Operational forecast fetch window** — `assemble_station_operational_inputs` already fetches
  `[issue−lookback, issue)` up to ~now (`operational_inputs.py:346,403-410`); it only fires when
  `past_dynamic_features` is non-empty, which the new model provides.
- **RprelimD write path** — fixed in Plan 128 (this plan depends on it).

## Prerequisites / gates

- **Plan 128 landed + RprelimD rows present** on staging (this plan is meaningless without them).
- **Station binding + basin geometry** — the two staging stations (Porte_du_Scex, Rheinfelden) need the
  MeteoSwiss reanalysis binding + valid basins (write and read both skip stations lacking them) and at least
  one must carry the new model.
- **Training uses definitive RhiresD** — RprelimD's ~2-month retention means historical training windows are
  covered by RhiresD; RprelimD's consumption is specifically the **operational recent-lookback** precip.

## Tasks

- **M1 — the consuming FI model** (`seasonal_precip_runoff_regression`): declare `past_known obs/discharge`
  + `past_known reanalysis/precipitation` (→ `past_dynamic_features`) + `future_known nwp/precipitation`;
  a season feature; a regression fit. FI-compliant (no contract deviation — past inputs declared the FI-native
  way). Register via `onboard-model`.
- **M2 — model tests:** the model's projected `past_dynamic_features` contains `precipitation` (routed, not
  stripped as a target); training + operational assembly each issue a `fetch_reanalysis(parameters=
  ["precipitation"], …)` for it. *Soundness: fails against a model whose `past_dynamic_features` is empty
  (i.e. the current NWP-only behaviour).*
- **S1 — the seam-continuity gate** (coarse, high-tolerance units/scale check per owner decision 2) at the
  RhiresD→RprelimD and RprelimD→NWP boundaries.
- **S2 — seam-gate tests:** a mm-vs-m (1000×) and per-hour-vs-per-day (24×) unit error at a seam is FLAGGED;
  a legitimate forecast rain event that RprelimD lacks is **NOT** flagged. *Soundness: the unit-error cases
  fail against a no-op gate; the met-difference case fails against a point-value-agreement gate (proves the
  gate is coarse, not statistical).*
- **T1 — staging consumption test (mac-mini):** deploy → verify bindings/basins + assign the model → confirm
  the recent RprelimD tail is present (Plan 128) → run `forecast-cycle` → verify the model consumed a
  continuous precip series: `forcing.source_selected`/`resolution_completed` logs show
  `winning_source=meteoswiss-rprelimd` on recent days (`hybrid_reanalysis.py:116-134`), the past-precip array
  reaches issue-time and meets the NWP future precip (no gap), the seam gate passes, and the forecast
  completes. Record RprelimD row counts + per-source counts.
- **D1 — doc sync:** the continuous-precip knit + the new model + the seam gate in the model/forcing docs and
  the weather-track references.

## Remaining grill-me (before READY)

- **Seam threshold value** — the exact high-tolerance number for the units/scale gate (decision 2 fixes the
  *shape*; the number remains).
- **Model lookback length** — must overlap the RprelimD tail; pick a concrete value (and season encoding).
- **Train + skill-score, or run-only?** For a *first* consumption test, is an untrained/trivially-fit
  regression enough to exercise the continuum, or do we train + skill-score it (`train-models` /
  `compute-skills`)?
- **Seam-gate home** — a reusable validation util (like the 115b3 gate) vs a model-internal guard.

## Tests

- **M2 model projection + reanalysis fetch** (above).
- **S2 seam-gate coarseness** (above) — unit errors flagged, met differences tolerated.
- **Continuous-series assembly** — with RprelimD rows present, the operational past-precip fetch reaches
  issue-time (no gap before the NWP future precip). *Soundness: fails if the past array stops at the RhiresD
  boundary (i.e. RprelimD not consumed).*
- **T1 staging consumption gate** — RprelimD consumed on the mini, seam gate passes, forecast completes.
  *Must fail against a model that consumes only future NWP precip (RprelimD untouched).*

## Exit gates

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run pyright src/            # ratchet vs baseline
uv run pytest                  # incl. M2 + S2 + continuous-series assembly
```
Plus the T1 staging consumption gate on the mini.

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1",
      "name": "Consuming model + seam gate",
      "tasks": ["M1-consuming-model", "M2-model-tests", "S1-seam-gate", "S2-seam-gate-tests", "D1-doc-sync"],
      "parallel": false,
      "task_depends_on": {
        "M2-model-tests": ["M1-consuming-model"],
        "S2-seam-gate-tests": ["S1-seam-gate"],
        "D1-doc-sync": ["M1-consuming-model", "S1-seam-gate"]
      },
      "note": "M (model) and S (seam gate) are independent; both land before the staging test. M1 declares reanalysis/precipitation as a non-target past_known (FI-native) — the RprelimD-consuming channel. No read-path / forecast-fetch changes (already correct).",
      "depends_on": []
    },
    {
      "id": "phase-2",
      "name": "Staging consumption test (mac-mini)",
      "tasks": ["T1-staging-consumption"],
      "parallel": false,
      "depends_on": ["phase-1"]
    }
  ]
}
```

## Provenance

Split from Plan 128 on the owner reframe (2026-07-19): RprelimD is the temporal knit giving a continuous
precip series past→forecast, and testing a model's consumption of that continuum is a richer design than the
adapter fix. Grill-me owner-decided 2026-07-19 (seam-first / no bias correction; coarse high-tolerance
unit-error seam gate; a past-runoff + season + continuous-rainfall regression; supersession deferred). DRAFT
— `plan` workflow (incl. independent Codex) + the remaining grill-me before READY. Depends on Plan 128
(RprelimD rows must be written first). Relates to the 115b weather-identity track and the FI adherence
contract (the model declares past reanalysis precip the FI-native way).
