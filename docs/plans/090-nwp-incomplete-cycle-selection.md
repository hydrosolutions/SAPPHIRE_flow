# Plan 090 — NWP adapter: don't select an incompletely-published cycle

**Status**: DRAFT (parked — captured for prioritization; not yet actionable)
**Phase**: v0b operational hardening (NWP adapter robustness)
**Parent**: epic 088 (NWP-on forecasting) — surfaced during the 2026-07-02 live
onboarding of stations 2009/2091
**Related**: `adapters/meteoswiss_nwp.py` (`resolve_cycle` / `CycleResolution`,
`max_fallback_steps`), M4 provenance (`nwp_cycle_source`), `operational_inputs`
daily aggregation, Plan 089 (which recorded this as a sibling follow-up)
**Created**: 2026-07-02

---

## Problem (observed live, 2026-07-02)

MeteoSwiss publishes an ICON-CH2-EPS cycle **incrementally** — step-0 / early
lead-time GRIB items appear on the OGD object store first, later lead-times fill
in over ~an hour (see the existing note at `adapters/meteoswiss_nwp.py:78`,
"Newer cycles' step-0 items only surface after older cycles' items are…").

The adapter's cycle resolver snaps to the newest 6-hourly slot and walks back
(`max_fallback_steps`) only until it finds a cycle with **any** published items —
it does **not** check that the cycle has adequate **lead-time coverage**. So in
the live run it selected the freshly-published **06Z** cycle (only ~30 of ~120
hourly steps uploaded at fetch time) as `primary` over the **complete 00Z** cycle
(121 steps). After the daily aggregation dropped the ≤ issue-time bucket, only
**1** future daily step survived → the NWP forecast horizon was truncated to
1 step / 24 h instead of the intended 5 days.

Evidence (stored `weather_forecasts` at fetch time):

| cycle | hourly steps | valid range |
|---|---|---|
| 00Z (complete) | 121 | 07-02 00:00 → 07-07 00:00 (5 days) |
| 06Z (selected, incomplete) | 30 | 07-02 06:00 → 07-03 14:00 (~1.3 days) |

The daily aggregation / `_filter_and_cap_daily_records` are **correct** — the
defect is upstream in **cycle selection**: a newer-but-incomplete cycle is
preferred over a complete older one.

## Goal

The adapter selects a cycle only if it has **adequate lead-time coverage** for
the deployment's forecast horizon; otherwise it treats that cycle as
not-yet-available and falls back to the last complete cycle. Provenance
(`nwp_cycle_source`) continues to distinguish primary vs fallback correctly.

## Open design questions (for a grill-me before READY)

1. **Coverage criterion.** What counts as "complete enough"? Options: require the
   full published step count (≈120 h hourly); require ≥ the model's
   `forecast_horizon_steps` worth of lead-time after issue-time; or a configured
   minimum-coverage threshold. The daily models need only ~5 future days, so a
   horizon-relative check may suffice and is cheaper than demanding the full 120 h.
2. **Detection mechanism.** Determine coverage from the STAC item listing (count
   distinct lead-times / max step) BEFORE downloading, to avoid fetching a doomed
   partial cycle. Confirm the STAC/OGD listing exposes per-item lead-time.
3. **Interaction with `max_fallback_steps`.** An incomplete newest cycle should
   consume a fallback step (walk back to the previous complete cycle) — verify the
   fallback budget + the `CycleResolution.fallback_used` semantics still read
   correctly (this WOULD legitimately be `fallback_used=True`).
4. **Staleness vs completeness trade-off.** Falling back to 00Z when 06Z is
   partial trades ~6 h of freshness for a full horizon. Confirm that is the right
   call for daily models (almost certainly yes); note it may differ for a future
   sub-daily / nowcasting use case.
5. **Config surface.** Whether the coverage threshold is a `DeploymentConfig`
   value (operator-tunable) or derived from the forecast horizon.

## Non-goals

- Changing the daily aggregation / filter (they are correct).
- Sub-daily / nowcasting cycle handling (future).

## Affected surfaces (preliminary)

- `adapters/meteoswiss_nwp.py` — `resolve_cycle` / STAC item enumeration /
  `CycleResolution`; a coverage check before accepting a cycle.
- possibly `DeploymentConfig` — a coverage threshold.
- `flows/run_forecast_cycle.py` — provenance already threads `fallback_used`; a
  completeness-driven fallback should flow through unchanged.
- tests — a fake STAC listing with a partial newest cycle + a complete older one;
  assert the complete cycle is chosen and `fallback_used=True`.

## Process

DRAFT until a grill-me resolves the coverage criterion + detection mechanism,
then re-draft with phases + JSON dependency graph and flip DRAFT → READY per
`docs/workflow.md`. No implementation from DRAFT.
