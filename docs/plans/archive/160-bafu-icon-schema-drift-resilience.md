---
status: COMPLETE
created: 2026-08-13
plan: 160
title: BAFU forecast adapter — survive upstream schema drift instead of dying on one station
scope: Fix the live outage caused by BAFU adding the icon value `river_missing`, and close the failure CLASS behind it: one station out of 54 invalidating the entire inventory. Models the icon compositionally as (water body kind, data present) so the `lake_missing` that will follow does not break us again; contains per-station validation failures so a bad feature is skipped and recorded rather than aborting the batch; and emits drift telemetry so the next BAFU vocabulary change arrives as a warning rather than a 16-hour outage. Auditing the other Literal-typed external vocabularies is a named follow-on.
depends_on: []
blocks: []
supersedes: []
---

# Plan 160 — BAFU forecast adapter: schema-drift resilience

## Status
**COMPLETE** — shipped on `main` as `2bfb24d` (fix(bafu): survive BAFU icon schema drift instead of dying on one station (Plan 160) (#146)).
*(Status reconciled 2026-08-21 in a housekeeping pass: the plan had shipped but was still marked READY, so it read as outstanding work.)*

**READY** (2026-08-13). Operational reliability (category **A**). Fixes a **live, ongoing** collection outage.

## Problem

**The BAFU forecast collector has been dead since 2026-08-12 ~16:00 UTC.** Every scheduled run crashes:

```
BAFU forecast station GeoJSON ... failed schema validation:
features.14.properties.icon
  Input should be 'river', 'lake' or 'missing'  [input_value='river_missing']
```

`BafuIcon = Literal["river", "lake", "missing"]` (`types/bafu_forecast.py:25`) is validated on every feature by
`_StationPropertiesModel` (`adapters/bafu_forecast.py:106`), and `fetch_station_inventory` validates the **whole
collection in one call** (`:186-190`), raising `AdapterError` on any failure. So one unrecognised value in one
station aborts the inventory for all 54.

**Detected by the Slack alerting staged the previous day** (Plan 158 D1) — its first real catch, ~16 h after going
live. Without it this would have run silently, exactly like the 14-day engine outage.

**Blast radius is limited and should not be overstated:** only the BAFU *forecast* collector (the Plan 111
benchmarking feed) is affected. Observations, NWP and operational forecasting are unaffected and were verified
healthy during triage.

### What the live feed actually says (probed 2026-08-13)

| icons across 54 features | count |
|---|---|
| `river` | 40 |
| `lake` | 13 |
| **`river_missing`** | **1** |
| `missing` | **0** |

The affected station is **`2034` "Broye - Payerne, Caserne d'aviation"**, and its own payload settles the semantics
without needing to ask BAFU:

- `icon_path: /assets/map-symbols/fluss_keine_daten.svg` — *fluss keine daten* is German for **"river, no data"**.
- Its properties carry `failure_text` and `failure_valid_from`, i.e. BAFU models this as a station in a failure state.
- It still reports a stale `last_value: 0.254` with `metric: discharge_ms`, so "has a value" is **not** evidence of
  current data.

Two consequences follow, and they are the heart of this plan:

1. **`river_missing` routes like `river` — verified, not assumed.** The intuitive reading ("no data → skip, like
   `missing`") is **wrong**: probing the forecast endpoint for station `2034` returns **HTTP 200 with a full
   27,646-byte forecast**, comparable to a healthy control station. The icon reflects the **live gauge** state on
   BAFU's web map, not forecast availability, so skipping `*_missing` would silently discard usable data for every
   station whose sensor is temporarily down. See D8.
2. **The vocabulary is compositional, not a flat enum**: `{river|lake}` × optional `_missing`. Plain `missing` no
   longer appears at all (0 of 54), which suggests BAFU migrated *away* from it. **`lake_missing` therefore almost
   certainly exists** and will break the collector identically the first time a lake station goes down. Fixing only
   `river_missing` schedules the next outage.

### The real defect is the containment, not the enum

One station in 54 invalidating the whole inventory is the same failure class Plan 154 fixed one level down: a
**per-entity** problem escalated into a **batch-wide** abort. Adding an enum value fixes today; per-station
containment fixes the class.

## Design

- **D1 — Model the icon compositionally.** Replace the flat `BafuIcon` literal with a parse into a small frozen
  value type carrying **water-body kind** (`river` | `lake`) and **data presence** (`present` | `missing`), derived
  from the `{kind}` / `{kind}_missing` pattern. `lake_missing` is then supported **before it is ever seen**, and
  routing reads off the semantics rather than string identity. Plain `missing` stays accepted as a legacy value —
  it is absent from the feed today, but nothing says BAFU cannot emit it, and accepting it costs one branch.
- **D2 — Routing follows water-body KIND; the `_missing` suffix does NOT suppress the fetch** (D8, probe-backed).
  `_variants_for_station` becomes: **lake (with or without live data) → `q_forecast` + `p_forecast`**; **river (with
  or without live data) → `q_forecast`**; **bare legacy `missing` → `()` unchanged**.
  - **Why bare `missing` is left alone:** it appears **0 times** in the current feed, so there is no evidence about
    what it means today, and it plausibly denotes something different from a temporary gauge outage (a decommissioned
    or sensor-less site). Changing behaviour we cannot observe would be guessing in the opposite direction. This plan
    changes only what it measured.
  - The existing politeness rationale ("skip rather than waste 404 requests") is preserved for bare `missing` and is
    simply **not applicable** to `*_missing`, which demonstrably returns 200.
- **D3 — Per-station validation containment (the class fix).** `fetch_station_inventory` must validate features
  **individually**: a feature that fails validation is **skipped and recorded**, and the remaining stations are
  returned. Reserve the batch-wide `AdapterError` for a payload that is structurally unusable (not JSON, no
  `features`, unparseable `meta.produced_at`) or where **no** station validates — the same "partial vs total"
  distinction Plan 154 drew for per-HRU containment.
- **D4 — An unknown icon skips the station; it never falls through to the river default.** Falling through would
  emit 404-generating fetches against a station whose state we do not understand. Skipping degrades exactly one
  station, keeps the other 53 collecting, and is reversible once the value is understood. **Fail-safe, not
  fail-open.**
- **D5 — Drift is telemetry, not an outage.** An unrecognised icon (or any skipped feature) emits a WARNING with the
  station key and the offending value, **and** a queryable `pipeline_health` record — per
  `docs/standards/logging.md` § "Audit log vs application log", which requires resilience events that affect
  operator trust to be queryable, not log-only. The next BAFU vocabulary change should reach us as a warning while
  collection continues.
- **D6 — The regression test must prove the class is closed, not just the instance.** Pinning `river_missing`
  alone would leave `lake_missing` to repeat the outage. Tests must cover: `river_missing` → **`("q_forecast",)`**;
  **`lake_missing` → `("q_forecast","p_forecast")`** (unseen today — the forward-looking lock); an **arbitrary
  unknown** icon **skipped** with the other stations still returned; and `river`/`lake`/bare `missing` behaviour
  **unchanged**. Note the deliberate asymmetry: a *recognised* `_missing` station is **fetched** (D8 probe), while an
  *unrecognised* icon is **skipped** (D4) — known-safe versus unknown-unsafe.

## Phases

- **T1 — Compositional icon type + routing (D1, D2).**
  - **In scope:** `types/bafu_forecast.py` (replace the flat literal with the parsed type), `adapters/bafu_forecast.py`
    (`:106` model field, `:205` construction), `flows/collect_bafu_forecasts.py:215-227` (route on water-body kind, D2).
  - **Red-first:** `river_missing` → **`("q_forecast",)`** (fails today with a `ValidationError` before routing is
    even reached); **`lake_missing` → `("q_forecast","p_forecast")`** — unseen in the feed today, and the
    forward-looking lock that stops the next outage; `river`, `lake` and legacy bare `missing` all **unchanged**.
- **T2 — Per-station containment + drift telemetry (D3, D4, D5).**
  - **In scope:** `fetch_station_inventory` (`adapters/bafu_forecast.py:173-200`) validates per feature; skipped
    stations are counted and reported; WARNING + `pipeline_health` emitted; the batch-wide `AdapterError` is
    narrowed to structurally-unusable payloads and the all-stations-failed case.
  - **Red-first:** a 54-feature payload where **one** feature is invalid returns **53 stations** rather than raising
    — the exact production failure, and it fails against current code; a payload where **every** feature is invalid
    still raises; malformed JSON and a bad `meta.produced_at` still raise (unchanged).
- **T3 — Verify against the live feed and restore collection.**
  - Re-run the collector on the mini and confirm a successful cycle plus a fresh `bafu_forecast` heartbeat, clearing
    the staleness alert. Record the skipped-station count (expected **0** — D2/D8 route `river_missing` like `river`,
    they do not skip it). **Deliverable:** confirmation that station `2034` is **retained and fetched** (its
    `river_missing` icon routes to `q_forecast` exactly like a healthy river station) alongside the other 53, for
    **54** stations collected and `skipped_count == 0`.

## Phase dependency graph
```json
{
  "phases": [
    { "id": "T1", "tasks": ["T1"], "parallel": false },
    { "id": "T2", "tasks": ["T2"], "parallel": false, "depends_on": ["T1"] },
    { "id": "T3", "tasks": ["T3"], "parallel": false, "depends_on": ["T2"] }
  ]
}
```

## Non-goals
- **Auditing the other external-vocabulary literals** — `BafuMetric`, `BafuForecastVariant`
  (`types/bafu_forecast.py:18-19`), `LindasKind`, `BafuObservationParameter` (`types/bafu_observation.py:25-30`) all
  carry the identical drift risk. Named follow-on (D9); this plan fixes the one that is on fire and establishes the
  pattern.
- **Changing what we archive or how the scorer works** (Plan 111 territory).
- **Backfilling the missed window.** The BAFU forecast endpoint is forward-only, so 2026-08-12 16:00 → recovery is
  **unrecoverable**. Recorded, not fixed.

## Open items
- **D7-bafu-contact — ⚠️ OWNER.** Ask BAFU whether `hydrodaten` publishes change notices or has a data-services
  contact, and confirm the icon vocabulary (their web-map legend defines these symbols). There is already an open
  channel: Plan 111's scorer is gated on an **unsent BAFU licence**, so this can ride along. **Design assumption
  regardless: no notification.** This feed backs a public web map; it is not a versioned API with a contract, and we
  should not learn about drift from an outage even if a newsletter exists.
- **D8-missing-forecast-fetch — RESOLVED BY PROBE 2026-08-13, and it REVERSES the draft's recommendation.** The
  draft proposed skipping `*_missing` stations by analogy with `missing`. **That would have silently dropped usable
  data.** Probed directly:
  `https://www.hydrodaten.admin.ch/plots/q_forecast/2034_q_forecast_en.json` → **HTTP 200, 27,646 bytes**, against a
  healthy control station (`2009`) at 28,669 bytes. So BAFU **does publish a forecast** for a station whose live
  gauge is down: the icon describes the **measurement** state on their web map, not forecast availability.
  **Therefore `*_missing` routes by water-body KIND, exactly like its non-missing counterpart** — `river_missing` →
  `("q_forecast",)`, `lake_missing` → `("q_forecast","p_forecast")`.
- **D9-vocabulary-audit — follow-on.** Apply D1's compositional/tolerant treatment to the remaining external
  literals above, with the same drift telemetry.
