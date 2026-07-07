# Plan 100 — forecast-feed resilience: persist NWP-on across restarts + always-on fallback (no silent blackout)

**Status**: DRAFT — **FINAL-GATE FIXES FOLDED (2026-07-07)**: a final-gate review
returned NOT-READY (3 blockers + 2 majors — genuine self-contradictions +
under-specification); all folded here with two owner decisions.
**Fatal NWP gate RAISES (Prefect FAILED):** `SAPPHIRE_REQUIRE_NWP=1` + NWP-off →
`raise ConfigurationError` → the Prefect `forecast-cycle` run **FAILS** and returns
NO `ForecastCycleResult`; that gate therefore **NEVER** sets
`ForecastCycleHealth.FAILED` (a raised exception carries no result) — `FAILED` is
reserved for a cycle-wide station failure that STILL completes the Prefect task.
**`no_floor` DERIVED (no migration):** the `no_floor` indicator is computed at
query/render time from active-`climatology_fallback`-artifact presence — NO persisted
column, NO `StationStatus` member, NO migration; a computed dashboard/API badge.
**Canonical fallback-priority reconciled:** the assignment/backfill/creation path
derives a fallback's `≥90` priority from a canonical tier map
(`FALLBACK_ASSIGNMENT_PRIORITIES`, climatology=100/persistence=90), **NEVER** from
`priority_for_model()` (which yields `DEFAULT_PRIORITY=50` on omission), so the M0b
write-guard is always satisfied; config `[model_priorities]` omission is safe for
CLASSIFICATION ONLY (categorical), not for the assignment VALUE.
**`pipeline_health` plumbing pinned:** `PgPipelineHealthStore` wired into
`make_pg_stores()` / `run_forecast_cycle_flow` / `api/deps.py` + a minimal
route/schema/template, and **concrete `PipelineCheckType` values pinned** per signal
(no "or/e.g.").
**EXTERNAL REVIEW FOLDED (2026-07-07)**: a specialized
(hydrology + production-reliability) external reviewer returned
**SHIP-WITH-CHANGES** (one blocker + several majors). All seven owner-decided
resolutions are folded into the relevant sections below (integrated, not appended).
**They are cited throughout as `ER-D1`…`ER-D7`** to avoid collision with the earlier
plan-review round-3 "Decision 1/2/4" numbering (which stands unchanged):
**(ER-D1, BLOCKER)** the blanket "suppress ANY fallback-only forecast alert" rule was
UNSAFE (with obs-alerts off, a gauge observably above threshold during an NWP
outage would yield NO alert at all) → replaced by an **`AlertEligibility`**
classification (`SKILL_FORECAST` / `CURRENT_OBS_PROXY` / `NO_EVENT_INFORMATION`,
distinct from `ModelTier`): climatology (`NO_EVENT_INFORMATION`) never raises a
flood alert, and a persistence (`CURRENT_OBS_PROXY`) exceedance likewise raises no
FORECAST flood alert; a dangerous CURRENT level is covered independently by the
enabled observation-alert path (Flow 2's obs checker), NOT by any forecast-cycle
re-routing (see the targeted-review note below); HARD ship precondition
`enable_observation_alerts=true` on the mini; **(ER-D2)** a climatology-QA diagnostic
at onboarding/backfill (recurring seasonal
baseline crossing a danger threshold → a config/threshold-review signal, NOT a
flood warning); **(ER-D3)** the Step-8 floor-gate separates a **DERIVED `no_floor` indicator**
(computed at query/render time from active-`climatology_fallback`-artifact presence
— NOT a persisted field, NO `StationStatus` member, NO migration) from station
operational status, rolled out **phased** (hard gate for NEW onboarding; existing
stations get the derived `no_floor` badge + operator reporting, not a surprise
fleet-wide OPERATIONAL→NOT flip); **(ER-D4)**
dark/suppressed/stale events become **first-class queryable `pipeline_health`
records** (the existing health-metrics table, NOT `AlertSource.PIPELINE` alert rows)
**+ explicit `ForecastCycleHealth` run-outcome semantics** (degraded / failed /
healthy on the cycle result, NOT a new `FlowRunState` member), written regardless of
the `enable_*_alerts` flags — not log-only; **(ER-D5)** the
mini-state root-cause capture (old check 10) is promoted to a **step-0
prerequisite** (immutable snapshots BEFORE any M0/M2 mutation); **(ER-D6)** M0a gains
a **fleet-mutation safety envelope** (maintenance-mode / advisory lock, DB backup,
dry-run diff, per-divergence triage, single transaction, migration-audit record);
**(ER-D7)** a **registry/load-time tier guard** — every discovered model must declare
its `ModelTier` + `AlertEligibility` (or be explicitly listed) or **fail loud**
before it can participate in combination or alerting.
**TARGETED-REVIEW FIXES FOLDED (2026-07-07, NEEDS-FIXES → 3 blockers + 4 majors, all
code-grounded fold-integrity fixes, no open forks):** (1) the forecast-cycle
fresh-persistence → `AlertSource.OBSERVATION` routing is **DELETED as redundant** — the
existing observation checker (`services/observation_alert_checker.py`) already fetches
QC-passed obs (24h lookback), selects the latest, checks thresholds, and writes
`AlertSource.OBSERVATION` (`:25,:52,:63,:73,:80`), wired under
`enable_observation_alerts` (`flows/ingest_observations.py:366`); with that flag a HARD
ship precondition, a dangerous current level is covered by Flow 2, so the forecast cycle
needs no parallel current-condition path (net model: `SKILL_FORECAST` → `FORECAST`
alert; `CURRENT_OBS_PROXY` + `NO_EVENT_INFORMATION` → no FORECAST flood alert). (2) the
ungrounded "freshness gate" claim is removed — `check_observation_alerts` reads no
`observation_staleness_hours`/`input_quality`, only a fixed 24h lookback
(`observation_alert_checker.py:25,:52`). (3) all stale "`FALLBACK_MODEL_IDS` drives M3
alert-suppression" text is purged — `FALLBACK_MODEL_IDS` / `ModelTier` governs
COMBINATION + UI labelling ONLY; `AlertEligibility` governs alert routing. (4) M0d uses
**explicit `MODEL_TIERS` + `ALERT_ELIGIBILITIES` maps** (no skill-by-absence default;
`FALLBACK_MODEL_IDS` is DERIVED from `MODEL_TIERS`). (5) M5's dark/suppressed/stale
events are **`pipeline_health` records** (`store/pipeline_health_store.py`,
`types/pipeline.py`), NOT `AlertSource.PIPELINE` alert rows. (6) run-outcome uses a NEW
**`ForecastCycleHealth`** field on `ForecastCycleResult` (Prefect run stays COMPLETED;
`FlowRunState` is NOT extended). (7) the B1b decisions-recap is reconciled to ER-D3
(hard floor-gate for NEW onboarding only; existing stations → `no_floor` + phased
audit→backfill→verify). Plan 100 has GROWN; see
Process for the one sub-part flagged as a candidate follow-up split. — grill-me
COMPLETE (2026-07-06) + **plan-review round 3
decisions folded (2026-07-06)**: v0-scope §A4 amendment **ratified** (the OPERATIONAL
floor-gate is a deliberate, owner-ratified tightening of the locked rule, not a
silent override); **categorical `MODEL_TIERS` single-source tier** (the same
`ModelTier` fact now drives forecast combination and the B3 badge — no more dual
DB-priority-vs-config divergence, DB `priority` demoted to ordering/tie-break among
admitted skill models only; **alert routing is a SEPARATE `AlertEligibility` facet, NOT
`FALLBACK_MODEL_IDS`** — targeted-review correction); a
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

Milestones M0–M5 plus a **Step 0 root-cause-capture prerequisite** (ER-D5) that
now runs FIRST (M5 + the M0d registry guard were added by the 2026-07-07 external
review). **M0 is a prerequisite** the live DB check forced. The incident
root cause was that fallback-tier membership was inferred from the mutable DB
`assignment.priority` (a `priority ≥ 90` check), and real rows carry drifted
priorities (`0`/`-10`) so the floor was silently treated as a skill model. M0
attacks this on **two** fronts: (a) **M0c makes the tier categorical** — a
git-versioned `MODEL_TIERS` map (from which `FALLBACK_MODEL_IDS` is derived), used
identically by forecast combination and the B3 UI badge, so no DB-priority value can
move a model into or out of the fallback tier ever again; and (b) **M0a/M0b repair
and guard the priority *ordering*** so the reconciled rows are correct and a
below-tier fallback can no longer be written. **`FALLBACK_MODEL_IDS` / `ModelTier`
governs forecast COMBINATION and UI labelling ONLY; `AlertEligibility` (a separate
facet) governs alert routing.** A forecast flood alert fires only for
`SKILL_FORECAST`; suppression of the FORECAST alert happens for both fallback
eligibilities (`CURRENT_OBS_PROXY` and `NO_EVENT_INFORMATION`), with current-condition
coverage provided by the enabled observation-alert path (Flow 2's obs checker). M1+M2
are the incident fix; M3 stops operators trusting a fallback and keeps a fallback out
of any FORECAST alert; M4 is a minimal staleness + drift tripwire.

### Step 0 — Root-cause capture BEFORE any mutation (PREREQUISITE — ER-D5, external review)

The former acceptance check 10 ("mini-state diagnostic") was a **late closure
check** — but M0a/M0b/M2 all *mutate* the mini's `model_assignments`, artifacts,
and config, which would **destroy the evidence** needed to prove the incident
mechanism after the fix. The external reviewer required this be **front-loaded to a
step-0 prerequisite**, run BEFORE any M0/M2 write:
- **Immutable snapshots** of the mini's incident-time state — `model_assignments` +
  `group_model_assignments` (priorities, `time_step`, status), active/failed model
  artifacts, the forecasts + `Alert` rows produced during the 07-03→07-06 blackout,
  the resolved config env (`SAPPHIRE_CONFIG` / overlay in effect at restart), and the
  NWP-archive state (last grid archived, Plan-095 retention actions). Captured as a
  read-only export (DB dump excerpt + logged JSON), stored out-of-band so the
  subsequent repairs cannot overwrite it.
- **Dry-run diagnostics** over that snapshot: replay which candidate gap (unassigned
  floor / inert floor artifact / priority drift / `enabled=false` gate) was live at
  incident time, WITHOUT mutating anything.
- Only AFTER Step 0's snapshots + dry-run are recorded may M0a/M0b/M2 mutate state.
  Acceptance check 10 is reworded (below) from a closure check to this **prerequisite**
  — the incident mechanism must remain provable from the snapshot after the fix ships.
  (ER-D6's M0a safety envelope layers a *second* pre-mutation guard — backup +
  dry-run diff — specifically around the fleet priority rewrite.)

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
and the intended B3 tier check both inferred the tier from `assignment.priority`,
the drifted `climatology_fallback=0` row was treated as a **combinable *skill*
model** — blended into the pooled forecast and eligible to drive an alert. **M0c
removes this failure mode structurally:** the tier is now keyed on the explicit
`MODEL_TIERS` map (from which `FALLBACK_MODEL_IDS` is derived), so combination and the
B3 badge both classify climatology as a fallback regardless of its (now merely
ordering-only) DB `priority`; the alert path classifies it via the separate
`ALERT_ELIGIBILITIES` map (`NO_EVENT_INFORMATION` → never a forecast flood alert). M0a/M0b
still reconcile the priority column so the ordering is correct and no below-tier fallback
row can be written.

- **M0a — repair existing rows (precedence rule, aligned with the Plan-089 model
  in `docs/conventions.md:424-449`):** a named idempotent admin action rewrites
  **BOTH** `model_assignments.priority` **and** `group_model_assignments.priority`,
  applying this precedence crisply (the earlier "rewrite everything to
  `priority_for_model` *and also* preserve overrides" wording was internally
  contradictory and is replaced):
  1. **Every `FALLBACK_MODEL_IDS` member is UNCONDITIONALLY rewritten to its
     CANONICAL fallback-assignment priority** — `climatology_fallback→100`,
     `persistence_fallback→90` — resolved from the canonical
     **`FALLBACK_ASSIGNMENT_PRIORITIES`** tier map (`types/ids.py`, alongside
     `MODEL_TIERS`), **NOT** from `priority_for_model(model_id)` (FIX-A). This
     distinction is load-bearing: `priority_for_model` returns `DEFAULT_PRIORITY=50`
     (`config/deployment.py:29,:250-256`) for any fallback **absent** from a
     deployment's `[model_priorities]`, and `50 < 90` would land the floor **below**
     its tier and trip the M0b write-guard. The canonical map resolves a fallback's
     ordering integer as: its explicitly-configured `[model_priorities]` value when
     present (the M0c validator guarantees that is `≥90`), else the hard-coded
     canonical floor (`climatology_fallback=100`, `persistence_fallback=90`) — never
     `50`. So the floor always lands in the `≥90` tier; a fallback row is never left
     below threshold, regardless of any prior manual edit **or a config that omits the
     fallbacks** (the whole point of M0 is that a fallback below `90` is the bug).
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
  - **M0a fleet-mutation safety envelope (ER-D6, external review) —
    concurrency + rollback story for the fleet-wide priority rewrite:** M0a mutates
    `priority` across potentially the whole ~1000-station fleet while forecast cycles
    (`run_forecast_cycle_flow`) and model onboarding (`onboard_model_flow`) may be
    reading/writing the same `model_assignments`/`group_model_assignments` rows — a
    concurrent write is a corruption window, and a bad rewrite has no rollback today.
    Required before M0a is allowed to run:
    1. **Block concurrent writers:** put the deployment in **maintenance mode** (pause
       the `forecast-cycle` and `onboard-model` Prefect deployments) OR take a
       DB **advisory lock** that the forecast cycle + onboarding paths respect, for the
       duration of the mutation, so no cycle/onboarding runs interleave with the rewrite.
    2. **DB backup** of `model_assignments` + `group_model_assignments` (or a full
       snapshot) taken immediately before the rewrite — the rollback artifact.
    3. **Dry-run diff FIRST:** produce the full `(station_id, model_id, old_priority →
       new_priority)` diff and require operator sign-off before applying; every
       **non-config divergence** (a skill row whose stored priority ≠
       `priority_for_model` and is not on the override allowlist) requires **explicit
       triage** — this is the same read-only audit as MAJOR-3 above, now hardened to a
       mandatory pre-apply gate, not a silent blanket rewrite.
    4. **Apply in ONE transaction** where the store/driver allows, so a mid-rewrite
       failure rolls back cleanly rather than leaving the fleet half-reconciled.
    5. **Persist a migration-audit record** — a `pipeline_health` record via
       `PgPipelineHealthStore.append_health_record` (`store/pipeline_health_store.py:16`),
       pinned (FIX-E) to `check_type=PRIORITY_MIGRATION_AUDIT` (a **new**
       `PipelineCheckType` member), `status=OK`,
       `subject="m0a_priority_reconciliation"`, `detail={"rows_changed",
       "triaged_overrides", "backup_reference"}` — **NOT** an `AlertSource.PIPELINE`
       alert row (see ER-D4) — capturing who ran M0a, when, the applied diff, and the
       backup reference, so the mutation is queryable after the fact, not just a
       shell-history line.
    Acceptance check 1 is extended to assert the envelope ran (maintenance/lock held,
    backup taken, dry-run diff signed off, single-transaction apply, migration-audit
    record written).
- **M0b — fix the creation paths:** the config-driven `priority_for_model` value
  must reach **every** assignment write. The two pipeline call sites
  (`onboarding.py` Steps 6/7) already do this; the live gap is **`onboard_model_flow`
  (`flows/onboard_model.py:470`)** — either (a) drop the bare `assignment_priority:
  int = 0` default and resolve `deployment_config.priority_for_model(model_id)`
  inside the flow when the caller omits it (mirroring `onboarding.py:608-612`), or
  (b) require callers to always pass an explicit config-sourced priority (fail loud
  if omitted). `onboard_model()`'s `:994` default may stay (its only caller
  overrides it) but making it explicit there too is cheap belt-and-suspenders.
  - **M0b canonical fallback-priority resolution (FIX-A) — every creation path that
    resolves a fallback's priority uses the canonical map, NOT `priority_for_model`:**
    `onboarding.py` Steps 6/7 (`onboarding.py:481-494`,`:604-612`) resolve **every**
    model's priority via `deployment_config.priority_for_model(model_id)`, which returns
    `DEFAULT_PRIORITY=50` for any model absent from `[model_priorities]`. For a
    **`FALLBACK_MODEL_IDS` member**, a deployment config that omits the fallbacks would
    therefore write `50` — below tier — and the M0b write-guard would raise, breaking
    onboarding. **Fix:** at every creation site (onboarding Steps 6/7 **and**
    `onboard_model_flow`), resolve a fallback's assignment priority from the canonical
    `FALLBACK_ASSIGNMENT_PRIORITIES` map (its explicit `[model_priorities]` value when
    present, validator-guaranteed `≥90`; else the hard-coded canonical `100`/`90`) —
    **never** the raw `priority_for_model()` `50`-on-omission. Skill models continue to
    resolve via `priority_for_model`. This is what makes the write-guard structurally
    satisfiable for a fallback under **any** deployment config, and keeps M0a/M0b/the
    write-guard/the M0c-validator mutually consistent: no path assigns a fallback at `50`.
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
- **M0c — an explicit `MODEL_TIERS` map is the SINGLE categorical source of truth
  for the fallback/skill tier; `FALLBACK_MODEL_IDS` is DERIVED from it — DECIDED
  (plan-review round 3, Decision 2; explicit-map form folded from the targeted
  review, MAJOR-4):** the earlier "hand-listed `FALLBACK_MODEL_IDS` frozenset + tier
  by absence" form was itself a skill-by-absence default (a model NOT in the set
  defaults to skill — the same silent-default hazard M0d exists to kill). Replace it
  with **two explicit maps** in `types/ids.py` (next to `FALLBACK_PRIORITY_THRESHOLD`,
  `:20`) that enumerate **every known model** (or require each model to declare both
  facets as attributes — see M0d):
  - `MODEL_TIERS: dict[ModelId, ModelTier]` — the categorical skill/fallback tier
    (`climatology_fallback`→`FALLBACK`, `persistence_fallback`→`FALLBACK`;
    `nwp_regression`/`nwp_rainfall_runoff`/`linear_regression_daily`→`SKILL`).
  - `ALERT_ELIGIBILITIES: dict[ModelId, AlertEligibility]` — the SEPARATE alert-routing
    facet (M3 / ER-D1): skill ids→`SKILL_FORECAST`,
    `persistence_fallback`→`CURRENT_OBS_PROXY`,
    `climatology_fallback`→`NO_EVENT_INFORMATION`.
  `FALLBACK_MODEL_IDS: frozenset[ModelId]` is now **DERIVED FROM `MODEL_TIERS`**
  (`frozenset(mid for mid, t in MODEL_TIERS.items() if t is ModelTier.FALLBACK)`), NOT
  a second hand-maintained list that could diverge — `ModelId`-wrapped
  (`= NewType("ModelId", str)`, `types/ids.py:16`) per the existing sentinel pattern
  (`POOLED_MODEL_ID`/`BMA_MODEL_ID`/`CONSENSUS_MODEL_ID`, `:17-19`), NOT bare
  `frozenset[str]`. The *categorical* fact "is this a fallback model" is
  `MODEL_TIERS[model_id] is ModelTier.FALLBACK` (equivalently `model_id in
  FALLBACK_MODEL_IDS`), and **the identical categorical fact is used in the two places
  that previously computed the tier from the mutable DB `priority` — forecast
  combination and the B3 UI badge. Alert routing does NOT use this fact: it is a
  separate facet, `AlertEligibility`/`ALERT_ELIGIBILITIES` (M3 / ER-D1).**
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
  2. **B3 `ModelTier` badge** — `MODEL_TIERS[model_id]` (equivalently
     `ModelTier.FALLBACK if model_id in FALLBACK_MODEL_IDS else ModelTier.SKILL`) — a
     derived enum, not a raw `is_fallback: bool` (MAJOR-6).
  (Alert routing is intentionally NOT on this list — it reads the separate
  `AlertEligibility` facet, M3 / ER-D1, so a FORECAST flood alert can fire only for a
  `SKILL_FORECAST` model and neither fallback eligibility ever produces one; the
  earlier "M3 alert-suppression gate = `model_id in FALLBACK_MODEL_IDS`" item is
  DELETED as stale.)
  - **DB `assignment.priority` is DEMOTED to an ordering / tie-break key AMONG
    admitted skill models only** (the `min(priority)` primary-selection dispatch and
    combination ordering). It no longer decides *tier membership* anywhere — so a
    deliberate skill-tier override (`nwp_rainfall_runoff=-10`) still legitimately
    reorders which skill model wins primary, but no DB-priority value can ever move a
    model into or out of the fallback tier. This **dissolves the entire dual-source
    divergence** the earlier draft carried (the "trade-off accepted" paragraph is
    removed — there is no longer a runtime-vs-safety split to trade off; combination
    and the badge both read the one categorical `MODEL_TIERS` map, and alert routing
    reads `ALERT_ELIGIBILITIES`).
  - **Why this is drift-immune:** the whole incident was a mutable, siloed DB column
    silently drifting from the intended tier with nothing noticing. Keying the tier on
    the git-versioned `MODEL_TIERS` map makes the B3 badge **and** forecast combination
    immune to the entire class of DB-priority drift: a manual DB edit or any
    onboarding-path bug that mis-sets `priority` cannot un-badge a fallback and cannot
    blend it into a skill forecast. (Alert routing is separately drift-immune because it
    reads `ALERT_ELIGIBILITIES`, never `priority` — M3 / ER-D1.)
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
    **absent** entry is safe **for CLASSIFICATION ONLY**: the categorical
    `FALLBACK_MODEL_IDS` / `MODEL_TIERS` tier governs classification (M0c Decision 2),
    so classification never depends on the numeric value and an omitted fallback is
    still classified `FALLBACK`. **Omission is NOT safe for the assignment VALUE** —
    and the earlier claim that "an absent fallback simply falls back to the
    `DEFAULT_PRIORITY=50` ordering integer" is **DELETED as a self-contradiction**
    (FIX-A): a fallback must never be *assigned* at `50`, because the M0b write-guard
    raises on any `FALLBACK_MODEL_IDS` member written `<90`. The reconciliation is that
    the **assignment path (M0a repair, B1b backfill, M0b creation paths) assigns
    fallbacks at their canonical `≥90` priority via `FALLBACK_ASSIGNMENT_PRIORITIES`
    (the FIX-A canonical map), NEVER via `priority_for_model()`** — so the write-guard
    is **always satisfied** whether or not `[model_priorities]` lists the fallbacks. The
    config-load validator's sole job is to reject an **explicitly present,
    below-threshold** fallback priority (a genuine self-contradictory config, e.g.
    `climatology_fallback = 5`); an omitted fallback loads cleanly (classification is
    categorical) AND is still *assigned* at its canonical `≥90` value by the assignment
    path. This keeps M0/M0a/M0b/the write-guard/the M0c-validator mutually consistent —
    **no line implies a fallback assignment at `50` is acceptable.**
    Acceptance check 11 covers both cases: a config that prices a present
    `climatology_fallback = 5` raises; **a well-formed config that OMITS the fallback
    ids from `[model_priorities]` still loads cleanly** (and the assignment path still
    assigns them at their canonical `≥90` priority). (Test-migration note: if any test
    wants the fallbacks explicitly priced, add them to the config under test — but the
    default `make_deployment_config()` helper must continue to load without listing
    them.)
  - **Belt-and-suspenders (folded into M4/C1c):** M4's tripwire still flags any
    `model_assignments`/`group_model_assignments.priority` for a `FALLBACK_MODEL_IDS`
    member that has drifted below `FALLBACK_PRIORITY_THRESHOLD`, so a recurrence of the
    root cause is *visible* even though (post-Decision-2) it can no longer misclassify
    the tier. (M0c introduces a small **`ModelTier` enum** (`SKILL`/`FALLBACK`, see
    Decision 2 / MAJOR-6 below) — the values keyed by the `MODEL_TIERS` map — this
    is the value exposed on the badge/JSON schema, replacing a raw `is_fallback: bool`.
    Either declaration form is admissible under M0d: explicit membership in the central
    `MODEL_TIERS`/`ALERT_ELIGIBILITIES` maps, OR a per-model declared attribute — the
    M0d registry guard enforces that one of the two is present for every discovered
    model, so neither tier nor eligibility is ever defaulted by omission.)

### M0d — Registry / load-time tier guard (ER-D7, external review)

M0c keys the tier on the explicit `MODEL_TIERS` map (and derives `FALLBACK_MODEL_IDS`
from it). That is drift-immune for the *known* models, but a central map is still
**silent-by-default for a NEW model** if the map is consulted with a `.get(..., SKILL)`
style default: a future `*_fallback` / emergency / experimental model that is NOT added
to `MODEL_TIERS` + `ALERT_ELIGIBILITIES` would be treated as `SKILL` /
`SKILL_FORECAST` — combinable into the pooled forecast AND alert-eligible — the moment
it is discovered, exactly the "a fallback looks like a skill model" failure this plan
exists to kill, just relocated to onboarding time. Fix by making the lookups
**total, never defaulted**, enforced at a **registry / load-time guard** in
`services/model_registry.py` (`discover_models` / `register_models`,
`model_registry.py:27-88`):
- **Every discovered model must be present in BOTH explicit maps
  (`MODEL_TIERS` + `ALERT_ELIGIBILITIES`) OR declare both facets as attributes** — its
  `ModelTier` (`SKILL`/`FALLBACK`) **and** its `AlertEligibility`
  (`SKILL_FORECAST`/`CURRENT_OBS_PROXY`/`NO_EVENT_INFORMATION`, defined in M3 / ER-D1).
  `FALLBACK_MODEL_IDS` is derived from `MODEL_TIERS`, so populating the tier map is what
  admits a fallback to the fallback tier — there is no "skill by absence" anywhere.
- A model **present in neither map and declaring neither attribute** (i.e. an unknown
  tier OR eligibility) **fails loud at registry load/discovery** — raise
  `ConfigurationError` in `discover_models()` (`model_registry.py:27-88`, before the
  model is returned in the `discovered` dict), do **NOT** default to `SKILL` /
  `SKILL_FORECAST`, so the model cannot participate in forecast combination or alerting.
  This is a fail-closed default: an un-triaged new model cannot be combinable +
  alert-eligible by omission.
- `ModelTier` and `AlertEligibility` are the **two distinct facets** a model
  declares (ER-D1): `ModelTier` (from `MODEL_TIERS`) governs forecast-combination
  admission + the B3 UI badge; `AlertEligibility` (from `ALERT_ELIGIBILITIES`) governs
  the alert path (M3). Keep both; the registry guard enforces that **both** are declared
  for every model, and Acceptance check 16 asserts an undeclared model fails discovery.

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
    `nwp_enabled = weather_forecast_config.enabled` (`run_forecast_cycle.py:642`, in the
    gate region `:641-656`), `if adapter_config.require_nwp and not
    weather_forecast_config.enabled: raise ConfigurationError(...)`. This one check
    catches the unset-config case (1, where `enabled` is the hardcoded `False`), the
    explicit-`enabled=false` incident case (3), AND leaves the pre-existing
    unconditional STAC-missing raise (case 2) as-is. It reads the typed `require_nwp`
    field (parsed at the config boundary, below) — never a raw inline `os.environ.get`.
    **This fatal gate RAISES (owner decision, FIX-B):** `SAPPHIRE_REQUIRE_NWP=1` + NWP
    off → `raise ConfigurationError` → the Prefect `forecast-cycle` run **FAILS** loudly
    and returns **NO `ForecastCycleResult`**. Because the raise fires in the gate region
    (`:641-656`) **before** any per-station processing, it does **NOT** — and cannot —
    set `ForecastCycleHealth.FAILED`: a raised exception returns no result object to
    carry that field. `ForecastCycleHealth` (M5) is reserved for cycles that ENTER
    station processing and complete; the fatal config gate is a loud Prefect FAILURE
    instead, deliberately distinct from a dark-but-completed cycle (which DOES return a
    result carrying `ForecastCycleHealth.DEGRADED`/`FAILED`). **(Defense-in-depth note:**
    post-A2 the mini's overlay
    is `enabled=true`, so on the fixed mini this gate never fires — it is the structural
    backstop against any future re-introduction of `enabled=false`, exactly the class of
    change that caused the incident.) Acceptance check 4 covers case (1) and case (3);
    the former "`SAPPHIRE_CONFIG`-unset" residual is subsumed by the same single check.
  - **Post-hoc dark detection — now writes a FIRST-CLASS health record, not just a
    log (ER-D4, external review):** **promote the existing zero-forecast
    branches** — `fc_result is None` (`run_forecast_cycle.py:1082-1086`) and
    `primary_model_id is None` (`:1137-1141`) — from `log.warning` to **`log.error`
    + `errors.append(...)`**, emitting a **dotted** structlog event
    **`forecast_cycle.station_dark`** (per `docs/standards/logging.md`'s
    `{entity}.{action}` pattern — NOT a colon-separated free-text string). This
    already fires exactly when a station produces **zero** forecasts this cycle, for
    **any** reason (NWP off + inert floor, empty obs, a model bug) — no
    climatology-specific preflight, no new artifact-store query, and it cannot
    over-skip a station a healthy `persistence_fallback` would have served.
    - **A log line is NOT enough — it recreates "green pipeline, wrong answer".**
      Per ER-D4, a `station_dark` event ALSO **persists a queryable `pipeline_health`
      record** — via `PgPipelineHealthStore.append_health_record`
      (`store/pipeline_health_store.py:16`; `PipelineHealthRecord`, `types/pipeline.py:15`;
      the `pipeline_health` table, `db/metadata.py:1078`), **NOT** an
      `AlertSource.PIPELINE` row in the `alerts` table. The architecture deliberately
      separates operational health-metrics (`pipeline_health`, `docs/v0-scope.md:296`,
      `docs/architecture-context.md:2404`) from ops-alerts (`AlertSource.PIPELINE` in the
      `alerts` table, `:486`); a dark station is a health metric. Written **regardless of
      the `enable_*_alerts` flags** (all three default `False`,
      `config/deployment.py:103-105`) — this is operational-health, not a flood alert.
      Pinned (FIX-E): `check_type=FORECAST_STATION_DARK` (a **new** `PipelineCheckType`
      member, `types/enums.py:134-142`), `status=CRITICAL`, `subject=str(station_id)`,
      `detail={"reason", "assigned_models", "nwp_enabled"}`, `cycle_time=issue_time`.
      The record is surfaced in the dashboard/API alongside the
      other health records (ER-D4). (Only if an *active operator alert* is later deemed
      necessary would an `AlertSource.PIPELINE` alert row be added — and that would
      require defining its alert_level / status-lifecycle / dedup; the default here is
      the `pipeline_health` record.) Acceptance check 4 asserts the `station_dark`
      health record is written **and visible** for a dark station.
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
  - **Step 8 floor-gate — SEPARATE forecast-readiness from operational status +
    PHASED rollout (ER-D3, external review — SOFTENS the earlier "strict: stays
    NOT-operational immediately" stance):** the earlier draft tightened the locked
    `docs/v0-scope.md §A4` rule to a hard gate ("≥1 skill artifact AND an active
    `climatology_fallback` floor artifact" → else NOT operational) and applied it
    fleet-wide. The external reviewer flagged two hazards: (a) it would **flip
    currently-OPERATIONAL stations to NOT-operational at deploy** (a surprise fleet
    outage — the mirror image of the incident), and (b) it **hides a skilled-but-
    floorless station** entirely rather than serving its good forecasts with a warning.
    Owner decision:
    - **Decouple "forecast readiness / floor state" from station operational
      status.** Surface a **DERIVED `no_floor` indicator** (FIX-C, owner decision:
      DERIVED) — computed at query/render time from "does the station have an ACTIVE
      `climatology_fallback` artifact?" (the **same check the Step-8 floor-gate already
      runs**) — **NOT a persisted column, NO new `StationStatus` member, NO migration.**
      It is a computed dashboard/API badge, rather than ONLY flipping OPERATIONAL→NOT. A
      station with good skill forecasts but a failed/absent floor artifact **stays
      visible and serving**, badged `no_floor` — its degradation is *loud and queryable*
      (see ER-D4 health records), not silent, and not a blackout.
    - **Phased rollout, operator-visible at each step:**
      **audit** (fleet-wide floor audit, below) → **backfill** (train + promote the
      floor for every floorless OPERATIONAL station) → **verify** → **enforce the
      strict gate for NEW onboarding only** → **THEN** decide, per operator review,
      whether any *existing* station's operational status should change. New stations
      get the hard gate ("≥1 skill artifact AND an active floor artifact" → else NOT
      OPERATIONAL + loud ERROR); existing stations get the **DERIVED `no_floor` badge**
      (no status change, no persisted field, no migration) + reporting, and any status
      change happens **only after backfill + verify**, never as a surprise deploy-time
      flip.
    - **§A4 amendment scoped to NEW onboarding:** the owner-ratified (2026-07-06)
      tightening of `docs/v0-scope.md §A4` step 8 ("≥1 model artifact" → "≥1 skill
      artifact AND an active `climatology_fallback` floor artifact") **applies to NEW
      onboarding**. Existing stations are reconciled via the `no_floor` degraded state
      + the phased backfill, NOT the amended gate at deploy. The amendment text in
      `docs/v0-scope.md §A4` is updated at land time to say exactly this (new-onboarding
      gate; existing-station degraded-flag + phased rollout) — so the doc and code
      agree and no fleet-wide flip is implied (per CLAUDE.md "every code change updates
      affected docs" — see Process + acceptance checks 6 + 14).
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
  - **Climatology QA diagnostic (ER-D2, external review — additive):** at
    onboarding/backfill, after a `climatology_fallback` artifact is trained, check
    whether its quantiles/mean **recurringly exceed a configured danger threshold**
    for day-of-year periods. A seasonal baseline that is itself frequently above a
    danger level is a **signal**, not a warning: either the station has a genuinely
    hazardous seasonal baseline, or the threshold is mis-set. On a recurring crossing,
    emit a **station-threshold-review / config-QA item** — a `pipeline_health` record
    (`PgPipelineHealthStore.append_health_record`, `store/pipeline_health_store.py:16`),
    pinned (FIX-E) to `check_type=CLIMATOLOGY_THRESHOLD_REVIEW` (a **new**
    `PipelineCheckType` member), `status=WARNING`, `subject=str(station_id)`,
    `detail={"danger_threshold", "exceeding_quantiles", "doy_range"}` — **NOT** an
    `AlertSource.PIPELINE` alert row and
    **NOT a flood warning** (a climatology exceedance is `NO_EVENT_INFORMATION`, D1 — it
    must never become a flood alert). This is a diagnostic that runs at floor-training
    time only,
    not in the operational cycle; it flags the config/threshold for operator review.
    Acceptance check 17 asserts a station whose trained climatology recurringly crosses
    a danger threshold emits the config-review item and **no flood alert**.
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
- **M3-alert (AlertEligibility-driven routing — BLOCKER FIX, ER-D1, external
  review 2026-07-07):** there is **no flood-alert webhook/dispatch in v0** (alerts
  are logged + shown via API), so there is no "webhook payload" to label. The earlier
  design said **"suppress ANY fallback-only forecast alert"** (drop every `model_id ∈
  FALLBACK_MODEL_IDS` before Phase C). **The reviewer flagged this as UNSAFE and
  blocking:** with obs-alerts off, a gauge **observably above a danger threshold
  during an NWP outage** would yield **NO alert at all** — because `persistence_fallback`
  is a transformed **CURRENT observation** (`past_targets[param][-1]`,
  `models/persistence_fallback.py:82` — the latest measured value), and blanket-
  suppressing it drops the only signal that the river is *already* dangerous. That is
  the exact "green pipeline, wrong answer" failure this plan exists to kill, just moved
  to the alert path.
  - **Replace the blanket rule with an `AlertEligibility` classification (small enum,
    `types/enums.py`):** `SKILL_FORECAST`, `CURRENT_OBS_PROXY`, `NO_EVENT_INFORMATION`.
    Map: **skill models → `SKILL_FORECAST`**; **`persistence_fallback` →
    `CURRENT_OBS_PROXY`**; **`climatology_fallback` → `NO_EVENT_INFORMATION`**. This
    is an **alert-specific** classification, **DISTINCT from `ModelTier`**
    (`SKILL`/`FALLBACK`, which governs forecast-combination admission + the B3 UI
    badge). Keep BOTH facets: a model declares each (via `MODEL_TIERS` +
    `ALERT_ELIGIBILITIES`), and the M0d registry guard (ER-D7) enforces that both are
    declared (fail-loud on an undeclared model). The relationship: `ModelTier.FALLBACK`
    covers both `CURRENT_OBS_PROXY` and `NO_EVENT_INFORMATION` (both are non-skill for
    *combination*). For the **forecast-alert** path both are dropped (only
    `SKILL_FORECAST` alerts as `AlertSource.FORECAST`); the eligibility distinction is
    retained because it documents *why* each is dropped — `NO_EVENT_INFORMATION` carries
    no signal at all, whereas `CURRENT_OBS_PROXY` IS a real current-condition signal that
    is delivered on a **different channel** (Flow 2's `AlertSource.OBSERVATION` obs
    checker), not on the forecast path. It also scopes the future shared-helper note
    above to the `CURRENT_OBS_PROXY` case specifically.
  - **Alert routing, per (station, model) ensemble, at Phase C:**
    - **`SKILL_FORECAST` (skill models)** → alert normally via the existing forecast-
      alert path (`services/alert_checker.py`, `source=AlertSource.FORECAST`, called
      from `run_forecast_cycle.py` Phase C).
    - **`NO_EVENT_INFORMATION` (`climatology_fallback`)** → **NEVER raises a forecast
      flood alert** — always dropped from the forecast-alert set. A day-of-year
      seasonal average carries zero event information; it would trip the identical
      "alert" every year on that calendar day (pure false alarms).
    - **`CURRENT_OBS_PROXY` (`persistence_fallback`)** → **dropped from the
      forecast-alert set** (like climatology, no `AlertSource.FORECAST` alert). A
      persistence flat-line is a transformed CURRENT observation
      (`past_targets[param][-1]`), not a *skill forecast*, so it must never produce a
      FORECAST flood alert. **The forecast cycle does NOT itself re-route it to the
      observation path** — that routing is DELETED as redundant (targeted review,
      BLOCKER-1): current-condition detection ("the river is dangerous right now") is
      Flow 2's job, and the existing observation checker
      (`services/observation_alert_checker.py`) independently alerts on the latest
      QC-passed observation above a danger threshold — it fetches QC-passed obs (24h
      lookback), selects the latest, checks thresholds, and writes `AlertSource.OBSERVATION`
      (`observation_alert_checker.py:25,:52,:63,:73,:80`), wired under
      `enable_observation_alerts` (`flows/ingest_observations.py:366`). Because that flag
      is a HARD ship precondition (below), a dangerous current level is already covered
      by Flow 2 — a forecast-cycle re-route of persistence to `AlertSource.OBSERVATION`
      would only **duplicate** the obs checker's write, so it is not done.
    - **Note (future timing gap — out of scope now):** IF a concrete case is ever
      identified where Flow 1 (forecast cycle) runs and a hazardous current level would
      go unseen until Flow 2 (obs ingest) runs, it must be closed as a **SHARED helper**
      that both flows call and that **dedups through the existing active-alert upsert**
      (`AlertStore.upsert_alert`, `observation_alert_checker.py:80`) — never a parallel
      current-condition path in the forecast cycle. No such gap is claimed today, so no
      such helper is built here.
  - **HARD SHIPPING PRECONDITION (ER-D1):** the entire current-condition safety
    coverage — the only thing that alerts on an observably-dangerous gauge during an NWP
    outage — comes from Flow 2's observation checker, which is **gated on
    `enable_observation_alerts` (default `False`, `config/deployment.py:104` +
    `config.toml:26`)**. If that flag is off, a dangerous current level yields NO alert
    at all — recreating the exact gap this blocker fixes. Therefore
    **`enable_observation_alerts=true` MUST be set on the mac-mini / staging deployment
    config** (`config/overlays/mac-mini.toml`, alongside the A2 `enabled=true` edit) as
    a precondition of shipping Plan 100 — added to the A2 / deployment-config changes
    and the doc-update list. Acceptance check 8 asserts the mini config sets it. (This
    precondition is what makes it safe for the forecast cycle to DROP `CURRENT_OBS_PROXY`
    from the forecast-alert set without any parallel routing: the obs checker
    independently covers the current-condition case.)
  - **Partition ONCE, centrally, on `all_ensembles` immediately before the Phase-C
    dispatch — NOT inside any per-branch build:** `all_ensembles[sid]` is populated at
    **THREE** distinct sites (the dict is initialised at `run_forecast_cycle.py:956`).
    All three feed the same Phase-C dispatch and every one can carry a fallback
    ensemble; partitioning once — by **`AlertEligibility`**, not by a blanket
    `FALLBACK_MODEL_IDS` drop — keeps a single greppable enforcement point robust to
    which build site produced the dict:
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
    - **Fix (branch-independent, eligibility-partitioned — ER-D1):** exactly
      **once**, on the assembled `all_ensembles` dict, in the few lines **before**
      the Phase-C dispatch (`run_forecast_cycle.py:1441-1447`), **partition each
      (station, model) ensemble by `AlertEligibility`** (looked up from the model's
      declared facet, M0d — NOT `assignment.priority`, so no drifted DB priority can
      leak, M0c):
      - `SKILL_FORECAST` → **keep**; pass to the existing
        `check_station_alerts(all_ensembles=…)` forecast-alert dispatch
        (`AlertSource.FORECAST`).
      - `NO_EVENT_INFORMATION` (climatology) **and** `CURRENT_OBS_PROXY` (persistence)
        → **drop** from the forecast-alert set entirely — neither ever produces an
        `AlertSource.FORECAST` flood alert, and neither is pooled into one. Climatology
        carries zero event information; persistence is a current-observation proxy whose
        current-condition case is covered independently by Flow 2's obs checker (the
        enabled `enable_observation_alerts` path). The forecast-cycle partition does
        **NOT** re-route persistence to `AlertSource.OBSERVATION` and does **NOT** read
        any freshness / `observation_staleness_hours` metadata — the obs checker's own
        24h-latest logic governs obs alerts (existing behavior, unchanged by this plan).
      Partitioning here — not inside any of the three `all_ensembles` build sites
      (`:1111` PRIMARY / `:1207` combination / `:1414` GROUP) — makes the guard robust
      to which strategy produced the dict AND to any future population site, and keeps a
      single greppable enforcement point. In effect the partition reduces to "keep only
      `SKILL_FORECAST` ensembles for the forecast-alert dispatch; drop every non-skill
      eligibility". This supersedes both the earlier "build it the
      same way `combinable_results` filters" instruction (a combination-mode-only
      property that skipped the PRIMARY default) AND the interim
      "route fresh persistence to `AlertSource.OBSERVATION`" rule (redundant with Flow
      2's obs checker — targeted review, BLOCKER-1).
  - **Suppress when NO eligible signal remains — STATION-level granularity + a
    first-class health record (ER-D1 + ER-D4):** after partitioning, a station
    is genuinely suppressed for the forecast-alert path when it has **zero
    `SKILL_FORECAST` ensembles** — i.e. all it had was non-skill (climatology and/or
    persistence, both dropped from the forecast-alert set). In that case, **suppress
    forecast-alert evaluation for that station** and:
    (1) log the monitorable **dotted** structlog event **`alert.suppressed_fallback_only`**
    (per `docs/standards/logging.md`'s `{entity}.{action}` pattern — the canonical
    `alert` entity already has a `suppressed` action; NOT colon-separated free-text);
    **and (2) persist a queryable `pipeline_health` record**
    (`PgPipelineHealthStore.append_health_record`, `store/pipeline_health_store.py:16`;
    `PipelineHealthRecord`, `types/pipeline.py:15`), pinned (FIX-E) to
    `check_type=ALERT_SUPPRESSED_FALLBACK` (a **new** `PipelineCheckType` member),
    `status=WARNING`, `subject=str(station_id)`, `detail={"alert_eligibility",
    "parameter"}`, `cycle_time=issue_time` — NOT an `AlertSource.PIPELINE` alert
    row — capturing the suppressed cycle for audit (ER-D4), written **regardless of the
    `enable_*_alerts` flags** and surfaced in the dashboard/API. A log alone would
    recreate "green pipeline, wrong answer"; the health record makes the suppressed cycle
    queryable. Suppression detection stays at **STATION granularity** (matching the
    (station, model)-granularity partition — with the categorical partition a
    genuinely-suppressed station is empty for all its parameters at once). **Acceptance
    check 8 is scoped to station-level** (fully-suppressed station → no `Alert`, one
    `alert.suppressed_fallback_only` event, one `pipeline_health` record). Note this is
    NOT the same as "a dangerous current level goes unseen": if the latest observation is
    above threshold, Flow 2's obs checker still writes an `AlertSource.OBSERVATION` alert
    independently — the forecast-alert suppression here only means no *skill forecast*
    fired.
  - **Rationale:** climatology (`NO_EVENT_INFORMATION`) is a day-of-year seasonal
    average — zero event information, would trip the identical "alert" every year on
    that calendar day → pure false alarms → never a flood alert. Persistence
    (`CURRENT_OBS_PROXY`) is obs-grounded: "the level is dangerous **now**" is a real,
    actionable signal during an NWP outage, but it is a **current-condition** fact, not
    a *skill forecast* — so it must not be dressed up as an `AlertSource.FORECAST` alert.
    That current-condition signal is already delivered by Flow 2's observation checker
    (the enabled `enable_observation_alerts` path, `AlertSource.OBSERVATION`), so the
    forecast cycle simply drops persistence from the forecast-alert set rather than
    re-deriving the same alert. A **forecast** flood alert (`AlertSource.FORECAST`) comes
    only from a **skill** forecast (`SKILL_FORECAST`).
  - **Corollary:** after eligibility partitioning, any `AlertSource.FORECAST` alert is
    skill-sourced by definition; a dangerous current level surfaces as
    `AlertSource.OBSERVATION` via Flow 2; climatology surfaces as neither. No per-alert
    tier label is needed on the forecast path. (A persisted tier/suppression flag on the
    `Alert` record is a deferred v-next, not this plan.)
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
  > `expected_delivery_offset_hours × cadence` → emit a loud monitorable event
  (dotted `nwp.grid_stale`) on the **same channel A3's post-hoc detection uses**,
  **and persist a queryable `pipeline_health` record** (`grid_stale`, via
  `PgPipelineHealthStore.append_health_record`, `store/pipeline_health_store.py:16`),
  pinned (FIX-E) to the **existing** `check_type=PipelineCheckType.NWP_DELIVERY`
  (`types/enums.py:135` — no new member needed; it already covers NWP grid
  delivery/staleness), `status=CRITICAL`, `subject="nwp_grid"`,
  `detail={"last_grid_age_hours", "expected_offset_hours"}` —
  NOT an `AlertSource.PIPELINE` alert row —
  written **regardless of the `enable_*_alerts` flags**, surfaced in the dashboard/API
  (ER-D4 — not log-only). The **FULL watchdog** (source-outage detection,
  notification routing / dispatch) stays deferred to the **Flow-4 monitoring plan** —
  referenced, not built here.
- **C1c (fallback-priority drift tripwire — folded from M0c):** the same channel
  also emits a loud monitorable event if any `model_assignments.priority` **or**
  `group_model_assignments.priority` for `climatology_fallback` /
  `persistence_fallback` has drifted **below** `FALLBACK_PRIORITY_THRESHOLD`
  (`types/ids.py:20`). This is the ongoing detector for a recurrence of M0's exact
  root cause (a fresh DB edit / onboarding-path bug re-introducing the drift); it
  complements M0c's **categorical** `MODEL_TIERS` (which already makes combination and
  the B3 badge immune to the drift, while alert routing reads `ALERT_ELIGIBILITIES`, not
  `priority`) by making the DB drift itself *visible* rather than silent,
  and complements the M0b write-time guard (which blocks the app-layer write) by
  catching a drift introduced out-of-band (raw SQL / direct DB edit).

### M5 — First-class pipeline-health records + cycle-outcome semantics (ER-D4, external review)

The dark / suppressed / stale events above must **not be log-only** — a log line
recreates the very "green pipeline, wrong answer" failure this plan exists to kill
(a flow completes green while the feed is dark). Consolidate the Decision-4 treatment:
- **Persist queryable `pipeline_health` records** — via
  `PgPipelineHealthStore.append_health_record` (`store/pipeline_health_store.py:16`;
  `PipelineHealthRecord`, `types/pipeline.py:15`; the `pipeline_health` table,
  `db/metadata.py:1078`) — for the three operational-health events introduced above —
  `station_dark` (zero forecasts this cycle, A3), `alert_suppressed` (fully-suppressed
  fallback-only cycle, M3), and `nwp.grid_stale` (M4/C1b). **These are `pipeline_health`
  records, NOT `AlertSource.PIPELINE` rows in the `alerts` table.** The architecture
  deliberately separates operational health-metrics (`pipeline_health`,
  `docs/v0-scope.md:296`, `docs/architecture-context.md:2404`) from ops-alerts
  (`AlertSource.PIPELINE` in the `alerts` table, an *active* operator alert with a
  notification lifecycle, `docs/architecture-context.md:486`). A dark/suppressed/stale
  event is a health metric, so it goes to `pipeline_health`. **Each signal maps to ONE
  pinned `(check_type, status, subject, detail)` — no "or/e.g." ambiguity (FIX-E;
  `PipelineHealthStatus` verified `OK`/`WARNING`/`CRITICAL`, `types/enums.py:128-133`;
  `PipelineHealthRecord` fields = `check_type, checked_at, status, subject, detail,
  cycle_time, created_at`, `types/pipeline.py:15`):**
  - **`station_dark`** (A3, zero forecasts this cycle) → **new `PipelineCheckType`
    `FORECAST_STATION_DARK`**, `status=CRITICAL`, `subject=str(station_id)`,
    `detail={"reason", "assigned_models", "nwp_enabled"}`, `cycle_time=issue_time`.
  - **`alert_suppressed`** (M3, fully-suppressed fallback-only cycle) → **new
    `PipelineCheckType` `ALERT_SUPPRESSED_FALLBACK`**, `status=WARNING`,
    `subject=str(station_id)`, `detail={"alert_eligibility", "parameter"}`,
    `cycle_time=issue_time`.
  - **`nwp.grid_stale`** (M4/C1b) → the **existing `PipelineCheckType.NWP_DELIVERY`**
    (`types/enums.py:135` — it already covers NWP grid delivery/staleness; no new member
    needed), `status=CRITICAL`, `subject="nwp_grid"`,
    `detail={"last_grid_age_hours", "expected_offset_hours"}`.
  - **M0a migration-audit** (ER-D6) → **new `PipelineCheckType`
    `PRIORITY_MIGRATION_AUDIT`**, `status=OK`, `subject="m0a_priority_reconciliation"`,
    `detail={"rows_changed", "triaged_overrides", "backup_reference"}`.
  - **climatology-QA** (ER-D2) → **new `PipelineCheckType`
    `CLIMATOLOGY_THRESHOLD_REVIEW`**, `status=WARNING`, `subject=str(station_id)`,
    `detail={"danger_threshold", "exceeding_quantiles", "doy_range"}`.
  The four NEW members (`FORECAST_STATION_DARK`, `ALERT_SUPPRESSED_FALLBACK`,
  `PRIORITY_MIGRATION_AUDIT`, `CLIMATOLOGY_THRESHOLD_REVIEW`) are added to
  `PipelineCheckType` (`types/enums.py:134-142`, which today holds `NWP_DELIVERY`,
  `OBSERVATION_FRESHNESS`, `FORECAST_FRESHNESS`, `FLOW_RUN_HEALTH`, `DISK_USAGE`,
  `BACKUP_FRESHNESS`, `BACKUP_RESTORE_TEST`); `NWP_DELIVERY` is reused as-is. **Written regardless of the `enable_*_alerts` flags**
  (all three default `False`, `config/deployment.py:103-105`) — health metrics are never
  gated on `enable_pipeline_alerts`. **Only if an ACTIVE operator alert is explicitly
  wanted** would an `AlertSource.PIPELINE` alert row be created in addition — and that
  path would have to define its `alert_level` / status-lifecycle / dedup; **the default
  for Plan 100 is the `pipeline_health` record.** **Surface them in the dashboard / API**
  next to the forecast/alert views so an operator sees a dark or suppressed station
  without reading logs. (The M0a migration-audit record, ER-D6, uses the same
  `pipeline_health` channel.)
- **Cycle-outcome semantics — define DEGRADED vs FAILED vs HEALTHY on a NEW
  `ForecastCycleHealth` field, NOT on `FlowRunState`:** so a dark/suppressed outcome is
  not silently "green". `FlowRunState` (`types/enums.py:199-206`) is a **direct mapping
  of Prefect's `StateType`** (`adapters/prefect_status.py:28`, `_STATE_MAP`), and Prefect
  has **no** degraded state — extending `FlowRunState` with `DEGRADED` would put a member
  in it that no Prefect state maps to, breaking that contract. Instead, introduce a
  **separate `ForecastCycleHealth` (a.k.a. `ForecastCycleOutcome`) enum**
  (`DEGRADED`/`FAILED`/`HEALTHY`) as a field on `ForecastCycleResult`
  (`run_forecast_cycle.py:89-97`, alongside `stations_succeeded`/`stations_failed`/`errors`)
  and on the API cycle-result response, **derived** from the cycle. **These three
  outcomes are set ONLY for a cycle that ENTERS station processing and COMPLETES the
  Prefect task (FIX-B); the fatal A3 config gate is NOT covered by this field — it
  RAISES `ConfigurationError` before processing, so the Prefect run FAILS and no
  `ForecastCycleResult` is returned to carry a health value.** Concrete semantics:
  **HEALTHY** = every station served by a live model, no suppression, no stale grid;
  **DEGRADED** = the cycle completed but ≥1 station is dark, an alert was suppressed, or
  a grid is stale (a partial degradation); **FAILED** = a **cycle-wide** failure that
  STILL completes the Prefect task — e.g. **every** station dark (zero forecasts
  fleet-wide) — reported on the returned `ForecastCycleResult`. **The Prefect run state
  stays `COMPLETED`** when the task completed (do NOT extend `FlowRunState`) — the
  `ForecastCycleHealth` field is what turns the incident's "green every 6 h while dark"
  into a visible DEGRADED/FAILED outcome. (An NWP-fetch failure or the A3 gate that
  *raises* likewise FAILS the Prefect run with **no** result; only failures that still
  return a `ForecastCycleResult` carry `ForecastCycleHealth.FAILED`.)
- **The FULL watchdog** (source-outage detection, notification routing / dispatch)
  stays deferred to the **Flow-4 monitoring plan** — Plan 100 writes + surfaces the
  records; Flow 4 owns dispatch. (See the Process note on a possible follow-up split.)
- Acceptance checks 4 + 8 + 18 assert these `pipeline_health` records are written **and
  visible** for a dark station, a suppressed cycle, and a stale-grid condition, and that
  a degraded cycle reports `ForecastCycleHealth.DEGRADED` (while the Prefect run stays
  COMPLETED).

### Acceptance checks (what WF2 must make pass)

1. **Priorities reconciled (M0):** after M0a, **both** `model_assignments.priority`
   **and** `group_model_assignments.priority` for the fallbacks equal their
   **canonical** values (climatology=100, persistence=90) for all stations/groups —
   resolved from the `FALLBACK_ASSIGNMENT_PRIORITIES` map (FIX-A), NOT
   `priority_for_model()`, so they land in-tier **even under a deployment config that
   omits the fallbacks**; **every non-allowlisted skill row converges to its config
   value** — specifically the
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
   **M0a safety envelope ran (ER-D6):** maintenance mode / advisory lock held for
   the mutation (no forecast-cycle or onboarding interleaved), a DB backup was taken, a
   `(station_id, model_id, old→new)` dry-run diff was produced + signed off with every
   non-config divergence triaged, the rewrite applied in one transaction, and a
   migration-audit record (ER-D4 channel) was persisted.
2. **Restart persistence (A2):** cold-boot the mini via `start-sapphire.sh` → a
   `forecast-cycle` runs NWP-on (`nwp.*` logs, grids archived), no manual overlay,
   no `-nwp` file anywhere; `docs/deployment/mac-mini-staging.md` no longer
   documents a toggle.
3. **Floor writes + is labelled (B1a/B3):** station with an NWP model + the floor,
   stack NWP-off → a `climatology_fallback` row **IS** written and **badged
   FALLBACK**. Flip NWP-on → the skill model becomes primary, row **not** badged.
4. **A3 fatal gate RAISES vs post-hoc dark detection (FIX-B — SPLIT into 4a/4b):**
   - **(4a) `SAPPHIRE_REQUIRE_NWP=1` + NWP off → the fatal gate RAISES → the Prefect
     run FAILS:** the single general check `require_nwp and not
     weather_forecast_config.enabled` (in the gate region `run_forecast_cycle.py:641-656`,
     right after `nwp_enabled = weather_forecast_config.enabled` at `:642`) fires
     **whenever NWP is required but `weather_forecast_config.enabled` is `False`** —
     covering BOTH the `SAPPHIRE_CONFIG`-unset case (hardcoded `enabled=False`) **and the
     explicit `enabled=false`-in-merged-TOML case that was the actual historical
     incident** (the earlier two enumerated conditions missed the latter). It **`raise`s
     `ConfigurationError` → the Prefect `forecast-cycle` run FAILS**; assert the raise
     and that **NO `ForecastCycleResult` is returned** (a raised exception carries no
     result, so this fatal gate **NEVER** sets `ForecastCycleHealth.FAILED`). The
     pre-existing unconditional STAC-missing raise (`:648-656`) still covers the
     adapter-unbuildable case independently. The env var is read through the config
     boundary (`_WeatherForecastAdapterConfig.require_nwp`, not an inline
     `os.environ.get`), and `docker-compose.macmini.yml` sets `SAPPHIRE_REQUIRE_NWP=1`
     **on the `prefect-worker` service** (`:24`) — the `default`-pool worker that runs
     `forecast-cycle`, NOT `prefect-worker-ingest` (`:32`) — so the gate is live on the
     deployed mini.
   - **(4b) NWP off but NOT required (or `require_nwp` unset) + a station yields zero
     forecasts → the flow does NOT abort:** a station that produces zero forecasts is
     logged at **ERROR** (dotted event `forecast_cycle.station_dark`), appears on
     `errors`/`stations_failed` (not a silent warning), and ALSO writes a queryable
     **`pipeline_health` record** (`FORECAST_STATION_DARK`, status CRITICAL, via
     `PgPipelineHealthStore.append_health_record` — NOT an `AlertSource.PIPELINE` alert
     row; written regardless of the `enable_*_alerts` flags) **visible in the
     dashboard/API**, while **every other station still forecasts (flow does NOT abort)**
     and the cycle COMPLETES the Prefect task carrying **`ForecastCycleHealth.DEGRADED`**
     (a NEW field on `ForecastCycleResult`, while the Prefect run state stays
     `COMPLETED` — `FlowRunState` is NOT extended) — not merely a log line. This
     dark-but-completed case is deliberately distinct from (4a)'s fatal raise.
5. **Backfill effective + idempotent + fleet-clean (B1b):** 2009/2091 have active
   climatology artifacts + assignments at the correct priority; a NWP-off
   `forecast-cycle` writes fallback rows for both. Re-run of the backfill = no-op.
   **The fleet-wide floor audit** (all OPERATIONAL non-weather stations lacking an
   active `climatology_fallback` artifact) **returns zero** after the backfill —
   i.e. no station beyond 2009/2091 is silently floor-less.
6. **Floor-gate PHASED + `no_floor` state (B1b / ER-D3):** (a) **NEW onboarding
   hard gate:** onboard a test station whose `climatology_fallback` training is forced
   to fail → station **NOT** OPERATIONAL and the onboarding run reports **non-green**
   (not swallowed); floor training succeeding → OPERATIONAL. (b) **Existing station
   degraded, not flipped:** an already-OPERATIONAL station whose floor artifact is
   absent/failed **stays OPERATIONAL and serving** but is flagged **`no_floor`**
   (a DERIVED indicator computed from active-`climatology_fallback`-artifact presence —
   NO persisted field/column/migration, NO `StationStatus` member; a computed
   dashboard/API badge) and reported — it is NOT silently flipped to NOT-operational at
   deploy. (c) Any existing-station status change happens **only
   after** the audit→backfill→verify phases, operator-visible at each step. Existing
   onboarding tests updated for the split.
7. **persistence empty-obs (B2):** `persistence_fallback.predict` on empty obs
   **raises the narrow `InsufficientObservationsError`** (not a bare `IndexError`),
   which the `_run_single_model` backstop (`run_station_forecast.py:174-208`) turns
   into a graceful "predict failed" reason → the chain advances; climatology still
   produces. (No "return `ModelFailure`" — the native protocol has no such return.)
8. **AlertEligibility routing (M3-alert / BLOCKER ER-D1) — REPLACES the old
   "blanket suppress" check:** the mini config sets **`enable_observation_alerts=true`**
   (`config/overlays/mac-mini.toml`) so the current-condition path is live (HARD ship
   precondition).
   - **(a) dangerous current level IS still alerted — via Flow 2, NOT the forecast
     cycle (the blocker's core case):** with the **latest QC-passed observation above a
     danger threshold + NWP off + fallback-only forecast**, an **`AlertSource.OBSERVATION`
     alert record IS written by the observation checker** (`check_observation_alerts`,
     `observation_alert_checker.py:80`, wired at `flows/ingest_observations.py:366`)
     because `enable_observation_alerts=true` is a HARD ship precondition. The forecast
     cycle itself does NOT write this alert (it drops `CURRENT_OBS_PROXY` from the
     forecast-alert set with no re-routing) — but the gauge is not silently dark, which
     is the exact regression the old blanket-suppress rule would have caused (gauge
     observably dangerous, zero alert). Assert an `AlertSource.OBSERVATION` alert exists,
     and that the forecast cycle writes no duplicate.
   - **(b) climatology-only OR persistence-only exceedance writes NO forecast flood
     alert:** a fallback-only (`NO_EVENT_INFORMATION` climatology and/or
     `CURRENT_OBS_PROXY` persistence) exceedance under the shipped-default config
     (`forecast_combination_strategy=PRIMARY` + `alert_model_strategy="primary"`,
     `config.toml:29`) — including the `all_ensembles[sid] = {fc_result.model_id: …}`
     PRIMARY build (`run_forecast_cycle.py:1111`) + `n_models<=1` shortcut
     (`alert_checker.py:212-213`) — **fires no `AlertSource.FORECAST` `Alert`** and logs
     `alert.suppressed_fallback_only` + writes one `alert_suppressed` **`pipeline_health`
     record** (ER-D4). (Any `AlertSource.OBSERVATION` alert from Flow 2 is separate — it
     is not a forecast alert.)
   - **(c) persistence never produces a FORECAST alert:** a `persistence_fallback`
     exceedance in the forecast cycle → **no `AlertSource.FORECAST` alert** regardless of
     its source-obs freshness (the forecast cycle reads no `observation_staleness_hours`
     metadata) — it is dropped from the forecast-alert set. Its current-condition case is
     covered independently by Flow 2 (case a).
   - **(d) skill alerts normally:** a `SKILL_FORECAST` exceedance under the same default
     writes an `AlertSource.FORECAST` alert.
   - **(e) no pooled contamination:** a mixed POOLED/BMA/CONSENSUS cycle (skill + a
     guaranteed climatology floor) computes the exceedance from the **skill ensemble
     only** — climatology and persistence members never enter the pooled/BMA calculation
     (`:1207-1210` build); neither is pooled into a FORECAST alert.
   All cases — and the GROUP build (`:1414`) — pass because the partition-by-
   `AlertEligibility` runs **once, centrally** on `all_ensembles` before Phase-C dispatch
   (`:1441-1447`), independent of which of the three sites populated the dict. The
   suppression assertion (b) is scoped to **STATION-level** (fully-suppressed station →
   zero `AlertSource.FORECAST` `Alert` + one `alert.suppressed_fallback_only` + one
   `alert_suppressed` `pipeline_health` record), NOT per-parameter.
9. **C1 staleness fires (M4):** after C1a plumbing, force a grid-archival gap past
   `expected_delivery_offset_hours × cadence` → the staleness event fires (proves
   it is not dead code).
10. **Mini-state root-cause capture is a STEP-0 PREREQUISITE (ER-D5) — not a
    closure check:** BEFORE any M0/M2 mutation, **immutable snapshots** of the mini's
    `model_assignments` + `group_model_assignments` (priorities/`time_step`/status),
    artifact status, blackout-window forecasts + `Alert` rows, config env, and NWP-
    archive state were captured out-of-band, and dry-run diagnostics recorded which
    candidate gap (unassigned floor / inert artifact / priority drift / `enabled=false`
    gate) was live at incident time. The check passes iff the snapshots exist and the
    mechanism is still provable from them AFTER the fix ships (the mutation cannot have
    destroyed the evidence).
11. **M0c config-load validator fails loud (FIX-A):** a `config.toml` that **explicitly
    prices** any `FALLBACK_MODEL_IDS` member below `FALLBACK_PRIORITY_THRESHOLD` (e.g.
    `climatology_fallback = 5`) raises `ConfigurationError` at config load, not a
    silent runtime misclassification. A well-formed config loads cleanly — **including a
    config that OMITS the fallbacks from `[model_priorities]`** (classification is
    categorical), and in that omitted case the assignment path still assigns the
    fallbacks at their canonical `≥90` priority via `FALLBACK_ASSIGNMENT_PRIORITIES` (so
    no fallback is ever assigned at `DEFAULT_PRIORITY=50` and the write-guard is
    satisfied).
12. **Write-time guard raises (M0b):** calling `create_station_assignment` /
    `create_group_assignment` (`services/model_onboarding.py:838-895`) — including via
    `onboard_model_flow` — with `model_id ∈ FALLBACK_MODEL_IDS` and
    `priority < FALLBACK_PRIORITY_THRESHOLD` **raises** rather than writing the row;
    a fallback at `>=90` and any skill model write normally.
13. **C1c fallback-priority drift tripwire fires (M4):** manually drift a
    `climatology_fallback` / `persistence_fallback` row in `model_assignments` **or**
    `group_model_assignments` below `FALLBACK_PRIORITY_THRESHOLD` → the tripwire emits
    its loud monitorable event (proving the DB-drift detector is not dead code), even
    though M0c's categorical `MODEL_TIERS` already keeps the B3 badge + combination
    correct and alert routing reads `ALERT_ELIGIBILITIES` (never `priority`).
14. **v0-scope §A4 amendment applied, scoped to NEW onboarding (B1b / ER-D3):**
    `docs/v0-scope.md §A4` step 8 is updated to the ratified rule ("≥1 skill artifact
    AND an active `climatology_fallback` floor artifact") **for NEW onboarding**, with
    the doc explicitly stating existing stations are reconciled via the `no_floor`
    degraded state + phased backfill (NOT a deploy-time fleet flip) — the doc and code
    agree, no silent divergence and no implied surprise fleet-wide status change.
15. **Retroactive alert audit clean (M3-alert / Goal #4):** the one-time query over
    all `FORECAST`-source `Alert` rows (`types/alert.py:19-35`) whose `model_ids`
    intersects `FALLBACK_MODEL_IDS` — restricted to still-ACTIVE/unresolved rows —
    either **returns zero** on the live system, or every returned row is surfaced for
    operator review/resolution before the plan closes. No historical fallback-sourced
    alert is left silently trusted.
16. **Registry / load-time tier guard fails loud (M0d / ER-D7):** a discovered
    model absent from BOTH explicit maps (`MODEL_TIERS` + `ALERT_ELIGIBILITIES`) and
    declaring neither `ModelTier` nor `AlertEligibility` as an attribute **raises
    `ConfigurationError` in `discover_models()`** (`services/model_registry.py:27-88`) —
    it does NOT default to `SKILL` / `SKILL_FORECAST` (no skill-by-absence) and cannot
    participate in combination or alerting. A model present in both maps (or declaring
    both facets) loads normally, and `FALLBACK_MODEL_IDS` is derived from `MODEL_TIERS`.
17. **Climatology QA diagnostic (M2 / ER-D2):** a station whose trained
    `climatology_fallback` quantiles/mean recurringly cross a configured danger
    threshold for day-of-year periods emits a **config / threshold-review item** — a
    `pipeline_health` record with pinned `check_type=CLIMATOLOGY_THRESHOLD_REVIEW`
    (WARNING, `subject=str(station_id)`), NOT an `AlertSource.PIPELINE` alert row — at
    onboarding/backfill, and **no flood alert** — flagging the seasonal baseline /
    threshold for operator review.
18. **Pipeline-health records + cycle-outcome (M5 / ER-D4 / FIX-E):** the `station_dark`
    (`FORECAST_STATION_DARK`, CRITICAL), `alert_suppressed` (`ALERT_SUPPRESSED_FALLBACK`,
    WARNING), and `nwp.grid_stale` (`NWP_DELIVERY`, CRITICAL) records are persisted as
    **`pipeline_health` records** with the pinned `(check_type, status, subject, detail)`
    (via `PgPipelineHealthStore.append_health_record`, NOT `AlertSource.PIPELINE` alert
    rows) **regardless of the `enable_*_alerts` flags** and are **visible in the
    dashboard/API**; a cycle with a dark or suppressed station (but not fleet-wide)
    reports **`ForecastCycleHealth.DEGRADED`** (a NEW field on `ForecastCycleResult`; the
    Prefect run stays `COMPLETED` and `FlowRunState` is NOT extended), and a **cycle-wide**
    failure that STILL completes the Prefect task (e.g. **every** station dark) reports
    **`ForecastCycleHealth.FAILED`** on the returned result. **The fatal A3 config gate is
    NOT asserted here** — it RAISES with no result (FIX-B, acceptance check 4a); this
    check covers only cycles that complete and return a `ForecastCycleResult`.
19. **Minimal health visibility wired (FIX-D):** `make_pg_stores()` (`flows/_db.py`)
    includes `PgPipelineHealthStore`; `run_forecast_cycle_flow` takes a
    `pipeline_health_store` param (production-bound from `stores`) and its `station_dark`
    / `alert_suppressed` / `nwp.grid_stale` events call `append_health_record` through
    it; `get_stores()` (`api/deps.py`) exposes the store; and a minimal read-only route +
    `PipelineHealthRecordResponse` schema + template renders the recent `pipeline_health`
    records (via `fetch_recent`) so a dark/suppressed/stale condition is **visible in the
    dashboard/API without reading logs**.

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
4. **Fallback forecasts never masquerade as skill, but a real current-condition
   hazard is never silently dropped** — fallbacks are labelled in the UI/API (B3
   `ModelTier`); a **skill forecast** is the only basis of an `AlertSource.FORECAST`
   flood alert; climatology (`NO_EVENT_INFORMATION`) and persistence
   (`CURRENT_OBS_PROXY`) never raise a forecast flood alert; a dangerous current level is
   covered independently by the **enabled observation-alert path** (Flow 2's obs checker,
   `AlertSource.OBSERVATION`) — a HARD ship precondition — NOT by any forecast-cycle
   re-routing (ER-D1 / `AlertEligibility`).

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
  See M0 above. M0c makes the fallback/skill tier a categorical `MODEL_TIERS` map (from
  which `FALLBACK_MODEL_IDS` is derived) — the single source for forecast combination and
  the B3 badge ONLY, demoting DB `priority` to ordering/tie-break. **Alert routing is a
  SEPARATE facet, `AlertEligibility`/`ALERT_ELIGIBILITIES`, not `FALLBACK_MODEL_IDS`** (a
  FORECAST flood alert fires only for `SKILL_FORECAST`; both fallback eligibilities are
  dropped from the forecast-alert set). M0a/M0b reconcile and guard the priority column;
  M0d fails loud on any model not declared in both maps.
- **B1a — DECIDED:** both fallbacks; guarantee keyed on
  `climatology_fallback`-with-active-artifact (post-M0, priority 100).
- **B1b — DECIDED (PHASED floor-gate + DERIVED `no_floor` indicator, no migration;
  ER-D3 — NOT a fleet-wide deploy-time flip):** the owner-ratified (2026-07-06) tightening of the
  locked `docs/v0-scope.md §A4` rule ("≥1 model artifact" → "≥1 skill artifact AND an
  active `climatology_fallback` floor artifact") is a **hard gate for NEW onboarding
  ONLY** (a new station without an active floor artifact → NOT OPERATIONAL + loud ERROR,
  applied to that doc at land time). **Existing stations are NOT flipped
  OPERATIONAL→NOT at deploy:** a currently-OPERATIONAL station lacking a floor artifact
  **stays serving** but carries a **DERIVED `no_floor` indicator** (FIX-C — computed
  from active-`climatology_fallback`-artifact presence at query/render time; NO persisted
  field, NO `StationStatus` member, NO migration; a computed dashboard/API badge) and is
  reported, and any status change
  happens **only** after the phased **audit → backfill → verify** sequence, operator-
  visible at each step — never a surprise fleet-wide flip. Independently: Step 7 does not
  swallow floor-training failure; `flows/onboard.py` surfaces it non-green; floor
  plugin-load/absence is loud; onboarding tests budgeted; idempotent backfill for
  2009/2091.
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
- **Alert routing by `AlertEligibility` — DECIDED (owner, 2026-07-07; BLOCKER
  ER-D1, external review — SUPERSEDES the earlier "blanket drop every
  `model_id ∈ FALLBACK_MODEL_IDS`" rule, which was UNSAFE):** in **one** central
  partition on `all_ensembles` before Phase-C dispatch — covering all THREE build sites
  (PRIMARY `:1111`, combination `:1207`, GROUP `:1414`) and every strategy — classify
  each (station, model) ensemble by `AlertEligibility`: **skill (`SKILL_FORECAST`) is
  kept** and alerts via `AlertSource.FORECAST`; **both non-skill eligibilities —
  climatology (`NO_EVENT_INFORMATION`) and persistence (`CURRENT_OBS_PROXY`) — are
  dropped** from the forecast-alert set (neither ever a flood alert, nor pooled into
  one). The forecast cycle does **NOT** re-route persistence to `AlertSource.OBSERVATION`
  and reads **no** freshness / `observation_staleness_hours` metadata (targeted review,
  BLOCKER-1/BLOCKER-2): a dangerous current level is delivered independently by Flow 2's
  observation checker (`observation_alert_checker.py:80`, 24h-latest logic), which is why
  `enable_observation_alerts=true` is a HARD ship precondition. Suppression (no skill
  ensemble left for a station) logs `alert.suppressed_fallback_only` + writes an
  `alert_suppressed` **`pipeline_health` record** (ER-D4, NOT an `AlertSource.PIPELINE`
  alert row). This keeps "the river is dangerous right now" visible during an NWP outage
  via Flow 2 (the blocker: blanket-suppress would have yielded zero alert on an
  observably-dangerous gauge with obs-alerts off), still refuses to dress a fallback up
  as a *skill* flood alert, and closes the pooled-contamination path
  (`PooledEnsembleStrategy` ignores `priorities`, `all_ensembles` built from
  `multi_result.results`, not `combinable_results`). **HARD ship precondition:
  `enable_observation_alerts=true` on the mini** so the current-condition path is live.

## Residual forks / follow-ups (post-100)

All plan-review round-3 forks are now RESOLVED (round-3 Decision 4). The 2026-07-07
external review (ER-D1…ER-D7) added scope — see the Process **growth note** flagging
the one sub-part the owner may want to split to a follow-up plan. Residuals still
open: the DEFERRED persisted-column work (needs a migration), the DB-level
`server_default` tightening (belt-and-suspenders, tracked by C1c), and the
health-record dashboard/API surfacing that overlaps Flow 4 (candidate split).

- **[OPEN — belt-and-suspenders, tracked by C1c] DB `priority` `server_default="0"`
  gap:** `model_assignments.priority` (`alembic/versions/0001_v0_schema.py:405`) and
  `group_model_assignments.priority` (`0021_add_group_model_assignments.py:35`) both
  carry `server_default="0"`, so a raw SQL INSERT that omits `priority` lands a row at
  the skill tier `0` — the same drift signature the incident showed, on any code path
  that bypasses the M0b app-layer write-time guard. **Tightening the DB DEFAULT is
  out of scope for Plan 100** (M0c's categorical `MODEL_TIERS` already makes the B3 badge
  + combination immune to the drift, alert routing reads `ALERT_ELIGIBILITIES`, and
  C1c's tripwire makes any such out-of-band row *visible*);
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
- **[CANDIDATE SPLIT — ER-D4] Pipeline-health-record dashboard/API surfacing** — the
  M5 `station_dark`/`alert_suppressed`/`grid_stale` records (write + surface) partly
  overlap the deferred **Flow-4 monitoring** plan. Plan 100 writes the records + the
  minimal dashboard/API surfacing needed to close the incident; if the surfacing grows,
  the owner may split the richer surfacing (and any notification dispatch) into the
  Flow-4 plan. Flagged, **not** split here (see Process growth note).

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

See **Acceptance checks** (19) in the IMPLEMENTATION VISION. Local dev repro:
onboard a test station with an NWP model + the floor, run a `forecast-cycle`
NWP-off → confirm a `climatology_fallback` row is written, badged fallback, that a
**climatology-only** exceedance fires **no** flood alert, but a **fresh
above-threshold observation** DOES write an `AlertSource.OBSERVATION`
current-condition alert (ER-D1); flip NWP-on → skill model becomes primary. Cold-boot
the mini via `start-sapphire.sh` → NWP-on unconditionally, no `-nwp` file.

## Process

grill-me COMPLETE + **three `plan-review` rounds folded** + **an external
specialized (hydrology + production-reliability) reviewer pass folded (2026-07-07,
SHIP-WITH-CHANGES: 1 blocker + several majors → ER-D1…ER-D7)**.

**GROWTH NOTE (external review):** Plan 100 has GROWN materially with ER-D1…ER-D7
(a new `AlertEligibility` model — climatology + persistence both dropped from the
forecast-alert set, current-condition coverage via Flow 2's existing obs checker — a
climatology-QA diagnostic, a DERIVED `no_floor` indicator (no migration) + phased floor-gate rollout,
first-class `pipeline_health` records + a `ForecastCycleHealth` cycle-outcome field, a
step-0 snapshot prerequisite, an M0a fleet-mutation safety envelope, and a registry tier
guard). **Candidate follow-up split (owner to decide, NOT split here):** the **M5
`pipeline_health`-record dashboard/API surfacing** (ER-D4) overlaps the deferred
**Flow-4 monitoring** plan —
Plan 100 keeps the minimal write + surface needed to close the incident; the richer
surfacing / notification dispatch could move to Flow 4. No sub-part is split by this
edit.

Next: **re-run `plan-review`** to confirm the folded decisions (the categorical
`MODEL_TIERS` tier + derived `FALLBACK_MODEL_IDS`, write-time guard, v0-scope §A4
amendment, A3 post-hoc + `SAPPHIRE_CONFIG` gate, plus the seven external-review
decisions: `AlertEligibility` routing (climatology + persistence both dropped from the
forecast-alert set; current-condition via Flow 2), climatology-QA diagnostic, phased
floor-gate + `no_floor`, first-class `pipeline_health` records + `ForecastCycleHealth`
outcome, step-0 snapshot, M0a safety envelope, registry tier guard) converge with no new
blockers/majors → phases → READY → `vision-build`
(WF2). Implementation touches:
- **`types/ids.py`** — new **explicit `MODEL_TIERS: dict[ModelId, ModelTier]`** and
  **`ALERT_ELIGIBILITIES: dict[ModelId, AlertEligibility]`** maps next to
  `FALLBACK_PRIORITY_THRESHOLD`, enumerating **every known model** (MAJOR-4 — no
  skill-by-absence). **`FALLBACK_MODEL_IDS: frozenset[ModelId]` is DERIVED from
  `MODEL_TIERS`** (`frozenset(mid for mid, t in MODEL_TIERS.items() if t is
  ModelTier.FALLBACK)`), NOT a second hand-listed constant — `ModelId`-wrapped per the
  existing sentinel-constant pattern (`:16-19`), not bare `frozenset[str]`. **Plus a
  small `ModelTier` enum (`SKILL`/`FALLBACK`)** (in `types/ids.py` or `types/enums.py`) —
  the value keyed by `MODEL_TIERS`, exposed on the B3 badge/JSON schema in place of a raw
  `is_fallback: bool` (MAJOR-6 / CLAUDE.md enum-over-bool). `MODEL_TIERS` governs
  combination + the badge ONLY; `ALERT_ELIGIBILITIES` governs alert routing (M3).
  **Plus a canonical `FALLBACK_ASSIGNMENT_PRIORITIES: dict[ModelId, int]` map (FIX-A)**
  — `climatology_fallback=100`, `persistence_fallback=90` — the SINGLE source the
  assignment/backfill/creation path uses to resolve a fallback's `≥90` ordering integer
  (explicit `[model_priorities]` value when present, validator-guaranteed `≥90`; else the
  hard-coded canonical). It is used **NEVER** via `priority_for_model()` (which returns
  `DEFAULT_PRIORITY=50` on omission and would trip the M0b write-guard), so a fallback is
  always assigned in-tier regardless of whether the deployment config lists it.
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
  **partition `all_ensembles` ONCE by `AlertEligibility` immediately before the
  Phase-C dispatch (`:1441-1447`)** — keep `SKILL_FORECAST` (→ `AlertSource.FORECAST`),
  **drop BOTH `NO_EVENT_INFORMATION` (climatology) and `CURRENT_OBS_PROXY` (persistence)**
  from the forecast-alert set (no re-routing, no freshness read; current-condition
  coverage is Flow 2's obs checker — ER-D1, targeted review) — covering all three build
  sites (`:1111`/`:1207`/`:1414`); the `station_dark` + `alert_suppressed` written as
  **`pipeline_health` records** (`PgPipelineHealthStore.append_health_record`, NOT
  `AlertSource.PIPELINE` alert rows; written regardless of `enable_*_alerts`) + a new
  **`ForecastCycleHealth` field** (DEGRADED/FAILED/HEALTHY) on `ForecastCycleResult`
  (`:89-97`) for a dark/suppressed cycle — the Prefect run stays COMPLETED, `FlowRunState`
  is NOT extended (ER-D4); C1a loader + `_WeatherForecastAdapterConfig` field, C1b/C1c
  checks + the `nwp.grid_stale` `pipeline_health` record.
- **`pipeline_health` store plumbing (FIX-D — the write/read path is currently
  unwired):** the store exists (`store/pipeline_health_store.py:12-38`,
  `append_health_record` `:16`, `fetch_recent` `:28`) but nothing in Flow 1 or the API
  can reach it today. Explicit scope, in FOUR parts:
  1. **`flows/_db.py:23-61`** — add `PgPipelineHealthStore` to `make_pg_stores()`
     (currently omitted, `:44-61`) so the production store bundle exposes it.
  2. **`flows/run_forecast_cycle.py:549-620`** — add a `pipeline_health_store: object |
     None = None` param to `run_forecast_cycle_flow` (signature `:549-570`, which has NO
     such param today), bind it from `stores["pipeline_health_store"]` in the
     production-setup block (`:603-620`), and route the `station_dark` /
     `alert_suppressed` / `nwp.grid_stale` append calls through it.
  3. **`api/deps.py:32-61`** — expose `PgPipelineHealthStore` from `get_stores()`
     (currently omitted, `:49-61`) so the API can read health records.
  4. **a minimal health-record route + response schema + template** — a read-only
     `api/routes/*` endpoint backed by `PgPipelineHealthStore.fetch_recent`
     (`pipeline_health_store.py:28`), a `PipelineHealthRecordResponse` schema, and a
     minimal dashboard template — so `station_dark` / `alert_suppressed` / `grid_stale`
     records are **visible** without reading logs (ER-D4 / FIX-D). Richer surfacing may
     later move to the Flow-4 monitoring plan (candidate split). Acceptance check 19
     asserts this minimal health-visibility wiring.
- **`services/model_registry.py:27-88`** — registry / load-time tier guard (ER-D7):
  every discovered model must be in BOTH explicit maps (`MODEL_TIERS` +
  `ALERT_ELIGIBILITIES`) or declare both facets as attributes, else **raise
  `ConfigurationError`** in `discover_models()` — no `SKILL`/`SKILL_FORECAST`
  skill-by-absence default; `FALLBACK_MODEL_IDS` is derived from `MODEL_TIERS`.
- **`services/observation_alert_checker.py`** (`check_observation_alerts`,
  `AlertSource.OBSERVATION`) + **`flows/ingest_observations.py:366-374`** — this is the
  EXISTING current-condition path (24h-latest, `:25,:52,:63,:73,:80`) that independently
  covers a dangerous current level; the forecast cycle does NOT re-route persistence to
  it (targeted review, BLOCKER-1). No code change here beyond the **HARD precondition
  `enable_observation_alerts=true` on the mini** (`config/overlays/mac-mini.toml`,
  `config/deployment.py:104`, `config.toml:26`) so the path is live.
- **`types/enums.py`** — new `AlertEligibility` enum
  (`SKILL_FORECAST`/`CURRENT_OBS_PROXY`/`NO_EVENT_INFORMATION`, ER-D1) + a new
  **`ForecastCycleHealth` enum** (DEGRADED/FAILED/HEALTHY) for the cycle-outcome field on
  `ForecastCycleResult` — **do NOT add `DEGRADED` to `FlowRunState`** (`:199-206`), which
  is a direct mapping of Prefect's `StateType` (`adapters/prefect_status.py:28`) and has
  no degraded state (ER-D4 / targeted review); the `ModelTier` enum may live here or in
  `types/ids.py` (alongside the `MODEL_TIERS` + `ALERT_ELIGIBILITIES` maps). **Plus four
  NEW `PipelineCheckType` members (FIX-E), added to `:134-142`:
  `FORECAST_STATION_DARK`, `ALERT_SUPPRESSED_FALLBACK`, `PRIORITY_MIGRATION_AUDIT`,
  `CLIMATOLOGY_THRESHOLD_REVIEW`** — the pinned `check_type`s for the M5 health signals
  (`nwp.grid_stale` reuses the existing `NWP_DELIVERY`; `PipelineHealthStatus` stays
  `OK`/`WARNING`/`CRITICAL`, `:128-133`, unchanged).
- **DERIVED `no_floor` indicator + dashboard/API badge (ER-D3 / FIX-C — NO migration,
  NO new field):** `no_floor` is **computed at query/render time** from "does the
  station have an ACTIVE `climatology_fallback` artifact?" (the same check the Step-8
  floor-gate runs) — it is **NOT** a persisted column on the stations table
  (`db/metadata.py:67-95`), **NOT** a field on the `Station` dataclass
  (`types/station.py:28-43`), **NOT** a field on the station API schema
  (`api/schemas.py:49-70`), and **NOT** a new `StationStatus` member
  (`types/enums.py:150-154`). The derivation lives in the **API/dashboard layer**
  (`api/routes/*` + templates) plus wherever the floor-gate artifact check already runs;
  it renders a computed `no_floor` badge so a floorless-but-skilled existing station
  stays serving + visibly flagged rather than flipped OPERATIONAL→NOT. The strict
  floor-gate applies to NEW onboarding only; phased rollout for existing — **no
  deploy-time status flip, no stations-table/schema/migration scope.**
- **climatology-QA diagnostic** at onboarding/backfill (ER-D2) — recurring seasonal
  threshold crossing → a `pipeline_health` record pinned to
  `check_type=CLIMATOLOGY_THRESHOLD_REVIEW` (FIX-E; a new member, WARNING),
  NOT an `AlertSource.PIPELINE` alert row and NOT a flood alert.
- **Step-0 root-cause snapshot** (ER-D5) + **M0a fleet-mutation safety envelope**
  (ER-D6: maintenance mode / advisory lock, DB backup, dry-run diff + per-divergence
  triage, single-transaction apply, migration-audit record) — both are named admin
  actions gating the M0a/M2 mutations.
- `onboarding.py` Step 7/8 + `flows/onboard.py` + onboarding tests (B1b) + a one-time
  fleet floor-audit query.
- `persistence_fallback.py` (B2 — **raise a narrow `InsufficientObservationsError`**
  caught by the `_run_station_forecast` backstop; NOT "return `ModelFailure`", which the
  native protocol cannot express) + **a filed FI-repo issue for
  the native-sentinel-fallback gap**.
- `alert_strategy.py`/`alert_checker.py` (M3 suppression `alert.suppressed_fallback_only`
  across Primary/Pooled/BMA/Consensus) — note the central `AlertEligibility` partition
  now makes BOTH `NO_EVENT_INFORMATION` (climatology) and `CURRENT_OBS_PROXY`
  (persistence) ensembles never reach these forecast-alert strategies; the
  current-condition case is handled independently by Flow 2's obs checker, not by any
  forecast-cycle re-routing (ER-D1, targeted review); API/templates (B3 — static
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
  artifact"), **scoped to NEW onboarding** (ER-D3), with the doc stating existing
  stations are reconciled via the `no_floor` degraded state + phased backfill (NOT a
  deploy-time fleet flip) — acceptance check 14.
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
  `alert.suppressed_fallback_only` / `nwp.grid_stale`, and a note that each of these
  ALSO persists a `pipeline_health` record (via `PgPipelineHealthStore`, NOT an
  `AlertSource.PIPELINE` alert row) regardless of the `enable_*_alerts` flags (ER-D4).
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
  replaces the tier's *source* with the explicit categorical `MODEL_TIERS` map (from
  which `FALLBACK_MODEL_IDS` is derived), so both must be updated to document the new
  scheme (folded, superseding the earlier "dual-source" framing):
  > **Fallback/skill tier membership is now categorical** — keyed on the explicit
  > `MODEL_TIERS` map (equivalently `model_id ∈ FALLBACK_MODEL_IDS`, derived from it) —
  > and is the single source read by forecast combination (`combinable_results`) and the
  > B3 badge. **Alert routing is a SEPARATE facet, `AlertEligibility` /
  > `ALERT_ELIGIBILITIES`, NOT the tier map**: a forecast flood alert fires only for a
  > `SKILL_FORECAST` model; both fallback eligibilities (`CURRENT_OBS_PROXY`,
  > `NO_EVENT_INFORMATION`) are dropped from the forecast-alert set. DB
  > `assignment.priority` no longer decides tier membership anywhere; it is retained
  > only as an **ordering / tie-break** key among admitted skill models
  > (`min(priority)` primary-selection dispatch, combination ordering). The config
  > `[model_priorities]` map sets that ordering integer, and a config-load validator
  > keeps every **explicitly-configured** fallback's ordering integer
  > `>= FALLBACK_PRIORITY_THRESHOLD` (an omitted fallback falls back to
  > `DEFAULT_PRIORITY` and is classified purely by the categorical map).
  This removes the reverse-engineering burden and documents that a drifted DB
  `priority` can no longer misclassify a tier. (B3's render-time `ModelTier` adds **no**
  new field to the `OperationalForecast` dataclass — only the DEFERRED persisted
  `served_as_fallback` column would; the spec edits here are the
  priority/`combinable_results` narrative **plus documenting the new `ModelTier` enum**
  on the API schema, not a new `OperationalForecast` field.) A named
idempotent backfill for 2009/2091 + fleet residue → **hold-at-PR** with a version
bump, per CLAUDE.md.
