---
status: DRAFT
created: 2026-09-25
plan: 329
title: The Forecast Lab snapshot cannot see a group-assigned model, so the pilot is invisible to the map
scope: Make the Forecast Lab snapshot enumerate GROUP model assignments alongside station ones, so a group-scoped model's forecasts reach the snapshot and therefore the SAPPHIRE Flow Map. NOT the map's own schema or rendering (separate repo), NOT the operational forecast path (group forecasts are already written per-station and correct), NOT the map's operational/threshold mode (a different product — see docs), NOT changing any model's priority or assignment.
depends_on: []
blocks: []
related: [262, 273, 326]
open_decisions: [D1, D2]
source: 2026-09-25 — the Flow Map session reported that `cmal_small` forecasts do not appear in the document we prepare for the map. Measured against `origin/main` the same day; the cause is on our side.
---

# Plan 329 — the snapshot cannot see a group-assigned model

⚠️ **Plan number PROVISIONAL until the owner grants it.** 340-344 are held by a concurrent session.

## Status

**DRAFT.** ⛔ No implementation until an independent review and a READY flip.

## Why this plan exists

The `cmal_small` pilot produces forecasts — 2 stations on 2026-09-25, and 139 group members are
queued to qualify by early October. **They are written correctly, per station, in `forecasts`.**

But the Forecast Lab snapshot — the document the map consumes — **never asks for them.** It builds
each station's model list from that station's OWN assignments, and `cmal_small` is assigned to a
**group**. So the pilot is invisible to the map, and will stay invisible for all 139 stations.

⭐ **Nothing is wrong with the forecasts, the group path, or the map.** The snapshot's enumeration
is simply blind to one of the two ways a model can be assigned.

## What is measured

`origin/main`, 2026-09-25.

1. **The enumeration is station-only.** `services/forecast_lab/db_sources.py:139-147` —
   `fetch_active_model_assignments()` calls `stores.station_store.fetch_model_assignments(station_id)`
   and nothing else.
2. **The store bundle has no group store.** `ForecastLabStores` (`db_sources.py:60-67`) holds
   station, observation, forecast, model, artifact, provenance and basin stores. ⇒ The snapshot
   **structurally cannot** reach a group assignment today.
3. ✅ **The lookup would already work.** `_sapphire_entries` (`snapshot.py:330-346`) asks
   `fetch_latest_forecast_for_model(stores, station.id, a.model_id)` — and group forecasts are
   stored **per station**. ⇒ Once `cmal_small` appears in the assignment list, its forecast is found
   by the existing code. **The fix is the enumeration, not the retrieval.**
4. ✅ **The station→group lookup exists.** `store/station_group_store.py:130` —
   `fetch_groups_for_station(station_id)`.
5. **The two assignment types differ by ONE field.** `types/station.py:66` `ModelAssignment` has
   `station_id`; `:76` `GroupModelAssignment` has `group_id`. Both then carry `model_id`,
   `time_step`, `status`, `priority`, `created_at`. ⇒ Merging is a projection, not a translation.
6. **Two construction sites**, and both must be updated or the two surfaces disagree:
   `cli/export_forecast_lab.py:76` and `api/routes/forecast_lab.py:68`.
7. **Confirmed on the live staging database:** stations 2009 and 2091 each list **six** station-level
   assignments — `nwp_regression`, `seasonal_precip_runoff_regression`, `nwp_rainfall_runoff`,
   `linear_regression_daily`, `persistence_fallback`, `climatology_fallback`. ⛔ **`cmal_small` is
   not among them**, and the pilot group holds **139** members.
8. ⚠️ **`is_primary` is decided by priority order.** `_sapphire_entries` takes the first entry it can
   actually RENDER as primary. `cmal_small`'s group assignment is **priority 50**, against the
   station models' 10/12/20/30 and the fallbacks' 90/100. ⇒ **It would slot fifth and NOT become
   primary** — which is what Plan 262 intended ("produces forecasts without displacing production").
   ⛔ *But that is a consequence of one number, not of any guard. A future group model at priority 5
   would silently become the map's primary.*

## Owner decisions

### D1 — does the snapshot SAY a model is group-assigned? **OPEN.**

| | option | cost |
|---|---|---|
| **(a)** ⭐ | **Project group assignments into the same list; the entry looks like any other model.** | Smallest change, and no map-side work — a model is a model. ⚠️ A map reader cannot tell that one model's forecast came from a pooled, group-trained artifact. |
| (b) | Carry a flag (e.g. `scope: group`) on the entry. | Honest, and lets the map label it. ⛔ **Touches the map's `forecast-snapshot-v2` schema, which the map repo owns** — a cross-repo change and a conversation, not a one-sided edit. |

**Recommendation: (a) now, and ASK the map session whether they want (b).** ⭐ *(a) makes the pilot
visible this week; (b) is a schema negotiation that should not block it.* ⚠️ *If the map already
renders an unknown field tolerantly, (b) is cheap later.*

### D2 — should a group model be eligible to be PRIMARY? **OPEN — § (8) is why.**

Today it would not be, because 50 > 30. But nothing *enforces* that.

| | option |
|---|---|
| **(a)** ⭐ | **Treat priority uniformly** — a group model competes like any other, and today's ordering keeps it fifth. *Simple, and the existing rule already expresses the intent.* |
| (b) | Never let a group model be primary. ⛔ *Rejected in drafting unless the owner wants it: it would hard-code a pilot's caution into the general mechanism, and a future group model may deserve to be primary.* |

⚠️ **Either way, state it.** *A reader of the snapshot should not have to infer from a priority number
whether a group model can lead.*

## Tasks

### T1 — Let the snapshot see group assignments (D1, D2)

**Outcome.** A station in a group with an ACTIVE model assignment lists that model, and its forecast
appears in the snapshot.

**In.**
- A group store on `ForecastLabStores`, and **both** construction sites updated (§ 6).
- `fetch_active_model_assignments` extended: station assignments **plus** the ACTIVE assignments of
  every group the station belongs to (`fetch_groups_for_station` → `fetch_groups_for_model`'s
  sibling lookup), merged and sorted by the existing `(priority, model_id)` rule.
- 🔴 **A station in NO group, and a group with no model, both still work** — the common case must
  not acquire a new failure mode.
- ⚠️ **Deduplicate.** A model assigned BOTH per-station and via a group must appear **once**. State
  which wins; the safe reading is the lower priority number, and it must be tested.

**Out.** ⛔ The map's schema or rendering — different repo (D1b). ⛔ Changing any priority or
assignment. ⛔ The operational forecast path — group forecasts are already written correctly.
⛔ The map's operational/threshold mode, which is a different product entirely.

**Pre-change.** A RED test asserting the DESIRED behaviour: **a station whose only assignment for a
model is via a GROUP lists that model in the snapshot, with its forecast.** ⚠️ It must fail because
the model is absent — not because a fixture lacks a group store.

**Verification.**
- A group-assigned model appears, with its forecast, for every member station.
- 🔴 **A station with no group is byte-identical to before** — asserted, because that is 137 of 139
  stations today and the whole fleet before the pilot.
- A model assigned both ways appears **once**, at the stated priority.
- `is_primary` follows D2's answer, asserted — ⛔ *not left to the accident of `cmal_small` being 50.*
- ⭐ **On staging: the pilot's two stations show `cmal_small` in the exported snapshot.** The unit
  tests prove the mechanism; only this proves the map gets it.

### T2 — Tell the map session what changed (D1)

**Outcome.** The map knows a new model will appear, and whether it can tell it apart.

**In.** A short note to the Flow Map side: `cmal_small` will appear as an ordinary model entry;
D1's answer on whether a `scope` flag is coming; and that member count grows from 2 to ~139 over
early October, so the map should expect the entry on many more stations.

**Out.** ⛔ Editing the map repo or its schema.

**Pre-change.** N/A.

**Verification.** The map session has acknowledged. ⚠️ *Not "a note was written" — the point is that
someone on the other side knows.*

## Explicitly out of scope

- **The map's operational / threshold-risk mode.** A different product, blocked on a licence
  question about BAFU-derived threshold values — see the Flow Map notes. This plan serves the
  **research comparison** view only.
- **Why 137 of 139 stations have no forecast yet** — they lack 30 unbroken days; they qualify on
  their own by early October.
- **The `_pooled` combination**, which is a station-level model and already appears.

```json
{
  "phases": [
    {"phase": 1, "tasks": ["T1"], "parallel": false},
    {"phase": 2, "tasks": ["T2"], "parallel": false, "note": "after T1 ships — tell them what IS, not what will be"}
  ]
}
```
