---
status: DRAFT
created: 2026-09-26
revised: 2026-09-26
plan: 403
title: Give Swiss river stations their surveyed gauge-zero datum, so water level can be judged reading by reading
scope: Source each Swiss river station's surveyed gauge-zero elevation (BAFU "Pegelnullpunkt") from the hydrological yearbook, validate it against the station's own recent readings, store it through the existing `[onboarding.water_level_datums_masl]` mechanism, make CAMELS onboarding apply it to RIVER stations as it already does for lakes, and set it on the existing station rows without re-onboarding them. Once set, observation QC runs `range_check` on water level relative to the gauge zero, so a reading no longer needs a neighbour to be judged. NOT climatological baselines for water level (nothing computes them for rivers; recorded, no plan), NOT re-QC of stored history, NOT DHM/Nepal datums (their binding carries its own reference, `config/dhm.py`), NOT rating curves, NOT any threshold value, NOT lakes (already supported).
depends_on: []
blocks: []
related: [101, 272, 323, 340, 400]
open_decisions: []
source: 2026-09-26 — the owner, after the round-5 review of Plans 323/400 found that no Swiss river station has a water-level datum, so water level is checked only by rules that compare a reading with its neighbours, and a reading after any gap can be judged by nothing. Owner's words — "we could onboard the reference datum. that should be available in the station information in the hydrological yearbook." Number granted by the owner 2026-09-26 (402 is another session's). Code facts at `main` `2fe660e2`; the station count from staging data pulled 2026-09-25.
---

# Plan 403 — give Swiss river stations their surveyed gauge-zero datum

## Status

**DRAFT, unreviewed.** ⛔ No implementation until an independent review of this exact state is
complete and the orchestrator sets READY.

## Why this plan exists

BAFU delivers water level in **absolute metres above sea level** — ~261 m at 2091, ~376 m at 2009
(Plan 101, `docs/plans/archive/101-investigate-observation-qc-failures.md:52-57`) — while the
water-level `range_check` bounds (−2 … 20 m at 600 s, −5 … 30 m daily) describe **stage above the
gauge zero**. Plan 101 reconciled them by subtracting a per-station datum before QC and, where no
datum is known, **skipping** the datum-dependent rules (`range_check`, `gross_outlier`) "until the
datum is set" (`services/qc_datum.py:14-17,32-35`). The skip was meant to be temporary.

It never ended for rivers. CAMELS onboarding applies `[onboarding.water_level_datums_masl]` to LAKE
stations only and forces `None` for every RIVER (`adapters/camelsch_adapter.py:171` vs `:177`,
`:188`); `config.toml` holds no datum table at all. So on every Swiss river station water level is
checked only by `rate_of_change`, `spike` and (at 600 s) `frozen_sensor` — rules that need
neighbours. Plan 323 D4 makes an unjudgeable reading `QC_UNCHECKED` instead of silently passed, and
its D5 records those without an alarm; this plan removes the cause: with a datum, `range_check`
judges each reading on its own.

## What is measured

1. **Population.** 142 Swiss stations delivered `water_level` in the 4 days to 2026-09-25 (staging
   pull), against 139 delivering `discharge` and 64 `water_temperature`. ⇒ up to 142 datums.
2. **The mechanism exists end to end, except for rivers.**
   - Config: `[onboarding.water_level_datums_masl]` (and `water_level_units`) keyed by station code,
     documented at `docs/spec/config-reference.toml:261-272` as "surveyed gauge-zero elevation (BAFU
     Pegelnullpunkt)", parsed at `config/onboarding.py:159-168`.
   - Domain and store: `StationConfig.water_level_datum_masl` / `water_level_unit`
     (`types/station.py:53-54`), nullable columns (`db/metadata.py:236-237`, migration 0027), read
     at `store/station_store.py:370-371`, written by `store_station` / `update_station`.
   - Flow: `flows/onboard.py:221-246,309-310` → `services/onboarding.py` → `load_stations` →
     `adapters/camelsch_adapter.py`. ⚠️ `scripts/onboard.py:338-363` calls `onboard_from_camelsch`
     **without** the datum and unit maps — only the Prefect flow passes them.
3. **Updating an existing station today means re-running onboarding.** `services/onboarding.py:477+`
   replaces an existing station's row through `update_station`, which also overwrites name,
   location, `measured_parameters`, `forecast_targets` and `updated_at`
   (`store/station_store.py:156-175`), and onboarding re-runs its QC and baselines. There is no
   datum-only update path.
4. **What a river datum changes, consumer by consumer.**
   - **Observation QC** (`flows/ingest_observations.py:905-907`, `:466-485`): `range_check` and
     `gross_outlier` start running on water level, the rule version becomes `1.2-datum`, and flag
     details carry raw/relative/datum (`services/qc_datum.py:21-30,70-131`). `gross_outlier` stays a
     no-op: nothing computes river water-level baselines (§ 5). Only `RAW`/`QC_UNCHECKED` rows are
     judged; rows already stored keep their verdict.
   - **Forecast QC** (`flows/run_forecast_cycle.py:2662-2664` builds the map; consumers in
     `run_station_forecast.py`, `run_group_forecast.py`, `forecast_combination.py`): applies only to
     a `water_level` ensemble. River models forecast discharge ⇒ **no change** unless a water-level
     model is assigned to a river.
   - **Forecast evidence** (`services/forecast_evidence.py:242,315,375`): the datum appears in new
     evidence snapshots, so their content hashes differ from before. Existing records are untouched.
   - **Onboarding QC and baselines** run only for the station's first `forecast_targets` element —
     `discharge` for rivers (`services/onboarding.py:506-507,786-808,857-868`) ⇒ no change.
   - Rating curves, alerts, thresholds, the API: no datum reference in `src/` ⇒ no change.
5. **Water-level baselines.** The only computer is `services/baselines.py:16`, called only from
   onboarding for the target parameter, and skipped for a null datum (`services/onboarding.py:861-868`).
   After this plan river water level still has no baseline ⇒ `gross_outlier` remains inert there.
6. **No gauge-zero values exist in the repository** — no data file, fixture or table; the only
   number is a lake test fixture (`tests/unit/adapters/test_camelsch_adapter.py:169`). No test
   asserts that a river datum is `None`.

## Design

- **Source:** the station information of the BAFU hydrological yearbook, which lists each gauge's
  Pegelnullpunkt in m a.s.l. (owner, 2026-09-26). T1 confirms it covers the stations and records the
  edition and retrieval date per value.
- **Storage:** the existing `[onboarding.water_level_datums_masl]` and `[onboarding.water_level_units]`
  tables, `unit = "m a.s.l."` for each station given a datum. ⚠️ If T1 finds the publication's terms
  do not allow the values in a public repository, they go in the host overlay instead — a T1
  finding, not a design change.
- **Rivers use the table like lakes do:** the RIVER branches of `attributes_to_station` take the datum
  and unit from the maps when present and stay `None` otherwise. ⛔ `measured_parameters` and
  `forecast_targets` for rivers do not change.
- **Existing rows get the datum without re-onboarding.** An additive store method setting only
  `water_level_datum_masl` and `water_level_unit` for one station (Protocol, Pg, fake), and a small
  command that applies the config table to existing stations and reports each change. ⛔ Re-running
  onboarding for 142 stations is rejected: it rewrites unrelated metadata and re-runs onboarding QC
  (§ 3).
- **Validate before applying.** A wrong datum makes `range_check` fail **every** reading of that
  station. So each datum is checked against the station's own recent readings first: `value − datum`
  must fall inside the water-level `range_check` bounds for (nearly) all of them. A station that
  fails is not given the datum and is reported — it stays on today's skip path.
- `scripts/onboard.py` passes the datum and unit maps like the Prefect flow, so the two onboarding
  entry points cannot disagree.

## Owner decisions

None open. Closed by the owner on 2026-09-26: the datum is onboarded; its source is the
hydrological yearbook's station information.

## Tasks

### T1 — Source and validate the datums

**Outcome.** A per-station table of gauge-zero elevations, each traceable to its source and checked
against the station's own data.

**In.**
- For every Swiss station that delivers `water_level` (§ 1): the Pegelnullpunkt from the hydrological
  yearbook's station information, with the edition, the retrieval date, and the page or URL.
- Whether the publication's terms allow storing the values in this repository (decides Design's
  storage location).
- **Validation on staging, read-only:** per station, the distribution of `value − datum` over its
  measured water-level readings (`source = measured`) — minimum, 1st/50th/99th percentile, maximum —
  and the share inside the 600 s `range_check` bounds (−2 … 20). A station whose share is below 99%,
  or whose median is negative, is marked **suspect** and excluded, with the reason.
- Stations with no yearbook entry, or whose gauge zero changed during the period covered, listed
  separately.
- The query recorded here so it can be re-run.

**Out.** ⛔ Any write to staging. ⛔ Adjusting a published datum to fit the data — a mismatch is
reported, never "corrected".

**Pre-change.** N/A — sourcing and measurement.

**Verification.** Every value in T2's table traces to a T1 row with its source; a reviewer can re-run
the validation query and get the same shares.

### T2 — Apply the datum to rivers, at onboarding and to existing rows

**Outcome.** Every validated Swiss river station has its gauge-zero datum, both when onboarded fresh
and in the running stations table.

**In.**
- The validated values in `[onboarding.water_level_datums_masl]` / `[onboarding.water_level_units]`
  (or the host overlay, per T1), each with a comment naming its source.
- `adapters/camelsch_adapter.py`: the RIVER branches (`:177-178`, `:188-189`) take datum and unit
  from the maps, as the LAKE branch does (`:171-172`).
- `scripts/onboard.py:338-363` passes both maps.
- The additive datum-only store method (`protocols/stores.py`, `store/station_store.py`, the fake)
  and the command that applies the table to existing stations: dry-run by default, printing each
  station's old and new datum; applying only on an explicit flag; skipping and reporting any station
  whose code has no row.
- `docs/spec/config-reference.toml` — the example gains a river station, and the text says rivers
  now use it.

**Out.** ⛔ `measured_parameters`/`forecast_targets` for rivers. ⛔ Water-level baselines (§ 5).
⛔ Re-QC of stored rows. ⛔ DHM bindings.

**Pre-change.** A RED test: `attributes_to_station` for a river gauge with an entry in the datum map
returns `water_level_datum_masl is None` today — it must fail on that value.

**Verification.**
- River with a datum entry → the datum and `"m a.s.l."`; river without one → `None` (asserted, which
  no test does today); lake behaviour unchanged.
- The datum-only update changes those two columns and nothing else (every other column asserted
  equal before and after).
- The command's dry run writes nothing; the apply run sets exactly the validated stations.
- An ingest test: a river water-level reading with the datum set runs `range_check` on
  `value − datum` and gets version `1.2-datum`; one at `datum + 25 m` is `QC_FAILED`.

### T3 — Documentation

**Outcome.** The datum's source and how to add or correct one are written where an operator looks.

**In.** `docs/touchpoint-maps.md` (observation-ingest map: rivers now carry a datum; the datum-only
update command); Stage 1 QC (step 2.3) in `docs/architecture-context.md`; the station-metadata row
in `docs/handover/hydrology-operations.md` (~line 83) — the Swiss source; Plan 323's D4/D5 text
already names this plan as the fix.

**Pre-change.** N/A — documentation.

**Verification.** `grep -n "Pegelnullpunkt" docs/touchpoint-maps.md docs/architecture-context.md`
returns the new entries.

### T4 — Apply on staging and prove it

**Outcome.** Water-level readings at the validated stations are judged reading by reading, and the
unjudged leftover of Plan 323 D4 shrinks accordingly.

**In.** Apply the command on staging (the orchestrator's host), then over the first full day:
- `range_check` verdicts on water level at the validated stations — count of `QC_FAILED`; any
  station with more than a handful is reported with its readings (a wrong datum shows up here first).
- `observation_qc_unjudged` records (Plan 323 D5) for water level, before vs after.
- The share of water-level readings with a real verdict, before vs after.

**Out.** ⛔ Tuning thresholds in response.

**Pre-change.** The same figures for the day before applying.

**Verification.** Each figure recorded here beside its query.

## Explicitly out of scope

- **Climatological baselines for river water level** — nothing computes them (§ 5); `gross_outlier`
  stays inert for river water level. No plan owns it.
- **Re-QC of stored history** — the owner's standing answer (Plan 323, Plan 315 D3): leave it.
  Rows judged under the skip keep their `1.2-datum-skip` verdict.
- **DHM/Nepal** — their bindings declare `level_reference` and require a null datum for
  gauge-zero readings (`adapters/dhm.py:103-110`).
- **Water-level forecasts for rivers** — none are assigned today (§ 4); if one is, its forecast QC
  starts using the datum, which is the intended behaviour.
- **Threshold values** — the water-level bounds stay as they are (loose-first, `docs/v1-scope.md`).

```json
{
  "phases": [
    {"phase": 1, "tasks": ["T1"], "parallel": false},
    {"phase": 2, "tasks": ["T2"], "parallel": false},
    {"phase": 3, "tasks": ["T3", "T4"], "parallel": false}
  ]
}
```

## Review record

None yet.
