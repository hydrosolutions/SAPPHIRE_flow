# Plan 100 — forecast-feed resilience: persist NWP-on across restarts + always-on fallback (no silent blackout)

**Status**: DRAFT — **grill-me COMPLETE (2026-07-06)**; all five forks (A2, A3,
B1, B2, B3, C1) decided below. Next: `plan-review` workflow (WF1) → phases →
READY, then `vision-build` (WF2). Implementation is a **code change** →
**hold-at-PR** with a version bump, per CLAUDE.md.
**Priority**: high — the operational forecast feed went **fully dark for ~3 days
(2026-07-03 → 07-06)** with every flow reporting green. Two independent gaps had
to line up, and both are still open.
**Phase**: v0b — operational reliability
**Parent**: Plan 091 (mac-mini NWP-on data collection); the `nwp_regression`
skill-comparison track
**Related**:
- `scripts/launchd/start-sapphire.sh:24-27` (boot/launchd auto-start — **omits**
  `docker-compose.macmini-nwp.yml`)
- `scripts/bootstrap-mac-mini.sh:110-117` (same overlay omission on `down`/`up`)
- `config/overlays/mac-mini.toml:1-2` (`[adapters.weather_forecast] enabled = false`)
- `config/overlays/mac-mini-nwp.toml:7-8` (`enabled = true`)
- `docker-compose.macmini.yml:24,32` / `docker-compose.macmini-nwp.yml:11,17`
  (`SAPPHIRE_CONFIG_OVERLAY` = mac-mini.toml vs mac-mini-nwp.toml)
- `src/sapphire_flow/flows/run_forecast_cycle.py:142-` (`_load_weather_forecast_adapter_config` — NWP gated **off** when `SAPPHIRE_CONFIG` unset **or** `[adapters.weather_forecast].enabled` absent → defaults `False`); flow entry `run_forecast_cycle_flow` at `:549`
- `src/sapphire_flow/services/run_station_forecast.py:301-341` (priority first-success loop; **empty `results` → `primary_model_id=None` → no forecast row written**); `_run_single_model:174-208` (predict wrapped in try/except → a raise becomes a graceful "predict failed" reason and the loop advances)
- `config.toml:56-64` (priority chain: `nwp_regression=10`, `nwp_rainfall_runoff=20`, `linear_regression_daily=30`, `persistence_fallback=90`, `climatology_fallback=100`); `FALLBACK_PRIORITY_THRESHOLD=90` (`types/ids.py:20`)
- `src/sapphire_flow/models/climatology_fallback.py` (needs only its trained day-of-year artifact — **zero runtime obs** → the only guaranteed floor), `src/sapphire_flow/models/persistence_fallback.py` (needs ≥1 recent obs; `past_targets[param][-1]` → `IndexError` on empty)
- `src/sapphire_flow/services/onboarding.py:472-505` (Step 6 assigns **every discovered STATION-scope model** unconditionally — no narrowing hook), `:637-643` (Step 7 swallows per-model training failure), `:662-673` (Step 8 marks OPERATIONAL on **any** active artifact, not the floor), `flows/onboard.py:208-211` (never fails on non-empty `errors`)
- `src/sapphire_flow/api/routes/forecasts.py` + `api/templates/forecasts/{list,detail}.html` (dashboard renders `f.model_id`; status badges exist, no fallback badge); `api/routes/api_forecasts.py` (JSON `ForecastDetail` carries `model_id`)
**Created**: 2026-07-06

---

## IMPLEMENTATION VISION (decided spec — feeds WF1 plan-review + WF2 vision-build)

The fix has **four milestones**. M1+M2 are the incident fix (the two gaps that
aligned); M3 protects operators from trusting a fallback; M4 is a minimal
staleness tripwire. Native→FI convergence and the `SAPPHIRE_CONFIG`-unset path
are **out of scope** (residuals below).

### M1 — Persist NWP-on deterministically (Part A: fold + fail-closed tripwire)

- **A2 (fold):** `config/overlays/mac-mini.toml` sets
  `[adapters.weather_forecast] enabled = true` **explicitly** (self-documents the
  mini as NWP-on rather than inheriting base `config.toml`'s `enabled=true`
  implicitly). **Delete** `config/overlays/mac-mini-nwp.toml` and
  `docker-compose.macmini-nwp.yml`. There is now **one** overlay and **one**
  mini compose file — no startup path can "forget the -nwp file." The A1 script
  edits (adding `-f docker-compose.macmini-nwp.yml`) are **dropped as
  unnecessary**; `start-sapphire.sh` + `bootstrap-mac-mini.sh` keep bringing up
  `docker-compose.yml + docker-compose.macmini.yml` only.
- **A3 (fail-closed tripwire — per-station scoped, revised after plan-review
  blocker):** set `SAPPHIRE_REQUIRE_NWP=1` in `docker-compose.macmini.yml`.
  - **Global gate (flow-level refuse, the ONLY hard-refuse path):** resolve
    `[adapters.weather_forecast].enabled` once at flow entry
    (`run_forecast_cycle_flow`, `run_forecast_cycle.py:549`). Raise a flow-level
    `ConfigurationError` **only** for a *genuinely global* misconfiguration —
    `SAPPHIRE_REQUIRE_NWP=1` **and** the NWP adapter itself cannot be constructed
    (missing STAC config etc.), i.e. no station could ever get NWP this cycle.
    This is the only condition that warrants stopping every station.
  - **Per-station tripwire (scoped-and-loud, NOT flow-level):** when
    `SAPPHIRE_REQUIRE_NWP=1` **and** `enabled` resolves `False` (NWP off but the
    adapter is otherwise fine), do the exposure check **inside the existing
    per-station loop** (`run_forecast_cycle.py:958`), reusing the already
    batch-fetched `model_assignments[sid]` dict (`run_forecast_cycle.py:757-760`
    — no new bulk query). A station is *exposed* iff it has an NWP model assigned
    but **no `climatology_fallback` assignment with an active artifact** (see M2 —
    the floor is keyed on climatology specifically, NOT on "any priority ≥ 90").
    - **Exposed station → skip ONLY that station**, mirroring the file's existing
      `forecast_cycle.station_skipped_model_not_loaded` convention
      (`run_forecast_cycle.py:973-984`): log an ERROR event naming the station,
      append to `errors`, increment `stations_failed`, `continue`. Every other
      station still forecasts.
    - **Non-exposed station** (has the climatology floor) → **loud WARN event,
      proceed** on the fallback floor.
  - If NWP resolves `True` (steady state) → no-op.
  - **Why per-station, not whole-flow (blocker resolution):** the whole-worker
    refuse originally decided here would, by design, black out ALL ~1000
    operational stations in the single flow invocation (`for station in
    operational:` at `run_forecast_cycle.py:958`) the moment ONE narrow
    skill-comparison station lacked its floor — *guaranteeing* the total darkness
    Goal 2 exists to prevent. The scoped skip reuses `stations_failed` +
    `errors` (already on `ForecastCycleResult`, `run_forecast_cycle.py:89-97`) as
    the loud, monitorable, non-silent signal, exactly as the file already does
    for a station whose configured model can't be loaded. The tripwire stays loud
    without letting one bad onboarding record dark the whole feed.

### M2 — Always-on climatology floor (Part B: structural fallback)

- **B1a (both fallbacks, guarantee keyed on climatology):** the floor is
  `persistence_fallback(90)` **then** `climatology_fallback(100)`. Persistence
  gives better lead-1/2 skill when recent obs exist; **climatology is the
  guaranteed last-resort** (needs only its artifact). The "floor guarantee" and
  the A3 invariant key on **`climatology_fallback`-with-active-artifact**, never
  on "any priority ≥ 90" (a persistence-only station would pass a naïve check yet
  still go dark on empty obs).
- **B1b (onboarding floor-gate + backfill — retargeted after plan-review
  blocker):** the original framing (Step 6 "bypasses the default assignment set")
  does **not** match the code: `onboarding.py` Step 6 (`onboarding.py:472-505`)
  *already* iterates over **every** `discover_models()` entry unconditionally for
  every non-weather station — there is no per-station narrowing hook to bypass,
  and both fallbacks would have been assigned. The real mechanism that let
  2009/2091 go live with an **inert** floor (verified against the code):
  - **Step 7 swallows a per-model training failure** (`onboarding.py:637-643`:
    `except Exception` → append to `errors` + log, no abort). A floor model whose
    training fails (e.g. `climatology_fallback` needs ≥365 discharge rows against
    a BAFU-LINDAS **real-time-only** deployment with a fresh, short archive) leaves
    **no active artifact** — the assignment is inert.
  - **Step 8 marks OPERATIONAL on "any model active", not the floor**
    (`onboarding.py:662-673`: `has_active = any(... for mid in discovered)`). A
    station with an active *NWP* artifact but a failed *floor* artifact is marked
    OPERATIONAL anyway.
  - **The onboarding flow never fails on a non-empty `errors` list**
    (`flows/onboard.py:208-211` logs `errors=len(result.errors)` and returns) — so
    the run reports green despite a silently-failed floor.

  Fix, retargeted to that mechanism:
  - **Step 8 floor-gate:** require the **floor model(s) specifically**
    (`climatology_fallback` with an active artifact) — not "any discovered model" —
    before a non-weather station is marked OPERATIONAL. A station without an active
    climatology artifact stays NOT operational and logs a loud ERROR.
  - **Step 7 floor-failure is not swallowed:** a training failure for the floor
    model(s) must either fail the onboarding run (surface on `errors` **and** flip
    the flow to a non-green outcome) or explicitly leave the station NOT
    operational — never silently `continue` past a dark floor. (Skill-model
    training failures may still be tolerated per the existing convention.)
  - **Backfill existing 2009/2091** (named idempotent admin action, see below):
    create the `persistence_fallback`+`climatology_fallback` assignments **AND
    train + promote a `climatology_fallback` artifact** (needs ≥365 discharge
    rows). An assignment without an active artifact is **inert**
    (`_run_single_model` returns "no active artifact" → still dark) — artifact
    promotion is part of acceptance, not optional.
- **B2 (fallback reliability + persistence guard):** `climatology_fallback`
  produces from its artifact alone → the floor holds under NWP-off. Add an
  explicit **empty/short-obs guard** to `persistence_fallback.predict` so an
  anticipated no-obs case **degrades cleanly** (graceful skip) instead of leaning
  on SAP3's `try/except` backstop (CLAUDE.md: the backstop is for *unanticipated*
  bugs only). Climatology remains the true floor beneath it.

### M3 — Label fallback-sourced forecasts (Part B3)

- Compute `is_fallback = deployment_config.priority_for_model(model_id) >=
  FALLBACK_PRIORITY_THRESHOLD` at **render/serialize time** (no DB migration; the
  forecast row already carries `model_id`).
- Render a distinct **`FALLBACK` badge** in `forecasts/list.html` +
  `forecasts/detail.html`, and expose the `is_fallback` flag on the JSON forecast
  schema (`api/routes/api_forecasts.py`). Config-driven, not name-string matching.
- **M3-alert (safety-relevant, added after plan-review major):** the alert path
  is currently fallback-blind. `PrimaryModelStrategy.evaluate`
  (`alert_strategy.py:146-194`) picks the min-priority ensemble present with **no**
  fallback filtering, and `ExceedanceResult` carries only `model_ids`/`strategy`
  (`alert_strategy.py:181-192`) — no fallback flag. Phase C passes the unfiltered
  `all_ensembles` (which, in the M2 floor scenario, may contain **only**
  `climatology_fallback`) straight into `check_station_alerts`
  (`run_forecast_cycle.py:1439-1460`). So once M2 makes climatology the guaranteed
  floor, an alert could fire off a pure-climatology forecast, **indistinguishable
  in the alert record and the webhook payload from an NWP-driven alert** — the one
  consumer (flood alerts, webhook-only per `feedback_alert_delivery_webhook_only`)
  where mislabeling matters most. Goal 2's "always writes something, clearly
  labeled" does not cover this today. **Decision:** propagate the same
  priority-derived `is_fallback` flag (`priority_for_model(primary_model_id) >=
  FALLBACK_PRIORITY_THRESHOLD`) onto `ExceedanceResult` and into the webhook
  payload so downstream consumers can distinguish a climatology-sourced alert.
  Whether a fallback-only cycle should **suppress/downgrade** alerting rather than
  alert normally is an explicit fork for plan-review to resolve — recorded here so
  it ships as a deliberate decision, not a silent default.

### M4 — Minimal runtime NWP-staleness tripwire (Part C1)

- **C1a (config plumbing — new sub-step, prerequisite):** `monitoring.
  expected_delivery_offset_hours` is currently **dead TOML**.
  `config.toml:397` defines it under `[adapters.weather_forecast.monitoring]`,
  but `load_config()` **discards** it (`config/deployment.py:341-343`:
  `data.pop("adapters", None)` and `data.pop("monitoring", None)`), and the
  adapter loader `_load_weather_forecast_adapter_config`
  (`run_forecast_cycle.py:100-106`, populating `_WeatherForecastAdapterConfig`)
  reads only `enabled`/`stac_base_url`/`stac_collection`/`scratch_path`/
  `max_files`/`grid_extractor` — never the nested `monitoring` table
  (`grep -rn expected_delivery_offset_hours src/ tests/` → no hits). So the
  staleness check **cannot read the value until config plumbing is added**:
  extend `_WeatherForecastAdapterConfig` (+ its loader) with
  `expected_delivery_offset_hours` / `expected_cycles_per_day`, or add a sibling
  loader for the `monitoring` sub-table. This work is a real deliverable of M4,
  not free.
- **C1b (the check):** once C1a lands, one lightweight check — if NWP is expected
  but **no grid archived in > `expected_delivery_offset_hours` × cadence**, emit a
  loud monitorable event on the **same channel A3 uses**. Full watchdog
  (source-outage detection, `weather_forecasts` row-staleness, alert fan-out)
  is **deferred to the Flow-4 monitoring plan** — referenced, not built here.

### Acceptance checks (what WF2 must make pass)

1. **Restart persistence:** cold-boot the mini via `start-sapphire.sh` → a
   `forecast-cycle` runs **NWP-on** (`nwp.*` logs present, grids archived), no
   manual overlay step, no `-nwp` file anywhere.
2. **Floor writes + is labelled:** local repro — station with an NWP model + the
   floor, stack **NWP-off** → a `climatology_fallback` row **IS** written and
   **badged FALLBACK** on the dashboard/JSON. Flip **NWP-on** → the skill model
   becomes primary and the row is **not** badged fallback.
3. **A3 tripwire fires per-station + clears (revised):** with
   `SAPPHIRE_REQUIRE_NWP=1` and NWP off, station X (NWP-only, no climatology floor)
   is **skipped and named** — ERROR event, `stations_failed` incremented, its
   error on `errors` — while **every other station still forecasts** (the flow
   does NOT abort). Add the floor to X → X **starts**, serves climatology, logs
   the loud WARN. Separately, the flow-level `ConfigurationError` fires **only**
   when `SAPPHIRE_REQUIRE_NWP=1` and the NWP adapter cannot be constructed at all.
4. **Backfill effective + idempotent:** 2009/2091 have **active** climatology
   artifacts + assignments; a `forecast-cycle` under NWP-off writes fallback rows
   for **both**. Re-running the named backfill script is a **no-op** (no duplicate
   assignments, no second ACTIVE artifact).
5. **persistence empty-obs:** `persistence_fallback.predict` on empty obs
   degrades gracefully (no reliance on an uncaught `IndexError`); climatology
   still produces the floor.
6. **Onboarding floor-gate (retargeted B1b):** onboard a test station whose
   `climatology_fallback` training is forced to fail → the station is **NOT**
   marked OPERATIONAL and the onboarding run reports a non-green outcome (the
   failure is not swallowed by `flows/onboard.py`). With floor training
   succeeding → OPERATIONAL as normal.
7. **C1 staleness fires:** after C1a config plumbing lands, force a grid-archival
   gap past `expected_delivery_offset_hours × cadence` → the staleness event fires
   on the **same channel A3 uses** (proves the tripwire is not dead code).
8. **Alert labelling (M3-alert):** a fallback-only cycle that trips an alert emits
   an `ExceedanceResult` / webhook payload carrying `is_fallback=True`,
   distinguishable from an NWP-driven alert.

---

## Problem (the 2026-07-03 → 07-06 blackout)

The forecast feed produced **zero** rows for ~3 days while `forecast-cycle`
completed green every 6 h. Root-caused live on the mini + dev:

1. **NWP-on was not persisted across restarts.** The mac-mini runs NWP-on only
   when the stack is brought up with **both** `docker-compose.macmini.yml` **and**
   `docker-compose.macmini-nwp.yml` (the latter sets
   `SAPPHIRE_CONFIG_OVERLAY=mac-mini-nwp.toml` → `enabled=true`). But the
   **auto-start** path (`start-sapphire.sh:24-27`, run on boot/launchd) brings the
   stack up with **only** `docker-compose.macmini.yml` → `mac-mini.toml` →
   `enabled=false`. NWP-on was set up manually and never written into the startup
   script. When the stack restarted on 07-03 (during the obs-ingest debugging),
   NWP silently reverted to **off**: the forecast-cycle ran runoff-only, emitted
   **no `nwp.*` logs**, archived no grids (the last grid is 07-03; Plan-095
   `nwp_grid_retention_days=3` then pruned them), and every NWP model returned
   `ModelFailure`.

2. **No graceful degradation — the feed has no floor.** Stations 2009/2091 ended
   the skill comparison with an **inert** floor. (Narrative corrected after
   plan-review: the earlier "narrowed assignment set bypassed the default" story
   does **not** match the code — `onboarding.py:472-505` Step 6 assigns *every*
   discovered STATION model unconditionally, with no narrowing hook.) The verified
   mechanism is a silently-failed floor **artifact**, not a missing assignment:
   - Step 7 wraps each model's training in `except Exception`
     (`onboarding.py:637-643`) that only appends to `errors` — a
     `climatology_fallback` training failure (its ≥365-discharge-row requirement
     against a BAFU-LINDAS **real-time-only** deployment with a short fresh
     archive, per `project_bafu_lindas_realtime_only`) leaves **no active
     artifact** and does **not** abort onboarding.
   - Step 8 marks a station OPERATIONAL if **any** discovered model has an active
     artifact (`onboarding.py:662-673`, `has_active = any(...)`) — an active NWP
     artifact alongside a dead floor artifact still passes.
   - `flows/onboard.py:208-211` never fails on a non-empty `errors` list, so the
     run reported green.

   At forecast time, an assignment whose artifact is inactive is skipped
   (`_run_single_model` → "no active artifact"), so when both NWP models fail the
   first-success loop (`run_station_forecast.py:301-341`) leaves `results` empty
   and writes **nothing** — no implicit climatology synthesis. The priority chain
   in `config.toml:56-64` *lists* the fallbacks, but a fallback only runs if it is
   assigned **and** has an active artifact; here the artifact was missing.

Either gap alone is survivable; together they produced a silent multi-day outage.
MeteoSwiss was healthy throughout (STAC `updated` 07-06 08:44Z), so this was
entirely our deployment + resilience gap, not an external outage.

## Goal

1. **NWP-on survives every restart/reboot deterministically** — no manual overlay
   step, no way for a boot to silently drop NWP.
2. **The forecast feed can never go fully dark** — when the skill (NWP) models
   fail, a fallback always writes *something*, so the dashboard always has a
   current forecast (clearly labelled as a fallback).
3. **A silent NWP-off is detectable** — if NWP is expected but disabled, or no NWP
   grid has been archived in > N hours, monitoring surfaces it (ties to Flow 4).

## Design decisions (grill-me RESOLVED 2026-07-06)

### Part A — persist NWP-on (fix gap 1)

- **A1 — SUPERSEDED by A2.** With the fold (A2), there is no `-nwp` overlay to add
  to the startup scripts. The scripts stay as-is (`docker-compose.yml +
  docker-compose.macmini.yml`). Kept here only to record that the originally
  proposed "add `-f docker-compose.macmini-nwp.yml`" edit is **no longer needed**.
- **A2 — DECIDED: FOLD NWP-on into the base mini overlay; delete the `-nwp`
  files.** `config/overlays/mac-mini.toml` sets `[adapters.weather_forecast]
  enabled = true` explicitly; **delete** `config/overlays/mac-mini-nwp.toml` and
  `docker-compose.macmini-nwp.yml`. One overlay, one mini compose file — the
  two-overlay split *was itself the footgun*, and NWP-on is now the intended
  steady state.
  - **Rationale:** the base `config.toml` already ships `enabled=true`; the sole
    job of `mac-mini.toml` was to *actively disable* NWP, and `mac-mini-nwp.toml`
    to re-enable it. Collapsing to one NWP-on overlay makes "forget the -nwp file"
    unrepresentable on any startup path.
  - **Trade-off accepted:** loses the one-flag runoff-only toggle. If a
    runoff-only experiment is ever needed, recover it with a throwaway overlay or
    an env override — not worth a permanent footgun for a rare experiment.
- **A3 — DECIDED (REVISED after plan-review blocker): fail-closed, but
  per-station scoped-and-loud, not whole-flow.** Add `SAPPHIRE_REQUIRE_NWP=1`
  (set on the mini via `docker-compose.macmini.yml`). At `run_forecast_cycle_flow`
  entry, resolve `enabled` once; raise a flow-level `ConfigurationError` **only**
  when NWP is required and the adapter itself cannot be constructed (a truly global
  defect). When NWP is required but merely resolves `False`, the exposure check
  runs **inside the per-station loop** (`run_forecast_cycle.py:958`), reusing the
  already batch-fetched `model_assignments[sid]` (`run_forecast_cycle.py:757-760`):
  an exposed station (NWP model assigned, no active `climatology_fallback` floor)
  is **skipped and flagged loud** — ERROR event, `stations_failed++`, `errors`
  appended, `continue` — exactly like the existing
  `station_skipped_model_not_loaded` path (`run_forecast_cycle.py:973-984`). Every
  other station still forecasts.
  - **Rationale:** Part B makes the climatology floor *structural*, so in steady
    state this tripwire **never fires**. It only trips for a station whose floor
    was bypassed (a future narrow skill-comparison station), i.e. exactly when
    genuine dark-feed risk exists. Blanket fail-closed was rejected: it would
    loop-crash the worker on a *genuine* NWP outage instead of serving the floor.
  - **Blast radius (REVISED — previous "whole-worker refuse if any station is
    exposed" was the plan-review blocker):** the original decision *guaranteed*
    total darkness — one misconfigured station in the single flow invocation (the
    `for station in operational:` loop over all ~1000 stations,
    `run_forecast_cycle.py:958`) would abort the entire feed, strictly worse than
    the 2-station incident it guards against and a direct contradiction of Goal 2.
    Corrected to **per-station skip-and-flag**, reusing `stations_failed`/`errors`
    (already on `ForecastCycleResult`, `run_forecast_cycle.py:89-97`) as the loud,
    monitorable signal — the same convention the file already uses for a
    per-station config defect. The earlier "per-station skip re-introduces silent
    darkness" objection is void: `stations_failed`/`errors` are *not* silent.
  - **"One query" claim retracted:** the previous "one query, one decision" framing
    was wrong — the store Protocol exposes only per-station lookups
    (`fetch_model_assignments(station_id)`, `stores.py:515`;
    `fetch_active_artifact_for_station`, `stores.py:421`), no bulk join. The revised
    design needs **no** new query: it piggybacks on the `model_assignments` dict
    already batch-fetched at `run_forecast_cycle.py:757-760`, plus a per-station
    active-artifact check only for exposed candidates (bounded by the loop already
    running), so there is no ~2000-round-trip cost at 1000-station scale.

### Part B — always-on fallback floor (fix gap 2)

- **B1a — DECIDED: both `persistence_fallback(90)` + `climatology_fallback(100)`;
  guarantee keyed on climatology.** Persistence improves very-short-lead skill
  when recent obs exist; climatology is the only model that produces from its
  artifact alone (zero runtime obs) and is therefore the **guaranteed floor**.
  The floor guarantee + the A3 invariant key on
  **`climatology_fallback`-with-active-artifact**, not on "priority ≥ 90" —
  otherwise a persistence-only station passes the check yet still goes dark on
  empty obs.
- **B1b — DECIDED (RETARGETED after plan-review blocker): floor-gate the
  operational mark + un-swallow floor-training failure + idempotent backfill.**
  Step 6 already assigns every discovered model unconditionally
  (`onboarding.py:472-505`) — there is no narrowing to fix there. The real defect
  is that a **failed floor artifact** still yields an OPERATIONAL station:
  - **Step 8** (`onboarding.py:662-673`) must require `climatology_fallback` (the
    floor) to have an active artifact before marking a non-weather station
    OPERATIONAL — not "any discovered model" (`has_active = any(...)`).
  - **Step 7** (`onboarding.py:637-643`) must not silently swallow a *floor*-model
    training failure: either fail the run or leave the station NOT operational
    (skill-model failures may still be tolerated). And `flows/onboard.py:208-211`
    must surface a floor failure as a non-green outcome rather than logging
    `errors=N` and returning green.
  - **Backfill 2009/2091** as a **named, idempotent admin action** (see below):
    create the assignments **and train + promote a `climatology_fallback`
    artifact** (an assignment without an active artifact is inert → still dark).
  A3's per-station tripwire is the belt-and-suspenders that catches anything that
  still slips through.
  - **Backfill idempotency (minor resolution):** implement as a small named admin
    script (or reuse `onboard-model`/`onboard-stations` scoped to the two station
    IDs). `create_station_assignment` already upserts (Plan 089), so re-running
    assignment creation is safe; the script must **guard artifact promotion** so a
    second run does not create a duplicate ACTIVE `climatology_fallback` artifact
    (check for an existing active artifact first → no-op). Acceptance check 4
    asserts the re-run is a no-op.
- **B2 — DECIDED: accept the native protocol for Plan 100 + harden the
  persistence empty-obs guard.** `climatology_fallback` / `persistence_fallback`
  are **native `ForecastModel` implementations** (train/predict/serialize), not
  FI-adapter models, so they have no `input_requirement()`/`ModelFailure`
  channel; SAP3's `try/except` in `_run_single_model` is the "return-not-raise"
  boundary. Plan 100's hard requirement is only that **climatology always
  succeeds given an artifact** — it does. Add an explicit empty/short-obs guard to
  `persistence_fallback` so an *anticipated* no-obs case degrades cleanly instead
  of relying on the backstop.
  - **FI mandate (owner, 2026-07-06):** the owner **does want** the existing
    native models converted onto the FI contract — but as a **SEPARATE track**,
    not folded into Plan 100. Recorded as a **residual follow-up** below (it also
    touches `linear_regression_daily` and any other native model, so it is a track
    of its own, not an incident fix).
- **B3 — DECIDED: compute `is_fallback` via priority lookup at render/serialize;
  badge in list + detail + API.** `is_fallback =
  priority_for_model(model_id) >= FALLBACK_PRIORITY_THRESHOLD`. Show a distinct
  `FALLBACK` badge on `forecasts/list.html` + `detail.html` and expose the flag on
  the JSON forecast schema. No DB migration; config-driven (not name-string
  matching). A persisted `served_as_fallback` column (queryable "how many fallback
  forecasts did we serve?") is the v-next upgrade — deferred, needs a migration.

### Part C — detectability (fix gap 3)

- **C1 — DECIDED: minimal grid-staleness check here + reuse the A3 signal; full
  monitoring deferred to Flow 4.** Add one runtime check: if NWP is expected but
  no grid archived in > `expected_delivery_offset_hours` × cadence → emit a loud
  monitorable event on the same channel A3 uses. The full watchdog (source-outage
  detection, `weather_forecasts` row-staleness, alert routing) stays in the Flow-4
  plan — referenced here, not built.
  - **Config-plumbing prerequisite (major resolution):** the cited value is
    **dead TOML today** — `config.toml:397` defines
    `expected_delivery_offset_hours` under `[adapters.weather_forecast.monitoring]`
    but it is discarded on load (`config/deployment.py:341-343` pops both
    `adapters` and `monitoring`) and never read by
    `_load_weather_forecast_adapter_config` (`run_forecast_cycle.py:100-106`).
    M4/C1a must add the loader + `_WeatherForecastAdapterConfig` field before the
    check can read it; this is scoped work, not a free lookup.

## Residual forks / follow-ups (post-100)

- **[NEW TRACK] Native models → FI contract.** Owner-requested (2026-07-06):
  converge `climatology_fallback`, `persistence_fallback`,
  `linear_regression_daily`, and any other native `ForecastModel` onto the FI
  contract (declare `input_requirement()`, return `ModelFailure`, route through
  `ForecastInterfaceAdapter`). **Separate plan** (suggest Plan 102) — not in
  Plan 100 scope. Ties to `feedback_forecastinterface_adherence_mandatory` and
  the Plan-076 FI adapter.
- **[HARDENING] `SAPPHIRE_CONFIG`-unset silent-off path.**
  `_load_weather_forecast_adapter_config` returns `enabled=False` when
  `SAPPHIRE_CONFIG` is **unset entirely** — a second silent-NWP-off path
  independent of overlays. In-scope-adjacent: **acceptance check** that the mini
  compose sets `SAPPHIRE_CONFIG`; consider extending the A3 assert to fail-loud if
  `SAPPHIRE_REQUIRE_NWP=1` **and** `SAPPHIRE_CONFIG` is unset. Flag for
  plan-review to decide whether to pull into M1.
- **[DEFERRED] Persisted `served_as_fallback` forecast column** (B3 v-next) —
  durable/queryable labelling; needs a DB migration.

## Non-goals

- Fixing the NWP fetch itself — it works; the outage was the config gate + missing
  overlay, not the fetcher.
- The observation-QC failures (`ingest.qc_complete failed=2`) — tracked in Plan 101.
- Reworking the priority/first-success algorithm — it is correct; the gap was an
  empty assignment set, not the loop.
- Converting native models to the FI contract — owner-requested but a separate
  track (residual above), not this incident fix.

## Verification (uses the local dev stack — now up)

See **Acceptance checks** in the IMPLEMENTATION VISION above (8 checks: restart
persistence, floor-writes-and-is-labelled, A3-per-station-tripwire, idempotent
backfill, persistence-empty-obs, onboarding-floor-gate, C1-staleness-fires,
alert-labelling). Repro harness:

- **Local repro of the floor:** onboard a test station with an NWP model + the
  fallback(s), bring the stack up NWP-**off** (or point the adapter at an empty
  cycle), run a `forecast-cycle`, and confirm a `climatology_fallback` row **is**
  written and badged as fallback. Then NWP-**on** and confirm the skill model
  becomes primary.
- **Restart persistence:** confirm the folded `mac-mini.toml` (A2) brings the mini
  up NWP-on from a cold boot **unconditionally**, with no `-nwp` file present.

## Process

Grill-me **COMPLETE** (A2, A3, B1a/B1b, B2, B3, C1 all decided above). Next:
run the `plan-review` workflow (WF1) — it has concrete milestones + acceptance
checks to attack, plus three residuals to adjudicate (native→FI track scope,
`SAPPHIRE_CONFIG`-unset hardening pull-in, persisted-flag deferral) — then break
into phases → READY, then `vision-build` (WF2). Implementation is a **code
change** — `onboarding.py` (Step 7/8 floor-gate, retargeted B1b), `flows/
onboard.py` (surface floor-failure as non-green), `run_forecast_cycle.py` (A3
per-station tripwire in the loop + C1a monitoring-config loader + `_Weather
ForecastAdapterConfig` field + C1b staleness check), `alert_strategy.py`/
`ExceedanceResult` + webhook payload (M3-alert `is_fallback`),
`persistence_fallback.py`, the API/templates, the overlays + compose, and a named
idempotent backfill script for 2009/2091 → **hold-at-PR** with a version bump,
per CLAUDE.md.
