---
status: DRAFT
created: 2026-09-30
plan: 516
title: Station 2024 has no reanalysis binding — give it one or take it out of the cmal_small pilot group
scope: Find out why one member of the 139-station pilot group has no reanalysis forcing, fix that one station either way, look at four other stations that lack a model, and decide whether onboarding should refuse this state in future.
risk: medium   # a live-database change on the staging host; no code unless Q3 is answered yes (then it is a code change with its own review)
related: [514, 262, 115, 307]
open_decisions: [Q1, Q2, Q3]
priority: low
---

# Plan 516 — station 2024 has no reanalysis binding

## Status

**DRAFT, low priority.** Read-only investigation first (T1, T2); the fix is chosen from the result (T3). Only the
orchestrator sets READY. Whatever is done to the staging database is an owner-approved action; direct database
writes are avoided (see D2).

## Why

Plan 514 explains how one member with empty past forcing can drop a whole 139-station group. On 2026-09-29 the
pilot run `powerful-leech` produced zero `cmal_small` rows; the most likely trigger (**UNVERIFIED** — the worker
logs were gone) is station **2024**. Plan 514 stops that station denying the others a forecast; it does not give
2024 a forecast, and a group member that can never forecast is still a defect in the group.

## Facts (measured by the orchestrator session 2026-09-29; not re-measured here — T1 re-measures)

- Station uuid `a0aa6f2a-e318-42e2-99e5-4224e4639af2`, code **2024**, member of `swiss-cmal-small-pilot`
  (139 stations).
- It has **one** weather binding, role `forecast` (`icon_ch2_eps`). It has **no** `meteoswiss_rprelimd` or
  `meteoswiss_tabsd` reanalysis binding and **no forcing rows**.
- It has never had `nwp_regression`, `nwp_rainfall_runoff` or seasonal models either — every model that needs
  forcing.
- Four other members (**2206, 2356, 2414, 2640**) also lacked `nwp_rainfall_runoff` on 2026-09-29. Reason not
  checked. They may have forcing; this is a separate question until T2 answers it.

## What the code says about how bindings are created (`origin/main`, read 2026-09-30)

- Onboarding Step 4c (`services/onboarding.py`, "MeteoSwiss reanalysis binding") stores a `REANALYSIS` binding
  for every station returned by `eligible_meteoswiss_configs` (`services/reanalysis_backfill.py`): the station
  needs a **`basin_id`** and a **valid, non-empty basin polygon**. Stations failing that are logged
  `reanalysis_backfill.station_excluded` (reason `no_basin_id` or the geometry reason) and get **no binding**.
  So the likely explanations for 2024 are: no basin, an empty or invalid basin polygon, the binding write failed
  (`meteoswiss_binding_error`), or the station was added by a route that skips Step 4c. The MeteoSwiss
  reanalysis is a national grid, so "outside the grid" is possible only for a basin polygon that does not overlap
  it; UNVERIFIED for 2024.
- After Step 4c, a station with a binding but no rows is held out of assignment, training and operational
  promotion until its backfill landed one row. A station that never got a binding is `meteoswiss_eligible` false
  — **the hold does not apply**, which is exactly how a stationary "no binding, still in the group, still
  operational" state can exist.
- **Group membership.** `scripts/create_station_group.py` (Plan 262 T3a) creates a group and **adds** members by
  station code, idempotent, audited, with a dry-run default. Its docstring says it **never removes a member**.
  `StationGroupStore` has `add_station_to_group`; the repo has no operator route that removes one (UNVERIFIED —
  T3 greps `src/` and `scripts/` for a remover before asserting this).

## Decisions (recommended answers)

| # | Decision | Why |
|---|---|---|
| D1 | **Diagnose before choosing.** T1 finds out why 2024 has no binding. | The right fix depends on the cause; guessing between "add" and "exclude" repeats the mistake that let this happen. |
| D2 | **No direct database write for membership or binding.** Use an audited path: the existing onboarding path for a binding (re-run Step 4c for the one station), and either a new small script or an added `--remove` mode of `create_station_group.py` for exclusion. | Direct writes leave no audit trail; the record shows `operational` was already applied to 111 stations by a direct write. A one-station change does not justify a new habit. |
| D3 | **Prefer giving the station a binding if it can have one** (basin present and valid, inside the MeteoSwiss grid). **Exclude it if it cannot**; a station with no basin is not a forcing-consuming forecast target. | A binding restores a forecast; an exclusion only stops the noise. |

## Tasks

### T1 — Why does 2024 have no binding? (read-only, staging host)
**Outcome**: a recorded cause, one of: no `basin_id`; basin polygon empty or invalid; polygon outside the
MeteoSwiss grid; binding write failed at onboarding; station added by a route that skips Step 4c.
- Read the station's `station.basin_id`, the basin row and its geometry validity, and its onboarding audit
  entries. Run `eligible_meteoswiss_configs` for this one station (no writes) and read the log line.
- Compare with a neighbour that has a binding (same catchment size class).
**In / Out**: a dated note in this plan. Out: any write.
**Verification**: the note quotes the query outputs and the exclusion reason.
**Pre-change**: N/A — read-only.

### T2 — The four other stations (2206, 2356, 2414, 2640)
**Outcome**: a recorded reason each lacks `nwp_rainfall_runoff`.
- For each: does it have a reanalysis binding and forcing rows; does it have an assignment for that model and
  in which status; was a training run attempted and skipped (`ModelOnboarding` record, skill gate, short history).
- State which of these is the same defect as 2024 and which is a different, legitimate cause (for example, the
  model's own training gate).
**In / Out**: a dated note in this plan. Out: changing assignments.
**Verification**: a table of station, binding present, forcing rows, assignment status, reason.
**Pre-change**: N/A — read-only.

### T3 — Apply the chosen fix
**Outcome**: 2024 either has a binding and forcing rows, or is not in `swiss-cmal-small-pilot`.
- **If T1 finds a fixable binding cause:** re-run onboarding Step 4c and the one-station backfill for 2024 only
  (owner-approved, staging host, dry-run first). Verify a binding exists and forcing rows landed. Then run one
  cycle and confirm 2024 appears in the `cmal_small` forecasts (Plan 514 T7 count: 139, not 138).
- **If it cannot have a binding:** remove it from the group. Because no audited remover exists (D2), the
  smallest change is a `--remove` mode on `scripts/create_station_group.py`: dry-run by default, audited, tenant
  checked, never touching station status. Then verify the group has 138 members and the next cycle stores 138
  `cmal_small` forecasts with no `station_inputs_unavailable` for it. **That is a code change**: it needs its
  own tests and the ordinary review, and the Dockerfile curated-script list and its lock test already include the
  script (no new entry).
- Either way: never write `station_status`.
**In / Out**: staging database through an audited route; the group script only in the exclusion branch. Out:
station status, model assignments, the model.
**Verification**: read back the binding rows or group members; a cycle's `cmal_small` count; the audit-log entry.
**Pre-change**: baseline — 2024 has no binding and no forecast (2026-09-29).

### T4 — A gate that refuses this state (only if Q3 is answered yes)
**Outcome**: adding a station to a group whose model consumes forcing fails, with a message naming the station,
when the station has no reanalysis binding.
- Put the check in the one place members are added (`plan_station_group` in `scripts/create_station_group.py`
  returns the blocking codes today for unknown codes; a missing binding becomes another blocking reason), so a
  dry run shows it. It must read the group's model assignments to know whether the group consumes forcing —
  UNVERIFIED how cheaply; if a group has no model yet, the check cannot run and says so.
- A second place worth checking is the operational promotion step (a station is promoted with no binding and no
  hold today). Do not widen this plan to it; note it as a finding.
**In / Out**: `scripts/create_station_group.py` and its tests. Out: onboarding flow changes.
**Verification**: a unit test where a member without a binding blocks a forcing-consuming group; one where the
same member is accepted into a group whose model needs no forcing.
**Pre-change**: RED — on `main` the dry run accepts it.

## Exit gates

1. T1 and T2 notes are in this plan with quoted evidence.
2. 2024 is either bound with forcing rows, or out of the group; the change is audited (D2).
3. The next cycle's `cmal_small` count matches the group size, or the reason for the difference is recorded.
4. If T4 is taken: its tests pass, and `uv run pyright`, `uv run ruff check` and the CI shards pass; review by
   the ordinary pair.

## Not in this plan

- Plan 514's code (empty forcing no longer drops a group, dropped groups are visible).
- Changing station status or removing stations from the database.
- Any change to the model, its training, or `cmal_small`'s statics.
- Onboarding-time gates for anything other than group membership.

## Risks

| Risk | Mitigation |
|---|---|
| Re-running Step 4c/backfill touches more than 2024 | One-station scope, dry-run first, read back the rows written. |
| Removing 2024 hides a wider pattern (the same fault at other stations) | T2 looks at the four others; T1's note says whether the cause is per-station or systematic. |
| A `--remove` mode is a new destructive operation | Dry-run default, audit entry, tenant check, no status writes; separate review. |

## Open questions (plain language, with a recommendation)

- **Q1 — Give the station forcing, or take it out of the pilot?** Decided after T1. *Recommendation:* give it a
  binding if it has a valid catchment; otherwise take it out. Do not keep a station in the group that can never
  forecast.
- **Q2 — Is a small script change acceptable to remove a member**, rather than editing the database by hand?
  *Recommendation:* yes; it is a few lines, audited, and avoids another unrecorded database edit.
- **Q3 — Should adding a station to a group be refused when the station has no forcing source?** *Recommendation:*
  yes, for groups whose model needs forcing. It would have stopped this. The catch: it needs the group to have a
  model assigned already, so it protects later additions more than the first fill.

## Dependency graph

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1", "T2"], "parallel": true },
    { "id": "phase-2", "tasks": ["T3"], "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T4"], "depends_on": ["phase-2"] }
  ]
}
```
