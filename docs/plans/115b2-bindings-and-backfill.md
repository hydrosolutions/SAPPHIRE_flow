---
status: DRAFT
created: 2026-07-15
plan: 115b2
parent: 115b
title: Bindings + chunked backfill — MeteoSwiss binding for all stations, 1981→present forcing through our polygons
scope: Create the reanalysis binding and populate historical_forcing. Writes ~100M rows. No live-forecast behaviour change.
depends_on: [115b1]
blocks: [115b3]
---

# Plan 115b2 — Bindings + chunked backfill

> **Design source: [Plan 115b](115b-weather-flow6-reachability.md)** — read §2 (binding for existing +
> new stations), §3 (definitive-over-preliminary is PRIORITY, not version supersession), §4 (MeteoSwiss
> binding, four pinned fields), and the §2 scale/chunked-backfill discussion. Carries **phases 2 and 3**.

## Status

**DRAFT.** Second chunk (115b1 → **115b2** → 115b3 → 115b4). Independent Codex review before READY.

## Why these two phases are one chunk

Binding and backfill are inseparable: **the adapter only processes configs that declare its
`nwp_source`**, so a backfill with no binding does nothing *and reports success*
(`ingest_weather_history.py:243`). Bind first, then backfill. Both write to `historical_forcing` but the
default reader is still `single`, and Flow 6's call shape was already rewritten to `fetch_products` in
115b1 (1G) — so **no live forecast behaviour changes** here; the risk is **scale and correctness of the
write path**, not production impact. *(This plan adds the binding that makes Flow 6's rolling ingest
non-empty; it does not touch the Flow 6 call itself.)*

## Scope

### Phase 2 — bindings first

- **2A — MeteoSwiss binding for the EXISTING fleet.** A one-shot data backfill inserting the binding for
  every eligible existing station (onboarding runs only at station-onboarding, so the deployed fleet has
  none). **Pin all four fields** the adapter matches on (`meteoswiss_open_data_reanalysis.py:159-166`):
  `nwp_source`, `role=REANALYSIS` (115a), `status=ACTIVE`,
  `extraction_type=BASIN_AVERAGE` — the adapter's four-field match at `meteoswiss_open_data_reanalysis.py:159-166`. Missing any one leaves the feed dark *and green*.
- **2B — onboarding writes the binding** for new stations, so both paths agree.
- **2C — onboarding per-station backfill or hold (round-5 blocker 3).** A binding alone gives a NEW station
  **zero forcing rows** — onboarding still imports CAMELS only (`onboarding.py:341,365`). Onboarding must
  run the per-station MeteoSwiss backfill **before** the station is promoted operational/trainable, or
  explicitly **hold it out** until it has. Task + test, not just the 2B binding write.

### Phase 3 — chunked, resumable backfill 1981 → present (our polygons)

The existing path is **all-in-memory end to end** and cannot do this scale (adapter accumulates all `rows`
and returns one list `meteoswiss_open_data_reanalysis.py:170,198`; Flow 6 stores only after the full fetch
`:318`; the store copies the whole input before 5k-row SQL batches `historical_forcing_store.py:35`). Build
a dedicated chunked path:

- **3A — work units = (product, year, station-batch).** Uses 115b1's `fetch_products` + archive support.
- **3B — per-chunk persistence** (write each chunk before the next; never hold the full series in memory).
- **3C — resumable gap detection (gap key SPECIFIED — round-1 blocker).** "Fills only what is missing"
  needs an exact key. The `historical_forcing` **unique** key includes `version` (`db/metadata.py:419`), but
  latest-row reads collapse by the **logical** key *excluding* version (`historical_forcing_store.py:91`).
  So **gap detection keys on the LOGICAL key**: `(station_id, source, valid_time, parameter, spatial_type,
  band_id, member_id)` — a date is "present" if a row exists for that logical key regardless of version. A
  re-run inserts only logical keys with no existing row; `on_conflict_do_nothing` makes a duplicate insert a
  no-op, so the backfill is idempotent. **Version policy:** the initial backfill writes version 1; a *later*
  reprocessed MeteoSwiss file for the same logical key is a **within-source** supersession (a higher version
  of the same product), handled by the existing latest-version mechanism — NOT a cross-source concern, and
  NOT something the initial backfill must handle. Re-run interruptible, because it will be interrupted.
- **3D — eligible stations only.** "Every operational station" means **every station with a valid basin
  polygon**. `ExactExtractGridExtractor` **skips** invalid-geometry stations and raises only if none are
  valid (`exact_extract_grid_extractor.py:64-89`), so a station silently missing a polygon would be
  silently dropped — **pre-enumerate** the eligible set and **log** any operational station excluded.

**The split rule (from 115b1's §0a), as HALF-OPEN ranges bounded by PER-PRODUCT availability (round-1
blocker 2 — do NOT use a single `T`):** each product has a **published high-water mark** = its latest
available valid date, **discovered from STAC per product** (extend 1D's `R`-discovery to every product; the
backfill must never request dates a product does not yet serve — "today" can be *later* than the latest
RprelimD). Then, with `hwm(p)` the high-water mark of product `p`:
- precipitation: `RhiresD` over `[1981-01-01, R+1d)`, `RprelimD` over `[R+1d, hwm(rprelimd)+1d)` — half-open,
  **disjoint by construction**, and leap-years / mid-month `R` fall out for free.
- `TabsD` / `TminD` / `TmaxD` / `relative_sunshine_duration` (`SrelD`): one product each over
  `[1981-01-01, hwm(p)+1d)`.
Definitive-over-preliminary at read time is the hybrid PRIORITY chain (115b4), **not** a write-time
overwrite — both rows coexist (§3).

**Scale:** the durable archive is **5 full-depth product series** (RhiresD, TabsD, TminD, TmaxD, SrelD ×
~16,630 days) **+ a short RprelimD live tail** — so ~5 × ~16,630 × ~1000 ≈ **~80-100M rows** at the v0
~1000-station target (RprelimD adds only the ~8-week tail, not a sixth full series). A supervised batch job,
not a request path.

## Tests

- **Bind-before-backfill / MIGRATED data (§4):** an existing station gets the four-field binding and the
  backfill ingests, asserted via an **advancing `MAX(valid_time)` per source** (NOT `rows_stored` — see
  115b4 §7). *Soundness: fails against a backfill run with no binding (returns 0/0/0).*
- **Chunked + resumable (3A–3C):** a backfill interrupted mid-run and re-started fills only the missing
  (product, year, station) chunks; no chunk is written twice, none skipped.
- **Eligible-stations pre-enumeration (3D):** an operational station with no basin polygon is **logged and
  excluded**, not silently dropped; a run over N eligible stations writes exactly N stations' rows.
- **Split-rule coverage (backfill window):** the historical backfill writes `RhiresD` over `[1981,R]` and
  `RprelimD` over `[R+1d,T-1d]`, disjoint, under their own source tags.
- **New-station onboarding (2C):** a station onboarded after this plan gets forcing rows (or is held out of
  operational), never a binding with zero rows.
- **Resumability gap key (3C):** a backfill interrupted after writing some logical keys and re-run writes
  ONLY the missing logical keys (excluding `version`); a full re-run over already-complete data is a no-op
  (`on_conflict_do_nothing`), writing zero new rows. *Soundness: fails against gap detection that keys on the
  full unique key including version.*
- **Per-product high-water mark (3B/3C):** the backfill requests no date beyond a product's STAC-published
  latest valid date; when "today" is later than the latest RprelimD, no phantom rows are generated for the
  gap. *Soundness: fails against a fixed `T-1d` bound.*

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-2",
      "name": "Bindings first + per-station onboarding backfill",
      "tasks": ["2A-backfill-meteoswiss-binding", "2B-onboarding-writes-binding", "2C-onboarding-per-station-backfill-or-hold"],
      "parallel": false,
      "depends_on": ["plan-115b1"]
    },
    {
      "id": "phase-3",
      "name": "Chunked, resumable 1981->present backfill through our polygons",
      "tasks": ["3A-chunked-work-units", "3B-per-chunk-persistence", "3C-resumable-gap-detection", "3D-eligible-stations-only"],
      "parallel": false,
      "depends_on": ["phase-2"]
    }
  ]
}
```

## Exit gates

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run pyright src/
uv run pytest
```

**Deploy gate (staging, do not skip):** run the backfill for the 2 staging stations; confirm
`historical_forcing` gains `meteoswiss_rhiresd`/`tabsd`/`tmind`/`tmaxd`/`sreld` (and `rprelimd` for the
tail) with `MAX(valid_time)` advancing to ~T-1d. **A green flow is not evidence** — check the rows.

## Provenance

Extracted from Plan 115b (phases 2–3), 2026-07-15.
