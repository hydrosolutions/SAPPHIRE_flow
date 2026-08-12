---
status: DRAFT
created: 2026-08-12
plan: 158
title: Session-independent operational stack — the mac-mini must collect data without a GUI login
scope: Remove the operational stack's dependency on a macOS GUI login session, which caused a silent 14-day data outage (2026-07-29 → 2026-08-12). Three phases, increasing risk: (1) DETECTION — stage the missing Slack webhook, move the watchdog to a LaunchDaemon, add an off-box dead-man's-switch; (2) REBOOT RESILIENCE — auto-login + Docker-Desktop-at-login + stack-start job as a LaunchDaemon; (3) TRUE INDEPENDENCE — replace Docker Desktop with a headless, launchd-managed runtime, including the Postgres volume migration. Phase 1 is independently valuable and lands first. Backup-target relocation and the disk-capacity investigation are named follow-ons, not in scope.
depends_on: []
blocks: []
supersedes: []
---

# Plan 158 — Session-independent operational stack

## Status
**DRAFT.** Operational reliability (category **A**). Prompted by the outage diagnosed on 2026-08-12; the owner has
made this a priority so operational data collection is dependable.

## Problem

**The mac-mini collected no data for 14 days and nobody found out.** Observations, NWP and forecasts all stopped
mid-day **2026-07-29 ~11:05 UTC** and resumed only when the Docker engine was manually started on **2026-08-12
12:34:24 UTC**. The host never rebooted (51 days uptime).

**Root cause: every moving part is scoped to the `sapphire` GUI login session.** When that session ended, all four
layers went with it — and each one independently prevented recovery:

| Layer | Scope | What it did |
|---|---|---|
| Docker Desktop engine | GUI application | Died with the session. Cannot be started over SSH — `open -a Docker` fails with `OSLaunchdErrorDomain Code=125`, because macOS will not launch a GUI app into an Aqua session from the SSH launchd domain. |
| `ch.hydrosolutions.sapphire` (stack start) | **LaunchAgent** | Died with the session. Its script `scripts/launchd/start-sapphire.sh` only **waits** for `docker info` (240 s) — it has no ability to *start* the engine, so even had it survived it would have retried forever. |
| `ch.hydrosolutions.sapphire-watchdog` | **LaunchAgent** | Died with the session, so nothing detected the outage. Its log jumps straight from `2026-07-29T11:16Z` to `2026-08-12T12:39Z`. |
| Slack alerting | secret file | `secrets/slack_webhook_url` **does not exist**, so `read_slack_webhook()` returns `None` and every alert degrades to log-only. Its own log shows **`watchdog.slack_skipped_log_only` ×55**, including 48 `bafu_stale_alert` and one `health_failure_alert`. |

So the system detected failures, wrote them to a file nobody reads, and had no path back up. Containers carry
`unless-stopped` with `RestartCount=0` and came back **3 seconds** after the engine returned — the containers were
never the problem.

**Why this matters beyond one outage:** v1 targets Nepal DHM operational forecasting. A feed that silently stops for
two weeks and depends on someone being logged in is not an operational system.

## Goals and non-goals

**Goals**
1. **Detection** — an outage is known within minutes, off-box, without anyone looking at a log file.
2. **Recovery** — the stack returns by itself after a reboot, an engine crash, or a session ending.
3. **Independence** — no component's liveness depends on a GUI login session.

**Non-goals (named follow-ons, deliberately out of scope)**
- **Backup-target relocation.** `/Volumes/sapphire-backup` is **not a mount** — it is a plain folder on the internal
  disk (`mount | grep sapphire` is empty), so pg_dumps sit on the same physical disk as the database. Real data-safety
  issue, unrelated to session independence.
- **The disk-capacity question.** APFS reports 3.7 TB used on the Data volume while `du` as `sapphire` sees 288 GB;
  3,150 subtrees return "Operation not permitted" (TCC), so the remainder is unreadable over SSH. Needs Full Disk
  Access or a GUI-session `sudo du`.
- **Migrating off macOS entirely.** A Linux host removes this whole class of problem and is the honest long-term
  answer, but it is a different project.

## Design

Three phases, ordered by **value per unit of risk**. Phase 1 is independently valuable and must not be blocked on
Phase 3.

### Phase 1 — Detection (low risk, no downtime, highest value)

- **D1 — Stage `secrets/slack_webhook_url`.** ⚠️ OWNER: the file must be created on the mini; the code already reads
  it (`DEFAULT_SLACK_PATH`, `sapphire_flow/ops/watchdog.py:57`). Nothing else is needed to turn 55 suppressed alerts
  into delivered ones. **This single change converts "14 days blind" into "alerted on the first failed check."**
- **D2 — Move the watchdog from LaunchAgent to LaunchDaemon.** System scope survives logout. It probes
  `http://localhost:8000/api/v1/health`, so it needs no user session. A LaunchDaemon runs as root unless given
  `UserName`; set `UserName=sapphire` so it keeps reading `./secrets/*` and writing its existing log, and set
  `WorkingDirectory`, `PATH` (uv lives at `/Users/sapphire/.local/bin/uv`) and `HOME` explicitly, since daemons
  inherit almost no environment.
- **D3 — Off-box dead-man's-switch.** The watchdog alerts on *detected* failure but is silent when it dies itself,
  which is exactly what happened. Something external must alarm on **silence**: the watchdog pings a hosted
  dead-man's-switch each successful cycle, and that service alerts when pings stop. ⚠️ OWNER DECISION: which service
  (e.g. healthchecks.io) and where the ping URL is stored.

### Phase 2 — Reboot resilience (low risk, requires GUI access once)

- **D4 — Auto-login for `sapphire` + Docker Desktop at login.** FileVault is **off** (`fdesetup status`), so
  auto-login is possible. **Honest scoping: this fixes reboots, not what actually happened.** The July outage
  involved no reboot — the session ended while the machine stayed up — so D4 alone would not have prevented it. It is
  worth doing because a power cut is the *other* obvious failure mode, and it is cheap. ⚠️ Security trade-off for the
  owner: auto-login means physical access to the mini is a logged-in session; acceptable on a LAN-only staging box,
  and it should be recorded, not assumed.
- **D5 — Move `ch.hydrosolutions.sapphire` (and `-recap-probe`) to LaunchDaemons**, same treatment as D2. Note this
  only helps once the engine can start without a session, i.e. it is fully effective only with Phase 3; under Phase 2
  it still improves matters, because a daemon-scoped starter survives a logout and brings the stack up the moment an
  engine reappears.

### Phase 3 — True session independence (the real fix; highest risk)

- **D6 — Replace Docker Desktop with a headless, launchd-managed runtime.** The mini is macOS 26.5.1 / **arm64**, so
  a Linux VM is required either way; the question is only whether that VM is owned by a GUI app or by launchd.
  **Recommended: Colima** (Lima + `vz` backend on Apple Silicon), started by a LaunchDaemon with `KeepAlive`, which
  also gives automatic engine restart — something Docker Desktop cannot offer here. Colima is **not currently
  installed**.
- **D7 — The Postgres volume migration is the hard part, and the reason this is Phase 3.** The database lives in a
  Docker **named volume inside Docker Desktop's VM** (`docker system df`: 136.2 GB of local volumes). Changing runtime
  therefore moves the data. Preferred route is a logical `pg_dump` → restore into the Colima-hosted volume, with a
  verified row-count/consistency comparison and a documented rollback to Docker Desktop while its VM is still intact.
  ⚠️ OWNER DECISION: acceptable downtime window for the cutover.
- **D8 — Everything that names the socket must be re-pointed.** `DOCKER_HOST`, the deploy commands, the runbook, and
  `start-sapphire.sh`'s `docker info` wait all assume Docker Desktop's socket. Colima exposes its own; the macmini
  overlay and both `-f` flags stay as they are.

## Phases

- **T1 — Detection (D1–D3).** Stage the webhook; convert the watchdog plist to a LaunchDaemon with explicit
  `UserName`/`WorkingDirectory`/`PATH`/`HOME`; add the dead-man's-switch ping.
  **Acceptance:** with the stack deliberately stopped, a Slack message arrives within one watchdog interval (300 s);
  with the **watchdog itself** stopped, the external service alarms. Both must be demonstrated, not assumed — the
  second is the one that was missing.
- **T2 — Reboot resilience (D4–D5).** Enable auto-login, set Docker Desktop to start at login, convert the two
  remaining LaunchAgents to LaunchDaemons.
  **Acceptance:** a full reboot brings the stack back with no human interaction, verified by API health plus a fresh
  row appearing in `observations`.
- **T3 — Runtime migration (D6–D8).** Install Colima, stand it up under a LaunchDaemon, migrate the Postgres volume,
  re-point the socket, cut over, verify, and keep the Docker Desktop VM until verification passes.
  **Acceptance (the real test of this plan):** with **no GUI session logged in at all**, the stack runs, survives a
  reboot, and recovers from `colima stop`. This is the assertion the whole plan exists to satisfy.

## Phase dependency graph
```json
{
  "phases": [
    { "id": "T1", "tasks": ["T1"], "parallel": false },
    { "id": "T2", "tasks": ["T2"], "parallel": false },
    { "id": "T3", "tasks": ["T3"], "parallel": false, "depends_on": ["T1"] }
  ]
}
```
T1 and T2 are independent and may be done in either order; T3 depends on T1 so that the migration is observable —
attempting a runtime cutover while alerting is still blind repeats the original mistake.

## Open items
- **D9-runtime-choice — ⚠️ OWNER DECISION.** Colima under launchd (recommended: headless, `KeepAlive` gives engine
  auto-restart, no GUI dependency) vs staying on Docker Desktop with auto-login (cheaper, no data migration, but the
  liveness dependency remains and a logout still darkens the feed) vs moving the stack to a Linux host (removes the
  class of problem; largest change).
- **D10-downtime-window — ⚠️ OWNER DECISION.** Acceptable outage for the T3 Postgres migration.
- **D11-deadman-service — ⚠️ OWNER DECISION.** Which external service, and where its URL is stored.
- **D12-autologin-security — ⚠️ OWNER ACKNOWLEDGEMENT.** Auto-login makes physical access equal a logged-in session.
- **What ended the session on 2026-07-29 is unknown and probably unknowable** — `wtmp` was rotated, macOS unified-log
  retention has rolled past, and no crash report exists. This plan therefore removes the *dependency* rather than
  attempting to prevent one specific trigger. Recorded so nobody later assumes the trigger was diagnosed.
