---
status: DRAFT
created: 2026-08-20
plan: 192
title: Second isolated mac-mini stack — operational recap-gateway forcing for HRU 12300
scope: Stand up a SECOND, isolated Docker Compose stack on the mac-mini running `[adapters.weather_forecast] type = "recap_gateway"`, so Nepal test basin 12300 can be forced operationally from the recap Data Gateway WITHOUT disturbing the live Swiss/BAFU stack. Delivers the stack, the 12300 records the adapter needs, and a scheduled daily cycle that provably stores IFS forcing for 12300. The discharge model itself is Phase 4 / a follow-on plan.
depends_on: [82, 120, 139]
blocks: []
supersedes: []
---

# Plan 192 — Second mac-mini stack: operational recap forcing for 12300

## Status

**DRAFT — round 1 of independent Codex review folded in (2026-08-20); still needs `/plan` before READY.**
Grounded in a live gateway re-probe (2026-08-20, evidence appendix below) and a code re-read of the
Flow-1 dispatch. This plan owns the **deployment-isolation** half of
[Plan 139](139-nepal-12300-swe-regression-enablement.md) W5+W8; it does **not** re-open 139's model,
training or target workstreams.

**Round-1 review (Codex, read-only, repo-grounded) returned NEEDS_WORK with 2 blockers + 8 majors; all
were verified against the code before folding.** The two blockers were real and are fixed here: Compose
`ports` **concatenate** rather than override (empirically rendered: adding `8081:80` to the base yields
host ports `80, 443, 8081, 8444` — the Nepal stack would have collided with the live Swiss caddy on 80/443),
and Phase 2 was **ordered backwards** (the basin importer resolves the station first and holds the whole
package if it is absent). The review also correctly killed a false claim (§D8's "no backup service") and
showed the stack would have registered every operational schedule, including 5-minutely Swiss observation
ingest. The central argument — the adapter selector is deployment-wide — was independently **VERIFIED**.

## Why a second stack (the structural constraint)

`[adapters.weather_forecast].type` is a **single deployment-wide selector**
(`run_forecast_cycle.py:256`, `_WEATHER_FORECAST_TYPES = ("meteoswiss_nwp", "recap_gateway")`; default
at `:257`; config **validation** at `:390`; the actual **dispatch** — one adapter built from one
selector — at `:1957-1973`). There is exactly one weather-forecast adapter per stack.
Flipping the mini's overlay to `recap_gateway` would take NWP away from the two live Swiss stations.

Making forcing per-station is Plan 115 (`status: DRAFT`) / Plan 139 W5 — a genuine subsystem change,
not a config edit. A second Compose project reaches the same operational goal today, is config-only,
and is reversible with `docker compose -p <name> down -v`.

## Live evidence (probed 2026-08-20, HRU 12300)

The mac-mini's launchd probe has been reporting false negatives since it was installed: the agent runs
`/Users/sapphire/recap-probe/run.sh` (a 2026-07-20 snapshot), **not** the repo wrapper Plan 132
reconciled it to. That copy omits `--workdir /tmp`, so the client's `Path.cwd() / .tmp_recap_*.parquet`
(`recap_client/http.py:178`) lands in `/app`, which is `read_only: true` — `[Errno 30]`, logged as
failure. **0 `ok=True` records in 2433 rows / 135 cycles; 44 % were that bug.** Re-running the *repo*
wrapper once gave a completely different picture:

| Product | Newest | Coverage | Note |
|---|---|---|---|
| IFS `fc` 00Z | **same day** | 84 steps → +14.75 d | 48×3 h then 35×6 h; `tp` and `2t` both |
| IFS `fc` 06/12/18Z | day−1 | **24 steps ≈ 2 d** | short runs |
| IFS `pf` 1–50 | day−1 | 84 steps | ~1 d behind `fc` |
| JSNOW `swe`/`hs`/`rof` fcst | day−1 | 241 hourly → +10 d | all three subscribed |
| ERA5-Land | day−7 | complete: 30/30 days, all 24 h | history ≥ 2025-08 |
| JSNOW reanalysis | ~day−7 | deep history, ragged at the edge | needs `allow_missing_data=True` |
| `ecmwf.operational` / `gap_fill` / `snow.operational` | — | **hard-fail** | see D6 |

IFS run retention is **~4 days** (runs older than day−4 return `source_data_missing`). The stitched
`operational` endpoints fail because ERA5 ends at day−7 while IFS retention starts at day−4: the two
intervening days are coverable by nothing. Not a parameter problem. Separately,
`subdaily_resolution=3` returns **HTTP 500** (report upstream).

Training-window check for the Plan 139 W1a proxy target: JSNOW `rof` reanalysis and ERA5-Land `tp` each
return **307 complete days** (2025-10-01 → 2026-08-03, zero missing days) for `g_123`; winter SWE peaks
at 1.87 m. So W1a is empirically viable — that gating decision can close.

## Objective

A second stack on the mac-mini that, once a day, resolves the 00Z IFS cycle for HRU 12300, fetches
`tp` + `2t` through `RecapGatewayForecastAdapter`, and stores basin-average forcing for a 12300 station
— with the Swiss stack provably untouched.

## Non-goals

- **Not** per-station forcing dispatch (Plan 115 / 139 W5). This plan routes around it.
- **Not** the discharge model, training, or hindcast/skill (139 W4/W6; Phase 4 below).
- **Not** real DHM discharge (blocked on the DHM questionnaire).
- **Not** multi-tenant Nepal production. One test basin, one throwaway stack.
- **Not** promoting the recap probe to a pipeline component (Plan 132 §Out of scope stands).

## Design decisions

- **D1 — Isolate by Compose project, not by code.** `docker compose -p sapphire-nepal -f docker-compose.yml
  -f docker-compose.nepal.yml -f docker-compose.recap.yml`. Compose prefixes named volumes and the
  network by project, so the second stack gets its own `pgdata`, its own Postgres, its own Prefect
  server/worker — no shared state with `sapphire_flow-*`, and no tenant-isolation work required.
- **D2 — Host ports, and they MUST use `!override`.** The base file binds caddy `80:80`/`443:443`
  (`docker-compose.yml:319-321`); the macmini overlay binds the API `8000:8000`
  (`docker-compose.macmini.yml:48-49`). **Compose CONCATENATES `ports` across files — it does not
  replace them.** Verified by rendering: a plain overlay adding `8081:80`/`8444:443` produces published
  host ports `['80', '443', '8081', '8444']`, i.e. an immediate collision with the live Swiss caddy.
  With `ports: !override` the same render yields `['8081']` for caddy and `['8001']` for the API.
  So: `api` → `!override` `8001:8000`; caddy → `!override` `8081:80` **only** (the default `Caddyfile`
  listens on `:80` unless `SAPPHIRE_DOMAIN` is set, so publishing `8444:443` buys nothing), or profile
  caddy out of the Nepal project entirely — preferred, since this stack is LAN-only and the API is
  published directly. The overlay must NOT include `docker-compose.macmini.yml` (that one carries the
  Swiss BAFU collectors, the camels-ch bind and the backup binds).
- **D3 — Its own config overlay**, `config/overlays/mac-mini-nepal.toml`, setting
  `[adapters.weather_forecast] type = "recap_gateway"`, an `[adapters.recap_gateway]` section, and a
  `[deployment]` identity block. The Swiss `mac-mini.toml` is not touched.
  **`SAPPHIRE_CONFIG_OVERLAY` alone is not enough: `config/` is NOT in the image** (the Dockerfile
  copies only `src/`, `alembic.ini`, `alembic/` and the venv — `Dockerfile:130-133`). Every worker that
  reads the overlay needs a bind mount of the TOML, exactly as the Swiss overlay does
  (`docker-compose.macmini.yml:23-29` sets the env var **and** mounts the file). The Nepal overlay must
  also carry `SAPPHIRE_REQUIRE_NWP=1` itself — that flag is supplied only by the Swiss macmini overlay,
  which this stack does not load.
  **Tenant:** keep `writable_tenants = ["sapphire"]`. A fresh DB seeds only the `sapphire` tenant
  (`alembic/versions/0041_tenants_table.py`), stations require a non-null tenant, and an unknown
  configured tenant code fails hard — declaring `dhm` would need a tenant seed/migration first.
- **D4 — 00Z-only cycle resolution — needs a small code change; the config field alone does NOT do it.**
  `RecapGatewayConfig.cycle_cadence_hours` exists (`config/recap_gateway.py`) but is threaded **only**
  into the dormant `ForcingResolutionPolicy` carrier (`run_forecast_cycle.py:542,555`, Plan 151 T8a).
  The live path — `_resolve_effective_cycle` (`recap_gateway.py:806`) → `resolve_latest_cycle`
  (`:434`) — is called **without** `cadence_hours`, so `_IFS_CADENCE_HOURS = 6.0` applies. Combined
  with a resolver that accepts the first candidate whose probe merely **does not raise** — it never
  checks the frame is non-empty, never probes `2t`, and never checks horizon (`recap_gateway.py:452`) —
  a run at 08:00 UTC resolves the **06Z 2-day** run instead of the **00Z 15-day** one. Options:
  - **(a) RECOMMENDED — thread `cycle_cadence_hours` into the adapter** (constructor arg → the
    `resolve_latest_cycle` call) and set `cycle_cadence_hours = 24.0`. Walk-back then floors to 00Z and
    steps in whole days. Contained to the recap path; the Swiss adapter is untouched. Implementation
    note: `floor_to_ifs_cadence` (`:422`) is used to decide whether a resolved cycle counts as a
    *fallback* for provenance (`run_forecast_cycle.py:1484-1488`; `:1482` is only the comment above it)
    — it must floor the same way. Scope, precisely: a 00Z resolution is mislabelled a fallback only when
    the **nominal** time floors to 06Z/12Z/18Z under the hardcoded 6 h flooring, which includes the
    proposed post-publication run time. It is not mislabelled when nominal itself floors to 00Z.
  - (b) Accept 6 h walk-back and schedule so the nominal cycle floors to 00Z, with
    `max_cycle_age_hours < 6` so it cannot walk back at all. Simpler, but one missed run = no forcing.
  - (c) Wait for Plan 151's `services/track_resolution.py` resolver to go live. Correct long-term
    owner, wrong timeline for this test.

  **Layering caveat (review finding, accepted).** The architecture direction is that walk-back policy
  belongs in `services/`, with `CandidateAwareForecastSource.fetch_requirement` an exact-cycle call
  (`protocols/adapters.py`, `recap_gateway.py:1050`). D4(a) deliberately adds a little more policy to the
  **legacy** adapter path. It is therefore recorded as a **temporary legacy-path exception with an
  explicit removal criterion**: delete it when Plan 151's services resolver goes live on the recap path.
  If the reviewer of this plan prefers, option (c) is the architecturally clean route at the cost of
  waiting on 151.
- **D5 — `max_cycle_age_hours = 72.0`, not the 18.0 default** (`recap_gateway.py:412`). With D4(a) that
  is 3 whole-day walk-back candidates, inside the measured ~4-day retention. The 18 h default is
  documented as structurally too small for this gateway.
- **D6 — Future forcing only in this plan; the past-forcing hole is NOT solved.** ERA5-Land ends at
  day−7 and the `operational`/`gap_fill` bridge is dead (evidence above), so a model declaring
  `past_known reanalysis/*` continuous to t₀ **cannot be fed operationally today**. Control-only,
  future-forcing models can. This is a hard constraint on Phase 4's model design and must be settled
  before a model is written, not after.
- **D7 — Target = JSNOW `rof` (Plan 139 W1a).** Empirically viable (307 complete days). Any forecast
  produced is **modeled runoff, not observed discharge**, and must be reported as such.
- **D8 — No watchdog coverage, and backups must be actively DISABLED (not merely unconfigured).**
  The host watchdog probes `localhost:8000` (the Swiss API), so this stack is unmonitored — accepted for
  a throwaway basin, stated so it is not mistaken for an oversight.
  **Correction from review:** an earlier draft claimed omitting the macmini overlay means "no backup
  service". That is false — the dedicated backup worker lives in the **base** file
  (`docker-compose.yml:199`) and the base init service registers a backup schedule
  (`cli/register_deployments.py`). Omitting the Swiss overlay only drops the USB bind; the Nepal project
  would still get its own backup worker, volume, pool, deployment and cron. Disabling the *service*
  alone is insufficient because the schedule is still registered — see D9.
- **D9 — Register ONLY the forcing schedule; this stack is not a general deployment.** Base
  initialisation registers every operational deployment, including `ingest-observations` every five
  minutes (`cli/register_deployments.py:42,92`). A directly seeded station defaults to
  `gauging_status = 'gauged'` (`db/metadata.py:283`, `server_default="gauged"`), and the observation flow
  selects gauged operational river stations and would drive them against the **Swiss LINDAS endpoint**
  from the base `config.toml`. Two mitigations, both required: **(a)** seed 12300 as **`ungauged`** (it
  has no gauge anyway — this is honest, not a workaround), and **(b)** restrict which deployments this
  stack registers, so observation ingest, the BAFU collectors and the backup cron never run here.

## Phases and tasks

### Phase 1 — the stack

- **T1 — `docker-compose.nepal.yml`** — `ports: !override` per D2 (a plain override CONCATENATES);
  `SAPPHIRE_CONFIG_OVERLAY` **plus a bind mount** of the new overlay TOML into every worker that reads
  it (D3 — `config/` is not in the image); `SAPPHIRE_REQUIRE_NWP=1`; does NOT include
  `docker-compose.macmini.yml`. Disable the backup worker AND ensure its schedule is never registered
  (D8/D9) — the backup worker is in the **base** file, so omitting the Swiss overlay does not remove it.
  Restrict deployment registration to the forecast cycle (D9).
- **T2 — `config/overlays/mac-mini-nepal.toml`** — D3 + D4 + D5 values.
- **T3 — stage the API-key secret** — `docker-compose.recap.yml` already appends the
  `sapphire_dg_api_key` secret to both workers; the app reads `/run/secrets/sapphire_dg_api_key`
  (`config/recap_gateway.py:25`). Copy from the key file already on the host
  (`~/.config/sapphire/recap_api_key`), `0600`, never committed.
- **T4 — bring up + migrate** under `-p sapphire-nepal`; confirm Alembic head, API healthy on `:8001`.
- **T5 — non-interference gate (blocking)** — before AND after bring-up: Swiss stack container ids
  unchanged, `docker volume ls` shows two disjoint volume sets, the Swiss API still healthy on `:8000`,
  and the Swiss overlay assertion from the runbook still passes (overlay path + both
  `bafu_*_archive_path` non-None).

**Exit:** two stacks running side by side; Swiss stack demonstrably unchanged.

### Phase 2 — make 12300 exist (ORDER IS LOAD-BEARING)

Round-1 review caught that the original T6→T7 order was **backwards**. The Plan 120 importer resolves
`(network, station_code)` to a SAP3 station **first**; if the station is absent the basin is put on an
onboarding **hold**, and a fully-held package persists no basin and no provenance — it must be re-run
after onboarding (`services/basin_package_loader.py`, hold reason "unmatched to a SAP3 station"). The
importer itself then creates the basin, assigns `stations.basin_id`, **and writes the gateway polygon
binding** (`store/basin_importer.py`) — so the binding must NOT be created separately.

Correct sequence:

- **T6 — create the 12300 station FIRST** — `kind = RIVER`, `station_status = OPERATIONAL`,
  `gauging_status = 'ungauged'` (D9), tenant `sapphire` (D3), matching the package's
  `(network, station_code)`.
  **Open question O1:** build Plan 139 W3's gateway-fed onboarding path (reusable, does not exist), or
  seed the row directly for one test basin (cheap, reversible)? Owner call.
- **T7 — import the basin package** (Plan 120 importer, `archive/120-basin-static-importer.md`). It
  creates the basin, sets `stations.basin_id`, and writes the polygon binding.
  **Blocking prerequisite:** the package must carry the right **gateway identity**. The resolver passes
  the binding's `gateway_hru_name` as the Gateway `hru_code`, while `name` is the polygon **column**
  (`adapters/recap_gateway.py:822` — `hru_code=probe_hru`). So this basin needs
  **`gateway_hru_name = "12300"`** and **`name = "g_123"`**. The only checked-in package does **not**
  fit — `tests/fixtures/basin_static/nepal-dhm-basins/` declares `gateway_hru_names = ["nepal_dhm_v1"]`
  and station code `123`, and `tests/` is excluded from the image anyway (`.dockerignore`). **T7 must
  name where the correct package comes from** — this is unresolved and is the most likely thing to
  block Phase 2 in practice.
- **T8 — the FORECAST weather-source row** — exactly one active binding that survives `_prefilter`
  (`recap_gateway.py:585`): `nwp_source = "ifs_ecmwf"` (`:793`), `role = FORECAST`,
  `status = ACTIVE`, `extraction_type = BASIN_AVERAGE`. `fetch_forecast_binding` raises on **0 or ≥2**
  FORECAST bindings, so exactly one.

**Exit:** the adapter resolves 12300 → `g_123` with `hru_code = "12300"`, no resolver miss, no hold.

**Minimum viable record set** (confirmed by review + independent read — nothing else is needed to fetch
and store forcing): a river/operational station with a valid tenant; exactly one active
`ifs_ecmwf`/`basin_average` FORECAST binding; a basin plus one basin-average gateway binding with the
identity above; migrations applied. **No thresholds, no model assignment, no station group.** Canonical
precipitation/temperature parameters are seeded by the initial migration, and weather-forecast storage
does not FK parameters.

### Phase 3 — prove operational forcing

- **T9 — cadence threading (code, hold-at-PR)** — D4(a) plus the `floor_to_ifs_cadence` provenance
  detail. Red-first test: assert the **exact candidate probe calls** and the **resolved horizon** — a
  06Z short run present alongside an older 00Z full run must resolve to 00Z. (Do not phrase the test as
  a short cycle being "offered" to a 24 h resolver; assert the call sequence.)
- **T10 — merge + rebuild + redeploy gate (NEW, from review).** T9 stops at a PR, but source is baked
  into the image at build time (`Dockerfile`), and T4 has already started the stack from an older image.
  Nothing verifies until a human merges and the **Nepal project only** is rebuilt and restarted. This is
  an explicit gate, not an implied step.
- **T11 — schedule** the daily forecast cycle on the Nepal stack. Timing: today's 00Z was already served
  at 09:27 UTC; the true publication time is unmeasured — **O2**, and the cheapest way to measure it is
  to fix the probe install first (see Related work).
- **T12 — verification, with the RIGHT signals.** Review corrected the original expectations, which were
  wrong in three ways. With no model assigned, Phase B skips the station by design, so:
  - **assert:** weather-forecast rows stored for 12300; provenance cycle is a **00Z** cycle; horizon
    ≥ 14 d; units canonical (mm, °C — the adapter converts m→mm, K→°C); `fallback_used` correctly
    labelled (see D4's scope note).
  - **expect and do not treat as failure:** `forecasts_stored = 0`, and a **CRITICAL**
    `FORECAST_FRESHNESS` pipeline record — that is the designed response to zero forecasts, not a bug.
  - **do not assert "health record is HEALTHY":** `PipelineHealthStatus` has no such value — it is
    `OK` / `WARNING` / `CRITICAL` (`types/enums.py`). `ForecastCycleResult.health` may report HEALTHY
    even with zero forecasts; it is not the signal that proves forcing landed.

**Exit:** a scheduled daily run stores a ≥14-day 00Z IFS forcing series for 12300, unattended, with the
zero-forecast state correctly explained rather than alarmed on.

### Phase 4 — a forecast (gated, likely its own plan)

Target ingest (D7), training-path wiring (139 W4), the model (139 W6), model onboarding. **Blocked on
D6** — settle what a model may declare given the 7-day past-forcing hole before writing one.

## Dependency graph

```json
{
  "phases": [
    { "id": "stack",   "tasks": ["T1","T2","T3","T4","T5"], "depends_on": [] },
    { "id": "records", "tasks": ["T6","T7","T8"],           "depends_on": ["stack"] },
    { "id": "forcing", "tasks": ["T9","T10","T11","T12"],   "depends_on": ["records"] },
    { "id": "model",   "tasks": [],                          "depends_on": ["forcing"], "gated_on": "D6" }
  ]
}
```

## Risks

- **Gateway retention is ~4 days.** Three missed daily runs and the walk-back window is exhausted.
  Mitigated by D5 (72 h) plus a health signal, not by retries.
- **`pf` and JSNOW forecast lag `fc` by ~1 day.** Irrelevant while this stack is control-only; it
  becomes a real constraint the moment an ensemble or snow-fed model is added.
- **Resource contention.** The Swiss stack idles at ~1.1 GB of a 7.65 GB Docker VM and the host has
  2.7 TB free, so headroom is fine — but the worker's `mem_limit: 8g` is effectively unbounded, and two
  stacks can now both claim it.
- **A second unmonitored stack on a host with a documented 14-day-silent-outage history** (D8). It can
  die without anyone noticing. Acceptable for a test; not acceptable if it becomes load-bearing.
- **"Fully isolated" is too strong.** The Compose project name does isolate named volumes and networks
  (no volume declares an explicit `name`/`external`), and each project runs its own Prefect server, so
  pool/deployment names cannot collide. But both projects still share the repo bind mounts, the secret
  source files, and the daemon-global `sapphire-flow:${VERSION}` **image tag** — so an unqualified
  `--build` can rebuild the tag the Swiss stack also runs. Nepal rebuilds must be project-scoped, and
  the host `start-sapphire.sh` launchd script manages only the Swiss file set (it will not restart this
  stack after a reboot).

## Open questions for the owner

- **O1** — seed the 12300 station directly, or build Plan 139 W3's reusable gateway-fed onboarding path?
- **O2** — what daily schedule? Needs the measured 00Z publication time (fix the probe to get it).
- **O3** — how long does this stack live? Decides D8/D9 effort (backups, monitoring) and O1.
- **O4** — D4 option (a) legacy-path exception with a removal criterion, (b) no walk-back at all, or
  (c) wait for Plan 151's services resolver?
- **O5 (NEW, likely the real blocker)** — where does an accepted basin package with
  `gateway_hru_name = "12300"` / `name = "g_123"` / matching station code come from? The only
  checked-in package is `nepal_dhm_v1` / station `123`. Without this, Phase 2 cannot complete.

## Related work (not in scope, but adjacent)

**Fix the mac-mini probe install** — copy `scripts/launchd/ch.hydrosolutions.sapphire-recap-probe.plist`
and reload the agent so it runs the repo wrapper instead of the July snapshot. Ten minutes, and it turns
a 31-day dead experiment into the longitudinal record that would answer O2 and validate the retention
and lag assumptions this whole plan rests on. Recommended **before** Phase 3.

## Appendix — evidence for the empirical claims (2026-08-20)

Round-1 review correctly noted that D5–D7, the schedule and the risk profile all rest on numbers that
exist nowhere in the repo. Recorded here so they are reproducible and falsifiable.

**How they were obtained.** All probes ran read-only inside the live worker container on the mac-mini,
so the API key never left the host and never entered a log:

```
docker exec -i --user app --workdir /tmp \
  -e RECAP_API_KEY="$(cat /Users/sapphire/.config/sapphire/recap_api_key)" \
  sapphire_flow-prefect-worker-1 python - < <probe>.py
```

`--workdir /tmp` is **mandatory**: the client writes its temp parquet to `Path.cwd()`
(`recap_client/http.py:178`) and `/app` is `read_only`. That single missing flag is what made the
installed host probe report 31 days of false negatives.

**Results (HRU 12300, `g_123`):**

| Claim | Measurement |
|---|---|
| IFS `fc` 00Z same-day | 84 rows, `2026-08-20T00:00Z → 2026-09-03T18:00Z`, steps `{3h: 48, 6h: 35}`, `tp` and `2t` |
| 06/12/18Z are short | 24 rows each (~2 d); today's not yet published at 09:30 UTC |
| Run retention ~4 days | run_date 08-16…08-20 present; **08-15 and older** → `source_data_missing` |
| `pf` lags `fc` ~1 day | today → missing; 08-17/18/19 → 84 rows; `member` must be `'1'`..`'50'` (`'51'` → `invalid_date_range`) |
| JSNOW fcst lags ~1 day | `swe`/`hs`/`rof` today → missing; 08-18/19 → 241 hourly rows (+10 d) |
| ERA5-Land edge + completeness | edge `2026-08-13`; 2026-07-15→08-13 = **720 rows, 30/30 days, every day 24 h, no gaps** |
| ERA5-Land depth | 2025-08 ✅, 2026-01 ✅; **2020-06 and 2015-06 → `source_data_missing`** |
| JSNOW reanalysis ragged | deep history (2025-03 ✅) but holes near the edge (e.g. 08-05) → needs `allow_missing_data=True` |
| `operational`/`gap_fill` dead | ERA5 ends 08-13, IFS retained from 08-16 → the intervening days are coverable by nothing; every legal `start_date` fails |
| `subdaily_resolution=3` | **HTTP 500** (report upstream) |
| W1a target viable | `rof` and ERA5 `tp` each **307 complete days**, 2025-10-01→2026-08-03, **0 missing**; daily rof mean 4.4, max 52.8 |
| SWE is not flat | winter 2025-11-01→2026-04-30: max **1.866 m**, 828 non-zero hours (short summer windows read 0.0 — do not generalise from those) |
| Host headroom | Swiss stack ≈1.1 GB across 7 containers of a 7.65 GiB Docker VM; `/System/Volumes/Data` 25 % used, 2.7 TB free |
| Probe harness dead | `sapphire-recap-probe.jsonl`: 2433 records / 135 cycles / 31 days, **0 `ok=True`**; 1082 (44 %) `[Errno 30] Read-only file system` |

**Not reproducible from the repo alone** — these require the host and a valid API key. The installed
host plist (`~/Library/LaunchAgents/…`) points at `/Users/sapphire/recap-probe/run.sh`, which is **not**
the checked-in wrapper; the repo's own plist and wrapper are correct. That divergence is the finding.
