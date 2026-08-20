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

**DRAFT — needs `/plan` before READY.** Grounded in a live gateway re-probe (2026-08-20, this session)
and a code re-read of the Flow-1 dispatch. This plan owns the **deployment-isolation** half of
[Plan 139](139-nepal-12300-swe-regression-enablement.md) W5+W8; it does **not** re-open 139's model,
training or target workstreams.

## Why a second stack (the structural constraint)

`[adapters.weather_forecast].type` is a **single deployment-wide selector**
(`run_forecast_cycle.py:256`, `_WEATHER_FORECAST_TYPES = ("meteoswiss_nwp", "recap_gateway")`; default
at `:257`; dispatch branch at `:390`). There is exactly one weather-forecast adapter per stack.
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
- **D2 — Host ports.** The base file binds caddy `80:80`/`443:443` (`docker-compose.yml:319-321`) and the
  macmini overlay binds the API `8000:8000` (`docker-compose.macmini.yml:48-49`). The new
  `docker-compose.nepal.yml` overrides these to **API `8001`**, caddy **`8081`/`8444`**. It must NOT
  include `docker-compose.macmini.yml` (that overlay carries the Swiss BAFU collectors, the NWP-require
  flag and the backup binds).
- **D3 — Its own config overlay**, `config/overlays/mac-mini-nepal.toml`, setting
  `[adapters.weather_forecast] type = "recap_gateway"`, an `[adapters.recap_gateway]` section, and a
  `[deployment]` identity block. The Swiss `mac-mini.toml` is not touched.
- **D4 — 00Z-only cycle resolution — needs a small code change; the config field alone does NOT do it.**
  `RecapGatewayConfig.cycle_cadence_hours` exists (`config/recap_gateway.py`) but is threaded **only**
  into the dormant `ForcingResolutionPolicy` carrier (`run_forecast_cycle.py:542,555`, Plan 151 T8a).
  The live path — `_resolve_effective_cycle` (`recap_gateway.py:806`) → `resolve_latest_cycle`
  (`:434`) — is called **without** `cadence_hours`, so `_IFS_CADENCE_HOURS = 6.0` applies. Combined
  with a resolver that returns the first cycle yielding *any* data regardless of horizon, a run at
  08:00 UTC resolves the **06Z 2-day** run instead of the **00Z 15-day** one. Options:
  - **(a) RECOMMENDED — thread `cycle_cadence_hours` into the adapter** (constructor arg → the
    `resolve_latest_cycle` call) and set `cycle_cadence_hours = 24.0`. Walk-back then floors to 00Z and
    steps in whole days. Contained to the recap path; the Swiss adapter is untouched. Implementation
    note: `floor_to_ifs_cadence` (`:422`) is used to decide whether a resolved cycle counts as a
    *fallback* for provenance (`run_forecast_cycle.py:1482`) — it must floor the same way, or every
    00Z resolution is mislabelled a fallback.
  - (b) Accept 6 h walk-back and schedule so the nominal cycle floors to 00Z, with
    `max_cycle_age_hours < 6` so it cannot walk back at all. Simpler, but one missed run = no forcing.
  - (c) Wait for Plan 151's `services/track_resolution.py` resolver to go live. Correct long-term
    owner, wrong timeline for this test.
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
- **D8 — No backups, no watchdog coverage for this stack, deliberately.** The host watchdog probes
  `localhost:8000` (the Swiss API) and the backup service binds the Swiss volumes. The test stack gets
  neither. Accepted for a throwaway basin; stated here so it is not mistaken for an oversight. Revisit
  if this stack ever outlives the test.

## Phases and tasks

### Phase 1 — the stack

- **T1 — `docker-compose.nepal.yml`** — port overrides per D2, `SAPPHIRE_CONFIG_OVERLAY` pointing at the
  new overlay, no macmini overlay, no backup service.
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

### Phase 2 — make 12300 exist

- **T6 — basin geometry** for `g_123` via the Plan 120 importer (`archive/120-basin-static-importer.md`,
  COMPLETE).
- **T7 — station + bindings** — a `stations` row for 12300, a `station_weather_sources` row that the
  adapter's `_prefilter` (`recap_gateway.py:585`) will accept — `nwp_source = "ifs_ecmwf"`
  (`:793`), `role = FORECAST`, `status = ACTIVE`, `extraction_type = BASIN_AVERAGE` — and a
  `recap_gateway_polygon_bindings` row mapping the station to `g_123`.
  **Open question O1:** do this via Plan 139 W3's gateway-fed onboarding path (does not exist yet), or
  by direct seeded rows for a single test basin? The latter is far cheaper and reversible; the former
  is the reusable thing. Owner call.

**Exit:** the adapter resolves 12300 → `g_123` without a resolver miss.

### Phase 3 — prove operational forcing

- **T8 — cadence threading (code, hold-at-PR)** — D4(a), plus the `floor_to_ifs_cadence` provenance
  detail. Red-first test: a resolver offered a 06Z short run and an older 00Z run must pick 00Z.
- **T9 — schedule** the daily forecast cycle on the Nepal stack. Timing: today's 00Z was already served
  at 09:27 UTC; the actual publication time is unmeasured — **O2**, and the cheapest way to measure it
  is to fix the probe install first (see Related work).
- **T10 — verification** — after a scheduled run: forcing rows stored for 12300, provenance cycle is a
  00Z cycle, horizon ≥ 14 d, units canonical (mm, °C — the adapter converts m→mm, K→°C), and the
  health record is HEALTHY rather than DEGRADED.

**Exit:** a scheduled daily run stores a 15-day 00Z IFS forcing series for 12300, unattended.

### Phase 4 — a forecast (gated, likely its own plan)

Target ingest (D7), training-path wiring (139 W4), the model (139 W6), model onboarding. **Blocked on
D6** — settle what a model may declare given the 7-day past-forcing hole before writing one.

## Dependency graph

```json
{
  "phases": [
    { "id": "stack",   "tasks": ["T1","T2","T3","T4","T5"], "depends_on": [] },
    { "id": "records", "tasks": ["T6","T7"],                "depends_on": ["stack"] },
    { "id": "forcing", "tasks": ["T8","T9","T10"],          "depends_on": ["records"] },
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

## Open questions for the owner

- **O1** — seeded rows vs building Plan 139 W3's onboarding path (Phase 2).
- **O2** — what daily schedule? Needs the measured 00Z publication time.
- **O3** — how long does this stack live? That answer decides D8 (backups/monitoring) and O1.
- **O4** — D4 option (a), (b) or (c)?

## Related work (not in scope, but adjacent)

**Fix the mac-mini probe install** — copy `scripts/launchd/ch.hydrosolutions.sapphire-recap-probe.plist`
and reload the agent so it runs the repo wrapper instead of the July snapshot. Ten minutes, and it turns
a 31-day dead experiment into the longitudinal record that would answer O2 and validate the retention
and lag assumptions this whole plan rests on. Recommended **before** Phase 3.
