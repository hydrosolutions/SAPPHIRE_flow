# Plan 039 — Sensor/Model Failure Visibility for Operators

**Status**: DEFERRED
**Phase**: Flow 4 (pipeline monitoring)
**Depends on**: Flow 4 implementation
**Deferred reason**: Sensor-offline detection belongs to pipeline monitoring
(Flow 4 / `AlertSource.PIPELINE` / `PipelineCheckType.OBSERVATION_FRESHNESS`),
which is deferred to v0c/v1 per `docs/v0-scope.md`. Alerting is default-off
during v0 (per-source flags in `DeploymentConfig`, §A8c) but can be enabled
incrementally within v0 — the stale-alert hazard becomes live when
`enable_observation_alerts` or `enable_forecast_alerts` is set to `true`. At
that point operators see stale RAISED alerts on the dashboard
(`dashboard.py:190,195`) with no indication the station is offline. This is
acceptable risk for early v0 deployment but must be addressed before production
use with human operators. Revisit when Flow 4 is scoped.

## Context

### The problem

When a station's sensors go offline or a model fails to produce a forecast, the
alert checkers cannot evaluate whether a flood alert should be raised or resolved.
The current code handles this by **deferring resolution** — active alerts remain
in their current state (`RAISED` or `ACKNOWLEDGED`) indefinitely until fresh data
arrives.

This creates two operational hazards:

1. **Stale RAISED alerts**: A flood alert raised during a real event persists
   forever after the sensor dies. Operators see an active RED alert for a station
   that has been offline for days. They cannot distinguish "still flooding" from
   "sensor dead, alert frozen."

2. **No visibility**: There is no signal to operators that data is unavailable.
   The alert just... stays. No timestamp of last evaluation, no flag, no visual
   indicator on the dashboard.

### Why auto-resolve is dangerous

The Plan 037 security audit initially proposed auto-resolving alerts when sensors
go offline (`evaluated_parameters` is empty → resolve all active alerts). The
pre-implementation review identified this as **operationally dangerous**:

> A sensor dies during an active flood at danger level RED. The observation alert
> checker runs, gets zero QC-passed observations, `evaluated_parameters` is empty.
> Auto-resolve fires. Operators see the alert cleared. They assume the flood is
> over. It is not.

This is a genuine safety hazard for an operational flood warning system. The
rejection of auto-resolve aligns with WMO-1150 (Guidelines on Multi-Hazard
Impact-Based Forecast and Warning Services), which recommends that warnings be
rescinded only when the hazard has demonstrably passed.

### Current behaviour (v0)

The deferred-resolution approach is **safe for early v0 deployment**: alerting is
default-off (`enable_forecast_alerts: false`, `enable_observation_alerts: false`
per `DeploymentConfig` §A8c), and the staged activation sequence (pipeline alerts
first, then observation, then forecast) means operators control when alerts go
live. The current guards in `observation_alert_checker.py` (line ~106,
`configured.issubset(evaluated_parameters)`) and `alert_checker.py` (line ~138,
`_ensemble_size_adequate`) correctly prevent false resolution.

Note: a dashboard (`dashboard.py`) exists in v0 and already renders active alert
counts and breakdowns by level. It hardcodes `status == "raised"` at lines 190
and 195. Stale alerts will appear as active on this dashboard once alerting is
enabled.

### Existing partial mitigations

The architecture already provides forecaster-facing signals for data degradation
that partially address the visibility gap without touching the alert state machine:

- `observation_staleness_hours` on forecast records — visible in the API and
  dashboard, flags when the most recent observation is older than a configurable
  threshold (Flow 1 note 1.6)
- `InputQualityLevel: FULL | PARTIAL | DEGRADED` with `input_quality_flags` on
  the `forecasts` table — visible to forecasters

These do not address stale *alerts* specifically, but they provide context that
helps operators interpret suspicious alert states.

### Architectural decision: pipeline alerts, not alert state mutation

`docs/architecture-context.md` Flow 4 (pipeline monitoring) owns detection of
"data source outages, missing observations, stale forecasts" via
`AlertSource.PIPELINE` — deliberately separate from flood alerts. The
`pipeline_health` table already defines `PipelineCheckType.OBSERVATION_FRESHNESS`
with per-station detail fields (`station_code`, `last_received`, `age_hours`,
`expected_interval_hours`).

An earlier design sketch proposed adding `DATA_UNAVAILABLE` as a fourth
`AlertStatus` on flood alerts (a "suspension" state). This was rejected during
review for the following reasons:

1. **Violates the architectural separation**: The architecture routes pipeline
   concerns to the ops team via `AlertSource.PIPELINE` and flood alerts to
   forecasters — different recipients, different channels, different urgency
   models. Embedding pipeline-health semantics into the flood alert state machine
   conflates these concerns.

2. **No architectural precedent for suspension**: The alert state machine is
   linear (`RAISED → ACKNOWLEDGED → RESOLVED`). A non-terminal, non-resolved
   limbo state raises unanswered questions: Can it be acknowledged? Resolved
   manually? What happens to deduplication when fresh data arrives?

3. **Breaks deduplication indexes**: The partial unique indexes
   (`ix_alerts_station_level_source_active`,
   `ix_alerts_level_source_system_active`) filter on
   `status IN ('raised', 'acknowledged')`. A fourth active status would require
   expanding these indexes, breaking the assumption that exactly two states are
   "active."

4. **Routes an ops concern to the wrong audience**: Forecasters receiving a
   `DATA_UNAVAILABLE` flood alert cannot act on it — sensor recovery is an
   infrastructure concern for the ops team.

The correct approach is: **Flow 4 raises `AlertSource.PIPELINE` alerts for sensor
outages; flood alerts retain their last-known state unchanged.** The
deferred-resolution behavior is not a bug — it is the correct safety measure. The
flood alert state machine remains three states.

## Design sketch (for Flow 4 implementation)

When this plan is revisited, the following items must be addressed:

### Approach

Flow 4 step 4.2 detects per-station observation staleness. When a station's
observations are stale beyond a configurable threshold, Flow 4 step 4.6 raises
an `AlertSource.PIPELINE` alert with check type `observation_freshness`, targeted
at the ops team via the pipeline notification channel. The existing flood alert
(RAISED or ACKNOWLEDGED) remains unchanged.

Operators learn about the offline station via the pipeline alert channel, not by
inspecting flood alert statuses. The dashboard surfaces pipeline alerts separately
from flood alerts.

### Implementation requirements

1. **Flow 4 steps 4.2 + 4.6**: Implement per-station observation freshness
   checking and pipeline alert raising. The `pipeline_health` table and
   `PipelineCheckType.OBSERVATION_FRESHNESS` already exist in the schema.

2. **Pipeline alert upsert**: Use the existing `AlertStore.upsert_alert` with
   `source=AlertSource.PIPELINE` and `alert_level="observation_freshness"`.
   No new Protocol methods needed — pipeline alerts use the same store interface.

3. **Dashboard**: `dashboard.py:190,195` hardcodes `status == "raised"` for flood
   alert counts. Add a separate section for pipeline alerts
   (`source = 'pipeline'`), with distinct visual treatment. Do not mix pipeline
   alerts into flood alert counts.

4. **Logging**: Event names must follow `{entity}.{action}` past-tense convention
   per `docs/standards/logging.md`. Use `pipeline_alert.raised` (not
   `alert.data_unavailable_transitioned`). Log level: `WARNING` for a
   degraded-state detection. The codebase uses `alert.*` for forecast-based and
   `observation_alert.*` for observation-based events — pipeline alerts should use
   `pipeline_alert.*` to maintain the entity namespace separation.

5. **Orchestration**: Per `docs/standards/orchestration.md`:
   - The staleness check and alert raise must be wrapped in `@task` (DB writes
     require task boundaries)
   - Use `task.map()` for per-station fan-out
   - Address write-concurrency with Flow 1 and Flow 2: if Flow 4 fires while
     Flow 1/2 is mid-execution for the same station, a Prefect named-concurrency
     slot or transaction isolation is needed to prevent interleaving

6. **DB user boundary**: `transition_to_data_unavailable` (if any store method
   is added) must be called exclusively by `sapphire_worker` (Flow 4), not
   `sapphire_api`. Enforce via the existing DB user privilege separation per
   `docs/standards/security.md`.

7. **CI/CD migration**: If any schema changes are needed (e.g. adding
   `data_unavailable_since` to the alerts table as metadata), the migration must
   be **additive-only** per `docs/standards/cicd.md` — the previous image tag
   must be able to run against the new schema during the migration window. New
   nullable columns are safe; enum expansion requires a two-phase deployment
   (Phase 1: expand constraint, Phase 2: deploy code that writes new values).

8. **WMO/CAP note**: When WMO-1109 CAP integration is implemented (v1/Nepal),
   pipeline alerts must not be emitted as CAP alert messages. They are
   operational, not public-facing warnings.

9. **Resolution of pipeline alerts**: When observation freshness recovers
   (station comes back online), the pipeline alert should be auto-resolved.
   Unlike flood alerts, auto-resolve is safe for pipeline alerts — the condition
   being monitored (data availability) is directly observable.

10. **Tests**: Pipeline alert lifecycle (raise on staleness, resolve on recovery),
    dashboard rendering of pipeline alerts separately from flood alerts,
    concurrency with Flow 1/2 alert writes.

### Optional enhancement: staleness metadata on flood alerts

As a lower-priority addition, consider adding `last_evaluated_at: UtcDatetime |
None` to the `Alert` dataclass and table. This gives the dashboard a way to show
"last checked 3 days ago" on a stale flood alert without changing the alert's
status. This is a nullable column addition (additive-only migration, no
backwards-compatibility concern) and is independent of the pipeline alert work.

### Open question (to resolve during Flow 4 design)

1. Should pipeline alerts for observation staleness have a configurable
   auto-escalation if the station remains offline beyond a second, longer
   threshold? (e.g. WARNING after 6h, CRITICAL after 24h)
