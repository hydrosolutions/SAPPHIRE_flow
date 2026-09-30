---
status: DRAFT
created: 2026-09-30
plan: 517
title: Station 2024 has no reanalysis binding — give it one or take it out of the cmal_small pilot group
scope: Find out why one station has no reanalysis forcing (read-only), build the durable audited route for removing a group member, and - only if the diagnosis says it can be bound - a narrowly scoped audited single-station binding operation with its own review; look at four other stations that lack a model; decide whether adding a member should be refused in future.
risk: medium   # a live-database change on the staging host; the code branches (audited remover, single-station binder, optional gate) each need their own review
related: [514, 255, 256, 259, 262, 115, 307]
open_decisions: [Q1, Q2, Q3, Q4]
priority: low
---

# Plan 517 — station 2024 has no reanalysis binding

## Status

**DRAFT, low priority.** Read-only investigation first (T1, T2); the durable remover (T3) and the binding branch
(T4) and the optional gate (T5) follow. Only the orchestrator sets READY. Whatever is done to the staging database
is an owner-approved action.

**Numbering.** Drafted as 516 on 2026-09-30, renumbered to 517 the same day because 516 was already taken by `516-region-bundle-export-route.md`.

## Owner decisions (2026-09-30)

- **The immediate unblock is done, as an exception.** On 2026-09-30 the owner authorised removing station 2024
  from `swiss-cmal-small-pilot` (**139 -> 138**) through a **one-off direct database write**, approved by the
  owner. It is recorded here as an **exception, not a precedent**: direct writes leave no audit trail, and the
  earlier draft's D2 (no direct writes for membership or binding) stands as the rule.
- **The durable path** is an audited group-member removal route (T3), so the next removal is not another
  unrecorded edit. Whether 2024 is ever re-added is decided after T1 (Q1).

## Why

Plan 514 explains how one member with empty past forcing can drop a whole group (its 2026-09-29 cause is
**UNVERIFIED** - the worker logs were gone; nothing measured since confirms it). Station **2024** is the suspected
trigger; it has never had reanalysis forcing, so no forcing-consuming model can ever forecast it. With Plan 514
implemented, such a station no longer drops the group; a group member that can never forecast is still a defect,
and the durable ways to fix or remove one do not exist yet.

## Facts (2026-09-29 measurement plus the prior record; T1 re-measures)

- Station uuid `a0aa6f2a-e318-42e2-99e5-4224e4639af2`, code **2024**. Until 2026-09-30 a member of
  `swiss-cmal-small-pilot` (139); now removed (138).
- It has **one** weather binding, role `forecast` (`icon_ch2_eps`), **no** `meteoswiss_rprelimd` or
  `meteoswiss_tabsd` reanalysis binding and **no forcing rows**. It has never had `nwp_regression`,
  `nwp_rainfall_runoff` or seasonal models.
- **Prior evidence that already answers part of T1 (read 2026-09-30):** Plan 256
  (`256-onboarding-must-not-promote-what-it-did-not-build.md`, ~lines 50-65) records station 2024 (Branson) on
  staging with `Ring Self-intersection[7.20127964026713 46.1757792259881]`, **no MeteoSwiss binding and zero
  `meteoswiss_*` rows**, while the other 147 carry six products through 2026-09-06; it also explains why the
  Plan 115b2 hold cannot fire (`held_out_ids = meteoswiss_eligible_ids - meteoswiss_backfilled` - an ineligible
  station was never eligible, so it is never held out). Plan 255 (`255-typed-eligibility-exclusions.md`, ~36)
  records the same finding and that Plan 259's report cannot name the reason. So the leading explanation is an
  **invalid (self-intersecting) basin polygon**, not a missing basin or a failed write. This is prior record,
  not a fresh measurement; T1 reconciles it with what is on the host now and coordinates with 255/256/259
  rather than treating every explanation as equally open. Note that fixing a self-intersecting polygon is a
  **geometry** repair, larger than "add a binding" (T4).
- Four other members (**2206, 2356, 2414, 2640**) also lacked `nwp_rainfall_runoff` on 2026-09-29. Reason not
  checked; a separate question until T2 answers it.
- Live state after the removal: 138 members; **three sparse stations exist, two with no data since 2026-09-22**
  (Plan 514); they are unrelated to the binding defect.

## What the code says (`origin/main`, read 2026-09-30)

- **Bindings.** Onboarding Step 4c (`services/onboarding.py` ~690-720) stores a `REANALYSIS` binding for every
  station returned by `eligible_meteoswiss_configs` (`services/reanalysis_backfill.py`): needs a `basin_id` and
  a valid, non-empty basin polygon; the rest are logged `reanalysis_backfill.station_excluded`.
- **Step 4c is an embedded block inside the full onboarding flow, not an operator entry point.** Full onboarding
  writes station metadata (`store_station`, ~450-459, ~503), proceeds through model assignment and writes
  operational status (~1204-1220). Re-running it for one station would rewrite metadata, assignments and status
  - violating this plan's "never write `station_status`, one station only" boundary. The earlier draft's "re-run
  onboarding Step 4c for the one station" is **withdrawn**.
- **`scripts/backfill_meteoswiss_history.py` cannot be scoped to one station.** Its arguments are `--bind-only`,
  `--dry-run`, `--station-batch-size` (~96-108); it calls `bind_meteoswiss_reanalysis_fleet` (fleet-wide, no
  selector, `reanalysis_backfill.py` ~154-167) and its `--dry-run` only prints its own arguments (~131) without
  computing anything. `store_weather_source` is an upsert, so a fleet run is idempotent, but it is not
  one-station, and the dry run shows nothing.
- **Group membership removal.** A store-level remover **exists**: `StationGroupStore.remove_station_from_group`
  (Protocol `protocols/stores.py` ~786, `store/station_group_store.py` ~182, fake
  `tests/fakes/fake_stores.py` ~1806, one integration test). **No operator route calls it.**
  `scripts/create_station_group.py` adds members by code (dry-run default, audited, tenant-checked,
  transactional) and "never removes a member" (docstring; ~185). The earlier draft's "UNVERIFIED whether a remover
  exists" is resolved.
- **Audit.** `AuditEventType` (`types/enums.py` ~369) has `STATION_GROUP_CREATED` but no removal event.
  `audit_log.event_type` is plain TEXT (only `actor_type` is constrained): a new member needs **no migration**.
- **Dockerfile.** `scripts/create_station_group.py` is already in the curated script list (`Dockerfile` ~150) and
  `tests/unit/deploy/test_dockerfile_operator_scripts.py` locks it (~32): a `--remove` mode of the same script
  needs no new entry; a **new** script would.
- **Group assignments and the group artifact are keyed by group.** `group_model_assignments` and the active
  group artifact belong to the group, not to a station, so serving 138 members is fine. **UNVERIFIED:** whether
  the active group artifact (`687ae9c2...`) was trained with 2024's statics. The check (T1): read the artifact's
  recorded training station set or static table, and state whether 2024 is in it. If it was, the model's
  behaviour on the other 138 is unchanged (per-station prediction) but the artifact's provenance should be noted
  and retraining on the 138 is a separate decision.

## Decisions (recommended answers)

| # | Decision | Why |
|---|---|---|
| D1 | **Diagnose before choosing.** T1 finds out why 2024 has no binding (starting from Plan 256's record). | The right fix depends on the cause. |
| D2 | **No direct database write for membership or binding, except the 2026-09-30 owner-authorised exception above.** Removal goes through an audited `--remove` route (T3); a binding, if ever needed, through a single-station audited operation (T4). | Direct writes leave no trail. |
| D3 | **The removal wrapper is dry-run first, tenant-checked, and atomic with its audit entry.** It reuses `remove_station_from_group` and the script's existing plan/apply split. | The store method exists; only the operator route and audit are missing. |
| D4 | **A binding operation, if built, reuses `eligible_meteoswiss_configs`, `store_weather_source` and `run_backfill`, and touches exactly one station's weather-source and forcing rows.** It never writes station metadata, assignments or status. It has its own review. | Step 4c and the fleet script do not meet the boundaries. |

## Tasks

### T1 - Why does 2024 have no binding, and was the group artifact trained with it? (read-only, staging host)
**Outcome**: a recorded cause, one of: no `basin_id`; invalid polygon (Plan 256's record); polygon outside the
MeteoSwiss grid; binding write failed; station added by a route that skipped Step 4c.
- Read `station.basin_id`, the basin row and its geometry validity, and the onboarding audit entries. Run
  `eligible_meteoswiss_configs` for this one station **with no writes** and read the `station_excluded` reason.
  Reconcile with Plan 255/256/259.
- Read the active group artifact's recorded training stations/statics: is 2024 in it?
- Compare with a neighbour that has a binding.
**In / Out**: a dated note in this plan. Out: any write.
**Verification**: the note quotes the query outputs and the exclusion reason.

### T2 - The four other stations (2206, 2356, 2414, 2640)
**Outcome**: a recorded reason each lacks `nwp_rainfall_runoff`: binding present, forcing rows, assignment status,
whether a training run was attempted and skipped. State which is the same defect as 2024 and which is legitimate
(for example the model's own training gate).
**In / Out**: a dated note. Out: changing assignments.
**Verification**: a table of station, binding, forcing rows, assignment status, reason.

### T3 - The durable audited group-member removal (code change; independent of T1's outcome)
**Outcome**: `scripts/create_station_group.py` gains a `--remove` mode (or an equivalent single small operator
command in the same script), so the next removal is not another direct write.
- **Plan/apply split:** `plan_station_group` today resolves only additions; removal needs a **new plan field**
  (for example `members_to_remove` and `not_members_codes`), computed with the same tenant check.
- **Defined behaviour:** absent group -> refuse with a clear message (never create one); a code that resolves but
  is **not a member** -> reported as "already not a member", exit 0, no audit row for it (idempotent, like add);
  an unresolvable or wrong-tenant code -> blocking, nothing written; dry run is the default and prints the
  plan.
- **Audit:** a new `AuditEventType.STATION_GROUP_MEMBER_REMOVED` (no migration), one entry per removed member or
  one per command with the station list - written in the **same transaction** as the removal (the add path
  already shares one write connection for `add_station_to_group` and the audit insert; keep that), so the removal
  and its audit are atomic.
- Never touches `station_status`, model assignments or the group's artifact.
**In / Out**: `scripts/create_station_group.py`, `types/enums.py`, tests (`tests/unit/scripts/`, the fake store,
the Dockerfile lock test only if a new script is chosen). Out: onboarding.
**Verification**: unit tests for each behaviour above with the fake store; an integration test that removal and
audit commit or roll back together; `uv run pytest tests/unit/deploy -q`; `uv run pyright`; `uv run ruff check`.
**Pre-change**: RED - `--remove` does not exist.
**Review**: the ordinary pair; the destructive operation gets the dry-run/audit/tenant tests above.

### T4 - A single-station binding operation (ONLY if T1 says 2024 can have a binding, and the owner wants it re-added)
**Outcome**: a narrowly scoped, tenant-checked, audited operation that binds and backfills reanalysis forcing for
**one named station** and nothing else.
- Built from the existing services (`eligible_meteoswiss_configs`, `store_weather_source`, `run_backfill`), as a
  new operator script (needs a Dockerfile entry and an update to `test_dockerfile_operator_scripts.py`) with a
  **real dry run** (it computes eligibility and the backfill spans and prints them; the fleet script's dry run does
  not), a tenant check, and an audit entry. It does not touch station metadata, assignments or status.
- If T1 shows an **invalid polygon**, "eligible" is false and no binding is possible without repairing the
  geometry. Repairing a basin polygon is a data decision (who owns the shape, what `make_valid` does to the
  catchment area) and is **not** part of this task: the operation reports the exclusion reason and stops.
- Its own multi-model review (it writes production data); it may not be smuggled into T3.
**Verification**: unit tests with fakes (one station in, one station out, others untouched); the dry run prints
spans without writing; after a live run read back the binding rows and forcing rows for that station only.
**Pre-change**: RED - no single-station entry point exists.

### T5 - A gate that refuses this state (only if Q3 is answered yes; may be deferred)
**Outcome**: adding a station to a group is refused, with a message naming the station, when the group's model
needs **past** forcing and the station has no **usable** reanalysis binding.
- Predicate: the assigned group model's declared **past-dynamic** requirements only. A model that needs only
  future NWP legitimately has no reanalysis binding and must not block. **Which assignments count:** the group's
  ACTIVE group-model assignments; a group with none cannot be checked and the dry run says so.
- "Usable binding" means an existing reanalysis binding that is active, not merely any weather-source row (the
  station always has a `forecast` binding).
- Put it in `plan_station_group` so the dry run shows it as a blocking reason.
- **Limits, stated:** it protects **add-time only**; a first fill (membership created before any model is
  assigned) bypasses it; a station whose binding is later lost is not caught. A **doctor-style health check** (a
  read-only report of group members lacking a past-forcing source) would cover all three and may be the better
  place; the owner may prefer to defer T5 for that.
**In / Out**: `scripts/create_station_group.py` and its tests. Out: onboarding.
**Verification**: a unit test where a member without a binding blocks a past-forcing group; one where the same
member is accepted into a **future-only** group; one where a group with no assignment reports "cannot check".
**Pre-change**: RED - on `main` the dry run accepts it.

### T6 - Documents and final verification
**Outcome**: docs match the branches taken, and removal is verified even if T4/T5 are declined.
- If T3 lands: `docs/touchpoint-maps.md` (group/onboarding map), `docs/spec/types-and-protocols.md` and
  `docs/architecture-context.md` (`AuditEventType` lists), and the script's own docstring (it no longer "never
  removes"), plus the operator-scripts note in `docs/standards/cicd.md` if it lists the script's modes.
- If T4 lands: the same maps plus the Dockerfile script list and its lock test.
- If T5 lands or is declined: record the decision in this plan.
- **Final verification (independent of T4/T5):** the audited route has been used (or dry-run verified) on the
  pilot group; the audit entry exists; the group has 138 members; a cycle's `cmal_small` forecasts are compared
  **as a station-ID set** against the 138 members for the intended model, parameter, issue time and current
  status - not by count alone (duplicate or superseded rows can hide a missing station). The duplicate
  `linear_regression_daily` rows are Plan 514's out-of-scope follow-up; they are not evidence here.
**Verification**: the set comparison and the audit entry, recorded in a dated note.

## Exit gates

1. T1 and T2 notes are in this plan with quoted evidence, reconciled with Plan 255/256.
2. The audited removal route exists, is tested (dry run, tenant, atomic audit, absent group, non-member), and its
   review is done. (The 2026-09-30 removal itself was the recorded exception.)
3. If 2024 is to return: it is bound with forcing rows through the T4 operation, then re-added through the audited
   add path; otherwise it stays out.
4. The next cycle's `cmal_small` station-ID set matches the group members, or the differences are recorded.
5. If T3/T4/T5 are taken: `uv run pyright`, `uv run ruff check`, `uv run ruff format --check`, the CI shards and
   the ordinary review pair pass.

## Not in this plan

- Plan 514's code (missing forcing no longer drops a group; dropped groups are visible).
- Changing station status, or removing stations from the database.
- Any change to the model, its training, or `cmal_small`'s statics (retraining on 138 is a separate decision).
- Repairing basin polygons (a data decision; see T4).
- Onboarding-time gates for anything other than group membership.

## Risks

| Risk | Mitigation |
|---|---|
| A fleet-wide binding run when only one station was intended | The fleet script is not used; T4 is single-station with a real dry run. |
| Removing 2024 hides a wider pattern | T2 looks at four others; T1 says whether the cause is per-station or systematic (Plan 256 already implies a polygon fault). |
| The audited remover is a new destructive operation | Dry-run default, atomic audit, tenant check, no status writes, ordinary review. |
| The direct-write exception becomes a habit | Recorded as an exception; T3 removes the reason for it. |

## Open questions (plain language, with a recommendation)

- **Q1 - Should station 2024 come back into the pilot later?** It is out now. Earlier records suggest its
  catchment shape is faulty, which would need the shape repaired by whoever owns the geometry before it can get
  weather data. *Recommendation:* leave it out; do not build the single-station binding tool (T4) until someone
  wants the station back and the shape owner has fixed the polygon.
- **Q2 - Is a small script change acceptable to remove a member, instead of another manual database edit?**
  *Recommendation:* yes (T3); it is small, audited, and it is the durable answer to the exception you approved
  today.
- **Q3 - Should adding a station to a group be refused when it has no past weather data source?**
  *Recommendation:* defer the refusal and consider a read-only health report instead - a refusal only protects
  later additions (the first fill bypasses it) and must not block groups whose model needs only forecast weather.
  If you want the refusal, it is T5 as written.
- **Q4 - Which station group check should have caught this earlier?** (Plan 256 covers why operational promotion
  ignores a missing binding.) *Recommendation:* leave that to Plan 256/259; do not widen this plan.

## Dependency graph

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1", "T2"], "parallel": true },
    { "id": "phase-2", "tasks": ["T3"] },
    { "id": "phase-3", "tasks": ["T4"], "depends_on": ["phase-1", "phase-2"] },
    { "id": "phase-4", "tasks": ["T5"], "depends_on": ["phase-2"] },
    { "id": "phase-5", "tasks": ["T6"], "depends_on": ["phase-2", "phase-3", "phase-4"] }
  ]
}
```
