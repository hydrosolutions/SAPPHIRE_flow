---
status: DRAFT
created: 2026-09-26
revised: 2026-09-26
plan: 403
title: Give Swiss river stations their surveyed gauge-zero datum, so water level can be judged reading by reading
scope: Source each Swiss river station's surveyed gauge-zero elevation (BAFU "Pegelnullpunkt") from the hydrological yearbook, classify and validate it against the station's own readings, store it through the existing `[onboarding.water_level_datums_masl]` mechanism, make CAMELS onboarding apply it to RIVER stations as it already does for lakes, and set it on existing station rows through a validated, tenant-checked, audited datum-only command rather than a re-onboarding. Once set, observation QC runs `range_check` on water level relative to the gauge zero, so a reading no longer needs a neighbour to be judged. NOT computing climatological baselines for water level (no plan), NOT re-QC of stored history, NOT DHM/Nepal datums (no plan owns them — Plan 323 D4), NOT rating curves, NOT any threshold value, NOT lakes (their datum route is re-onboarding, which this plan keeps).
depends_on: [323]
blocks: []
related: [101, 147, 218, 272, 323, 340, 400]
open_decisions: []
source: 2026-09-26 — the owner, after the round-5 review of Plans 323/400 found that no Swiss river station has a water-level datum, so water level is checked only by rules that compare a reading with its neighbours, and a reading after any gap can be judged by nothing. Owner's words — "we could onboard the reference datum. that should be available in the station information in the hydrological yearbook." Number granted by the owner 2026-09-26 (402 is another session's). Code facts at `main` `2fe660e2`; the station count from staging data pulled 2026-09-25.
---

# Plan 403 — give Swiss river stations their surveyed gauge-zero datum

## Status

**DRAFT.** Five review rounds have run (§ Review record, rounds 6-10); **round 10 was READY from
both reviewers**, with LOW findings folded since — **this fold is unreviewed.** ⛔ No implementation until an independent review of this exact state is complete and
the orchestrator sets READY. **Implementation follows Plan 323** (`depends_on`): T4's before/after
evidence reads 323's `observation_qc_unjudged` records. T1's sourcing needs no code and no deploy,
so the yearbook values may be gathered earlier.

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
   ⚠️ **Departure from Plan 101, stated:** 101 § 5 item 4
   (`docs/plans/archive/101-investigate-observation-qc-failures.md:479-484`) asks for delete **and
   recompute** of the baselines **and re-QC** of affected rows on a datum change. This plan deletes
   only: nothing computes river water-level baselines to recompute, and re-QC of stored history is
   closed by the owner (leave it).
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
  command computes, per station, over its measured (`source = measured`) water-level readings of the
  last 14 days, the share whose `value − datum` falls inside the 600 s water-level `range_check`
  bounds (−2 … 20), and **refuses to apply** a datum below 99% — or when fewer than 100 readings
  exist ("insufficient data") — reporting either. The same check guards a datum added or corrected
  by hand later: it is part of the command, not a one-off analysis.
- 🔴 **For an existing RIVER station, the command is the only way its datum changes.** Re-running
  onboarding replaces an existing station from the maps (`services/onboarding.py:503`) without
  checking any readings, so it could overwrite a validated datum with an unchecked one. ⇒ When the
  **existing row is `StationKind.RIVER`**, onboarding's update keeps that row's
  `water_level_datum_masl` and `water_level_unit`; it applies the maps to a river it creates (no
  readings yet to validate against). ⛔ **Lakes keep today's behaviour** — re-onboarding still
  fills or corrects a lake's datum from the maps (the route `docs/spec/config-reference.toml:264`
  documents and Plan 101 § 5 item 4 names), since the command does not handle lakes.
- **Storage:** the existing `[onboarding.water_level_datums_masl]` and `[onboarding.water_level_units]`
  tables in `config.toml`. ⚠️ This repository is public, and so is the staging host overlay
  (`config/overlays/mac-mini.toml` is tracked and bind-mounted from the checkout). ⇒ **If T1 finds
  the publication's terms do not allow publishing the values, T1 stops and escalates to the owner
  before T2** — an untracked overlay would be a deploy change this plan does not carry.
- **Rivers use the table like lakes do:** the RIVER branches of `attributes_to_station` take the datum
  and unit from the maps when present and stay `None` otherwise; a datum with no unit entry defaults
  to `m a.s.l.`, as the lake branch does (`adapters/camelsch_adapter.py:172`). ⛔ `measured_parameters` and
  `forecast_targets` for rivers do not change. `scripts/onboard.py` passes the maps like the flow.
- **Existing rows: a datum-only command, with the same guarantees as onboarding's writes.**
  - **What it is:** `scripts/set_water_level_datums.py`, added to the Dockerfile's curated
    operator-script list (`Dockerfile:145-151`, Plan 218) so it can run in the staging container.
    Flags as the `create_station_group.py` precedent and `docs/standards/security.md` § Tenant
    write-isolation: `--apply` (dry run without it), `--tenant`, `--operator`.
  - **Input:** the `[onboarding.water_level_datums_masl]` / `water_level_units` tables — already
    classified in T1 (§ above). The command does not classify; it **validates** each entry against
    the station's readings (§ above) and refuses what fails.
  - **Population: RIVER stations only.** Each entry is looked up by `(code, network="bafu")`
    (`protocols/stores.py:629`); a station that is not `StationKind.RIVER` is skipped and reported —
    ⛔ lakes' datum route is re-onboarding, which this plan keeps, and a lake that has a datum may
    have real water-level baselines (onboarding computes them once a datum exists,
    `services/onboarding.py:863-869`) that must never be deleted. Whether any lake has a datum today
    is unmeasured — T1 measures it. An unknown code is skipped and reported.
  - **Units are validated — for a river carrying a datum.** The command and the new-river
    onboarding path refuse a datum whose unit is outside `SUPPORTED_WATER_LEVEL_UNITS`
    (`services/qc_datum.py:23`); a river with no datum keeps unit `None`, as today
    (`adapters/camelsch_adapter.py:178,189`). Onboarding's own unit guard
    (`services/onboarding.py:464-473`) fires only when `water_level` is a forecast target, which it
    never is for rivers, and `config/onboarding.py:168` accepts any string — so without this a river
    entry in `cm` would be stored, contradicting `docs/spec/config-reference.toml:269-271`.
  - **Tenant:** resolves the configured write principal as onboarding does
    (`services/write_principal.py:41`) and enforces the target's tenant
    (`enforce_tenant_isolation`, `:107`). The dry run uses a write-free pre-check, as
    `create_station_group.py` does (`plan_station_group`, `scripts/create_station_group.py:152-156`)
    — `enforce_tenant_isolation` writes a rejection audit row, which a dry run must not. 🔴 **On
    apply, the check runs on an autocommit connection before the write transaction opens**, as
    `scripts/create_station_group.py:315-329` does: `enforce_tenant_isolation` appends the rejection
    row and then raises (`services/write_principal.py:143-157`), so inside `engine.begin()` the raise
    would roll its own audit row back. A rejection **aborts the whole run**, as onboarding does
    (`services/onboarding.py:529-532`).
  - **One transaction, named:** a single `engine.begin()` connection
    (`scripts/create_station_group.py:360` precedent) carrying `PgStationStore`,
    `PgClimBaselineStore` and `PgAuditLogStore`, in which it writes the datum and unit through an
    additive datum-only `StationStore` method (Protocol, Pg, fake), deletes the station's
    water-level baselines if any exist (Plan 101 § 5 item 4 — `delete_baselines`,
    `protocols/stores.py:1040`), and appends an audit row with a new `AuditEventType.STATION_DATUM_SET`
    carrying old and new datum and unit. `make_audited_stores` (`store/audited_writer.py:27-54`)
    carries no baseline store, which is why it is not the mechanism here.
  - **Dry-run output, per station:** old and new datum and unit, the in-range share and reading
    count, the baselines that would be deleted, and every refusal with its reason.
  - **Clearing a wrong datum:** `--clear <code>` sets a river station's datum and unit back to
    `None` — no validation (there is nothing to validate against), same tenant check, same single
    transaction and a `STATION_DATUM_SET` audit row recording old → none. It honours `--apply` like
    everything else (a dry run without it), and a lake or unknown code is refused and reported.
    A `--clear` run does **only** the clear — it does not process the tables — and it **refuses**
    while the code still has an entry in `[onboarding.water_level_datums_masl]`: otherwise the next
    ordinary run would re-validate that entry and put the wrong datum back (a datum can pass the
    14-day, 99% gate and still show up in T4's one-day check). The operator removes the entry in the
    same change. Without `--clear`, onboarding can no longer change a river's datum and an absent
    table entry does nothing, so a wrong datum found in T4 would have no way back.
  - ⛔ Re-running onboarding for 142 stations is rejected: it rewrites unrelated metadata and re-runs
    onboarding QC (§ 3).

## Owner decisions

None open. Closed by the owner on 2026-09-26: the datum is onboarded; its source is the
hydrological yearbook's station information.

## Tasks

### T1 — Source, classify and measure

**Outcome.** A per-station table of datums, each traceable to its source, classified, and measured
against the station's own data — plus the three facts § 4 and § 5 left unmeasured.

**In.**
- 🔴 **First, before any value is written anywhere in the repository:** whether the publication's
  terms allow publishing the values (this repository is public, and so is this plan). If not, stop
  and escalate to the owner before recording any value (§ Design, Storage).
- For every Swiss **river** station that delivers `water_level` (§ 1; lakes excluded — their route is
  re-onboarding): the Pegelnullpunkt from the yearbook's station information, with edition,
  retrieval date, and page or URL; stations with no entry, or whose gauge zero changed during the
  period covered, listed separately.
- **Read-only, on staging:**
  - per station, the median of its measured (`source = measured`) water-level readings ⇒
    absolute or stage-relative (§ Design), and the datum that follows;
  - per station, the distribution of `value − datum` (min, 1st/50th/99th percentile, max) and the
    share inside −2 … 20 — the value the command's validation will compute;
  - any existing river water-level `clim_baselines` rows (§ 5);
  - any river station with a water-level model assignment (§ 4);
  - any river station that already has a datum set;
  - every **lake** station's datum and water-level baseline status. ⚠️ If lakes have no datum
    either, Plan 323 D4's unjudged leftover covers them too, and **no plan fills the lake table** —
    record that beside this plan's DHM bullet in § Explicitly out of scope, as an unowned gap.
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
- The T1 values in `[onboarding.water_level_datums_masl]` / `[onboarding.water_level_units]`, each
  with a comment naming its source.
- `adapters/camelsch_adapter.py`: the RIVER branches (`:177-178`, `:188-189`) take datum and unit
  from the maps, as the LAKE branch does (`:171-172`).
- `scripts/onboard.py:339-365` passes both maps.
- The datum-only `StationStore` method (Protocol, Pg, fake) and the command of § Design, with its
  validation, river-only population, tenant pre-check and apply-time check, baseline deletion,
  single-transaction audited write and `--clear`.
- The curated operator-script list, locked in five places (the test's docstring,
  `tests/unit/deploy/test_dockerfile_operator_scripts.py:195-202`, names them): `Dockerfile:145-151`;
  `_CURATED_SCRIPTS` in that test (`:143`, exact-set equality); the "Smoke-check operator scripts"
  step in `.github/workflows/ci.yml` (~`:771-782`); and, for T3, `docs/touchpoint-maps.md:842` and
  `docs/deployment/mac-mini-staging.md:772`.
- `AuditEventType.STATION_DATUM_SET`, with the two places that lock that enum:
  `tests/unit/types/test_enums.py:95-121` (member count and expected set) and the spec enum in
  `docs/spec/types-and-protocols.md:312-331` — the test's docstring requires all three together.
- Onboarding on **update of an existing river** keeps its datum and unit (§ Design); lakes
  unchanged. `docs/spec/config-reference.toml:264`'s sentence says so.
- Onboarding's unit guard (`services/onboarding.py:464-473`) extended to a river that carries a
  datum (§ Design, Units).
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
- The command (unit, with fakes): dry run writes nothing, audit included; a station below 99% in
  range, or with fewer than 100 readings, is refused and reported; a stage-relative entry (`0.0`,
  `m`) validates and applies; a lake entry in the same tables is skipped and its datum and baselines
  are untouched; an unknown code is skipped and reported; `--clear` sets a river's datum and unit to
  `None` with an audit row, writes nothing without `--apply`, refuses a lake, processes no table
  entries, and refuses while the code still has a table entry; a river entry with unit `cm` is
  refused by both the command and new-river onboarding, while a new river with no datum onboards
  with unit `None`.
- **Atomicity and the rejection audit, against real Postgres** (integration test — fakes have no
  transactions): with the audit insert forced to fail, both the original datum and the existing
  water-level baseline rows are still there afterwards; on success, all three changes are present;
  a target under another tenant aborts the run, writes nothing, and its **rejection audit row
  survives**.
- **Onboarding cannot bypass validation, and lakes keep their route:** re-onboarding an existing
  river station with a different datum in the maps leaves its stored datum unchanged; onboarding a
  new river applies the map value; re-onboarding an existing **lake** with a changed map datum still
  updates it.
- An ingest test: a river water-level reading with the datum set runs `range_check` on
  `value − datum` and gets version `1.2-datum`; one at `datum + 25 m` is `QC_FAILED`.

### T3 — Documentation

**Outcome.** The datum's source, the command and how to add or correct a datum are written where an
operator and an implementer look.

**In.** `docs/spec/types-and-protocols.md` — the new `StationStore` method (its contract matching
Protocol, Pg and fake) and the audit enum; `docs/touchpoint-maps.md` (observation-ingest map: rivers
now carry a datum; the datum command and its validation); Stage 1 QC (step 2.3) in
`docs/architecture-context.md`; `docs/handover/hydrology-operations.md` § "Observation ingest and
quality control" and § 10 "Observation QC — What the Flags Mean" — the Swiss source, and the command
as the only way to add, correct or clear an **existing Swiss river station's** datum (a new river
takes it at onboarding; lakes keep re-onboarding); the write-path lists in `docs/standards/security.md`
(`:74-77`, `:86`, and § Tenant write-isolation `:109-113`) and the
`services/write_principal.py` module docstring (`:9-14`) — the command is a new write chokepoint;
the audited-call-site list in `docs/spec/types-and-protocols.md` (~`:1389-1396`); the operator-script
inventories in `docs/touchpoint-maps.md:842` and `docs/deployment/mac-mini-staging.md:769-801`
(the script list, "all six scripts require `DATABASE_URL`" at `:779`, and the per-script
`SAPPHIRE_CONFIG` note at `:801`).
Plan 323's D4/D5 text already names this plan.

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
  station where 1% or more of that day's water-level readings failed is reported with its readings
  (a wrong datum shows up here first).
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
- **2026-09-26 — round 7: independent Claude review and independent Codex review: both NOT READY;
  all findings folded, no decision needed.** Both: the "one transaction" had no mechanism —
  `make_audited_stores` carries no baseline store (→ one `engine.begin()` connection with the three
  Pg stores; real-Postgres atomicity test covering the baselines too). Codex: re-running onboarding
  could overwrite a validated datum unchecked (→ onboarding keeps an existing datum; the command is
  the only way to change one); the shared tables could pull lakes into baseline deletion (→ rivers
  only). Claude: the command was unspecified — location, Dockerfile script list, input, flags,
  dry-run output, and a dry run that would still write a tenant-rejection audit row (→ § Design);
  the audit enum is locked by a count test and the spec; security.md and the `write_principal`
  docstring list every chokepoint; Plan 101 also asks for recompute and re-QC (departure stated);
  T3 pointed at the DHM table; frontmatter vs prose on the dependency; thresholds and the validation
  window numbered; a datum without a unit defaults to `m a.s.l.`.
- **2026-09-26 — round 8: both NOT READY for this plan (323 and 400 READY from both); all findings
  folded, no decision needed.** Both: keeping the existing datum on every onboarding update froze
  lakes, whose only fill/correct route is re-onboarding (→ rivers only; a lake re-onboarding test).
  Codex: the operator-script list is locked in five places (→ all named). Claude: the apply-time
  tenant rejection would be rolled back inside the write transaction (→ autocommit check before
  `engine.begin()`, abort the run, real-Postgres test that the rejection row survives); the fallback
  storage — the host overlay — is public too (→ stop and escalate if publishing is not allowed); no
  way to clear a wrong datum (→ `--clear`); the audited-call-site list in the spec.
- **2026-09-26 — round 9: Codex READY; Claude NOT READY (two MEDIUM); all folded, no decision
  needed.** Claude: "lakes already carry datums" was unmeasured and the code suggests otherwise
  (→ the reason restated as "their route is re-onboarding"; T1 measures lake datums and records an
  unowned gap if they have none); T2 still offered the overlay fallback the storage fold removed
  (→ deleted; Codex found the same). Also: the terms check comes before any value is recorded
  (T1); river units validated (§ Design, T2); `--clear` honours `--apply` and refuses lakes; the
  doc sweep's other list copies; T3's "only way" scoped to existing rivers (Codex too).
- **2026-09-26 — round 10: READY from both reviewers**; Claude's LOW findings folded: units are
  validated only for a river carrying a datum (a datum-less river keeps unit `None`), and the
  onboarding guard extension is named in T2; `--clear` runs alone and refuses while the table
  still holds the entry, so it cannot be undone by the next run; the lake gap is recorded beside
  this plan's own DHM bullet; § Status names the rounds as the record does.
