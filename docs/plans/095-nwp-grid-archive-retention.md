# Plan 095 — NWP grid-archive retention (prune old cycle zarrs)

**Status**: DRAFT
**Priority**: medium — real disk-fill risk for long-running deployments
(unbounded ~7 GB/day accumulation); surfaced during the 2026-07-03 Mac-mini
data-collection deployment.
**Phase**: v0b — Flow 11 (NWP archive management) / operational hardening
**Related**: `store/zarr_nwp_grid_store.py` (`_cleanup_stale_artifacts`,
`archive`), the `nwp_grids` named volume, Flow 11 (NWP archive management), the
`weather_forecasts` table (the permanent extracted-value archive), Plan 046
(disk sizing), Plan 090 P2 (cycle reuse)
**Created**: 2026-07-03

---

## Problem

Each `forecast-cycle` archives the **full ICON grid cube (~1.7 GB zarr)** per
distinct cycle to `/data/nwp_grids`. `_cleanup_stale_artifacts`
(`store/zarr_nwp_grid_store.py:51`) prunes **only stale *versions* of the
current `cycle_stem`** (plus `*_tmp` / `.zarr.old`) — it does **not** prune old
cycles (a different `cycle_stem`, e.g. yesterday's `20260702T00.zarr`). So
distinct cycle archives accumulate **unbounded**: ~4 cycles/day × ~1.7 GB ≈
**~7 GB/day → ~50 GB/week**. Plan 046 sized the Docker VM at ≥100 GB → **~1-2
weeks** of headroom before disk pressure → `nwp.archive` / forecast failures.

The **permanent** NWP archive is the **extracted basin-average values** (the
`weather_forecasts` DB table; architecture: "extracted values, not raw GRIB2,
permanent retention") — **not** the full grid cube. So old grid zarrs are safe
to prune **after** their values are extracted + persisted.

## Goal

Bound the `nwp_grids` disk footprint: prune grid-cube zarrs older than a
configured retention window, keeping only recent cycles (those still reachable by
the fallback budget / a recent readback). The permanent extracted-value archive
(DB) is never touched.

## Open design questions (grill-me before READY)

1. **Retention window.** `nwp_grid_retention_days` (default ~2-3 days). Must be
   **≥ the fallback budget** (`nwp_max_fallback_age_hours = 12`) + margin, so a
   cycle that could still be *selected* or *read back* is never pruned.
2. **Trigger site.** (a) prune old cycles in the `archive()` path right after the
   version swap (cheap, no new flow); (b) a dedicated **Flow 11 (NWP archive
   management)** maintenance flow on a cron; (c) both — (a) inline + (b) as a
   backstop. Prefer (a) + a cron backstop.
3. **Safety criterion.** A cycle is prunable iff its extracted values are already
   persisted (its forecasts stored) AND it is older than the window AND no live
   readback needs it. Confirm the readback path can never request a pruned cycle.
4. **Config surface.** `DeploymentConfig.nwp_grid_retention_days`
   (operator-tunable); document in config-reference.
5. **Interaction with Plan 090 P2** (archive/reuse a fetched cycle): retention
   must not prune a cycle P2 would reuse — reconcile the windows.

## Non-goals

- Pruning the extracted basin-average values (permanent, in the DB).
- The full-cube **re-download-every-cycle** inefficiency (separate — Plan 090 P2
  territory; a fetched-cycle cache would also cut network cost).

## Interim operational mitigation (until this ships)

On a long-running deployment, **monitor disk** (`df -h`, or the Docker VM disk
usage) and, if it pressures, manually remove old cube archives —
`rm -rf /data/nwp_grids/icon_ch2_eps/<old YYYYMMDDTHH>.zarr` for cycles whose
values are already in `weather_forecasts`. Do **not** touch the DB or the
`weather_forecasts` rows (the permanent archive).

## Process

DRAFT until a grill-me resolves the retention window + trigger site + safety
criterion, then phases + JSON graph → READY. Small change in
`store/zarr_nwp_grid_store.py` + `DeploymentConfig`; RED-confirmed test that a
cycle older than the window (with values persisted) is pruned while recent
cycles + the extracted DB values are kept.
