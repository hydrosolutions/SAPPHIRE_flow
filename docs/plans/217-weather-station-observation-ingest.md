---
status: DRAFT
created: 2026-08-30
plan: 217
title: M-G1 — weather-station observation ingest (station selection, eligibility, cursor)
scope: Teach the observation ingest flow to fetch StationKind.WEATHER stations, gate them on the right status, and track their cursor under the right parameter. NOT the precipitation QC rule set (that is M-I4, which the milestone gates behind M-G2), NOT the DHM adapter (M-G2), NOT any real DHM data.
depends_on: [172, 173]
source: docs/design/dhm-precipitation-milestones.md § M-G1, authorised by M-DEC 2026-08-30
---

# Plan 217 — M-G1 weather-station observation ingest

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ PROPORTIONALITY IS BINDING

**This is a gap-fill, not a subsystem.** Onboarding already handles WEATHER; the QC checker, the store,
the health record and the flow all exist. No new framework, abstraction layer, config surface or file
format. **Adding length is a cost.**

## Why this plan exists

Owner authorised Phase-2 (M-DEC) on 2026-08-30. **M-G1 is the half that is not blocked**: it is an
internal gap, not an integration. M-G2 (the DHM adapter) stays blocked until DHM tells us what their
precipitation API looks like.

## The gap, measured

- **Ingest fetches RIVER + LAKE only** — `flows/ingest_observations.py:557-559`. WEATHER stations are
  never polled, though `StationKind.WEATHER` exists (`types/enums.py:165`) and **onboarding already
  handles it**, marking weather stations OPERATIONAL (`services/onboarding.py:1053`, also `:537`,
  `:797`, `:910`).
- **The parameter selection is a binary with no weather branch** — `"water_level" if LAKE else
  "discharge"` (`ingest_observations.py:585-590`). It governs the **`fetch_latest_timestamp` cursor**,
  not what the adapter polls (see D3) — so a weather station would resume from a *discharge* watermark.
- **Eligibility is river semantics applied by accident.** The filter requires
  `GaugingStatus.GAUGED` (`:562`), and `Station.gauging_status` *defaults* to `GAUGED`
  (`types/station.py:52`) — so weather stations would pass the filter by default rather than by
  decision. "Gauged" is a discharge concept; D2 settles what it means here.

## ⛔ The precipitation QC rules are broken — and fixing them is M-I4's job, not this plan's

An independent review (2026-08-30) overturned this plan's first draft, which folded the rule-set
correction in here. **The milestone gates M-I4 on M-I1, M-G1 AND M-G2**
(`docs/design/dhm-precipitation-milestones.md:1272`, graph at `:1338`) and states that G1 alone is
insufficient *while no adapter maps precipitation*. ⇒ **Config binding and the defect-oriented
end-to-end QC test belong to M-I4, after M-G2.** This plan is station selection, eligibility and cursor.

**What the review established about the rules — recorded here so M-I4 inherits it rather than
rediscovering it:**

1. **Hourly precipitation currently matches NO range rule.** Rules select on **exact** parameter *and*
   time-step equality (`types/domain.py:160`); the bound is registered at 86,400 s
   (`config/qc_rules.py:217`, and deployed `config.toml:364`). ⇒ A 400 mm/h value passes because the
   daily rule is **silently skipped**, not because it is checked against 500.
2. 🔴 **The bound is already decided: `200.0 mm/h`.** Plan 173 chose it as a physical-impossibility
   bound with 100 mm/h passing and 200.1 failing, partly from Nepal extreme-hourly literature rather
   than this sample's maximum (`docs/plans/archive/173-fit-for-purpose-qc-mask.md:111`). ⛔ M-I4 must
   **not** leave this to an implementer — the answer exists.
3. **Two rule sources must both change.** Production sets `SAPPHIRE_CONFIG=/app/config.toml`
   (`docker-compose.yml:150`), so the flow loads file-backed rules (`ingest_observations.py:73`).
   ⛔ Editing only `config/qc_rules.py` leaves deployed behaviour unchanged.
4. **The default QC window cannot see the run it is meant to catch.** Ingest examines two hours of
   history (`ingest_observations.py:247`) while the stuck-high rule needs **12 consecutive hourly
   values** (Plan 173:61). ⛔ A test that widens the window would not prove deployed behaviour.
5. `gross_outlier` is unsuitable for zero-inflated precipitation, but ⚠️ **"it never flags zeros" is
   FALSE** — a zero is flagged whenever `|0 − rolling_mean| > k·rolling_std`, and Plan 172 already
   corrected that wording (`docs/plans/archive/172-…md:139`). The milestone text still carries the
   refuted claim; M-I4 should fix it there.
6. No `frozen_sensor` binding exists for precipitation, though Plan 172 built the value exclusion
   (`services/qc.py:92`) precisely so one could.

⚠️ **Consequence for THIS plan, stated plainly:** until M-I4 lands, weather observations ingested by
M-G1 pass QC because **no rule matches them**, and are stamped `qc_rule_version="1.0"`
(`services/qc_datum.py:23`) — a version implying a check that did not happen. **M-G1 must not be
scheduled against a live feed before M-I4.**

## Decisions

- **D1 — WEATHER joins the existing fetch; it does not get its own flow.** Add
  `fetch_all_stations(kind=StationKind.WEATHER)` beside RIVER and LAKE (`ingest_observations.py:557`).
  ⛔ One flow, one health record, one QC path.

- **D2 — `GaugingStatus` does NOT gate weather stations.** "Gauged" describes whether a river station
  has a rating curve. Eligibility requires `GAUGED` (`ingest_observations.py:561`) while
  `Station.gauging_status` **defaults** to `GAUGED` (`types/station.py:52`), so weather stations would
  pass by accident. ⇒ Apply the gauging filter to RIVER/LAKE **only**; gate WEATHER on
  `station_status` alone.

- **D3 — The parameter mapping fixes the CURSOR, not the poll.** ⚠️ The first draft said a weather
  station would be "polled for discharge". **That is wrong**: the flow passes no parameter to the
  adapter, which receives station configs and timestamps only (`protocols/adapters.py:123`), and the
  production adapter *explicitly skips* WEATHER (`adapters/hydro_scraper.py:125`). What the binary
  `"water_level" if LAKE else "discharge"` (`:585`) actually controls is the
  **`fetch_latest_timestamp` cursor** — a weather station would resume from its *discharge* watermark,
  which is wrong and usually absent. ⇒ Replace the binary with an explicit `StationKind` mapping;
  WEATHER → `"precipitation"`. ⛔ An unhandled kind must raise, not default.

- **D4 — CALCULATED derivation keeps the hydro subset.** Adding WEATHER to `all_stations` also exposes
  it to the later calculated-station selection, which has no `StationKind` guard
  (`ingest_observations.py:681`). ⛔ Derivation is a discharge concept; keep it on RIVER/LAKE.

- **D5 — Do not claim production polling before M-G2.** The default adapter drops WEATHER without
  producing an outcome (`adapters/hydro_scraper.py:123`), while `IngestResult.stations_polled` counts
  flow eligibility (`:617`) and health counts derive from adapter outcomes (`:153`). ⇒ After this plan,
  weather stations are **eligible and cursor-correct but unserved** until an adapter maps them.
  ⛔ State that in the exit; do not report a polled weather station as a working feed.

- **D6 — The implausible-dry-run gap is recorded, not closed.** M-I1 excluded it: it needs a dry-spell
  climatology that cannot be estimated from a record containing false zeros without circularity, and
  `QcRuleParams.thresholds` holds only scalars (`types/domain.py:152`). ⇒ **A new stuck-at-zero run
  will not be caught.** ⛔ Do not invent a substitute rule here.

## Tasks

### T1 — fetch and gate weather stations (depends: nothing)
**In:** D1 fetch; D2 eligibility split (gauging applies to RIVER/LAKE only); D4 calculated-derivation
guard.
**Out:** any QC rule change; any adapter; any parameter mapping (T2).
**Verify:** an UNGAUGED **weather** station is eligible while an UNGAUGED **river** station is not; a
weather station never reaches calculated derivation; `IngestResult` counts are unchanged for a
RIVER/LAKE-only deployment. ⛔ That last one proves this plan changes nothing for existing users.

### T2 — the cursor mapping (depends: nothing; parallel with T1)
**In:** D3 — replace the `"water_level" if LAKE else "discharge"` binary with an explicit
`StationKind` → parameter mapping used by the `fetch_latest_timestamp` cursor.
**Out:** any claim about what the adapter polls.
**Verify:** a weather station's cursor is read under `"precipitation"` and never `"discharge"`; an
unhandled `StationKind` **raises** rather than defaulting. ⛔ Prove the raise by adding a kind.

### T3 — end to end on the replay path (depends: T1, T2)
**In:** run the ingest flow against a replay fixture containing a weather station
(`adapters/replay/station.py:97` reads the parameter per fixture row), asserting eligibility, cursor,
storage and `IngestResult` counts.
**Out:** ⛔ **any QC-defect assertion** — a stuck-high block, false-zero run or sentinel belongs to
M-I4's end-to-end test, after the rules exist. Asserting QC behaviour now would lock in the
no-matching-rule state as if it were intended.
**Verify:** the observation is stored under `"precipitation"`; the health record is written before QC
as the existing flow requires; and the run is **explicitly recorded as passing QC only because no rule
matched** — the honest statement of where M-G1 leaves things.

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1", "T2"], "parallel": true},
    {"id": "phase-2", "tasks": ["T3"], "parallel": false, "depends_on": ["phase-1"]}
  ]
}
```

## Exit

The ingest flow fetches WEATHER stations, gates them on `station_status` rather than river gauging
semantics, tracks their cursor under `"precipitation"`, and keeps them out of calculated derivation —
demonstrated on the replay path. ⛔ **Weather stations are eligible and cursor-correct but UNSERVED**
until M-G2 supplies an adapter, and any observation that does arrive **passes QC only because no rule
matches it** (see above). **M-G1 must not be scheduled against a live feed before M-I4.**

## Non-goals

The precipitation QC rule set and its config binding (**M-I4**, gated behind M-G2 — the findings above
are recorded for it) · the DHM adapter (**M-G2**) · any real DHM data · the implausible-dry-run rule
(D6) · new `QcRuleId` values · temperature or other weather parameters · scheduling or deployment
changes · anything in Track A.
