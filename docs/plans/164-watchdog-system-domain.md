---
status: DRAFT
created: 2026-08-17
plan: 164
title: Run the watchdog as a LaunchDaemon so it stops dying with the login session
scope: A one-time, console-operated migration of the watchdog from a per-user LaunchAgent to a system LaunchDaemon, plus a small fresh-host-only guard in the installer. Deliberately NOT a transactional migration engine — that design was cut after a proportionality review (see "What was cut"). Converting the stack-starter and prune jobs, removing auto-login, and the headless runtime (Plan 159) are out of scope.
depends_on: []
blocks: []
supersedes: []
---

# Plan 164 — Watchdog as a LaunchDaemon

## Status
**DRAFT** (2026-08-17). Operational reliability (category **A**). **Rewritten after a third review round whose
mandate was to REMOVE rather than add** — it cut 12 of 16 requirements and 5 of 6 tasks. This is the small
version. The larger design is in git history; do not re-inflate without a reason.

## Problem

The watchdog is a per-user **LaunchAgent** (`install-launchd.sh:11`), so it **dies with the login session**. It
has failed that way twice: **29 July** (undetected for 14 days) and **~03:54 on 16 August** (alerts stopped
mid-incident — and since backups could not self-repair, alerts stopping meant the *monitor* stopped). Plan 163's
dead-man switch makes this visible within ~20 minutes; it does not prevent it.

**Caveat:** the 03:54 *cause* is unconfirmed. Auto-login is on and reboot recovery worked on 13 Aug, so an agent
*should* have reloaded. Diagnose first (last log timestamp vs `uptime`). If auto-login completed and it still
died, this plan is not the fix for that incident — it remains the right shape, because a monitor that depends on
a GUI session is wrong regardless.

## Decision — manual migration, not an automated one

**The transactional installer is cut.** It turned a one-time, console-observed operation into a cross-domain
state machine with backups, tri-state overrides, signal traps, nonces and a fake harness — and *still* could not
be atomic across power loss or `SIGKILL`. **A checked manual runbook produces fewer bugs here:** a human at a
console sees what happened and reacts; a script must anticipate everything.

Two properties replace all that machinery:
- **Keep the legacy agent plist until acceptance passes** — rollback is then one command, not a restore engine.
- **The permanent installer becomes fresh-host-only**, refusing migration, reinstall or conflicting state.

## The three load-bearing requirements (NOT cut)

- **R8 — pass an absolute `--state-path`.** The state file is `HOME`-derived (`watchdog.py:85`). A daemon with a
  different or unset `HOME` starts from a **fresh state file**, silently discarding the notification hysteresis
  Plans 162/163 built — re-alerting, broken recovery. Point it at the existing
  `/Users/sapphire/.sapphire-watchdog-state.json`.
- **R9 — the service account's log directory must exist and be writable.** The installer provisions
  `${HOME}/Library/Logs`, i.e. **root's** under `sudo`. The plist's log paths are absolute and fine — but if
  `/Users/sapphire/Library/Logs` is missing, **launchd may not start the job at all**.
- **R11 — `UserName=sapphire` must actually grant access.** Same UID normally means same POSIX access, but it
  fails if a secret is root-owned or a parent is not traversable, and the service UID **does not inherit
  GUI-process TCC grants**. Test access *as* `sapphire` **before** removing the old job.

Already true and also load-bearing: `/Library/LaunchDaemons` with `root:wheel` `0644`, and the existing absolute
`WorkingDirectory` — which is precisely why the relative secret paths resolve correctly today.

## T1 — the migration runbook (console)

1. Add `--state-path /Users/sapphire/.sapphire-watchdog-state.json` to the plist. Preserve `UserName`,
   `WorkingDirectory`, `RunAtLoad`, `StartInterval` and the current log paths.
2. `plutil -lint`; test access **as `sapphire`** to the executable, repo, required secrets, state file, log dir.
3. Create `/Users/sapphire/Library/Logs` **only if missing**; verify ownership and writability.
4. **Keep** the legacy LaunchAgent plist; disable its GUI label so it cannot reload.
5. Install the daemon plist to `/Library/LaunchDaemons`, `root:wheel`, `0644`.
6. Wait until the old job is **not running**, then boot it out — do not kill it mid-state-write.
7. `launchctl enable`, then `bootstrap system/ch.hydrosolutions.sapphire-watchdog`. **Do not `kickstart`** —
   `RunAtLoad=true` already triggers a run, and kickstarting would kill the run being measured.
8. From a log tail started **after** GUI removal, observe `deadman_ping_attempted pinged=true` and exit 0.
9. Verify the **system** label present and the **GUI** label absent. Then test **logout** and **reboot**.
10. Delete the legacy plist **only after** acceptance passes.

**Rollback (one sequence):** disable/bootout the system label, move its plist aside, enable/bootstrap the
retained GUI plist.

## T2 — fresh-host-only installer guard (Repo; small)

The installer keeps doing fresh-host provisioning and **refuses everything else**: if the label already exists in
either domain, or a destination plist is present, it must **fail loudly rather than migrate**. Validate
prerequisites — secrets, directories, resolved account via `id` (not a generic `dscl` subsystem) — **before** any
`launchctl` mutation.

**Test:** a fresh install succeeds; an install against an existing or conflicting registration **refuses and
mutates nothing**.

## Acceptance

**Log out and back in — the watchdog must still be running and pinging. Then reboot and confirm the same.**
That is the whole point; everything else is means.

## Access constraint

System-domain `launchctl` works over SSH as root, but **TCC prompts and the logout/login test need console or
Screen Sharing**. This cannot be completed end-to-end over SSH — unlike the Plan 162/163 deployments, which can.

## What was cut, and why (do not re-add without a reason)

Cut: the transactional state machine, automated rollback re-registration, backup capture/restore, tri-state
enable capture, baseline classification, staging in an unscanned directory, signal traps, the `--label` selector,
generic `dscl` account resolution, generic startup assertions (they would break Plan 163 D3, where an absent
dead-man URL is *legitimate*), `--invocation-id` and its structured events, and the fake privileged-path harness.

**Every one existed only because we had chosen to automate a one-time migration** — answers to "what if the
script dies halfway?", a question that does not arise when a person runs ten commands and reads the output. The
risk they added (~900 lines of new installer and harness) exceeded the risk they removed.

**Kept deliberately: the verification step.** Not for elegance — "it looked like it worked" produced two of this
week's three incidents. But for a one-time manual conversion that is a human reading the log for `pinged=true`,
not a nonce protocol.
