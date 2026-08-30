---
status: DRAFT
created: 2026-08-30
plan: 217
title: M-G1 — weather-station observation ingest, and the precipitation rule set it must be QC'd with
scope: Teach the observation ingest flow to fetch StationKind.WEATHER stations and route precipitation through the QC checker with a rule set that is correct for precipitation. NOT the DHM adapter (M-G2, blocked on the API), NOT the implausible-dry-run rule (M-I1 excluded it), NOT any real DHM data.
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
  "discharge"` (`ingest_observations.py:585-590`). ⛔ A weather station reaching that line today would
  be polled for **discharge**, silently.
- **Eligibility is river semantics applied by accident.** The filter requires
  `GaugingStatus.GAUGED` (`:562`), and `Station.gauging_status` *defaults* to `GAUGED`
  (`types/station.py:52`) — so weather stations would pass the filter by default rather than by
  decision. "Gauged" is a discharge concept; D2 settles what it means here.

## ⛔ The QC finding that changes this plan's shape

**A precipitation rule set already exists in deployed config — and it is wrong on three counts**
(`config/qc_rules.py:217-231`):

1. **`range_check` is bound at `timedelta(seconds=86400)` — DAILY.** The live feed is understood to be
   10–15 minute, aggregated hourly. A daily 0–500 mm bound admits an hourly value of 400 mm.
2. **`gross_outlier` is bound at `k_sigma=5.0`, and M-I1 says explicitly DO NOT USE IT.** It is a
   symmetric `|value − mean| > k·std` test (`services/qc.py:197`); on a zero-inflated right-skewed
   variable it **flags real heavy rain and never flags zeros** — the exact inversion of what
   precipitation QC needs.
3. **There is NO `frozen_sensor` rule for precipitation at all.** The rule exists and is bound for
   discharge, water_level and water_temperature — but not precipitation, *despite Plan 172 having built
   the value exclusion precisely so it could be used for precipitation.*

⇒ **Fetching weather stations without fixing this ships data through QC that research already
rejected.** That is worse than not ingesting: a stuck sensor would pass, real heavy rain would be
flagged, and both would carry a `qc_rule_version` implying they had been checked properly.
**The rule-set correction is therefore IN SCOPE here, not deferred to M-I4.**

## Decisions

- **D1 — WEATHER joins the existing fetch; it does not get its own flow.** Add
  `fetch_all_stations(kind=StationKind.WEATHER)` beside RIVER and LAKE. ⛔ One flow, one health record,
  one QC path — a parallel weather pipeline would double the surface for no gain.

- **D2 — `GaugingStatus` does NOT gate weather stations.** "Gauged" describes whether a river station
  has a rating curve; it is meaningless for a rain gauge, and weather stations currently pass only
  because `GAUGED` is the dataclass default. ⇒ Apply the gauging filter to RIVER/LAKE **only**; gate
  WEATHER on `station_status` alone. ⛔ Do not leave it implicit — a later change to the default would
  silently empty the weather feed.

- **D3 — Parameter selection becomes an explicit mapping keyed on `StationKind`**, not an `if/else`.
  WEATHER → `"precipitation"`. ⛔ The current binary has no weather branch and would poll a rain gauge
  for discharge; a mapping makes an unhandled kind a visible failure rather than a wrong default.

- **D4 — Correct the precipitation rule set, and state each change's reason in config.**
  - `range_check` → **hourly** `time_step`, with an hourly-plausible `value_max`. ⛔ State the value and
    its basis; do not carry the daily 500 mm across.
  - **Remove `gross_outlier` for precipitation** (M-I1: it flags real heavy rain and never flags zeros).
  - **Add `frozen_sensor` for precipitation** using Plan 172's value exclusion, so runs of legitimate
    zeros do not trip it.
  ⚠️ `QcRuleId` is a closed `Literal` (`types/domain.py:137`) **and** independently allowlisted
  (`config/qc_rules.py:14`) — this plan needs neither changed, because all three rules already exist.

- **D5 — The known QC gap is recorded, not silently carried.** M-I1 deliberately excluded the
  **implausible-dry-run** rule: it needs a dry-spell climatology that cannot be estimated from a record
  containing false zeros without circularity, and the contract cannot express one
  (`QcRuleParams.thresholds` is `dict[str, float]`, scalars only). ⇒ **A new stuck-at-zero run in a live
  feed will not be caught.** State this in the plan's exit and in the config comment. ⛔ Do not invent a
  substitute rule here.

- **D6 — No real DHM data, and no adapter.** M-G2 is blocked on the API. ⇒ Verify end-to-end against
  the existing **replay/fixture** adapter mechanism (Plans 019/020/021/045), with a weather station and
  synthetic precipitation carrying the defects M-A3 found: a stuck-high block, a false-zero run, and a
  sentinel value. ⛔ Synthetic by default — real excerpts only if M-D1 later permits.

## Tasks

### T1 — fetch and route weather stations (depends: nothing)
**In:** D1 fetch, D2 eligibility split, D3 parameter mapping. The `since` lookup
(`ingest_observations.py:585-592`) must use each station's own parameter.
**Out:** any QC rule change; any adapter.
**Verify:** a weather station is polled for `"precipitation"` and never for `"discharge"`; an
UNGAUGED weather station is still polled while an UNGAUGED **river** station is not; an unmapped
`StationKind` raises rather than defaulting. ⛔ The last one is the regression that D3 exists to prevent.

### T2 — correct the precipitation rule set (depends: nothing; parallel with T1)
**In:** D4's three changes, each with its reason in a config comment.
**Out:** any new `QcRuleId`; the implausible-dry-run rule (D5).
**Verify:** against **synthetic** series — a 120-hour stuck-high block is flagged; a long run of
legitimate zeros is **not** flagged by `frozen_sensor`; an hourly value above the new bound is flagged
where the old daily bound admitted it; and a heavy-rain hour that the removed `gross_outlier` would
have flagged now passes. ⛔ That last test is the point of the removal — prove it by reverting.

### T3 — end to end on the replay path (depends: T1, T2)
**In:** run the ingest flow against a fixture weather station whose series carries a stuck-high block,
a false-zero run and a sentinel; assert what is stored, what is flagged, and the counts in
`IngestResult` (`qc_passed`/`qc_failed`/`qc_suspect`).
**Out:** any real DHM endpoint.
**Verify:** the health record is written before QC as the existing flow requires; stored rows carry the
corrected `qc_rule_version`; ⛔ **and the false-zero run is stored UNFLAGGED** — the honest
demonstration of D5's known gap, not a hidden failure.

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1", "T2"], "parallel": true},
    {"id": "phase-2", "tasks": ["T3"], "parallel": false, "depends_on": ["phase-1"]}
  ]
}
```

## Exit

The ingest flow polls WEATHER stations for precipitation through the existing QC and storage path, with
a precipitation rule set that is hourly, has no `gross_outlier`, and has a `frozen_sensor` that
tolerates legitimate zero runs — demonstrated end to end on the replay path against synthetic defects
drawn from M-A3's real findings. **The implausible-dry-run gap (D5) is recorded in config and in this
exit, not closed.**

## Non-goals

The DHM adapter (**M-G2** — blocked on the API) · any real DHM data · the implausible-dry-run rule
(D5) · new `QcRuleId` values · temperature or other weather parameters (precipitation only) · operational
deployment or scheduling changes · anything in Track A.
