# Plan 100 — forecast-feed resilience: persist NWP-on across restarts + always-on fallback (no silent blackout)

**Status**: DRAFT — grill-me COMPLETE (2026-07-06) + **plan-review round 3
decisions folded (2026-07-06)**: v0-scope §A4 amendment **ratified** (the OPERATIONAL
floor-gate is a deliberate, owner-ratified tightening of the locked rule, not a
silent override); **categorical `FALLBACK_MODEL_IDS` single-source tier** (the same
`model_id ∈ FALLBACK_MODEL_IDS` boolean now drives forecast combination, the B3
badge, AND M3 alert-suppression — no more dual DB-priority-vs-config divergence, DB
`priority` demoted to ordering/tie-break among admitted skill models only); a
**write-time guard** at the assignment choke points (a below-tier fallback write now
raises, a structural barrier not just C1c's post-hoc tripwire); and the residuals
resolved (B2 FI-repo issue mandatory, `SAPPHIRE_CONFIG`-unset folded into the A3 gate,
runoff-only toggle loss accepted, persisted `served_as_fallback` deferred). Earlier
round 3 (code-grounded blocker/major sweep): M3-alert **scoped to the shipped PRIMARY
default** (`all_ensembles` filtered ONCE centrally before Phase C — covers the
`:1111` PRIMARY build, the `:1207-1210` combination build, AND the `:1414` GROUP
build, closing the `n_models<=1` unfiltered-shortcut leak); M0c gained a
**config-load-time validator + `FALLBACK_MODEL_IDS` constant** so a fallback
mis-priced in config fails loud instead of silently misclassifying; **M0a `time_step`
side-effect audit** added (priority reorder must not silently flip input cadence);
`SAPPHIRE_REQUIRE_NWP` given a **typed parse point + compose wiring**; Process
doc-list extended to `conventions.md`+`types-and-protocols.md` for the **categorical
tier** scheme; **B2 FI-adherence** discharged via a filed FI-repo issue at land time.
Round 1: A3 redesigned to post-hoc detection, alert path
flipped from label→**suppress**, a new **M0 priority-reconciliation** prerequisite
added after a live DB check, root-cause narrative corrected to "unconfirmed +
defense-in-depth". Round 2 (code-grounded): M0 root-cause citation **corrected** —
the live drift vector is `onboard_model_flow` (`flows/onboard_model.py:470`), not
`create_station_assignment` (the two `onboard_model()` callers already override the
default per Plan 089); M0a extended to `group_model_assignments`; M0c **decided**
(config-derived tier for the safety-critical B3/M3 checks — **superseded in round 3
by the categorical `FALLBACK_MODEL_IDS` single-source tier, Decision 2**); M3 extended
to tier-filter the **pooled/BMA/consensus** alert path (not just Primary); B3
reconciled with the `input_quality` spec mechanism; fleet-wide floor audit + a
fallback-priority drift tripwire (C1c) added; several citations corrected. **Round 4 (code-grounded major sweep, 2026-07-06) folded:** M0a precedence rule 2
replaced (value-pattern "deliberate override" inference was indistinguishable from the
bug's own drift signature → now an **explicit `(station_id, model_id)` override
allowlist**, every other skill row unconditionally → config); **M0a write path pinned
to priority-only** (the upsert helpers also SET `time_step`/`status`, and re-deriving
`time_step` would clobber operator-set cadence → read-back-and-round-trip OR a new
`UPDATE` store method); **A3 `SAPPHIRE_REQUIRE_NWP` read UNCONDITIONALLY at the loader
top** into BOTH the `SAPPHIRE_CONFIG`-unset early-return dataclass AND the main path
(not "mirroring" `enabled`, whose early-return value is hardcoded `False` — mirroring
would silently defeat the unset-config gate case — now the single general check,
see round-4 correctness sweep below); **broken regression test**
`test_run_station_forecast.py:617-640` added to the migration budget; **retroactive
`FORECAST`-alert audit** added (acceptance check 15, Goal #4); **`docs/v0-scope.md`
model-inventory + §A8e mechanism** added to the doc-sync list; `FALLBACK_MODEL_IDS`
retyped `frozenset[ModelId]`. **Plan-review round 4 (correctness sweep, 2026-07-06)
folded:** A3 gate **generalized to the single check `require_nwp and not
weather_forecast_config.enabled` at `:642`** (the two enumerated conditions missed the
explicit-`enabled=false` merged-TOML case that was the actual incident); **M0c
config-load validator scoped to fallback ids EXPLICITLY PRESENT in `[model_priorities]`**
(the `priority_for_model(...)`-based form over-fired via `DEFAULT_PRIORITY=50` and broke
`make_deployment_config()`); **B2 RAISES a narrow `InsufficientObservationsError`** (the
native `predict` Protocol has no `ModelFailure` return — that is FI-only); **`ModelTier`
enum replaces the `is_fallback: bool`** on the badge/JSON schema (CLAUDE.md
enum-over-bool); **M0a gains a pre-rewrite read-only fleet AUDIT** (allowlist must not be
assumed = the 2-station dev sample); **B3 extended to the model-assignment surfaces**
(`stations/detail.html:89`, `models/detail.html:59`, `_to_model_assignment_response`);
plus minors — DB `server_default="0"` recorded as an open residual (C1c-tracked), M0a
option-(b) `UPDATE` path guarded/scoped, vestigial `MultiModelForecastResult.priorities`
flagged, M3 suppression pinned to station-level granularity, `architecture-context.md`
doc-sync extended to rewrite the numeric-gate mechanism clause to categorical. Still
DRAFT — re-run `plan-review` to confirm convergence, then phases → READY, then
`vision-build` (WF2).
Implementation is a **code change** → **hold-at-PR** with a version bump.
**Priority**: high — the operational forecast feed went **fully dark for ~3 days
(2026-07-03 → 07-06)** with every flow reporting green.
**Phase**: v0b — operational reliability
**Parent**: Plan 091 (mac-mini NWP-on data collection); the `nwp_regression`
skill-comparison track
**Related**:
- `scripts/launchd/start-sapphire.sh:24-27` (startup path — omits the `-nwp` overlay); `scripts/bootstrap-mac-mini.sh:278-284` (the install/`up` path — `docker compose … up -d`, macmini overlay only, no `-nwp` file). *(NOTE: `bootstrap-mac-mini.sh:110-117` is the UNINSTALL branch's `docker compose … down` — inside `if [ "${UNINSTALL}" -eq 1 ]`, `:98` — NOT the auto-start path; the earlier `:110-117` citation was wrong.)*
- `config/overlays/mac-mini.toml:1-2` (`enabled = false`), `config/overlays/mac-mini-nwp.toml:7-8` (`enabled = true`)
- `docker-compose.macmini.yml:24,32` / `docker-compose.macmini-nwp.yml:11,17` (`SAPPHIRE_CONFIG_OVERLAY`)
- `docs/deployment/mac-mini-staging.md:304-336` (operator doc — documents the two-file NWP toggle A2 deletes)
- `src/sapphire_flow/flows/run_forecast_cycle.py:141-151` (`_load_weather_forecast_adapter_config` — NWP off = `enabled=False` when `SAPPHIRE_CONFIG` unset), `:169` (`enabled` absent → `weather_forecast.get("enabled", False)`), `:100-106` (`_WeatherForecastAdapterConfig` field list), `:958` (per-station loop), `:757-760` (batch-fetched `model_assignments[sid]`), `:1082-1086` (PRIMARY `fc_result is None` → `log.warning("forecast_cycle.all_models_failed")`), `:1137-1141` (combination `primary_model_id is None`), `:1207-1210` (`all_ensembles[sid]` built from **`multi_result.results`** — ALL models incl. fallback, NOT `combinable_results`), `:1439-1460` (Phase C passes `all_ensembles` into `check_station_alerts`), `:89-97` (`ForecastCycleResult.stations_failed`/`errors`), `:549` (`run_forecast_cycle_flow` entry)
- `src/sapphire_flow/services/run_station_forecast.py:301-341` (`primary_model_id=None` iff every assigned model failed → no row), `_run_single_model:148` (`fetch_active_artifact_for_station`), `:174-208` (predict try/except backstop), `MultiModelForecastResult.combinable_results` (`priority < FALLBACK_PRIORITY_THRESHOLD`)
- `src/sapphire_flow/services/onboarding.py:472-505` (Step 6 assigns **every** discovered STATION model unconditionally), `:637-643` (Step 7 swallows per-model training failure), `:662-673` (Step 8 marks OPERATIONAL on **any** active artifact), `flows/onboard.py:208-211` (never fails on non-empty `errors`)
- `src/sapphire_flow/services/model_onboarding.py:838-867` (`create_station_assignment` — required `priority: int`, **no default**), `:977` (`onboard_model`), `:994` (`onboard_model`'s `assignment_priority: int = 0` default — dead in the pipeline path; its only caller `onboarding.py:613` overrides it, Plan 089)
- **`src/sapphire_flow/flows/onboard_model.py:470`** (`onboard_model_flow`'s `assignment_priority: int = 0` — **the LIVE drift source**), `:438-455` (`_create_assignment_task` → `create_station_assignment`/`create_group_assignment`), `:888` (unmodified pass-through), `cli/register_deployments.py:93-94` (`onboard-model` deployment registration)
- `src/sapphire_flow/services/onboarding.py:483-495` (Step 6 overrides priority via `priority_for_model`), `:604-612` (Step 7 same, Plan-089 comment), `:479-480`,`:536-537` (Steps 6/7 both `continue`-skip `ArtifactScope.GROUP` — so `onboard_model_flow` is the ONLY writer of `group_model_assignments`)
- `src/sapphire_flow/services/model_registry.py:45-46` (`discover_models` — `log.exception("model_discovery_failed", model_id=…)`, ERROR+traceback per failed entry point — NOT a silent except)
- `src/sapphire_flow/services/alert_strategy.py:197-232` (`PooledEnsembleStrategy.evaluate` — pools **all** members, **ignores `priorities`**), `alert_checker.py:201-250` (`_resolve_strategy_and_filter` → Pooled for POOLED/BMA/CONSENSUS w/ homogeneous MEMBERS)
- `docs/architecture-context.md:132` (STALE fallback priorities — `climatology 90`/`persistence 99`, OPPOSITE of `conventions.md:441-442` + `config.toml`; pre-existing Plan-089 drift, corrected by M0)
- `src/sapphire_flow/config/deployment.py:250-256` (`priority_for_model` → config value or `DEFAULT_PRIORITY=50`), `:341-346` (`load_config` pops `adapters`/`monitoring`)
- `config.toml:59-64` (`[model_priorities]`: `nwp_regression=10`, `nwp_rainfall_runoff=20`, `linear_regression_daily=30`, `persistence_fallback=90`, `climatology_fallback=100`); `config.toml:397` (`monitoring.expected_delivery_offset_hours` — dead TOML); `FALLBACK_PRIORITY_THRESHOLD=90` (`types/ids.py:20`)
- **Schema-level priority DEFAULT (drift source, M0):** `alembic/versions/0001_v0_schema.py:405` (`model_assignments.priority` `server_default="0"`) and `alembic/versions/0021_add_group_model_assignments.py:35` (`group_model_assignments.priority` `server_default="0"`) — a raw INSERT that omits `priority` lands at `0` (skill-tier), the same drift the incident showed. The write-time guard (Decision 2 / M0b) is the app-layer barrier; the DB default is noted in M0 for whether it also needs changing.
- `src/sapphire_flow/models/climatology_fallback.py` (needs only its trained artifact — zero runtime obs → the only guaranteed floor), `models/persistence_fallback.py` (`past_targets[param][-1]` → `IndexError` on empty)
- `src/sapphire_flow/services/alert_strategy.py:146-194` (`PrimaryModelStrategy.evaluate` — fallback-blind), `alert_checker.py:269-330` (`_process_results` discards `ExceedanceResult`, builds persisted `Alert`), `types/alert.py:19-35` (`Alert`, no fallback field); **v0 has NO alert delivery** — `docs/v0-scope.md` §A8: "Alerts logged to alerts table. Visible via API. No notification dispatch."
- `src/sapphire_flow/api/routes/forecasts.py` + `api/templates/forecasts/{list,detail}.html` + `api/routes/api_forecasts.py` (dashboard/JSON render `model_id`; no fallback badge)
**Created**: 2026-07-06

---

## IMPLEMENTATION VISION (decided spec — feeds re-`plan-review` + WF2 vision-build)

Five milestones. **M0 is a prerequisite** the live DB check forced. The incident
root cause was that fallback-tier membership was inferred from the mutable DB
`assignment.priority` (a `priority ≥ 90` check), and real rows carry drifted
priorities (`0`/`-10`) so the floor was silently treated as a skill model. M0
attacks this on **two** fronts: (a) **M0c makes the tier categorical** — a
git-versioned `FALLBACK_MODEL_IDS` constant, used identically by forecast
combination, the B3 badge, and M3 alert-suppression, so no DB-priority value can
move a model into or out of the fallback tier ever again; and (b) **M0a/M0b repair
and guard the priority *ordering*** so the reconciled rows are correct and a
below-tier fallback can no longer be written. M1+M2 are the incident fix; M3 stops
operators (and the alert path) trusting a fallback; M4 is a minimal staleness +
drift tripwire.

### M0 — Reconcile assignment priorities to the config chain (PREREQUISITE)

Live-DB finding (2026-07-06, dev stack — the 2 skill-comparison stations 2009 &
2091): both have **all 5 models assigned + all 5 artifacts active** (incl. a live
`climatology_fallback` artifact), but `model_assignments.priority` is
`climatology_fallback=0`, `persistence_fallback=0`, `nwp_rainfall_runoff=-10`,
others `0` — **not** `config.toml`'s chain (100/90/…).

**Root cause of the drift — CORRECTED (2026-07-06, code re-read; the earlier
`create_station_assignment:994` citation was wrong):** `create_station_assignment`
(`model_onboarding.py:838-867`) takes a **required** `priority: int` with **no
default**. The bare `assignment_priority: int = 0` default lives on TWO higher-level
callers:
  1. `onboard_model()` (`model_onboarding.py:977`, default at `:994`) — but its
     **only** production caller, `onboarding.py:613`, already **always overrides**
     it with `deployment_config.priority_for_model(...)` (`onboarding.py:604-612`,
     explicit Plan-089 comment "so the assignment written on artifact promotion
     does not regress to the default 0"). So this default is **dead** in the
     `onboard-stations` pipeline path — NOT the live bug.
  2. **`onboard_model_flow` (`flows/onboard_model.py:470`, `assignment_priority:
     int = 0`)** — threaded UNMODIFIED into `_create_assignment_task`
     (`:888`) → `create_station_assignment`/`create_group_assignment` (`:438-455`)
     with **no** reference to `priority_for_model` anywhere in the flow body. This
     is the live drift vector: `onboard_model_flow` is independently registered as
     the `onboard-model` Prefect deployment (`cli/register_deployments.py:93-94`)
     and is exactly the per-station manual entry point Plan 091 Phase 2a's runbook
     uses (`prefect deployment run onboard-model/onboard-model -p model_id=…`),
     consistent with the manual `nwp_rainfall_runoff=-10` override also present on
     these rows. It is ALSO the **only** code path that writes
     `group_model_assignments` rows at all — `onboarding.py` Steps 6/7 both
     `continue`-skip `ArtifactScope.GROUP` models (`onboarding.py:479-480`,
     `:536-537`).

Consequence (pre-fix): because `combinable_results` (`services/run_station_forecast.py:71-76`)
and the intended B3/M3 tier checks all inferred the tier from `assignment.priority`,
the drifted `climatology_fallback=0` row was treated as a **combinable *skill*
model** — blended into the pooled forecast and eligible to drive an alert. **M0c
removes this failure mode structurally:** the tier is now `model_id in
FALLBACK_MODEL_IDS`, so combination, the B3 badge, and M3 alert-suppression all
classify climatology as a fallback regardless of its (now merely ordering-only) DB
`priority`. M0a/M0b still reconcile the priority column so the ordering is correct
and no below-tier fallback row can be written.

- **M0a — repair existing rows (precedence rule, aligned with the Plan-089 model
  in `docs/conventions.md:424-449`):** a named idempotent admin action rewrites
  **BOTH** `model_assignments.priority` **and** `group_model_assignments.priority`,
  applying this precedence crisply (the earlier "rewrite everything to
  `priority_for_model` *and also* preserve overrides" wording was internally
  contradictory and is replaced):
  1. **Every `FALLBACK_MODEL_IDS` member is UNCONDITIONALLY rewritten to
     `priority_for_model(model_id)`** — `climatology_fallback→100`,
     `persistence_fallback→90` — so the floor always lands in the `≥90` tier. A
     fallback row is never left below threshold, regardless of any prior manual
     edit (the whole point of M0 is that a fallback below `90` is the bug).
  2. **A skill model is UNCONDITIONALLY rewritten to `priority_for_model(model_id)`
     (`nwp_regression→10`, `nwp_rainfall_runoff→20`, `linear_regression_daily→30`)
     EXCEPT for an explicit, hard-coded allowlist of `(station_id, model_id)` pairs
     known to carry an intentional experiment override** — corrected after the
     2026-07-06 review, which showed the earlier "infer deliberate from the value
     pattern" rule was unsound. The prior rule preserved a skill priority whenever it
     was "(a) below `FALLBACK_PRIORITY_THRESHOLD` **and** (b) differs from the config
     value". But (a) is true of **every** skill priority by design (config values are
     10/20/30, all `< 90`) and (b) is true of **any** drifted value by definition — so
     that rule would classify the incident's own drift signature as "deliberate" and
     preserve it. The live-DB finding above (line 88: nwp_regression and
     linear_regression_daily also stuck at `0` at 2009/2091, not only the documented
     `nwp_rainfall_runoff=-10` override) is exactly this failure: under the old rule
     both `0`-valued skill rows satisfy (a)+(b) identically to the `-10` row and would
     be left at `0` forever, leaving `sorted(assignments, key=lambda a: a.priority)`
     (`run_forecast_cycle.py:970`, `run_station_forecast.py:301`) with a
     non-deterministic tie between `nwp_regression` and `linear_regression_daily` for
     both primary-model selection **and** `time_step` selection — the very ordering
     fragility M0a's `time_step` audit (below) worries about, on the audited stations.
     **Fix:** do NOT infer "deliberate" from the value (indistinguishable from the
     bug's own signature). Reconcile from a **named, explicit allowlist** of the
     specific `(station_id, model_id)` pairs that carry an intentional override — for
     this repair, exactly `nwp_rainfall_runoff` on stations `2009` and `2091`
     (`config.toml:59-64` skill-comparison knob). Every skill row NOT on that
     allowlist is rewritten to `priority_for_model(model_id)` — the same unconditional
     treatment rule 1 gives fallbacks. Allowlisted rows keep their stored value.
  Net: `FALLBACK_MODEL_IDS` membership is the categorical fact (Decision 2 /
  Plan-review round 3); `priority_for_model` sets the default ordering integer for
  every non-allowlisted row; only an **explicitly allowlisted** skill override is
  respected. Acceptance check 1 asserts exactly this (fallbacks at `100`/`90`; the
  allowlisted `nwp_rainfall_runoff=-10` row survives; the previously-drifted-to-`0`
  `nwp_regression`/`linear_regression_daily` rows converge to config `10`/`30`).
  - **M0a pre-rewrite fleet AUDIT — derive the allowlist from the REAL target DB,
    not the 2-station dev sample (folded from the 2026-07-06 round-4 review):** the
    allowlist above (`nwp_rainfall_runoff` on `2009`/`2091`) is derived from the
    **2-station dev stack**. The real production / mac-mini fleet could carry other
    legitimate skill-priority overrides that were never seen on dev, and a blanket
    "rewrite every non-allowlisted skill row to `priority_for_model`" would silently
    clobber them — the mirror-image of the bug this plan fixes. **Before** the blanket
    rewrite runs, M0a MUST first run a **read-only fleet audit** (mirroring B1b's
    fleet-wide floor audit): list every skill-model assignment row across the REAL
    target DB (`model_assignments` **and** `group_model_assignments`) whose stored
    `priority` differs from `priority_for_model(model_id)`, and **require each
    divergence to be explicitly triaged** — either added to the `(station_id, model_id)`
    override allowlist (a confirmed intentional experiment override) or confirmed as
    drift to be reconciled — **before** any row is rewritten. The dev-derived allowlist
    (`nwp_rainfall_runoff` on `2009`/`2091`) is the *starting* allowlist; the audit is
    what makes it complete for whichever DB M0a actually targets. Acceptance check 1 is
    extended to assert the audit ran and that every skill-row divergence was triaged
    (allowlisted or reconciled), none silently clobbered.
  - **M0a side effect — `time_step` selection can flip (folded from the 2026-07-06
    review; must be audited before the repair runs):** the per-station input-assembly
    cadence is keyed on the **first priority-sorted assignment's** `time_step`
    (`run_forecast_cycle.py:970-971`: `sorted_assignments = sorted(assignments,
    key=lambda a: a.priority); time_step = sorted_assignments[0].time_step`), and
    `time_step` is an **independent per-`ModelAssignment` field**
    (`types/station.py:52-61`) — the team already fixed the analogous starvation bug
    for feature *requirements* by taking a superset union
    (`run_forecast_cycle.py:985-989`) but left `time_step` on `sorted_assignments[0]`
    alone. M0a rewrites `priority`, so for any station whose assigned models carry
    **heterogeneous** `time_step`, M0a silently changes which model's `time_step`
    wins for the whole cycle. Concrete risk at the incident stations: 2091 has
    `nwp_rainfall_runoff` (currently priority `-10` → ranked first) and
    `nwp_regression` (config priority `10`); if their `time_step` differ (e.g. 1h vs
    24h — plausible for sub-daily v0b skill-comparison stations onboarded via
    `onboard_model_flow`), M0a flips the first-ranked model and the cadence flips
    with it, feeding a model daily-windowed data it was never validated against.
    **Required M0a step:** BEFORE running the repair, audit whether every
    station/group with multiple assignments has a **homogeneous** `time_step` across
    its assigned models. If homogeneous, the reorder is a `time_step` no-op and M0a
    is safe as-is (record the audit result). If any station is heterogeneous, do NOT
    silently let M0a flip it — either derive a single consistent `time_step` for the
    station (analogous to the superset-requirements fix) or escalate that station as
    a design decision rather than clobbering it. Acceptance check 1 is extended to
    assert this audit ran and that no station's effective `time_step` changed as a
    side effect of M0a.
  - **M0a WRITE-PATH requirement — priority-only, must not clobber `time_step`/`status`
    (folded from the 2026-07-06 review):** the only assignment writers in the codebase
    are the full-row upsert helpers `create_station_assignment` /
    `create_group_assignment` (`model_onboarding.py:838-895`) →
    `store_model_assignment` (`store/station_store.py:193-211`) /
    `store_group_model_assignment` (`store/station_group_store.py:132-150`), whose
    `INSERT … ON CONFLICT DO UPDATE` **SET clause also overwrites `time_step` and
    `status` on every call** — there is no priority-only writer today. Two consequences
    M0a MUST honour: (1) `create_station_assignment`'s `time_step: timedelta` parameter
    has **no default** (`model_onboarding.py:838-867`), so a script built on the
    upserts must supply a `time_step` for every row it rewrites; (2) it must **NOT
    re-derive** `time_step` from `model.data_requirements.supported_time_steps` (the
    pattern at `onboarding.py:482,592`, i.e. `next(iter(...supported_time_steps))`),
    because `onboard_model_flow` writes an arbitrary operator-supplied `time_step_hours`
    (default 24, `flows/onboard_model.py:469,577`) that is NOT re-derivable from the
    model — re-deriving would silently clobber legitimately-different stored cadences,
    reintroducing the exact corruption the plan is preventing. **Required:** M0a either
    (a) reads back each existing row's current `time_step` **and** `status` and passes
    them through byte-for-byte unchanged when re-invoking the upsert to rewrite only
    `priority`, or — simpler and safer — (b) **bypasses the upsert helpers entirely and
    issues a priority-only `UPDATE`** (a new thin store method, `model_assignments` /
    `group_model_assignments` `SET priority=… WHERE …`) so the write path structurally
    cannot touch `time_step`/`status`. (b) is preferred. **Guard note (folded round 4):
    a priority-only `UPDATE` is a NEW write path that bypasses the M0b write-time guard
    in `create_station_assignment`/`create_group_assignment`.** To keep the invariant
    "no code path can persist a fallback below tier", scope this method as
    **private/one-off to the M0a repair script ONLY**, OR have it assert the same
    `model_id in FALLBACK_MODEL_IDS and priority < FALLBACK_PRIORITY_THRESHOLD → raise`
    check before issuing the UPDATE. **Doc-sync:** if option (b) is taken, add the new
    method signature to both Store Protocols in `docs/spec/types-and-protocols.md` at
    land time. Acceptance check 1 is extended to assert **every individual row's**
    `time_step` (not just the post-sort *effective* one) is bit-for-bit identical
    before/after M0a — the effective-only check would miss corruption of a
    non-first-ranked row.
- **M0b — fix the creation paths:** the config-driven `priority_for_model` value
  must reach **every** assignment write. The two pipeline call sites
  (`onboarding.py` Steps 6/7) already do this; the live gap is **`onboard_model_flow`
  (`flows/onboard_model.py:470`)** — either (a) drop the bare `assignment_priority:
  int = 0` default and resolve `deployment_config.priority_for_model(model_id)`
  inside the flow when the caller omits it (mirroring `onboarding.py:608-612`), or
  (b) require callers to always pass an explicit config-sourced priority (fail loud
  if omitted). `onboard_model()`'s `:994` default may stay (its only caller
  overrides it) but making it explicit there too is cheap belt-and-suspenders.
  - **M0b write-time guard (STRUCTURAL BARRIER — Decision 2):** patching the
    `onboard_model_flow:470` default fixes today's known drift vector, but the class
    of bug (a `FALLBACK_MODEL_IDS` member written below the `≥90` tier) recurs any
    time a caller supplies a below-tier priority, and the DB columns even carry a
    `server_default="0"` that a raw INSERT omitting `priority` inherits
    (`alembic/versions/0001_v0_schema.py:405`, `0021_add_group_model_assignments.py:35`).
    Rather than rely only on C1c's post-hoc tripwire, add a **write-time guard at the
    single assignment-write choke point**: in `create_station_assignment` /
    `create_group_assignment` (`services/model_onboarding.py:838-895`) — through which
    both `onboarding.py` Steps 6/7 and `onboard_model_flow` (`flows/onboard_model.py:470`
    → `_create_assignment_task` `:438-455`) funnel — **raise (do NOT silently write
    the row) if `model_id in FALLBACK_MODEL_IDS and priority < FALLBACK_PRIORITY_THRESHOLD`**.
    A fallback can no longer be persisted below its tier by any code path. (The
    `onboard_model_flow:470` default fix from above still stands — it prevents the
    guard from *firing* on the normal path; the guard is the backstop for everything
    else.) The **DB `server_default="0"`** is retained but is now belt-and-suspenders
    only for the app-layer path — a purely raw SQL INSERT bypassing the service layer
    still cannot be guarded at the app layer, so M0 notes it as a residual DB-level
    consideration (tightening the DEFAULT is out of scope; C1c catches any such row).
    Acceptance check asserts a below-tier fallback write raises.
- **M0c — `FALLBACK_MODEL_IDS` is the SINGLE categorical source of truth for the
  fallback/skill-tier boolean — DECIDED (plan-review round 3, Decision 2):**
  Introduce a `FALLBACK_MODEL_IDS: frozenset[ModelId] = frozenset({
  ModelId("climatology_fallback"), ModelId("persistence_fallback")})` constant next to
  `FALLBACK_PRIORITY_THRESHOLD` (`types/ids.py:20`) — wrapped through `ModelId`
  (`= NewType("ModelId", str)`, `types/ids.py:16`) to match the existing sentinel
  pattern (`POOLED_MODEL_ID`/`BMA_MODEL_ID`/`CONSENSUS_MODEL_ID`, `:17-19`) and every
  `ModelId`-typed membership call site, NOT bare `frozenset[str]`. The *categorical* fact "is this a
  fallback model" is `model_id in FALLBACK_MODEL_IDS`, and **the identical boolean is
  used in all three places that previously computed the tier two different ways**:
  1. **Forecast combination** — `MultiModelForecastResult.combinable_results`
     (`services/run_station_forecast.py:71-76`) today filters on
     `self.priorities.get(mid, 0) < FALLBACK_PRIORITY_THRESHOLD`, i.e. the **mutable
     DB `assignment.priority`**. This is exactly why a drifted fallback (priority `0`)
     was blended into the pooled forecast as a skill member. **Replace that check
     with `model_id not in FALLBACK_MODEL_IDS`** — combination now admits a model iff
     it is not categorically a fallback, independent of any drifted DB priority.
     **Vestigial-field note (folded round 4):** once `combinable_results` no longer reads
     `self.priorities`, the `MultiModelForecastResult.priorities` field
     (`run_station_forecast.py:66`, populated `:304,:309`, passed `:338`) is no longer
     consulted for tier classification. Landing this change should either **remove the
     `priorities` field + its threading** or **explicitly re-comment it as
     ordering/tie-break-only** (it may still legitimately feed `min(priority)`
     primary-selection ordering) — do not leave a dead field that reads as the old tier
     source.
  2. **B3 `ModelTier` badge** — `ModelTier.FALLBACK if model_id in FALLBACK_MODEL_IDS
     else ModelTier.SKILL` (a derived enum, not a raw `is_fallback: bool` — MAJOR-6).
  3. **M3 alert-suppression gate** — `model_id in FALLBACK_MODEL_IDS`.
  - **DB `assignment.priority` is DEMOTED to an ordering / tie-break key AMONG
    admitted skill models only** (the `min(priority)` primary-selection dispatch and
    combination ordering). It no longer decides *tier membership* anywhere — so a
    deliberate skill-tier override (`nwp_rainfall_runoff=-10`) still legitimately
    reorders which skill model wins primary, but no DB-priority value can ever move a
    model into or out of the fallback tier. This **dissolves the entire dual-source
    divergence** the earlier draft carried (the "trade-off accepted" paragraph is
    removed — there is no longer a runtime-vs-safety split to trade off; combination,
    badge, and suppression all read the one categorical set).
  - **Why this is drift-immune:** the whole incident was a mutable, siloed DB column
    silently drifting from the intended tier with nothing noticing. Keying the tier on
    a git-versioned `frozenset` constant makes B3/M3 **and** combination immune to the
    entire class of DB-priority drift: a manual DB edit or any onboarding-path bug
    that mis-sets `priority` cannot un-badge a fallback, cannot re-admit it into a
    pooled alert, and cannot blend it into a skill forecast.
  - **M0c config-load validator (retained — closes the config-side hazard;
    scoped to PRESENT keys, corrected 2026-07-06 round 4):** the numeric
    `[model_priorities]` map (read via `deployment.py:250-256 priority_for_model`,
    default `DEFAULT_PRIORITY=50`) still drives *ordering*, so a `config.toml` edit that
    priced a fallback below `90` (copy-paste, merge conflict, an experiment tweak
    mirroring `nwp_rainfall_runoff = -10`) would give a fallback a nonsensical
    skill-tier ordering integer. Add a **config-load-time validator** (a
    `model_validator` on the deployment-config model, alongside the existing
    `@model_validator(mode="after")` chain at `deployment.py:173-248`). **The validator
    MUST enforce only on fallback ids EXPLICITLY PRESENT in `self.model_priorities`,
    NOT via `priority_for_model(...)`.** The earlier "every `model_id in
    FALLBACK_MODEL_IDS` has `priority_for_model(...) >= FALLBACK_PRIORITY_THRESHOLD`"
    wording over-fires: `priority_for_model` returns `DEFAULT_PRIORITY=50`
    (`deployment.py:29,256`) for any model absent from `[model_priorities]`, and
    `50 < 90` — so ANY `DeploymentConfig` that does not explicitly list BOTH fallbacks
    in `[model_priorities]` would raise at construction. That would break the shared
    test helper `make_deployment_config()` (`tests/conftest.py:288-293`, which passes
    only `max_retention_days`) used across the suite, plus every real config that omits
    the fallbacks. **Correct form:**
    `for mid in FALLBACK_MODEL_IDS: v = self.model_priorities.get(mid); if v is not
    None and v < FALLBACK_PRIORITY_THRESHOLD: raise ConfigurationError(...)` — an
    **absent** entry is safe because the categorical `FALLBACK_MODEL_IDS` tier already
    governs classification (M0c Decision 2), and an absent fallback simply falls back
    to the `DEFAULT_PRIORITY=50` ordering integer, which never crosses the `>=90` tier
    but also never mis-*classifies* the model (classification is categorical, not
    numeric). Only an **explicitly present, below-threshold** fallback priority is a
    genuine self-contradictory config and must fail loud. This keeps the ordering
    integer consistent with the categorical set without regressing well-formed configs.
    Acceptance check 11 covers both cases: a config that prices a present
    `climatology_fallback = 5` raises; **a well-formed config that OMITS the fallback
    ids from `[model_priorities]` still loads cleanly**. (Test-migration note: if any
    test wants the fallbacks explicitly priced, add them to the config under test — but
    the default `make_deployment_config()` helper must continue to load without listing
    them.)
  - **Belt-and-suspenders (folded into M4/C1c):** M4's tripwire still flags any
    `model_assignments`/`group_model_assignments.priority` for a `FALLBACK_MODEL_IDS`
    member that has drifted below `FALLBACK_PRIORITY_THRESHOLD`, so a recurrence of the
    root cause is *visible* even though (post-Decision-2) it can no longer misclassify
    the tier. (M0c introduces a small **`ModelTier` enum** (`SKILL`/`FALLBACK`, see
    Decision 2 / MAJOR-6 below) *derived* from the `FALLBACK_MODEL_IDS` frozenset — this
    is the value exposed on the badge/JSON schema, replacing a raw `is_fallback: bool`.
    A fuller **registry-declared per-model tier** — each model declaring its own tier at
    registration rather than membership in a central frozenset — is the cleaner
    long-term shape but is out of scope here; the `frozenset` + derived-enum pairing is
    the minimal decoupling.)

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
  preflight.
  - **Where it is parsed + wired (specified after the 2026-07-06 review — do NOT
    leave WF2 to invent an ad-hoc `os.environ.get`):** per the repo's
    parse-don't-validate convention, `require_nwp` is parsed **once at the config
    boundary** as a typed field, not read inline in flow logic. Add a `require_nwp:
    bool = False` field to `_WeatherForecastAdapterConfig` (field list at
    `run_forecast_cycle.py:100-106`) and populate it in
    `_load_weather_forecast_adapter_config` (`:141-169`) from `SAPPHIRE_REQUIRE_NWP`.
    **`SAPPHIRE_REQUIRE_NWP` MUST be read from `os.environ` unconditionally at the TOP
    of `_load_weather_forecast_adapter_config`, before the `config_path is None` branch
    (`:142`), and threaded into BOTH constructions** — the early-return
    `_WeatherForecastAdapterConfig` at `:143-151` AND the main-path one. Do **NOT**
    "mirror" the `enabled` field's read location (corrected after the 2026-07-06
    review): `enabled` is hardcoded `False` in the early-return branch (`:145`) and is
    env-derived only later at `:169`, in the branch taken when `SAPPHIRE_CONFIG` **is**
    set. But the early-return branch is taken **exactly** when `SAPPHIRE_CONFIG` is
    unset — the precise unset-config scenario the single general gate must catch — so
    literally mirroring `enabled` would leave `require_nwp` at its unread default `False`
    there, and the `ConfigurationError` would never fire even with
    `SAPPHIRE_REQUIRE_NWP=1`, silently defeating the gate in exactly the unset-config
    case it exists for. Populate `require_nwp` in **both** dataclass constructions; the
    general gate (above) then reads `weather_forecast_config.require_nwp` (the same
    `_WeatherForecastAdapterConfig` object returned by the loader, in scope at
    `run_forecast_cycle.py:642`), not the raw env var. **Deployment wiring:** the mini's `docker-compose.macmini.yml` (the sole
    remaining mini compose file after A2's fold) sets `SAPPHIRE_REQUIRE_NWP=1` in
    the `environment:` block of the **`prefect-worker` service** — the `forecast-cycle`
    deployment runs on the `default` pool → `prefect-worker` only
    (`docker-compose.macmini.yml:24`; `cli/register_deployments.py`;
    `docs/deployment/mac-mini-staging.md:307-309` confirms the mini config is wired for
    `prefect-worker` only). It is **NOT** set on `prefect-worker-ingest`
    (`docker-compose.macmini.yml:32`), a *different* service (the ingest pool, Plan
    098) that never runs the forecast cycle. Acceptance check 4 asserts the env var is
    on the `prefect-worker` service specifically, so the guard is actually enabled on
    the real mini and not merely exercisable by hand-setting the env var in a test.
  - **Global gate (ONE general check — corrected 2026-07-06 round 4):** the earlier
    "two enumerated hard-refuse conditions ((a) adapter unconstructable, (b)
    `SAPPHIRE_CONFIG` unset)" **missed the historical incident's actual mechanism**.
    Verified against `run_forecast_cycle.py:625-682`,
    `_load_weather_forecast_adapter_config` feeds THREE return paths:
    1. `SAPPHIRE_CONFIG` unset → hardcoded `enabled=False` (`:143-151`) — the earlier
       condition (b).
    2. `enabled=True` but STAC fields missing → **already raises `ConfigurationError`
       UNCONDITIONALLY today** (`:648-656`, pre-existing, NOT flag-gated) — the earlier
       condition (a) is already covered by existing code and needs no new gate.
    3. **The merged TOML explicitly sets `enabled=false`** (mac-mini overlay,
       `config/overlays/mac-mini.toml`, `enabled` absent/`false` → `:642`
       `nwp_enabled = weather_forecast_config.enabled` is `False`) → neither an
       early-return nor a construction failure. **Case (3) IS the historical incident**
       (`SAPPHIRE_CONFIG` was set; the mac-mini overlay set `enabled=false`), and it
       fell through the two enumerated conditions → nothing raised, nothing logged,
       silent runoff-only.
    **Fix — a single general check that subsumes all three:** right after
    `nwp_enabled = weather_forecast_config.enabled` (`run_forecast_cycle.py:642`),
    `if adapter_config.require_nwp and not weather_forecast_config.enabled: raise
    ConfigurationError(...)`. This one check catches the unset-config case (1, where
    `enabled` is the hardcoded `False`), the explicit-`enabled=false` incident case (3),
    AND leaves the pre-existing unconditional STAC-missing raise (case 2) as-is. It
    reads the typed `require_nwp` field (parsed at the config boundary, below) — never a
    raw inline `os.environ.get`. **(Defense-in-depth note:** post-A2 the mini's overlay
    is `enabled=true`, so on the fixed mini this gate never fires — it is the structural
    backstop against any future re-introduction of `enabled=false`, exactly the class of
    change that caused the incident.) Acceptance check 4 covers case (1) and case (3);
    the former "`SAPPHIRE_CONFIG`-unset" residual is subsumed by the same single check.
  - **Post-hoc dark detection:** **promote the existing zero-forecast branches** —
    `fc_result is None` (`run_forecast_cycle.py:1082-1086`) and
    `primary_model_id is None` (`:1137-1141`) — from `log.warning` to **`log.error`
    + `errors.append(...)`**, emitting a **dotted** structlog event
    **`forecast_cycle.station_dark`** (per `docs/standards/logging.md`'s
    `{entity}.{action}` pattern — NOT a colon-separated free-text string). This
    already fires exactly when a station produces **zero** forecasts this cycle, for
    **any** reason (NWP off + inert floor, empty obs, a model bug) — no
    climatology-specific preflight, no new artifact-store query, and it cannot
    over-skip a station a healthy `persistence_fallback` would have served.
  - **Logging-standard reconciliation (folded after review):** `logging.md`'s Log
    levels section currently lists "Station skipped due to missing data (flow
    continues with remaining stations)" as a **WARNING** example. Promoting
    *zero-forecasts-for-a-station-this-cycle* to ERROR is a deliberate carve-out:
    it is a stricter class than "one model of several missing data" — it means the
    station produced **nothing** and (post-M2) even the guaranteed floor failed,
    which is human-attention-worthy. **This code change therefore also amends
    `docs/standards/logging.md`** to distinguish "zero forecasts produced for a
    station this cycle" (ERROR) from the general "station skipped due to missing
    data" (WARNING), keeping the standard and code in sync (CLAUDE.md: every code
    change updates affected docs). (The earlier "mirrors
    `station_skipped_model_not_loaded`" phrasing is dropped — that is a distinct
    config/load-failure class, not the reference for this level choice.)
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
  - **Step 8 floor-gate — a deliberate, owner-ratified (2026-07-06) amendment to the
    LOCKED `docs/v0-scope.md §A4` operational-mark rule:** the locked v0 rule
    (`docs/v0-scope.md §A4` step 8: "Marks stations operational after ≥1 model
    artifact") is **tightened** — from "≥1 model artifact" to **"≥1 skill artifact
    AND an active `climatology_fallback` floor artifact"**. This is **not a silent
    override**: the old "any active artifact" rule is *exactly* what let a floor-less
    station go live and dark (the incident), so the owner has **ratified** the
    tightening (2026-07-06). Concretely: require `climatology_fallback` (the floor) to
    have an active artifact — not "any discovered model" (`:662-673`) — before a
    non-weather station is marked OPERATIONAL. No floor artifact → station stays **NOT
    operational** + loud ERROR. The amendment is applied to `docs/v0-scope.md §A4` at
    land time (per CLAUDE.md "every code change updates affected docs" — see Process +
    acceptance check).
  - **Step 7 un-swallow:** a *floor*-model training failure must not silently
    `continue` (`:637-643`); it fails the run or leaves the station NOT
    operational, and `flows/onboard.py:208-211` surfaces it as a **non-green**
    outcome (skill-model failures may still be tolerated).
  - **Floor absence is escalated (retargeted after review):** `discover_models`
    already logs a per-entry-point failure at ERROR with traceback + `model_id`
    (`model_registry.py:45-46`, `log.exception("model_discovery_failed", …)`), so
    the *load* path is not silent today — the earlier "silent `except`" claim was
    wrong. The real gap is downstream: **nothing checks whether `climatology_fallback`
    is present in the `discovered` dict** and escalates its *absence* as a capacity
    outage. Add that check at the Step-8 floor-gate — floor model missing from
    `discovered` → loud ERROR + station stays NOT operational (same outcome as a
    missing floor artifact).
  - **Fleet-wide floor audit (one-time, folded from review):** the same latent
    defects (Step 7 swallowing floor-training failure, Step 8 marking OPERATIONAL
    on any active artifact) could already have produced OPERATIONAL stations across
    the ~1000-station fleet with no active `climatology_fallback` artifact — not
    just 2009/2091. As part of M2 rollout, run a one-time audit query (all
    OPERATIONAL non-weather stations lacking an active `climatology_fallback`
    artifact) and extend the backfill to every station it returns; acceptance
    check 5 asserts the audit returns zero post-fix.
  - **Test-migration budget:** two breakages, both scoped work (not free):
    1. onboarding unit tests that mock `discover_models` with a non-floor model set
       and assert OPERATIONAL (`tests/unit/services/test_onboarding.py:510-578`) **will
       break** under the B1b gate — update them to include a working climatology fake
       or assert the new NOT-operational outcome.
    2. **`tests/unit/services/test_run_station_forecast.py:617-640`
       (`test_combinable_results_excludes_high_priority_fallbacks`) breaks under M0c**
       (folded from the 2026-07-06 review): it seeds `model-a`/`model-b`/`model-c`
       (`:40-41,:474`) at priorities `1`/`50`/`90` and asserts `model-c` is excluded
       from `combinable_results` **purely because `priority=90` crosses the old numeric
       threshold**. None of `model-a/b/c` are members of `FALLBACK_MODEL_IDS`, so under
       M0c's `model_id not in FALLBACK_MODEL_IDS` filter `model-c` is **no longer
       excluded** and `assert _MODEL_ID_C not in combinable` fails. Update it to the
       new membership semantics: assert a synthetic high-priority-but-non-fallback
       `model_id` is **NOT** excluded post-fix, while an actual
       `climatology_fallback`/`persistence_fallback` id **IS** excluded regardless of
       its priority value.
  - **Backfill 2009/2091** (named idempotent admin action): create assignments
    **and train + promote a `climatology_fallback` artifact** (an assignment
    without an active artifact is inert → still dark). Re-run = no-op (guard
    artifact promotion; `create_station_assignment` already upserts, Plan 089).
- **B2 (persistence empty-obs guard) — RAISES a narrow exception, NOT "return
  `ModelFailure`" (corrected 2026-07-06 round 4):** `climatology_fallback` produces
  from its artifact alone (floor holds). Add an explicit empty/short-obs guard to
  `persistence_fallback.predict` so an *anticipated* no-obs case degrades cleanly.
  **Mechanism note (native protocol, not FI):** `ModelFailure` is an **FI-contract
  type** (`adapters/forecast_interface.py:22`, from the external `forecast_interface`
  package). The native `StationForecastModel.predict` Protocol
  (`protocols/forecast_model.py:32-39`) fixes the return type to
  `tuple[dict[str, ForecastEnsemble], bytes | None]` — there is **no failure-sentinel
  union member**, so a native model *cannot* "return `ModelFailure`". Instead, the
  empty/short-obs guard **raises a specific, narrow exception** (e.g. a dedicated
  `InsufficientObservationsError`, not the current bare `IndexError` on
  `past_targets[param][-1]`), which the existing `_run_single_model` try/except
  backstop (`run_station_forecast.py:174-208`) converts into a graceful
  `"predict failed: {exc}"` reason → the model is recorded failed and the first-success
  chain advances to `climatology_fallback`. Using a *named* exception (rather than
  leaning on the incidental `IndexError`) makes the anticipated case explicit and
  distinguishable from an unanticipated bug — while still respecting the native return
  contract. **This is distinct from a real FI `ModelFailure` return**, which only
  exists once the native model converges onto the FI contract — the separate Plan-102
  track (residuals), not this plan.
  - **FI-adherence gate for touching a native model (folded from the 2026-07-06
    review):** CLAUDE.md §ForecastInterface Adherence is unconditional ("no
    exceptions, no silent workarounds") and sanctions exactly two responses to a
    model that does not fit the contract: (1) fix the model to comply, or (2) file
    an issue in the ForecastInterface repo and co-design. `persistence_fallback` /
    `climatology_fallback` are native (non-FI) `StationForecastModel`s, and B2
    modifies one of them without bringing it into FI compliance — a full native→FI
    convergence is genuinely out of Plan 100's scope, so **path (2) applies and must
    be discharged BEFORE B2 lands**, not deferred to a downstream SAPPHIRE plan (a
    SAPPHIRE plan doc is *not* an FI-repo issue). **Required:** as part of B2, draft
    (and file) the FI-repo issue documenting the native-sentinel-fallback gap
    (`climatology_fallback` + `persistence_fallback` implement the native protocol,
    not the FI contract) and proposing the convergence design — this is cheap (it
    records an already-known gap) and satisfies CLAUDE.md path (2). The actual
    convergence work then proceeds under that FI issue via the separate track (see
    residuals), but the issue itself is filed now so no "no exceptions" violation
    ships in the merged PR. **DECIDED (plan-review round 3):** filing the FI-repo
    issue at B2 land time is the resolution — per the MANDATORY FI feedback
    (`feedback_forecastinterface_adherence_mandatory`: "can't adhere → file an FI-repo
    issue + co-design, never silently work around"). The earlier "obtain a CLAUDE.md
    carve-out instead" alternative is **dropped** — a carve-out is not on the table.

### M3 — Fallback handling in forecasts + alerts (Part B3 + alert safety)

- **B3 (label fallback forecasts) — a static membership check exposing a
  `ModelTier` enum, no config plumbing (Decision 2; enum + surface-coverage
  corrected round 4):** derive the tier at render/serialize time as
  **`ModelTier.FALLBACK if model_id in FALLBACK_MODEL_IDS else ModelTier.SKILL`** — a
  static import of the `types/ids.py` frozenset constant plus the new `ModelTier` enum
  (MAJOR-6, below). **Do NOT expose a raw `is_fallback: bool`:** per CLAUDE.md
  Type-Driven-Development ("Never use `bool` to represent a domain state with two named
  possibilities. Use `enum.Enum`"), a skill-vs-fallback tier is exactly that
  anti-pattern. Introduce a small **`ModelTier` enum** (`SKILL`, `FALLBACK`) alongside
  `FALLBACK_MODEL_IDS` (in `types/ids.py` or `types/enums.py`), and expose **that** on
  the JSON schema + badge, not a bool.
  - **Surfaces to cover — ALL that render raw priority / tier next to `model_id`
    (extended round 4):** the earlier scope covered only the forecast surfaces
    (`forecasts/list.html`, `forecasts/detail.html`, `api_forecasts.py`), but the
    **model-assignment surfaces** also render a raw `model_assignments.priority` right
    next to the `model_id` with no tier context, which is exactly the "a fallback
    priced 0 looks like a skill model" confusion this plan kills:
    - `api/templates/stations/detail.html:89` (`<td>{{ ma.priority }}</td>` in the
      per-station assignment table),
    - `api/templates/models/detail.html:59` (`<td>{{ a.priority }}</td>` in the
      per-model assignment table),
    - `ModelAssignmentResponse` (`api/schemas.py:36`) via `_to_model_assignment_response`
      (`api/routes/api_stations.py:56-57`).
    Thread the derived `ModelTier` (same static `FALLBACK_MODEL_IDS` membership check)
    into `_to_model_assignment_response` and render it in both assignment tables, so a
    fallback assignment is visibly badged everywhere its priority appears. These files
    join the B3 scope + Process doc-list.
  - Render a `FALLBACK` tier badge in `forecasts/list.html` + `detail.html` and expose
    the `ModelTier` on the JSON forecast schema (`api_forecasts.py`). No DB migration.
    **This DISSOLVES the earlier "the API needs `DeploymentConfig` plumbing" major:**
    because the tier is now a categorical constant (not
    `deployment_config.priority_for_model(...)`), the API needs **no** config load and
    **no** `SAPPHIRE_CONFIG` env var added to the `api` compose service — it only
    imports `FALLBACK_MODEL_IDS` + `ModelTier`. The Process file-list carries no
    `api/deps.py` config-plumbing scope.
  - **Relationship to the existing `input_quality` provenance mechanism (DECIDED
    after review):** `OperationalForecast` already carries
    `input_quality: InputQualityLevel` + `input_quality_flags`
    (`types-and-protocols.md:1352-1353`, categories today OBSERVATION/NWP/WARM_UP
    at `:435-459`) for "forecast produced under degraded *input* conditions". B3's
    `ModelTier` is a **deliberately separate** concept: it is not about degraded
    inputs but about **which model tier served the row**, and it is a **derived,
    render-time UI label** — no stored domain state, so it adds **no** typed *forecast*
    field to the spec now. We do NOT overload `InputQualityCategory` with a model-tier
    value, because that field's contract is input-data provenance, not model
    selection. **Consequence for CLAUDE.md's "every code change updates docs":**
    the render-time label needs no `OperationalForecast` spec change; the DEFERRED
    persisted `served_as_fallback` column (residuals) is the variant that WOULD add
    a first-class typed field and update the spec — flagged there so the decision is
    explicit, not silent. (The `ModelTier` enum itself — being a new domain enum on the
    API schema — is documented in `types-and-protocols.md` at land time.)
- **M3-alert (SUPPRESS + tier-filter, redesigned — the webhook framing was
  fictional; extended after review to cover pooled/BMA/consensus):** there is
  **no flood-alert webhook/dispatch in v0** (alerts are logged + shown via API),
  so there is no "webhook payload" to label. Instead: **no flood alert may be
  computed from any fallback-tier ensemble**, whether it is the sole ensemble
  (PRIMARY path) or one of several pooled together.
  - **Filter ONCE, centrally, on `all_ensembles` immediately before the Phase-C
    `check_station_alerts` call — NOT inside any per-branch build (BLOCKER fix,
    2026-07-06 review):** `all_ensembles[sid]` is populated at **THREE** distinct
    sites (the dict is initialised at `run_forecast_cycle.py:956`), and an earlier
    draft framed it as "exactly two" and treated the rest as hypothetical — that was
    wrong. All three feed the same Phase-C `check_station_alerts` call and every one
    can carry a fallback ensemble:
    - **PRIMARY branch (the shipped fleet default** — `config.toml:29`
      `alert_model_strategy = "primary"`; `forecast_combination_strategy` unset →
      dataclass default `PRIMARY`, `deployment.py:112-114`**):**
      `all_ensembles[sid] = {fc_result.model_id: dict(fc_result.ensembles)}`
      (`run_forecast_cycle.py:1111`) — the **single** model that won
      `run_station_forecast`'s first-success fallback chain. On a fallback-only
      cycle (now *guaranteed* to succeed by M2's climatology floor) that one model
      IS `climatology_fallback`/`persistence_fallback`, so the dict is a single
      fallback entry. `_resolve_strategy_and_filter`'s `n_models <= 1` shortcut
      (`alert_checker.py:212-213`) then returns `PrimaryModelStrategy(),
      param_ensembles` **completely unfiltered**, and `PrimaryModelStrategy.evaluate`
      (`alert_strategy.py:146-194`) emits an `ExceedanceResult` → persisted `Alert`
      from that fallback. This is the incident-default path and the earlier
      "`combinable_results`"-style framing did **not** cover it (`combinable_results`
      only exists on the combination-mode `MultiModelForecastResult`;
      `FALLBACK_PRIORITY_THRESHOLD`'s sole codebase use is
      `run_station_forecast.py:75`, inside that combination-mode type).
    - **Combination branch:** `all_ensembles[sid] = {mid: dict(result.ensembles)
      for mid, result in multi_result.results.items()}`
      (`run_forecast_cycle.py:1207-1210`) — **all** successful models, fallback
      included, handed to `PooledEnsembleStrategy` for POOLED/BMA/CONSENSUS with
      homogeneous MEMBERS (`alert_checker.py:230,236,246`).
      `PooledEnsembleStrategy.evaluate` (`alert_strategy.py:197-232`) **never
      consults `priorities`** — it `_pool_ensembles` every member, so a fallback
      ensemble dilutes/skews the pooled exceedance.
    - **GROUP branch (third site):** the operational GROUP forecast path populates
      `all_ensembles.setdefault(sid, {})[model_id] = dict(result.ensembles)`
      (`run_forecast_cycle.py:1414`) per station produced by a group model, then
      hands the same dict to Phase C — so a group-scoped fallback would leak
      identically. The central filter (below) must cover this site too; it is real,
      not hypothetical.
    - **Because M2's B1a guarantees a climatology floor EVERY cycle**, from now on
      almost every station has ≥1 fallback ensemble in `all_ensembles` in **all three**
      build sites, so the leak is not an edge case — it is the steady state.
    - **Fix (branch-independent, categorical):** apply the tier filter exactly
      **once**, on the assembled `all_ensembles` dict, in the few lines **before**
      the Phase-C `check_station_alerts(all_ensembles=…)` call
      (`run_forecast_cycle.py:1441-1447`) — **drop every `model_id in
      FALLBACK_MODEL_IDS`** (`types/ids.py:20`), per M0c's single categorical tier
      source. Filtering here — not inside any of the three `all_ensembles` build
      sites (`:1111` PRIMARY / `:1207` combination / `:1414` GROUP) — makes the guard
      robust to which strategy produced the dict AND to any future population site,
      and keeps a single, greppable enforcement point. Because the filter tests
      `FALLBACK_MODEL_IDS` membership (not `assignment.priority`), **no drifted DB
      priority can leak a fallback into an alert** (M0c). This supersedes the earlier
      "build it the same way `combinable_results` filters" instruction, which named a
      combination-mode-only property and silently skipped the PRIMARY default.
  - **Suppress when the eligible set is empty (fallback-only cycle) — STATION-level
    granularity (resolved round 4):** the central filter drops every `model_id ∈
    FALLBACK_MODEL_IDS` from `all_ensembles[sid]` at **(station, model)** granularity,
    *before* `check_station_alerts` (which does the per-parameter fan-out internally,
    `alert_checker.py:108-176`). To keep the guard at that same greppable granularity —
    rather than re-implementing a pre/post-filter comparison inside `_check_station`'s
    per-parameter loop — **suppression is detected and logged per STATION**: if
    filtering leaves **zero** eligible models for a station (the whole
    `all_ensembles[sid]` dict is emptied), **suppress alert evaluation for that station**
    and log the monitorable **dotted** structlog event **`alert.suppressed_fallback_only`**
    (per `docs/standards/logging.md`'s `{entity}.{action}` pattern — the canonical
    `alert` entity already has a `suppressed` action; NOT colon-separated free-text like
    "alert suppressed: fallback-only"). This subsumes the primary-only case
    (`PrimaryModelStrategy`) and the pooled case under one rule. **Acceptance check 8 is
    therefore scoped to station-level** (fallback-only station → no `Alert`, one
    suppression event) — NOT a per-parameter suppression assertion. (Per-parameter
    suppression, comparing pre/post-filter `param_ensembles` inside the per-parameter
    loop, is a deliberately-declined finer granularity: with the categorical filter a
    fallback-only station is empty for *all* its parameters at once, so the station-level
    check is sufficient and simpler.)
  - **Rationale:** climatology is a day-of-year seasonal average — it carries zero
    event information and would trip the identical "alert" every year on that
    calendar day → pure false alarms. Persistence is obs-grounded but "current
    level is dangerous" is Flow 2's (observation→QC→alert) job, not a naive
    flat-line forecast alert. A flood alert must come from a **skill** forecast.
  - **Corollary:** after tier-filtering, any alert that fires is skill-sourced by
    definition, so no per-alert tier label is needed. (A persisted tier/suppression
    flag on the `Alert` record is a deferred v-next, not this plan.)
  - **Retroactive alert audit (one-time, folded from the 2026-07-06 review):** the
    fix is forward-looking, but the plan's own M0 root-cause section confirms the drift
    is **empirically present** on the live dev DB — so `FORECAST`-source alerts may
    ALREADY have been raised from a mis-classified fallback under the pre-fix path
    (`PooledEnsembleStrategy` never consults `priorities`, `alert_strategy.py:197-232`,
    and pools every member). The schema makes the audit trivial: every `Alert` persists
    `model_ids: tuple[ModelId, ...]` and `alert_model_strategy` (`types/alert.py:19-35`),
    populated at write time by `_process_results` from the contributing
    `ExceedanceResult.model_ids`/`.strategy` (`alert_checker.py:295-334`). **Required
    one-time audit (acceptance check 15):** query all `FORECAST`-source `Alert` rows
    whose `model_ids` intersect `FALLBACK_MODEL_IDS` (particularly any still
    ACTIVE/unresolved), and either surface them for operator review/resolution or
    explicitly confirm none exist on the live system before closing the plan. This is
    the backward-looking analogue of the forward-looking floor audit (check 5) and
    mini-state diagnostic (check 10), and it directly serves Goal #4 (a fallback must
    never be the basis of a flood alert) for alerts already on record.

### M4 — Minimal runtime tripwires: NWP-staleness + fallback-priority drift (Part C1)

- **C1a (config plumbing — prerequisite):** `monitoring.expected_delivery_offset_hours`
  is dead TOML (`config.toml:397`; `load_config` pops `adapters`+`monitoring`,
  `deployment.py:341-346`; the adapter loader `run_forecast_cycle.py:100-106`
  never reads it). Add the loader + `_WeatherForecastAdapterConfig` field before
  the check can read it — scoped work, not a free lookup.
- **C1b (the check):** once C1a lands, if NWP is expected but no grid archived in
  > `expected_delivery_offset_hours × cadence` → emit a loud monitorable event on
  the **same channel A3's post-hoc detection uses**. Full watchdog deferred to the
  Flow-4 monitoring plan.
- **C1c (fallback-priority drift tripwire — folded from M0c):** the same channel
  also emits a loud monitorable event if any `model_assignments.priority` **or**
  `group_model_assignments.priority` for `climatology_fallback` /
  `persistence_fallback` has drifted **below** `FALLBACK_PRIORITY_THRESHOLD`
  (`types/ids.py:20`). This is the ongoing detector for a recurrence of M0's exact
  root cause (a fresh DB edit / onboarding-path bug re-introducing the drift); it
  complements M0c's **categorical** tier (which already makes combination, B3, and M3
  immune to the drift) by making the DB drift itself *visible* rather than silent,
  and complements the M0b write-time guard (which blocks the app-layer write) by
  catching a drift introduced out-of-band (raw SQL / direct DB edit).

### Acceptance checks (what WF2 must make pass)

1. **Priorities reconciled (M0):** after M0a, **both** `model_assignments.priority`
   **and** `group_model_assignments.priority` for the fallbacks equal the config
   chain (climatology=100, persistence=90) for all stations/groups; **every
   non-allowlisted skill row converges to its config value** — specifically the
   previously-drifted-to-`0` `nwp_regression`/`linear_regression_daily` rows at 2009/2091
   become `10`/`30` (they are NOT on the override allowlist), while **an explicitly
   allowlisted override survives** (the `nwp_rainfall_runoff` on 2009/2091 stays `-10`).
   `combinable_results` **excludes** climatology and the tier on a climatology-sourced
   forecast is **`ModelTier.FALLBACK`** — **both because
   `climatology_fallback ∈ FALLBACK_MODEL_IDS`** (categorical, M0c), independent of its
   DB `priority`. **The pre-rewrite fleet audit ran** (MAJOR-3): every skill-row
   `priority`≠`priority_for_model(...)` divergence across the REAL target DB was
   explicitly triaged (allowlisted or reconciled), none silently clobbered — the
   allowlist is not assumed to equal the 2-station dev sample. Re-running M0a is a
   no-op. **M0b is proven against the live vector:**
   invoking `onboard_model_flow` (`flows/onboard_model.py`) directly — the
   `onboard-model` deployment path, station- AND group-scoped — with no explicit
   `assignment_priority` writes the **config** priorities, not `0`. (The `onboarding.py`
   Step 6/7 pipeline path already does this correctly — the new coverage is the flow
   path.) **M0a `time_step`/`status` preservation:** **every individual row's**
   `time_step` **and** `status` is bit-for-bit identical before/after M0a (the
   priority-only write path never touches them — NOT re-derived from
   `supported_time_steps`), and the pre-repair `time_step`-homogeneity audit ran so no
   station's/group's *effective* input-assembly `time_step`
   (`run_forecast_cycle.py:970-971`) changed as a side effect of the priority reorder
   either — heterogeneous stations were handled explicitly, not silently flipped.
2. **Restart persistence (A2):** cold-boot the mini via `start-sapphire.sh` → a
   `forecast-cycle` runs NWP-on (`nwp.*` logs, grids archived), no manual overlay,
   no `-nwp` file anywhere; `docs/deployment/mac-mini-staging.md` no longer
   documents a toggle.
3. **Floor writes + is labelled (B1a/B3):** station with an NWP model + the floor,
   stack NWP-off → a `climatology_fallback` row **IS** written and **badged
   FALLBACK**. Flip NWP-on → the skill model becomes primary, row **not** badged.
4. **A3 post-hoc detection + global gate (revised):** with `SAPPHIRE_REQUIRE_NWP=1`
   and NWP off, a station that produces zero forecasts is logged at **ERROR** (dotted
   event `forecast_cycle.station_dark`) and appears on `errors`/`stations_failed`
   (not a silent warning), while every other station still forecasts (flow does NOT
   abort). The flow-level `ConfigurationError` (the single general check
   `require_nwp and not weather_forecast_config.enabled` at `run_forecast_cycle.py:642`)
   fires **whenever NWP is required but `weather_forecast_config.enabled` is `False`** —
   which covers BOTH the `SAPPHIRE_CONFIG`-unset case (hardcoded `enabled=False`) **and
   the explicit `enabled=false`-in-merged-TOML case that was the actual historical
   incident** (the earlier two enumerated conditions missed the latter). The
   pre-existing unconditional STAC-missing raise (`:648-656`) still covers the
   adapter-unbuildable case independently. The env var is read through the config
   boundary (`_WeatherForecastAdapterConfig.require_nwp`, not an inline
   `os.environ.get`), and `docker-compose.macmini.yml` sets `SAPPHIRE_REQUIRE_NWP=1`
   **on the `prefect-worker` service** (`:24`) — the `default`-pool worker that runs
   `forecast-cycle`, NOT `prefect-worker-ingest` (`:32`) — so the gate is live on the
   deployed mini.
5. **Backfill effective + idempotent + fleet-clean (B1b):** 2009/2091 have active
   climatology artifacts + assignments at the correct priority; a NWP-off
   `forecast-cycle` writes fallback rows for both. Re-run of the backfill = no-op.
   **The fleet-wide floor audit** (all OPERATIONAL non-weather stations lacking an
   active `climatology_fallback` artifact) **returns zero** after the backfill —
   i.e. no station beyond 2009/2091 is silently floor-less.
6. **Onboarding floor-gate STRICT (B1b):** onboard a test station whose
   `climatology_fallback` training is forced to fail → station **NOT** OPERATIONAL
   and the onboarding run reports **non-green** (not swallowed). Floor training
   succeeding → OPERATIONAL. Existing onboarding tests updated.
7. **persistence empty-obs (B2):** `persistence_fallback.predict` on empty obs
   **raises the narrow `InsufficientObservationsError`** (not a bare `IndexError`),
   which the `_run_single_model` backstop (`run_station_forecast.py:174-208`) turns
   into a graceful "predict failed" reason → the chain advances; climatology still
   produces. (No "return `ModelFailure`" — the native protocol has no such return.)
8. **Alert suppressed on fallback-only + no pooled contamination (M3-alert):** (a)
   **shipped-default path (the incident config):**
   `forecast_combination_strategy=PRIMARY` + `alert_model_strategy="primary"`
   (`config.toml:29`) — a fallback-only cycle whose single `climatology_fallback`
   (or `persistence_fallback`) ensemble would trip a threshold **fires no `Alert`**
   and logs the dotted event `alert.suppressed_fallback_only`. This exercises the exact
   `all_ensembles[sid] = {fc_result.model_id: …}` PRIMARY build
   (`run_forecast_cycle.py:1111`) + `n_models<=1` unfiltered shortcut
   (`alert_checker.py:212-213`) that the blocker identified — NOT only the POOLED
   case. A skill-sourced exceedance under the same default alerts normally. (b)
   **mixed POOLED/BMA/CONSENSUS cycle** — a skill model AND a guaranteed climatology
   floor both succeed — computes the exceedance from the **skill ensemble only**;
   the climatology members never enter the pooled/BMA calculation
   (`run_forecast_cycle.py:1207-1210` build). Both (a) and (b) — and the GROUP build
   (`:1414`) — pass because the tier filter drops every `model_id ∈ FALLBACK_MODEL_IDS`
   **once, centrally**, on `all_ensembles` before `check_station_alerts`
   (`:1441-1447`), independent of which of the three sites populated the dict. **The
   suppression assertion is scoped to STATION-level** (a fallback-only station → zero
   `Alert` + exactly one `alert.suppressed_fallback_only` event), matching the
   (station, model)-granularity central filter — NOT a per-parameter suppression
   assertion (a fallback-only station is empty for all its parameters at once).
9. **C1 staleness fires (M4):** after C1a plumbing, force a grid-archival gap past
   `expected_delivery_offset_hours × cadence` → the staleness event fires (proves
   it is not dead code).
10. **Mini-state diagnostic (root-cause closure):** capture the **mini's** actual
    `model_assignments` + artifact status + priorities for 2009/2091 and record
    which candidate gap (unassigned floor / inert artifact / priority drift) was
    live at incident time, confirming the shipped fix closes it.
11. **M0c config-load validator fails loud:** a `config.toml` that prices any
    `FALLBACK_MODEL_IDS` member below `FALLBACK_PRIORITY_THRESHOLD` (e.g.
    `climatology_fallback = 5`) raises `ConfigurationError` at config load, not a
    silent runtime misclassification. A well-formed config loads cleanly.
12. **Write-time guard raises (M0b):** calling `create_station_assignment` /
    `create_group_assignment` (`services/model_onboarding.py:838-895`) — including via
    `onboard_model_flow` — with `model_id ∈ FALLBACK_MODEL_IDS` and
    `priority < FALLBACK_PRIORITY_THRESHOLD` **raises** rather than writing the row;
    a fallback at `>=90` and any skill model write normally.
13. **C1c fallback-priority drift tripwire fires (M4):** manually drift a
    `climatology_fallback` / `persistence_fallback` row in `model_assignments` **or**
    `group_model_assignments` below `FALLBACK_PRIORITY_THRESHOLD` → the tripwire emits
    its loud monitorable event (proving the DB-drift detector is not dead code), even
    though M0c's categorical tier already keeps B3/M3/combination correct.
14. **v0-scope §A4 amendment applied (B1b / Decision 1):** `docs/v0-scope.md §A4`
    step 8 is updated from "≥1 model artifact" to the ratified rule ("≥1 skill
    artifact AND an active `climatology_fallback` floor artifact"), matching the
    onboarding floor-gate — the doc and the code agree, no silent divergence.
15. **Retroactive alert audit clean (M3-alert / Goal #4):** the one-time query over
    all `FORECAST`-source `Alert` rows (`types/alert.py:19-35`) whose `model_ids`
    intersects `FALLBACK_MODEL_IDS` — restricted to still-ACTIVE/unresolved rows —
    either **returns zero** on the live system, or every returned row is surfaced for
    operator review/resolution before the plan closes. No historical fallback-sourced
    alert is left silently trusted.

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
  Trade-off (runoff-only one-flag toggle lost) **accepted permanently** (Decision 4) —
  recover ad-hoc via a throwaway overlay only if ever needed; no open fork.
- **A3 — DECIDED (redesigned): post-hoc dark detection, not a preflight.** Promote
  the existing `fc_result is None` / `primary_model_id is None` branches to ERROR
  + `errors.append` (dotted event `forecast_cycle.station_dark`); the
  `ConfigurationError` gate is **one general check** (`require_nwp and not
  weather_forecast_config.enabled` at `run_forecast_cycle.py:642`, corrected round 4)
  that fires whenever NWP is required but `enabled` is `False` — subsuming BOTH the
  `SAPPHIRE_CONFIG`-unset path AND the explicit `enabled=false`-in-merged-TOML case
  that was the **actual** historical incident (the earlier two enumerated conditions
  missed the latter). Simpler and more general than the climatology-keyed
  preflight (which duplicated an artifact fetch and could itself cause darkness by
  over-skipping a station a healthy persistence would serve).

### Part B — always-on fallback floor

- **B0/M0 — DECIDED: categorical tier + reconcile priorities (prerequisite).**
  See M0 above. M0c makes the fallback/skill tier a categorical `FALLBACK_MODEL_IDS`
  constant (single source for combination, B3, M3), demoting DB `priority` to
  ordering/tie-break; M0a/M0b reconcile and guard the priority column.
- **B1a — DECIDED:** both fallbacks; guarantee keyed on
  `climatology_fallback`-with-active-artifact (post-M0, priority 100).
- **B1b — DECIDED (STRICT floor-gate; owner-ratified v0-scope §A4 amendment):**
  Step 8 requires an active `climatology_fallback` floor artifact before OPERATIONAL —
  a deliberate, owner-ratified (2026-07-06) tightening of the locked `docs/v0-scope.md
  §A4` rule ("≥1 model artifact" → "≥1 skill artifact AND an active floor artifact"),
  applied to that doc at land time; Step 7 does not swallow floor-training failure;
  `flows/onboard.py` surfaces it non-green; floor plugin-load/absence is loud;
  onboarding tests budgeted; idempotent backfill for 2009/2091.
- **B2 — DECIDED:** persistence empty-obs guard **raises a narrow named exception**
  (e.g. `InsufficientObservationsError`) that the existing `_run_single_model` backstop
  (`run_station_forecast.py:174-208`) turns into a graceful "predict failed" reason →
  chain advances to climatology. It does **NOT** "return `ModelFailure`" — that is an
  FI-contract type and the native `StationForecastModel.predict` Protocol
  (`protocols/forecast_model.py:32-39`) has no failure-sentinel return (corrected round
  4). Because B2 touches a native (non-FI) model, CLAUDE.md §FI path (2) is discharged
  **at B2 land time** by filing the FI-repo issue for the native-sentinel-fallback gap;
  the full native→FI convergence (where a real `ModelFailure` return would exist) is the
  separate Plan-102 track (residual). **The "CLAUDE.md carve-out instead" alternative is
  dropped (Decision 4) — filing the FI issue is the resolution.**
- **B3 — DECIDED:** label fallback forecasts via a **derived `ModelTier` enum
  (`SKILL`/`FALLBACK`)** computed from a **static `model_id in FALLBACK_MODEL_IDS`
  membership check** (Decision 2), NOT a raw `is_fallback: bool` (MAJOR-6 / CLAUDE.md
  enum-over-bool) — no config load, no `SAPPHIRE_CONFIG` in the `api` service, no
  migration. The tier is rendered/serialized on **all** surfaces that show
  `model_id`/priority: the forecast list/detail + JSON schema **and** the
  model-assignment tables (`stations/detail.html:89`, `models/detail.html:59`,
  `ModelAssignmentResponse` via `_to_model_assignment_response`). `ModelTier` is a
  deliberately separate render-time UI concept from the existing input-provenance
  mechanism (`OperationalForecast.input_quality`, `types-and-protocols.md:1352-1353`)
  — it is model-tier, not degraded-input, so it does NOT overload
  `InputQualityCategory` and adds no typed *forecast* field now (see M3/B3 body).

### Part C — detectability + alert safety

- **C1 — DECIDED:** minimal grid-staleness check (C1a plumbing + C1b), full
  monitoring deferred to Flow 4.
- **Alert suppression + tier-filter — DECIDED (owner, 2026-07-06; extended after
  review):** drop every `model_id ∈ FALLBACK_MODEL_IDS` from the alert-eligible set
  (categorical tier, M0c) in **one** central filter on `all_ensembles` before Phase-C
  dispatch — covering all THREE build sites (PRIMARY `:1111`, combination `:1207`,
  GROUP `:1414`) and every strategy (Primary AND Pooled/BMA/Consensus) — then suppress
  (dotted event `alert.suppressed_fallback_only`) when nothing eligible remains. A
  climatology seasonal average cannot be a real flood alert nor be *pooled into* one;
  persistence "river is high now" belongs to Flow 2, not a forecast alert. Replaces
  the fictional webhook-label scope plan-review flagged, and closes the
  pooled-contamination path the second review found (`PooledEnsembleStrategy` ignores
  `priorities`, and `all_ensembles` was built from `multi_result.results`, not
  `combinable_results`).

## Residual forks / follow-ups (post-100)

All plan-review round-3 forks are now RESOLVED (Decision 4). Two residuals remain
open: the DEFERRED persisted-column work (needs a migration) and the DB-level
`server_default` tightening (belt-and-suspenders, tracked by C1c).

- **[OPEN — belt-and-suspenders, tracked by C1c] DB `priority` `server_default="0"`
  gap:** `model_assignments.priority` (`alembic/versions/0001_v0_schema.py:405`) and
  `group_model_assignments.priority` (`0021_add_group_model_assignments.py:35`) both
  carry `server_default="0"`, so a raw SQL INSERT that omits `priority` lands a row at
  the skill tier `0` — the same drift signature the incident showed, on any code path
  that bypasses the M0b app-layer write-time guard. **Tightening the DB DEFAULT is
  out of scope for Plan 100** (M0c's categorical tier already makes B3/M3/combination
  immune to the drift, and C1c's tripwire makes any such out-of-band row *visible*);
  it is recorded here as an explicit open residual rather than silently closed. (This
  corrects the earlier "all round-3 forks resolved / only the persisted-column residual
  open" framing, which omitted this gap.)
- **[NEW TRACK] Native models → FI contract** (owner-requested): converge
  `climatology_fallback`, `persistence_fallback`, `linear_regression_daily` onto
  the FI contract. Separate plan (suggest Plan 102); ties to
  `feedback_forecastinterface_adherence_mandatory` + Plan 076. **The FI-repo issue
  for the native-sentinel-fallback gap is filed as part of B2 (see M2/B2 above),
  not deferred to this track** — this track is the *implementation* that proceeds
  under that already-filed issue, satisfying CLAUDE.md path (2) at B2 land time.
  (The former "CLAUDE.md carve-out" alternative fork is **dropped** — Decision 4:
  filing the FI issue is the resolution, a carve-out is not on the table.)
- **[RESOLVED] Runoff-only toggle** — lost by the A2 fold; **accepted permanently**
  (Decision 4). Recover ad-hoc via a throwaway overlay only if a future
  skill-comparison experiment ever needs it. No longer an open fork.
- **[RESOLVED → folded into the A3 single gate] silent-NWP-off paths** —
  `_load_weather_forecast_adapter_config` yields `enabled=False` both when
  `SAPPHIRE_CONFIG` is unset (`run_forecast_cycle.py:143-151`) **and** when the merged
  TOML explicitly sets `enabled=false` (the actual historical incident). **Both are now
  caught by the single general A3 gate** (`require_nwp and not
  weather_forecast_config.enabled` at `:642`, Decision 4 / round-4 correction);
  acceptance check 4 covers both.
- **[DEFERRED] Persisted `served_as_fallback` forecast column** + a persisted
  tier/suppression flag on the `Alert` record — durable/queryable; needs
  a migration. Confirmed deferred to a later plan — B3's `ModelTier` ships as a
  render-time label only.

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

See **Acceptance checks** (15) in the IMPLEMENTATION VISION. Local dev repro:
onboard a test station with an NWP model + the floor, run a `forecast-cycle`
NWP-off → confirm a `climatology_fallback` row is written, badged fallback, and
**no alert fires**; flip NWP-on → skill model becomes primary. Cold-boot the mini
via `start-sapphire.sh` → NWP-on unconditionally, no `-nwp` file.

## Process

grill-me COMPLETE + **three `plan-review` rounds folded** (this doc), most recently
round 3 (Decisions 1–4). Next: **re-run `plan-review`** to confirm the folded
decisions (categorical `FALLBACK_MODEL_IDS` tier, write-time guard, v0-scope §A4
amendment, A3 post-hoc + `SAPPHIRE_CONFIG` gate, alert suppression, strict
floor-gate) converge with no new blockers/majors → phases → READY → `vision-build`
(WF2). Implementation touches:
- **`types/ids.py`** — new `FALLBACK_MODEL_IDS: frozenset[ModelId] = frozenset({
  ModelId("climatology_fallback"), ModelId("persistence_fallback")})` constant next to
  `FALLBACK_PRIORITY_THRESHOLD` — the SINGLE categorical tier source (Decision 2);
  wrapped through `ModelId` per the existing sentinel-constant pattern (`:16-19`), not
  bare `frozenset[str]`. **Plus a small `ModelTier` enum (`SKILL`/`FALLBACK`)** (in
  `types/ids.py` or `types/enums.py`) derived from `FALLBACK_MODEL_IDS` — the value
  exposed on the B3 badge/JSON schema in place of a raw `is_fallback: bool` (MAJOR-6 /
  CLAUDE.md enum-over-bool).
- **`services/run_station_forecast.py:71-76`** — `combinable_results` filters on
  `model_id not in FALLBACK_MODEL_IDS` (was `priorities.get(mid,0) <
  FALLBACK_PRIORITY_THRESHOLD`); **migrate the regression test
  `tests/unit/services/test_run_station_forecast.py:617-640`
  (`test_combinable_results_excludes_high_priority_fallbacks`)** to the membership
  semantics (see B1b test-migration budget item 2).
- **`services/model_onboarding.py:838-895`** — write-time guard in
  `create_station_assignment`/`create_group_assignment` (raise if `model_id ∈
  FALLBACK_MODEL_IDS and priority < FALLBACK_PRIORITY_THRESHOLD`); **`flows/onboard_model.py`**
  (M0b — the live drift default) + `model_onboarding.py:994` (make explicit).
- a named M0a reconciliation script covering **both** `model_assignments` and
  `group_model_assignments`, preceded by a **read-only fleet audit** (every skill-row
  `priority`≠`priority_for_model(...)` divergence across the REAL target DB, each
  explicitly triaged before any rewrite — MAJOR-3), driven by an **explicit
  `(station_id, model_id)` override allowlist** (M0a precedence rule 2 —
  `nwp_rainfall_runoff` on 2009/2091 as the *starting* allowlist, completed by the
  audit), a **priority-only write path** (a new thin `UPDATE` store method — **scoped
  private/one-off to the M0a repair script** OR, if reused, itself asserting the
  `FALLBACK_MODEL_IDS`/threshold guard before issuing the UPDATE so no code path
  persists a fallback below tier; or read-back-and-round-trip of each row's existing
  `time_step`/`status` — never re-derive `time_step`), **plus a pre-repair
  `time_step`-homogeneity audit**. **Doc-sync: if the new `UPDATE` store method (option
  b) is taken, add its signature to both Store Protocols in
  `docs/spec/types-and-protocols.md` at land time.**
- a one-time **retroactive alert audit** query (`FORECAST`-source `Alert` rows whose
  `model_ids` intersect `FALLBACK_MODEL_IDS`, still-ACTIVE/unresolved) — acceptance
  check 15 / Goal #4.
- **`config/deployment.py`** — M0c config-load-time validator, enforced **only on
  fallback ids explicitly present in `self.model_priorities`** (`v =
  self.model_priorities.get(mid); if v is not None and v < FALLBACK_PRIORITY_THRESHOLD:
  raise ConfigurationError`) — NOT via `priority_for_model(...)`, whose
  `DEFAULT_PRIORITY=50` for an absent id would over-fire and break
  `make_deployment_config()` and every config omitting the fallbacks (BLOCKER-1).
- `mac-mini.toml` + compose (`docker-compose.macmini.yml` sets `SAPPHIRE_REQUIRE_NWP=1`
  **on the `prefect-worker` service**, `:24`) + `docs/deployment/mac-mini-staging.md`
  (A2 toggle rewrite).
- **`run_forecast_cycle.py`** — A3 post-hoc ERROR+errors (dotted
  `forecast_cycle.station_dark`) + `_WeatherForecastAdapterConfig.require_nwp` field +
  parse in `_load_weather_forecast_adapter_config` + **single general gate**
  (`ConfigurationError` when `require_nwp and not weather_forecast_config.enabled` at
  `:642` — subsumes `SAPPHIRE_CONFIG`-unset AND the explicit-`enabled=false` incident
  case; the STAC-missing raise at `:648-656` already covers adapter-unbuildable) +
  **filter `all_ensembles` ONCE
  — drop `model_id ∈ FALLBACK_MODEL_IDS` immediately before the Phase-C
  `check_station_alerts` call (`:1441-1447`)**, covering all three build sites
  (`:1111`/`:1207`/`:1414`); C1a loader + `_WeatherForecastAdapterConfig` field,
  C1b/C1c checks.
- `onboarding.py` Step 7/8 + `flows/onboard.py` + onboarding tests (B1b) + a one-time
  fleet floor-audit query.
- `persistence_fallback.py` (B2 — **raise a narrow `InsufficientObservationsError`**
  caught by the `_run_station_forecast` backstop; NOT "return `ModelFailure`", which the
  native protocol cannot express) + **a filed FI-repo issue for
  the native-sentinel-fallback gap**.
- `alert_strategy.py`/`alert_checker.py` (M3 suppression `alert.suppressed_fallback_only`
  across Primary/Pooled/BMA/Consensus) — note the central filter now makes fallback
  ensembles never reach these strategies; API/templates (B3 — static
  `FALLBACK_MODEL_IDS` import + derived `ModelTier` enum, **no `api/deps.py` config
  plumbing, no `SAPPHIRE_CONFIG` in the `api` service**). **B3 surfaces:**
  `api/templates/forecasts/{list,detail}.html` + `api/routes/api_forecasts.py` (forecast
  tier badge/flag) **AND the model-assignment surfaces**
  `api/templates/stations/detail.html:89`, `api/templates/models/detail.html:59`, and
  `ModelAssignmentResponse` (`api/schemas.py:36`) via `_to_model_assignment_response`
  (`api/routes/api_stations.py:56-57`) — all render `model_id`/priority and must carry
  the `ModelTier` (MAJOR-5).

**Docs updated at land time** (CLAUDE.md: every code change updates affected docs):
- `docs/v0-scope.md §A4` — apply the owner-ratified operational-mark amendment
  ("≥1 model artifact" → "≥1 skill artifact AND an active `climatology_fallback` floor
  artifact"; Decision 1 / acceptance check 14).
- **`docs/v0-scope.md` model-inventory table + §A8e mechanism sentence (folded from the
  2026-07-06 review)** — v0-scope.md is CLAUDE.md's #1 "read first" authoritative doc,
  and it carries the identical staleness M0c fixes elsewhere: the inventory table
  (`docs/v0-scope.md:120-124`) lists `LinearRegressionDaily` priority `0`,
  `ClimatologyFallbackModel` `90`, `PersistenceFallbackModel` `99` — all stale vs.
  `config.toml:59-64` (`linear_regression_daily=30`, `climatology_fallback=100`,
  `persistence_fallback=90`; and the reversed climatology/persistence order is the same
  pre-existing Plan-089 drift M0 diagnoses). **Correct the table to the config values**,
  and **rewrite the two mechanism sentences that describe the superseded numeric gate**
  — `:126` ("Models with priority ≥ `FALLBACK_PRIORITY_THRESHOLD` (= 90) are excluded
  from multi-model combination") and `:173` ("`combinable_results` property excludes
  fallback models (priority ≥ 90)") — to the categorical `model_id ∈ FALLBACK_MODEL_IDS`
  mechanism, mirroring the `conventions.md`/`types-and-protocols.md` edits below. Leaving
  these stale is exactly the "silent divergence" CLAUDE.md forbids, on the highest-
  priority doc.
- `docs/standards/logging.md` — the A3 WARNING→ERROR carve-out for a zero-forecast
  station, plus the new dotted events `forecast_cycle.station_dark` /
  `alert.suppressed_fallback_only`.
- `docs/architecture-context.md` (the **Fallback models** paragraph, `:132`, plus the
  `:128` `combinable_results` sentence) — TWO edits: (1) correct the STALE fallback
  priorities `ClimatologyFallbackModel (priority 90)` / `PersistenceFallbackModel
  (priority 99)` to `climatology=100` / `persistence=90`, matching `conventions.md:441-442`
  + `config.toml`; **and (2) rewrite the parenthetical mechanism clause** — "(models with
  priority ≥ `FALLBACK_PRIORITY_THRESHOLD` = 90 are never included in pooled, bma, or
  consensus combination)" at `:132` and "excludes fallback models (priority ≥
  `FALLBACK_PRIORITY_THRESHOLD`)" at `:128` — to the **categorical `model_id ∈
  FALLBACK_MODEL_IDS`** scheme (M0c Decision 2), mirroring the
  `conventions.md`/`types-and-protocols.md`/`v0-scope.md` edits. Fixing only the numeric
  values while leaving the superseded numeric-gate mechanism described would re-introduce
  the same "silent divergence" CLAUDE.md forbids.
- **`docs/conventions.md` §Model assignment priority (`:424-449`)** and
  **`docs/spec/types-and-protocols.md` (the `combinable_results` / priority section)** —
  both today describe a **single DB-`priority`-driven** fallback-tier mechanism; M0c
  replaces the tier's *source* with the categorical `FALLBACK_MODEL_IDS` constant, so
  both must be updated to document the new scheme (folded, superseding the earlier
  "dual-source" framing):
  > **Fallback/skill tier membership is now categorical** — `model_id ∈
  > FALLBACK_MODEL_IDS` — and is the single source read by forecast combination
  > (`combinable_results`), the B3 badge, and M3 alert-suppression. DB
  > `assignment.priority` no longer decides tier membership anywhere; it is retained
  > only as an **ordering / tie-break** key among admitted skill models
  > (`min(priority)` primary-selection dispatch, combination ordering). The config
  > `[model_priorities]` map sets that ordering integer, and a config-load validator
  > keeps every **explicitly-configured** fallback's ordering integer
  > `>= FALLBACK_PRIORITY_THRESHOLD` (an omitted fallback falls back to
  > `DEFAULT_PRIORITY` and is classified purely by the categorical set).
  This removes the reverse-engineering burden and documents that a drifted DB
  `priority` can no longer misclassify a tier. (B3's render-time `ModelTier` adds **no**
  new field to the `OperationalForecast` dataclass — only the DEFERRED persisted
  `served_as_fallback` column would; the spec edits here are the
  priority/`combinable_results` narrative **plus documenting the new `ModelTier` enum**
  on the API schema, not a new `OperationalForecast` field.) A named
idempotent backfill for 2009/2091 + fleet residue → **hold-at-PR** with a version
bump, per CLAUDE.md.
