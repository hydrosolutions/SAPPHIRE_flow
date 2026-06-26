---
status: DRAFT
created: 2026-06-25
plan: 083
title: Human-readable station code in structured logs
scope: Observability — log context fields
---

# Plan 083 - Human-readable station code in structured logs

## Status

**DRAFT** — do not implement until promoted to READY. Captured 2026-06-25 from an
operator observation during Mac-mini onboarding: per-station log events carry the
opaque UUID `station_id` (e.g. `hindcast.skip.no_observations station_id=3f9c…`),
which is not human-readable when triaging a run.

## Objective

Make per-station structured log events identify the station by its **human-readable
code** (e.g. BAFU `2009`) in addition to the canonical UUID, so operators reading
`logs -f prefect-worker` (and the watchdog/log files) can tell which station an event
is about without a UUID→code lookup.

## Key decision (confirm at promotion)

**Add `station_code` alongside `station_id`; do NOT drop the UUID.** The UUID is the
canonical correlation/join key and is deployment-stable; station codes can collide
across networks/deployments (Swiss BAFU vs Nepal DHM) and are not guaranteed unique
on their own. Recommendation: bind **both** — `station_id` for machine correlation,
`station_code` for humans. (The operator's framing was "code instead of id"; flag this
trade-off for the user to confirm — replace vs augment.)

## Mechanism

The infrastructure already exists — `docs/standards/logging.md` mandates `station_id`
and uses `structlog.contextvars.bound_contextvars(station_id=...)` at per-station
fan-out boundaries. This plan binds `station_code` at the **same boundaries**, so all
nested events inherit it without threading arguments:

```python
with structlog.contextvars.bound_contextvars(
    station_id=str(station.id), station_code=station.code
):
    process_station(station)
```

- Resolve the code **once** at the fan-out boundary (the flow/service usually has the
  `StationConfig`/`Station` in scope there). **Do not** add per-call DB lookups in hot
  loops just to fetch a code.
- Once bound on the contextvar, the ~63 explicit `station_id=str(station_id)` kwargs
  across ~23 files become redundant; dropping them is an optional cleanup (see scope).

## Scope

**In scope:**
- Bind `station_code` at every per-station fan-out boundary that currently binds
  `station_id` (flows: `run_forecast_cycle`, `onboard`, `ingest_observations`,
  hindcast/skill services, etc.).
- Update `docs/standards/logging.md`: add `station_code` to the mandatory/recommended
  context-field table and update the per-station fan-out example to bind both.

**Optional / second pass (decide at promotion):**
- Remove the now-redundant explicit `station_id=str(station_id)` kwargs where the
  contextvar covers them (~63 occurrences) to cut noise. Low value, broad churn — may
  be deferred.

**Out of scope:**
- No change to log levels, event names, or non-station context fields.
- No new runtime behavior — logging-only.
- Group-scoped events (`group_id`) — same idea could apply but is a separate follow-up.

## Phases (TODO — planner expands into tasks + exit gates + dependency graph)

1. **Standard update** — amend `logging.md` (mandatory fields + fan-out example).
2. **Bind at fan-out boundaries** — add `station_code` to each `bound_contextvars`/
   `bind_contextvars` site; add tests asserting emitted events carry `station_code`.
3. **(Optional) redundant-kwarg cleanup** — drop explicit per-call `station_id` kwargs.

## Risks / notes

- Low runtime risk (logging only), but touches many call sites — keep the core change
  (binding at boundaries) separate from the optional kwarg-cleanup sweep so review stays
  focused.
- Independent of Plan 068 (`onboard-stations` parallelization); no ordering dependency.
- Tests should assert on emitted log context (capture structlog events), not on string
  formatting, per the project testing conventions.

## References

- `docs/standards/logging.md` — context-field standard this plan amends.
- `src/sapphire_flow/services/hindcast.py:170,398` — example sites logging UUID `station_id`.
