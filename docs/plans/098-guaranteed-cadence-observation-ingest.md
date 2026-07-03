# Plan 098 — guaranteed-cadence observation ingest (decouple from forecast-cycle)

**Status**: READY (plan-review 2026-07-03 corrected the premise + resolved the
design against the installed Prefect source; grill-me approved the two forks —
see below). Implementation-precision residuals (register_all restructure so
`_build_specs()` precedes the client block; per-overlay ingest-worker bind
precision — do NOT copy the backup/CAMELS binds; Phase-3 test deps on the
`work_pool_name` field) are watch-outs for the implementer, not blockers.

**Grill-me decisions (2026-07-03):** (1) **Approach = dedicated ingest worker +
`ingest` pool** (isolation), not a concurrency knob (Option A is a no-op — the
worker is already unbounded). (2) **Phase 0 root-cause test DONE on the mini
(2026-07-03) — see "Phase 0 RESULT" below.** OOM/contention is **ruled out**
(OOMKilled=false, Restarts=0, memory far below limit); confirmed cause = **poll/
scheduling latency** (the shared worker's poll loop starves during the 6-hourly
forecast-cycle, delaying the `*/5` ingest pickup by ≈25–60 min → lost LINDAS
windows). Plan 062 absorption is **not urgent** (the recreate-hang did not cause
today's loss). Phases 1–3 are unblocked and unchanged.

**Priority**: high — LINDAS observations are only live for ~10 min, so any
delayed/blocked ingest **permanently loses discharge data**. Directly hit on the
Mac-mini 2026-07-03.
**Phase**: v0b — orchestration / operational reliability
**Parent**: the Mac-mini data-collection deployment (Plan 091); the operational
obs feed that the `nwp_regression` skill comparison depends on
**Related**: `docker-compose.yml` (`prefect-worker` service, `:67-119`; note
`command :70`, `mem_limit :78`, tmpfs `/tmp/sapphire_nwp :110-114`; `init`
service `:202-243`, `restart: "no"`),
`docker-compose.macmini.yml` (`:21-28`, patches `prefect-worker` by name),
`docker-compose.macmini-nwp.yml` (`:9-13`, patches `prefect-worker` by name),
`docker-compose.staging.yml` (`:7-11`, patches `prefect-worker` by name with
`SAPPHIRE_CONFIG_OVERLAY` + overlay TOML bind mount),
`docker-compose.dev.yml` (`:25-27`, adds a CAMELS-CH bind mount to
`prefect-worker` only),
`docker/entrypoint.sh` (`:13-15`, assembles `DATABASE_URL`),
`src/sapphire_flow/cli/register_deployments.py` (crons, `WORK_POOL`,
per-deployment `concurrency_limit`), `tests/unit/cli/test_register_deployments.py`
(hardcoded `== 10` spec/register counts, `create_work_pool.assert_awaited_once`
`:224`, `test_handles_existing_work_pool` `:228-255`),
`docs/standards/cicd.md` (`:129` upgrade `stop prefect-worker`; `:13-24`
service-topology table; `:59-66` v0 dependency chain; `:478` overlay-service
list), `docs/standards/orchestration.md` (`:5`, `:33`, `:59`, `:132` "single
`default` pool" statements), `docs/v0-scope.md` §A6 + service table (`:449-458`),
Plan 062 (PREFECT_HOME persistence / restart fragility)
**Created**: 2026-07-03

---

## Problem (observed live 2026-07-03)

`ingest-observations` runs on a `*/5` cron; `forecast-cycle` on `0 */6`;
`ingest-weather-history` on `0 6` daily. All are served by a **single
`ProcessWorker`** (`prefect worker start --pool default --type process`,
`docker-compose.yml:70`) on the one `default` pool. A `forecast-cycle` downloads
~2.8 GB of ICON GRIB and takes ~10 min; `ingest-weather-history` fetches a
rolling 60-day MeteoSwiss reanalysis (`ingest_weather_history.py:51` `_WINDOW_DAYS
= 60`, station-by-station HTTP + raster extraction) and can also run for several
minutes. On 2026-07-03 the obs ingest was observed stacking LATE while a heavy
flow was active, and LINDAS serves only the last ~10 min of discharge, so a
delayed ingest **misses that data permanently** (no backfill — BAFU LINDAS is
real-time only).

Compounding fragility (also hit today): after a `docker compose up -d` recreate,
the worker stopped claiming scheduled runs (schedule Active, worker up, nothing
RUNNING, ingests stacking LATE) until a manual `restart` — the Plan-062
PREFECT_HOME / restart-resilience gap. A single point of failure for the worker
also stops the obs feed.

### Root-cause is NOT a concurrency cap — confirmed from installed Prefect 3.6.x

The reviewers correctly flagged that the original draft's premise ("the single
worker serialises obs ingest *behind* a running forecast-cycle") is **wrong as
stated**. The installed Prefect source resolves the mechanism unambiguously:

- The worker is started **without `--limit`** (`docker-compose.yml:70`), so
  `BaseWorker._limiter` is `None`
  (`.venv/.../prefect/workers/base.py:1071-1073`: the `CapacityLimiter` is only
  built when `self._limit is not None`). The submission guard at
  `base.py:1351` is `if self._limiter:` — always False — so no `WouldBlock`, no
  queueing. **The worker is unbounded at the worker level.**
- `ProcessWorker.__aenter__` additionally constructs `Runner(..., limit=None)`
  (`.venv/.../prefect/workers/process.py:333`), so the runner imposes no cap
  either.
- The `default` pool is created with **no `concurrency_limit`**
  (`register_deployments.py:146-148` — `WorkPoolCreate(name=WORK_POOL,
  type="process")` only), so there is no pool-level cap.
- Per-deployment `concurrency_limit=1` (`register_deployments.py:54` for
  `forecast-cycle`, `:66` train-models, `:92` onboard-model) only blocks two runs
  of the **same** deployment; it does not block a *different* deployment from
  starting.

**Conclusion**: scheduled flow runs of different deployments can already start as
concurrent subprocesses immediately
(`start_soon(self._submit_run, flow_run)`). **Option A ("raise worker/pool
concurrency") is therefore a no-op** and is dropped below. The observed LATE
stacking must have a different cause, and the plan must **confirm which** before
committing to a fix. The two credible causes:

1. **Resource contention / OOM** — two heavy subprocesses (forecast-cycle ±
   weather-history) running concurrently under the single 8 GiB `mem_limit`
   (`docker-compose.yml:78`) push the cgroup over budget; a SIGKILL/OOM takes down
   the worker container (or a run), and the ingest tick that lands during the
   crash/restart window is lost.
2. **Plan-062 recreate/hang** — after `docker compose up -d`, the worker stops
   claiming runs until a manual `restart` (PREFECT_HOME not persisted / server
   loses scheduling state). This is a single point of failure for the obs feed
   regardless of concurrency.

Either way the fix is **isolation + resilience**, not a concurrency cap.

### Phase 0 RESULT — confirmed on the Mac mini (2026-07-03)

The Q1 controlled test was run on the mini. Evidence:

- **`docker inspect prefect-worker` → `OOMKilled=false`, `RestartCount=0`**, and
  the container's `StartedAt` predates all the LATE ingests — the worker has been
  up continuously and was **never OOM-killed or restarted**.
- **`docker stats` during a live forecast-cycle → memory far below the 8 GiB
  `mem_limit`** (no cgroup pressure).
- **`docker top prefect-worker | grep -c python` → 2** during the overlap — the
  worker genuinely runs the ingest as a **concurrent subprocess** (confirms F1: no
  worker-level cap; the run is not queued behind forecast-cycle).
- **`prefect flow-run ls --state RUNNING` → none** at rest — no run is stuck
  `RUNNING` holding a `concurrency_limit=1` slot (rules out a hung-run block).
- **Observed lateness ≈ 25–60 min** on `ingest-observations` ticks that land
  during a forecast-cycle window; the runs themselves complete **fast** once they
  start.

**Confirmed root cause = poll/scheduling latency, NOT resource exhaustion.**
Cause 1 (contention/OOM) is **ruled out** (OOMKilled=false, Restarts=0, memory
far below limit). Cause 2 (Plan-062 recreate-hang) is a *distinct, separate*
symptom (total stall until manual `restart`, seen on `up -d` recreate) — not what
produces the recurring, bounded 25–60 min lateness here. The actual mechanism is
that the **single shared process worker's poll/submission loop is starved while it
runs the 6-hourly forecast-cycle** (21 members × 2 stations pegs CPU), stretching
the `*/5` ingest pickup by tens of minutes. Because LINDAS obs live only ~10 min,
that pickup delay = **permanent data loss**.

This **strengthens** the plan's direction: the fix is **cadence isolation** (a
dedicated `ingest` worker + pool so the obs poll loop is independent of the
forecast-cycle's CPU load), justified by poll-starvation rather than memory. D3's
small `mem_limit` for the ingest worker is still fine — memory was never the
constraint, and a lightweight always-polling worker is exactly what isolates the
5-min cadence. Phases 1–3 proceed unchanged; Plan 062 absorption is **not urgent**
(the recreate-hang did not cause today's loss, though it remains a tracked
residual risk — D6/Q4).

## Goal

The observation ingest runs on its **guaranteed 5-min cadence regardless of
forecast-cycle / weather-history (or any other flow) activity, resource
contention, or a heavy-flow hang** — no missed LINDAS windows. Resource-safe
(must not enable an OOM by over-committing the single 8 GiB worker).

## Confirmed facts (settle before design — were "open questions")

These were open questions in the draft; the reviewers established them from the
installed source, so they are now recorded as **facts**, not questions:

- **F1 — worker is unbounded.** `prefect worker start` with no `--limit` runs
  flows with unbounded concurrency (`base.py:1071-1073`, `process.py:333`). The
  question was never "raise the limit"; it is "isolate obs ingest from resource
  contention / a hung heavy worker". Confirmed.
- **F2 — pool has no cap.** `WorkPoolCreate` at `register_deployments.py:146-148`
  passes no `concurrency_limit`. Confirmed.
- **F3 — per-deployment `concurrency_limit=1` is single-deployment only.** It
  does not serialise ingest behind forecast-cycle. Confirmed.
- **F4 — `ingest-weather-history` is a second heavy blocking peer.** Daily 06:00,
  no `concurrency_limit` (`register_deployments.py:94-99`), 60-day rolling fetch
  (`ingest_weather_history.py:51`). It shares the one worker with obs ingest and
  can contend for the same 8 GiB budget as forecast-cycle. Confirmed. It must be
  covered by the design, not just forecast-cycle.

## Open design questions (grill-me before READY)

1. **Which root cause is real? (documents urgency; does NOT gate Phases 1–2.)**
   The isolation fix (a second `ingest` pool + dedicated worker) is correct for
   **both** candidate causes, so confirming the cause does not change *whether* to
   build Phases 1–3 — only *which cause to document* and *how urgently Plan 062
   must be absorbed* (D6). Run a controlled test on the Mac Mini:
   - Trigger a `forecast-cycle` manually (`prefect deployment run
     forecast-cycle/forecast-cycle`) and, while it runs, confirm whether a
     scheduled `ingest-observations` run **starts a concurrent subprocess**
     (`docker stats` / `docker top prefect-worker` shows two `sapphire`/python
     run processes) or queues. Given F1–F3 it should run concurrently.
   - If they run concurrently, watch for OOM: `docker events` /
     `docker inspect prefect-worker` OOMKilled state, `dmesg`, and worker log for
     SIGKILL around the overlap. This distinguishes **cause 1 (contention/OOM)**
     from **cause 2 (Plan-062 hang)** — the hang reproduces on
     `docker compose up -d` recreate, not on overlap.
   - Record the confirmed cause in this plan. The chosen option must target the
     confirmed cause; do not ship Option B *and* claim it fixed an OOM if the OOM
     was never reproduced.
2. **Isolation mechanism (Option B) — required scoping.** A second worker on the
   **same `default` pool would NOT be dedicated**: a ProcessWorker polls all
   pending runs from its pool's queues, so it would freely pick up
   forecast-cycle/train-models/etc. True isolation requires a **separate work
   pool**. Decision to confirm: use a second pool `ingest` and start the
   dedicated container with `--pool ingest`. (Work-queue filtering within
   `default` via `--work-queue` is the alternative, but Prefect 3 `adeploy` here
   takes `work_pool_name` cleanly and no `work_queue_name`, so a second pool is
   the smaller, clearer change — see Design decision D1.)
3. **Placement of `ingest-weather-history` (F4).** It is heavy and must **stay on
   the general/heavy worker** (the `default` pool), NOT on the dedicated ingest
   worker — otherwise it re-introduces the blocking peer onto the obs feed. Add
   `concurrency_limit=1` to it so two weather-history runs cannot race and double
   the memory footprint. Confirm this split.
4. **Interaction with Plan 062 (PREFECT_HOME / restart).** A dedicated ingest
   worker survives a forecast-worker recreate/hang, but if the root cause is the
   Plan-062 server-side state loss, a dedicated worker alone does **not** fix the
   "stops claiming runs after recreate" symptom. Decide whether 098 also pins
   PREFECT_HOME / adds a worker healthcheck + auto-restart, or defers that to 062
   and scopes 098 to isolation only. (Recommendation: 098 = isolation; reference
   062 for the recreate/hang; note the residual risk here rather than silently
   depending on 062.)
5. **Missed-window semantics / monitoring.** Confirmed no backfill (LINDAS
   real-time only) — the fix is prevention. Consider a monitoring alert when an
   ingest is LATE by > one interval (ties into Flow 4 pipeline monitoring).

## Design decisions (proposed; confirm in grill-me)

- **D0 — drop Option A.** Raising worker/pool concurrency is a no-op (F1/F2); the
  worker is already unbounded. Not pursued.
- **D1 — Option B via a second work pool.** Add `INGEST_POOL = "ingest"`. Add a
  `work_pool_name: str = WORK_POOL` field to `DeploymentSpec`; set it to
  `INGEST_POOL` only for `ingest-observations`; leave all other deployments
  (including forecast-cycle **and** ingest-weather-history) on `WORK_POOL`. In
  `register_all`, create both pools (a second `WorkPoolCreate(name=INGEST_POOL,
  type="process")`, same `ObjectAlreadyExists` guard). `_register_one` passes
  `spec.work_pool_name` to `adeploy` instead of the module-level `WORK_POOL`.
- **D2 — dedicated ingest-worker container.** Add a `prefect-worker-ingest`
  service in `docker-compose.yml` started with
  `prefect worker start --pool ingest --type process`. It must replicate the
  worker's infrastructure **except** the heavy bits (see D3). The base
  `prefect-worker` keeps serving `default` (forecast-cycle, weather-history,
  train, hindcast, backup, onboarding).
- **D3 — ingest-worker resource profile.** The ingest worker does **not** need:
  the `/tmp/sapphire_nwp` 4 GiB tmpfs (`docker-compose.yml:110-114`), the 8 GiB
  `mem_limit`, or the `model_artifacts` / `backups` / `nwp_grids` volumes. It
  **does** need: `read_only: true`, `tmpfs: [/tmp, /data/cache]`, the same
  `cap_drop: [ALL]` + `cap_add: [SETUID, SETGID, CHOWN, FOWNER]`, the
  `db_password` secret, the same `entrypoint`/`command`-side environment
  (`SAPPHIRE_DATA_DIR`, `SAPPHIRE_CONFIG`, `DATABASE_URL_TEMPLATE`,
  `PREFECT_API_URL`, `PREFECT_LOGGING_LEVEL`), the `./config.toml` bind mount, the
  same `depends_on` (prefect-server healthy, init completed), `restart:
  unless-stopped`, and `networks: [backend]`. Set a **small `mem_limit`** (e.g.
  512m–1g — obs ingest is tiny) so the two workers together stay under the
  ~15.84 GiB VM. Grill-me: pin the exact value. It also does **not** need the
  `docker-compose.dev.yml:25-27` CAMELS-CH `/data/raw` bind mount that
  `prefect-worker` carries: that mount exists for onboard-stations reference data,
  and obs ingest resolves stations from the DB (`ingest_observations.py`), so the
  ingest worker intentionally omits it (see D4 — do not add "for symmetry").
- **D4 — overlays must patch the new service name.** THREE overlay files patch
  `prefect-worker` **by name** and each must receive a matching
  `prefect-worker-ingest` block:
  - `docker-compose.macmini.yml` (`:21-28`)
  - `docker-compose.macmini-nwp.yml` (`:9-13`)
  - `docker-compose.staging.yml` (`:7-11` — sets `SAPPHIRE_CONFIG_OVERLAY:
    /app/config/overlays/staging-5-stations.toml` + the overlay TOML bind mount)

  A new `prefect-worker-ingest` in `docker-compose.yml` is silently **not**
  patched by any of these (Compose merges by service name), so without a matching
  block the ingest worker starts **without** `SAPPHIRE_CONFIG_OVERLAY` and the
  overlay TOML bind mount → runtime `ConfigurationError` / `FileNotFoundError` for
  station resolution (`cicd.md:474` documents the missing-overlay-file crash) on
  every `docker compose -f docker-compose.yml -f docker-compose.<overlay>.yml up`.
  This is the same failure class D4 already documents for the macmini overlays —
  it applies identically to staging.

  **Copy EXACTLY these — and nothing else — into each `prefect-worker-ingest`
  overlay block (do NOT "mirror the whole block"):** the existing
  `prefect-worker` block in `docker-compose.macmini.yml:22-28` also carries a
  backup-disk bind (`/Volumes/sapphire-backup/pg_dumps:/data/backups:rw`, `:26`)
  and a CAMELS-CH bind (`/Users/sapphire/camels-ch:/data/raw:ro`, `:27`).
  Reproducing those onto the ingest worker would give it **write access to the
  backup disk** and an unneeded read mount, contradicting D3's intentional
  omissions. The ingest block gets ONLY:
  - `docker-compose.macmini.yml`: `SAPPHIRE_CONFIG_OVERLAY:
    /app/config/overlays/mac-mini.toml` + the `./config/overlays/mac-mini.toml:
    /app/config/overlays/mac-mini.toml:ro` bind (`:24`, `:28`). **NOT** the
    `/data/backups:rw` bind (`:26`), **NOT** the `/data/raw:ro` CAMELS-CH bind
    (`:27`).
  - `docker-compose.macmini-nwp.yml` (`:9-13`): only the `SAPPHIRE_CONFIG_OVERLAY`
    env var + its overlay TOML `:ro` bind — nothing else that block carries.
  - `docker-compose.staging.yml` (`:7-11`): only `SAPPHIRE_CONFIG_OVERLAY:
    /app/config/overlays/staging-5-stations.toml` + the
    `staging-5-stations.toml:ro` bind — nothing else.

  **CAMELS-CH mount — intentionally omitted.** `docker-compose.dev.yml:25-27`
  adds a `${CAMELS_CH_HOST_DIR}:/data/raw:ro` bind to `prefect-worker` **only**
  (onboard-stations needs CAMELS-CH reference data). `prefect-worker-ingest`
  intentionally omits it — obs ingest resolves stations from the DB
  (`ingest_observations.py`), not from `/data/raw`. Do **not** add the CAMELS-CH
  mount to the ingest worker "for symmetry": it is unneeded and would enlarge the
  ingest worker's attack surface. Dev/staging asymmetry here is by design, not an
  oversight. The dev overlay does **not** need a `prefect-worker-ingest` block at
  all (it only re-exposes ports + the CAMELS-CH mount, neither of which the ingest
  worker needs); confirm no incompatible `/data/raw` mount is silently inherited
  (it is not — Compose does not apply a service's overrides to a differently-named
  service).
- **D5 — `concurrency_limit=1` on `ingest-weather-history`.** Prevents two
  60-day-fetch runs racing on the heavy worker. Add to
  `register_deployments.py:94-99`.
- **D6 — Plan 062 boundary.** 098 delivers isolation; it references Plan 062 for
  the recreate/hang and records the residual risk (a dedicated ingest worker
  survives a *forecast*-worker recreate, but a *server*-side scheduling-state loss
  affects both pools). PREFECT_HOME persistence stays in Plan 062 unless the
  grill-me decides to absorb it.
- **D7 — `init` one-shot dependency (no new lifecycle guarantee).**
  `prefect-worker-ingest` uses the **same** `depends_on` pattern as the existing
  `prefect-worker`: `prefect-server` (healthy) + `init`
  (`service_completed_successfully`). Two consequences to state plainly so the
  plan does not over-claim:
  - `init` is `restart: "no"` (one-shot, `docker-compose.yml:202-243`). On a
    fresh `docker compose up` it runs `register_deployments.register_all`, which
    (post-D1) creates **both** the `default` and `ingest` pools before the worker
    waits on it. On a `docker compose up -d` **recreate of only the worker**,
    `init` does **not** re-run (it already exited
    `service_completed_successfully`), and Compose treats the completed dependency
    as satisfied — so the recreated `prefect-worker-ingest` starts normally. It
    does **not** hang waiting for a re-run of `init`. (This matches the existing
    `prefect-worker` behaviour; 098 introduces no new lifecycle hazard here.)
  - Because both workers share the `init`-completed dependency, on a **fresh host
    where `init` was never run**, neither worker starts until `init` runs. The
    ingest worker inherits exactly the same precondition as the forecast worker —
    it is not a new failure mode.
  - **098 does NOT claim to fix the Plan-062 recreate hang.** A dedicated ingest
    worker isolates the obs feed from a *forecast*-worker crash/recreate, but the
    Plan-062 symptom (worker stops claiming runs after `up -d` until a manual
    `restart`) is server/PREFECT_HOME-side and would affect the ingest worker too.
    That residual risk stays tracked in D6/Q4 — do not read the new container as a
    Plan-062 fix.
- **D8 — affected-docs updates (CLAUDE.md "every code change updates docs").**
  Adding `prefect-worker-ingest` and the second `ingest` pool changes operator
  procedure and the documented v0 topology. This plan must update:
  - `docs/standards/cicd.md:129` upgrade step: `docker compose stop
    prefect-worker` → `docker compose stop prefect-worker prefect-worker-ingest`
    (both workers must be quiesced before `init`/`alembic upgrade head` re-run;
    leaving the ingest worker running during an upgrade breaks the sequence).
  - `docs/standards/cicd.md:13-24` service-topology table: add a
    `prefect-worker-ingest` row (custom image; depends on `prefect-server
    (healthy)`, `init (completed)`; `restart: unless-stopped`; scope **v0b**).
    **Also correct the pre-existing drift in the neighbouring `prefect-worker`
    row (`:18`)**: it currently lists `Depends on = prefect-server, postgres`, but
    the actual v0 base compose (`docker-compose.yml:85-89`) makes the worker depend
    on `prefect-server (healthy)` + `init (service_completed_successfully)`, **not
    postgres directly**. Fix the `prefect-worker` row's "Depends on" to
    `prefect-server (healthy), init (completed)` so the new ingest row is not
    templated off a wrong value. (The `prefect-worker-*` v1 rows describe a
    different v1 topology and are out of scope.)
  - `docs/standards/cicd.md:59-66` v0 dependency-chain diagram: it currently shows
    only `prefect-server ──→ prefect-worker`. Add `prefect-worker-ingest` as a
    second arrow off `prefect-server` (both workers are init-gated one-shot
    dependents, per D7).
  - `docs/standards/cicd.md:478` overlay-pattern prose: it lists the services that
    receive `SAPPHIRE_CONFIG_OVERLAY` as `(prefect-worker, api, init)`. Add
    `prefect-worker-ingest` to that parenthetical — it is precisely the service
    that MUST receive the overlay env var + TOML bind or it crashes with
    `FileNotFoundError` at startup (the failure mode already noted at `cicd.md:474`
    and in D4). New list: `(prefect-worker, prefect-worker-ingest, api, init)`.
  - `docs/standards/orchestration.md` — four stale "v0 uses a single `default`
    pool" statements become incorrect after 098. Annotate each with a note that
    Plan 098 introduces a second `ingest` pool + dedicated worker for obs-feed
    isolation, so v0 now runs **two** pools (`default` + `ingest`), not one:
    - `:5` ("v0 uses a single `default` pool — the three-pool topology … applies to
      v1").
    - `:33` (the plan-013 DECISION note "At ~1000 stations on a single `default`
      work pool (v0), staggering …"). The obs-feed isolation now supersedes the
      "stagger cron schedules to reduce contention" workaround for the
      obs-vs-heavy-flow case specifically; annotate that staggering is no longer the
      obs-feed protection mechanism (a dedicated pool is) — retain it as general
      guidance for the remaining `default`-pool flows.
    - `:59` ("v0 uses a single work pool with in-process execution"). Note the obs
      pool is a **separate worker process**, not in-process; `default` retains
      in-process `task.map` fan-out.
    - `:132` ("v0 uses a single pool — sub-flows run in-process"). Note the `ingest`
      pool serves only `ingest-observations` (no sub-flow composition), so the
      in-process sub-flow statement still holds for `default`; clarify the pool
      count is now two.
  - `docs/v0-scope.md` §A6: it documents "single `default` pool" as an explicit v0
    simplification. Add a clarifying note that Plan 098 introduces a second
    `ingest` pool + dedicated worker purely for obs-feed isolation (not a change
    to the general-worker model).
  - `docs/v0-scope.md:449-458` service-topology table: the `prefect-worker` row
    says "**Single worker** (no pool separation)" and the "Not in v0" note (`:454`)
    excludes "separate training/hindcast workers". Add a `prefect-worker-ingest`
    row (custom image; scope **v0b**; obs-feed isolation) and adjust the "Not in v0"
    note to clarify that a separate worker exists in v0 **for obs-feed isolation**,
    not for training/hindcast pool separation (which stays v1).

## Non-goals

- Backfilling missed LINDAS obs (not possible — real-time only).
- The full PREFECT_HOME persistence fix (Plan 062) — reference, don't absorb
  (unless the grill-me decides otherwise per Q4/D6).
- Reducing the forecast-cycle's 2.8 GB download (Plan 090 P2 / a cache).
- Moving `ingest-weather-history` off the heavy worker — by design it stays on
  `default` (D3/F4); it is daily and its STAC window is forgiving.

## Resource interaction matrix (Q5 → confirmed)

| Flow | Pool (after 098) | Cadence | Weight | Blocks obs ingest? |
|---|---|---|---|---|
| `ingest-observations` | `ingest` (dedicated) | `*/5` | tiny | n/a — protected |
| `forecast-cycle` | `default` | `0 */6` | ~2.8 GB / 10 min | No (separate pool + worker) |
| `ingest-weather-history` | `default` | `0 6` daily | 60-day rolling HTTP+raster | No (separate pool + worker); `concurrency_limit=1` prevents self-race (D5) |
| train / hindcast / skills / onboarding / backup | `default` | various | mixed | No (separate pool + worker) |

The only remaining shared-fate risk is **server-side** (Plan 062): if the Prefect
server loses scheduling state on recreate, *both* pools stall. That is out of
scope for 098's isolation change and tracked in D6/Q4.

## Implementation phases (draft — finalise after Q1 confirms root cause)

**Phases 1 and 2 are NOT independently deployable — they ship together.**
Phase 1 (routing `ingest-observations` to the `ingest` pool via `init`/
`register_all`) and Phase 2 (the `prefect-worker-ingest` container that serves
that pool) MUST land in a **single image build + compose update**. A partial
deploy — Phase 1 committed and `init` re-run, re-registering `ingest-observations`
onto the `ingest` pool, while only the original `prefect-worker` is running —
leaves the `ingest` pool **workerless**: the flow sits PENDING with no worker to
claim it and the obs feed goes **completely dead** (worse than the original
LATE-stacking). Correct rollout sequence (also mirrored in the revised
`cicd.md:129` upgrade step, D8): `build image` → `docker compose stop
prefect-worker prefect-worker-ingest` → `docker compose run --rm init` (creates
both pools, reroutes deployments) → `docker compose up -d` (brings **both**
workers up). If the work is split across commits, the **compose change (Phase 2)
must land first** so the `ingest` pool always has a worker even before `init`
reroutes deployments to it. Do NOT merge/deploy Phase 1 alone.

**Phase 0 — reproduce & confirm (documentation gate only, NOT a Phase 1–2 gate).**
Execute Q1 on the Mac Mini; record the confirmed cause (contention/OOM vs
Plan-062 hang) in this plan. Phase 0 determines **which root cause to document**
and **whether Plan 062 absorption is urgent** (D6) — it does **NOT** determine
whether to proceed with Phases 1–3. Phases 1–3 (the second `ingest` pool +
dedicated worker) are the correct isolation fix **regardless** of which root
cause is real: both OOM-contention and the Plan-062 recreate-hang are resolved,
or their blast radius contained, by isolating the obs feed onto its own pool +
worker. Phases 1–3 are therefore **unconditional**. Phase 4 validation itself
exercises both isolation (a concurrent heavy flow must not delay a tick) and
recreate resilience (bring the worker up under each overlay), and will surface
whether the Plan-062 hang is fixed or still present. Do not stall the
architectural fix waiting on an observation-ambiguity that may take days to
reproduce.

**Phase 1 — deployment routing (register_deployments.py).**
- Add `INGEST_POOL = "ingest"` and `work_pool_name: str = WORK_POOL` on
  `DeploymentSpec` (`:24-30`).
- Set `work_pool_name=INGEST_POOL` on the `ingest-observations` spec (`:43-48`).
- Add `concurrency_limit=1` on the `ingest-weather-history` spec (`:94-99`, D5).
- **Restructure `register_all` — required reordering (do not skip).** Today
  `register_all` (`:138-157`) creates the `default` pool **inside** the
  `async with get_client()` block (`:144-151`) and only then calls `specs =
  _build_specs()` **after** that block closes (`:153`). Phase 1 needs the pool set
  derived from `specs`, so `specs` must exist **before** pool creation. Collapse
  the current two-phase structure into one:
  1. Call `specs = _build_specs()` **first**, before opening the client context.
  2. Open **one** `async with get_client() as client:` block that (a) loops over
     `{spec.work_pool_name for spec in specs}` (an ordered dedup is fine), creating
     each pool with its **own** per-iteration `try/except ObjectAlreadyExists` so
     one pre-existing pool does not abort creation of the other, then (b) calls
     `await _register_one(spec)` for each spec **inside the same block**.

  The current split (pool creation inside the client context at `:144-151`, spec
  registration outside at `:153-155`) must be **collapsed into one** `async with`
  context. Following the old ordering literally — leaving `_build_specs()` after
  the client block while referencing `specs` inside it — produces an
  `UnboundLocalError`/`NameError` (specs not yet in scope). The per-iteration guard
  is what the partial-existing test in Phase 3 exercises. Deriving the pool set
  from the specs keeps it correct if more pools appear later.
- `_register_one` reads `spec.work_pool_name` and passes it to `adeploy` (`:118`)
  instead of the module-level `WORK_POOL` constant.
- **`init` service needs no change.** It invokes `register_all`, which now creates
  both pools; there is no new env var (no `SCHEDULE_INGEST_POOL` or similar) and no
  change to the `init` service definition. Pool identity is derived from the specs,
  not injected. See D7 for the `init` one-shot lifecycle and why the new worker's
  `depends_on: init` does not introduce a recreate hang.

**Phase 2 — docker-compose worker service (D2/D3/D4) + affected docs (D8).**
- Add `prefect-worker-ingest` to `docker-compose.yml` per D3 (small `mem_limit`,
  no NWP tmpfs, no heavy volumes; same `depends_on: prefect-server healthy + init
  completed` as `prefect-worker`, per D7).
- Add matching `prefect-worker-ingest` patch blocks to **all three** overlays
  that patch `prefect-worker` by name (D4):
  - `docker-compose.macmini.yml`
  - `docker-compose.macmini-nwp.yml`
  - `docker-compose.staging.yml` (`SAPPHIRE_CONFIG_OVERLAY` +
    `staging-5-stations.toml` `:ro` bind mount, mirroring `:7-11`)
- Do **not** add a block to `docker-compose.dev.yml` (D4: the ingest worker needs
  neither the re-exposed ports nor the CAMELS-CH mount).
- Update `docs/standards/cicd.md` per D8: upgrade step `:129` (`stop
  prefect-worker prefect-worker-ingest`), the service-topology table (`:13-24` —
  add the ingest row **and** fix the stale `prefect-worker` `postgres` dependency),
  the v0 dependency-chain diagram (`:59-66`), and the overlay-pattern prose
  (`:478`).
- Update `docs/standards/orchestration.md` per D8: annotate the four stale
  "single `default` pool" statements (`:5`, `:33`, `:59`, `:132`) — v0 now runs
  two pools (`default` + `ingest`).
- Update `docs/v0-scope.md` per D8: §A6 clarifying note **and** the service-topology
  table (`:449-458` — add the `prefect-worker-ingest` row + adjust the "Not in v0"
  note).

**Phase 3 — tests (register_deployments).**

> **Phase 3 depends on Phase 1 landing first — not standalone.** The spec-routing
> and `_register_one`-forwarding tests below reference `DeploymentSpec.
> work_pool_name`, a field that **does not exist yet** on the current dataclass
> (`register_deployments.py:24-30`) — it is added in Phase 1. Likewise the
> `_register_one` forwarding test asserts on the Phase-1 change at `:118`. Running
> Phase 3 against pre-Phase-1 code yields `AttributeError`, not a meaningful
> assertion failure. Phase 1 and Phase 3 are part of the **same atomic change**;
> author them together.

*One existing test is genuinely BROKEN by Phase 1 and MUST be fixed in this phase
— a new test does not substitute for repairing the broken assertion:*

- **`test_creates_work_pool_and_registers_all` (`:202-225`) — BROKEN, must fix.**
  Phase 1 makes `register_all` call `create_work_pool` **twice** (once per distinct
  pool: `default`, `ingest`), so the existing `mock_client.create_work_pool.
  assert_awaited_once()` (`:224`) fails with "awaited 2 times". Replace it with
  `assert mock_client.create_work_pool.await_count == 2` and assert **both** pool
  names appear across the two calls — inspect
  `mock_client.create_work_pool.await_args_list` and assert the set of
  `WorkPoolCreate.name` values equals `{"default", "ingest"}` (order-independent).
  Also replace the `== 10` register count (`:225`) with `len(_build_specs())`.

*One existing test still PASSES after Phase 1 but is incomplete — improve it (do
not skip as optional cleanup):*

- **`test_handles_existing_work_pool` (`:228-255`) — still passes, improve.** It
  is **not** broken by Phase 1: it uses a **scalar**
  `side_effect=ObjectAlreadyExists("pool exists")`, so after Phase 1 both
  per-iteration pool-creation calls raise and both are caught by the per-iteration
  guard — all specs still register and `mock_register.await_count == 10` still
  holds. It merely exercises only the "both pools already exist" path. Change it to
  `side_effect=[ObjectAlreadyExists("pool exists"), None]` so it tests the
  realistic **partial** case (`default` pre-exists, `ingest` is new) and confirms
  the per-iteration guard catches only the raising call while the other succeeds.
  Replace the `== 10` register count (`:255`) with `len(_build_specs())`. Add a
  companion `test_handles_all_work_pools_existing` with
  `side_effect=[ObjectAlreadyExists(...), ObjectAlreadyExists(...)]` for the
  both-existing path.
- **`test_returns_ten_specs` (`:69-72`).** Replace `== 10` with
  `len(_build_specs())` (self-correcting; the spec count is unchanged by 098 but
  the magic number should stop being asserted against a literal).

*New / strengthened tests for the D1 routing + field forwarding:*

- **`_register_one` forwards `spec.work_pool_name` (not the module constant).**
  Add `test_register_one_uses_spec_work_pool`: construct a `DeploymentSpec` with
  `work_pool_name="ingest"`, call `_register_one`, and assert
  `call_kwargs["work_pool_name"] == "ingest"`. This is the assertion the existing
  `test_registers_scheduled_flow` (`:120-153`) **cannot** catch: it asserts
  `== WORK_POOL` (the module constant), which still passes even if `_register_one`
  keeps the hardcoded `WORK_POOL` at `:118` instead of reading
  `spec.work_pool_name`. Strengthening the existing test alone is insufficient —
  a distinct non-default pool is required to catch a copy-paste regression.
- **Spec-level routing.** Add a test asserting each `DeploymentSpec.work_pool_name`
  routes correctly: `ingest-observations` → `"ingest"`, all others (forecast-cycle,
  ingest-weather-history, train, hindcast, skills, onboarding, backup) → `"default"`.
- **`register_all` creates both pools.** Covered by the
  `test_creates_work_pool_and_registers_all` update above (two calls, both names).
- **`test_concurrency_limits` (`:43-53`).** Add
  `assert by_name["ingest-weather-history"].concurrency_limit == 1` (D5) to the
  explicit assertion block (do not add it in isolation elsewhere). The existing
  `compute-skills` / `compute-combined-skills` deployments remain untested for
  concurrency and stay `None`; no change needed there.

**Phase 4 — validation.** Deploy on Mac Mini; confirm (a) `ingest-observations`
runs on the `ingest` pool via a dedicated worker (`prefect deployment inspect`),
(b) an in-flight `forecast-cycle` + a concurrent `ingest-weather-history` do not
delay an ingest tick, (c) each overlay applies the correct config to the ingest
worker (no `ConfigurationError` / `FileNotFoundError`) — explicitly bring the
ingest worker up under **each** of the three overlays (macmini, macmini-nwp,
staging) and confirm `SAPPHIRE_CONFIG_OVERLAY` resolves and the TOML bind mount
is present, and (d) an upgrade following the revised `cicd.md:129` step
(`stop prefect-worker prefect-worker-ingest`) quiesces both workers before
`init`/migrations re-run.

## Process

DRAFT until a grill-me (i) records the Q1-confirmed root cause, (ii) confirms the
D1 second-pool scoping mechanism, (iii) confirms the `ingest-weather-history`
placement + `concurrency_limit=1` (D5/F4), and (iv) confirms the ingest-worker
resource profile + overlay patches (D3/D4). Then the phases above → READY. The
change is small but touches several files: `register_deployments.py`,
`docker-compose.yml`, **three** overlays (`docker-compose.macmini.yml`,
`docker-compose.macmini-nwp.yml`, `docker-compose.staging.yml`), the
register-deployments tests, and the affected docs (`docs/standards/cicd.md`
upgrade step + topology table + dependency chain + overlay-service list,
`docs/standards/orchestration.md` single-pool statements, `docs/v0-scope.md` §A6 +
service table) per the CLAUDE.md "every code change updates docs" rule (D8).
