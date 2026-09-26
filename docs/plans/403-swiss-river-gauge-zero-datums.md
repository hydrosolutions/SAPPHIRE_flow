---
status: DRAFT
created: 2026-09-26
revised: 2026-09-26
plan: 403
title: Give Swiss river stations their surveyed gauge-zero datum, so water level can be judged reading by reading
scope: Source each Swiss river station's surveyed gauge-zero elevation (BAFU "Pegelnullpunkt") from the hydrological yearbook, classify and validate it against the station's own readings, store it through the existing `[onboarding.water_level_datums_masl]` mechanism, make CAMELS onboarding apply it to RIVER stations as it already does for lakes, and set it on existing station rows through a validated, tenant-checked, audited datum-only command rather than a re-onboarding. Once set, observation QC runs `range_check` on water level relative to the gauge zero, so a reading no longer needs a neighbour to be judged. NOT computing climatological baselines for water level (no plan), NOT re-QC of stored history, NOT DHM/Nepal datums (no plan owns them — Plan 323 D4), NOT rating curves, NOT any threshold value, NOT lakes (already supported).
depends_on: [323]
blocks: []
related: [101, 147, 272, 323, 340, 400]
open_decisions: []
source: 2026-09-26 — the owner, after the round-5 review of Plans 323/400 found that no Swiss river station has a water-level datum, so water level is checked only by rules that compare a reading with its neighbours, and a reading after any gap can be judged by nothing. Owner's words — "we could onboard the reference datum. that should be available in the station information in the hydrological yearbook." Number granted by the owner 2026-09-26 (402 is another session's). Code facts at `main` `2fe660e2`; the station count from staging data pulled 2026-09-25.
---

# Plan 403 — give Swiss river stations their surveyed gauge-zero datum

## Status

**DRAFT.** One review round has run (§ Review record), NOT READY, folded; **this fold is
unreviewed.** ⛔ No implementation until an independent review of this exact state is complete and
the orchestrator sets READY. T1–T3 may proceed independently; **T4 runs after Plan 323 is deployed**
(`depends_on`), because its before/after evidence reads Plan 323's `observation_qc_unjudged` records.

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
     at `store/station_store.py:370-371`, written by `store_station` / `update_station`. Supported
     units: `services/qc_datum.py:23`.
   - Flow: `flows/onboard.py:221-246,309-310` → `services/onboarding.py` → `load_stations` →
     `adapters/camelsch_adapter.py`. ⚠️ `scripts/onboard.py:339-365` calls `onboard_from_camelsch`
     **without** the datum and unit maps — only the Prefect flow passes them.
3. **Updating an existing station today means re-running onboarding**, which replaces the row through
   `update_station` — overwriting name, location, `measured_parameters`, `forecast_targets` and
   `updated_at` (`store/station_store.py:156-175`) — and re-runs onboarding QC and baselines. It does
   so under a resolved write principal, a tenant check (`services/onboarding.py:484-495`,
   `services/write_principal.py:107` `enforce_tenant_isolation`) and a station write + audit row in
   one transaction (`services/onboarding.py:440-459`, `docs/standards/security.md` § Tenant
   write-isolation). There is no datum-only update path.
4. **What a river datum changes, consumer by consumer.**
   - **Observation QC** (`flows/ingest_observations.py:905-907`, `:466-485`): `range_check` and
     `gross_outlier` start running on water level, the rule version becomes `1.2-datum`, and flag
     details carry raw/relative/datum (`services/qc_datum.py:21-30,70-131`). Only `RAW`/
     `QC_UNCHECKED` rows are judged; rows already stored keep their verdict.
   - **Forecast QC** (`flows/run_forecast_cycle.py:2662-2664` builds the map; consumers in
     `run_station_forecast.py`, `run_group_forecast.py`, `forecast_combination.py`): applies only to
     a `water_level` ensemble. River models forecast discharge ⇒ no change **unless** a water-level
     model is assigned to a river, in which case `range_check`/`negative_value` start running and a
     `QC_FAILED` drops that station's result. Not measured — T1 measures it.
   - **Forecast evidence** (`services/forecast_evidence.py:242,315,375`): the datum appears in new
     evidence snapshots, so their content hashes differ from before. Existing records are untouched.
   - **Onboarding QC and baselines** run only for the station's first `forecast_targets` element —
     `discharge` for rivers (`services/onboarding.py:506-507,786-808,857-868`) ⇒ no change.
   - Rating curves, alerts, thresholds, the API: no datum reference in `src/` ⇒ no change.
5. **Water-level baselines.** Plan 101 § 5 item 4 requires that when a datum goes from null to a
   value, the station's water-level `ClimBaseline` rows are deleted — an absolute-metre baseline
   compared with datum-shifted values would flag every reading. `delete_baselines` exists
   (`protocols/stores.py:1040`, `store/clim_baseline_store.py:44`) with no caller. From the code,
   river water-level baselines are never computed (only the target parameter is,
   `services/onboarding.py:861-868`), so none should exist — **not measured**; T1 measures it. After
   this plan river water level still has no baseline ⇒ `gross_outlier` stays inert there.
6. **No gauge-zero values exist in the repository** — no data file, fixture or table; the only
   number is a lake test fixture (`tests/unit/adapters/test_camelsch_adapter.py:169`). No test
   asserts that a river datum is `None`.

## Design

- **Source:** the station information of the BAFU hydrological yearbook, which lists each gauge's
  Pegelnullpunkt in m a.s.l. (owner, 2026-09-26). T1 records the edition and retrieval date per
  value.
- **Classify each station's series before choosing its datum.** Swiss gauges sit above ~190 m a.s.l.,
  so a LINDAS water-level series whose median is below that is **stage-relative**, not absolute. Such a
  station gets datum `0.0` with unit `m` (supported, `services/qc_datum.py:23`) — ⛔ not the yearbook
  value, which would push every reading ~−datum and fail it. An absolute series gets the yearbook
  value with unit `m a.s.l.`.
- **Validate, at apply time, every time.** A wrong datum fails every reading of that station. So the
  command computes, per station, the share of its recent measured readings whose `value − datum`
  falls inside the 600 s water-level `range_check` bounds (−2 … 20) and **refuses to apply** a
  datum below 99%, reporting it. The same check guards a datum added or corrected by hand later —
  it is part of the command, not a one-off analysis.
- **Storage:** the existing `[onboarding.water_level_datums_masl]` and `[onboarding.water_level_units]`
  tables. ⚠️ If T1 finds the publication's terms do not allow the values in a public repository,
  they go in the host overlay instead — a T1 finding, not a design change.
- **Rivers use the table like lakes do:** the RIVER branches of `attributes_to_station` take the datum
  and unit from the maps when present and stay `None` otherwise. ⛔ `measured_parameters` and
  `forecast_targets` for rivers do not change. `scripts/onboard.py` passes the maps like the flow.
- **Existing rows: a datum-only command, with the same guarantees as onboarding's writes.** An
  additive `StationStore` method setting only `water_level_datum_masl` and `water_level_unit`
  (Protocol, Pg, fake). The command: looks each station up by `(code, network="bafu")`
  (`protocols/stores.py:629`); resolves the configured write principal as onboarding does
  (`services/write_principal.py:41`); enforces the target station's tenant
  (`enforce_tenant_isolation`); runs the validation above; deletes the station's water-level
  baselines if any exist (Plan 101 § 5 item 4); and writes the datum, the baseline deletion and an
  audit row — a new `AuditEventType.STATION_DATUM_SET` (audit `event_type` is a text column,
  `db/metadata.py:2013`), carrying old and new datum and unit — **in one transaction**. Dry run by
  default. ⛔ Re-running onboarding for 142 stations is rejected: it rewrites unrelated metadata and
  re-runs onboarding QC (§ 3).

## Owner decisions

None open. Closed by the owner on 2026-09-26: the datum is onboarded; its source is the
hydrological yearbook's station information.

## Tasks

### T1 — Source, classify and measure

**Outcome.** A per-station table of datums, each traceable to its source, classified, and measured
against the station's own data — plus the three facts § 4 and § 5 left unmeasured.

**In.**
- For every Swiss station that delivers `water_level` (§ 1): the Pegelnullpunkt from the yearbook's
  station information, with edition, retrieval date, and page or URL; stations with no entry, or
  whose gauge zero changed during the period covered, listed separately.
- Whether the publication's terms allow the values in this repository (decides storage location).
- **Read-only, on staging:**
  - per station, the median of its measured (`source = measured`) water-level readings ⇒
    absolute or stage-relative (§ Design), and the datum that follows;
  - per station, the distribution of `value − datum` (min, 1st/50th/99th percentile, max) and the
    share inside −2 … 20 — the value the command's validation will compute;
  - any existing river water-level `clim_baselines` rows (§ 5);
  - any river station with a water-level model assignment (§ 4);
  - any river station that already has a datum set.
- The queries recorded here so they can be re-run.

**Out.** ⛔ Any write to staging. ⛔ Adjusting a published datum to fit the data — a mismatch is
reported, never "corrected".

**Pre-change.** N/A — sourcing and measurement.

**Verification.** Every value T2 stores traces to a T1 row with its source; a reviewer can re-run
the queries and get the same table.

### T2 — Apply the datum to rivers, at onboarding and to existing rows

**Outcome.** Every validated Swiss river station has its datum, both when onboarded fresh and in the
running stations table, through a write as guarded as onboarding's.

**In.**
- The T1 values in `[onboarding.water_level_datums_masl]` / `[onboarding.water_level_units]` (or the
  host overlay, per T1), each with a comment naming its source.
- `adapters/camelsch_adapter.py`: the RIVER branches (`:177-178`, `:188-189`) take datum and unit
  from the maps, as the LAKE branch does (`:171-172`).
- `scripts/onboard.py:339-365` passes both maps.
- The datum-only `StationStore` method (Protocol, Pg, fake) and the command of § Design, with its
  validation, tenant check, baseline deletion and atomic audited write; `AuditEventType.STATION_DATUM_SET`.
- `docs/spec/config-reference.toml` — the example gains a river station and the text says rivers now
  use it.

**Out.** ⛔ `measured_parameters`/`forecast_targets` for rivers. ⛔ Computing water-level baselines.
⛔ Re-QC of stored rows. ⛔ DHM bindings.

**Pre-change.** A RED test: `attributes_to_station` for a river gauge with an entry in the datum map
returns `water_level_datum_masl is None` today — it must fail on that value.

**Verification.**
- River with a datum entry → the datum and its unit; river without one → `None` (asserted, which no
  test does today); lake behaviour unchanged.
- The datum-only store method changes those two columns and nothing else (every other column
  asserted equal before and after).
- The command: dry run writes nothing; a station below 99% in range is refused and reported; a
  stage-relative station is given `0.0`/`m`; a target station under another tenant is rejected
  (and the rejection audited, as `enforce_tenant_isolation` does); an audit-write failure rolls the
  datum write back; existing water-level baselines are deleted in the same transaction; an unknown
  code is skipped and reported.
- An ingest test: a river water-level reading with the datum set runs `range_check` on
  `value − datum` and gets version `1.2-datum`; one at `datum + 25 m` is `QC_FAILED`.

### T3 — Documentation

**Outcome.** The datum's source, the command and how to add or correct a datum are written where an
operator and an implementer look.

**In.** `docs/spec/types-and-protocols.md` — the new `StationStore` method (its contract matching
Protocol, Pg and fake); `docs/touchpoint-maps.md` (observation-ingest map: rivers now carry a datum;
the datum command and its validation); Stage 1 QC (step 2.3) in `docs/architecture-context.md`; the
station-metadata row in `docs/handover/hydrology-operations.md` (~line 83) — the Swiss source and
the command as the way to add or correct a datum; Plan 323's D4/D5 text already names this plan.

**Out.** ⛔ Plan 101's archived text. ⛔ Documenting DHM datums.

**Pre-change.** N/A — documentation.

**Verification.** `grep -n "Pegelnullpunkt" docs/touchpoint-maps.md docs/architecture-context.md`
returns the new entries; the spec's method signature matches `protocols/stores.py`.

### T4 — Apply on staging and prove it (after Plan 323 is deployed)

**Outcome.** Water-level readings at the validated stations are judged reading by reading, and Plan
323's unjudged leftover shrinks accordingly.

**In.** Only once Plan 323 is deployed and has run a full day (its `observation_qc_unjudged` records
are the baseline). Record the day before applying, apply the command on staging (the
orchestrator's host), then record the first full day after:
- `range_check` verdicts on water level at the validated stations — count of `QC_FAILED`; any
  station with more than a handful is reported with its readings (a wrong datum shows up here first).
- Unjudged water-level readings — **distinct observation ids** from `observation_qc_unjudged`
  records (records count runs, Plan 323 D5), before vs after.
- The share of water-level readings with a real verdict (`QC_PASSED`, `QC_SUSPECT`, `QC_FAILED`),
  before vs after.

**Out.** ⛔ Tuning thresholds in response.

**Pre-change.** The "before" day, recorded first.

**Verification.** Each figure recorded here beside its query.

## Explicitly out of scope

- **Computing climatological baselines for river water level** — nothing computes them (§ 5);
  `gross_outlier` stays inert for river water level. No plan owns it.
- **Re-QC of stored history** — the owner's standing answer (Plan 323, Plan 315 D3): leave it.
  Rows judged under the skip keep their `1.2-datum-skip` verdict.
- **DHM/Nepal** — gauge-zero-referenced bindings require a null datum (`adapters/dhm.py:103-110`),
  so their water level stays neighbour-checked only. **No plan owns that** (Plan 323 D4 records it).
- **Threshold values** — the water-level bounds stay as they are (loose-first, `docs/v1-scope.md`).

```json
{
  "phases": [
    {"phase": 1, "tasks": ["T1"], "parallel": false},
    {"phase": 2, "tasks": ["T2"], "parallel": false},
    {"phase": 3, "tasks": ["T3"], "parallel": false},
    {"phase": 4, "tasks": ["T4"], "parallel": false}
  ]
}
```

## Review record

- **2026-09-26 — round 6 (this plan's first): independent Claude review and independent Codex review
  of `a2f32b5f`: both NOT READY; all findings folded, no decision needed.** Codex: the datum command
  lacked the tenant check and atomic audit every station write has (§ Design, T2); T4's evidence
  needs Plan 323 deployed (`depends_on`, T4); the store method was missing from the spec, and T3 had
  no Out. Claude: a stage-relative LINDAS series would have been excluded as "suspect" instead of
  given datum 0 / `m` (§ Design, T1); Plan 101 requires deleting water-level baselines when a datum
  is set (§ 5, command); validation ran once as analysis but the design promised it at apply time
  (→ part of the command); station lookup by `(code, "bafu")`; water-level model assignments to
  rivers unmeasured (T1); `scripts/onboard.py` citation corrected; DHM recorded as unowned.
