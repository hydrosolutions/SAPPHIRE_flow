---
status: DRAFT
created: 2026-07-19
plan: 128
title: RprelimD live-tail — fix the id-fetch defect + a model that consumes past reanalysis precip (Swiss staging test)
scope: Make the preliminary-precipitation live tail (RprelimD) actually flow end-to-end and be consumed by a model on the mac-mini Swiss test deployment. Two code changes (the confirmed adapter fetch defect; a consuming test-subject model that declares past reanalysis precipitation) plus a staging deploy-and-verify. The read path and forecast-fetch date range are already correct and unchanged.
depends_on: []
---

# Plan 128 — RprelimD live-tail: fix the id-fetch defect + prove a model consumes it

## What this is (and why the adapter fix alone is not enough)

The 115b3 staging validation (2026-07-17) found **RprelimD wrote 0 rows** in both the backfill and the
live-tail fetch. A 2026-07-19 investigation (live STAC probe + code trace) **confirmed it is an adapter
defect, not a MeteoSwiss gap** (§Evidence). But the goal here is bigger than the 0-rows fix: the owner
wants to **test a model that consumes RprelimD on the mac-mini Swiss test deployment**. Grounding that
end-to-end surfaced the load-bearing fact:

> **No current model consumes past-dynamic reanalysis precipitation.** All five registered models read
> either *future* NWP precip (`nwp_regression`/`nwp_rainfall_runoff`) or discharge only
> (`linear_regression_daily`, `climatology_fallback`, `persistence_fallback`). The FI adapter even
> **strips the discharge target's own history out of `past_dynamic_features`**
> (`adapters/forecast_interface.py:505-511`), and `past_dynamic_features` is the *only* channel that
> triggers a reanalysis fetch (`services/training_data.py:177-191`, `services/operational_inputs.py:402-410`).
> So RprelimD currently feeds **nothing** downstream — the adapter fix is invisible end-to-end without a
> model that declares past reanalysis precip.

Therefore Plan 128 is three parts: **(A)** fix the adapter so RprelimD rows get written; **(B)** add a
**consuming test-subject model** (FI-compliant) that declares past reanalysis precipitation so the read
path is exercised; **(C)** deploy + verify on the mac-mini. The read path and the forecast fetch window
are already correct (§Evidence 4) and are **not** changed.

## Evidence (all file:line grounded, 2026-07-19)

1. **The defect.** RprelimD is the ONLY daily-only (`archive_backed=False`) product
   (`adapters/meteoswiss_open_data_reanalysis.py:149-154`); all others are archive-backed and never touch
   the daily path. Both writers funnel RprelimD through `fetch_products → _fetch_range →
   _fetch_daily_items_range → _fetch_day_feature`. `_fetch_day_feature` (~`:618-645`) locates each per-day
   item by querying `items?datetime={day}T00:00:00Z`, which filters on `properties.datetime`. But
   MeteoSwiss keys per-day items by **data-date in the item id** (`20260520-ch`) while `properties.datetime`
   **drifts ~2 months forward** as the multi-product item accrues/refreshes assets → the query returns 0
   features → the day is silently skipped (`reanalysis.day_gap`) → **0 rows**. **Reproduced live:**
   `items?datetime=2026-05-20T00:00:00Z` → 0 features; `items/20260520-ch` → HTTP 200 (that item's
   `properties.datetime` = 2026-07-17). RprelimD IS published next-day (`created` ≈ data-date), and has a
   ~2-month rolling retention (2026-05-18/19 are 404 = aged out; 05-20→now present).
2. **Write path — both writers, and the scheduled ingest self-heals after the fix.** Backfill:
   `services/reanalysis_backfill.py:332` (`fetch_products([span.product], …)`, RprelimD span at `:217-229`).
   Scheduled Flow-6 ingest: `flows/ingest_weather_history.py:501-511` requests RprelimD over
   `[max(start, rhiresd_end), now)`; its window is a hard-coded `_WINDOW_DAYS = 60`
   (`ingest_weather_history.py:59`, computed `:424-425`); it runs daily (cron `0 6 * * *`,
   `register_deployments.py:40-42,101-107`). **So after the id-fetch fix the scheduled daily ingest
   automatically populates the recent RprelimD tail — no other write-side change.** Gate: it only fetches
   for stations with a REANALYSIS-role binding to `nwp_source="meteoswiss_open_data_reanalysis"`
   (`ingest_weather_history.py:280-289`), created by `bind_meteoswiss_reanalysis_fleet`
   (`reanalysis_backfill.py:154-167`), which needs a valid basin polygon per station.
3. **Read path — per-day fallback, already correct.** `HybridForcingSource.fetch_reanalysis`
   (`hybrid_reanalysis.py:61-135`) indexes rows by `(station_id, valid_time, parameter)` and walks the
   priority chain **per key** (`:87-99`); precip chain `(RHIRESD, RPRELIMD)`
   (`hybrid_reanalysis_factories.py:37-46`). For a recent day with only RprelimD rows, RprelimD wins. The
   read path never hits STAC — the bug is purely write-side. **No read-path change needed.**
4. **Forecast fetch reaches ~now (needs RprelimD), already correct.** Operational assembly
   `assemble_station_operational_inputs` fetches `fetch_reanalysis(start=issue−lookback, end=issue, …)`
   (`operational_inputs.py:336,346,403-410`), gated on `past_dynamic_features` non-empty; wired at
   `run_forecast_cycle.py:1539-1548,1802-1809`. For a daily model the recent-lookback window sits inside
   the RhiresD-lag gap → served by RprelimD. Training/hindcast uses the same call over a historical window
   (`training_data.py:141-191`) — that window is covered by **definitive RhiresD**, so RprelimD's
   consumption is specifically the **operational recent-lookback** precip. **No date-range change needed.**
5. **The tautological-fixture trap is present.** `tests/unit/adapters/test_meteoswiss_open_data_reanalysis.py`
   fixtures set `properties.datetime` **equal to** the data date (`_feature`, `:184-186`; also `:493-495`,
   `:760-762`), and the fake STAC handler returns whatever date is in the `?datetime=` query (`:200-214`) —
   so the buggy query **passes in tests but fails in prod**. The regression test must use a **drifted-datetime
   fixture** + a handler that simulates the real server (`items?datetime={day}`→0; `items/{YYYYMMDD}-ch`→200).

## Design

### Part A — fix `_fetch_day_feature` to address the per-day item by id

Replace the `items?datetime={day}T00:00:00Z` search with a **direct item fetch by id**
`GET …/items/{YYYYMMDD}-ch` (the MeteoSwiss data-date item id): HTTP 200 → use it; **404 → a genuine gap**
(that day is not published / aged out, e.g. 2026-05-18/19), logged as `reanalysis.day_gap` exactly as
today. The asset-matching downstream (`_asset_href`, key `RprelimD_`/href `rprelimd_`) is unchanged and
already correct. Confirm the item-id format is stable (`{YYYYMMDD}-ch`) and derive it from the requested
day; keep the loud `AdapterError` path if an item exists but carries no RprelimD asset
(`meteoswiss_open_data_reanalysis.py:~657`). *(Open: whether to keep a bounded `?datetime=` range fallback —
see grill-me.)*

### Part B — a consuming test-subject model (FI-compliant)

To exercise RprelimD end-to-end, a model must declare `precipitation` as a **non-target** `past_known`
variable (product key e.g. `"reanalysis"`), so `_project_requirements` routes it into
`past_dynamic_features` (`forecast_interface.py:505-511`) → drives `fetch_reanalysis(parameters=
["precipitation"], …)` → the hybrid RhiresD/RprelimD chain. This is **FI-native** (past inputs are
declared exactly this way — no FI deviation, no SAP3 workaround; see CLAUDE.md §FI adherence). The model's
lookback must reach into the RhiresD-lag gap so the operational fetch window is RprelimD-served. **Which
model — a minimal probe vs a realistic variant vs a genuinely useful operational model — is the owner fork
(grill-me #1).** It is a **model code change** (the requirement is declared in Python `input_requirement`,
not config), onboarded via the `onboard-model` deployment.

### Part C — deploy + verify on the mac-mini (Swiss staging)

1. Deploy the fixed image (Part A + Part B).
2. Confirm the two staging stations (Porte_du_Scex, Rheinfelden) have the MeteoSwiss reanalysis binding +
   valid basin geometry (115b §2A already created these — **verify**, don't assume), and assign the Part-B
   model to at least one.
3. Trigger `ingest-weather-history` (manual run) to fill the recent RprelimD tail (the scheduled daily run
   also does this; a deep `run_backfill` is NOT needed — RprelimD's ~2-month retention means old history is
   gone anyway, and training uses RhiresD).
4. Run `forecast-cycle` with the consuming model assigned.
5. **Verify consumption:** `forcing.source_selected` / `forcing.resolution_completed` logs show
   `winning_source`/`source_counts` = `meteoswiss-rprelimd` on recent days (`hybrid_reanalysis.py:116-134`);
   DB: `historical_forcing WHERE source='meteoswiss-rprelimd'` has recent-window rows; the forecast for the
   consuming model completes using them.

## Prerequisites / gates (flag before implementing)

- **Station binding + basin geometry** — both the write (ingest) and read (fetch) silently skip stations
  without a REANALYSIS binding + valid basin polygon (`ingest_weather_history.py:280-289`,
  `reanalysis_backfill.py:154-167`). Verify on staging first.
- **RprelimD retention** — you can only ever fetch/consume the recent ~2-month tail; deep RprelimD history
  is unrecoverable (definitive RhiresD covers deep history). The test is inherently about the *live tail*.
- **No read-path / forecast-fetch changes** — those are already correct; a change there would be a
  regression, not a fix.

## Tasks

- **A1 — id-fetch in `_fetch_day_feature`.** Fetch `items/{YYYYMMDD}-ch` directly; 404 = genuine gap
  (unchanged `reanalysis.day_gap` semantics); keep the no-asset `AdapterError`. Resolve the range-fallback
  grill-me before coding.
- **A2 — drifted-datetime regression test (soundness-critical).** Add a fixture whose item id = `{YYYYMMDD}-ch`
  but `properties.datetime` is ~2 months forward, and a fake handler that simulates the real server
  (`items?datetime={day}`→0 features; `items/{YYYYMMDD}-ch`→200). It **must fail against the current
  `_fetch_day_feature`** and pass only after A1. Keep a distinct **genuine-gap** test (404 → no rows), so the
  drift bug and a real gap are not conflated. Extend the daily happy-path + RprelimD source-mapping tests
  (`test_meteoswiss_open_data_reanalysis.py:329,459-464,945-959`).
- **B1 — consuming test-subject model** (per grill-me #1): declare `precipitation` as a non-target
  `past_known` (product `"reanalysis"`) so it projects into `past_dynamic_features`; set a lookback that
  overlaps the RprelimD tail; register it. FI-compliant (no contract deviation).
- **B2 — model tests:** the model's projected `past_dynamic_features` contains `precipitation`
  (i.e. the FI projection routes it, not stripped as a target); training + operational assembly issue a
  `fetch_reanalysis(parameters=["precipitation"], …)` for it (assert the call/effect, mirroring
  `training_data.py:186-191` / `operational_inputs.py:404-410`). *Soundness: fails against a model whose
  `past_dynamic_features` is empty.*
- **C1 — staging deploy + verify** (the mac-mini test): deploy → verify bindings/basins + assign model →
  `ingest-weather-history` → `forecast-cycle` → confirm `meteoswiss-rprelimd` in the resolution logs + DB +
  a completed forecast. Record the RprelimD row counts + winning-source counts.
- **D1 — doc sync:** note the RprelimD id-fetch behaviour + retention in the adapter/reanalysis docs and
  `docs/plans/…` weather-track references; record the consuming model + its lookback contract.

## Grill-me (owner decisions before READY)

1. **What is the consuming test-subject model?** (a) a **minimal "reanalysis-precip probe"** FI model whose
   only purpose is to declare + consume past precip (clean, isolated, validates the plumbing — recommended
   for a *test*); (b) a **realistic variant of `nwp_regression`** that adds past reanalysis precip as an
   extra feature (closer to operational, but forks a production model); (c) a **genuinely useful new
   operational model** (e.g. past-precip + discharge regression) — more work, but the natural follow-on.
   Which is the intent — *validate the plumbing* (a), or *start building precip-consuming models* (c)?
2. **Range-fallback in A1?** After switching to id-fetch, keep a bounded `?datetime=` range fallback for the
   day, or id-only (simplest, and id-fetch is authoritative)? Recommend **id-only** — the `?datetime=` filter
   is the bug; a fallback re-introduces the drift blind spot.
3. **Deep RprelimD backfill?** Confirm we do NOT attempt to backfill deep RprelimD history (it is aged out;
   RhiresD covers deep history). The test is live-tail-only. (Recommend: confirmed, no deep RprelimD backfill.)
4. **Keep the probe model after the test?** If grill-me #1 = (a), does the probe model stay registered
   (as a standing RprelimD/read-path smoke check) or get removed after the staging verification?

## Tests

- **A2 drifted-datetime regression** (above) — the load-bearing soundness test; must fail red against current code.
- **Genuine-gap test** — `items/{YYYYMMDD}-ch` → 404 → no rows, no error (distinct from the drift bug).
- **B2 model projection + fetch** — the consuming model projects `precipitation` into `past_dynamic_features`
  and drives a reanalysis precip fetch (training + operational).
- **Read-path unchanged** — existing hybrid per-day RhiresD→RprelimD fallback tests still pass (no change).
- **C1 staging deploy-gate** — on the mini: after `ingest-weather-history`, `historical_forcing` has recent
  `source='meteoswiss-rprelimd'` rows; a `forecast-cycle` for the consuming model logs
  `winning_source=meteoswiss-rprelimd` on recent days and completes. *Must fail against the pre-Plan-128
  image (0 RprelimD rows / no consuming model).*

## Exit gates

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run pyright src/            # ratchet vs baseline
uv run pytest                  # incl. A2 drifted-datetime + genuine-gap + B2 model tests
```
Plus the C1 staging deploy-gate (RprelimD rows written + consumed on the mini).

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1",
      "name": "RprelimD id-fetch fix + consuming test-subject model",
      "tasks": ["A1-id-fetch", "A2-drift-regression-test", "B1-consuming-model", "B2-model-tests", "D1-doc-sync"],
      "parallel": false,
      "task_depends_on": {
        "A2-drift-regression-test": ["A1-id-fetch"],
        "B2-model-tests": ["B1-consuming-model"],
        "D1-doc-sync": ["A1-id-fetch", "B1-consuming-model"]
      },
      "note": "A (adapter fix) and B (consuming model) are independent code changes; both land before the staging test. A1 fetches items/{YYYYMMDD}-ch (404=gap); B1 declares precipitation as a non-target past_known (FI-native). Grill-me #1 (which model) gates B1's shape.",
      "depends_on": []
    },
    {
      "id": "phase-2",
      "name": "Staging deploy + consumption verification (mac-mini)",
      "tasks": ["C1-staging-deploy-verify"],
      "parallel": false,
      "depends_on": ["phase-1"]
    }
  ]
}
```

## Provenance

Root cause confirmed 2026-07-19 (live STAC probe reproducing the adapter's exact failing call + an
independent code trace). Scoped as an enablement plan — not just the 0-rows fix — after the owner asked to
**test a model that consumes RprelimD on the mac-mini Swiss deployment**; grounding then established that no
current model consumes past reanalysis precip (the adapter fix is invisible end-to-end without a consuming
model). DRAFT — `plan` workflow (incl. independent Codex) + owner grill-me decisions before READY. Relates
to the 115b weather-identity track (RprelimD is the live-tail product of the hybrid reanalysis reader).
