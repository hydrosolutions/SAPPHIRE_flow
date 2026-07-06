# Plan 100 — forecast-feed resilience: persist NWP-on across restarts + always-on fallback (no silent blackout)

**Status**: DRAFT
**Priority**: high — the operational forecast feed went **fully dark for ~3 days
(2026-07-03 → 07-06)** with every flow reporting green. Two independent gaps had
to line up, and both are still open.
**Phase**: v0b — operational reliability
**Parent**: Plan 091 (mac-mini NWP-on data collection); the `nwp_regression`
skill-comparison track
**Related**:
- `scripts/launchd/start-sapphire.sh:24-27` (boot/launchd auto-start — **omits**
  `docker-compose.macmini-nwp.yml`)
- `scripts/bootstrap-mac-mini.sh:110-117` (same overlay omission on `down`/`up`)
- `config/overlays/mac-mini.toml:1-2` (`[adapters.weather_forecast] enabled = false`)
- `config/overlays/mac-mini-nwp.toml:7-8` (`enabled = true`)
- `docker-compose.macmini.yml:24` / `docker-compose.macmini-nwp.yml:11`
  (`SAPPHIRE_CONFIG_OVERLAY` = mac-mini.toml vs mac-mini-nwp.toml)
- `src/sapphire_flow/flows/run_forecast_cycle.py:142-` (`_load_weather_forecast_adapter_config` — NWP gated **off** when `SAPPHIRE_CONFIG` unset **or** `[adapters.weather_forecast].enabled` absent → defaults `False`)
- `src/sapphire_flow/services/run_station_forecast.py:301-341` (priority first-success loop; **empty `results` → `primary_model_id=None` → no forecast row written**)
- `config.toml:56-64` (priority chain: `nwp_regression=10`, `nwp_rainfall_runoff=20`, `persistence_fallback=90`, `climatology_fallback=100`)
- `src/sapphire_flow/models/climatology_fallback.py`, `src/sapphire_flow/models/persistence_fallback.py` (fallbacks; **no NWP requirement**)
- `src/sapphire_flow/services/onboarding.py` (assigns models to stations at onboarding)
**Created**: 2026-07-06

---

## Problem (the 2026-07-03 → 07-06 blackout)

The forecast feed produced **zero** rows for ~3 days while `forecast-cycle`
completed green every 6 h. Root-caused live on the mini + dev:

1. **NWP-on was not persisted across restarts.** The mac-mini runs NWP-on only
   when the stack is brought up with **both** `docker-compose.macmini.yml` **and**
   `docker-compose.macmini-nwp.yml` (the latter sets
   `SAPPHIRE_CONFIG_OVERLAY=mac-mini-nwp.toml` → `enabled=true`). But the
   **auto-start** path (`start-sapphire.sh:24-27`, run on boot/launchd) brings the
   stack up with **only** `docker-compose.macmini.yml` → `mac-mini.toml` →
   `enabled=false`. NWP-on was set up manually and never written into the startup
   script. When the stack restarted on 07-03 (during the obs-ingest debugging),
   NWP silently reverted to **off**: the forecast-cycle ran runoff-only, emitted
   **no `nwp.*` logs**, archived no grids (the last grid is 07-03; Plan-095
   `nwp_grid_retention_days=3` then pruned them), and every NWP model returned
   `ModelFailure`.

2. **No graceful degradation — the feed has no floor.** Stations 2009/2091 were
   onboarded with **only** the two NWP models (`nwp_regression`,
   `nwp_rainfall_runoff`) for the skill comparison. When both fail, the
   first-success loop (`run_station_forecast.py:301-341`) leaves `results` empty
   and writes **nothing** — there is no implicit climatology synthesis. The
   priority chain in `config.toml:56-64` *lists* the fallbacks, but a fallback only
   runs if it is **assigned** to the station, and it was not.

Either gap alone is survivable; together they produced a silent multi-day outage.
MeteoSwiss was healthy throughout (STAC `updated` 07-06 08:44Z), so this was
entirely our deployment + resilience gap, not an external outage.

## Goal

1. **NWP-on survives every restart/reboot deterministically** — no manual overlay
   step, no way for a boot to silently drop NWP.
2. **The forecast feed can never go fully dark** — when the skill (NWP) models
   fail, a fallback always writes *something*, so the dashboard always has a
   current forecast (clearly labelled as a fallback).
3. **A silent NWP-off is detectable** — if NWP is expected but disabled, or no NWP
   grid has been archived in > N hours, monitoring surfaces it (ties to Flow 4).

## Design decisions (proposed; confirm in grill-me)

### Part A — persist NWP-on (fix gap 1)

- **A1 — startup scripts must include the NWP overlay.** Add
  `-f docker-compose.macmini-nwp.yml` to `start-sapphire.sh:24-27` and to the
  `up`/`down` invocations in `bootstrap-mac-mini.sh:110-117`. (Both currently stop
  at `docker-compose.macmini.yml`.)
- **A2 — GRILL-ME: fold NWP-on into the base mini overlay instead of a separate
  file?** NWP-on is now the intended steady state for the mini, so the two-overlay
  split is itself the footgun. Option (a): set `enabled = true` directly in
  `mac-mini.toml` and **delete** `mac-mini-nwp.toml` + `docker-compose.macmini-nwp.yml`
  — one overlay, impossible to "forget the -nwp file". Option (b): keep the split
  (useful to toggle NWP off for a runoff-only experiment) but rely on A1 + A3 to
  make the default safe. **Recommend (a)** unless we still need a first-class
  runoff-only toggle. Decide before READY.
- **A3 — startup assertion / fail-closed on unexpected NWP-off.** Add a config or
  env signal (e.g. `SAPPHIRE_REQUIRE_NWP=1` on the mini) so the worker **logs a
  loud warning (or refuses to start)** if NWP is expected but resolves to
  `enabled=false`. Prevents a future silent revert regardless of overlay wiring.
  GRILL-ME: warn-and-continue vs fail-closed.

### Part B — always-on fallback floor (fix gap 2)

- **B1 — assign a fallback to every operational station.** Onboarding
  (`services/onboarding.py`) should assign `climatology_fallback` (last-resort,
  priority 100) — and optionally `persistence_fallback` (90) — to **every** station
  by default, beneath whatever skill models are chosen. Skill models outrank it
  (config.toml:56-64 already encodes this), so normal operation is unaffected; the
  fallback only fires when the skill models fail. This makes "always produce a
  forecast" a structural guarantee, not a per-onboarding choice.
  - GRILL-ME: `climatology_fallback` only (simplest floor) vs `persistence_fallback`
    (90) **then** `climatology_fallback` (100) (persistence is better very-short-lead,
    climatology is the ultimate floor). Recommend both, in that order.
  - GRILL-ME: auto-assign at onboarding **for all future stations** vs a one-off
    backfill assignment for existing stations (2009/2091) vs both. Existing stations
    need an explicit backfill (a small migration / admin action) — auto-assign only
    covers new onboarding.
- **B2 — verify the fallbacks can actually produce.** `climatology_fallback`
  declares no NWP requirement (discharge climatology) and `persistence_fallback`
  needs only recent obs — confirm each has (or can build) its artifact for 2009/2091
  and returns a `ModelSuccess` under NWP-off. Their `input_requirement()` must be
  satisfiable from obs history alone (verify against the FI contract — a fallback
  that itself returns `ModelFailure` under NWP-off defeats the purpose).
- **B3 — the forecast must be clearly attributed as a fallback.** The dashboard +
  API already carry `primary_model_id`; ensure a fallback-sourced forecast is
  visibly labelled (so operators are not misled into trusting a climatology value
  as a skill forecast). Check the forecast list/detail templates.

### Part C — detectability (fix gap 3)

- **C1 — monitor "NWP expected but absent".** A lightweight check (Flow 4 pipeline
  monitoring, or a startup log assertion per A3) that alerts when: NWP is
  configured-expected but disabled, OR no NWP grid archived in > `expected cadence`
  (config `monitoring.expected_delivery_offset_hours=5`,
  `expected_cycles_per_day=4`), OR `weather_forecasts` has no new rows in > N hours.
  GRILL-ME: scope C into this plan vs defer to the Flow-4 monitoring plan and only
  reference here. (Leaning: minimal grid-staleness check here; full monitoring in
  Flow 4.)

## Non-goals

- Fixing the NWP fetch itself — it works; the outage was the config gate + missing
  overlay, not the fetcher.
- The observation-QC failures (`ingest.qc_complete failed=2`) — tracked in Plan 101.
- Reworking the priority/first-success algorithm — it is correct; the gap was an
  empty assignment set, not the loop.

## Verification (uses the local dev stack — now up)

- **Local repro of the floor:** onboard a test station with an NWP model + the
  fallback(s), bring the stack up NWP-**off** (or point the adapter at an empty
  cycle), run a `forecast-cycle`, and confirm a `climatology_fallback` row **is**
  written and labelled as fallback. Then NWP-**on** and confirm the skill model
  becomes primary.
- **Restart persistence:** confirm the patched `start-sapphire.sh` brings the mini
  up NWP-on from a cold boot (or fold-into-base per A2 makes it unconditional).

## Process

DRAFT until a grill-me settles: A2 (fold vs keep the -nwp overlay), A3 (warn vs
fail-closed), B1 (which fallback(s) + auto-assign vs backfill), C1 (scope of
monitoring here vs Flow 4). Then run through the `plan-review` workflow, then
phases → READY. Implementation is a **code change** (`onboarding.py`, the two
startup scripts, possibly the overlays + templates) → **hold-at-PR** with a
version bump, per CLAUDE.md.
