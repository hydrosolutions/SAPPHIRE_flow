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
5. **The two assignment types differ by ONE field.** `types/station.py:67` `ModelAssignment` has
   `station_id`; `:77` `GroupModelAssignment` has `group_id`. Both then carry `model_id`,
   `time_step`, `status`, `priority`, `created_at`. ⇒ Merging is a projection, not a translation.
6. 🔴 **SEVEN bundle constructors, plus the API fixture dict — not two.**
   ⛔ *An earlier version of this item said "**Two construction sites**". **That is false**, and
   BOTH independent reviews caught it surviving here after the correction had been folded into T1.*
   `grep -rn 'ForecastLabStores(' src tests` returns exactly seven, all passing every field by
   keyword — so a required new field breaks all seven:
   | site | |
   |---|---|
   | `cli/export_forecast_lab.py:76` | production |
   | `api/routes/forecast_lab.py:68` | production |
   | `tests/unit/cli/test_export_forecast_lab.py:37, :296` | test |
   | `tests/unit/services/forecast_lab/test_snapshot.py:118, :2122` | test |
   | `tests/unit/services/forecast_lab/test_db_sources.py:64` | test |
   ⚠️ **An eighth edit site is NOT a constructor:** `tests/unit/api/conftest.py:69` is a plain dict
   and lacks `group_store`. ⭐ **Production `api/deps.py:72` ALREADY supplies it** — the route change
   is one line and `deps.py` needs nothing. ⛔ *An implementer who does not know that may add a
   duplicate key.*
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
   🔢 **Measured 2026-09-25, not promised:** all 139 members are eligible (`bafu` / `river` /
   `operational`) and **2** have a stored `discharge` forecast ⇒ **137 would gain a `no_forecast`
   entry if the change shipped that day.** ⛔ *An earlier version stated "the map WILL see 137 the
   day this ships" as a delivery promise. It is a dated observation: members qualify continuously,
   so the number falls every day.* ⇒ **T2 re-measures at handoff** rather than quoting 137.
   ⚠️ *"No forecast" means no non-superseded `discharge` forecast returned by retrieval at all —
   **not** "nothing from the latest cycle". An older stored forecast still counts and still renders.*

10. ⭐ **This exact union already exists in merged code.** `services/basin_importer.py:220-231`
    takes ACTIVE station assignments ∪ ACTIVE group assignments via `fetch_groups_for_station` +
    `fetch_group_model_assignments`. ⇒ **T1 is not inventing a pattern**, and an implementer and
    reviewer get a free consistency check. Likewise `StationGroupStore` is already a Protocol
    (`protocols/stores.py:660-701`) and `FakeStationGroupStore` already has
    `seed_group_model_assignment` (`tests/fakes/fake_stores.py:1447-1514`) — no new store code, and
    no new fake.
11. ⚠️ **Neither group query has an `ORDER BY`.** `fetch_groups_for_station`
    (`station_group_store.py:130-142`) and `fetch_group_model_assignments` (`:216-229`) both
    `SELECT` unordered. ⇒ **A tie at the minimum priority has no defined winner**, and the
    verification bullet that reverses insertion order is precisely the test that trips on it.
    T1 defines the tiebreak.

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
- A group store on `ForecastLabStores`, and **all seven constructors plus the API fixture dict —
  the table in § 6.** ⛔ *Updating the route alone makes existing route tests fail on a missing
  fixture key, which reads as an unrelated breakage.* ⭐ *No new Protocol and no new fake are
  needed (§ 10).*
- `fetch_active_model_assignments` extended: station assignments **plus** the group ones, via
  `fetch_groups_for_station` → **`fetch_group_model_assignments`**. ⚠️ **That returns assignments of
  BOTH statuses — filter ACTIVE explicitly**, and filter **before** deduplicating.
- **Deduplicate by `model_id` across station assignments and ALL overlapping groups, taking the
  MINIMUM priority.** Then sort `(priority, model_id)`. ⚠️ *Assignment precedence selects DISPLAY
  priority only — retrieval still returns the latest station/model forecast either way.*
- 🔴 **Define the tiebreak** (§ 11 — neither group query is ordered): at equal minimum priority the
  **station assignment wins**, else the lowest `group_id`. ⛔ *Alternatively state outright that only
  `(model_id, priority)` is consumed downstream so every other field is don't-care — but then the
  tests must not compare whole dataclasses.*
- ⚠️ **Say how a group assignment is materialised.** `_sapphire_entries` annotates its list as
  `list[tuple[ModelAssignment, …]]` (`snapshot.py:341`) and `GroupModelAssignment` has no
  `station_id` (`types/station.py:77`) ⇒ the group assignment is projected into a `ModelAssignment`
  carrying the **enumerating loop's** `station_id`. ⛔ *That fabricates a record with no row in
  `model_assignments`. Harmless — nothing downstream reads it — but it must be stated, not
  discovered.*
- ⚠️ **State the query cost and accept it, or resolve it once per snapshot.** `fetch_groups_for_station`
  materialises every matching group's FULL member set per call (`station_group_store.py:244-263`),
  inside the per-station loop (`snapshot.py:777-783`) ⇒ for a 139-member group, ~139 × 139 member
  rows are built and discarded per export, plus one extra round-trip for every non-member station.
  ⛔ *Not a blocker — but silence here is how a measured cost becomes a surprise.*
- 🔴 **Documentation, which an earlier draft omitted entirely** (⛔ *`CLAUDE.md`: "every code change
  updates affected docs — no exceptions"*). Four places state something this change falsifies:
  | file | what it says now |
  |---|---|
  | `services/forecast_lab/db_sources.py:3-6` | *"no new store code, no new query surface (D14)"* — a group store IS a new query surface; reconcile or record the amendment |
  | `services/forecast_lab/db_sources.py:11-12` | *"Model assignments are filtered to ACTIVE (D17b)"* — must say station **and group** |
  | `services/forecast_lab/snapshot.py:334` | *"one entry per ACTIVE assignment"* |
  | `docs/spec/forecast-lab-snapshot.md:117-119` | *"one entry per assigned model"* — 🔴 **because D1 closed on "nothing marks it group-scoped", this spec is the ONLY place a map developer could ever learn an entry can exist for a model with no station assignment row.** Leaving it makes D1(a) undocumented rather than merely unlabelled |
  | `docs/touchpoint-maps.md:697` | records the exact composition of `ForecastLabStores` (it documents Plan 222 *removing* a field) — a field addition belongs there by the same precedent |
- 🔴 **Eligibility and principal scoping stay BEFORE the group lookup.** ⛔ **Group membership must
  never expand the exported station set** — a member that is not an eligible station does not enter
  the snapshot. **The boundary lives at `api/routes/forecast_lab.py:94-118` (`_resolve_requested_stations`
  — eligibility AND principal scope) and `cli/export_forecast_lab.py:88` (`_resolve_stations` —
  eligibility; the CLI has no principal).** ⚠️ *`build_snapshot` (`snapshot.py:729`) takes an
  already-resolved list and filters NEITHER — so a direct builder test cannot prove this boundary.*
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

**Verification.** ⛔ *An earlier draft named no test files or command at all — and then said so in
its own prose without supplying them.*

**Command:**
```
uv run pytest tests/unit/services/forecast_lab/test_db_sources.py \
              tests/unit/services/forecast_lab/test_snapshot.py \
              tests/unit/cli/test_export_forecast_lab.py \
              tests/unit/api/test_forecast_lab.py
```

- A group-assigned model appears **with its forecast** for a member that HAS a renderable one.
- 🔴 **A member with NO forecast gains an unavailable entry** with `reason="no_forecast"` (§ 9).
- 🔴 **An actual NON-member's station payload is byte-identical** — clock frozen for the comparison,
  and compared at the STATION level. ⚠️ *Snapshot-wide status may legitimately change; asserting the
  whole document unchanged would be wrong.*
- 🔴 **Dedup, with more than one ACTIVE candidate.** ⛔ *An earlier draft tested only an
  ACTIVE/INACTIVE pair — which leaves ONE candidate standing and therefore proves nothing about
  taking the minimum.* The case: model X at station priority **90**, group A **50**, group B **5**,
  plus an INACTIVE X at **0**; model Y at **10**. Expect **X once, before Y**, regardless of group
  or insertion order. ⭐ *This is what distinguishes the specified algorithm from station-first,
  first-group-only, and filter-AFTER-dedup.*
- 🔴 **`is_primary` per D2, asserted BOTH ways** (§ 8): a group model does **not** displace a
  renderable priority-10 model, **and** it **does** become primary when 10/12/20/30 are all
  unrenderable. ⛔ *Testing only the first case would certify a protection that does not exist.*
  ⚠️ **Two traps in direction (ii):** the 90/100 fallbacks must be seeded **renderable**, or the
  test degenerates into "the only renderable entry wins" and proves nothing about displacement; and
  the higher-priority models must be made unrenderable by BOTH routes — **no forecast at all** and
  **a deficient quantile set** (`_is_renderable`, `snapshot.py:266-290`) — because merely omitting
  today's run can leave an older stored forecast winning (§ 9).
- 🔴 **The scoping invariant, asserted — it had NO verification bullet.** ⛔ *By this plan's own
  standard in D2, an invariant marked 🔴 with nothing testing it is the gap.* Two cases, **at the
  caller boundary, not the builder**: (a) a group member whose `station_status` is not
  `operational` does **not** enter the CLI export; (b) a group member outside a non-admin
  principal's `station_ids` does **not** enter the API response.
- ⚠️ **"No new failure mode" cannot mean "no database outage"** — a non-member now incurs a group
  lookup. Existing error propagation is preserved, not swallowed.
- ⭐ **On staging: the pilot's two stations show `cmal_small` in the exported snapshot.**

### T2 — Tell the map session what changed (D1)

**Outcome.** The map knows a new model will appear, how many entries it brings, and that it can
lead a station. ⛔ *Not "whether it can tell it apart" — D1 closed: it does not need to.*

**In.** A short note to the Flow Map side:
- `cmal_small` appears as an **ordinary model entry**. ⛔ *Nothing marks it group-scoped — D1,
  closed: the map does not care, so no schema change is coming.*
- 🔴 **A batch of NEW entries carrying `reason="no_forecast"`** — the group's members that have not
  yet accumulated 30 unbroken days (§ 9). ⛔ **Re-measure the count at handoff; do not quote 137.**
  *It was 137 of 139 on 2026-09-25 and falls every day as members qualify.* They convert to real
  forecasts through early October.
- ⚠️ That a group model **can become a station's primary** where the established models are silent
  (§ 8, D2).

**Out.** ⛔ Editing the map repo or its schema.

**Pre-change.** N/A.

**Verification.** The map session has acknowledged. ⚠️ *Not "a note was written" — the point is that
someone on the other side knows.* ⛔ **Timebox: 3 working days.** *An external party with no timebox
gates plan completion on someone else's inbox; after that, record the note as delivered-unacknowledged
and close T2 — do not hold the plan open indefinitely.*

## Explicitly out of scope

- **The map's operational / threshold-risk mode.** A different product, blocked on a licence
  question about BAFU-derived threshold values — see the Flow Map notes. This plan serves the
  **research comparison** view only.
- **Why 137 of 139 stations have no forecast yet** — they lack 30 unbroken days; they qualify on
  their own by early October.
- **The `_pooled` combination.** ⛔ *An earlier version called it "a station-level model". **That is
  false.*** It has **no assignment row at all** and is exported through the sibling
  `combined_forecast` block, not through assignment enumeration
  (`docs/spec/forecast-lab-snapshot.md:113-119`). ⇒ It is out of scope because this plan changes
  enumeration, which never touched it — not because it is station-scoped.

```json
{
  "phases": [
    {"phase": 1, "tasks": ["T1"], "parallel": false},
    {"phase": 2, "tasks": ["T2"], "parallel": false, "note": "after T1 ships — tell them what IS, not what will be; this is deliberately NOT advance notice"}
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
- **2026-09-25 — round 2, TWO independent reviews (Codex + Claude), both NEEDS CHANGES.** They
  agreed on the two blockers, and every claim below was re-verified against the code before folding.
  - 🔴 **§ 6 still said "Two construction sites"** after the correction had been folded into T1 and
    the changelog. ⛔ *This is the documented failure mode exactly — correct the text in one place,
    leave the original standing. Both reviewers found it independently.* § 6 now carries the table
    and an explicit ⛔ correction marker.
  - 🔴 **T1 updated NO documentation**, in a repo whose `CLAUDE.md` forbids that. Five surfaces
    state something this change falsifies; all five are now named in T1's In-list. ⭐ *The snapshot
    spec matters most: because D1 closed on "nothing marks it group-scoped", that spec is the only
    place a map developer could ever learn an entry can exist with no station assignment row.*
  - **The dedup verification could not tell a correct implementation from three wrong ones** — an
    ACTIVE/INACTIVE pair leaves one candidate standing. Replaced with a three-ACTIVE overlapping-group
    case.
  - **The one invariant marked 🔴 had no verification bullet**, and the boundary it depends on was
    unnamed. Both fixed; the tests must run at the CALLER, since `build_snapshot` filters neither.
  - **"137 entries the day this ships" was a promise built from a dated measurement.** Re-measured:
    all 139 members eligible, 2 with forecasts. Now stated as an observation, and T2 re-measures.
  - **`_pooled` was called "a station-level model".** False — it has no assignment row and travels
    through a different block. The exclusion stands; its stated reason did not.
  - **D2 direction (ii) would have passed on a degenerate fixture** — the fallbacks must be seeded
    renderable, and unrenderability must be exercised by both routes, because an older stored
    forecast still renders.
  - Added: the tiebreak (neither group query is ordered), how a group assignment is materialised,
    the per-snapshot query cost, a timebox on T2's external acknowledgement, the named test command,
    and § 10 — ⭐ **`services/basin_importer.py:220-231` is merged code doing exactly this union.**
