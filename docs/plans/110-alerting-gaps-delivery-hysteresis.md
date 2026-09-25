---
status: DRAFT
depends_on: [341, 342]
---

# Plan 110 — Alerting gaps: delivery, hysteresis, first_detected_at

**Status:** DRAFT
**Type:** Code (alerting) — hold-at-PR; some items may resolve to explicit v1 deferral
**Owner:** Bea (marti@hydrosolutions.ch)
**Created:** 2026-07-08
**Surfaced by:** the Alerting / alert-state touchpoint map (`docs/touchpoint-maps.md`),
codex-confirmed 2026-07-08. Next-session plan — do not start until reviewed.
**Related:** [[feedback_alert_delivery_webhook_only]]; Plan 039 (DATA_UNAVAILABLE, deferred — out of scope here).

**CHWRR publication dependency (Plans 341 and 342, DRAFT):** Hydrological webhook delivery must read
only a human-PUBLISHED warning decision and its audited replacement/withdrawal history. Neither
an active `alerts` row nor an un-notified threshold candidate authorizes external delivery, whether
the source is a forecast or an observation. Keep CHWRR hydrological delivery disabled until this
gate is implemented; pipeline-health Slack remains a separate operations path. This changes the
delivery option below, not the hysteresis/`first_detected_at` investigation.

**MVP decision (owner, 2026-09-24):** CHWRR hydrological warnings are available through the
authenticated, human-publication-gated API only. Webhook delivery is a later option and remains
disabled for the MVP; email/Slack distribution is not an MVP backend promise. This DRAFT records
delivery and hysteresis gaps but is not an implementation-ready phase plan. Before later delivery
work reaches READY, decide recipients/channel behavior and rewrite it with bounded tasks,
verification and a dependency graph under `docs/workflow.md`.

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

**Later decision needed.** Webhook delivery is out of the CHWRR MVP. If a later release includes it:
- Implement a **webhook** `NotificationAdapter` (webhook-only per project decision — no
  email/SMS through v1).
- Wire it into the Plan 342 warning-publication lifecycle: store delivery outcome against the
  immutable publication ID, not `Alert.notified_at` on the mutable active row; define retry,
  failure and idempotency behavior for publication, replacement and cancellation.
- Trigger delivery from the human publication/replacement/withdrawal ledger in Plan 342, with
  idempotency keyed to the publication decision. Define update and cancellation delivery and
  expiry reconciliation; do not send from forecast-cycle Phase C, observation ingest, or
  un-notified active `alerts` rows.
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
false affordance. If (a), persist a per-candidate pending-onset time before an active
alert exists; reset it when the condition clears before the duration elapses. An
already-raised `Alert.first_detected_at` cannot by itself time a pre-raise duration.
Recommended: decide (a) vs (b) deliberately; validated-but-unused config is a trap.

## Gap 3 — `first_detected_at` is not stable across re-raises

**Finding.** `upsert_alert`'s `ON CONFLICT` resets `first_detected_at` to the new trigger
time every cycle (`alert_store.py:194`), so it means "last raised at," not "first
detected." Any duration-based logic (e.g. "alert active ≥ N hours") built on it would be
wrong.

**Decision needed.** If duration semantics are wanted, set `first_detected_at` from
the persisted pending-onset when an alert is raised, preserve it across upserts only
within the same episode, and reset it after resolution before a later re-raise.
Otherwise rename/document the field as last-raised-at. Test threshold flapping,
clear-before-trigger and resolved-then-raised episodes separately.

## Scope / non-goals

- Code changes → branch + PR + review + human merge (hold-at-PR). High-value but
  behaviour-changing; the delivery adapter is the largest piece.
- Non-goals: pipeline / `DATA_UNAVAILABLE` alerting (Plan 039); non-webhook channels;
  the acknowledge-route TOCTOU race (tracked in the Persistence map / its own follow-up).

## Acceptance criteria

1. Delivery: a webhook `NotificationAdapter` sends only human-published hydrological warning
   decisions, records successful delivery against the publication ID with defined
   retry/idempotency and update/cancellation behavior — OR delivery is explicitly deferred with a
   recorded rationale and scope note. Observation-sourced warnings use the same review gate.
2. Hysteresis: the unused threshold/duration fields are either honoured or removed.
3. `first_detected_at`: tied to the raised episode's pending onset and reset on a new
   episode (if duration logic is wanted), or documented as last-raised-at.
4. Tests cover whichever behaviour is chosen for each gap.
