---
status: READY
created: 2026-07-20
plan: 130
title: Temperature (+min/max/sunshine) reanalysis live-tail — fetch the recent-daily tier so reanalysis reaches present, and harden nwp_regression missing-value handling
scope: The reanalysis temperature series (TabsD/TminD/TmaxD) and sunshine (SrelD) stop at the yearly-archive edge (~T-45d; 2026-05-31 on staging) because the adapter fetches only their archive tier — but MeteoSwiss ALSO serves them in the recent-daily per-day items (to ~T-2d), which the adapter ignores. This tail gap makes nwp_regression training crash (float(None)) on a missing future temperature for recent samples. Fix: fetch the recent-daily tier for these archive-backed products, and harden the model + training flows so a missing value never crashes a whole run. Temperature analog of Plan 128 (RprelimD precip tail).
depends_on: []
---

# Plan 130 — temperature reanalysis live-tail + model/flow robustness

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
the right state, and a missing value should never crash a whole train/onboard run.)*

## Evidence (file:line + live probes, 2026-07-20)

- **The crash:** `nwp_regression._aligned_future` (`models/nwp_regression.py:304-309`):
  `lookup = dict(zip(frame["datetime"], frame[name]))`; `return np.asarray([float(lookup[ts]) for ts in
  target_times])` → `float(None)` when a target time's value is missing. Reached via
  `_NwpRegressionBase.train` (`:163`). Confirmed on-mini (train-models over a window including the
  2026-07 staging discharge).
- **The tail gap (staging DB):** `meteoswiss_tabsd`/`tmind`/`tmaxd`/`sreld` end at **2026-05-31**;
  `meteoswiss_rhiresd` also 2026-05-31; only `meteoswiss_rprelimd` (precip) extends past it (to
  2026-07-18, via Plan 128). So temperature has no live tail in the DB.
- **Live STAC probe:** the per-day items (`{YYYYMMDD}-ch`) carry ALL of `rprelimd_ch01h`, `sreld_ch01r`,
  `tabsd_ch01r`, `tmind_ch01r`, `tmaxd_ch01r` (e.g. item `20260715-ch`), with `tabsd`/`tmind`/`tmaxd`
  present **to 2026-07-18**. Recent-daily TabsD uses the **same** `tabsd_ch01r` asset name/grid as the
  yearly archive.
- **Tier structure confirmed (probe, resolves grill-me #1):** the `archive-ch` item's yearly TabsD
  assets run **1961→2025 only** — there is **no 2026 yearly archive**. So 2026 is served by the
  **monthly-"last"** tier (complete months, ending at the last complete month = 2026-05-31 on staging)
  **plus** the **recent-daily** per-day items (the partial current month, 06-01→07-18). Therefore the
  recent-daily tier is the **provisional partial-current-month tail**, superseded by the
  monthly-"last"/yearly definitive once the month completes — the same provisional→definitive pattern as
  RhiresD↔RprelimD, but within the `tabsd` product name (not a separate `Tprelim` product).
- **The adapter miss:** `_PRODUCT_REGISTRY` marks TabsD/TminD/TmaxD/SrelD `archive_backed=True`
  (`:174-206`); `fetch_products` routes archive-backed → archive path only, non-archive (RprelimD) →
  daily per-day path (`:330-331`). So the recent-daily tier of the temp/sunshine products is never
  fetched.
- **Writer callers of the tail fetch:** the scheduled `ingest-weather-history` (60-day window), the
  one-shot `scripts/backfill_meteoswiss_history` (`run_backfill`), **and station onboarding**
  (`services/onboarding.py:451` also calls `run_backfill` with default span discovery). All three would
  pick up the recent tail once Part A routes it through the daily path.
- **Related:** Plan 128 (RprelimD id-fetch daily path — reused here); the #103 re-probe noted MeteoSwiss
  serves each product in **three tiers** (Historical yearly / Recent daily / Now). Plan 129 (continuous
  precip knit) is the precipitation analog; a complete temperature tail also benefits 129's future
  consuming models.

## Proposed design (two parts — forks in grill-me)

### Part A — fetch the recent-daily tier for the archive-backed temp/sunshine products

Fill the reanalysis tail between the yearly-archive/monthly-"last" coverage and the recent-daily edge
(~T-2d) by fetching TabsD/TminD/TmaxD/SrelD from the **recent-daily per-day items** (the same
`{YYYYMMDD}-ch` id-fetch path Plan 128 fixed for RprelimD), in addition to their yearly/monthly tiers
for history. The scheduled `ingest-weather-history` then populates the recent temperature tail
automatically, as it now does for RprelimD.

**Routing must split by ACTUAL coverage, not a coarse flag (review blocker).** Do NOT simply disable
the yearly/monthly-"last" tier for these products — that can skip current-year months older than the
recent-daily retention window on a fresh backfill (`meteoswiss_open_data_reanalysis.py:607,614`;
`reanalysis_backfill.py:302`). The recent-daily path is an **additional tail source** layered on top of
the existing archive/monthly-last coverage — used for the window AFTER the monthly-last high-water mark
(or as a per-day fallback), never a replacement for it. A test must prove older current-year monthly
temp/sunshine still fetches while the post-edge daily fetch avoids duplicate natural keys.

**Open (grill-me):** whether the recent-daily tier is *provisional* (later revised when the yearly
archive publishes — so it needs its own source/version tag + supersession, like RhiresD↔RprelimD) or is
the *same* definitive TabsD served earlier (so the same `meteoswiss_tabsd` source simply extends).
Needs a live confirm (compare a recent-daily value vs the archive value for an overlapping past date).

### Part B — a missing future value must never crash a run

Two layers:
1. **Model:** `_aligned_future` must not `float(None)`-crash. Per the FI contract (CLAUDE.md §FI), a
   missing input is an **anticipated** condition. In **training**, drop the affected samples (train on
   the available rows); in **predict**, a genuinely missing required future input surfaces as a
   `ModelFailure`, not a `TypeError`.
2. **Flow (prefer flow-level try/except — review recommendation):** wrap the per-unit training call so a
   raised exception (including the existing insufficient-data `ValueError` at `nwp_regression.py:175-179`)
   is recorded as a failed unit and the run continues, instead of aborting ALL units. This must cover
   **BOTH** training flows: `train_models_flow` (`_train_model_task`, `train_models.py:377-383`) **and**
   `onboard_model_flow` (`_train_and_store_artifact_task`, `onboard_model.py:747`, which currently has
   no try/except — the older service path `model_onboarding.py:1235` already maps to `FAILED_TRAINING`).
   A flow-level guard is a strict superset that also covers any model-internal drop logic, without
   duplicating row-classification into the generic assembly layer.

### Not changed / out of scope
- **Operational forecasting** already sources future precip+temp from ICON (unaffected).
- **The RprelimD precip daily path** (Plan 128) is reused, not modified.
- **Predict-side cross-variable alignment refactors** beyond the missing-value guard are OUT of scope
  (a `/plan` pass proposed a broader predict alignment rework unrelated to the tail-gap crash — declined).

## Grill-me — resolved 2026-07-21

1. **Recent-daily TabsD provisional-vs-definitive? — RESOLVED (probe): PROVISIONAL** (partial-current-month
   tail; yearly archive stops 2025, monthly-"last" ends at complete months, recent-daily fills the rest).
   **Design:** fetch the recent-daily tier into the **same `meteoswiss_tabsd` source** (same product name),
   layered after the monthly-"last" high-water mark; the definitive monthly-"last"/yearly value supersedes
   it when the month completes via the rolling 60-day re-fetch + `historical_forcing.version` latest-wins
   read (`historical_forcing_store.py:55`). *Confirm the store write semantics (versioned-append vs upsert
   on (source, valid_time, parameter)) in the build; explicit write-side supersession stays out-of-scope,
   consistent with 129 §4 — read-time latest-wins suffices.*
2. **Part A scope — RESOLVED: all four archive-backed products** (TabsD/TminD/TmaxD/SrelD). They share the
   per-day items and the same tier structure; fill all for consistency. (Precip needs nothing — RprelimD
   is already its recent tier.)
3. **Sequencing — RESOLVED: ship BOTH Part A and Part B.** Part B (robustness) is the load-bearing unblock
   for **Plan 129's T1** (the consuming model inherits this `train()` path); Part A completes the reanalysis
   data so training uses real recent temperature.
4. **Training missing-value strategy — RESOLVED: DROP** affected samples (no synthetic imputation).

## Tests

- **Flow robustness (Part B, load-bearing):** a unit whose training raises (missing value, or the
  insufficient-data `ValueError`) is recorded as a failed unit and the run **continues** for the other
  units — in **both** `train_models_flow` AND `onboard_model_flow`. *Soundness: fails against the current
  no-try/except flows (one raise aborts the whole run).*
- **Model missing-value handling (Part B):** `_aligned_future`/`train` with a missing future value does
  not raise `TypeError` (drops the row); `predict` with a genuinely missing required future input returns
  `ModelFailure`. *Soundness: fails against the current `float(None)`.*
- **Recent-daily fetch (Part A):** `fetch_products([METEOSWISS_TABSD], recent-window)` fetches the
  per-day-item `tabsd` asset and writes rows past the archive edge (mirroring the RprelimD daily tests);
  an absent/aged-out day is a gap, not a crash (reuse Plan 128 id-fetch + asset-absent handling).
  *Soundness: fails against archive-only routing.*
- **Routing preserves history (Part A blocker):** older current-year monthly temp/sunshine still
  fetches via the monthly-"last" tier while the post-edge daily fetch adds the tail without duplicate
  natural keys. *Soundness: fails against a fix that disables monthly-last for these products.*
- **Staging gate:** on the mini, the temperature tail populates and `train-models` over the full window
  (incl. 2026-07 samples) completes without the crash.

## Exit gates

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run pyright src/            # ratchet vs baseline
uv run pytest                  # incl. Part B flow+model robustness + Part A recent-daily + routing-preservation tests
```
Plus the staging tail-fill + clean-retrain gate on the mini.

## Known limitations

**Backfill-path supersession of provisional temperature.** `run_backfill` (used for both the one-shot
backfill and station onboarding, `services/onboarding.py`) skips logical days already present, so it
does **not** re-fetch to supersede a provisional recent-daily temperature value with the later
definitive monthly-"last"/yearly value (`reanalysis_backfill.py:~315-335` filters fetched rows before
the store write, so `historical_forcing_store`'s latest-wins read never sees the definitive row on this
path). The scheduled `ingest-weather-history` path, by contrast, is correct: it stores fetched rows
directly (`ingest_weather_history.py:~513,525`), and its 60-day window covers essentially the whole
~2-month recent-daily tier, so it supersedes provisional→definitive in normal operation. The
stale-provisional edge is therefore narrow — a day whose definitive monthly-"last" only publishes after
it has aged out of the 60-day ingest window — and low-impact, since a provisional temperature is
approximately equal to its definitive value. **Follow-up (if it matters):** make `run_backfill`
re-fetch the recent-daily window instead of skipping, or add explicit write-side supersession
(currently deferred, consistent with Plan 129 §4).

**TminD/TmaxD/SrelD tail coverage.** These three products share TabsD's `recent_daily_tail` code path
via the shared `_Product` registry, but they are not independently fixture-tested at the tail level;
coverage relies on the shared implementation plus the per-product NetCDF variable-name tests elsewhere.
This is a residual risk, accepted.

## Provenance

Surfaced 2026-07-20 while retraining on the mac-mini after the 128+Release-B deploy: `nwp_regression`
crashed on a missing recent reanalysis temperature. A live STAC probe then showed MeteoSwiss DOES serve
recent temperature (recent-daily per-day items to T-2d, same asset names) — the adapter just doesn't
fetch that tier for archive-backed products. So the fix is MeteoSwiss-native (fetch the recent tier)
plus flow/model robustness, NOT an ICON cross-fill. A `plan`-workflow run (2026-07-20) escalated after
its planner over-expanded the design (a predict-alignment refactor + a 4th, unrelated failure mode —
declined); its genuine findings folded here: harden BOTH training flows (not just `train_models`),
prefer a flow-level try/except, add onboarding to the caller list, and the routing-must-not-drop-history
blocker. Grill-me resolved 2026-07-21 (a live STAC probe settled the provisional question — recent-daily is
the provisional partial-current-month tail; yearly archive stops 2025). **READY (owner, 2026-07-21) —
sequenced BEFORE Plan 129's T1, which is blocked on this fix.** Build via `implement`; hold-at-PR.
Temperature analog of Plan 128; relates to Plan 129 and the #103 three-tier re-probe.

**Post-implementation fixer round (2026-07-21):** the first committed pass (`3f5fd70`) re-introduced
exactly the predict-side cross-variable alignment refactor this plan declared out of scope (`_HORIZON`-capped
grid + dict-based timestamp alignment for BOTH future-known variables, replacing the original
per-series-positional grid construction) — flagged by independent Codex review. Reverted `predict()` to
its original per-series `_sorted_series`-based positional grid, keeping only the missing-value
(`np.isnan`) guard actually in scope; the two tests that only made sense under the reworked mechanism
(mismatched-length / same-tail-truncation cross-alignment) were removed. `train()`/`_aligned_future()`
(the load-bearing Part B fix) are unchanged. Also fixed a pyright-ratchet regression from the same
commit (`onboard_model.py` 23→24, the new `_store_onboarding_artifact_task` split adding a 7th
`prefect.runtime` stub-gap renderer): suppressed locally with `# pyright: ignore[reportAttributeAccessIssue]`
(precedent: `run_forecast_cycle.py:1208`) instead of growing the baseline; baseline restored to main's
542/23.
