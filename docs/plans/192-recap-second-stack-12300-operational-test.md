---
status: DRAFT
created: 2026-08-20
plan: 192
title: Second isolated mac-mini stack — operational recap-gateway forcing for HRU 12300
scope: Prove that recap Data Gateway forcing works end-to-end for Nepal test basin 12300 on the mac-mini, WITHOUT disturbing the live Swiss/BAFU stack. Stage A is a throwaway one-shot proof (disposable DB, manual run). Stage B — only if the owner wants a STANDING daily feed — is a second, isolated Compose stack running `[adapters.weather_forecast] type = "recap_gateway"`. Models, training and targets stay in Plan 139.
depends_on: [82, 120, 139]
blocks: []
supersedes: []
---

# Plan 192 — Second mac-mini stack: operational recap forcing for 12300

## Status

**DRAFT — two rounds of independent Codex review folded in (2026-08-20); needs `/plan` before READY.**
Grounded in a live gateway re-probe (evidence appendix) and a code re-read of the Flow-1 dispatch. This
plan owns only the **deployment** half of [Plan 139](139-nepal-12300-swe-regression-enablement.md)
(W5+W8); models, training and targets stay in 139.

**Round 1** (2 blockers, 8 majors) fixed the Compose `ports` concatenation trap and a backwards Phase 2.
**Round 2 (NEEDS_WORK) went further and was right to**, on two counts that reshaped this plan:

- **An identity error.** The basin loader derives the polygon name as `g_<normalized station_code>`
  (`services/basin_package_loader.py:774`), so `g_123` requires station code **`123`** — `12300` is the
  **Gateway HRU**, a different field. The plan had been calling 12300 the station code. Corrected
  throughout: station code `123`, `gateway_hru_name = "12300"`, polygon `name = "g_123"`.
- **Over-engineering.** Round 2's verdict — *"354 lines, 12 tasks, a production code change, a
  PR/merge/rebuild gate and a permanent schedule to demonstrate one live fetch-and-store"* — was fair.
  The plan is now **two stages**: Stage A proves the thing cheaply and throws the evidence away;
  Stage B builds the standing stack **only if the owner actually wants a daily feed**. Phase 4, the
  target decision (D7) and most of the appendix are cut — they are Plan 139's, and this plan already
  declared them non-goals while carrying them anyway.

## Why a second stack is the only config-level route

`[adapters.weather_forecast].type` is a **single deployment-wide selector** — one weather adapter per
stack (details and citations in D2). Making forcing per-station is Plan 115 (DRAFT) / Plan 139 W5, a
genuine subsystem change. Nothing else in the current code multiplexes adapters, so co-hosting 12300
with the Swiss stations on one stack is not possible today.

The measured gateway behaviour this plan depends on is in the **appendix**, not repeated here.

## Objective

Prove that recap Gateway forcing reaches the store for basin 12300 on the mac-mini, with the Swiss stack
provably untouched — cheaply first (Stage A), and only then, if the owner wants it, as a standing daily
feed (Stage B).

## Non-goals

- **Not** per-station forcing dispatch (Plan 115 / 139 W5). This plan routes around it.
- **Not** the discharge model, training, target or hindcast/skill — all Plan 139.
- **Not** real DHM discharge (blocked on the DHM questionnaire).
- **Not** multi-tenant Nepal production. One test basin, disposable by design.
- **Not** exercising the Plan 120 package importer (D4 seeds records directly).
- **Not** promoting the recap probe to a pipeline component (Plan 132 §Out of scope stands).

## Design decisions

- **D1 — Two stages, and Stage B is optional.** *Stage A* proves gateway forcing reaches the store for
  12300, using a disposable database and one manual invocation — no schedule, no code change, no PR.
  *Stage B* is the standing daily feed, and is worth building **only if the owner wants one running**
  (O1). Everything expensive in this plan lives in Stage B.
- **D2 — If Stage B happens, isolate by Compose project, not by code.** `[adapters.weather_forecast].type`
  is a single deployment-wide selector (`run_forecast_cycle.py:256`; validation `:390`; dispatch
  `:1957-1973`) — one weather adapter per stack, so flipping the mini's overlay would take NWP away from
  the Swiss stations. Per-station dispatch is Plan 115 (DRAFT) / 139 W5, a real subsystem change.
  `docker compose -p sapphire-nepal` gives its own volumes, network and DB (no compose file declares an
  explicit volume `name`/`external`), and is reversible with `down -v`.
- **D3 — Identity (corrected).** Station code **`123`**; basin polygon `name` **`g_123`** (the loader
  requires `g_<normalized station_code>` — `basin_package_loader.py:774`); binding
  `gateway_hru_name` **`"12300"`**, which is what the resolver passes as the Gateway `hru_code`
  (`recap_gateway.py:822`). Getting these three confused is the single easiest way to waste a day here.
- **D4 — Seed the records directly; do not manufacture a production basin package.** The importer is a
  Plan 120 concern and testing it proves nothing about gateway forcing. The needed set is small (see
  Stage A). This also sidesteps the fact that no checked-in package fits: the only one
  (`tests/fixtures/basin_static/nepal-dhm-basins/`) declares `gateway_hru_names = ["nepal_dhm_v1"]`, and
  `tests/` is excluded from the image anyway.
- **D5 — Cycle resolution: avoid the problem in Stage A, decide it in Stage B.** `resolve_latest_cycle`
  (`recap_gateway.py:434`) accepts the first candidate whose probe merely does not raise — it never
  checks the frame is non-empty, never probes `2t`, never checks horizon (`:452`). Because 06/12/18Z
  runs carry ~2 days against 00Z's ~15, a run at 08:00 UTC resolves the **06Z 2-day** run.
  `RecapGatewayConfig.cycle_cadence_hours` does **not** fix this: it reaches only the dormant
  `ForcingResolutionPolicy` (`run_forecast_cycle.py:542,555`), while the adapter constructor
  (`recap_gateway.py:795`) does not accept it and `_resolve_effective_cycle` (`:806`) calls the resolver
  without it, so `_IFS_CADENCE_HOURS = 6.0` applies.
  **Stage A dodges this entirely** by invoking with an explicit 00Z cycle. **Stage B must choose (O2):**
  (a) thread `cycle_cadence_hours` into the adapter and set `24.0` — a *temporary legacy-path exception*,
  removable when Plan 151's `services/track_resolution.py` resolver goes live; (b) schedule so the
  nominal floors to 00Z with `max_cycle_age_hours < 6` so it cannot walk back at all — simpler, but one
  missed run means no forcing; or (c) wait for the Plan 151 resolver. **No task may assume an option
  until O2 is answered.**
- **D6 — `max_cycle_age_hours = 72.0`, not the 18.0 default** (`recap_gateway.py:412`), which is
  structurally too small for this gateway. 72 h sits inside the measured ~4-day run retention.
- **D7 — What this can and cannot prove.** ERA5-Land ends ~7 days back and the `operational`/`gap_fill`
  bridge is dead (appendix), so a model needing `past_known reanalysis/*` continuous to t₀ **cannot be
  fed operationally today**. This plan proves the **future/forcing** channel only. Settle the past-forcing
  question before anyone writes a model against it — it is a Plan 139 decision, not this plan's.

## Stage A — the cheap proof (do this first)

Goal: one live fetch that lands in the store, then throw the database away. No schedule, no image
rebuild, no production code change.

- **A1 — a disposable database** — a Postgres for this test only (its own Compose project, or any
  throwaway instance), migrated to head. Nothing else from the base topology is needed: no API, no
  caddy, no ingest worker, no backup worker, no Prefect scheduling. Cutting them also cuts the port
  collisions, the backup-cron problem and the deployment-registration problem entirely.
- **A2 — seed the minimum records** (D3, D4). Confirmed sufficient by review + independent read —
  **no thresholds, no model assignment, no station group**:
  - a station: code `123`, `kind = RIVER`, `station_status = OPERATIONAL`,
    `gauging_status = 'ungauged'` (it has no gauge, and `gauged` would make the observation flow
    eligible — `db/metadata.py:283` defaults to `gauged`), tenant `sapphire` (a fresh DB seeds only
    that tenant, and stations require a non-null one);
  - a basin with `name = "g_123"`, and one basin-average gateway binding with
    `gateway_hru_name = "12300"`;
  - exactly one active FORECAST weather-source row that survives `_prefilter` (`recap_gateway.py:585`):
    `nwp_source = "ifs_ecmwf"` (`:793`), `role = FORECAST`, `status = ACTIVE`,
    `extraction_type = BASIN_AVERAGE`. `fetch_forecast_binding` raises on **0 or ≥2**, so exactly one.
- **A3 — one manual invocation** against an explicit 00Z cycle (dodging D5), persisting through the
  existing store path. Direct flow/adapter invocation is already an accepted deployment-test technique
  here (`docs/deployment/dress-rehearsal-2026-04-21.md`).
- **A4 — assert the right things.** Forcing rows stored for 12300; provenance cycle is the 00Z cycle
  requested; horizon ≥ 14 d; units canonical (mm, °C — the adapter converts m→mm, K→°C).
  **Do not assert a healthy forecast:** with no model assigned the station is deliberately skipped
  (`run_forecast_cycle.py:2406`), so `forecasts_stored = 0` and a **CRITICAL** `FORECAST_FRESHNESS`
  record are the designed state. And `PipelineHealthStatus` has no `HEALTHY` value at all — it is
  `OK`/`WARNING`/`CRITICAL` (`types/enums.py:183`).

**Exit:** a 00Z IFS series for 12300, ≥14 d, in the store, from a real gateway call. Then drop the DB.
**If Stage A fails, Stage B was never worth building.**

## Stage B — the standing daily feed (only if the owner wants one)

Everything here exists to make Stage A repeat unattended. Skip the whole stage if the answer to O1 is
"a one-off proof is enough."

- **B1 — the Nepal Compose overlay.** `-p sapphire-nepal` with the base file plus
  `docker-compose.recap.yml` (which already appends the `sapphire_dg_api_key` secret; the app reads
  `/run/secrets/sapphire_dg_api_key`, `config/recap_gateway.py:25`). Must NOT include
  `docker-compose.macmini.yml` (Swiss BAFU collectors, camels-ch bind, backup binds).
  - **Ports:** publish the API on `8001`. Base `api` declares no `ports` (`docker-compose.yml:262`) —
    the `8000:8000` binding comes only from the excluded Swiss overlay — so a plain `8001:8000` is
    enough. **Caddy is the trap:** base publishes `80`/`443` (`:319-321`) and Compose **concatenates**
    `ports`, so a plain overlay yields `['80','443','8081','8444']` and collides with the live Swiss
    caddy. Profile caddy out of this project (preferred — LAN-only, API published directly); if it is
    kept, it needs `ports: !override` and only `8081:80` (the default `Caddyfile` listens on `:80`
    unless `SAPPHIRE_DOMAIN` is set).
  - **Config:** `SAPPHIRE_CONFIG_OVERLAY` **plus a bind mount** of the overlay TOML into every worker
    that reads it — `config/` is not in the image (`Dockerfile:130-133` copies only `src/`,
    `alembic.ini`, `alembic/`, venv), exactly as the Swiss overlay does both
    (`docker-compose.macmini.yml:23-29`). Also set `SAPPHIRE_REQUIRE_NWP=1`, which otherwise comes only
    from the Swiss overlay. Keep `writable_tenants = ["sapphire"]`.
  - **Unwanted schedules:** base init registers **every** deployment and there is no selector —
    `_build_specs()` returns all of them unconditionally and the CLI takes no argument or env filter
    (`cli/register_deployments.py`). The cheap route is therefore **not** to run their workers:
    `ingest-observations` and `collect-bafu-observations` are on the `ingest` pool and `backup-database`
    on the `backup` pool, each served by its own container (`docker-compose.yml:151,210`). Omit those two
    services and those deployments are registered but never execute. `collect-bafu-forecasts` shares the
    `default` pool with `forecast-cycle`, but no-ops without an archive path this overlay will not set.
    Seeding the station `ungauged` (A2) is a second, independent guard.
- **B2 — `config/overlays/mac-mini-nepal.toml`** — `type = "recap_gateway"`, the
  `[adapters.recap_gateway]` section, D6's `max_cycle_age_hours`, `[deployment]` identity.
- **B3 — stage the API-key secret** from the key already on the host
  (`~/.config/sapphire/recap_api_key`), `0600`, never committed.
- **B4 — bring up + migrate**, then the **non-interference gate (blocking)**: before and after, the
  Swiss containers are unchanged, volume sets are disjoint, the Swiss API is still healthy on `:8000`,
  and the Swiss overlay assertion still passes (overlay path + both `bafu_*_archive_path` non-None).
- **B5 — the cycle-resolution decision from O2**, implemented. If option (a), it is a code change and
  therefore hold-at-PR, with a red-first test asserting the **exact candidate probe calls and the
  resolved horizon** (a 06Z short run present alongside an older 00Z full run must resolve to 00Z).
  Note the provenance detail: `floor_to_ifs_cadence` (`recap_gateway.py:422`) is used to decide
  fallback-vs-nominal (`run_forecast_cycle.py:1484-1488`) and hardcodes 6 h flooring, so a 00Z
  resolution is mislabelled a fallback whenever the nominal time floors to 06Z/12Z/18Z.
- **B6 — merge, rebuild, redeploy — an explicit gate.** Source is baked in at build time, and B4 has
  already started the stack from an older image, so nothing verifies until a human merges and the
  **Nepal project only** is rebuilt. The rebuild needs `export RECAP_DG_CLIENT_TOKEN=...` — the
  Dockerfile requires that build secret for `uv sync` and it is **not** the runtime gateway API key of
  B3. Beware the shared `sapphire-flow:${VERSION}` image tag: an unqualified `--build` rebuilds the tag
  the Swiss stack also runs.
- **B7 — schedule and verify** the daily cycle, asserting exactly what A4 asserts. Timing depends on the
  measured 00Z publication time (O3).

## Dependency graph

Tasks within a phase run in parallel unless stated otherwise (`docs/workflow.md`). **Stage A's and
Stage B's tasks are strictly sequential** — the ordering is load-bearing, not incidental.

```json
{
  "phases": [
    { "id": "stage-a", "tasks": ["A1","A2","A3","A4"], "depends_on": [], "parallel": false },
    { "id": "decide",  "tasks": ["O1","O2"],           "depends_on": ["stage-a"], "parallel": false,
      "gate": "owner answers O1 (build Stage B at all?) and O2 (cycle-resolution option)" },
    { "id": "stage-b", "tasks": ["B1","B2","B3","B4","B5","B6","B7"], "depends_on": ["decide"],
      "parallel": false, "optional": true }
  ]
}
```

## Risks

- **Gateway run retention is ~4 days.** Three missed daily runs exhaust the walk-back window. D6
  mitigates; retries do not.
- **`pf` and JSNOW forecast lag `fc` by ~1 day.** Irrelevant while this is control-only; a hard
  constraint the moment an ensemble or snow-fed model is added.
- **Stage B is unmonitored.** The host watchdog probes `localhost:8000` (the Swiss API), and the launchd
  start script manages only the Swiss file set, so this stack will not restart after a reboot and can
  die unnoticed — on a host with a documented 14-day-silent-outage history. Acceptable for a test
  basin; revisit the moment anything depends on it.
- **Isolation is good, not total.** Volumes, networks and Prefect state are per-project, but both stacks
  share the repo bind mounts, the secret source files and the `sapphire-flow:${VERSION}` image tag.

## Open questions for the owner

- **O1 (decides everything below it)** — is a one-off proof (Stage A) enough, or do you want a standing
  daily feed (Stage B)? Stage B is most of the cost and all of the ongoing risk.
- **O2** — cycle resolution: D5 option (a), (b) or (c)? Stage B cannot start without this.
- **O3** — what daily schedule? Needs the measured 00Z publication time.

## Related work

Fixing the mac-mini probe install (repo plist + wrapper, ten minutes) would answer O3 and revive a
31-day-dead longitudinal record. Not in this plan's scope; worth doing before B7.

## Appendix — evidence (2026-08-20)

Probed read-only inside the live worker container, so the API key never left the host:

```
docker exec -i --user app --workdir /tmp \
  -e RECAP_API_KEY="$(cat /Users/sapphire/.config/sapphire/recap_api_key)" \
  sapphire_flow-prefect-worker-1 python - < <probe>.py
```

`--workdir /tmp` is mandatory — the client writes its temp parquet to `Path.cwd()`
(`recap_client/http.py:178`) and `/app` is read-only. That one missing flag is why the installed host
probe logged **0 `ok=True` in 2433 records / 135 cycles / 31 days** (44 % `[Errno 30]`).

| Claim | Measurement (HRU 12300) |
|---|---|
| IFS `fc` 00Z is same-day | 84 rows, `2026-08-20T00:00Z → 2026-09-03T18:00Z`, steps `{3h: 48, 6h: 35}`, `tp` and `2t` |
| 06/12/18Z are short | 24 rows each (~2 d) — the reason D5 matters |
| Run retention ~4 days | 08-16…08-20 present; 08-15 and older `source_data_missing` |
| `pf` lags ~1 day | today missing; 08-17/18/19 → 84 rows; `member` is `'1'`..`'50'` |
| ERA5-Land | edge `2026-08-13`; 2026-07-15→08-13 complete (720 rows, 30/30 days, 24 h each) |
| `operational`/`gap_fill` dead | ERA5 ends 08-13, IFS retained from 08-16 → the gap is coverable by nothing; `subdaily_resolution=3` → HTTP 500 |
| Host headroom | Swiss stack ≈1.1 GB of a 7.65 GiB Docker VM; disk 25 % used, 2.7 TB free |

Training-window and snow figures that justified Plan 139's target decision (307 complete days of `rof`
and ERA5 `tp`; winter SWE peak 1.87 m) belong to **139**, not here.
