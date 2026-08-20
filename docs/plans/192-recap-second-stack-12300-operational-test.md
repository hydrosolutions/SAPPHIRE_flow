---
status: DRAFT
created: 2026-08-20
plan: 192
title: Second isolated mac-mini stack — operational recap-gateway forcing for HRU 12300
scope: Prove that recap Data Gateway forcing works end-to-end for Nepal test basin 12300 on the mac-mini, WITHOUT disturbing the live Swiss/BAFU stack. Stage A is a throwaway one-shot proof (disposable DB, one direct fetch-and-store call). Stage B — only if the owner wants a STANDING daily feed — is a second, isolated Compose stack running `[adapters.weather_forecast] type = "recap_gateway"`. Models, training and targets stay in Plan 139.
depends_on: [82, 120]
blocks: []
supersedes: []
---

# Plan 192 — Second mac-mini stack: operational recap forcing for 12300

## Status

**DRAFT — four rounds of independent Codex review folded in (2026-08-20); needs `/plan` before READY.**
Grounded in a live gateway re-probe (evidence appendix) and a code re-read of the Flow-1 dispatch.

This plan proves the **deployment/forcing** half of the Nepal test-basin question. It is a cheap
**precursor** to [Plan 139](139-nepal-12300-swe-regression-enablement.md) — 139 is still a DRAFT epic whose
W8 sits behind all model/training work, and its W5 (per-station forcing dispatch) is precisely what this
plan *routes around* rather than owns. Nothing here is a 139 deliverable; 139 can reuse this plan's
deployment evidence. Hence `depends_on: [82, 120]` only — **139 is related work, not a dependency.**

**Round 1** (2 blockers, 8 majors) fixed the Compose `ports` concatenation trap and a backwards Phase 2.
**Round 2 (NEEDS_WORK)** corrected an identity error and cut the plan in half. **Round 3 (2 blockers,
5 majors)** reshaped Stage A's invocation seam and Stage B's ordering. **Round 4 (2 majors, 2 minors)**
cut Stage B back to what the plan actually consumes and closed the shared-image-tag hole:

- **An identity error (round 2).** The basin loader derives the polygon name as
  `g_<normalized station_code>` (`services/basin_package_loader.py:774`), so `g_123` requires station code
  **`123`** — `12300` is the **Gateway HRU**, a different field. Corrected throughout: station code `123`,
  `gateway_hru_name = "12300"`, polygon `name = "g_123"`.
- **Over-engineering (round 2).** Two stages now: Stage A proves the thing cheaply and throws the evidence
  away; Stage B builds the standing stack **only if the owner wants a daily feed**.
- **A false acceptance criterion (round 3).** Stage A had asserted a CRITICAL `FORECAST_FRESHNESS` record
  for a run pinned to an explicit cycle — but `_emit_forecast_freshness_record` early-returns and writes
  **nothing** when `cycle_time_param is not None` (`run_forecast_cycle.py:728-729`, docstring `:706-712`).
  A3/A4 now name one exact seam and assert only what that seam produces.
- **Unsound cycle-resolution options (round 3).** Two of D5's three options did not work as written; D5 and
  D6 also prescribed contradictory values for the *same* config field. Both rewritten below.
- **Stage B ran before it was configured (round 3).** The old B4 started the stack — and therefore the
  worker, and therefore the default 6-hourly cron — before the O2 code change, its merge, and the schedule
  decision. Stage B is re-ordered so nothing executes until everything is decided, merged and paused.
- **Stage B built things nothing consumed, and leaked one shared resource (round 4).** Stage B started
  `api` (and engineered around caddy's port collision) with no consumer anywhere in the plan, contradicting
  Stage A's own "no API, no caddy" scope — both are now dropped, with external HTTP access demoted to a
  decision (O4). And the `sapphire-flow:${VERSION}` image tag, which `-p sapphire-nepal` does *not* scope,
  is now separated structurally (B1 `image:` override) instead of carried as a warning.

### Round 3 — the `/plan` workflow (ESCALATED, then folded by hand)

Ran the repo `/plan` loop (3 rounds, 16 agents, 1 Codex round failed). It **escalated: stalled — a
revision failed to reduce the finding count** — with **0 blockers and 2 residual majors**. It also grew
the doc 252 → 425 lines. Reviewing that growth, most of it is load-bearing correctness, not padding, and
two of its catches were genuinely important:

- **It withdrew a wrong option of mine.** D5(b) used to read "schedule so the nominal time floors to
  00Z". That cannot work: with no explicit `cycle_time` the flow takes the cycle from `clock()` at
  *execution* time (`_resolve_cycle_time`), so a run starting after 06Z floors to the short 06Z
  candidate however it was scheduled. D5(b) is now an explicit `cycle_time` passed from outside Prefect.
- **It killed option (c) as a cheap wait.** Plan 151's resolver projects tracks from an
  `(assignment, model, station_weather_source)` join, so a station with no model assignment — which is
  exactly A2 — yields zero tracks and zero forcing.

Both residual majors are folded (see D8 and B4(a)); the minor trimmed O4 back to a question. The
escalation is recorded rather than papered over: this plan reached "no blockers, 2 majors" and the loop
could not close them on its own.

## Objective

Prove that recap Gateway forcing reaches the store for basin 12300 on the mac-mini, with the Swiss stack
provably untouched — cheaply first (Stage A), and only then, if the owner wants it, as a standing daily
feed (Stage B). See **D2**: the adapter-type selector is deployment-wide, so co-hosting Nepal and Swiss on
one stack is not possible today; a second Compose project is the only config-level route.

The measured gateway behaviour this plan depends on is in the **appendix**, not repeated here.

## Non-goals

- **Not** per-station forcing dispatch (Plan 115 / 139 W5). This plan routes around it (D2).
- **Not** the discharge model, training, target or hindcast/skill — all Plan 139.
- **Not** real DHM discharge (blocked on the DHM questionnaire).
- **Not** multi-tenant Nepal production. One test basin, disposable by design.
- **Not** exercising the Plan 120 package importer (D4 seeds records through the existing stores).
- **Not** promoting the recap probe to a pipeline component (Plan 132 §Out of scope stands).

## Design decisions

- **D1 — Two stages, and Stage B is optional.** *Stage A* proves gateway forcing reaches the store for
  12300, using a disposable database and one direct fetch-and-store call — no schedule, no code change,
  no PR. *Stage B* is the standing daily feed, and is worth building **only if the owner wants one
  running** (O1). Everything expensive in this plan lives in Stage B.
- **D2 — If Stage B happens, isolate by Compose project, not by code.** `[adapters.weather_forecast].type`
  is a single deployment-wide selector (`run_forecast_cycle.py:256`; validation `:390`; dispatch
  `:1957-1973`) — one weather adapter per stack, so flipping the mini's overlay would take NWP away from
  the Swiss stations. Per-station dispatch is Plan 115 (DRAFT) / 139 W5, a real subsystem change.
  `docker compose -p sapphire-nepal` gives its own volumes, network and DB (no compose file declares an
  explicit volume `name`/`external`), and is reversible with `down -v`.
  **The image tag is NOT project-scoped and must be separated explicitly.** Every app service pins
  `image: sapphire-flow:${VERSION}` (`docker-compose.yml:82,150,209,264,355`) — a Docker-host-global name
  that `-p sapphire-nepal` does nothing to isolate, so an unqualified `--build` in the Nepal project would
  overwrite the exact tag the running Swiss containers were created from. B1 therefore **mandates an
  `image:` override** (`sapphire-flow-nepal:${VERSION}`) on every app service this project runs, closing
  the gap structurally rather than by warning — matching the isolation this decision already claims for
  volumes, network and DB.
- **D3 — Identity (corrected).** Station code **`123`**; basin polygon `name` **`g_123`** (the loader
  derives `g_<normalized station_code>` — `basin_package_loader.py:774`); binding `gateway_hru_name`
  **`"12300"`**, which is what the resolver passes as the Gateway `hru_code` (`recap_gateway.py:822`).
  The binding's `name` is **load-bearing at runtime, not cosmetic**: response columns map back to a
  `GatewayPolygonRef` by `polygon_name` (`recap_gateway.py:537-538,570-573`), and any resolved polygon with
  no matching column raises `AdapterError` batch-wide (`:556-564`). So `name` must equal the column the
  Gateway actually returns for HRU 12300 — measured `g_123` (appendix). Getting these three confused is the
  single easiest way to waste a day here.
- **D4 — Seed the records directly through the existing stores; do not manufacture a basin package.** The
  importer is a Plan 120 concern and testing it proves nothing about gateway forcing. Use
  `PgStationStore.store_station` (`store/station_store.py:121`), `.store_weather_source` (`:261`),
  `PgBasinStore.store_basin` (`store/basin_store.py:62` — writes the `basins` projection row *and* its
  paired `version=1, superseded_at IS NULL` `basin_versions` row in one CTE, so the Plan 120 versioning
  invariant survives) and `PgGatewayPolygonBindingStore.store_binding`
  (`store/recap_gateway_polygon_store.py:39`). Raw INSERTs would skip the basin-version pairing. This also
  sidesteps the fact that no checked-in package fits: the only one
  (`tests/fixtures/basin_static/nepal-dhm-basins/`) declares `gateway_hru_names = ["nepal_dhm_v1"]`, and
  `tests/` is excluded from the image anyway.
- **D5 — Cycle resolution: avoid the problem in Stage A, decide it in Stage B.** `resolve_latest_cycle`
  (`recap_gateway.py:434`) accepts the first candidate whose probe merely does not raise — it never
  checks the frame is non-empty, never probes `2t`, never checks horizon (`:452-467`). Because 06/12/18Z
  runs carry ~2 days against 00Z's ~15, a run at 08:00 UTC resolves the **06Z 2-day** run.
  `RecapGatewayConfig.cycle_cadence_hours` does **not** fix this: it reaches only the dormant
  `ForcingResolutionPolicy` (`run_forecast_cycle.py:542,555`), while the adapter constructor
  (`recap_gateway.py:795-804`) accepts only `max_cycle_age_hours` and `_resolve_effective_cycle`
  (`:806-828`) calls the resolver without a cadence, so `_IFS_CADENCE_HOURS = 6.0` (`:406`) applies.
  Walk-back depth is `steps = int(max_age_hours // cadence_hours) + 1` (`:456`).
  **Stage A dodges this entirely** (A3). **Stage B must choose (O2):**
  - **(a) Thread `cycle_cadence_hours` into the adapter and set `24.0`** — a *temporary legacy-path
    exception*, removable when Plan 151's `services/track_resolution.py` resolver goes live. Every
    walk-back step then lands on a 00Z cycle, so a 72 h age bound (D6) is safe.
  - **(b) Trigger each daily run with an explicit 00Z `cycle_time`, from outside Prefect's own
    schedule** — e.g. a host cron/launchd entry running `prefect deployment run
    'forecast-cycle/forecast-cycle' -p cycle_time=$(date -u +%Y-%m-%dT00:00:00Z)` with the deployment's own
    schedule paused (B6). Zero `src/` change. **The previously-drafted variant of (b) — "schedule so the
    nominal floors to 00Z" — was WRONG and is withdrawn:** with no `cycle_time` the flow takes the cycle
    from `clock()` at *execution* time (`_resolve_cycle_time`, `run_forecast_cycle.py:665-671`);
    `scheduled_start_time` is used only to build the run name (`:1776-1783`), so a run that starts after
    06Z floors to the short 06Z candidate no matter how it was scheduled. Two consequences of (b), both
    accepted explicitly: `max_cycle_age_hours` **must be `< 6.0`** so `steps == 1` and the run is
    "today's 00Z or a hard `RecapDataUnavailableError`", never a silent walk-back to yesterday's 18Z; and
    an explicit `cycle_time` **suppresses the `FORECAST_FRESHNESS` heartbeat entirely**
    (`run_forecast_cycle.py:728-729`), so this stack has no freshness record to alert on — consistent with
    "Stage B is unmonitored" under Risks, and the reason (b) is the cheap option rather than the good one.
  - **(c) Wait for the Plan 151 resolver — NOT viable inside this plan's scope.** The new resolver projects
    tracks from an explicit `(assignment, model, station_weather_source)` join
    (`services/track_projection.py:29-33`), so a station with **no model assignment** (A2) yields zero
    tracks and therefore zero forcing; and Plan 151 T8b re-scopes legacy Phase A so it no longer
    fetches/persists for per-track-served stations
    (`docs/plans/151-forecast-redesign-phase3-track-resolution-assembly.md:910-925`). Choosing (c) means
    first creating a real active forcing-requiring assignment — i.e. Plan 139's model work — which changes
    this plan's scope. Recorded here so nobody re-proposes it as a cheap wait.

  **No task may assume an option until O2 is answered**, and B4 forks on the answer.
- **D6 — `max_cycle_age_hours`: scoped to the O2 answer, not a flat default.**
  - Under **(a)** only: `72.0`, not the `18.0` default (`recap_gateway.py:412`), which is
    structurally too small for this gateway. 72 h sits inside the measured ~4-day run retention, and with
    cadence `24.0` every walk-back candidate is itself a 00Z run. (Option **(c)** gets no value at all —
    B4 stops before writing any config under (c), so prescribing one here would be dead text.)
  - Under **(b)**: `max_cycle_age_hours` **must be `< 6.0`** — 6.0 being the hardcoded
    `_IFS_CADENCE_HOURS` (`recap_gateway.py:404`) that the adapter's walk-back actually steps by — so
    `steps == 1` (`:456`); use `5.0`. The `cycle_cadence_hours` TOML field is **dormant on this path (D5)
    and must NOT be set in the B2 overlay under (b)**: writing it would be silently ignored and would give
    false confidence that the walk-back had been bounded. A 72 h bound under (b) would let the resolver walk
    back into yesterday's 18Z/12Z/06Z the moment 00Z is one probe late — reproducing the exact
    short-horizon bug D5 exists to fix, triggered by lateness instead of time-of-day.
  This field is `RecapGatewayConfig.max_cycle_age_hours`, threaded straight into `resolve_latest_cycle`'s
  `max_age_hours` — a different path from `cycle_cadence_hours`, which is load-bearing only *after*
  option (a)'s code change. The assignment therefore lives in **B4** (the O2-implementing task), not as an
  unconditional value in B2.
- **D8 — Stage B's SHAPE is itself an open choice: cron'd direct call vs the full Prefect stack.**
  Three independent reviews have now said Stage B is heavier than its goal. The lighter design: run
  **Stage A's already-accepted technique** (`_fetch_nwp_task.fn(...)` with an explicit adapter and
  `station_configs` — `run_forecast_cycle.py:1105-1128`, plain parameters, decoupled from the
  deployment-wide `type` selector) from a host cron against a **small standing Postgres**, with no
  Prefect server, no worker, no `init` deployment registration. That deletes B1's dual-image-tag risk,
  B5's bring-up, B6's deployment-pause gate (nothing is registered, so nothing needs pausing), and most
  of B7 — leaving "is Postgres up, are the Swiss containers untouched".
  Note this is *not* an argument for seeding station `123` into the **Swiss production** DB: the real
  flow's operational scan (`run_forecast_cycle.py:2163-2181`) would hand a Nepal station to the global
  MeteoSwiss adapter. A separate store is required either way; only the Prefect layer is in question.
  **The heavier stack is only justified if the point is to rehearse the real Nepal-v1 deployment shape**
  — a legitimate goal, but a different one from "prove gateway forcing works daily", and nobody has
  stated it. **Recommendation: take the light design** unless the owner wants the rehearsal.
  Folded into O1 — answering "standing feed?" now also means answering "which shape?".
- **D7 — What this can and cannot prove.** ERA5-Land ends ~7 days back and the `operational`/`gap_fill`
  bridge is dead (appendix), so a model needing `past_known reanalysis/*` continuous to t₀ **cannot be
  fed operationally today**. This plan proves the **future/forcing** channel only. Settle the past-forcing
  question before anyone writes a model against it — it is a Plan 139 decision, not this plan's.

## Stage A — the cheap proof (do this first)

Goal: one live fetch that lands in the store, then throw the database away. No schedule, no image
rebuild, no production code change, no Prefect.

- **A1 — a disposable database** — a Postgres for this test only (its own Compose project, or any
  throwaway instance), migrated to head. Nothing else from the base topology is needed: no API, no
  caddy, no Prefect server or worker, no ingest worker, no backup worker. Cutting them also cuts the port
  collisions, the backup-cron problem and the deployment-registration problem entirely.
- **A2 — seed the minimum records** (D3, D4), via the stores named in D4. **No thresholds, no model
  assignment, no station group.** The complete seed transaction, in FK order:
  1. **tenant** — a fresh DB seeds `sapphire`; `stations.tenant_id` is non-null
     (`db/metadata.py:294-299`), so reuse that tenant rather than inventing one.
  2. **basin** — `code`/`name` (both non-null, `db/metadata.py:93-95`), `network`, and a **non-null
     `MULTIPOLYGON` `geometry`** (`:96-99`). Use a clearly labelled **disposable placeholder polygon** —
     a small square near 12300 is fine: nothing in this plan reads basin geometry (the Gateway does the
     basin averaging server-side), and the record is dropped at the end of Stage A. Do **not** pass it off
     as the real 12300 catchment.
  3. **station** — code `123`, `station_kind = 'river'`, `station_status = OPERATIONAL`,
     `gauging_status = 'ungauged'` (it has no gauge, and the column defaults to `gauged`
     — `db/metadata.py:283-290` — which would make the observation flow eligible), `basin_id` = the basin
     above, plus every other non-null column: `name`, a `POINT` `location`, `timezone`,
     `measured_parameters`, `network`, `ownership` (defaults `'own'`), `tenant_id`.
  4. **gateway binding** — one `GatewayPolygonBindingRow` (`types/station.py:113-131`) with
     `station_id`, `basin_id`, `gateway_hru_name = "12300"`, **`name = "g_123"`** (non-null,
     `db/metadata.py:380`; this is what the resolver returns as `polygon_name` — `recap_gateway.py:179-181`
     — **not** `basins.name`), `spatial_type = BASIN_AVERAGE`, `band_id = None`.
  5. **weather source** — exactly one active FORECAST row that survives `_prefilter`
     (`recap_gateway.py:585`): `nwp_source = "ifs_ecmwf"` (`:793`), `role = FORECAST`, `status = ACTIVE`,
     `extraction_type = BASIN_AVERAGE`. `fetch_forecast_binding` raises on **0 or ≥2**, so exactly one.
- **A3 — one direct fetch-and-store call. One seam, named exactly** (the old "flow *or* adapter" wording
  was not interchangeable — a task-level call has no `ForecastCycleResult.forecasts_stored`, and the full
  flow needs every production store):
  1. Construct `RecapGatewayForecastAdapter` directly (`recap_gateway.py:795-804`) with a real
     `RecapClient`, `StoreBackedGatewayPolygonResolver(<binding store>)`, and
     **`max_cycle_age_hours=0.0`** → `steps == 1` (`:456`), so the adapter probes **only** the nominal
     cycle: today's 00Z or a hard `RecapDataUnavailableError`. This is Stage A's D5 dodge, and it needs no
     config overlay. The API key comes from the `RECAP_API_KEY` env fallback
     (`config/recap_gateway.py:107`), exactly as the appendix probe does.
  2. Call `_fetch_nwp_task.fn(...)` (`run_forecast_cycle.py:1105-1128`) — `.fn` bypasses Prefect entirely
     — passing the adapter, the seeded `StationWeatherSource`, `cycle_time` = today 00:00Z, and a
     `PgWeatherForecastStore`. That task is the production persist path
     (`store_weather_forecasts`, `:1379/1461/1544`).
  Direct flow/adapter invocation is already an accepted deployment-test technique here
  (`docs/deployment/dress-rehearsal-2026-04-21.md`). Run it from a writable CWD — see the appendix.
- **A4 — assert the right things**, and only things this seam produces:
  - Forcing rows stored for station `123`, `nwp_source = "ifs_ecmwf"`, `spatial_type = 'basin_average'`.
  - Provenance `cycle_time` equals the 00Z cycle requested (guaranteed by `max_cycle_age_hours=0.0`, but
    assert it — a silent walk-back is the failure this plan exists to prevent).
  - **Per parameter, independently:** the `precipitation` and `temperature` member-0 series each reach
    **≥ 14 days** with the expected valid-time stamps. The adapter only requires each control variable to
    contribute ≥1 row (`control_coverage`, `recap_gateway.py:884-905`), so a single-parameter horizon
    assertion would pass on a truncated series.
  - **Units:** `weather_forecasts` stores `parameter` and a numeric `value` with **no unit column**
    (`db/metadata.py:757-780`), so units cannot be read back directly. Prove the conversion instead by
    capturing the raw Gateway frame in the same run and comparing selected stored values against it:
    `precipitation ≈ raw × 1000` (`_metres_to_mm`, `recap_gateway.py:48-49`) and
    `temperature ≈ raw − 273.15` (`_kelvin_to_celsius`, `:52-53`).
  - **No flow-health assertion at all.** Stage A never runs the flow, so there is no
    `ForecastCycleResult`, no `forecasts_stored` and no `FORECAST_FRESHNESS` record — and for the record,
    a *flow* run pinned to an explicit `cycle_time` would emit no freshness record either
    (`run_forecast_cycle.py:728-729`), so the earlier "expect a CRITICAL record" criterion was
    unsatisfiable. (Separately: `PipelineHealthStatus` has no `HEALTHY` value — it is `OK`/`WARNING`/
    `CRITICAL`, `types/enums.py:183`.)

**Exit:** a 00Z IFS series for 12300, ≥14 d in both parameters, in the store, from a real gateway call.
Then drop the DB. **If Stage A fails, Stage B was never worth building.**

## Stage B — the standing daily feed (only if the owner wants one)

Everything here exists to make Stage A repeat unattended. Skip the whole stage if the answer to O1 is
"a one-off proof is enough."

**Ordering rule (load-bearing):** nothing in this stack *executes* a flow until the O2 decision is
implemented and merged (B4), the deployments are enumerated and the unwanted ones paused (B6), and the
non-interference gate has passed (B7). `init` registers **every** deployment with a default 6-hourly
forecast cron, and `prefect-worker` `depends_on: init: service_completed_successfully`
(`docker-compose.yml:108-112`) — so starting the worker with the stack means executing that cron
immediately, on whatever image and config happen to be present.

- **B1 — the Nepal Compose overlay** (written, not started). `-p sapphire-nepal` with the base file plus
  `docker-compose.recap.yml` (which already appends the `sapphire_dg_api_key` secret; the app reads
  `/run/secrets/sapphire_dg_api_key`, `config/recap_gateway.py:25`). Must NOT include
  `docker-compose.macmini.yml` (Swiss BAFU collectors, camels-ch bind, backup binds).
  - **Image tag (mandatory, D2):** override `image:` to `sapphire-flow-nepal:${VERSION}` on every app
    service this project runs (`prefect-worker`, `init`). Compose replaces a scalar `image` from the later
    file, while `build: *app-build` still merges from the base — so `--build` here tags the Nepal name and
    **cannot** touch `sapphire-flow:${VERSION}`, the tag the live Swiss containers run
    (`docker-compose.yml:82,355`). A distinct Nepal `VERSION` value is an acceptable equivalent; a plain
    `--build` against the base tag is not. Trade-off, accepted: the Nepal image is built and stored
    separately (layer cache still shared), costing one extra image on the host.
  - **No `api`, no `caddy` — and therefore no port problem at all.** Nothing in this plan reads through
    the API: B8's acceptance criteria are direct store reads (identical to A4), and alerts fire from
    inside the forecast-cycle flow as webhooks, not via the API server. This matches A1's scope
    exactly rather than silently widening it. Dropping `api` also drops `caddy`, which `depends_on: api`
    (`docker-compose.yml:326-328`), and with caddy gone so does the Compose `ports`-concatenation trap
    (base publishes `80`/`443`, `:319-321`, which would collide with the live Swiss caddy). Exclude both
    by simply never naming them in an `up` command — and, so a stray `up` cannot start them, assign both a
    `profiles:` entry in the Nepal overlay. **If the owner wants Nepal results queryable externally that
    is a new requirement (O4), to be decided rather than built in unexamined.**
  - **Config:** `SAPPHIRE_CONFIG_OVERLAY` **plus a bind mount** of the overlay TOML into every worker
    that reads it — `config/` is not in the image (`Dockerfile:130-133` copies only `src/`,
    `alembic.ini`, `alembic/`, venv), exactly as the Swiss overlay does both
    (`docker-compose.macmini.yml:23-29`). Also set `SAPPHIRE_REQUIRE_NWP=1`, which otherwise comes only
    from the Swiss overlay. Keep `writable_tenants = ["sapphire"]`.
  - **Services omitted from this project:** `api`, `caddy`, `prefect-worker-ingest` and
    `prefect-worker-backup`. The two omitted workers' deployments (`ingest-observations`,
    `collect-bafu-observations` on the `ingest` pool;
    `backup-database` on the `backup` pool — `cli/register_deployments.py:96-116,175-183`) are still
    registered but have no worker, so they never execute. This does **not** cover the `default` pool —
    see B6.
- **B2 — `config/overlays/mac-mini-nepal.toml`** — `type = "recap_gateway"`, the
  `[adapters.recap_gateway]` section, `[deployment]` identity. **`max_cycle_age_hours` is deliberately
  NOT fixed here** — its value depends on the O2 answer (D6) and is set in B4.
- **B3 — stage the API-key secret** from the key already on the host
  (`~/.config/sapphire/recap_api_key`), `0600`, never committed.
- **B4 — implement the O2 decision, and set `max_cycle_age_hours` per D6. Nothing starts until this is
  done (and, for option (a), merged).**
  - **If (a):** thread `cycle_cadence_hours` into the adapter — a `src/` change, therefore hold-at-PR.
    **The red-first test MUST target `RecapGatewayForecastAdapter`, not `resolve_latest_cycle`.**
    Construct the adapter with `cycle_cadence_hours=24.0` and drive its public fetch path (or
    `_resolve_effective_cycle`) with a fake client returning a short 06Z frame and a full older 00Z
    frame; assert it resolves to 00Z. **A test written against `resolve_latest_cycle` alone is vacuous
    and passes today** — that function already accepts `cadence_hours` (`recap_gateway.py:434-440`) and
    already walks back correctly on whatever cadence it is given (`_floor_to_cadence` is epoch-aligned,
    so 24.0 floors to UTC midnight and never probes 06/12/18Z). The defect lives one level up, in
    `_resolve_effective_cycle` (`:806-828`), which drops the cadence. The existing sibling file
    `tests/unit/adapters/test_recap_gateway_cycle_resolution.py` tests the resolver directly, so an
    implementer copying that pattern would produce an already-green test that locks nothing. Set
    `max_cycle_age_hours = 72.0` in the B2 overlay. The stack is built from the **merged** image (B5).
  - **If (b):** no `src/` change. Set `max_cycle_age_hours = 5.0` in the B2 overlay (D6), and write the
    host cron/launchd trigger that passes an explicit 00Z `cycle_time`. Record in the overlay comment
    that this stack emits **no** `FORECAST_FRESHNESS` record (`run_forecast_cycle.py:728-729`).
  - **If (c):** stop — (c) requires Plan 139 model work (D5), which is out of scope here.
- **B5 — build and bring up infrastructure only, WITHOUT the worker and WITHOUT the API.**
  `docker compose -p sapphire-nepal ... up -d postgres prefect-server init` — `init` migrates, bootstraps
  roles and registers deployments (`docker-compose.yml:353-362`), and nothing consumes the `default` pool
  yet. Set the schedule env vars this stack wants **on this `init` run** (`SCHEDULE_FORECAST_CYCLE` etc.,
  `docker-compose.yml:381-398`) rather than editing them afterwards. The build needs
  `export RECAP_DG_CLIENT_TOKEN=...` — the Dockerfile requires that build secret for `uv sync` and it is
  **not** the runtime gateway API key of B3. Build **only** with B1's `image:` override in place
  (`sapphire-flow-nepal:${VERSION}`) — an unqualified `--build` against the base tag would rebuild
  `sapphire-flow:${VERSION}`, the tag the Swiss stack runs (D2). Verify before building:
  `docker compose -p sapphire-nepal ... config | grep 'image:'` must show no bare `sapphire-flow:`.
- **B6 — deployment hygiene gate (blocking, before any worker starts).** Enumerate what `init` actually
  registered — `prefect deployment ls` — and **assert the full list: name, work pool, cron, paused
  state.** Then pause every schedule this stack does not want:
  `prefect deployment schedule pause '<flow>/<deployment>'` (Prefect 3.6.x). The `default` pool is the
  problem — omitting the ingest/backup workers (B1) does nothing for it, and three scheduled default-pool
  deployments would otherwise run against a stack that has exactly one station and no models:
  - `ingest-weather-history` — `default`, `0 6 * * *`; with no bound reanalysis source it writes a
    **CRITICAL** `WEATHER_HISTORY_INGEST` record for `no_stations_bound`
    (`flows/ingest_weather_history.py:434-444`) — a permanent false alarm.
  - `ingest-recap-reanalysis` — `default`, `0 5 * * *`; benign no-op plus an OK heartbeat
    (`flows/ingest_recap_reanalysis.py:425-435`).
  - `collect-bafu-forecasts` — `default`, hourly. Note `SCHEDULE_COLLECT_BAFU_FORECASTS` is **not** among
    the schedule env vars the compose `init` service passes through (`docker-compose.yml:381-398`), so the
    Python default `0 * * * *` (`cli/register_deployments.py:59`) applies regardless of what the overlay
    sets — pausing is the only lever. (It no-ops without an archive path, but a paused schedule is the
    assertion, not a hoped-for no-op.)
  - `forecast-cycle` itself — **paused under option (b)** (it is triggered externally with an explicit
    `cycle_time`); left scheduled under option (a) at the O3 time.
  The remaining `default`-pool deployments carry no cron at all (`train-models`, `run-hindcast`,
  `compute-skills`, `compute-combined-skills`, `onboard-stations`, `onboard-model`,
  `import-model-artifact`) and need nothing done.
- **B7 — non-interference gate (blocking).** Snapshot before B5 and re-check after B6: the Swiss
  containers are unchanged, volume sets are disjoint, the Swiss API is still healthy on `:8000`, and the
  Swiss overlay assertion still passes (overlay path + both `bafu_*_archive_path` non-None). **Add an
  image-tag assertion (D2/B1):** the image ID behind `sapphire-flow:${VERSION}` is byte-identical before
  and after (`docker image inspect -f '{{.Id}}' sapphire-flow:${VERSION}`), and every Nepal container was
  created from `sapphire-flow-nepal:*`.
- **B8 — start the worker, then verify one live cycle** — `up -d prefect-worker` (no `api` and no
  `caddy`: nothing in this plan reads through them — B1), then trigger or await exactly one forecast cycle and assert **exactly what A4 asserts** (per-parameter ≥14 d,
  00Z provenance, unit conversion against the raw frame). Under option (a) only, additionally assert the
  `FORECAST_FRESHNESS` accounting: a cycle with no explicit `cycle_time` and zero forecasts stored writes
  a **CRITICAL** record (`run_forecast_cycle.py:728-733`, call site `:3020-3025`) — expected here, because
  with no model assigned the station is deliberately skipped (`:2406-2410`). Under option (b) assert the
  opposite: **no** `FORECAST_FRESHNESS` row for this cycle. Re-run B7 afterwards.

## Dependency graph

Tasks within a phase run in parallel unless stated otherwise (`docs/workflow.md`). **Stage A's and
Stage B's tasks are strictly sequential** — the ordering is load-bearing, not incidental (see Stage B's
ordering rule).

```json
{
  "phases": [
    { "id": "stage-a", "tasks": ["A1","A2","A3","A4"], "depends_on": [], "parallel": false },
    { "id": "decide",  "tasks": ["O1","O2","O3"],      "depends_on": ["stage-a"], "parallel": false,
      "gate": "owner answers O1 (build Stage B at all?), O2 (cycle-resolution option) and O3 (daily schedule / trigger time); all three are inputs to B4-B6" },
    { "id": "stage-b", "tasks": ["B1","B2","B3","B4","B5","B6","B7","B8"], "depends_on": ["decide"],
      "parallel": false, "optional": true,
      "gate": "B4 must be implemented AND (for option (a)) merged before B5 builds/starts anything; no worker starts before B6 and B7 pass" }
  ]
}
```

## Risks

- **Gateway run retention is ~4 days.** Under option (a), three missed daily runs exhaust the walk-back
  window (D6's 72 h). Under option (b) there is no walk-back at all by design — a missed 00Z is a missed
  day. Retries do not help either way.
- **`pf` and JSNOW forecast lag `fc` by ~1 day.** Irrelevant while this is control-only; a hard
  constraint the moment an ensemble or snow-fed model is added.
- **Stage B is unmonitored.** The host watchdog probes `localhost:8000` (the Swiss API) and the launchd
  start script manages only the Swiss file set, so this stack is **not launchd-managed, not reconciled and
  not monitored** — nothing brings it back if it is stopped, nothing notices if it fails, and nothing
  reconciles it after a host rebuild. (Its containers do carry `restart: unless-stopped`
  — `docker-compose.yml:42,126` — so an already-running stack will generally come back with Docker after a
  reboot; that is Docker's doing, not this stack's, and it does not restore an intentionally-stopped or
  never-started stack.) Under option (b) there is additionally no `FORECAST_FRESHNESS` record to alert on
  (D5). Acceptable for a test basin on a host with a documented 14-day-silent-outage history; revisit the
  moment anything depends on it.
- **Isolation is good, not total.** Volumes, networks, Prefect state and (per D2/B1) the image tag are
  separated; both stacks still share the repo bind mounts and the secret source files on the host. Those
  two are read-only inputs, not mutable state, so a Nepal action cannot alter what Swiss is running —
  editing a shared source file is a deliberate host-level act, not a side effect of `up`/`--build`.

## Open questions for the owner

- **O1 (decides everything below it)** — is a one-off proof (Stage A) enough, or do you want a standing
  daily feed? **And if standing: which shape (D8)** — the light cron'd direct call against a small
  standing Postgres (recommended), or the full second Prefect stack, which is only worth it if you want
  to rehearse the real Nepal-v1 deployment shape? Stage B as currently written assumes the heavy answer;
  the light answer deletes roughly half of it.
- **O2** — cycle resolution: D5 option (a) (code change, keeps the freshness heartbeat) or (b) (no code
  change, externally-triggered, no heartbeat)? Option (c) is not available without Plan 139 scope. Stage B
  cannot start without this — B4 and B6 both fork on it.
- **O4 (new, low-stakes)** — does anyone need to query Nepal results *externally* (HTTP), or is direct
  DB/store access enough? Stage B ships no `api`/`caddy` by default (B1). If yes, whatever implements it
  must handle the round-1 ports-concatenation trap (D2/B1) when adding those services back.
- **O3** — what daily time? Under (a) it is the `SCHEDULE_FORECAST_CYCLE` cron; under (b) it is the host
  trigger time. Either way it needs the measured 00Z publication time, and it is an input to B5/B6 —
  **not** something to settle after the stack is running.

## Related work

- **Plan 139** (DRAFT epic) — this plan is a cheap precursor to its W5/W8 question, not a delivery of it.
  139 can reuse this plan's deployment evidence; 139's model, training and target work stays there.
- Fixing the mac-mini probe install (repo plist + wrapper, ten minutes) would answer O3 and revive a
  31-day-dead longitudinal record. Not in this plan's scope; worth doing before B5.

## Appendix — evidence (2026-08-20)

Probed read-only inside the live worker container, so the API key never left the host:

```
docker exec -i --user app --workdir /tmp \
  -e RECAP_API_KEY="$(cat /Users/sapphire/.config/sapphire/recap_api_key)" \
  sapphire_flow-prefect-worker-1 python - < <probe>.py
```

`--workdir /tmp` is mandatory — the client writes its temp parquet to `Path.cwd()`
(`recap_client/http.py:178`) and `/app` is read-only. That one missing flag is why the installed host
probe logged **0 `ok=True` in 2433 records / 135 cycles / 31 days** (44 % `[Errno 30]`). A3 inherits this
constraint: run it from a writable CWD.

| Claim | Measurement (HRU 12300) |
|---|---|
| IFS `fc` 00Z is same-day | 84 rows, `2026-08-20T00:00Z → 2026-09-03T18:00Z`, steps `{3h: 48, 6h: 35}`, `tp` and `2t` |
| 06/12/18Z are short | 24 rows each (~2 d) — the reason D5 matters |
| Polygon column name | the numeric column for HRU 12300 is `g_123` — the value D3 requires in the binding's `name` |
| Run retention ~4 days | 08-16…08-20 present; 08-15 and older `source_data_missing` |
| `pf` lags ~1 day | today missing; 08-17/18/19 → 84 rows; `member` is `'1'`..`'50'` |
| ERA5-Land | edge `2026-08-13`; 2026-07-15→08-13 complete (720 rows, 30/30 days, 24 h each) |
| `operational`/`gap_fill` dead | ERA5 ends 08-13, IFS retained from 08-16 → the gap is coverable by nothing; `subdaily_resolution=3` → HTTP 500 |
| Host headroom | Swiss stack ≈1.1 GB of a 7.65 GiB Docker VM; disk 25 % used, 2.7 TB free |

Training-window and snow figures that justified Plan 139's target decision (307 complete days of `rof`
and ERA5 `tp`; winter SWE peak 1.87 m) belong to **139**, not here.
