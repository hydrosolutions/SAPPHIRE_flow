---
status: DRAFT
created: 2026-09-25
plan: 329
title: The Forecast Lab snapshot cannot see a group-assigned model, so the pilot is invisible to the map
scope: Make the Forecast Lab snapshot enumerate GROUP model assignments alongside station ones, so a group-scoped model's forecasts reach the snapshot and therefore the SAPPHIRE Flow Map. NOT the map's own schema or rendering (separate repo), NOT the operational forecast path (group forecasts are already written per-station and correct), NOT the map's operational/threshold mode (a different product — see docs), NOT changing any model's priority or assignment.
depends_on: []
blocks: []
related: [262, 273, 326]
open_decisions: []   # both closed by the owner 2026-09-25
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
7. **Measured on the live staging database 2026-09-25** ⚠️ *(not verifiable from this repo — a
   reviewer can check the code claims but not the data ones)*: stations 2009 and 2091 each list
   **six** station-level assignments — `nwp_regression`, `seasonal_precip_runoff_regression`, `nwp_rainfall_runoff`,
   `linear_regression_daily`, `persistence_fallback`, `climatology_fallback`. ⛔ **`cmal_small` is
   not among them**, and the pilot group holds **139** members.
8. 🔴 **`is_primary` goes to the first RENDERABLE entry — NOT simply the lowest priority.**
   `services/forecast_lab/snapshot.py:345` skips entries with no forecast or an unrenderable one.
   ⛔ *An earlier version of this claim concluded that `cmal_small` at priority 50 therefore "would
   NOT become primary". **That is false.*** It cannot displace a **renderable** priority-10 model —
   but it **can and will become primary when 10/12/20/30 are all unavailable**, displacing the
   90/100 fallbacks. ⇒ On a station whose NWP models have no forecast today, a group model becomes
   the map's headline forecast **immediately**, with no rule having decided that. D2 is live, not
   theoretical.
9. 🔴 **Enumerating group assignments changes what 137 stations SHOW, not just two.** The group
   already holds 139 members; only 2 have forecasts (the rest lack 30 unbroken days until early
   October). Under the proposed enumeration a member with no forecast gains a
   `reason="no_forecast"` **unavailable entry** (`snapshot.py:353`). ⇒ **Three distinct outcomes**,
   which an earlier draft collapsed into "137 stations have no group":
   | | station | after this change |
   |---|---|---|
   | actual non-member | not in the pilot group | **payload unchanged** |
   | member, no forecast yet | 137 today | **NEW unavailable entry** appears |
   | member with a renderable forecast | 2 today, ~136 by October | available entry |
   ⚠️ **The map will see 137 new "no forecast" entries the day this ships.** That is correct
   behaviour and must be *told to them*, not discovered.

## Owner decisions

### D1 — does the snapshot SAY a model is group-assigned? **⚖️ CLOSED — owner, 2026-09-25: NO.**

| | option | cost |
|---|---|---|
| **(a)** ⭐ | **Project group assignments into the same list; the entry looks like any other model.** | Smallest change, and no map-side work — a model is a model. ⚠️ A map reader cannot tell that one model's forecast came from a pooled, group-trained artifact. |
| (b) | Carry a flag (e.g. `scope: group`) on the entry. | Honest, and lets the map label it. ⛔ *An earlier version called this "the map's schema, which the map repo owns". **Wrong.*** The authoritative export is **`forecast-lab-snapshot/v2`**, OUR contract, its JSON Schema generated from OUR Pydantic models — and it **forbids unknown fields** (`docs/spec/forecast-lab-snapshot.md:31-43`). ⇒ (b) is **local schema + model + test changes AND consumer coordination**. Consumer tolerance alone cannot carry it. |

**⚖️ CLOSED on (a).** Owner: *"the map does not care if a model is group-scoped or not."*

⇒ **A group-assigned model is projected into the list as an ordinary entry, and NOTHING marks it as
group-scoped.** ⛔ *No `scope` field, no versioned change to `forecast-lab-snapshot/v2`, no consumer
negotiation.* ⭐ *This is the smallest possible fix, and the owner's answer removes the only reason
it might not have been.*

⚠️ **T2 no longer asks the map anything** — it tells them what is coming.

### D2 — should a group model be eligible to be PRIMARY? **⚖️ CLOSED — owner, 2026-09-25: YES.**

⛔ **The draft's premise was wrong** (§ 8): it is not simply "50 > 30, so no". `is_primary` goes to
the first **renderable** entry, so a priority-50 group model **becomes primary wherever the
priority-10/12/20/30 models have no renderable forecast** — displacing the fallbacks. That happens
today, on real stations.

| | option |
|---|---|
| **(a)** ⭐ | **Treat priority uniformly** — a group model competes like any other, including winning primary when everything above it is unrenderable. *Simple, and consistent with how every other model is ranked.* ⚠️ Accept that the pilot can headline a station whose established models are silent. |
| (b) | Never let a group model be primary. ⛔ *Rejected in drafting unless the owner wants it: it would hard-code a pilot's caution into the general mechanism, and a future group model may deserve to be primary.* |

**⚖️ CLOSED on (a) — treat priority uniformly.** Owner: *"yes, the group model can be a station's
headline forecast."*

⇒ **A group model competes exactly like any other**, including winning `is_primary` when everything
above it has no renderable forecast. ⛔ *No special case, no guard, no exception for group scope.*

🔴 **This makes § (8)'s correction load-bearing rather than academic.** *The behaviour the owner just
approved is the one I had wrongly described as impossible — so the verification must assert it
HAPPENS, not merely that it does not happen in the easy case.* ⇒ **Both directions are tested**: a
group model does not displace a renderable higher-priority model, **and** it does become primary
when those are unrenderable. ⛔ *Testing only the first would certify the protection I invented.*

## Tasks

### T1 — Let the snapshot see group assignments (D1, D2)

**Outcome.** A station in a group with an ACTIVE model assignment lists that model, and its forecast
appears in the snapshot.

**In.**
- A group store on `ForecastLabStores`, and 🔴 **SEVEN construction sites**, not two: the CLI
  (`cli/export_forecast_lab.py:76`), the route (`api/routes/forecast_lab.py:68`) **and five test
  constructors**. ⛔ *`tests/unit/api/conftest.py:68` has no `group_store` either — updating the
  route alone makes existing route tests fail on a missing fixture key, which reads as an unrelated
  breakage.*
- `fetch_active_model_assignments` extended: station assignments **plus** the group ones, via
  `fetch_groups_for_station` → **`fetch_group_model_assignments`**. ⚠️ **That returns assignments of
  BOTH statuses — filter ACTIVE explicitly**, and filter **before** deduplicating.
- **Deduplicate by `model_id` across station assignments and ALL overlapping groups, taking the
  MINIMUM priority.** Then sort `(priority, model_id)`. ⚠️ *Assignment precedence selects DISPLAY
  priority only — retrieval still returns the latest station/model forecast either way.*
- 🔴 **Eligibility and principal scoping stay BEFORE the group lookup.** ⛔ **Group membership must
  never expand the exported station set** — a member that is not an eligible station does not enter
  the snapshot.
- ⚠️ *Tenant consistency needs no new work: the composite membership foreign key enforces it
  (`db/metadata.py:475`). A group has no inactive status, and an empty group cannot match a
  membership lookup — so only inactive ASSIGNMENTS need excluding.*

**Out.** ⛔ The map's rendering. ⛔ Adding a field to `forecast-lab-snapshot/v2` — that is D1(b),
a versioned change to OUR contract. ⛔ Changing any priority or assignment. ⛔ The operational
forecast path. ⛔ The map's operational/threshold mode.

**Pre-change.** A RED test asserting the DESIRED behaviour: **a station whose only assignment for a
model is via a GROUP lists that model in the snapshot, with its forecast.**
⚠️ **Prepare the bundle and fake wiring FIRST, then measure red** — otherwise it fails on a missing
fixture key rather than on the model's absence, which is red for the wrong reason.
⭐ **Assert direct retrieval succeeds first**, then snapshot inclusion — that separates "the
enumeration is blind" from "the forecast cannot be found", which is the whole premise (§ 3).

**Verification.** ⛔ *An earlier draft named neither test nodes nor commands and said "with its
forecast, for every member", which ignores eligibility and the members that have no forecast.*
- A group-assigned model appears **with its forecast** for a member that HAS a renderable one.
- 🔴 **A member with NO forecast gains an unavailable entry** with `reason="no_forecast"` (§ 9) —
  asserted, because that is 137 of 139 stations today and it is what the map will actually see.
- 🔴 **An actual NON-member's station payload is byte-identical** — clock frozen for the comparison,
  and compared at the STATION level. ⚠️ *Snapshot-wide status may legitimately change; asserting the
  whole document unchanged would be wrong.*
- A model assigned **both** ways appears **once**, at the minimum priority — tested with an
  ACTIVE/INACTIVE duplicate pair and with reversed insertion order.
- 🔴 **`is_primary` per D2, asserted BOTH ways** (§ 8): a group model does **not** displace a
  renderable priority-10 model, **and** it **does** become primary when 10/12/20/30 are all
  unrenderable. ⛔ *Testing only the first case would certify a protection that does not exist.*
- ⚠️ **"No new failure mode" cannot mean "no database outage"** — a non-member now incurs a group
  lookup. Existing error propagation is preserved, not swallowed.
- ⭐ **On staging: the pilot's two stations show `cmal_small` in the exported snapshot.**

### T2 — Tell the map session what changed (D1)

**Outcome.** The map knows a new model will appear, how many entries it brings, and that it can
lead a station. ⛔ *Not "whether it can tell it apart" — D1 closed: it does not need to.*

**In.** A short note to the Flow Map side:
- `cmal_small` appears as an **ordinary model entry**. ⛔ *Nothing marks it group-scoped — D1,
  closed: the map does not care, so no schema change is coming.*
- 🔴 **On the day this ships they will see ~137 NEW entries carrying `reason="no_forecast"`** — the
  group's members that have not yet accumulated 30 unbroken days (§ 9). ⛔ *That is correct and
  expected; told, not discovered.* They convert to real forecasts through early October.
- ⚠️ That a group model **can become a station's primary** where the established models are silent
  (§ 8, D2).

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

## Changelog

**2026-09-25 — created, then corrected by review: 3 medium, 1 low.** ⭐ **The central diagnosis held
under an explicit attempt to falsify it:** group execution writes per-station `OperationalForecast`
records through the ordinary store, and retrieval filters station/model/parameter and **never checks
assignment or artifact scope** — so there is no group-specific retrieval barrier. The fix really is
the enumeration.

Two things I asserted were wrong:

| | what I claimed | measured |
|---|---|---|
| **§ 8** | priority 50 means the pilot "would NOT become primary" | **False.** `is_primary` goes to the first **RENDERABLE** entry, so it displaces the 90/100 fallbacks wherever 10/12/20/30 have no renderable forecast — **today, on real stations.** D2 is live, not theoretical |
| **D1** | a `scope` flag "touches the map's schema, which the map repo owns" | **Wrong owner.** `forecast-lab-snapshot/v2` is OUR contract, generated from OUR Pydantic models, and **forbids unknown fields** — so (b) is a versioned change here *plus* a consumer conversation |

And two things I had not thought through:

- **Enumerating group models changes 137 stations, not 2.** A member with no forecast gains a
  `reason="no_forecast"` entry. The map sees 137 of those the day this ships — correct behaviour,
  and now in T2's handoff so they are told rather than surprised.
- **T1's verification was not executable.** Seven construction sites, not two (five are test
  constructors, and `tests/unit/api/conftest.py:68` lacks `group_store`, so touching the route alone
  breaks route tests on a missing fixture). `fetch_group_model_assignments` returns **both**
  statuses. Deduplication must take the **minimum** priority across all overlapping groups. And the
  byte-identical check must be per-STATION with a frozen clock — snapshot-wide status may
  legitimately change.

⭐ **The lesson that keeps recurring, in a new dress:** *I reasoned "50 > 30, therefore not primary"
from a number instead of reading the selection rule. The rule is "first renderable", and the
protection I described does not exist.*
- **2026-09-25** — ⚖️ **D1 and D2 CLOSED by the owner.** D1 on (a): *"the map does not care if a
  model is group-scoped or not"* ⇒ no `scope` field and no change to `forecast-lab-snapshot/v2`.
  D2 on (a): *"the group model can be a station's headline forecast"* ⇒ priority is uniform, and
  T1's verification asserts **both** directions of § (8) because the approved behaviour is the one
  the first draft wrongly called impossible. `open_decisions` now empty.
