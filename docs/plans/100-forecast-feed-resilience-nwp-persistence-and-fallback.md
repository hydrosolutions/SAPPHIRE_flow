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
- `src/sapphire_flow/services/onboarding.py:459-505` (Step 6 assigns **every discovered STATION-scope model** to each non-weather station)
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
- **A3 (fail-closed tripwire):** set `SAPPHIRE_REQUIRE_NWP=1` in
  `docker-compose.macmini.yml`. Add a **startup invariant** at the top of
  `run_forecast_cycle_flow` (`run_forecast_cycle.py:549`), before any station is
  processed:
  - Resolve `[adapters.weather_forecast].enabled`.
  - If `SAPPHIRE_REQUIRE_NWP=1` **and** it resolves `False`: query assignments;
    find stations that have an NWP model assigned but **no active
    `climatology_fallback` floor** (see M2 — the floor is keyed on climatology
    specifically, NOT on "any priority ≥ 90").
    - **Any exposed station → raise `ConfigurationError`** naming the exposed
      station IDs → the flow run **fails loudly** (worker refuses to forecast).
    - **No exposed station** (every station has the climatology floor) → **loud
      ERROR/WARN event, continue** on the fallback floor.
  - If NWP resolves `True` (steady state) → no-op.

### M2 — Always-on climatology floor (Part B: structural fallback)

- **B1a (both fallbacks, guarantee keyed on climatology):** the floor is
  `persistence_fallback(90)` **then** `climatology_fallback(100)`. Persistence
  gives better lead-1/2 skill when recent obs exist; **climatology is the
  guaranteed last-resort** (needs only its artifact). The "floor guarantee" and
  the A3 invariant key on **`climatology_fallback`-with-active-artifact**, never
  on "any priority ≥ 90" (a persistence-only station would pass a naïve check yet
  still go dark on empty obs).
- **B1b (onboarding-default + backfill):**
  - `services/onboarding.py` Step 6: assign the climatology floor (+persistence)
    **independent of the requested/narrowed skill-model set**, so even a
    deliberately narrow experimental station (the 2009/2091 failure mode) still
    gets the floor. Make the floor assignment explicit, not merely a side-effect
    of "assign every discovered model."
  - **Backfill existing 2009/2091** (one-off migration/admin action): create the
    `persistence_fallback`+`climatology_fallback` assignments **AND train +
    promote a `climatology_fallback` artifact** (needs ≥365 discharge rows). An
    assignment without an active artifact is **inert** (`_run_single_model`
    returns "no active artifact" → still dark) — artifact promotion is part of
    acceptance, not optional.
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

### M4 — Minimal runtime NWP-staleness tripwire (Part C1)

- One lightweight check: if NWP is expected but **no grid archived in >
  `monitoring.expected_delivery_offset_hours` × cadence**, emit a loud
  monitorable event on the **same channel A3 uses**. Full watchdog
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
3. **A3 tripwire fires + clears:** with `SAPPHIRE_REQUIRE_NWP=1` and NWP off, a
   station with NWP-only + no climatology floor → the flow **refuses to start**
   and names the station. Add the floor → the flow **starts**, serves
   climatology, logs the loud WARN.
4. **Backfill effective:** 2009/2091 have **active** climatology artifacts +
   assignments; a `forecast-cycle` under NWP-off writes fallback rows for **both**.
5. **persistence empty-obs:** `persistence_fallback.predict` on empty obs
   degrades gracefully (no reliance on an uncaught `IndexError`); climatology
   still produces the floor.

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

2. **No graceful degradation — the feed has no floor.** Stations 2009/2091 were
   onboarded with **only** the two NWP models (`nwp_regression`,
   `nwp_rainfall_runoff`) for the skill comparison — a deliberately **narrowed**
   assignment set (default onboarding assigns every discovered STATION model,
   including the fallbacks; this skill-comparison onboarding bypassed that). When
   both NWP models fail, the first-success loop
   (`run_station_forecast.py:301-341`) leaves `results` empty and writes
   **nothing** — there is no implicit climatology synthesis. The priority chain in
   `config.toml:56-64` *lists* the fallbacks, but a fallback only runs if it is
   **assigned** to the station, and it was not.

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
- **A3 — DECIDED: fail-closed, conditioned on a MISSING floor (startup invariant
  assert).** Add `SAPPHIRE_REQUIRE_NWP=1` (set on the mini via
  `docker-compose.macmini.yml`). At `run_forecast_cycle_flow` entry: if NWP is
  required and resolves `False`, and **any** station has an NWP model but no
  active `climatology_fallback` floor → **raise `ConfigurationError` naming the
  exposed stations** so the whole flow run refuses to start. If NWP is off but
  every station has the floor → **loud event + continue** on fallbacks.
  - **Rationale:** Part B makes the climatology floor *structural*, so in steady
    state this assert **never fires** — it is a tripwire that only slams the door
    when B's guarantee was bypassed (e.g., a future narrow skill-comparison
    station), i.e. exactly when genuine dark-feed risk exists. Blanket fail-closed
    (chosen over) was rejected: it would loop-crash the forecast worker on a
    *genuine* NWP outage instead of serving the fallback floor we just built.
  - **Blast radius (DECIDED):** whole-worker refuse if **any** station is exposed
    (one query, one decision, no per-station branching in the hot path).
    Per-station skip-and-flag was rejected — it re-introduces the silent
    per-station darkness we are fixing.

### Part B — always-on fallback floor (fix gap 2)

- **B1a — DECIDED: both `persistence_fallback(90)` + `climatology_fallback(100)`;
  guarantee keyed on climatology.** Persistence improves very-short-lead skill
  when recent obs exist; climatology is the only model that produces from its
  artifact alone (zero runtime obs) and is therefore the **guaranteed floor**.
  The floor guarantee + the A3 invariant key on
  **`climatology_fallback`-with-active-artifact**, not on "priority ≥ 90" —
  otherwise a persistence-only station passes the check yet still goes dark on
  empty obs.
- **B1b — DECIDED: onboarding-default (floor independent of skill set) + backfill
  existing, including the artifact.** `onboarding.py` Step 6 assigns the floor
  regardless of the requested/narrowed skill-model set, so a narrow experimental
  station cannot drop it. **AND** a one-off backfill for 2009/2091: create the
  assignments **and train + promote a `climatology_fallback` artifact** (an
  assignment without an active artifact is inert → still dark). A3's startup
  assert is the belt-and-suspenders that catches anything that still slips
  through.
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
  no grid archived in > `monitoring.expected_delivery_offset_hours` × cadence →
  emit a loud monitorable event on the same channel A3 uses. The full watchdog
  (source-outage detection, `weather_forecasts` row-staleness, alert routing)
  stays in the Flow-4 plan — referenced here, not built.

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

See **Acceptance checks** in the IMPLEMENTATION VISION above (5 checks: restart
persistence, floor-writes-and-is-labelled, A3-tripwire-fires-and-clears,
backfill-effective, persistence-empty-obs). Repro harness:

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
change** (`onboarding.py`, `run_forecast_cycle.py` flow entry,
`persistence_fallback.py`, the API/templates, the overlays + compose, a backfill
migration for 2009/2091) → **hold-at-PR** with a version bump, per CLAUDE.md.
