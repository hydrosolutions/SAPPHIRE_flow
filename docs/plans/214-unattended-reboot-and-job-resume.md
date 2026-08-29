---
status: DRAFT
created: 2026-08-29
plan: 214
title: Stop unattended macOS updates killing long jobs, and make a killed job visible and resumable
scope: One macOS update-policy change on the staging host, one watchdog probe for stuck flow runs, and one resume mechanism for long idempotent jobs. No change to any flow's business logic.
depends_on: []
blocks: []
source: 2026-08-29 incident — macOS 26.6.2 auto-installed and rebooted the mini at 01:51 CEST, killing a 12-hour backfill; Prefect reported it RUNNING for the next 8 hours
---

# Plan 214 — unattended reboots kill long jobs silently

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ Proportionality is a binding constraint on this plan AND on its review

The cause is **already known** (§ Root cause) — this plan does **not** contain an investigation
task, and adding one is a finding against the reviewer, not the plan. Three small changes: a host
setting, a watchdog probe, a resume path.

**Rules binding every reviewer:**

1. **"No findings" is a complete and welcome review.**
2. **A finding must name a CONCRETE FAILURE** with `file:line`.
3. **Do not propose new apparatus** — no new daemon, no new service, no new dashboard. The
   watchdog (`ops/watchdog.py`) and its Slack webhook already exist; use them.
4. **Do not propose making flows generally resumable.** The backfill already is (§ D3).
5. **Adding length is a cost.**

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

## What already works, and must not be "fixed"

- **Containers recover on their own.** Every service carries `restart: unless-stopped` and all
  eight were back within three minutes of boot. The container layer is not the problem.
- **The backfill is idempotent and resumable.** `run_backfill`
  (`services/reanalysis_backfill.py:283`) calls `forcing_store.fetch_covered_days` per
  (year, station-batch) and skips the fetch entirely when the days are already stored
  (`chunks_skipped`). Re-running costs a DB query per chunk, not a re-download.

## What is broken

**A killed flow run stays `RUNNING` in Prefect forever, and nothing restarts it.**

- The 2026-08-28 backfill was reported `StateType.RUNNING` for **8 hours** after its process died.
  It had to be marked `CRASHED` by hand.
- **This is not rare and not only caused by reboots.** It happened *twice* on 2026-08-29 — once
  from the macOS reboot, once when an operator redeployed (`compose up --build` recreates
  containers under a running process). Any deploy during a long job reproduces it.
- The existing watchdog probes health, BAFU freshness, forecast freshness, disk, backup target and
  launchd agents (`ops/watchdog.py`) — **it has no check for a stuck flow run**, so the failure is
  invisible until someone looks.

Cost of the incident: ~12 h of backfill wall-clock, and a day where the fleet looked onboarded and
was not.

## Decisions

- **D1 — Prevent first. Turn off automatic *macOS* updates on the staging host; keep automatic
  *security data* updates.** These are separate settings. `AutomaticallyInstallMacOSUpdates`
  governs OS updates, which reboot; XProtect/MRT config-data updates do not reboot and must stay
  on. **This is an owner decision, not an implementer one** — it trades unattended patching for
  job continuity, and the host is on the office LAN. Recommended: OS updates become
  download-and-notify, applied during a maintenance window.

- **D2 — Detect before resume.** A stuck run that nobody sees is worse than one that does not
  restart. The watchdog probe (T2) ships even if the owner declines T3.

- **D3 — Resume only what is provably idempotent, and only by re-triggering — never by resuming
  in place.** Prefect cannot reattach to a dead process. The backfill's `fetch_covered_days` skip
  makes a fresh run cheap and safe. **Auto-resume must be opt-in per deployment**, not a global
  behaviour: re-triggering a non-idempotent flow could double-write.

## Tasks

### T1 — set the macOS update policy on the staging host
*In:* the host setting, plus one paragraph in `docs/deployment/mac-mini-staging.md` recording what
was chosen, why, and how to apply updates deliberately.
*Out:* anything about the Nepal host; anything about Docker Desktop's own auto-update (separate,
and it does not reboot the host).
*Exit:* `defaults read /Library/Preferences/com.apple.SoftwareUpdate.plist` shows the chosen
values, and the doc records the maintenance-window procedure.

### T2 — watchdog probe: a flow run stuck in RUNNING
*In:* one probe in `ops/watchdog.py` beside the existing ones, reusing the established
`probe_*` shape and the existing Slack webhook. It flags any flow run in `RUNNING` whose last state
change is older than a per-deployment threshold. Unit tests in the existing watchdog test module.
*Out:* a new service, a dashboard, a new alert channel, or auto-remediation (that is T3).
*Exit:* tests green including a fake clock; a run stuck past the threshold produces exactly one
alert; a healthy long run produces none. **The threshold must exceed the longest legitimate run** —
the backfill's own chunks take ~7 min but the whole flow runs ~12 h, so the threshold is on
*time since last state change*, not total duration.

### T3 — opt-in auto-resume for the backfill
*In:* mark the killed run `CRASHED` and re-trigger it once, for deployments explicitly flagged
resumable. Cap the retries so a genuinely broken flow cannot loop.
*Out:* making other flows resumable; changing `run_backfill`; any change to flow business logic.
*Exit:* a killed backfill is re-triggered automatically and its skip logic means it resumes rather
than restarts; a flow not flagged resumable is only alerted on, never re-triggered.

## Non-goals

Investigating the reboot (**already established** — § Root cause) · making arbitrary flows
resumable · a general job-orchestration redesign · Docker Desktop's update policy · the Nepal
stack · replacing Prefect's state model.

## Exit gates

- The host's update policy is set and documented, with the owner's choice recorded.
- A stuck `RUNNING` run raises exactly one alert through the existing webhook.
- A killed backfill resumes without a human, and provably skips the years already stored.
