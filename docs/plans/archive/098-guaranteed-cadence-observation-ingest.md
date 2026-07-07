# Plan 098 — guaranteed-cadence observation ingest (decouple from forecast-cycle)

**Status**: READY (plan-review 2026-07-03, 3 rounds — corrected the premise +
resolved the design against the installed Prefect source; reconciled the Phase-0
evidence with the root cause, corrected the `register_all` restructure to the
minimal safe change, added the `read_only` ingest-worker tmpfs fix, pinned the
`mem_limit`, added a rollback path, and fixed the `work_pool_name` field default +
test deps). Final round left **0 blockers**; the residual "majors" were all
doc-completeness/prose-accuracy nits — now folded in: the ingest worker's
`logging:` block (unbounded-log fix, D3); five more stale single-pool/one-worker
doc lines added to D8 (`orchestration.md:16`, `cicd.md:71`, `cicd.md:45`,
`v0-scope.md:66`); the missing-overlay failure mode corrected to
silent-wrong-station (not `FileNotFoundError`); the poll interval corrected to 10 s;
and the `test_handles_existing_work_pool` `side_effect` made deterministic (callable
keyed on pool name, not a positional list against an unordered set). **No residual
design fork** — the two grill-me forks (dedicated worker + `ingest` pool; root
cause) were already decided. Implementation-precision residuals (per-overlay
ingest-worker bind precision — do NOT copy the backup/CAMELS binds; Phase-3 test
deps on the `work_pool_name` field) are watch-outs for the implementer, not
blockers.

**Grill-me decisions (2026-07-03):** (1) **Approach = dedicated ingest worker +
`ingest` pool** (isolation), not a concurrency knob (Option A is a no-op — the
worker is already unbounded). (2) **Phase 0 root-cause test PARTIALLY DONE on the
mini (2026-07-03) — see "Phase 0 RESULT" below.** OOM/contention is **ruled out**
(OOMKilled=false, Restarts=0, memory far below limit). The residual mechanism
producing the 25–60 min lateness is **narrowed to two consistent candidates**
(async-poll-cycle starvation on the worker's event loop vs. server-side
scheduled-run dequeue latency) — both are resolved OR their blast radius contained
by the dedicated-worker isolation, so the fix does not depend on disambiguating
them. A one-line Phase-0 timing measurement (cron-tick → subprocess-appears gap;
see the RESULT section) tells operators which is dominant, but **Phases 1–3 do not
gate on it**. Plan 062 absorption is **not urgent** (the recreate-hang did not
cause today's loss). Phases 1–3 are unblocked and unchanged.

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

**Confirmed: resource exhaustion is ruled out.** Cause 1 (contention/OOM) is
**ruled out** (OOMKilled=false, Restarts=0, memory far below limit). Cause 2
(Plan-062 recreate-hang) is a *distinct, separate* symptom (total stall until
manual `restart`, seen on `up -d` recreate) — not what produces the recurring,
bounded 25–60 min lateness here.

**The `docker top → 2` evidence corrects the naive "run is queued behind
forecast-cycle" story and narrows — but does not by itself pin — the lateness
mechanism.** Two subprocesses concurrent during the overlap proves the ingest run
*was* submitted and *did* run concurrently (F1: no worker-level cap). So the
lateness is NOT "the run waits in a queue for forecast-cycle to finish." That
leaves two mechanisms consistent with `count == 2`:

  - **(a) Async poll-cycle starvation (worker-side, pre-submission).** The
    `ProcessWorker` poll loop runs on a single async event loop
    (`PREFECT_WORKER_QUERY_SECONDS`, default **10 s** between "Discovered N work"
    polls — `prefect.settings.models.worker:42`, `Field(default=10)`; NOT ~2 s).
    When the worker's event loop is CPU-saturated by *managing* the running
    forecast-cycle subprocess (21 members × 2 stations pegs CPU), it can skip or
    delay poll cycles — so the *next* scheduled ingest tick is discovered and
    submitted tens of minutes late. At a 10 s poll cadence, 25–60 min of lateness
    means **150–360 consecutive skipped/delayed polls** — that magnitude points at a
    *sustained* CPU-saturation stall of the event loop, not occasional misses, which
    strengthens mechanism (a) as the dominant explanation. This is fully consistent with `count == 2`: a
    *previous* ingest started fine (hence a second python subprocess is visible),
    while the *current* tick's poll is what is delayed. This is a form of
    poll-starvation, but pre-submission and worker-local — a dedicated ingest
    worker with its own event loop and no forecast-cycle subprocess to manage
    resolves it directly.
  - **(b) Server-side scheduled-run dequeue latency.** The Prefect *server* may be
    slow to emit the scheduled run to the worker (late-run backoff, scheduled-run
    dequeue latency, or slow emission when a pool already has a busy worker). If
    this is dominant, a second *pool* still helps — the `ingest` pool's runs are
    dequeued independently of `default`'s busy worker — but it does NOT help if the
    slowness is a global server throttle. That residual server-side risk is the
    same shared-fate risk already tracked in D6/Q4 and the resource-interaction
    matrix; a dedicated worker + pool is still the correct and lowest-risk first
    move, and Phase 4 validation measures whether lateness persists.

**Disambiguating measurement (Phase 0 follow-on — one line, does NOT gate Phases
1–3).** While a live forecast-cycle runs AND the ingest subprocess is visible in
`docker top` (confirming pickup), measure the gap from the cron tick to when the
subprocess appears, and cross-check the "Discovered N work" poll timestamps:
`docker logs prefect-worker | grep 'Discovered'` (poll cadence) and the
`ingest-observations` flow-run `Scheduled`→`Running` timestamps
(`prefect flow-run ls`). If cron-tick→subprocess is 25–60 min, the delay is
**pre-submission** → mechanism (a) or (b) (poll cadence tells which: sparse
"Discovered" lines during the overlap ⇒ (a); regular polls but late run emission
⇒ (b)). If that gap is short but *completion* is 25–60 min late, the ingest
subprocess itself is being throttled by contention with forecast-cycle — which
contradicts the "runs complete fast" observation below and would re-open Cause 1.
The observation that ingest runs "complete fast once they start" points at a
pre-submission delay (a)/(b), i.e. poll/scheduling latency.

This **strengthens** the plan's direction: the fix is **cadence isolation** (a
dedicated `ingest` worker + pool so the obs poll loop runs on its own event loop,
independent of the forecast-cycle's CPU load and subprocess-management burden),
justified by pre-submission poll/scheduling latency rather than memory. Mechanism
(a) is resolved outright by the second worker; mechanism (b) is mitigated by the
second pool and any residual is the shared server-side risk tracked in D6/Q4.
D3's small `mem_limit` for the ingest worker is still fine — memory was never the
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
  `prefect worker start --pool ingest --type process`. **It uses the SAME image as
  the base worker — `build: .` / `image: sapphire-flow:${VERSION:?...}` — copied
  verbatim from the existing `prefect-worker` service (`docker-compose.yml:68-69`).
  This is the highest-risk implementation detail to leave implicit: an
  `image:`-less service definition is rejected by Compose, and copying the wrong
  image would run stale flow code.** It must replicate the worker's infrastructure
  **except** the heavy bits (see D3). The base `prefect-worker` keeps serving
  `default` (forecast-cycle, weather-history, train, hindcast, backup, onboarding).
- **D3 — ingest-worker resource profile.** The ingest worker does **not** need:
  the `/tmp/sapphire_nwp` 4 GiB tmpfs (`docker-compose.yml:110-114`), the 8 GiB
  `mem_limit`, or the `model_artifacts` / `backups` / `nwp_grids` volumes. It
  **does** need: `read_only: true`, the same
  `cap_drop: [ALL]` + `cap_add: [SETUID, SETGID, CHOWN, FOWNER]`, the
  `db_password` secret, the same `entrypoint`/`command`-side environment
  (`SAPPHIRE_DATA_DIR`, `SAPPHIRE_CONFIG`, `DATABASE_URL_TEMPLATE`,
  `PREFECT_API_URL`, `PREFECT_LOGGING_LEVEL`), the `./config.toml` bind mount, the
  same `depends_on` (prefect-server healthy, init completed), `restart:
  unless-stopped`, `networks: [backend]`, and — **do not omit** — the same
  `logging: {driver: json-file, options: {max-size: 50m, max-file: 5}}` block every
  other service in `docker-compose.yml` carries (postgres, prefect-server,
  prefect-worker, api, caddy, init). The ingest worker runs 288×/day (`*/5`) emitting
  structlog per tick; without the capped `logging:` block Docker's default json-file
  driver grows unbounded and defeats the `cicd.md:162` log-volume cap. Copy the block
  verbatim from the `prefect-worker` service.

  **`tmpfs` MUST include `/data/artifacts` and `/data/raw`, not just
  `/data/cache` — BLOCKER fix (2026-07-03 review).** `ingest_observations_flow`
  calls `setup_production_stores` → `make_pg_stores` → `resolve_artifact_dir()`
  (`src/sapphire_flow/flows/_db.py:42`) → `resolve_data_dir()`, which
  **unconditionally** runs `mkdir(parents=True, exist_ok=True)` for the `raw`,
  `artifacts`, and `cache` subdirs of the data root at flow startup
  (`src/sapphire_flow/config/paths.py:8` `_SUBDIRS = ("raw", "artifacts",
  "cache")`, `:22-23`). Under `read_only: true` with `SAPPHIRE_DATA_DIR=/data`,
  every `mkdir` targets a read-only path unless a writable tmpfs is mounted there,
  so the flow would raise `OSError: [Errno 30] Read-only file system` on the
  first ingest tick and the dedicated worker would be **completely
  non-functional**. Fix: mount all three as tmpfs —
  **`tmpfs: [/tmp, /data/cache, /data/artifacts, /data/raw]`**. These are only
  *created* by the ingest flow (not written — `PgModelArtifactStore` performs no
  artifact-file writes during an obs-ingest run), so tmpfs is sufficient and
  correct. (A cleaner long-term fix — lazy `mkdir` in `resolve_data_dir` so
  subdirs are created only when actually needed — is a larger change to shared
  boundary code and is out of scope for 098; the two extra tmpfs mounts are the
  minimal fix.)

  **`mem_limit` — pinned (2026-07-03 review).** Set **`mem_limit: 512m`**. Obs
  ingest performs only SPARQL HTTP (BAFU LINDAS) + PostgreSQL writes with no
  GRIB/Zarr/raster processing, and the Prefect worker baseline (poll loop + async
  event loop + subprocess manager + interpreter) is small. Before deploy the
  implementer SHOULD sanity-check with `docker stats prefect-worker --no-stream
  --format '{{.MemUsage}}'` at idle and during a live `ingest-observations` run,
  add ~50% headroom, and round up to the next 128 MiB boundary — but `512m` is the
  authoritative pinned value to write into `docker-compose.yml` unless the
  measurement shows a higher peak. Undershooting risks a cgroup-kill of the ingest
  worker itself, recreating the exact outage 098 prevents, so do NOT set it below
  the measured peak + headroom. Both workers together stay well under the
  ~15.84 GiB VM (8 GiB default + 512m ingest). It also does **not** need the
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
  patched by any of these (Compose merges by service name). The failure mode of a
  missing block is **NOT a startup crash** — it is a **silent behavioural
  misconfiguration** (2026-07-03 review correction). When the overlay block is
  simply absent, `SAPPHIRE_CONFIG_OVERLAY` is **unset** on the ingest worker, so
  `_resolve_overlay_paths()` returns `[]`
  (`src/sapphire_flow/config/_overlay.py:29-33` — `if raw is None or raw == "":
  return []`) and `load_merged_toml` uses the **base config alone**. No crash —
  the ingest worker just queries the **wrong station set**: in staging it queries
  **all** operational stations instead of the intended 5-station subset; on the
  macmini it runs against the full config rather than the overlay. The
  `ConfigurationError` / `FileNotFoundError` crash at `cicd.md:474` /
  `_overlay.py:37-38` is a **distinct** scenario — it fires only when
  `SAPPHIRE_CONFIG_OVERLAY` **IS** set to a path that is not bind-mounted (e.g., a
  future compose change adds the env var but forgets the mount). Adding the overlay
  blocks is the correct fix for **both** scenarios; the point for Phase 4 is that
  the validation criterion is **not** "absence of a startup crash" — it is
  **confirming the correct overlay is applied** (see Phase 4). This silent-wrong-
  station failure class applies identically to all three overlays (macmini,
  macmini-nwp, staging).

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
    **Override chain (2026-07-03 review — confirm intentional).** The NWP-on
    deployment layers macmini THEN macmini-nwp (`docker compose -f docker-compose.yml
    -f docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml up -d`,
    documented at `docker-compose.macmini-nwp.yml:1-6`). Compose merges in file
    order, so for `prefect-worker-ingest` the `macmini.yml` block sets
    `SAPPHIRE_CONFIG_OVERLAY=/app/config/overlays/mac-mini.toml` and the
    `macmini-nwp.yml` block **overrides** it to `.../mac-mini-nwp.toml` — the same
    override chain the existing `prefect-worker` block relies on. This is the
    expected, correct behaviour, and **both** overlay blocks are required: without
    the `macmini-nwp.yml` `prefect-worker-ingest` block, the ingest worker in
    NWP-on mode silently runs the runoff-only `mac-mini.toml` overlay (a
    misconfiguration), so this block is mandatory, not optional.
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

  **Dev-mode diagnostics — no port exposure by design (2026-07-03 review note).**
  Under `-f docker-compose.yml -f docker-compose.dev.yml`, `prefect-worker-ingest`
  **does** start (it is in the base compose) but, unlike the regular
  `prefect-worker`, has **no host port re-exposure** — there is no way to reach its
  Prefect pool-polling behaviour from the host in dev mode. This is **intentional**:
  the ingest worker is a headless polling worker, not a UI/debug endpoint. Do **not**
  add port exposure "for symmetry". The diagnostic path for dev-mode ingest-worker
  issues is `docker logs prefect-worker-ingest` (look for the "Discovered" poll
  lines and any `mkdir`/read-only traceback).
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
  - `docs/standards/cicd.md:162` container-count prose: "v0 has 6 containers (no
    PgBouncer, one worker instead of three)" → **7 containers (no PgBouncer, two
    workers instead of three)** — adding `prefect-worker-ingest` makes it 7.
  - `docs/standards/cicd.md:129` upgrade step: also append the **Rollback** sequence
    (from the Phase 1+2 atomicity section) so operators can revert routing to
    `WORK_POOL`, re-run `init`, and run only the `default` worker if
    `prefect-worker-ingest` fails to start during the upgrade window.
  - `docs/standards/cicd.md:478` overlay-pattern prose: it lists the services that
    receive `SAPPHIRE_CONFIG_OVERLAY` as `(prefect-worker, api, init)`. Add
    `prefect-worker-ingest` to that parenthetical → `(prefect-worker,
    prefect-worker-ingest, api, init)`. **State the failure mode accurately (per D4,
    do NOT repeat the pre-correction "crashes with FileNotFoundError" claim):**
    without the overlay block the ingest worker's `SAPPHIRE_CONFIG_OVERLAY` is
    simply unset, `_resolve_overlay_paths()` returns `[]` (`config/_overlay.py:27-33`)
    and the worker **silently falls back to the base config and queries the wrong
    station set** (staging: all stations instead of the 5-station subset; macmini:
    full config instead of overlay) — no crash. The `FileNotFoundError` crash fires
    only in the *distinct* misconfiguration where the env var IS set but the TOML
    bind mount is absent (`cicd.md:474`). Both are prevented by adding the overlay
    block; the doc must not conflate them.
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
  - **`docs/design/v0-flow678-training-pipeline.md:15` (2026-07-03 review — was
    missing).** The simplification table row `Work pools | 3 pools (ops, training,
    hindcast) | Single `default` pool` becomes **false** after 098 adds the second
    pool. Update the v0 cell from "Single `default` pool" to "Two pools (`default`
    + `ingest` — obs-feed isolation via Plan 098; training stays on `default`)".
  - **`docs/design/v0-flow13-model-onboarding.md:16, 907, 936` (2026-07-03 review —
    was missing).** Three stale single-pool statements. `:16` ("Single `default`
    pool. Work pool routing annotation is in place…") → note training still runs on
    `default` but v0 now has **two** pools (the second is `ingest`, dedicated to obs
    ingest via Plan 098). `:907` ("saturating the single `default` work pool with
    concurrent training jobs") and `:936` ("All training in v0 runs on the single
    `default` pool") → drop the word "single" (training still runs on **the**
    `default` pool; a separate `ingest` pool now also exists for obs isolation).
  - **`docs/standards/orchestration.md:16` flow-to-Prefect mapping table (2026-07-03
    review — was missing).** The Flow 2 (`ingest_observations_flow`) row shows
    `Work pool = ops` with no v0 override. After 098, obs ingest uses the `ingest`
    pool in v0. Change that cell to `ingest (v0) / ops (v1)`, mirroring the
    existing Flow-13 annotation style (`training (v0: default)` at `:27`). This is
    the **authoritative** pool-mapping table — leaving it as `ops` would have
    operators route/document ingest onto the wrong pool.
  - **`docs/standards/cicd.md:71` (2026-07-03 review — was missing).** The "Prefect
    work pool separation" section header states `> **v1-only** … v0 uses a single
    `default` work pool.` This is adjacent to the topology table D8 already edits but
    was itself missed. Amend to note v0 now runs **two** pools (`default` + `ingest`)
    — the `ingest` pool + dedicated worker is a v0b obs-feed-isolation addition
    (Plan 098); the three-pool ops/training/hindcast topology below still applies to
    v1.
  - **`docs/standards/cicd.md:45` (2026-07-03 review — was missing).** "Config bind
    mount: `./config.toml:/app/config.toml:ro` on api and **all three workers**."
    The "three workers" phrasing tracks the v1 topology table; v0 now has **two**
    workers (`prefect-worker` + `prefect-worker-ingest`), both of which mount the
    config. Clarify: "…on `api` and both v0 workers (`prefect-worker`,
    `prefect-worker-ingest`); the three-worker phrasing refers to the v1 topology."
    Prevents an in-document contradiction with the new ingest-worker row.
  - **`docs/v0-scope.md:66` §A3 PgBouncer deferral (2026-07-03 review — was
    missing).** Two edits on this line: (i) "One API process + **one** Prefect
    worker = no connection pooling needed" is now factually wrong — change to "One
    API process + **two** Prefect workers (general `default` + dedicated `ingest`,
    Plan 098)". (ii) The line's own review trigger — "Revisit … if **multiple worker
    processes are introduced**" — is now literally tripped by 098. Add a note that
    the trigger was evaluated: **PgBouncer deferral remains safe** — the ingest
    worker adds only a small SPARQL+PG-write footprint (5-10 connections each, well
    below exhaustion on the single Postgres), so no PgBouncer is required in v0
    despite the second worker. Record the evaluation so the trigger is not silently
    left tripped.

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

**Rollback (2026-07-03 review — covers the failure mode where the combined deploy
lands but `prefect-worker-ingest` then fails to start or crashes on startup, e.g.
a missing `/data/artifacts` tmpfs, too-low `mem_limit`, missing `db_password`
propagation, or a wrong overlay path).** At that point `init` has already
re-registered `ingest-observations` onto the `ingest` pool and the `default`
worker no longer claims it, so the obs feed is **dead** until recovery. The
rollback restores the pre-098 state (LATE obs, not dead obs):
  0. **Pre-upgrade (do this BEFORE the upgrade window opens):** verify the current
     image is still present and tag it as a rollback anchor. The project ships **no
     registry-publish workflow** (`cicd.md:256` — "No image publish / release
     workflow is shipped today"), so images are local-only in the common v0
     pattern; if the pre-upgrade image has been pruned there is **nothing to roll
     back to**. Run
     `docker images sapphire-flow --format "{{.Tag}}"` to confirm the current tag,
     then `docker tag sapphire-flow:${OLD_VERSION} sapphire-flow:rollback-backup`
     so the pre-upgrade image survives a later `docker image prune`.
  1. `docker compose stop prefect-worker prefect-worker-ingest` — quiesce both.
  2. Revert `register_deployments.py` to the pre-098 routing (no `INGEST_POOL`;
     `ingest-observations` routes to `WORK_POOL = "default"`) and deploy the
     previous image — either rebuild the revert, or point `VERSION` in `.env` back
     to the tagged `rollback-backup` / `${OLD_VERSION}` image from step 0 and
     `docker compose up -d`. (Mirrors the `cicd.md` upgrade procedure, which sets
     the image tag via `VERSION` in `.env`, `cicd.md:129`.) **Note:** if the revert
     also requires undoing a DB migration shipped in the same upgrade, follow the
     `cicd.md` rollback note (restore from backup + redeploy) — a code-level
     rollback that crossed a migration boundary also needs a DB restore. 098 itself
     ships **no** migration, so this only applies if 098 is bundled with a
     migration-carrying release.
  3. `docker compose run --rm init` — re-registers `ingest-observations` back onto
     the `default` pool. (`init` is idempotent — `register_deployments.py:139`.)
  4. `docker compose up -d prefect-worker` **without** the `prefect-worker-ingest`
     service — the `default` worker now serves the obs feed again.
This same sequence is added to the revised `cicd.md:129` upgrade step (D8) so
operators have it at hand during the upgrade window. The symmetric partial-deploy
failures (Phase 1 without Phase 2 → workerless `ingest` pool; Phase 2 without the
`ingest` pool created → ingest worker polls an empty pool) reduce to the same
recovery: revert routing to `WORK_POOL`, re-run `init`, run only the `default`
worker.

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
- Add `INGEST_POOL = "ingest"` and a **field-level-defaulted**
  `work_pool_name: str = WORK_POOL` on `DeploymentSpec` (`:24-30`). The default is
  **required** (not a bare `work_pool_name: str`): `DeploymentSpec` is
  `@dataclass(frozen=True, slots=True)` (`register_deployments.py:24`) and every
  existing `_build_specs()` call site and test constructs it **without**
  `work_pool_name`. A defaulted field keeps all those call sites compiling
  unchanged; only `ingest-observations` passes `work_pool_name=INGEST_POOL`
  explicitly. (A required field would break every `DeploymentSpec(...)`
  construction with a missing-argument error.)
- Set `work_pool_name=INGEST_POOL` on the `ingest-observations` spec (`:43-48`).
- Add `concurrency_limit=1` on the `ingest-weather-history` spec (`:94-99`, D5).
- **Restructure `register_all` — minimal two-step reordering (2026-07-03 review
  correction).** Today `register_all` (`:138-157`) creates the `default` pool
  **inside** the `async with get_client()` block (`:144-151`) and only then calls
  `specs = _build_specs()` **after** that block closes (`:153`). Phase 1 needs the
  pool set derived from `specs`, so `specs` must exist **before** pool creation.
  The strictly required change is only two steps — **do NOT collapse the
  `_register_one` registration loop into the client context**:
  1. Move `specs = _build_specs()` to **before** the `async with get_client()`
     block (so the pool set is in scope when the block opens — otherwise the
     reference is `UnboundLocalError`/`NameError`).
  2. Inside the `async with get_client() as client:` block, replace the single
     `create_work_pool(WORK_POOL)` call with a loop over the distinct pool names,
     `{spec.work_pool_name for spec in specs}`. **Ordering is irrelevant** — a
     Python `set` is unordered, and pool-creation order does not matter; do **NOT**
     add `sorted()` or `list(dict.fromkeys(...))` (the earlier "ordered dedup fine"
     phrasing was misleading — there is nothing to order). **Each pool gets its
     OWN per-iteration `try/except ObjectAlreadyExists`, INSIDE the loop body.**
     This is a **BLOCKER-level** implementation detail: a single `try/except`
     wrapping the *whole* loop (a common error when copy-pasting the existing
     single-pool pattern) would let the **first** `ObjectAlreadyExists` abort
     creation of the **second** pool — the exact failure the per-iteration guard
     prevents. Add this as a code comment at the loop:
     `# Each pool gets its own guard — a single try/except around the loop would
     abort on the first ObjectAlreadyExists.`
     **Also bind the two `log.info` calls to the LOOP VARIABLE, not `WORK_POOL`.**
     The existing `log.info("workpool.created", name=WORK_POOL)`
     (`register_deployments.py:149`) and `log.info("workpool.exists",
     name=WORK_POOL)` (`:151`) are inside the block being replaced; after the
     restructure they must reference the current pool name (`name=pool_name`),
     otherwise **both** pool events log `name="default"` regardless of which pool
     was created vs. already existed, and operators cannot distinguish them in the
     structured logs.
  3. The `for spec in specs: await _register_one(spec)` registration loop stays
     **outside** the `async with` block, exactly as today (`:153-155`).

  **Why registration stays outside the client context (review correction):**
  `_register_one` calls `flow_fn.afrom_source(...)` and `sourced_flow.adeploy(...)`
  (`register_deployments.py:111`, `:128`) — both open and manage their **own**
  internal Prefect client connections; they do **not** use the passed-in `client`.
  Collapsing the 10 `_register_one` awaits inside a single `async with
  get_client()` context would hold that outer client connection open across all 10
  `adeploy` calls (minutes of work) for no benefit — a gratuitous change to
  connection management. The current split (pool creation inside the client
  context, spec registration outside) is correct as-is; only the `specs` ordering
  and the pool loop change. The per-iteration guard is what the partial-existing
  test exercises.
- **Author the per-iteration-guard test IN THIS PHASE, not Phase 3 (2026-07-03
  review).** Phase 1 and Phase 3 are separate atomic commits, so a subtly-wrong
  single-block `try/except` shipped in Phase 1 would go **uncaught until Phase 3
  lands**. To close that window, the `test_handles_existing_work_pool` partial-path
  improvement (`side_effect=[ObjectAlreadyExists("pool exists"), None]` — see
  Phase 3) MUST be authored **together with the Phase 1 code change**, exercising
  the per-iteration guard the moment it is introduced. (Phase 3 remains the
  home for the *other* register-deployments test updates; only this one guard test
  is pulled forward. Since Phase 1 and Phase 3 both touch the same test file and
  are "authored together" per the Phase 3 preamble, this is a sequencing note, not
  a structural split.)
- `_register_one` reads `spec.work_pool_name` and passes it to `adeploy` (`:118`)
  instead of the module-level `WORK_POOL` constant.
- **`init` service needs no change.** It invokes `register_all`, which now creates
  both pools; there is no new env var (no `SCHEDULE_INGEST_POOL` or similar) and no
  change to the `init` service definition. Pool identity is derived from the specs,
  not injected. See D7 for the `init` one-shot lifecycle and why the new worker's
  `depends_on: init` does not introduce a recreate hang.

**Phase 2 — docker-compose worker service (D2/D3/D4) + affected docs (D8).**
- Add `prefect-worker-ingest` to `docker-compose.yml` per D3:
  **`build: .` / `image: sapphire-flow:${VERSION:?...}` copied verbatim from the
  existing `prefect-worker` at `docker-compose.yml:68-69`** (do NOT omit `image:` —
  Compose rejects an image-less service),
  `command: prefect worker start --pool ingest --type process`, pinned
  `mem_limit: 512m`, `read_only: true`, **`tmpfs: [/tmp, /data/cache,
  /data/artifacts, /data/raw]`** (the last two are required — the ingest flow's
  `resolve_data_dir()` mkdir's all three subdirs at startup, `paths.py:8,:22-23`;
  omitting them raises read-only-fs on the first tick), no NWP tmpfs, no heavy
  volumes; same `depends_on: prefect-server healthy + init completed` as
  `prefect-worker`, per D7.
- Add matching `prefect-worker-ingest` patch blocks to **all three** overlays
  that patch `prefect-worker` by name (D4):
  - `docker-compose.macmini.yml`
  - `docker-compose.macmini-nwp.yml`
  - `docker-compose.staging.yml` (`SAPPHIRE_CONFIG_OVERLAY` +
    `staging-5-stations.toml` `:ro` bind mount, mirroring `:7-11`)
- Do **not** add a block to `docker-compose.dev.yml` (D4: the ingest worker needs
  neither the re-exposed ports nor the CAMELS-CH mount).
- Update `docs/standards/cicd.md` per D8: upgrade step `:129` (`stop
  prefect-worker prefect-worker-ingest` **+ the Rollback sequence**), the
  service-topology table (`:13-24` — add the ingest row **and** fix the stale
  `prefect-worker` `postgres` dependency), the v0 dependency-chain diagram
  (`:59-66`), the container-count prose (`:162` — "6 containers … one worker" →
  "7 containers … two workers"), and the overlay-pattern prose (`:478`).
- Update `docs/standards/orchestration.md` per D8: annotate the four stale
  "single `default` pool" statements (`:5`, `:33`, `:59`, `:132`) — v0 now runs
  two pools (`default` + `ingest`).
- Update `docs/v0-scope.md` per D8: §A6 clarifying note **and** the service-topology
  table (`:449-458` — add the `prefect-worker-ingest` row + adjust the "Not in v0"
  note).
- Update the two design docs per D8: `docs/design/v0-flow678-training-pipeline.md:15`
  ("Single `default` pool" → two-pool cell) and
  `docs/design/v0-flow13-model-onboarding.md:16, 907, 936` (drop "single" from the
  three single-`default`-pool statements).

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
  names appear across the two calls. **Concrete extraction pattern (2026-07-03
  review — use exactly this):**
  `assert {c.args[0].name for c in mock_client.create_work_pool.await_args_list}
  == {"default", "ingest"}`. The call site passes `WorkPoolCreate(...)` as the
  **sole positional argument** (`register_deployments.py:146-148`), so the mock
  records it at `call_args.args[0]`, **not** as a kwarg — using `.kwargs["name"]`
  raises `KeyError`. `WorkPoolCreate` is a Pydantic model, so `.args[0].name`
  reads the pool name cleanly. The result set comparison is order-independent.
  Also replace the `== 10` register count (`:225`) with the exact set of
  deployment names (see `test_returns_ten_specs` below for the rationale — a set
  assertion is non-circular and more informative than `len(_build_specs())`).

*One existing test still PASSES after Phase 1 but is incomplete — improve it (do
not skip as optional cleanup):*

- **`test_handles_existing_work_pool` (`:228-255`) — still passes, improve.** It
  is **not** broken by Phase 1: it uses a **scalar**
  `side_effect=ObjectAlreadyExists("pool exists")`, so after Phase 1 both
  per-iteration pool-creation calls raise and both are caught by the per-iteration
  guard — all specs still register and `mock_register.await_count == 10` still
  holds. It merely exercises only the "both pools already exist" path. Change it to
  test the realistic **partial** case (`default` pre-exists, `ingest` is new) and
  confirm the per-iteration guard catches only the raising call while the other
  succeeds. **Use a callable `side_effect` keyed on the pool name — NOT a positional
  `[ObjectAlreadyExists(...), None]` list (2026-07-03 review correction).** The
  production loop iterates `{spec.work_pool_name for spec in specs}`, an **unordered
  `set`**, and the plan deliberately does **not** add `sorted()` (D-Phase-1: ordering
  is irrelevant for the impl). A positional `side_effect` list would therefore map
  to whichever pool the set happens to yield first — non-deterministic across
  Python versions, so the test could silently pass without ever exercising the
  guard for the `ingest` pool. Key the raise on the argument instead:
  `create_work_pool.side_effect = lambda work_pool: (_ for _ in ()).throw(
  ObjectAlreadyExists("pool exists")) if work_pool.name == "default" else None`
  (or an equivalent `def` that raises for `name == "default"` and returns `None`
  otherwise). This makes "`default` pre-exists, `ingest` is created" deterministic
  regardless of set iteration order, and lets the test assert the `ingest` pool
  creation specifically succeeded. Replace the `== 10` register count (`:255`) with
  the **exact registered-name
  set** (option (b) in `test_returns_ten_specs` above — non-circular), not
  `len(_build_specs())`. Add a
  companion `test_handles_all_work_pools_existing` with
  `side_effect=[ObjectAlreadyExists(...), ObjectAlreadyExists(...)]` for the
  both-existing path.
- **`test_returns_ten_specs` (`:69-72`).** The count is **unchanged by 098** (still
  10). **Do NOT replace `== 10` with `len(_build_specs())` (2026-07-03 review
  correction)** — that is circular: the test would call the same function under
  test to produce its own expected value, so an accidental ±1 change to
  `_build_specs()` would silently pass and the regression guard is destroyed. Two
  acceptable options, pick one: (a) **keep the literal `== 10`** and add an inline
  comment enumerating the ten named deployments, or (b) — preferred, non-circular
  and more informative — assert the **exact set of deployment names**:
  ```python
  assert {s.deployment_name for s in _build_specs()} == {
      "ingest-observations", "forecast-cycle", "backup-database", "train-models",
      "run-hindcast", "compute-skills", "compute-combined-skills",
      "onboard-stations", "onboard-model", "ingest-weather-history",
  }
  ```
  Option (b) also subsumes the `test_creates_work_pool_and_registers_all` /
  `test_handles_existing_work_pool` register-count assertions: assert the exact
  registered-name set there too, rather than a bare count or `len(_build_specs())`.

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
- **`test_registers_scheduled_flow` (`:120-153`) — update the assertion so it is
  not vacuously true (2026-07-03 review).** After Phase 1 the field is defaulted
  (`work_pool_name: str = WORK_POOL`), so the spec at `:121-126` — which does not
  set `work_pool_name` — defaults to `WORK_POOL` and the assertion at `:149`
  (`== WORK_POOL`) still passes, but only by accident (spec default == module
  constant). Change the assertion to read the spec:
  `assert call_kwargs["work_pool_name"] == spec.work_pool_name` (equivalently
  `== WORK_POOL` with a comment that it is the spec's default, not the module
  constant). This makes the intent explicit and prevents a future non-default spec
  from silently passing the wrong assertion. Set `work_pool_name=WORK_POOL`
  explicitly on the spec construction at `:121-126` for the same clarity.
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
worker — explicitly bring the ingest worker up under **each** of the three
overlays (macmini, macmini-nwp, staging), confirm `SAPPHIRE_CONFIG_OVERLAY`
resolves and the TOML bind mount is present, **and — critically — confirm the
ingest worker queries the CORRECT station set, not just the absence of a startup
crash** (2026-07-03 review: a missing overlay block does **not** crash — it
silently falls back to the base config and queries the wrong stations; see D4).
For staging, verify the ingest run queries the **5-station** subset (not all
operational stations); for macmini, verify it uses the mac-mini overlay config.
Check via the flow-run's structured logs / queried-station count, and (d) an upgrade following the revised `cicd.md:129` step
(`stop prefect-worker prefect-worker-ingest`) quiesces both workers before
`init`/migrations re-run.
- **Post-deploy smoke check (do this FIRST, within 10 min of the combined
  deploy).** Confirm the ingest worker is actually serving — the D3 `read_only`
  tmpfs fix means the first tick must not raise `OSError: Read-only file system`.
  Run `prefect flow-run ls` (or filter to the `ingest-observations` deployment)
  and confirm a run reaches `Running`/`Completed` within one-to-two `*/5`
  intervals; also `docker logs prefect-worker-ingest` should show a "Discovered"
  poll and NO `mkdir`/read-only traceback. If the ingest worker crash-loops or the
  first run fails, execute the **Rollback** sequence (above) immediately so the
  obs feed reverts to the `default` worker rather than staying dead.

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
service table, and the two design docs
`docs/design/v0-flow678-training-pipeline.md:15` +
`docs/design/v0-flow13-model-onboarding.md:16,907,936` single-pool statements)
per the CLAUDE.md "every code change updates docs" rule (D8).
