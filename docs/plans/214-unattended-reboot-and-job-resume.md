---
status: DRAFT
created: 2026-08-29
plan: 214
title: Stop unattended macOS updates killing long jobs, and make a killed job visible
scope: One macOS update-policy change on the staging host, and one watchdog-visible probe for flow runs stuck in RUNNING. Auto-resume is DEFERRED to a follow-on (see § Deferred). No change to any flow's business logic.
depends_on: []
blocks: []
source: 2026-08-29 incident — macOS 26.6.2 auto-installed and rebooted the mini at 01:51 CEST, killing a 12-hour backfill; Prefect reported it RUNNING for the next 8 hours
---

# Plan 214 — unattended reboots kill long jobs silently

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ Proportionality is a binding constraint on this plan AND on its review

The cause is **already known** (§ Root cause) — this plan does **not** contain an investigation
task, and adding one is a finding against the reviewer, not the plan. Two small changes: a host
setting, and a detection probe.

**Rules binding every reviewer:**

1. **"No findings" is a complete and welcome review.**
2. **A finding must name a CONCRETE FAILURE** with `file:line`.
3. **Do not propose new apparatus** — no new daemon, no new service, no new dashboard. The
   watchdog (`src/sapphire_flow/ops/watchdog.py`) and its Slack webhook already exist; use them.
4. **Do not propose making flows generally resumable.** Auto-resume is deferred (§ Deferred).
5. **Adding length is a cost.**

*(Round-2/3 note: this plan grew. Every added paragraph exists to close a reviewer blocker — the
liveness signal, the Prefect access boundary, the alert-latch state, the scope cut of T3, and
round 3's push-vs-live correction in D5 plus the threshold relocation in D6. Round 3 also DELETED
~15 lines of vendored-library line-number citations from D7/D3/D4/Deferred. Nothing was added for
completeness's sake.)*

## Root cause — established, not inferred

Evidence gathered on the mini 2026-08-29:

- **`kern.boottime` = Sat Aug 29 01:51:04 CEST** (23:51:04 UTC). All eight containers restarted
  together at 23:54:07 UTC, within milliseconds — a host event, not a per-service one.
- **`/Library/Receipts/InstallHistory.plist` records `macOS 26.6.2` installed at 23:53:16 by
  `softwareupdated`**, followed by `RosettaUpdateAuto` at 23:53:28.
- **`AutomaticallyInstallMacOSUpdates = 1`**, `AutomaticDownload = 1`, `CriticalUpdateInstall = 1`
  in `/Library/Preferences/com.apple.SoftwareUpdate.plist`.
- **No panic report exists** (`/Library/Logs/DiagnosticReports/*.panic` is empty). This was not a
  crash.

**The mini reboots itself whenever Apple ships a macOS update, and will do so again.**

## Which unit actually got stuck — pinned down, not assumed

The stuck run was the **`onboard-stations` deployment**, i.e. one run of
`onboard_stations_flow` (`src/sapphire_flow/flows/onboard.py:82`, registered at
`src/sapphire_flow/cli/register_deployments.py:141`). Two facts fix this:

- Prefect reported the run `StateType.RUNNING` for 8 h. Only a Prefect-tracked flow run has a
  state. `scripts/backfill_meteoswiss_history.py` contains **no** `@flow` or `@task` — it calls
  `run_backfill` directly (`scripts/backfill_meteoswiss_history.py:194`), so a run of that script
  would have been invisible to Prefect entirely and could not have been "reported RUNNING".
- `run_backfill` has exactly **two** callers in the tree: that script, and
  `src/sapphire_flow/services/onboarding.py:600` (Step 4c), which executes inside
  `onboard_stations_flow`.

**This matters for scope.** `onboard_stations_flow` is a single flow whose body runs eight
sequential steps in one function — store basins (`onboarding.py:260`), store stations (`:277`),
store observations (`:464`), store forcing + weather-source mappings (`:490`, `:507`), the backfill
(`:600`), QC/baselines/flow-regimes (`:636`, `:700`, `:747`), model assignment (`:775`), training
trigger (`:878`), mark OPERATIONAL (`:1034`). There is **no** `@flow`/`@task` boundary around the
backfill call alone. Re-triggering this deployment re-executes all eight steps, not just
`run_backfill`'s idempotent fetch loop — which is why T3 was cut (§ Deferred).

## What already works, and must not be "fixed"

- **Containers recover on their own.** All **seven long-running services** carry
  `restart: unless-stopped` and were back within three minutes of boot. The eighth container,
  `init`, is a one-shot that carries `restart: "no"` (`docker-compose.yml:431`) — it had already
  completed successfully; it is not meant to restart. The container layer is not the problem.
- **`run_backfill` itself is idempotent.** `src/sapphire_flow/services/reanalysis_backfill.py:283`
  calls `forcing_store.fetch_covered_days` per (year, station-batch) and skips the fetch entirely
  when the days are already stored (`chunks_skipped`). Re-running *that function* costs a DB query
  per chunk, not a re-download. **This says nothing about the other seven steps of the flow that
  contains it** — see § Deferred.

## What is broken

**A killed flow run stays `RUNNING` in Prefect forever, and nothing tells anyone.**

- The 2026-08-28 backfill was reported `StateType.RUNNING` for **8 hours** after its process died.
  It had to be marked `CRASHED` by hand.
- **This is not rare and not only caused by reboots.** It happened *twice* on 2026-08-29 — once
  from the macOS reboot, once when an operator redeployed (`compose up --build` recreates
  containers under a running process). Any deploy during a long job reproduces it.
- The existing watchdog probes health, BAFU freshness, forecast freshness, disk, backup target and
  launchd agents (`src/sapphire_flow/ops/watchdog.py`) — **it has no check for a stuck flow run**,
  so the failure is invisible until someone looks.

Cost of the incident: ~12 h of backfill wall-clock, and a day where the fleet looked onboarded and
was not.

## Decisions

- **D1 — Prevent first. Turn off automatic *macOS* updates on the staging host; keep XProtect/MRT
  config-data updates.** Three keys are cited in § Root cause and T1 must state a value for each:
  - `AutomaticallyInstallMacOSUpdates` → **0**. This is the key that installed 26.6.2 and rebooted.
  - `CriticalUpdateInstall` → **0**. It is *not* the XProtect/MRT switch (that is
    `ConfigDataInstall`, which is **left at its current value**); `CriticalUpdateInstall` governs
    auto-installed system security updates, which on modern macOS can themselves require a restart.
    Leaving it at 1 would leave a second, untouched path to the exact reboot this plan exists to
    stop.
  - `AutomaticDownload` → **left at 1**. Downloading does not reboot; keeping it means a maintenance
    window applies an already-staged update instead of waiting on a download.
  **This is an owner decision, not an implementer one** — it trades unattended patching for job
  continuity, and the host is on the office LAN. Updates become download-and-notify, applied
  during a maintenance window.

- **D2 — Detect before resume.** A stuck run that nobody sees is worse than one that does not
  restart. Detection (T2) is the whole of this plan's remediation surface.

- **D3 — The measured signal is total time since the run entered RUNNING. Say so plainly.**
  Prefect records a state row only at a transition (the installed prefect-client's `State` model
  carries one `timestamp`, set per transition); a flow that sits in RUNNING receives no
  periodic "still alive" transition. Nor is there a task-run heartbeat to fall back on:
  `onboard_stations_flow` has exactly one `@task` (`_download_task`,
  `src/sapphire_flow/flows/onboard.py:36`), which runs only when `download=True`; its twelve-hour
  body is plain function calls. So "age of current state" **is** "total duration since it started
  running", and the earlier draft's claim that the two differ was **wrong** — corrected here.
  Consequence: the threshold must sit comfortably above the longest legitimate run, and duration
  alone is **not** evidence the process is dead.

- **D4 — Duration-based detection ALERTS, it never remediates.** Because D3's signal cannot tell a
  dead run from a slow live one, the probe's alert text must say so ("still RUNNING after N h — may
  be alive; check before acting"). Nothing in this plan changes a flow run's state or launches a
  run. Positive dead-process evidence (matching the prefect-client `FlowRun` model's
  `infrastructure_pid` against a live process in the owning worker
  container, bound to that container's identity and start time to defeat PID reuse) is a
  **precondition of the deferred auto-resume work**, not of T2.

- **D5 — The Prefect query runs inside the compose network, in the API container. Prefect's port
  stays unpublished.** The host watchdog has no path to Prefect today and must not be given one:
  `prefect-server` publishes no port in the base topology (`docker-compose.yml`, the
  `prefect-server` block has no `ports:`), the Mac mini overlay publishes only the SAPPHIRE API
  (`docker-compose.macmini.yml:69`), and 4200 is published only by the dev overlay
  (`docker-compose.dev.yml:14`), which the staging launcher does not use (`scripts/launchd/start-sapphire.sh`).
  Publishing an unauthenticated Prefect API onto the LAN would be a security regression, and the
  watchdog's tokens are strictly GET-only by policy (`docs/standards/security.md:20`).
  Instead: **the API container already holds `PREFECT_API_URL`** (`docker-compose.yml:274`, the
  `api` service). T2 puts the scan there, behind the existing admin-GET auth dependency
  (`require_admin`, used by `health_detail` at `src/sapphire_flow/api/routes/health.py:68-72`), and
  the watchdog polls it with the GET-only probe token it already carries
  (`probe_token_path`, `src/sapphire_flow/ops/watchdog.py:1470`). **No new port, no new token, no
  write scope.**
  *(Trade-off, recorded not hidden: detection now depends on the API container being up. That is
  already true of every other watchdog freshness probe, and a dead API raises its own alert via
  `probe_health`, `src/sapphire_flow/ops/watchdog.py:468`.)*

- **D5a — The signal is COMPUTED LIVE per GET request; it is not a stored record, and nothing new
  writes one.** Every `check_type` on `/health/detail` today is a **push** record: a flow writes its
  own row on completion (`append_health_record`, `src/sapphire_flow/flows/run_forecast_cycle.py:777`)
  and the route does nothing but an unconditional
  `pipeline_health_store.fetch_recent(check_type=..., limit=...)`
  (`src/sapphire_flow/api/routes/health.py:75-87`) — there is **no** live-compute branch for any
  check type in that file. A flow stuck in RUNNING cannot write a row saying it is stuck, so the
  push pattern structurally cannot carry this signal. Therefore: **no periodic writer** — no
  scheduled task, flow or daemon that runs the scan and calls `pipeline_health_store.store(...)`
  (new apparatus under rule 3, and it would not fix the structural problem anyway) — and **no
  `FLOW_RUN_HEALTH` record is ever persisted**: `PipelineCheckType.FLOW_RUN_HEALTH`
  (`src/sapphire_flow/types/enums.py:193`; `docs/spec/types-and-protocols.md:201`) stays declared
  and unwritten, exactly as today.
  *(The earlier draft claimed that enum member and said the watchdog would read it "exactly like the
  three freshness heartbeats" — **wrong**, corrected here. The likeness is only transport: same
  admin GET, same JSON envelope, same bearer token, same `httpx` probe shape as
  `probe_forecast_freshness`. The payload is computed on demand, not read from a store.)*

- **D5b — The live compute is a NEW SIBLING admin-GET route, not a branch inside `health_detail()`.**
  `GET /api/v1/health/flow-runs`, same module (`src/sapphire_flow/api/routes/health.py`), same
  router, same `require_admin` dependency. The in-`health_detail` branch is rejected because that
  route has an HTML twin (`health_detail_page`, `src/sapphire_flow/api/routes/health.py:89-113`)
  sharing `_parse_check_type` and offering **every** `PipelineCheckType` in its dropdown
  (`check_types=[ct.value for ct in PipelineCheckType]`): a live-only check type would render an
  empty table there, and `/health/detail`'s "pure store read" contract would fork in two. A route on
  an existing router under an existing dependency is **not** new apparatus under rule 3 — no new
  service, port, token or channel. The watchdog derives its URL from `health_url` exactly as
  `_forecast_freshness_url_from_health` does (`src/sapphire_flow/ops/watchdog.py:163-167`).

- **D5c — The Prefect call reuses `PrefectStatusAdapter`; it is not re-implemented.**
  `src/sapphire_flow/adapters/prefect_status.py:28` already wraps a `SyncPrefectClient`, builds
  typed `FlowRunFilter`s (`:60-67`), maps `StateType` → the repo's `FlowRunState` (`:29-39`),
  normalises timestamps with `ensure_utc` (`:78-82`), and converts any client exception into
  `AdapterError` (`:101-104`) — the exact exception boundary T2(a)'s "unreadable" branch needs. It
  has **zero production callers** today (only `tests/integration/adapters/test_prefect_status.py`),
  so extending it disturbs nothing. Add one method that filters server-side on state —
  `FlowRunFilter(state=FlowRunFilterState(type=FlowRunFilterStateType(any_=[StateType.RUNNING])))`,
  both classes present in the pinned client's `prefect.client.schemas.filters` beside the
  `FlowRunFilterStartTime` the adapter already imports — returning a **new** frozen row type
  (`run_id`, `deployment_name`, `flow_run_name`, `state`, `running_since`); `FlowRunStatus`
  (`src/sapphire_flow/types/pipeline.py:27-33`) has no deployment field and is left untouched, so
  `fetch_recent_runs` and its tests stay green. The route constructs it with
  `get_client(sync_client=True)` (a documented overload of `prefect.client.orchestration.get_client`
  returning `SyncPrefectClient`), safe from FastAPI's **sync** handlers — `health_detail` is sync
  today. The client is a constructor argument (`prefect_status.py:41`), so route tests inject a fake
  exactly as the adapter's tests do (`tests/integration/adapters/test_prefect_status.py:85`).
  **Deployment identity:** the prefect-client `FlowRun` model carries `deployment_id` (a UUID), not
  a name, so the scan resolves each distinct id once via the sync client's `read_deployment`; a run
  with no `deployment_id` (ad-hoc/subflow) reports a null deployment name and takes the default
  threshold (D6) — never exempt.
  *(The earlier draft justified this query by citing the `httpx.get(.../health)` up/down ping,
  `src/sapphire_flow/api/routes/health.py:31-36` — a much smaller capability and no precedent for
  listing runs. Corrected here.)*

- **D6 — Per-deployment thresholds are module-level constants in `watchdog.py`, exactly like the
  other five; the API returns raw rows and no verdict.** This is the pattern the file already uses
  five times over: `DISK_FREE_THRESHOLD_PCT`, `BACKUP_STALE_THRESHOLD`, `BAFU_STALE_THRESHOLD`,
  `BAFU_OBS_STALE_THRESHOLD`, `FORECAST_FRESHNESS_STALE_THRESHOLD` are all plain module constants in
  `src/sapphire_flow/ops/watchdog.py:177-194`, and the staleness comparison is computed **in the
  watchdog process** against a timestamp the API returns undecided
  (`now - forecast_freshness_result.cycle_time > FORECAST_FRESHNESS_STALE_THRESHOLD`,
  `src/sapphire_flow/ops/watchdog.py:2001-2006`). So: `FLOW_RUN_STUCK_THRESHOLDS: dict[str,
  timedelta]` keyed by deployment name plus `DEFAULT_FLOW_RUN_STUCK_THRESHOLD: timedelta`, both in
  `watchdog.py` beside the other five; the API route returns only raw rows
  (`run_id`, `deployment_name`, `flow_run_name`, `state`, `running_since`) and never says "stuck".
  Unknown or null deployment name → the default (never silently exempt). Seed values:
  `onboard-stations` = 20 h (the observed legitimate run is ~12 h), default = 24 h. Owner-tunable
  constants, not config surface.
  Two homes rejected: `DeploymentSpec` (`src/sapphire_flow/cli/register_deployments.py:32`) has no
  threshold field and its deployment inventory is locked by a test
  (`tests/unit/cli/test_register_deployments.py:33`); `WatchdogConfig`
  (`src/sapphire_flow/ops/watchdog.py:1470` and the surrounding dataclass) holds only global
  settings, and the host watchdog never receives `SAPPHIRE_CONFIG_OVERLAY`
  (`docs/touchpoint-maps.md:710`).
  *(The earlier draft put the mapping API-side, which split the "is it stuck" verdict across two
  processes — a division none of the other five probes use. Corrected here.)*

- **D7 — A Prefect Automation was evaluated and rejected for T2; it is the leading candidate for
  the deferred auto-resume work.** The pinned server ships a working, self-hosted Automations engine
  (router, client SDK, and the `RunDeployment` / `ChangeFlowRunState` / `CallWebhook` /
  `SendNotification` actions, with a proactive `EventTrigger` posture) — checked, not assumed; the
  exact module paths are deliberately not cited here, since this plan touches none of that code and
  the line numbers would rot on the next `prefect` bump.
  **Rejected for T2** for two concrete reasons, not for unfamiliarity: (a) the Slack webhook URL is
  a **host** file the watchdog reads (`src/sapphire_flow/ops/watchdog.py`, `DEFAULT_SLACK_PATH =
  Path("./secrets/slack_webhook_url")`) — a `CallWebhook` action would require introducing that
  secret into the compose stack, which is new apparatus under rule 3; and (b) an Automation would
  bypass the watchdog's existing delivery/hysteresis machinery (`WatchdogState`'s pending-
  notification fields, `src/sapphire_flow/ops/watchdog.py:265`), duplicating alert policy in a
  second place with different semantics. Note also that a proactive `EventTrigger`
  (`prefect.flow-run.Running` not followed by a terminal event `within` N seconds) measures
  **exactly the same quantity as D3** — it is not a better liveness signal, only a different host
  for the same one.
  **Kept for the deferred work**, where its advantages are real: it runs server-side (so it does
  not need the GET-only host token to gain write scope, `docs/standards/security.md:20`), it is
  event-driven rather than bound to the watchdog's 300 s tick, and it survives the watchdog itself
  being killed by the same host event.

## Tasks

### T1 — set the macOS update policy on the staging host
*In:* the three key values enumerated in D1, applied on the mini; **and a revision of
`docs/deployment/mac-mini-staging.md:49-54`**, which today tells operators to blanket-uncheck
"Install macOS updates" and "Install application updates" in System Settings with no carve-out —
guidance that was demonstrably never applied (the host measured
`AutomaticallyInstallMacOSUpdates = 1`). Replace those lines with the `defaults`-level procedure,
the per-key rationale from D1, and the maintenance-window procedure. The doc must end with **one**
non-contradictory instruction.
*Out:* anything about the Nepal host; anything about Docker Desktop's own auto-update (separate,
and it does not reboot the host); `ConfigDataInstall` (left alone by D1).
*Exit:* `defaults read /Library/Preferences/com.apple.SoftwareUpdate.plist` on the mini shows
`AutomaticallyInstallMacOSUpdates = 0`, `CriticalUpdateInstall = 0`, `AutomaticDownload = 1`;
`grep -n "Install macOS updates" docs/deployment/mac-mini-staging.md` returns nothing (the
superseded instruction is gone, not merely supplemented); the doc records the maintenance-window
procedure.

### T2 — make a flow run stuck in RUNNING visible
*In:* two halves, both reusing established shapes.

**(a) API side.** A new admin-GET route `GET /api/v1/health/flow-runs` in
`src/sapphire_flow/api/routes/health.py` (D5b), **computed live per request** (D5a): it reads
nothing from `pipeline_health_store`, writes nothing anywhere, and persists no health record. It
lists flow runs whose current state is `RUNNING` through a new state-filtered method on
`PrefectStatusAdapter` (D5c) and returns one **raw** row per run — `run_id`, `deployment_name`
(null when the run has no `deployment_id`), `flow_run_name`, `state`, `running_since` (the run's
`start_time`, which per D3 equals the age of the current RUNNING state for these flows) — plus an
explicit readability field. No threshold and no verdict: the watchdog owns
those (D6). Read-only: no state change, no run creation. If the Prefect query fails the adapter
raises `AdapterError` (`src/sapphire_flow/adapters/prefect_status.py:101-104`), and the response
must say **unreadable**, never an empty "no stuck runs" list (D4's honesty rule, and the same
distinction Plan 195 D4 drew for the launchd probe).

**(b) Watchdog side.** `probe_flow_run_health` in `src/sapphire_flow/ops/watchdog.py` beside
`probe_forecast_freshness` (`:653`), polling that URL with the existing admin probe token
(`probe_token_path` on `WatchdogConfig`) and posting to the existing Slack webhook. It compares each
row's `running_since` against `FLOW_RUN_STUCK_THRESHOLDS` / `DEFAULT_FLOW_RUN_STUCK_THRESHOLD` (D6)
using the injected clock — the same `now - timestamp > THRESHOLD` shape as the forecast-freshness
check at `src/sapphire_flow/ops/watchdog.py:2001-2006`. Alert state is
**per run id** and persisted on the existing `WatchdogState`
(`src/sapphire_flow/ops/watchdog.py:265`) — no new store, no new file, no DB table — in exactly the
Plan-195 per-label shape: `stuck_flow_run_ids: tuple[str, ...]` (transition-latched membership) and
`flow_run_notification_pending: tuple[tuple[str, FlowRunNotificationKind], ...]` (so one run's
failed Slack post can never swallow another's), plus
`flow_run_probe_unreadable_ticks: int` / `flow_run_probe_notification_pending` for the distinct
probe-unreadable condition. A run id leaves the latch when it is no longer RUNNING; the latch is
capped at a fixed number of ids so a pathological day cannot grow the state file without bound.
`WatchdogState.load` must keep round-tripping state files written by the current watchdog
(`tests/unit/ops/test_watchdog.py:2466`).

*Out:* a new service, a dashboard, a new alert channel, any Prefect port publishing, any write to
Prefect, any persisted `FLOW_RUN_HEALTH` record or periodic writer that would produce one (D5a),
and auto-remediation (§ Deferred).
*Exit:* see § Exit gates. In behaviour terms: a run past its threshold produces **exactly one**
alert across repeated ticks and across a watchdog restart; a run under its threshold produces none;
two simultaneously stuck runs produce two independent alerts; a failed Slack post is retried, not
lost; a Prefect query failure produces a "probe unreadable" alert, not silence.

## Deferred — opt-in auto-resume (NOT in this plan)

The earlier draft's T3 ("mark the killed run CRASHED and re-trigger it once, for deployments
flagged resumable") is **cut**. Four preconditions do not hold today, and meeting them is a plan of
its own:

1. **The unit Prefect would re-trigger is not the idempotent unit.** Re-triggering `onboard-stations`
   re-runs all eight steps of `onboard_stations_flow`, including station/audit writes
   (`src/sapphire_flow/services/onboarding.py:284`), QC/hydro characterisation (`:636`), model
   assignment and training (`:775`, `:878`) and model promotion (`:1034`). `run_backfill`'s skip
   logic proves nothing about any of those.
2. **The safe target deployment does not exist yet.** A dedicated `backfill-meteoswiss-history`
   flow + deployment is owned by **READY Plan 122** (§ Phase 1, task 1A) and is not in the
   registered inventory (`src/sapphire_flow/cli/register_deployments.py:96-196`;
   `tests/unit/cli/test_register_deployments.py:33`). Historical backfill is still a supervised
   script (`scripts/backfill_meteoswiss_history.py:3`). **The follow-on must declare
   `depends_on: [122]`.**
3. **Duration is not death (D3/D4).** Remediation needs positive dead-process evidence, or it will
   eventually mark a healthy 14-hour run CRASHED and start a duplicate beside it — and setting
   Prefect state does not terminate the subprocess the process worker actually spawned
   (`docs/standards/orchestration.md:40`).
4. **"Retrigger once" needs a durable recovery state machine, which nothing here provides.** Two
   ordinary failures break the promise: mark-CRASHED succeeds then child creation fails (the next
   scan sees no RUNNING run — recovery lost permanently); child creation succeeds then the
   watchdog dies before recording it (the next tick creates a second child). The follow-on must
   persist recovery intent **before** the remote mutation, create the replacement with a
   deterministic idempotency key derived from the root run (the pinned client's deployment-run
   creation accepts one), record root/attempt
   lineage, cap attempts **across the whole lineage**, reconcile incomplete operations on restart,
   and prove all of it with fault injection after every remote call.

Also unresolved for the follow-on: the host watchdog cannot mutate Prefect without either a LAN-
exposed Prefect API or a write-scoped token, both of which contradict `docs/standards/security.md:20`.
D7 records why a server-side Prefect Automation is the leading candidate to sidestep that.

**Trade-off, stated not hidden:** this plan no longer makes a killed backfill resume by itself. The
owner gets prevention (T1) plus visibility (T2); resumption stays manual until the follow-on lands.
D2 already contemplated exactly this ("the probe ships even if the owner declines auto-resume").

## Non-goals

Investigating the reboot (**already established** — § Root cause) · making arbitrary flows
resumable · a general job-orchestration redesign · Docker Desktop's update policy · the Nepal
stack · replacing Prefect's state model · publishing Prefect's port.

## Dependency graph

```json
{
  "tasks": [
    {"id": "T1", "depends_on": [], "parallel": true},
    {"id": "T2", "depends_on": [], "parallel": true}
  ]
}
```

T1 is a host + doc change; T2 is a code change. They share no file and may run concurrently.

## Exit gates

### T1
- On the mini: `defaults read /Library/Preferences/com.apple.SoftwareUpdate.plist` shows
  `AutomaticallyInstallMacOSUpdates = 0`, `CriticalUpdateInstall = 0`, `AutomaticDownload = 1`.
- `grep -n "Install macOS updates" docs/deployment/mac-mini-staging.md` → no match.
- `docs/deployment/mac-mini-staging.md` records the per-key rationale and the maintenance-window
  procedure.

### T2 — focused
```bash
uv run pytest tests/unit/ops/test_watchdog.py -q
uv run pytest tests/unit/api/test_api_health.py -q
uv run pytest tests/integration/adapters/test_prefect_status.py -q
uv run ruff format --check src/sapphire_flow/ops/watchdog.py src/sapphire_flow/api src/sapphire_flow/adapters/prefect_status.py
uv run ruff check src/sapphire_flow/ops/watchdog.py src/sapphire_flow/api src/sapphire_flow/adapters/prefect_status.py
```
Required new tests (all with an injected clock — no `sleep`, no real `datetime.now`):
- run under threshold → no alert;
- run past threshold → exactly one alert, and still exactly one after four more ticks;
- watchdog restarted from the persisted state file → still no second alert;
- two runs past threshold → two alerts, independent;
- Slack post fails → pending is persisted and retried on the next tick;
- run leaves RUNNING → latch entry cleared, no further alert;
- Prefect query fails (adapter raises `AdapterError`) → the route reports **unreadable** and the
  watchdog turns that into the probe-unreadable alert, **not** an empty healthy result;
- route-level, with a fake Prefect client injected (the shape
  `tests/integration/adapters/test_prefect_status.py:85` already uses): a RUNNING run is returned as
  a raw row with its `running_since`, and **no** health record is written (the
  `pipeline_health_store` fake is never called) — the live-compute contract of D5a;
- unknown or null deployment name → the default threshold applies watchdog-side (never exempt);
- a state file written without the new fields loads (backward compatibility,
  `tests/unit/ops/test_watchdog.py:2466`).

### T2 — full
```bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest tests/unit -q
```

### Documentation (part of T2's exit, per `docs/workflow.md` § Task Exit Gate)
- `docs/deployment/mac-mini-staging.md` — the watchdog's probe list (the
  `ch.hydrosolutions.sapphire-watchdog` paragraph, ~line 305) gains the stuck-flow-run check; the
  SSH-tunnel block (~line 420) still forwards 4200, which the staging overlay does **not** publish
  — reconcile that stale instruction while in the file.
- `docs/standards/cicd.md` § Host-level watchdog (~line 886) — the new probe.
- `docs/standards/security.md` — record D5's boundary: Prefect stays unpublished; the new
  `/api/v1/health/flow-runs` route is read-only, admin-GET only, and adds no write scope.
- `docs/spec/types-and-protocols.md` — **no change expected**: `PipelineCheckType.FLOW_RUN_HEALTH`
  stays declared and unwritten (D5a), and `/health/detail` keeps its pure store-read contract.
- `docs/touchpoint-maps.md` — the watchdog entry (~line 710) gains the new `WatchdogState` fields
  and the API dependency.
- `docs/standards/orchestration.md` — **no change expected** (no new flow, no new deployment, no
  scheduling change). If the implementer finds one, that is a signal the scope drifted.

## Overall exit

- The host's update policy is set and documented, with the owner's choice recorded per key.
- A flow run stuck in `RUNNING` past its deployment's threshold raises **exactly one** alert
  through the existing webhook, and a failure of the probe itself raises a distinct one.
- Nothing in this plan writes to Prefect, publishes a port, or widens a token's scope.
