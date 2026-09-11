---
id: 124
title: Station active-assignment consistency (forecasting + alert-priority)
status: DRAFT
depends_on: []
owner: unassigned
created: 2026-07-17
---

# Plan 124 — Station active-assignment consistency

> **Scope locked (owner, 2026-07-18) — ready to implement directly** (no further `plan`
> rounds; the `plan` workflow kept over-scoping this tiny fix). NARROW scope only: make INACTIVE
> station model-assignments stop **forecasting** and stop appearing in the **alert-priority
> index**. The fallback-priority-drift **health** check stays **all-status** (Plan 100's locked
> DB-drift contract is NOT touched here). Making inactive assignments *fully* inert — incl. the
> drift detector — is a **separate follow-up** ([[Plan 125]]) that must supersede Plan 100 C1c.

## Problem

The station and group forecast paths treat model-assignment **status** inconsistently:

- `StationStore.fetch_model_assignments` returns **all** statuses, unfiltered
  (`store/station_store.py:212`).
- The **station** path consumes the batch-fetched dict as-is (`flows/run_forecast_cycle.py:1440-1443`)
  with **no** status filter on forecasting/assembly (`:1703`, `services/run_station_forecast.py:327`
  only sorts by priority — grep-clean of any status check).
- The **group** path filters `status == ModelAssignmentStatus.ACTIVE` at point-of-use
  (`flows/run_forecast_cycle.py:2034-2039`).

**Consequence:** an **INACTIVE** station assignment is still forecast (a retired/disabled model
keeps producing operational forecasts), while the same status on a group assignment is correctly
excluded. Live inconsistency, not latent. `ModelAssignmentStatus` has only `ACTIVE`/`INACTIVE`, so
"filter to active" fully defines the intended behaviour. (Re-confirmed against `main`, 2026-07-18.)

## Goal (narrow)

Only `ACTIVE` station model-assignments drive **forecasting**, **input assembly**, and the
**alert-priority index**. INACTIVE assignments produce no forecasts and no alert-priority entries.

## What stays UNCHANGED (explicit non-goals — verified boundaries)

- **`fetch_model_assignments` stays all-status.** Filtering it in the store would break real
  callers: onboarding's skip-if-inactive idempotency (`services/model_onboarding.py:869-878`; PK
  `(station_id, model_id)` at `db/metadata.py:593`, upsert at `store/station_store.py:224` — a
  filtered fetch would hide the inactive row and reactivate it on upsert), the admin detail
  endpoint that serializes `status` (`api/routes/api_stations.py:180`), and the contract test
  (`tests/integration/store/test_station_store.py:397`). ⇒ the ACTIVE filter lives at the
  **forecast-consumption call sites**, not the store.
- **`_check_fallback_priority_drift` stays all-status** (`flows/run_forecast_cycle.py:718-758`).
  Plan 100 deliberately made this an all-status DB-drift detector (fires on any fallback row
  drifting below threshold, incl. inactive / raw-DB-edited rows). Owner decision 2026-07-18: do
  **not** touch it here — that is Plan 125's job (with an explicit Plan 100 supersession).
- **The group path already filters active** (`:2034-2039`); the group *drift* loop reads
  `fetch_groups_for_model`, which the real store already filters to active
  (`store/station_group_store.py:107`, protocol `protocols/stores.py:559`, integration test
  `tests/integration/store/test_station_group_store.py:204`) — so there is **no group-side bug**.
  (The earlier "group degrades on inactive" claim was FALSE; dropped.)

## Fix

Because the drift check must keep reading **all** statuses, do NOT filter the dict in place. Keep
the raw `model_assignments` for the drift check; derive a **separate active-only view** for the
operational consumers:

1. Add one small active-filter (implementer's choice of typing — a `Union[ModelAssignment,
   GroupModelAssignment]` param, a structural `Protocol`+`TypeVar`, or two thin wrappers; the
   requirement is a single shared definition of "active-only", not a mandated mechanism).
2. Build `active_model_assignments = {sid: <active filter>(v) for sid, v in model_assignments.items()}`
   right after `flows/run_forecast_cycle.py:1440-1443`.
3. Route through `active_model_assignments`: the forecasting/assembly loop (`:1703`) and the
   alert-priority index build (`:1460`). **Leave the drift-check call (`:1464`) reading the raw
   `model_assignments`** (all-status — Plan 100).
4. (Optional, cosmetic) refactor the group forecast-dispatch inline active filter (`:2034-2039`)
   to reuse the same shared helper, so "same active-only rule" is one definition.
5. (Minor test-fidelity) `tests/fakes/fake_stores.py` — `FakeStationGroupStore.fetch_groups_for_model`
   ignores status while the real store filters active; align the fake so tests don't mask the
   real contract. (Not a production bug; keeps fakes honest.)

## Acceptance (red-first)

- **Bug fix:** an INACTIVE station assignment is EXCLUDED from forecasting + input assembly (RED
  against current code — currently included).
- **Plan-100 guard (must NOT regress):** an INACTIVE fallback assignment STILL counts in
  `_check_fallback_priority_drift` health (proves the drift detector was left all-status).
- **No-regression:** a station whose assignments are all ACTIVE produces the SAME forecasts /
  `forecasts_stored` / health as before (the active view is a no-op there).
- Station and group operational paths apply the same active-only rule (one shared definition).

## Source

Plan 123 `plan`-workflow escalation (2026-07-17) → 124-review re-assessment → owner narrowing
(2026-07-18: keep drift all-status here, defer full inertness to Plan 125). Prior history in git.
