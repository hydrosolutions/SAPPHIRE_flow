# Plan 089 — model-assignment priority hierarchy (skill models over fallbacks)

**Status**: DONE (2026-07-02) — config-driven `[model_priorities]` map added to
`DeploymentConfig` (+`DEFAULT_PRIORITY=50`, `priority_for_model()`); onboarding
Step 6 resolves per-model priority from config (falls back to `DEFAULT_PRIORITY`
when config is None); `create_station_assignment` already upserted priority (Pg
`on_conflict_do_update` + fake store) so re-onboard is idempotent unchanged.
Tests: RED-confirmed skill-over-fallback regression + config/onboarding units.
Docs: `config.toml`, `config-reference.toml`, `conventions.md`.
**Phase**: v0b operational hardening (forecast-cycle model selection)
**Parent**: epic 088 (NWP-on forecasting) — surfaced during the 2026-07-02 live
onboarding of stations 2009/2091
**Related**: `services/onboarding.py` Step 6 (assignment), `run_station_forecast`
(PRIMARY fallback chain), `ModelAssignment.priority`, forecast combination
strategy
**Created**: 2026-07-02

---

## Problem (verified live, 2026-07-02)

Onboarding Step 6 assigns **every** model to a station with a hardcoded
`priority=0` (`services/onboarding.py:484`). The forecast cycle's PRIMARY
combination strategy tries a station's models in ascending `priority` order and
**stops at the first success** (`run_forecast_cycle.py:912`,
`run_station_forecast.py:271`). With all priorities equal, the tie-break is the
arbitrary store fetch order.

In the live NWP-on run this meant `climatology_fallback` (a last-resort model)
was reached and succeeded **before** `nwp_rainfall_runoff` (the weather-driven
skill model) was ever tried — so the operational forecast came from climatology,
not the NWP model. The NWP model only produced its 21-member forecast after a
manual `UPDATE model_assignments SET priority = -10 WHERE
model_id='nwp_rainfall_runoff'` (dev DB, not committed).

The intended hierarchy is **skill models > fallbacks** (memory:
"linear regression > ML > conceptual"). Nothing in onboarding encodes it today.

## Goal

Onboarding assigns priorities that make the PRIMARY fallback chain prefer
higher-skill models and only fall through to fallbacks when the skill models
fail (e.g. insufficient input data). Idempotent, deterministic, and controllable.

## Design decision (RESOLVED 2026-07-02): config-driven priority map

Priority is a **config-driven map**, keyed by `model_id`, **lower = preferred**
(tried first in the PRIMARY chain). No model-API change; operator-tunable; works
for externally-authored FI models. (Rejected: model-declared attribute — touches
every model + the FI adapter and blocks operator retuning; hybrid — more work
than v0 warrants. Revisit if model tiers proliferate.)

**Config schema** — new `config.toml` section, parsed at the boundary
(`DeploymentConfig`, Pydantic) into `model_priorities: dict[str, int]`:

```toml
[model_priorities]
nwp_regression         = 10   # skill: weather + lags
nwp_rainfall_runoff    = 20   # skill: weather-only
linear_regression_daily = 30  # conceptual/regression
persistence_fallback   = 90   # fallback
climatology_fallback   = 100  # last-resort fallback
# any model not listed -> DEFAULT_PRIORITY (50), between skill and fallback
```

`DEFAULT_PRIORITY = 50` for unlisted models — a documented neutral tier that
sits between skill models and fallbacks (so an unlisted new model outranks the
fallbacks by default but not the tuned skill models). Onboarding resolves each
model's priority as `model_priorities.get(model_id, DEFAULT_PRIORITY)`.

Secondary decision (confirmed): PRIMARY stays the default combination strategy;
priority only affects the first-success fallback chain. POOLED is out of scope.

## Phases

- **P1 — config + boundary**: add `[model_priorities]` to `config.toml`; add
  `model_priorities: dict[str, int]` + `DEFAULT_PRIORITY` to `DeploymentConfig`
  (Pydantic boundary → typed). Seed the map with the values above.
- **P2 — onboarding wiring**: Step 6 (`services/onboarding.py:~478-486`) resolves
  `priority = deployment_config.model_priorities.get(str(model_id),
  DEFAULT_PRIORITY)` instead of the hardcoded `0`, passed to
  `create_station_assignment`. Idempotent re-onboard updates existing
  assignments' priority (confirm `create_station_assignment` upserts priority; if
  not, add it).
- **P3 — tests**: RED-confirmed — (a) a station with a skill model + fallbacks
  yields the SKILL model's forecast under PRIMARY (pre-fix a fallback can win by
  arbitrary tie-break); (b) unlisted model → DEFAULT_PRIORITY; (c) idempotent
  re-onboard updates priority.
- **P4 — docs**: document `[model_priorities]` (config docs/conventions) + the
  skill>fallback hierarchy; update the onboarding doc if it states priority=0.

## Non-goals

- Changing the PRIMARY→first-success semantics or adding POOLED here.
- The NWP-adapter incomplete-cycle issue (see below) — separate plan.

## Related follow-up (NOT this plan) — incomplete NWP cycle selection

The same live run surfaced a distinct NWP-adapter robustness gap: the cycle
resolver selected the freshly-published **06Z** ICON cycle (only ~30 of 120
hourly lead-times uploaded to MeteoSwiss OGD at fetch time) as `primary` over the
**complete 00Z** cycle, truncating the daily forecast horizon to 1 step. The
daily aggregation/filter are correct; the selection prefers a newer-but-
incomplete cycle. Candidate fix: treat a cycle with insufficient lead-time
coverage as not-yet-available and fall back to the last complete cycle. Should be
its own plan (090) — recorded here so it is not lost.

## Process

DRAFT until: (1) the priority-source mechanism is chosen, (2) user confirms →
flip to READY per `docs/workflow.md`. No implementation from DRAFT.
