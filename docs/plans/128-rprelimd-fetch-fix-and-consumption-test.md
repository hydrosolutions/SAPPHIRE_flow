---
status: DRAFT
created: 2026-07-19
plan: 128
title: RprelimD live-tail — fix the silent id-fetch defect so preliminary-precip rows get written
scope: Fix the confirmed adapter defect that makes the RprelimD (preliminary daily precipitation) live-tail write 0 rows, and handle the confirmed edge race where a fresh per-day item exists before its asset is attached (so the scheduled ingest cannot crash on it). Narrow, self-contained correctness fix — after it, the scheduled weather ingest populates the recent RprelimD tail into `historical_forcing`. Consuming RprelimD in a model (the continuous past→forecast precip knit) is the separate Plan 129.
depends_on: []
---

# Plan 128 — RprelimD live-tail: fix the silent id-fetch defect

## What this is

The 115b3 staging validation (2026-07-17) found **RprelimD wrote 0 rows** in both the backfill and the
live-tail fetch. A 2026-07-19 investigation (live STAC probe reproducing the adapter's exact failing call
+ an independent code trace) **confirmed it is an adapter defect, not a MeteoSwiss gap.** This plan is the
narrow, self-contained fix: make the recent RprelimD tail actually land in `historical_forcing` via the
already-scheduled ingest, **and** handle the confirmed edge race (item published before its asset) so the
scheduled ingest cannot crash. **Consuming** RprelimD in a model — the continuous past→forecast knit
(RhiresD → RprelimD → NWP) — is the richer **Plan 129** (depends on this).

## Evidence (file:line grounded; two live probes 2026-07-19)

- **The defect.** RprelimD is the ONLY daily-only (`archive_backed=False`) product
  (`adapters/meteoswiss_open_data_reanalysis.py:149-154`); all others are archive-backed and never touch the
  daily path. Both writers funnel RprelimD through `fetch_products → _fetch_range → _fetch_daily_items_range
  → _fetch_day_feature → _rows_for_product`. `_fetch_day_feature` (~`:618-645`) locates each per-day item by
  querying `items?datetime={day}T00:00:00Z`, which filters on `properties.datetime`. But MeteoSwiss keys
  per-day items by **data-date in the item id** (`20260520-ch`) while `properties.datetime` **drifts ~2
  months forward** → the query returns 0 features → the day is silently skipped (`reanalysis.day_gap`) →
  **0 rows**.
- **Probe A — the drift.** `items?datetime=2026-05-20T00:00:00Z` → 0 features; `items/20260520-ch` → HTTP 200
  (its `properties.datetime` = 2026-07-17). RprelimD IS published next-day (`created` ≈ data-date), ~2-month
  rolling retention (05-18/19 are 404 = aged out; 05-20→now present).
- **Probe B — the edge race (the "third state").** Fetched the newest item ids by id: `20260713..18-ch` →
  item 200 **with** the `rprelimd` asset; **`20260719-ch` (today) → item 200 but NO `rprelimd` asset yet.**
  So MeteoSwiss creates the per-day item first and attaches the RprelimD asset ~a day later. **Consequence:**
  once we fetch by id, the latest day(s) will routinely be *item-present, asset-absent* — and today's code
  raises a loud `AdapterError` when `_asset_href` finds no matching asset (`:~655-657`), inside
  `_fetch_daily_items_range` which has **no per-day try/except** → it would **crash the whole scheduled
  `ingest-weather-history` run** on every run. The fix must degrade this to a gap, not raise.
- **Both writers hit the daily path; the scheduled ingest self-heals after the fix.** Backfill:
  `services/reanalysis_backfill.py:332` (RprelimD span `:217-229`). Scheduled Flow-6 ingest:
  `flows/ingest_weather_history.py:501-511` requests RprelimD over a hard-coded 60-day window
  (`_WINDOW_DAYS = 60`, `:59`; computed `:424-425`), daily cron `0 6 * * *`
  (`register_deployments.py:40-42,101-107`). Gate: only stations with a REANALYSIS-role MeteoSwiss binding +
  valid basin polygon (`ingest_weather_history.py:280-289`, `reanalysis_backfill.py:154-167`).
- **The read path is unaffected** (reads the DB store, never STAC) — no change (see Plan 129).
- **Tautological-fixture trap.** `tests/unit/adapters/test_meteoswiss_open_data_reanalysis.py` fixtures set
  `properties.datetime` **equal to** the data date (`_feature`, `:184-186`; `:493-495`, `:760-762`) and the
  fake handler returns whatever date is in the `?datetime=` query (`:200-214`) — so the buggy query **passes
  in tests but fails in prod.**

## Design — address the per-day item by id, and degrade the asset-absent edge to a gap

1. **Id-fetch.** In `_fetch_day_feature`, replace the `items?datetime={day}` search with a **direct item
   fetch** `GET …/items/{YYYYMMDD}-ch`: HTTP 200 → use it; **404 → genuine gap** (not published / aged out,
   e.g. 05-18/19), logged `reanalysis.day_gap` as today. Derive the id from the requested day; confirm the
   `{YYYYMMDD}-ch` format against the live collection.
2. **Daily asset-absent → gap, not raise (the edge race, Probe B).** On the **daily path only**, when the
   item is present (200) but the requested product's asset is not attached, treat it as a **day-gap**: log at
   **`warning`** (operator-visible, distinct from the routine 404 `day_gap`) and skip the day — do NOT raise.
   This is **uniform, not recency-gated** (no undefined "most-recent N days" threshold). Rationale: the only
   producer of this state is MeteoSwiss's item-then-asset publication lag, which by nature affects only the
   newest day(s); a genuine asset-*matcher* bug would instead fail for **every** day and surface as sustained
   warnings + a failed C1 effect-gate (below), so visibility is preserved without crashing the run. **The
   archive-path `AdapterError` is unchanged** — a missing archive asset is still a hard error.

*(Scope guard: this plan does NOT change `discover_product_boundary` / high-water-mark discovery. A stale or
drifted boundary is harmless once (1)+(2) hold — `discover_backfill_spans` just probes a wider span and any
day beyond real availability cleanly 404s to a gap. If boundary-drift is a real separate defect it is a
follow-up (128b), reviewed on its own merits — keeping this plan narrow.)*

## Tasks

- **A1 — id-fetch + daily asset-absent gap in `_fetch_day_feature`/`_rows_for_product`.** (1) fetch
  `items/{YYYYMMDD}-ch` (404 = gap); (2) daily item-present-but-product-asset-absent → `warning` + gap, no
  raise (archive path unchanged). Resolve grill-me #1 first.
- **A2 — regression tests (soundness-critical).** Simulate the real server in the fake handler and add:
  - **drifted-datetime** fixture: item id `{YYYYMMDD}-ch` with `properties.datetime` ~2 months forward;
    `items?datetime={day}`→0 features, `items/{YYYYMMDD}-ch`→200. **Must fail against current
    `_fetch_day_feature`**, pass after A1(1).
  - **third-state** fixture: `items/{YYYYMMDD}-ch`→200 but no `rprelimd` asset → a `warning` gap and **no
    raise / no crash of the daily loop**. **Must fail against current code** (which raises `AdapterError`).
  - **genuine-gap**: `items/{YYYYMMDD}-ch`→404 → no rows, no error (distinct from both above).
  - **Fix the now-stale day-gap fixtures** that used search-shaped `{"features": []}` to mean "no data for
    the day": `test_empty_stac_result_yields_no_rows` (`:459`) and the ingest self-containment test
    `tests/unit/flows/test_ingest_weather_history.py:967` — with id-fetch, a daily gap is a **404 on
    `items/{YYYYMMDD}-ch`**, not an empty search. Keep `{"features": []}` only for the boundary/discovery
    **search** endpoints. Extend the daily happy-path + RprelimD source-mapping tests (`:329,:945-959`).
- **C1 — staging effect-gate (write side).** On the mac-mini: **snapshot** `COUNT(*)` and `MAX(valid_time)`
  for the targeted staging stations where `source='meteoswiss_rprelimd'` and `parameter='precipitation'`;
  deploy the fixed image; trigger `ingest-weather-history`; **require a strict increase** (new `MAX(valid_time)`
  or higher count) — do NOT accept "recent rows exist" (pre-existing rows satisfy that) and do NOT rely on the
  aggregate weather-history OK status (`_horizon_advanced` is `any(...)` across all products,
  `ingest_weather_history.py:365,465`). Prerequisite: the staging stations carry the MeteoSwiss reanalysis
  binding + valid basins (115b §2A created these — verify). Consumption is Plan 129.
- **D1 — doc sync.** Record the RprelimD id-fetch behaviour, the ~2-month retention, and the
  item-then-asset edge race (asset-absent daily = warn+gap) in the adapter/reanalysis docs + weather-track.

## Grill-me (owner decisions before READY)

1. **Range-fallback in A1(1)?** After id-fetch, keep a bounded `?datetime=` range fallback for the day, or
   id-only? **Recommend id-only** — the `?datetime=` filter is the bug; a fallback re-introduces the drift
   blind spot.
2. **Deep RprelimD backfill?** Confirm we do NOT attempt to backfill deep RprelimD history (aged out; RhiresD
   covers deep history). Live-tail-only. **Recommend: confirmed, no deep backfill.**
3. **~~Item/asset atomicity~~ — RESOLVED by Probe B (2026-07-19): NON-atomic.** The latest item(s) exist
   before the RprelimD asset attaches, so the asset-absent-daily case is real and MUST be handled — as the
   uniform `warning`+gap of A1(2) (not the recency-tiered policy an earlier review draft over-built; not the
   status-quo `AdapterError`, which would crash the scheduled run). No open decision remains here.

## Tests

- **A2 drifted-datetime** — must fail red against current code.
- **A2 third-state** — item 200 / no asset → warn-gap, no crash; must fail red against current `AdapterError`.
- **A2 genuine-gap** — 404 → no rows, no error.
- **A2 stale-fixture conversions** — `test_empty_stac_result_yields_no_rows:459` +
  `test_ingest_weather_history.py:967` updated to 404-driven daily gaps.
- **Archive products unaffected** — RhiresD/TabsD/… still fetch unchanged; archive no-asset still raises.
- **C1 staging effect-gate** — RprelimD `COUNT`/`MAX(valid_time)` strictly increases after the run. *Must
  fail against the pre-Plan-128 image (0 new RprelimD rows).*

## Exit gates

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run pyright src/            # ratchet vs baseline
uv run pytest                  # incl. A2 drift + third-state + genuine-gap + the stale-fixture conversions
```
Plus the C1 staging effect-gate (RprelimD rows strictly increase on the mini).

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1",
      "name": "RprelimD id-fetch fix + asset-absent-daily gap",
      "tasks": ["A1-id-fetch-and-gap", "A2-regression-tests", "D1-doc-sync"],
      "parallel": false,
      "task_depends_on": {
        "A2-regression-tests": ["A1-id-fetch-and-gap"],
        "D1-doc-sync": ["A1-id-fetch-and-gap"]
      },
      "depends_on": []
    },
    {
      "id": "phase-2",
      "name": "Staging effect-gate (mac-mini)",
      "tasks": ["C1-staging-effect-gate"],
      "parallel": false,
      "depends_on": ["phase-1"]
    }
  ]
}
```

## Provenance

Root cause confirmed 2026-07-19 (Probe A: live STAC drift + code trace). Split from a combined fix +
consumption plan on the owner reframe (RprelimD = the temporal knit → Plan 129). A `plan`-workflow run
(2026-07-19) escalated after its planner over-built a recency-tiered tail-gap policy + bundled a boundary-drift
fix; that bloat was rejected, but the run surfaced a **real** issue — the item-then-asset publication race —
which **Probe B then confirmed live** (`20260719-ch` item present, asset absent). This plan folds the SIMPLE
correct handling (uniform `warning`+gap on the daily path), plus two genuine review findings (stale day-gap
fixtures; a real before/after C1 effect-gate). Boundary-drift discovery is left as a potential 128b follow-up.
DRAFT — owner grill-me (#1, #2) before READY; then build via `implement`.
