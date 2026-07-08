# Plan 110 — Alerting gaps: delivery, hysteresis, first_detected_at

**Status:** DRAFT
**Type:** Code (alerting) — hold-at-PR; some items may resolve to explicit v1 deferral
**Owner:** Bea (marti@hydrosolutions.ch)
**Created:** 2026-07-08
**Surfaced by:** the Alerting / alert-state touchpoint map (`docs/touchpoint-maps.md`),
codex-confirmed 2026-07-08. Next-session plan — do not start until reviewed.
**Related:** [[feedback_alert_delivery_webhook_only]]; Plan 039 (DATA_UNAVAILABLE, deferred — out of scope here).

> Three gaps the Alerting map documents as current behaviour. Each needs an owner
> decision: implement, or deliberately defer + record it. Grouped because they are the
> same subsystem (`alert_checker` / `observation_alert_checker` / `alert_store`).

## Gap 1 (primary) — alert delivery is not implemented

**Finding.** The system **computes and persists** flood alerts but **nothing delivers
them**. `NotificationAdapter` (`src/sapphire_flow/protocols/notification.py`) is a
Protocol with **no concrete production implementation** (only a test fake at
`tests/fakes/fake_adapters.py`). Both alert producers hard-code `notified_at=None`
(`alert_checker.py:329`, `observation_alert_checker.py:94`). Nothing sends, retries, or
enforces webhook-exclusivity. ("Webhook-only" is a documented *convention*, not code —
[[feedback_alert_delivery_webhook_only]].) The `ops.watchdog` Slack poster is
pipeline-health, not flood-alert delivery.

**Decision needed.** Is alert delivery in scope for v0 / v1? If **yes**:
- Implement a **webhook** `NotificationAdapter` (webhook-only per project decision — no
  email/SMS through v1).
- Wire it into the alert lifecycle: on successful delivery set `Alert.notified_at`;
  define retry / failure handling (does a delivery failure degrade like a store failure,
  or raise?); decide idempotency (don't re-notify an already-notified active row).
- Decide *what* triggers delivery — inline in the forecast-cycle Phase C / observation
  ingest, or a separate delivery step reading un-notified active alerts.
If **no** (deferred): record the deferral explicitly so "nothing sends alerts" is a
known, owned state rather than a silent gap.

## Gap 2 — no hysteresis (alert flapping)

**Finding.** `DangerLevelDefinition` carries `resolve_probability`,
`min_trigger_duration`, `min_resolve_duration` (and the deployment-config `*_hours`
equivalents); they are **validated but never read** by either checker. So an alert
raises/resolves on the same `trigger_probability` boundary every cycle — it can **flap**
around the threshold, and there is no minimum active/clear duration.

**Decision needed.** Either (a) implement hysteresis — separate `resolve_probability`
below `trigger_probability`, and honour `min_trigger_duration` / `min_resolve_duration`
before raising/clearing — or (b) **remove the dead fields** so they don't present a
false affordance. Recommended: decide (a) vs (b) deliberately; validated-but-unused
config is a trap.

## Gap 3 — `first_detected_at` is not stable across re-raises

**Finding.** `upsert_alert`'s `ON CONFLICT` resets `first_detected_at` to the new trigger
time every cycle (`alert_store.py:194`), so it means "last raised at," not "first
detected." Any duration-based logic (e.g. "alert active ≥ N hours") built on it would be
wrong.

**Decision needed.** If duration semantics are wanted, `COALESCE` to the existing value
on conflict (preserve the original). Otherwise rename / document the field as
last-raised-at. (Note the interaction with Gap 2: `min_trigger_duration` hysteresis needs
a *stable* first-detected timestamp to measure against — Gaps 2 and 3 are coupled.)

## Scope / non-goals

- Code changes → branch + PR + review + human merge (hold-at-PR). High-value but
  behaviour-changing; the delivery adapter is the largest piece.
- Non-goals: pipeline / `DATA_UNAVAILABLE` alerting (Plan 039); non-webhook channels;
  the acknowledge-route TOCTOU race (tracked in the Persistence map / its own follow-up).

## Acceptance criteria

1. Delivery: a webhook `NotificationAdapter` exists and sets `notified_at` on success
   with defined retry/idempotency — OR delivery is explicitly deferred with a recorded
   rationale and scope note.
2. Hysteresis: the unused threshold/duration fields are either honoured or removed.
3. `first_detected_at`: preserved-on-conflict (if duration logic is wanted) or documented
   as last-raised-at.
4. Tests cover whichever behaviour is chosen for each gap.
