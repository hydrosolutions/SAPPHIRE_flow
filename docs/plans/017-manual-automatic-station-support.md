---
status: DRAFT
created: 2026-04-01
scope: design — manual vs automatic station classification, per-station observation frequency, watchdog/QC implications
depends_on: [015]  # target: v1
---

# 017 — Manual vs Automatic Station Support

## Problem

Central Asia (and other deployment regions) operate mixed networks: **manual stations**
(human observer, 2 readings/day) and **automatic stations** (telemetry, every 10-15
minutes). The current architecture assumes uniform observation frequency per adapter
type — `expected_interval_hours` is configured per-adapter, not per-station
(`architecture-context.md` line 478: "Not per-station — too tedious to configure").

This causes three concrete failures in mixed networks:

1. **Watchdog false alerts (Flow 4)** — Flow 4 step 4.2 computes a per-station overdue
   flag using `expected_interval_hours` from the adapter config (e.g. 0.17h = 10 min
   for BAFU). Manual stations sending observations every 12h are permanently overdue.
   The overdue flag propagates through steps 4.5→4.6→4.7, generating constant ops alert
   noise. (Flow 4 is deferred to v0c or v1 per `v0-scope.md` — this failure applies
   when Flow 4 is implemented, not to current v0 code.)
2. **Frozen-sensor QC false positives** — The QC system selects rules via
   `QcRuleSet.rules_for(parameter, time_step)` (`types-and-protocols.md` line 458).
   Different `time_step` buckets can carry different `frozen_sensor` thresholds (e.g.
   `min_consecutive=6` for 10-minute data, `min_consecutive=20` for 12-hour data). The
   problem: the `time_step` passed to `rules_for()` is currently derived from the
   per-adapter `expected_interval_hours`, so manual stations on mixed-network adapters
   get the automatic-station QC bucket. "N consecutive identical values" is a fault
   signal at 10-minute resolution but normal for a manual station reading a stable
   river twice a day.
3. **Flow 1 staleness warnings** — `observation_staleness_warning_hours` on
   `DeploymentConfig` (default 6.0, types-and-protocols.md line 2330) is checked in
   Flow 1 step 1.6. For manual stations whose most recent observation is routinely
   >6h old, every forecast gets a staleness warning flag
   (`observation_staleness_hours` on the forecast record). This is a different
   mechanism from failure 1 — Flow 1 proceeds with the forecast but records a warning;
   Flow 4 raises an ops alert. A secondary consequence: these expected-but-flagged
   staleness events generate WARNING-level log entries, creating log noise for manual
   stations whose observations are within their normal cadence.

## Design Questions

1. **Station automation level (resolved)** — New enum (e.g. `AutomationLevel`: `MANUAL`,
   `AUTOMATIC`) on station metadata. Only meaningful for gauged stations
   (`GaugingStatus.GAUGED`) — ungauged stations have no observations, and calculated
   stations (`GaugingStatus.CALCULATED`) inherit their effective observation cadence
   from their component stations (plan 015 Flow 4 row: "Monitor that component stations
   are fresh; derived freshness is implicit"). Storing `AutomationLevel` on calculated
   stations would be redundant metadata that goes stale if a component station changes
   from automatic to manual. Additionally, calculated stations are exempt from
   rule-based QC entirely (plan 015 D6: only propagated flags apply), so `time_step`
   selection — the primary downstream consumer of `AutomationLevel` — is structurally
   inapplicable for them. The QC `time_step` for calculated stations' component
   observations is determined by each component station's own `AutomationLevel`.

   **Onboarding invariant**: `automation_level` must be non-`None` for every station
   with `gauging_status == GAUGED`. This is enforced via a `__post_init__` check on
   `StationConfig` (`if self.gauging_status == GaugingStatus.GAUGED and
   self.automation_level is None: raise ValueError(...)`) and validated at station
   onboarding (Flow 5). The `None` sentinel is unambiguous: it means "not applicable"
   (ungauged or calculated), never "not yet classified." A station's automation level
   is known at registration time — the operator knows whether it has telemetry or a
   human observer.

2. **Per-station expected interval (resolved)** — Derive the per-station observation
   interval from `AutomationLevel` via a **deployment-configurable mapping** (e.g.
   `MANUAL → 12h`, `AUTOMATIC → 0.17h`). The mapping lives in `DeploymentConfig` (or
   adapter config), not hardcoded — Nepal's "manual" might be 12h while another
   deployment's might be 6h. The enum selects the bucket; the deployment config defines
   what each bucket means in hours.

   **Why enum-derived, not a per-station float field**: `QcRuleSet.rules_for()` uses
   exact equality on `time_step` (`types-and-protocols.md` line 460), so the per-station
   nominal interval must exactly match a configured `QcRuleParams` bucket. A free-form
   `expected_interval_hours: float` field creates an ops trap — any value that doesn't
   match a bucket silently bypasses QC (see Design Question 2a below). Enum-derived
   lookup constrains the valid set to the configured buckets, eliminating this failure
   mode by construction. If the enum proves too coarse for a future deployment (e.g. a
   network with 30-minute automatic stations), extend the enum (`AUTOMATIC_30MIN` or
   similar) — enums are extensible by design.

   This single value feeds **all three** downstream mechanisms:
   - **QC time-step selection**: passed as the `time_step` argument to
     `QcRuleSet.rules_for()`, selecting the correct QC rule bucket for the station's
     observation cadence. This is the primary QC calibration mechanism — different
     `frozen_sensor` thresholds per time-step bucket are already supported by
     `QcRuleParams` (`types-and-protocols.md` lines 422–445).
   - **Flow 1 staleness threshold**: per-station override of
     `observation_staleness_warning_hours` (step 1.6 warning).
   - **Flow 4 overdue threshold**: per-station override of `expected_interval_hours`
     (step 4.2 overdue flag, when Flow 4 is implemented).

2a. **Guard against silent QC bypass (resolved)** — Because `rules_for()` returns an empty tuple
   silently when no `QcRuleParams` entry matches the station's `time_step`, two guards
   are required:
   - **Onboarding-time validation** (primary): when a station is onboarded or its
     `automation_level` is changed, validate that a `QcRuleParams` entry exists for the
     resolved `time_step`. Reject the configuration if not. This is consistent with
     "parse, don't validate" — invalid configurations are caught at ingestion.
   - **Runtime warning** (belt-and-suspenders): if `rules_for()` returns an empty tuple
     for a station with `gauging_status == GAUGED`, log a WARNING. This catches
     configuration drift (e.g. a QcRuleParams entry deleted after onboarding).

3. **QC rule parameterization (resolved)** — The primary mechanism is already built into the type
   system: `QcRuleParams` is keyed on `(rule_id, parameter, time_step)`, so configuring
   separate `frozen_sensor` rules for `time_step=timedelta(minutes=10)` (automatic) and
   `time_step=timedelta(hours=12)` (manual) with different `min_consecutive` values
   requires no new types. The per-station expected interval (Design Question 2) selects
   the correct bucket via `QcRuleSet.rules_for()`.

   `StationQcOverride` (`types-and-protocols.md` lines 463–477) remains the escape
   hatch for individual station tuning beyond the time-step bucket defaults. Overrides
   are keyed on `(station_id, rule_id, parameter, time_step)` — the `time_step` must
   match the station's nominal interval.

   **Override orphaning risk**: if a station's `automation_level` changes (e.g.
   `AUTOMATIC → MANUAL`), its resolved `time_step` changes. Existing
   `StationQcOverride` rows keyed on the old `time_step` become orphaned and silently
   stop applying. The service layer that updates `automation_level` must identify and
   warn about (or delete) orphaned override rows. This is an implementation concern for
   the station management service, not a type-system change.

   Deployment configs should auto-populate QC rule entries for both automatic and manual
   time-step buckets at onboarding. This eliminates the silent-bypass failure mode where
   an operator onboards a manual station but forgets to configure `QcRuleParams` for the
   12h bucket. The onboarding validation (Design Question 2a) is the hard guard;
   auto-population is the convenience layer that prevents it from firing unnecessarily.

4. **WMO alignment (resolved)** — `wmo.md` references WMO-49 Vol III for "station
   classification," but investigation shows WMO-49 Vol III classifies stations by
   **network role** (principal, secondary, special-purpose), not by automation level.
   The manual/automatic distinction comes from two other WMO sources:
   - **WMO-1160** (Manual on WIGOS): glossary defines "automatic station" and "manual
     station" as the standard terms — matching our `AutomationLevel` enum values exactly.
   - **WMO-168** (Guide to Hydrological Practices): uses "manual stations" vs "recording
     stations" / "automatic stations" as the primary operational dichotomy (Vol I
     Table I.2.9, §21.3, §23.2).

   The WMDR metadata standard (WMO-1192) captures this distinction per-instrument via
   `ObservingMethodTerrestrial` codes (e.g. 348 "non-recording gauge" = manual, 354
   "recording gauge" = automatic), but has **no station-level automation field**. Our
   `AutomationLevel` enum is therefore a station-level summary of the predominant
   observation method — a practical classification that WMO supports terminologically
   but does not formalise as a single metadata field.

   No changes needed to enum values: `MANUAL` and `AUTOMATIC` align with WMO-1160
   glossary terms. `wmo.md` §Observation QC should be updated to note that the
   manual/automatic station distinction derives from WMO-1160/WMO-168 (not WMO-49
   Vol III, which covers network role classification only).

## Standards Document Updates

When this plan is implemented:

- **`types-and-protocols.md`**: Add `AutomationLevel` enum; add `automation_level`
  field to `StationConfig` (nullable — `None` for ungauged and calculated stations,
  non-`None` required for `GAUGED` via `__post_init__` invariant); add
  `automation_level_interval_mapping` to `DeploymentConfig` (the deployment-configurable
  `AutomationLevel → timedelta` lookup)
- **`conventions.md`**: Add `AutomationLevel` to enum master list; add observation QC
  rule IDs row if not yet added by plan 015's v1 implementation (pre-existing gap:
  conventions.md currently lists forecast QC rule IDs only)
- **`v0-scope.md`**: Add §I6 entry (complements §I5, which guards the gauging status
  assumption) — "Do not assume the adapter-level `expected_interval_hours` is the
  correct observation interval for every station on that adapter. v0 stations are all
  automatic (v0-scope.md §I5) with adapter-level observation intervals
  (architecture-context.md §Pipeline monitoring schedule config); v1 (plan 017)
  requires per-station interval derived from `AutomationLevel` for mixed
  manual/automatic networks. Avoid coupling Flow 1 step 1.6 staleness checks and QC
  `time_step` selection to adapter-level config in ways that cannot be overridden per
  station."
- **`architecture-context.md`**: Update pipeline monitoring schedule config section
  to document per-station interval derived from `AutomationLevel` (replacing
  adapter-level `expected_interval_hours` for observation freshness); update Flow 1
  step 1.6 to note per-station staleness threshold
- **`logging.md`**: Add `automation_level` as recommended context field for QC tasks
  (specify bind point — e.g. observation QC task entry in Flow 2). Staleness warnings
  for manual stations whose observations are within the expected interval should be
  downgraded from WARNING to INFO — this is a new logging pattern (context-conditional
  level change) that requires explicit documentation in `logging.md`. The WARNING level
  definition (logging.md lines 247–250) must be amended to note that context-conditional
  downgrades are permitted when documented
- **`wmo.md`**: Update §Observation QC to note that the manual/automatic station
  distinction derives from WMO-1160 (WIGOS Manual glossary) and WMO-168 (Guide to
  Hydrological Practices), not from WMO-49 Vol III (which covers network role
  classification: principal/secondary/special-purpose). Add `AutomationLevel` mapping
  to the §Station metadata section

## Urgency

v1 target. Required for Nepal (DHM operates both manual and automatic stations) and
Central Asia deployments. Not needed for v0 (Swiss BAFU/SMN stations are all automatic).

## Origin

Identified during plan 015 review (2026-04-01). The manual/automatic distinction is
orthogonal to virtual station support but surfaced in the same design discussion.
