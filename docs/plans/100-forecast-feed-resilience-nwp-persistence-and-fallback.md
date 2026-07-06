# Plan 100 — forecast-feed resilience: persist NWP-on across restarts + always-on fallback (no silent blackout)

**Status**: DRAFT — grill-me COMPLETE (2026-07-06) + **one `plan-review` round
folded (2026-07-06)**: A3 redesigned to post-hoc detection, alert path flipped
from label→**suppress**, a new **M0 priority-reconciliation** prerequisite added
after a live DB check, root-cause narrative corrected to "unconfirmed +
defense-in-depth". Still DRAFT — re-run `plan-review` to confirm the folded
decisions converge, then phases → READY, then `vision-build` (WF2).
Implementation is a **code change** → **hold-at-PR** with a version bump.
**Priority**: high — the operational forecast feed went **fully dark for ~3 days
(2026-07-03 → 07-06)** with every flow reporting green.
**Phase**: v0b — operational reliability
**Parent**: Plan 091 (mac-mini NWP-on data collection); the `nwp_regression`
skill-comparison track
**Related**:
- `scripts/launchd/start-sapphire.sh:24-27`, `scripts/bootstrap-mac-mini.sh:110-117` (startup paths — omit the `-nwp` overlay)
- `config/overlays/mac-mini.toml:1-2` (`enabled = false`), `config/overlays/mac-mini-nwp.toml:7-8` (`enabled = true`)
- `docker-compose.macmini.yml:24,32` / `docker-compose.macmini-nwp.yml:11,17` (`SAPPHIRE_CONFIG_OVERLAY`)
- `docs/deployment/mac-mini-staging.md:304-336` (operator doc — documents the two-file NWP toggle A2 deletes)
- `src/sapphire_flow/flows/run_forecast_cycle.py:100-106` (`_load_weather_forecast_adapter_config` — NWP off when `SAPPHIRE_CONFIG` unset **or** `enabled` absent), `:958` (per-station loop), `:757-760` (batch-fetched `model_assignments[sid]`), `:1082-1086` (PRIMARY `fc_result is None` → `log.warning("forecast_cycle.all_models_failed")`), `:1137-1141` (combination `primary_model_id is None`), `:973-984` (`station_skipped_model_not_loaded` convention), `:1439-1460` (Phase C passes `all_ensembles` into `check_station_alerts`), `:89-97` (`ForecastCycleResult.stations_failed`/`errors`), `:549` (`run_forecast_cycle_flow` entry)
- `src/sapphire_flow/services/run_station_forecast.py:301-341` (`primary_model_id=None` iff every assigned model failed → no row), `_run_single_model:148` (`fetch_active_artifact_for_station`), `:174-208` (predict try/except backstop), `MultiModelForecastResult.combinable_results` (`priority < FALLBACK_PRIORITY_THRESHOLD`)
- `src/sapphire_flow/services/onboarding.py:472-505` (Step 6 assigns **every** discovered STATION model unconditionally), `:637-643` (Step 7 swallows per-model training failure), `:662-673` (Step 8 marks OPERATIONAL on **any** active artifact), `flows/onboard.py:208-211` (never fails on non-empty `errors`)
- `src/sapphire_flow/services/model_onboarding.py:838-895` (`create_station_assignment`), `:994` (`assignment_priority: int = 0` default — the drift source)
- `src/sapphire_flow/config/deployment.py:250-256` (`priority_for_model` → config value or `DEFAULT_PRIORITY=50`), `:341-346` (`load_config` pops `adapters`/`monitoring`)
- `config.toml:59-64` (`[model_priorities]`: `nwp_regression=10`, `nwp_rainfall_runoff=20`, `linear_regression_daily=30`, `persistence_fallback=90`, `climatology_fallback=100`); `config.toml:397` (`monitoring.expected_delivery_offset_hours` — dead TOML); `FALLBACK_PRIORITY_THRESHOLD=90` (`types/ids.py:20`)
- `src/sapphire_flow/models/climatology_fallback.py` (needs only its trained artifact — zero runtime obs → the only guaranteed floor), `models/persistence_fallback.py` (`past_targets[param][-1]` → `IndexError` on empty)
- `src/sapphire_flow/services/alert_strategy.py:146-194` (`PrimaryModelStrategy.evaluate` — fallback-blind), `alert_checker.py:269-330` (`_process_results` discards `ExceedanceResult`, builds persisted `Alert`), `types/alert.py:19-35` (`Alert`, no fallback field); **v0 has NO alert delivery** — `docs/v0-scope.md` §A8: "Alerts logged to alerts table. Visible via API. No notification dispatch."
- `src/sapphire_flow/api/routes/forecasts.py` + `api/templates/forecasts/{list,detail}.html` + `api/routes/api_forecasts.py` (dashboard/JSON render `model_id`; no fallback badge)
**Created**: 2026-07-06

---

## IMPLEMENTATION VISION (decided spec — feeds re-`plan-review` + WF2 vision-build)

Five milestones. **M0 is a prerequisite** the live DB check forced: the
fallback-tier machinery every later milestone relies on is keyed on
`priority ≥ 90`, but real assignment rows carry drifted priorities (`0`/`-10`),
so the floor is not actually in the fallback tier until M0 repairs it. M1+M2 are
the incident fix; M3 stops operators (and the alert path) trusting a fallback;
M4 is a minimal staleness tripwire.

### M0 — Reconcile assignment priorities to the config chain (PREREQUISITE)

Live-DB finding (2026-07-06, dev stack — the 2 skill-comparison stations 2009 &
2091): both have **all 5 models assigned + all 5 artifacts active** (incl. a live
`climatology_fallback` artifact), but `model_assignments.priority` is
`climatology_fallback=0`, `persistence_fallback=0`, `nwp_rainfall_runoff=-10`,
others `0` — **not** `config.toml`'s chain (100/90/…). Root cause of the drift:
`create_station_assignment` defaults `assignment_priority=0`
(`model_onboarding.py:994`) and these rows were created off the default/manual
path, not `priority_for_model`. Consequence: `combinable_results`
(`priority < 90`) treats **climatology as a combinable *skill* model**, and any
`priority ≥ 90` fallback-tier check (B3 label, M3 alert-suppression) misclassifies.

- **M0a — repair existing rows:** a named idempotent admin action rewrites
  `model_assignments.priority` to `priority_for_model(model_id)` for all existing
  stations (climatology→100, persistence→90, skill→10/20/30). *(The manual
  `nwp_rainfall_runoff=-10` skill-comparison override is a deliberate experiment
  knob — the repair must let an explicit skill override stand while still placing
  the fallbacks in the ≥90 tier; specify precedence, don't blindly clobber.)*
- **M0b — fix the creation paths:** every `create_station_assignment` call site
  (onboarding Step 6/promotion, `model_onboarding.py`) passes the config-driven
  `priority_for_model` value; the bare `assignment_priority=0` default is removed
  or made explicit so future onboarding cannot re-introduce the drift.
- **M0c — single source of truth for the tier:** fallback classification
  (`combinable_results`, B3 `is_fallback`, M3 alert-suppression) all derive the
  tier the **same** way. Decide one: either (i) trust the (now-repaired) DB
  `assignment.priority`, or (ii) always derive from config `priority_for_model`.
  Runtime primary-selection/combination already sorts by DB priority, so option
  (i) + M0a repair keeps a single consistent source; document the choice.

### M1 — Persist NWP-on deterministically (Part A)

- **A2 (fold):** `config/overlays/mac-mini.toml` sets
  `[adapters.weather_forecast] enabled = true` **explicitly**; **delete**
  `config/overlays/mac-mini-nwp.toml` and `docker-compose.macmini-nwp.yml`. One
  overlay, one mini compose file — "forget the -nwp file" becomes
  unrepresentable. A1's script edits are dropped as unnecessary. **Rewrite
  `docs/deployment/mac-mini-staging.md:304-336`** ("Forecast-cycle NWP modes") to
  state NWP-on is the sole permanent steady state with no supported toggle, and
  **remove the now-dangerous "change only the overlay gate to `enabled = true`"
  instruction** (stale doc could lead an operator to recreate the incident).
- **A3 (detect, don't preflight — redesigned after plan-review):** set
  `SAPPHIRE_REQUIRE_NWP=1` on the mini. Two mechanisms, no per-station artifact
  preflight:
  - **Global gate:** at `run_forecast_cycle_flow` entry, if
    `SAPPHIRE_REQUIRE_NWP=1` **and** the NWP adapter cannot be constructed at all
    (missing STAC config etc.) → raise `ConfigurationError` (the only hard-refuse
    path; nothing could forecast anyway).
  - **Post-hoc dark detection:** **promote the existing zero-forecast branches** —
    `fc_result is None` (`run_forecast_cycle.py:1082-1086`) and
    `primary_model_id is None` (`:1137-1141`) — from `log.warning` to **`log.error`
    + `errors.append(...)`**, mirroring the `station_skipped_model_not_loaded`
    convention (`:973-984`). This already fires exactly when a station produces
    **zero** forecasts this cycle, for **any** reason (NWP off + inert floor, empty
    obs, a model bug) — no climatology-specific preflight, no new artifact-store
    query, and it cannot over-skip a station a healthy `persistence_fallback`
    would have served.
  - **Why not the earlier preflight:** the whole-flow refuse (grill-me) would
    black out all ~1000 stations on one bad record; the per-station climatology
    preflight (round-1 revision) still (a) duplicated the `fetch_active_artifact`
    call `_run_single_model` makes moments later and (b) skipped the *entire*
    station when only climatology was inert, discarding a would-have-succeeded
    persistence result. Post-hoc detection is strictly simpler and more general.

### M2 — Always-on climatology floor (Part B)

- **B1a (both fallbacks; guarantee keyed on climatology):**
  `persistence_fallback(90)` then `climatology_fallback(100)`. Climatology is the
  only model that produces from its artifact alone (zero runtime obs) → the
  guaranteed floor. The guarantee keys on **`climatology_fallback`-with-active-
  artifact** (post-M0, at the correct priority 100).
- **B1b (floor-gate onboarding + un-swallow floor-training failure + backfill —
  STRICT):** Step 6 already assigns every discovered model
  (`onboarding.py:472-505`), so there is no narrowing to fix. The defect is a
  **failed floor artifact still yields OPERATIONAL**:
  - **Step 8 floor-gate:** require `climatology_fallback` (the floor) to have an
    active artifact — not "any discovered model" (`:662-673`) — before a
    non-weather station is marked OPERATIONAL. No floor artifact → station stays
    **NOT operational** + loud ERROR.
  - **Step 7 un-swallow:** a *floor*-model training failure must not silently
    `continue` (`:637-643`); it fails the run or leaves the station NOT
    operational, and `flows/onboard.py:208-211` surfaces it as a **non-green**
    outcome (skill-model failures may still be tolerated).
  - **Floor plugin-load failure is loud:** if `climatology_fallback` fails to load
    via entry points (`model_registry.py` silent `except`), log ERROR for that
    specific `model_id` rather than a silent capacity outage.
  - **Test-migration budget:** onboarding unit tests that mock `discover_models`
    with a non-floor model set and assert OPERATIONAL
    (`tests/unit/services/test_onboarding.py:510-578`) **will break** under the
    gate — update them to include a working climatology fake or assert the new
    NOT-operational outcome. This is scoped work, not free.
  - **Backfill 2009/2091** (named idempotent admin action): create assignments
    **and train + promote a `climatology_fallback` artifact** (an assignment
    without an active artifact is inert → still dark). Re-run = no-op (guard
    artifact promotion; `create_station_assignment` already upserts, Plan 089).
- **B2 (persistence empty-obs guard):** `climatology_fallback` produces from its
  artifact alone (floor holds). Add an explicit empty/short-obs guard to
  `persistence_fallback.predict` so an *anticipated* no-obs case degrades cleanly
  instead of leaning on the `try/except` backstop (CLAUDE.md: backstop is for
  *unanticipated* bugs). Native protocol is accepted for Plan 100; native→FI
  convergence is a separate owner-requested track (residual).

### M3 — Fallback handling in forecasts + alerts (Part B3 + alert safety)

- **B3 (label fallback forecasts):** compute `is_fallback` from the tier (M0c's
  single source) at render/serialize time; render a `FALLBACK` badge in
  `forecasts/list.html` + `detail.html` and expose the flag on the JSON forecast
  schema (`api_forecasts.py`). No DB migration.
- **M3-alert (SUPPRESS, redesigned — the webhook framing was fictional):** there
  is **no flood-alert webhook/dispatch in v0** (alerts are logged + shown via
  API), so there is no "webhook payload" to label. Instead: **a flood alert must
  not fire from a fallback-only cycle.** In `check_station_alerts` /
  `PrimaryModelStrategy.evaluate` (`alert_strategy.py:146-194`,
  `run_forecast_cycle.py:1439-1460`), when the primary/only ensemble is
  **fallback-tier** (post-M0: `priority ≥ 90`), **suppress alert evaluation** for
  that station (log a monitorable "alert suppressed: fallback-only" event).
  - **Rationale:** climatology is a day-of-year seasonal average — it carries zero
    event information and would trip the identical "alert" every year on that
    calendar day → pure false alarms. Persistence is obs-grounded but "current
    level is dangerous" is Flow 2's (observation→QC→alert) job, not a naive
    flat-line forecast alert. A flood alert must come from a **skill** forecast.
  - **Corollary:** with suppression, an alert that *does* fire is skill-sourced by
    definition, so no per-alert `is_fallback` label is needed. (A persisted
    label on the `Alert` record is a deferred v-next, not this plan.)

### M4 — Minimal runtime NWP-staleness tripwire (Part C1)

- **C1a (config plumbing — prerequisite):** `monitoring.expected_delivery_offset_hours`
  is dead TOML (`config.toml:397`; `load_config` pops `adapters`+`monitoring`,
  `deployment.py:341-346`; the adapter loader `run_forecast_cycle.py:100-106`
  never reads it). Add the loader + `_WeatherForecastAdapterConfig` field before
  the check can read it — scoped work, not a free lookup.
- **C1b (the check):** once C1a lands, if NWP is expected but no grid archived in
  > `expected_delivery_offset_hours × cadence` → emit a loud monitorable event on
  the **same channel A3's post-hoc detection uses**. Full watchdog deferred to the
  Flow-4 monitoring plan.

### Acceptance checks (what WF2 must make pass)

1. **Priorities reconciled (M0):** after M0a, `model_assignments.priority` for the
   fallbacks equals the config chain (climatology=100, persistence=90) for all
   stations; `combinable_results` **excludes** climatology; `is_fallback` on a
   climatology-sourced forecast is **True**. Re-running M0a is a no-op. A fresh
   onboarding writes the config priorities (M0b), not `0`.
2. **Restart persistence (A2):** cold-boot the mini via `start-sapphire.sh` → a
   `forecast-cycle` runs NWP-on (`nwp.*` logs, grids archived), no manual overlay,
   no `-nwp` file anywhere; `docs/deployment/mac-mini-staging.md` no longer
   documents a toggle.
3. **Floor writes + is labelled (B1a/B3):** station with an NWP model + the floor,
   stack NWP-off → a `climatology_fallback` row **IS** written and **badged
   FALLBACK**. Flip NWP-on → the skill model becomes primary, row **not** badged.
4. **A3 post-hoc detection (revised):** with `SAPPHIRE_REQUIRE_NWP=1` and NWP off,
   a station that produces zero forecasts is logged at **ERROR** and appears on
   `errors`/`stations_failed` (not a silent warning), while every other station
   still forecasts (flow does NOT abort). The flow-level `ConfigurationError`
   fires **only** when the NWP adapter cannot be constructed at all.
5. **Backfill effective + idempotent (B1b):** 2009/2091 have active climatology
   artifacts + assignments at the correct priority; a NWP-off `forecast-cycle`
   writes fallback rows for both. Re-run of the backfill = no-op.
6. **Onboarding floor-gate STRICT (B1b):** onboard a test station whose
   `climatology_fallback` training is forced to fail → station **NOT** OPERATIONAL
   and the onboarding run reports **non-green** (not swallowed). Floor training
   succeeding → OPERATIONAL. Existing onboarding tests updated.
7. **persistence empty-obs (B2):** `persistence_fallback.predict` on empty obs
   degrades gracefully (no uncaught `IndexError`); climatology still produces.
8. **Alert suppressed on fallback-only (M3-alert):** a fallback-only cycle that
   would trip a threshold **fires no `Alert`** and logs the "alert suppressed:
   fallback-only" event; a skill-sourced exceedance alerts normally.
9. **C1 staleness fires (M4):** after C1a plumbing, force a grid-archival gap past
   `expected_delivery_offset_hours × cadence` → the staleness event fires (proves
   it is not dead code).
10. **Mini-state diagnostic (root-cause closure):** capture the **mini's** actual
    `model_assignments` + artifact status + priorities for 2009/2091 and record
    which candidate gap (unassigned floor / inert artifact / priority drift) was
    live at incident time, confirming the shipped fix closes it.

---

## Problem (the 2026-07-03 → 07-06 blackout)

The forecast feed produced **zero** rows for ~3 days while `forecast-cycle`
completed green every 6 h. Two conditions had to coincide:

1. **NWP-on was not persisted across restarts.** NWP-on ran only when the stack
   was brought up with **both** `docker-compose.macmini.yml` **and**
   `docker-compose.macmini-nwp.yml`. The auto-start path (`start-sapphire.sh`,
   boot/launchd) brings up **only** `docker-compose.macmini.yml` → `mac-mini.toml`
   → `enabled=false`. On the 07-03 restart, NWP silently reverted to off:
   runoff-only, no `nwp.*` logs, no grids archived (last grid 07-03, then
   Plan-095 retention pruned), every NWP model `ModelFailure`.

2. **No graceful degradation — the feed had no effective floor.** When the NWP
   models failed and no fallback produced, the first-success loop
   (`run_station_forecast.py:301-341`) left `results` empty and wrote **nothing**.

**Root-cause status: UNCONFIRMED (corrected after a live DB check, 2026-07-06).**
Two competing mechanisms were proposed; the dev DB (which holds exactly the two
skill-comparison stations 2009/2091) **rules both out for its current state** and
surfaces a third:
- **NOT "only NWP models assigned":** both stations have all 5 models assigned
  (status active), incl. `climatology_fallback` + `persistence_fallback`.
- **NOT "floor artifact silently failed to train":** both have an **active**
  `climatology_fallback` artifact.
- **Third mechanism, empirically present — priority drift:** assignment priorities
  are `0`/`-10`, not the config chain, so `climatology` sits **below** the
  fallback threshold and is treated as a combinable skill model rather than a
  guaranteed floor. See M0.

The dev DB is a healthy, fully-onboarded state that **cannot reproduce the
blackout**, and it likely post-dates / differs from the mini's incident-time
state — so the precise incident mechanism is **not confirmed** and requires the
mini's actual DB (acceptance check 10). The onboarding gaps the plan-review
verified are real *latent* defects regardless (Step 7 swallows floor-training
failure `onboarding.py:637-643`; Step 8 marks OPERATIONAL on any active artifact
`:662-673`; `flows/onboard.py:208-211` never fails on `errors`), so the fix is
**defense-in-depth** across all candidate mechanisms rather than a bet on one.

MeteoSwiss was healthy throughout (STAC `updated` 07-06 08:44Z) — this was
entirely our deployment + resilience gap, not an external outage.

## Goal

1. **NWP-on survives every restart/reboot deterministically** — no manual step,
   no silent drop.
2. **The forecast feed can never go fully dark** — a fallback always writes
   *something*, clearly labelled, and the floor is actually in the fallback tier.
3. **A silent NWP-off / zero-forecast station is detectable** — surfaced loudly
   (post-hoc dark detection + grid-staleness), ties to Flow 4.
4. **Fallback forecasts never masquerade as skill** — labelled in the UI/API and
   **never used as the basis of a flood alert**.

## Design decisions (grill-me 2026-07-06 + plan-review round folded)

### Part A — persist NWP-on

- **A1 — SUPERSEDED by A2.** With the fold there is no `-nwp` overlay to add to
  the startup scripts.
- **A2 — DECIDED: FOLD.** `mac-mini.toml` → `enabled = true`; delete
  `mac-mini-nwp.toml` + `docker-compose.macmini-nwp.yml`; rewrite the stale
  operator toggle in `docs/deployment/mac-mini-staging.md:304-336`. Rationale: the
  two-overlay split *was* the footgun; NWP-on is the intended steady state.
  Trade-off (runoff-only toggle lost) accepted — recover via a throwaway overlay
  if ever needed (residual fork).
- **A3 — DECIDED (redesigned): post-hoc dark detection, not a preflight.** Promote
  the existing `fc_result is None` / `primary_model_id is None` branches to ERROR
  + `errors.append`; keep the flow-level `ConfigurationError` only for
  "NWP adapter cannot be constructed at all". Simpler and more general than the
  climatology-keyed preflight (which duplicated an artifact fetch and could itself
  cause darkness by over-skipping a station a healthy persistence would serve).

### Part B — always-on fallback floor

- **B0/M0 — DECIDED: reconcile assignment priorities to config (prerequisite).**
  See M0 above. Without it the fallback tier is not encoded in the data and every
  `≥90` check is meaningless.
- **B1a — DECIDED:** both fallbacks; guarantee keyed on
  `climatology_fallback`-with-active-artifact (post-M0, priority 100).
- **B1b — DECIDED (STRICT floor-gate):** Step 8 requires the floor artifact before
  OPERATIONAL; Step 7 does not swallow floor-training failure; `flows/onboard.py`
  surfaces it non-green; floor plugin-load failure is loud; onboarding tests
  budgeted; idempotent backfill for 2009/2091.
- **B2 — DECIDED:** persistence empty-obs guard; native protocol accepted for
  Plan 100; native→FI is a separate track (residual).
- **B3 — DECIDED:** label fallback forecasts (badge + JSON flag), tier from M0c's
  single source; no migration.

### Part C — detectability + alert safety

- **C1 — DECIDED:** minimal grid-staleness check (C1a plumbing + C1b), full
  monitoring deferred to Flow 4.
- **Alert suppression — DECIDED (owner, 2026-07-06):** suppress flood-alert
  evaluation when the primary/only ensemble is fallback-tier. A climatology
  seasonal average cannot be a real flood alert; persistence "river is high now"
  belongs to Flow 2, not a forecast alert. Replaces the fictional
  webhook-label scope plan-review flagged.

## Residual forks / follow-ups (post-100)

- **[NEW TRACK] Native models → FI contract** (owner-requested): converge
  `climatology_fallback`, `persistence_fallback`, `linear_regression_daily` onto
  the FI contract. Separate plan (suggest Plan 102); ties to
  `feedback_forecastinterface_adherence_mandatory` + Plan 076.
- **[FORK] Runoff-only toggle** — lost by the A2 fold. Preserve via a throwaway
  overlay/env for future skill-comparison experiments, or accept losing it?
  (Leaning: accept; recover ad-hoc if needed.)
- **[HARDENING] `SAPPHIRE_CONFIG`-unset silent-off path** —
  `_load_weather_forecast_adapter_config` returns `enabled=False` when
  `SAPPHIRE_CONFIG` is unset entirely (a second silent-off path). Add an
  acceptance check that mini compose sets it; consider extending the A3 global
  gate to fail-loud if `SAPPHIRE_REQUIRE_NWP=1` and `SAPPHIRE_CONFIG` is unset.
- **[DEFERRED] Persisted `served_as_fallback` forecast column** + a persisted
  `is_fallback`/suppression flag on the `Alert` record — durable/queryable; needs
  a migration.

## Non-goals

- Fixing the NWP fetch itself — it works; the outage was the config gate + missing
  overlay.
- The observation-QC failures — tracked in Plan 101.
- Reworking the priority/first-success **algorithm** — the loop is correct; the
  gaps were the fold, the drifted priority *data* (M0), the inert-floor onboarding
  path (B1b), and fallback-blind alerting (M3). (Corrected: the earlier "gap was
  an empty assignment set" framing was wrong — assignments were present.)
- Converting native models to FI — separate track (residual).

## Verification

See **Acceptance checks** (10) in the IMPLEMENTATION VISION. Local dev repro:
onboard a test station with an NWP model + the floor, run a `forecast-cycle`
NWP-off → confirm a `climatology_fallback` row is written, badged fallback, and
**no alert fires**; flip NWP-on → skill model becomes primary. Cold-boot the mini
via `start-sapphire.sh` → NWP-on unconditionally, no `-nwp` file.

## Process

grill-me COMPLETE + one `plan-review` round folded (this doc). Next: **re-run
`plan-review`** to confirm the folded decisions (M0, A3 post-hoc, alert
suppression, strict floor-gate) converge with no new blockers/majors → phases →
READY → `vision-build` (WF2). Implementation touches: `model_onboarding.py`
(M0b priority default) + a named M0a reconciliation script; `mac-mini.toml` +
compose + `docs/deployment/mac-mini-staging.md` (A2); `run_forecast_cycle.py`
(A3 post-hoc ERROR+errors, C1a loader + `_WeatherForecastAdapterConfig` field,
C1b check, M3 alert suppression); `onboarding.py` Step 7/8 + `flows/onboard.py`
+ onboarding tests (B1b); `persistence_fallback.py` (B2); `alert_strategy.py`/
`alert_checker.py` (M3 suppression); API/templates (B3); a named idempotent
backfill for 2009/2091 → **hold-at-PR** with a version bump, per CLAUDE.md.
