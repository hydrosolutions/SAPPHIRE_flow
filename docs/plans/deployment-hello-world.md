---
status: READY
created: 2026-11-18
owner: deployment orchestrator
scope: Disposable synthetic forecast through a real Prefect worker and authenticated API
baseline: 7f4eed38c9f983e1447c09705a72388dd1339d69
---

# Deployment hello world

## Outcome and boundaries

An administrator with a prepared application image runs one command and sees
whether this installation can start its services, initialize an empty database,
execute one forecast through a worker, and return that forecast through the API.
The command removes its disposable resources and retains a small safe report.
It proves this synthetic path, not agency readiness or forecast skill.

The owner authorized planning/review and subsequently dispatched **HW1a only** on
2026-11-18. Independent model/weather/artifact packaging can proceed while the
station-supported discharge fixture is pending; that dependency still gates full
HW1a completion. Container execution, deployment, secret changes, operational
configuration changes and later implementation milestones remain undispatched.
The merged profile increment (#544) remains preparation-only. This smoke command
does not turn its pending checks into PASS or implement profile-driven launching.

Smallest scenario: one generic tenant/network/station, hourly discharge history,
one point-weather precipitation trace, one diagnostic FI model, one cycle, three
hourly forecast steps and twenty output members. No basin geometry, rating curve,
Gateway, trained Aquacast artifact, dashboard, Caddy, ingest/backup workers,
recurring schedules, notification delivery or publication workflow. No migrations.
Operational configs and scientific behavior remain untouched.

## Evidence and reuse

Read against the baseline above, not inferred from the other session's account:

| Existing capability | Reuse and limit |
| --- | --- |
| `cli/create_station.py` (#551), migration `0087` | Use the public command with explicit tenant/network/timezone and `--apply`. It creates an ONBOARDING station; it does not establish weather bindings, an active model or operational readiness. `--gateway-name` currently remains required; supply a synthetic label, make no Gateway request. |
| `tests/fakes/fake_fi_models.py`, `tests/integration/test_fi_adapter_smoke.py` | Reference for FI requirements, serialization, adaptation, twenty-member floor and failure tests. The reference model ignores weather values and lives outside the image; do not ship/import the tests package or claim it proves input consumption. |
| `adapters/replay/nwp.py` | Existing replay serves gridded data through `NwpGridStore`. Reusing it would add grid/basin extraction to this point scenario; use a bounded point fixture adapter instead, through the existing `WeatherForecastSource` protocol. |
| `flows/run_forecast_cycle.py` | Accepts injected `adapter`, `models`, `clock`, `rng`, `cycle_time`, while omitted stores use real production PostgreSQL stores. Its point-forecast branch persists weather and reads it back through normal input assembly. No second forecast engine or `.fn` test shortcut. |
| `services/model_registry.py`, `services/model_import.py`, `services/model_onboarding.py` | Existing registration, audited artifact import and assignment services. Smoke setup supplies the fixture model explicitly; do not add it to automatic operational model discovery. |
| `cli/register_deployments.py` | Source-root and ONLY/SKIP settings are preserved. It still registers operational schedules; the smoke setup registers only its own unscheduled wrapper deployment using the same Prefect source/deploy API, without modifying this shared registrar. |
| `docker-compose.yml`, `docker/entrypoint.sh`, `docker/bootstrap-roles.*`, `docker-entrypoint-initdb.d/` | Reuse image, initialization and scoped DB-role conventions. Base Compose includes operational services, secrets and country config; a small standalone smoke Compose file is safer and simpler than subtracting these through overlays. |
| `api/routes/api_forecasts.py`, `cli/access_tokens.py` | Verify `/api/v1/forecasts/{id}` with a newly generated station-scoped token. Normal authentication remains enabled. No reliance on dashboard JSON or health alone. |
| `tests/integration/test_e2e_pipeline.py` | Reuse assertions/service setup ideas, not its country fixtures or in-process execution as proof of worker wiring. |

The local ForecastInterface checkout was also inspected. SAP3 pins **v0.1.20**
(`pyproject.toml`, `uv.lock`); its protocol, input requirements and result types
are the implementation contract. The checkout's newer HEAD includes time-grid
extensions not in that pin. Do not upgrade FI or introduce those extensions here.
Re-read the pinned `interface/protocol.py`, `input/requirement.py`,
`interface/result.py`, `docs/model_interface.md`, `docs/input_requirement.md`
before model implementation/review. Anticipated failures return `ModelFailure`;
`max_nan` is not a replacement for model shape/coverage checks.

Consulted scope, architecture, security, CI/CD and orchestration standards, plus
the FI/model, forecast-cycle and Prefect/Docker touchpoint maps. Their current
runtime safeguards override obsolete v0 simplifications such as unauthenticated
API access. Shared entrypoint privileges, role grants, release identity and
hydrological-grid activation restrictions are not relaxed for smoke execution.

## Coordination contract and gates

Station onboarding owns creation, observation loading/QC, ratings, readiness,
its generator and `docs/operations/new-station-by-hand.md`. Do not edit that guide,
its uncommitted rewrite, their worktrees or their command implementations.
Deployment owns this scenario, model/weather packaging, runtime orchestration,
API assertions, cleanup and its own guide.

The owner relayed the handoff; local Plans 540/541 were read in
`/Users/bea/Documents/GitHub/sapphire-plan-540`. They and the guide rewrite are
unmerged coordination evidence, not shipped capabilities. Refresh their state at
dispatch; do not copy their plans into this branch.

| Dependency | Current state / dispatch condition |
| --- | --- |
| M2 station creation | Merged #551. Preserve new defaulted station fields and legacy migration-test helper. |
| M3a observation loader | Other session reports implementation in progress, not reviewed/merged. HW1b requires merged `load_station_observations discharge` and its actual config/tenant/CSV/`--apply` contract. |
| M3b Stage 1 QC on load | Planned. HW1b requires merged normal QC path and evidence of usable input coverage; never insert already-GOOD observations directly to bypass it. |
| Fixed discharge fixture | Requested from station side; no supported candidate yet accepted. HW1a pins a station-side supplied/qualified generic fixture. If unavailable, record blocked fixture work; do not silently build a competing generator. |
| M3c/M3d | Not dependencies of this direct-discharge scenario. M3d outputs water-level/rating files; that alternative needs M3c as well to produce discharge. No request to accelerate them. |
| M5 Gateway bindings / M7 promotion | Not reused as general commands here. A narrowly scoped smoke bootstrap seeds its own point binding and active diagnostic assignment through existing services/stores in the newly created smoke DB only. This is test setup, not a general onboarding/promotion command. |

M3a/M3b do not block planning or pure model/weather development. They do block
end-to-end qualification. Do not start a runtime substitute if they are late.
There is no claimed migration number; if a future need appears, stop and reconcile
scope. Other workstreams allocate the next free revision at integration time.

## Scenario and packaging contract

- Scenario id `hello-world-v1`; tenant/network `smoke`, station code `demo-001`.
  Use generic synthetic metadata, not a real agency/station identity. Resolve the
  created UUID normally and retain it in the report; UUIDs need not be identical
  across installations.
- Fixed cycle `2025-01-03T00:00:00Z`, hourly grid aligned to UTC, no daily aggregation.
  Target history must cover the three complete hourly buckets before that issue.
  The last three input stamps are 2025-01-02 21:00Z, 22:00Z and 23:00Z.
  The supplied fixture may include a longer QC-support window. All CSV timestamps
  carry explicit UTC offsets; pin first/last timestamps, columns and units in the
  manifest. Include only the necessary history after qualification with M3b.
- Smoke configuration explicitly selects UTC display and UTC station timezone.
  These are fixture choices, not defaults for agencies. Keep display, input grid
  and artifact training conventions distinct; no new hydrological-day logic.
- Forecast valid times: issue + 1h, + 2h, + 3h. With the last observation at
  issue - 1h, FI v0.1.20 output `offset=2`; assert this against delivered inputs.
  Future precipitation uses **start-labelled** hourly totals in mm: the value at
  issue + 1h represents [issue + 1h, issue + 2h), and likewise for the next two.
  These synthetic labels deliberately match the existing hourly LEGACY assembly;
  do not apply the daily ICON end-label shift or change that production policy.
  `PointForecast.values` columns are `valid_time,parameter,member_id,value`, with
  stored parameter and FI future variable both `precipitation`, control member 0.
  The model declares one hourly branch, `future_steps=3`, exact horizon, `max_nan=0`
  and SINGLE forcing mode; config explicitly allows `precipitation`. The model
  produces twenty output members from the single trace; weather is not broadcast
  to twenty input members. Test through `build_future_dynamic_frame` and the FI
  adapter, not just model-direct frames.
- The fixture binding uses existing routing tag `icon_ch2_eps`, role FORECAST,
  extraction POINT and status ACTIVE, written with
  `PgStationStore.store_weather_source`. This tag is needed by the unchanged
  injected-adapter staleness probe, which still defaults to ICON. All values and
  the adapter are synthetic; the manifest/report must say so, never claim provider
  acquisition. No live provider is constructed and no agency profile inherits this
  tag. A separate synthetic-source routing feature is outside this increment.
  Assert one binding and verify the point branch persists all three records.
- A tiny FI station model consumes the last delivered discharge and each future
  precipitation value. Proposed diagnostic equation is
  `Q[k,m] = Q_last + a * P[k] + b * m`, members `m=0..19`, with versioned constants
  `a=0.1 (m³/s)/mm`, `b=0.01 m³/s`. It is wiring evidence, not a scientific model.
  Twenty members preserve the existing minimum; no operational floor is lowered.
  It declares discharge targets with TRAJECTORIES output and precipitation
  requirements/units explicitly,
  checks timestamps/lengths/finite values, and returns `ModelFailure` for invalid
  input. Serialize a small self-contained artifact with the constants, schema
  version and configuration hash; no pickle, remote artifact or file paths.
  Expose `config_hash` as the SHA-256 of the exact packaged model-configuration
  bytes; the FI adapter proxies it. Test equality with the manifest's
  `expected_config_hash` and reject an artifact carrying a different hash.
  Resource loading belongs in setup/the wrapper; model train/predict remain pure.
- Packaging: `src/sapphire_flow/data/deployment_smoke/` contains README/manifest,
  fixed discharge CSV, fixed weather CSV, artifact and expected-result JSON.
  Add explicit package-data patterns in `pyproject.toml`. Load via
  `importlib.resources`; tests consume these same resources. An installed wheel
  and the prepared image must work without `tests/` or a checkout bind mount.
- Proposed code: `models/deployment_smoke.py` (FI model),
  `adapters/deployment_smoke_weather.py` (fixed `PointForecast` adapter),
  `flows/deployment_smoke.py` (thin wrapper), `cli/deployment_smoke.py` (setup,
  registration and result verification). The root host tool below only runs
  Compose and handles safe reports; keep application logic in existing layers.
- Use model id `deployment_smoke`, explicitly passed through the FI adapter with
  `ModelTier.SKILL` and `AlertEligibility.NO_EVENT_INFORMATION`. SKILL here is the
  existing routing/API tier, not scientific qualification. Do not extend
  `FALLBACK_MODEL_IDS` or add a climatology artifact: API tier is `skill`, station
  `no_floor=true`. Pin these facts in tests/report rather than claiming operational
  readiness. All three
  alert enablement flags are explicitly false in smoke config. No global
  plugin registration or new scientific classification enum. The wrapper passes
  no fake stores; normal PostgreSQL weather, observation, artifact and forecast
  reads/writes must be exercised. The point adapter is injected inside the worker,
  not serialized as an object through Prefect parameters. No monkeypatches.

HW1a freezes the exact manifest/artifact/CSV contract and records source hashes,
not a generic scenario schema or new plugin framework. If this route requires
changes to the live forecast/FI boundary, pause that expansion for reconciliation.

## Administrator command and execution

Proposed interface (not available until HW1c):

```bash
uv run python tools/deployment_smoke.py run --image sapphire-flow:<prepared-version> --report-dir /absolute/path/smoke-report
uv run python tools/deployment_smoke.py cleanup --report-dir /absolute/path/smoke-report
```

Prerequisites: local Docker/Compose, the checkout matching the prepared image,
and preloaded application/PostGIS/Prefect images. Reuse the existing build recipe
and release identity verifier. Private dependency/build credentials may be needed
in that separate preparation step; the smoke runtime neither retrieves nor reads
them. Use the standard application image, not the Aquacast image. Do not pull,
build, install packages or contact providers from `run`. Fail preflight with a
specific preparation instruction if anything is missing/mismatched.

1. Verify local Docker endpoint, compatible Compose, image/release identity and
   packaged scenario. Create an exclusive run directory (0700) and unique Compose
   project `sapphire-smoke-<random>`. Refuse an existing report/run identity.
   Reject conflicting ambient Compose selectors; construct a minimal child env
   without inherited SAPPHIRE/DATABASE/PREFECT/provider settings. Do not load the
   checkout's `.env` or private profile. Remote Docker execution is out of MVP scope.
2. Render only `docker-compose.smoke.yml`, with the generated env file and project.
   It defines PostGIS, Prefect server, init, one process worker and API; setup is a
   one-shot command using the same app image. No base/agency overlay composition.
   Reuse pinned infrastructure images and current security settings. Only the API
   gets a dynamically allocated localhost host port; no Postgres/Prefect exposure.
   Internal network has no provider egress; no host network, Docker socket,
   operational bind mounts, external volumes or fixed container names.
3. Generate disposable DB-role passwords and API pepper inside the private run
   directory, mounted through existing secret-file conventions. Reuse first-boot
   database initialization, migrations to the image's head, tenant provisioning and
   role bootstrap. API and worker retain their distinct scoped DB roles; owner
   credentials are setup-only. No secret values in command arguments or reports.
4. Setup invokes `create_station` and the merged M3a/M3b discharge/QC command with
   explicit fixture/config paths and identity. Require GOOD coverage of the
   model lookback. Register/import the fixture artifact using existing audited
   services (`register_models`, `import_external_artifact`, real audited writer,
   principal and audit store); create its assignment via `create_station_assignment`.
   Establish the point binding with `PgStationStore.store_weather_source`. After
   verifying loaded/QC-qualified inputs, artifact and assignment, explicitly call
   `PgStationStore.update_station_status` to transition this fixture station from
   ONBOARDING to OPERATIONAL. This sole smoke-only transition happens in setup,
   after confirming the new DB/project and exact tenant/network/station identity;
   it never accepts an existing/shared database or arbitrary station argument.
   Do not weaken
   readiness/promotion controls on existing paths. HW1b verifies grants needed by
   setup and uses owner authority only in its one-shot setup container.
5. Register one unscheduled `deployment-smoke` flow in one isolated process pool,
   sourced from the installed image's `/app` code. Disable Prefect telemetry.
   Enqueue once via Prefect API with serializable fixture identity only. The real
   worker calls the decorated `run_forecast_cycle_flow` as a nested flow with the
   fixture adapter/model, parsed smoke `DeploymentConfig`, real forecast-QC rule
   set, `data_use=STANDARD`, fixed cycle/clock and seeded RNG. Keep omitted stores
   on the production bootstrap, including rejected-forecast capture. The normal
   entrypoint constructs worker `DATABASE_URL` from its scoped role secret. Use
   a project-owned artifact named volume shared setup/worker RW and API RO. No
   ordinary deployment registration or schedules. Assert wrapper and child run
   IDs and their parent relationship in Prefect; direct/in-process execution is
   insufficient. Publication activation remains empty in smoke configuration.
6. Poll that flow run with bounded timeout, then resolve its forecast by exact
   station/model/cycle/data-use identity. Mint a short-lived station-scoped API
   token with `create_token`; keep raw credentials out of logs and Prefect results.
   Probe the localhost API with normal authentication and assert the returned
   forecast identity, units, issue/valid times, 20×3 shape, acceptable QC and every
   expected value (absolute tolerance `1e-8`). Require exactly one station attempted
   and succeeded, zero failed, and one diagnostic-model forecast. A skipped station
   is failure, even if Prefect itself completed. Require input coverage and weather
   freshness for this fixed clock; do not confuse fixture time with wall-clock
   token expiry. HW1a pins expected health/input-quality codes and API response
   shape; no blanket acceptance of degraded outcomes. `no_floor=true` is an explicit
   scope limitation, not missing required fixture input. Also assert no pre-existing forecast
   existed before enqueue and worker/flow-run provenance matches this run.
7. In `finally`, stop only this project's services and remove its volumes/network
   and generated credentials on success, failure or interrupt. Preserve safe
   summary/report only. Cleanup verifies ownership labels and recorded project
   identity; never prune globally or delete resources by name prefix alone.
   A cleanup failure is nonzero even if forecast verification passed; report
   remaining resource IDs and the exact retry command. Forced termination may
   leave resources: the explicit cleanup command handles this from the run record.

Report: schema/scenario version, image id/release, fixture/config hashes, named
stage results, timings, station/flow/forecast IDs, comparison counts, cleanup
status and next action. No environment dump, connection URLs, tokens, raw
subprocess logs or confidential data. Print allowlisted failure summaries; support
diagnostics must undergo the same redaction. Do not retain secret files as evidence.
Exit 0 only for verified forecast **and** verified cleanup; 2 for preflight refusal,
1 for runtime/assertion/cleanup failure. Interrupts still attempt cleanup.

Budget: initial runtime timeout 10 minutes plus at most 2 minutes for cleanup;
CI job timeout 20 minutes including setup. Target a host with 8 GiB available RAM,
2 CPUs and 10 GiB free disk after image preparation. These are proposed qualification
budgets, not measured performance claims. HW1b measures peak memory/disk and elapsed
time on supported Linux/macOS Docker hosts before documenting supported minima.
No automatic keep-running option in the MVP.

## Dispatch milestones

Run sequentially under one implementation owner. Each code PR updates its affected
docs, receives one independent relevant patch review, and passes the full final-code
suite locally or in CI. Use current release-target policy once per code PR. The
owner approves/merges; no auto-merge, host deployment or extra review loop.

| Milestone | Owned paths / dependency | Acceptance and meaningful tests |
| --- | --- | --- |
| HW0 — this plan | `docs/plans/deployment-hello-world.md`; owner authorized planning | Evidence reconciled, ownership/dependencies explicit, one bounded independent Claude plan review covering administrator, developer, FI and isolation concerns. Orchestrator sets READY only after findings are resolved; material owner decisions are escalated. |
| HW1a — qualify/package the scenario | Proposed model, adapter and package-data paths above; `tests/unit/models/test_deployment_smoke.py`, `tests/unit/adapters/test_deployment_smoke_weather.py`; accepted station-side discharge fixture gates fixture completion | Freeze UTC timestamps/offsets, QC support, exact variable mapping, model classification, artifact config hash and expected values. Test FI contract/floors; artifact round trip; missing/short/nonfinite inputs return typed failure; mutate observation and weather independently and require the specified output change. Wrong cycle/source/coverage must refuse; an hourly gap or skipped station cannot satisfy the fixture contract. Verify installed wheel contains all resources and has no tests-package imports. No services or complete-pass claim. |
| HW1b — worker-to-API path | `docker-compose.smoke.yml`, `tools/deployment_smoke.py`, smoke CLI/flow modules; `tests/unit/tools/test_deployment_smoke.py`, `tests/integration/test_deployment_smoke.py`; depends HW1a + merged M3a/M3b and qualified fixture | Complete steps 1–7 through actual process worker, real DB roles and real HTTP API. Assert no model/provider substitution, no fixture weather inserted behind the forecast flow, no SQL bypass of observation QC. Check missing image/unsafe selector before writes; cleanup on setup/worker/API failure and interrupt; no credential canary in any output/report. Mutation tests verify controller refuses cleanup of a foreign project and missing worker/malformed API output cannot pass. Measure resource/runtime budgets. |
| HW1c — release qualification and guide | `.github/workflows/ci.yml` (one smoke gate, coordinate shared edits), `docs/deployment/hello-world.md`, links from quick-start/profile guide, affected standards/touchpoint maps | Dedicated CI job uses the built app image and pinned infrastructure images; execute the same command, not a direct flow invocation. Run twice from clean state; exercise alongside an unrelated sentinel project and prove it is unchanged. Verify API 401 without token and expected authorized response. CLI help matches docs. Independent patch review, full suite, image checks and smoke job pass on final commit before PR ready. |
| HW2 — target-server proof | No dispatch from this plan | After HW1c merge, explicit owner server-run authorization. Run same versioned scenario and record cleanup. Real-station onboarding/scientific acceptance remain the station team's work. |

Documentation is short and task-oriented: prerequisites/build link, one run command,
what success proves, failure-stage actions and cleanup retry. Link profile/config
references rather than duplicating them. Preserve this plan as implementation
history. Do not advertise unimplemented commands in the administrator quick-start.

```json
{
  "planning_authorized": ["HW0"],
  "implementation_authorized": ["HW1a"],
  "milestones": [
    {"id": "HW0", "depends_on": [], "gate": "independent_plan_review"},
    {"id": "HW1a", "depends_on": ["HW0"], "external": ["owner_dispatch", "station_fixture_contract"]},
    {"id": "HW1b", "depends_on": ["HW1a"], "external": ["M3a_merged", "M3b_merged", "fixture_QC_qualified", "owner_dispatch"]},
    {"id": "HW1c", "depends_on": ["HW1b"], "external": ["owner_dispatch", "independent_patch_review", "final_code_full_CI", "smoke_CI"]},
    {"id": "HW2", "depends_on": ["HW1c"], "external": ["owner_merge", "explicit_server_run_authorization"]}
  ]
}
```

## Review record

One bounded independent Claude review completed on 2026-11-18. The initial verdict
required three design corrections: explicit station activation; model/API tier
agreement; precise hourly weather labeling/mapping. All were folded into the plan
against the cited source. Also clarified hash ownership/artifact volume, production
store/bootstrap wiring, nested-flow provenance, skipped-station failure, named
binding writer and inactive publication gate. No new owner decision or live-path
change is needed. M3a/M3b and the fixture remain external execution gates.

The orchestrator sets READY for this phased design after that revision. This is
not implementation dispatch or evidence of a successful smoke run. Full review
report remains local at `/private/tmp/sapphire-hello-world-plan-review.txt`; no
automatic additional review round. Dependency-graph/metadata/whitespace checks
passed. No container, application test or forecast ran during this planning pass.

## HW1a implementation checkpoint — 2026-11-18

Partial implementation independently reviewed; **HW1a is not
complete**. Added the explicitly supplied FI model, fixed point-weather adapter,
packaged configuration/artifact/weather/manifest/README and focused unit tests.
The manifest marks the scenario `pending_discharge_fixture`. Unit-test discharge
values exercise arithmetic and boundary contracts; they are not a replacement
station fixture. No runtime CLI, flow, Compose file or administrator run command
is delivered by this increment.

Validation on Python 3.12 with pinned FI 0.1.20:

- Both new test modules: 28 passed, including independent observation/weather
  mutations, malformed coverage and finite-input overflow returning `ModelFailure`.
  The real point-weather assembly and FI adapter produce a public
  `ForecastEnsemble` with three hourly steps and twenty members; its member count
  satisfies the configured operational minimum. This is a configured-floor
  assertion, not a pass of the generic onboarding qualification helpers.
- Ruff lint/format and changed-source-module strict Pyright pass. Repository
  Pyright scope is `src`; an exploratory test-file run reports only four existing
  pytest `approx` annotation gaps, not application-module errors.
- Built and installed a wheel in an isolated scratch environment. Outside the
  checkout, verified all five resources, manifest hashes, artifact loading and
  fixed weather fetch, with no `tests` package imported or shipped in the wheel.
  Dependencies came from the existing environment; no service or provider ran.

Generic helper qualification is **unavailable**, not waived: both
`assert_model_conforms` and `assert_operational_floors` reject prediction inputs
generated by `services/model_onboarding.py` with "exact three hourly timestamps".
That helper reuses historical timestamps as future weather (lines 361–375 and
630–644) and sets issue time to the final observation (lines 666–675). This model
requires history ending at issue minus one hour and future weather at issue plus
one through three hours. Keep those strict checks and shared helpers unchanged in
HW1a. The planned audited external-artifact import path does not depend on this
generic synthetic-input qualification; no generic `onboard_model` compatibility
is claimed. Reconcile the helper separately before claiming that compatibility.

One independent Claude patch review found no blockers for this bounded increment.
The generic-helper limitation was confirmed as correctly bounded. Low-severity
notes concern import layering, permissive CSV datetime parsing, an intentionally
temporary pending-fixture assertion and automating the wheel check. They do not
change the fixed fixture contract or authorize unrelated hardening. On fixture
completion, replace that pending-status assertion with qualified fixture evidence;
HW1c must run the wheel/image resource checks in CI. Full-source type ratchet:
344 errors against the existing ceiling of 400. Full report stays local at
`/private/tmp/sapphire-hw1a-implementation-review.txt`.

Remaining gates: station-supported generic discharge CSV; normal loader/QC
qualification; pinned expected runtime values and health/input-quality codes;
final-code full suite in CI. Worker/API execution,
cleanup and administrator instructions remain HW1b/HW1c. No operational files,
station-onboarding files, migrations, secrets or live services changed.
