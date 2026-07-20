---
status: DRAFT
created: 2026-07-20
plan: 130
title: Temperature (+min/max/sunshine) reanalysis live-tail — fetch the recent-daily tier so reanalysis reaches present, and harden nwp_regression missing-value handling
scope: The reanalysis temperature series (TabsD/TminD/TmaxD) and sunshine (SrelD) stop at the yearly-archive edge (~T-45d; 2026-05-31 on staging) because the adapter fetches only their archive tier — but MeteoSwiss ALSO serves them in the recent-daily per-day items (to ~T-2d), which the adapter ignores. This tail gap makes nwp_regression training crash (float(None)) on a missing future temperature for recent samples. Fix: fetch the recent-daily tier for these archive-backed products, and harden the model's missing-value handling. Temperature analog of Plan 128 (RprelimD precip tail).
depends_on: []
---

# Plan 130 — temperature reanalysis live-tail + model robustness

## What this is

Retraining on the mac-mini (2026-07-20) crashed `nwp_regression` with
`TypeError: float() argument must be a string or a real number, not 'NoneType'`
(`models/nwp_regression.py:308-309`). Root cause: the reanalysis **temperature** series has a **tail
gap**. `historical_forcing` holds `meteoswiss_tabsd` (and tmind/tmaxd/sreld) only to **2026-05-31**
(the yearly-archive edge), while training's recent discharge samples (staging BAFU, 2026-07-03..20)
need temperature past that edge. `_aligned_future` builds a `datetime→value` lookup and does
`float(lookup[ts])`, which crashes on the missing (`None`) recent temperature.

A live STAC probe (2026-07-20) reframed the fix: **MeteoSwiss is NOT missing recent temperature.** The
same recent per-day items that carry RprelimD also carry `tabsd_ch01r` / `tmind_ch01r` / `tmaxd_ch01r`
/ `sreld_ch01r`, **to 2026-07-18** — the SAME asset names as the yearly archive. Our adapter treats
those products as `archive_backed=True` and fetches only their yearly-archive tier
(`meteoswiss_open_data_reanalysis.py:330-331` routes only non-archive products through the daily
path), so it **never fetches their recent-daily tier** → the tail gap. This is a **MeteoSwiss-native
fix** (fetch the recent tier), NOT an ICON cross-fill.

*(Operational forecasting is unaffected — it sources future precip+temp from ICON NWP, which has both,
and the 2026-07-20 forecast cycle passed. The gap bites TRAINING only, where future precip/temp
teacher-forcing comes from the reanalysis, `training_data.py:186`. But a complete reanalysis tail is
the right state, and the model should not crash on a missing value regardless.)*

## Evidence (file:line + live probes, 2026-07-20)

- **The crash:** `nwp_regression._aligned_future` (`models/nwp_regression.py:304-309`):
  `lookup = dict(zip(frame["datetime"], frame[name]))`; `return np.asarray([float(lookup[ts]) for ts in
  target_times])` → `float(None)` when a target time's value is missing. Training reaches it via
  `_NwpRegressionBase.train` (`:163`). Confirmed on-mini (train-models over a window that includes the
  2026-07 staging discharge).
- **The tail gap (staging DB):** `historical_forcing` `meteoswiss_tabsd`/`tmind`/`tmaxd`/`sreld` end at
  **2026-05-31**; `meteoswiss_rhiresd` also 2026-05-31; only `meteoswiss_rprelimd` (precip) extends
  past it (to 2026-07-18, via the Plan 128 fix). So temperature has no live tail in the DB.
- **Live STAC probe:** the per-day items (`{YYYYMMDD}-ch`) carry ALL of
  `rprelimd_ch01h`, `sreld_ch01r`, `tabsd_ch01r`, `tmind_ch01r`, `tmaxd_ch01r` — e.g. item
  `20260715-ch`. `tabsd`/`tmind`/`tmaxd` assets are present in per-day items **to 2026-07-18**
  (min/max scan over the Jun-Jul window). The recent-daily TabsD uses the **same** `tabsd_ch01r` asset
  name/grid as the yearly archive.
- **The adapter miss:** `_PRODUCT_REGISTRY` marks TabsD/TminD/TmaxD/SrelD `archive_backed=True`
  (`:174-206`); `fetch_products` routes archive-backed products to the archive path and only
  non-archive (RprelimD) to the daily per-day path (`:330-331`). So the recent-daily tier of the
  temperature/sunshine products is never fetched — hence the DB stops at the archive edge.
- **Related:** Plan 128 (RprelimD id-fetch daily path — reused here); the #103 re-probe already noted
  MeteoSwiss serves each product in **three tiers** (Historical yearly / Recent daily / Now) and
  flagged asset-family/cadence for a live re-confirm (done here). Plan 129 (continuous precip knit) is
  the precipitation analog; a complete temperature tail also benefits 129's future consuming models.

## Proposed design (two parts — forks in grill-me)

### Part A — fetch the recent-daily tier for the archive-backed temp/sunshine products

Fill the reanalysis tail between the yearly-archive edge (~T-45d) and the recent-daily edge (~T-2d) by
fetching TabsD/TminD/TmaxD/SrelD from the **recent-daily per-day items** (the same `{YYYYMMDD}-ch`
path Plan 128 fixed for RprelimD), in addition to their yearly-archive tier for deep history. The
scheduled `ingest-weather-history` (60-day window) would then populate the recent temperature tail
automatically, exactly as it now does for RprelimD. **Open (grill-me):** whether the recent-daily tier
is *provisional* (later superseded by the definitive yearly archive — so it needs its own
source/version tag + supersession, like RhiresD/RprelimD) or is the *same* definitive TabsD served
earlier (so the same `meteoswiss_tabsd` source simply extends); and the exact routing (a per-product
"has a recent-daily tier" flag vs. reusing `archive_backed`).

### Part B — harden `nwp_regression` against a missing future value

`_aligned_future` must not `float(None)`-crash. Per the FI contract (CLAUDE.md §FI): a missing input
is an **anticipated** condition. In **training**, drop/skip (or impute) the affected samples rather
than crash (we want the model to train on the available data); in **predict**, a genuinely missing
future input should surface as a `ModelFailure` (the FI anticipated-failure path), not a `TypeError`.
This is defensive regardless of Part A and prevents a single missing day from taking down a whole
train-models or forecast run.

### Not changed
- **Operational forecasting** already sources future precip+temp from ICON (unaffected).
- **The RprelimD precip daily path** (Plan 128) is reused, not modified.

## Grill-me (owner decisions before READY)

1. **Recent-daily TabsD: provisional or definitive?** Does MeteoSwiss's recent-daily temperature get
   revised when the yearly archive publishes (→ treat as a separate provisional source/version with
   supersession, like RhiresD↔RprelimD), or is it the same value served earlier (→ extend the same
   `meteoswiss_tabsd` source)? Needs a live confirm (compare a recent-daily value vs the archive value
   for an overlapping past date).
2. **Scope of Part A — temperature only, or all archive-backed products?** TminD/TmaxD/SrelD are in the
   same per-day items; fill them too (consistent), or just TabsD (the one that crashed training)?
   Also: does precip need anything here, or is RprelimD already its recent tier (yes — leave precip)?
3. **Part B None-handling — drop vs impute in training; ModelFailure in predict?** Confirm the
   train-vs-predict split and the training strategy (drop affected rows vs impute).
4. **Is Part B alone enough for now?** Part B unblocks training immediately (no crash); Part A is the
   proper data-completeness fix. Ship both, or B first (fast) then A?

## Tests

- **Model robustness (Part B):** `_aligned_future` / `train` with a missing future value does NOT raise
  `TypeError`; training drops/imputes and completes; `predict` with a missing required future input
  returns `ModelFailure` (not raise). *Soundness: fails against the current `float(None)` code.*
- **Recent-daily fetch (Part A):** `fetch_products([METEOSWISS_TABSD], recent-window)` fetches the
  per-day-item tabsd asset and writes rows past the archive edge (mirroring the RprelimD daily-fetch
  tests); an aged-out/absent day is a gap, not a crash (reuse Plan 128's id-fetch + asset-absent
  handling). *Soundness: fails against the archive-only routing.*
- **Tail-fill end-to-end:** after the recent-daily fetch, `historical_forcing` temperature reaches the
  recent edge; a train-models run over a window including recent samples completes without the crash.
- **Staging gate:** on the mini, the temperature tail populates and `train-models` over the full
  window (incl. 2026-07 samples) succeeds.

## Exit gates

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run pyright src/            # ratchet vs baseline
uv run pytest                  # incl. Part B robustness + Part A recent-daily fetch tests
```
Plus the staging tail-fill + clean-retrain gate on the mini.

## Provenance

Surfaced 2026-07-20 while retraining on the mac-mini after the 128+Release-B deploy: `nwp_regression`
crashed on a missing recent reanalysis temperature. A live STAC probe then showed MeteoSwiss DOES serve
recent temperature (recent-daily per-day items to T-2d, same asset names) — the adapter just doesn't
fetch that tier for archive-backed products. So the fix is MeteoSwiss-native (fetch the recent tier)
plus model robustness, NOT an ICON cross-fill. Temperature analog of Plan 128; relates to Plan 129 and
the #103 three-tier re-probe. DRAFT — `plan` workflow (incl. independent Codex) + owner grill-me before
READY.
