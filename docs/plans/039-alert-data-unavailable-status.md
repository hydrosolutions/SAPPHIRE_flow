# Plan 039 — Alert DATA_UNAVAILABLE Status for Sensor/Model Failure

**Status**: DRAFT
**Phase**: Cross-cutting (alert lifecycle)
**Depends on**: Plan 037 (security audit finding H-23c)

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

This is a genuine safety hazard for an operational flood warning system.

### Current alert lifecycle

```
       upsert_alert(RAISED)
            │
            v
┌──────────────────────┐
│       RAISED         │──────────► resolve_alert() ──► RESOLVED
│                      │                                    │
│  trigger_value > 0   │                                    │
└──────────────────────┘                                    │
            │                                               │
     acknowledge_alert()                                    │
            │                                               │
            v                                               │
┌──────────────────────┐                                    │
│    ACKNOWLEDGED      │──────────► resolve_alert() ────────┘
│                      │
│  operator confirmed  │
└──────────────────────┘
```

Status enum (`types/enums.py:29`):
- `RAISED` — threshold exceeded, alert active
- `ACKNOWLEDGED` — operator has seen the alert
- `RESOLVED` — conditions returned below threshold

Missing: any representation of "we cannot evaluate this alert because data is
unavailable."

### Affected code paths

**Observation alerts** (`services/observation_alert_checker.py:96-107`):
When `evaluated_parameters` is empty (all sensors offline), the deferred-resolution
guard on line 100 (`configured.issubset(evaluated_parameters)` is `False`)
prevents any resolution. Active alerts persist indefinitely.

**Forecast alerts** (`services/alert_checker.py:136-139,239-244`):
When a station has no ensembles (model failed), `_check_station` exits before
calling `_process_results`. Active forecast alerts persist indefinitely.

### Design

**New status**: `DATA_UNAVAILABLE = "data_unavailable"`

This is NOT a resolution — it is a **suspension**. The alert remains active in
a degraded state. The semantic is: "this alert was RAISED (or ACKNOWLEDGED) but
we can no longer evaluate it because data is missing."

```
       upsert_alert(RAISED)
            │
            v
┌──────────────────────┐
│       RAISED         │──────► resolve_alert() ──► RESOLVED
│                      │
└──────┬───────────────┘
       │           │
       │    transition to
       │    DATA_UNAVAILABLE
       │           │
       │           v
       │   ┌──────────────────────┐
       │   │  DATA_UNAVAILABLE    │──► fresh data arrives ──► re-evaluate
       │   │                      │        │
       │   │  sensor/model offline│        ├──► still exceeds → RAISED
       │   └──────────────────────┘        └──► below threshold → RESOLVED
       │
     acknowledge_alert()
       │
       v
┌──────────────────────┐
│    ACKNOWLEDGED      │──────► same transitions as RAISED
└──────────────────────┘
```

When fresh data arrives, the alert checker evaluates normally:
- If the threshold is still exceeded → `upsert_alert(RAISED)` (re-raises)
- If below threshold → `resolve_alert()` (resolves)
- If still no data → stays `DATA_UNAVAILABLE`

### Key design decisions

1. `DATA_UNAVAILABLE` is a **non-terminal state** — it can transition back to
   `RAISED` or forward to `RESOLVED`. It is not a resolution.

2. The `fetch_active_alerts` query currently filters `status != 'resolved'`.
   `DATA_UNAVAILABLE` alerts ARE still "active" (they should appear on the
   dashboard with a distinct visual treatment — e.g., greyed out with a
   "data unavailable" badge).

3. The dashboard must distinguish `DATA_UNAVAILABLE` from `RAISED`. A greyed-out
   or hatched alert is safer than a bright red one — operators should see "we
   don't know" rather than "flood in progress."

4. A `DATA_UNAVAILABLE` alert should carry metadata: `data_unavailable_since`
   timestamp (when the transition happened) and optionally `missing_parameters`
   (which sensors are offline).

## Tasks

### Task 1: Add `DATA_UNAVAILABLE` to `AlertStatus` enum

**File**: `src/sapphire_flow/types/enums.py`
Add `DATA_UNAVAILABLE = "data_unavailable"` to the `AlertStatus` enum.

### Task 2: Add `data_unavailable_since` column to `alerts` table

**Migration**: New Alembic migration `0025_add_data_unavailable_status.py`.
- Add column `data_unavailable_since TIMESTAMPTZ NULL` to `alerts`
- Add `'data_unavailable'` to the CHECK constraint on `alerts.status` (if one
  exists; currently status is a plain Text column — verify)

### Task 3: Add `transition_to_data_unavailable` method to `PgAlertStore`

**File**: `src/sapphire_flow/store/alert_store.py`
New method that sets `status = 'data_unavailable'` and
`data_unavailable_since = now()` on a given alert ID. Only valid for alerts
currently in `RAISED` or `ACKNOWLEDGED` status.

### Task 4: Update `observation_alert_checker.py`

**File**: `src/sapphire_flow/services/observation_alert_checker.py`
When `evaluated_parameters` is empty for a station and there are active alerts,
transition those alerts to `DATA_UNAVAILABLE` instead of deferring resolution.
Log at WARNING level: `observation_alert.data_unavailable`.

### Task 5: Update `alert_checker.py` (forecast alerts)

**File**: `src/sapphire_flow/services/alert_checker.py`
When a station is absent from `all_ensembles` (no model output), check for active
forecast alerts for that station and transition them to `DATA_UNAVAILABLE`.

### Task 6: Handle re-evaluation on data return

When fresh data arrives and `evaluated_parameters` is non-empty, the existing
`upsert_alert(RAISED)` or `resolve_alert()` paths should handle re-evaluation
naturally — `DATA_UNAVAILABLE` alerts are still "active" (not resolved), so the
existing logic of checking active alerts and resolving/updating them applies
without changes.

Verify: does `upsert_alert` with `on_conflict_do_update` correctly update a
`DATA_UNAVAILABLE` alert back to `RAISED`? The conflict index is on
`(station_id, alert_level, source)` where `status IN ('raised', 'acknowledged')`.
A `DATA_UNAVAILABLE` alert would NOT match this conflict condition, causing a
new row to be inserted. This must be fixed — either widen the conflict condition
to include `'data_unavailable'`, or use a separate update path.

### Task 7: Tests

- Unit test: observation checker transitions to DATA_UNAVAILABLE when all sensors
  offline
- Unit test: observation checker re-raises when data returns after
  DATA_UNAVAILABLE
- Unit test: forecast checker transitions when model fails
- Integration test: full lifecycle RAISED → DATA_UNAVAILABLE → RESOLVED

## Open questions

1. Should `DATA_UNAVAILABLE` have a maximum duration? E.g., after 7 days of no
   data, auto-resolve to prevent indefinite accumulation? (This should be
   configurable via `DeploymentConfig`.)

2. Should `DATA_UNAVAILABLE` alerts trigger a separate notification to operators
   (pipeline health alert vs. flood alert)?

3. The `upsert_alert` conflict index needs careful review (Task 6) — this is the
   highest-risk part of the implementation.
