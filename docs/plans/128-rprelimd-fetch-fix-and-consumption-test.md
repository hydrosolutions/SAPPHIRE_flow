---
status: DRAFT
created: 2026-07-19
plan: 128
title: RprelimD live-tail — fix the silent id-fetch defect so preliminary-precip rows get written
scope: Fix the confirmed adapter defect that makes the RprelimD (preliminary daily precipitation) live-tail write 0 rows. Narrow, self-contained correctness fix — after it, the scheduled weather ingest populates the recent RprelimD tail into `historical_forcing`. Consuming RprelimD in a model (the continuous past→forecast precip knit) is the separate Plan 129.
depends_on: []
---

# Plan 128 — RprelimD live-tail: fix the silent id-fetch defect

## What this is

The 115b3 staging validation (2026-07-17) found **RprelimD wrote 0 rows** in both the backfill and the
live-tail fetch. A 2026-07-19 investigation (live STAC probe reproducing the adapter's exact failing call
+ an independent code trace) **confirmed it is an adapter defect, not a MeteoSwiss gap.** This plan is the
narrow, self-contained fix. It makes the recent RprelimD tail actually land in `historical_forcing` via the
already-scheduled ingest. **Consuming** RprelimD in a model — the continuous past→forecast precipitation
knit (RhiresD → RprelimD → NWP) — is the richer **Plan 129** (which depends on this).

*(Split rationale: the adapter fix is a confirmed correctness bug worth landing on its own — it starts
building the RprelimD archive regardless of when a consuming model exists — and it has no design questions.
The continuous-precip model + seam-continuity test carry real design decisions and live in 129.)*

## Evidence (file:line grounded, 2026-07-19)

- **The defect.** RprelimD is the ONLY daily-only (`archive_backed=False`) product
  (`adapters/meteoswiss_open_data_reanalysis.py:149-154`); all others are archive-backed and never touch the
  daily path. Both writers funnel RprelimD through `fetch_products → _fetch_range → _fetch_daily_items_range
  → _fetch_day_feature`. `_fetch_day_feature` (~`:618-645`) locates each per-day item by querying
  `items?datetime={day}T00:00:00Z`, which filters on `properties.datetime`. But MeteoSwiss keys per-day items
  by **data-date in the item id** (`20260520-ch`) while `properties.datetime` **drifts ~2 months forward** as
  the multi-product item accrues/refreshes assets → the query returns 0 features → the day is silently
  skipped (`reanalysis.day_gap`) → **0 rows**.
- **Reproduced live (2026-07-19):** `items?datetime=2026-05-20T00:00:00Z` → 0 features; `items/20260520-ch`
  → HTTP 200 (that item's `properties.datetime` = 2026-07-17). RprelimD IS published next-day
  (`created` ≈ data-date), and has a ~2-month rolling retention (2026-05-18/19 are 404 = aged out;
  05-20→now present).
- **Both writers hit it; the scheduled ingest self-heals after the fix.** Backfill:
  `services/reanalysis_backfill.py:332` (RprelimD span at `:217-229`). Scheduled Flow-6 ingest:
  `flows/ingest_weather_history.py:501-511` requests RprelimD over a hard-coded 60-day window
  (`_WINDOW_DAYS = 60`, `:59`; computed `:424-425`), daily cron `0 6 * * *`
  (`register_deployments.py:40-42,101-107`). **After the id-fetch fix the scheduled daily ingest
  automatically populates the recent RprelimD tail — no other write-side change.** Gate: it only fetches for
  stations with a REANALYSIS-role MeteoSwiss binding + valid basin polygon
  (`ingest_weather_history.py:280-289`, `reanalysis_backfill.py:154-167`).
- **The read path is unaffected** (it reads the DB store, never STAC) and needs no change — see Plan 129.
- **The tautological-fixture trap is present.** `tests/unit/adapters/test_meteoswiss_open_data_reanalysis.py`
  fixtures set `properties.datetime` **equal to** the data date (`_feature`, `:184-186`; also `:493-495`,
  `:760-762`), and the fake handler returns whatever date is in the `?datetime=` query (`:200-214`) — so the
  buggy query **passes in tests but fails in prod**.

## Design — address the per-day item by id

Replace the `items?datetime={day}T00:00:00Z` search in `_fetch_day_feature` with a **direct item fetch by
id** `GET …/items/{YYYYMMDD}-ch` (the MeteoSwiss data-date item id): HTTP 200 → use it; **404 → a genuine
gap** (not published / aged out, e.g. 2026-05-18/19), logged as `reanalysis.day_gap` exactly as today.
Downstream asset-matching (`_asset_href`, key `RprelimD_`/href `rprelimd_`) is unchanged and already correct;
keep the loud `AdapterError` if an item exists but carries no RprelimD asset (`:~657`). Derive the item id
from the requested day; confirm the `{YYYYMMDD}-ch` id format against the live collection.

## Tasks

- **A1 — id-fetch in `_fetch_day_feature`.** Fetch `items/{YYYYMMDD}-ch` directly; 404 = genuine gap
  (unchanged `reanalysis.day_gap` semantics); keep the no-asset `AdapterError`. (Resolve grill-me #1 first.)
- **A2 — drifted-datetime regression test (soundness-critical).** Add a fixture whose item id = `{YYYYMMDD}-ch`
  but `properties.datetime` is ~2 months forward, and a fake handler that simulates the real server
  (`items?datetime={day}` → 0 features; `items/{YYYYMMDD}-ch` → 200). It **must fail against the current
  `_fetch_day_feature`** and pass only after A1. Keep a distinct **genuine-gap** test (404 → no rows, no
  error), so the drift bug and a real gap are not conflated. Extend the daily happy-path + RprelimD
  source-mapping tests (`test_meteoswiss_open_data_reanalysis.py:329,459-464,945-959`).
- **C1 — staging confirmation (write side only).** On the mac-mini: deploy the fixed image, trigger
  `ingest-weather-history`, and confirm `historical_forcing WHERE source='meteoswiss-rprelimd'` now has recent
  rows for the staging stations (prerequisite: those stations carry the MeteoSwiss reanalysis binding + valid
  basins — 115b §2A created these; verify). This confirms the fix end-to-write; model **consumption** is 129.
- **D1 — doc sync.** Record the RprelimD id-fetch behaviour + the ~2-month retention in the adapter/reanalysis
  docs and the weather-track references.

## Grill-me (owner decisions before READY)

1. **Range-fallback in A1?** After switching to id-fetch, keep a bounded `?datetime=` range fallback for the
   day, or id-only? **Recommend id-only** — the `?datetime=` filter is the bug; a fallback re-introduces the
   drift blind spot.
2. **Deep RprelimD backfill?** Confirm we do NOT attempt to backfill deep RprelimD history (it is aged out;
   RhiresD covers deep history). The plan is live-tail-only. **Recommend: confirmed, no deep RprelimD backfill.**

## Tests

- **A2 drifted-datetime regression** — the load-bearing soundness test; must fail red against current code.
- **Genuine-gap test** — `items/{YYYYMMDD}-ch` → 404 → no rows, no error (distinct from the drift bug).
- **Write-side unaffected products** — archive-backed products (RhiresD/TabsD/…) still fetch unchanged.
- **C1 staging write-gate** — after `ingest-weather-history`, `historical_forcing` has recent
  `source='meteoswiss-rprelimd'` rows. *Must fail against the pre-Plan-128 image (0 RprelimD rows).*

## Exit gates

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run pyright src/            # ratchet vs baseline
uv run pytest                  # incl. A2 drifted-datetime + genuine-gap
```
Plus the C1 staging write-gate (RprelimD rows written on the mini).

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1",
      "name": "RprelimD id-fetch fix",
      "tasks": ["A1-id-fetch", "A2-drift-regression-test", "D1-doc-sync"],
      "parallel": false,
      "task_depends_on": {
        "A2-drift-regression-test": ["A1-id-fetch"],
        "D1-doc-sync": ["A1-id-fetch"]
      },
      "depends_on": []
    },
    {
      "id": "phase-2",
      "name": "Staging write-confirmation (mac-mini)",
      "tasks": ["C1-staging-write-confirm"],
      "parallel": false,
      "depends_on": ["phase-1"]
    }
  ]
}
```

## Provenance

Root cause confirmed 2026-07-19 (live STAC probe + code trace). Originally drafted as a combined fix +
consumption-test plan; **split 2026-07-19** on owner reframe — RprelimD is the temporal *knit* giving a
continuous precip series past→forecast, and testing model consumption of that continuum is a richer design
(**Plan 129**). This plan is the standalone adapter correctness fix. DRAFT — `plan` workflow (incl.
independent Codex) + owner grill-me before READY. Relates to the 115b weather-identity track.
