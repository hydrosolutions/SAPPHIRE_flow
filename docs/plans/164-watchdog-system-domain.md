---
status: DEPRIORITISED
created: 2026-08-17
plan: 164
title: Run the watchdog as a LaunchDaemon so it stops dying with the login session
scope: A one-time, console-operated migration of the watchdog from a per-user LaunchAgent to a system LaunchDaemon, plus a small fresh-host-only guard in the installer. Deliberately NOT a transactional migration engine — that design was cut after a proportionality review (see "What was cut"). Converting the stack-starter and prune jobs, removing auto-login, and the headless runtime (Plan 159) are out of scope.
depends_on: []
blocks: []
supersedes: []
---

# Plan 164 — Watchdog as a LaunchDaemon

## ⬇️ DEPRIORITISED 2026-08-18 — the premise was falsified on the host

**The watchdog never died.** Diagnosed at the machine on 2026-08-18: uptime **3 d 20 h** (boot 08-14 14:05, the
macOS 26.6.1 update), agent **loaded**, log **current**. It ran continuously straight through the incident.

**The real cause of the 03:54 silence was the 0-byte backup masking**, now fixed and deployed (Plan 162 Phase A
atomic publication + the monitor's size predicate): the nightly backup failed, wrote an empty `.dump`, and that
fresh-looking artifact cleared its own staleness alarm — for **four consecutive nights** (15th–18th).

This plan's own caveat called it: *"If auto-login completed and the agent still died, the cause is something
else and this plan is not the fix for that specific incident."* That is exactly what happened.

**Still worth doing eventually** — a monitor that depends on a GUI session is the wrong shape, and 29 July was
real. But it now hardens against a failure mode that has not recurred, needs **console** access (TCC prompts +
a real logout test), and is the only remaining item that could leave the host worse than it started.
**T2 (the installer migration guard) is separate and still worth finishing** — see its escalated findings.

## 🔄 REVISIT 2026-08-20 — new evidence, conclusion UNCHANGED for T1

A launchd audit on 2026-08-20 produced evidence bearing directly on the deprioritisation above. It does
**not** reopen T1. Recorded here so the next reader does not have to re-derive it.

**What is new:**

1. **`ch.hydrosolutions.sapphire` — the stack-starter — has NEVER worked**, since its only commit
   `514ff36` (2026-04-23). launchd's default `PATH=/usr/bin:/bin:/usr/sbin:/sbin` cannot resolve
   `/usr/local/bin/docker`, so `docker info` returns **127**, the 240 s wait always expires, the script
   exits 1, and `KeepAlive` restarts it every ~250 s. Measured: 15,217 log lines, one distinct string,
   zero compose output in 58 days. Fix is a two-line PR, not this plan.
2. **The session-death failure mode is confirmed from a SECOND, independent source.** Log-line arithmetic
   on `sapphire-flow.log` (constant 249.8 s cadence, so line count is a clock) recovers a **14.21-day
   dormancy** — matching the 2026-07-29 → 08-12 window in the post-mortem (14.06 d) to within 3.5 hours.
   That agent really did stop with the GUI session. This corroborates 29 July; it shows **no recurrence
   after 08-12**.
3. **Nothing monitors launchd agent health.** There is no `launchctl` anywhere in `watchdog.py`. The
   documented verification in `docs/deployment/mac-mini-staging.md:314-316` expects
   `ch.hydrosolutions.sapphire  0  -`; the live value is `1`. A 119-day-dead agent sat behind a
   green-looking status line and was found only by hand.

**Why T1 stays deprioritised.** Finding 2 confirms what this plan already accepted — 29 July was real —
and adds no *recurrence*. The watchdog itself is alive (verified 2026-08-20: `runs = 646`, last exit 0,
healthchecks.io dead-man ping returning 200). T1 still needs **console** access, still cannot be done over
SSH, and is still the only item that could leave the host worse than it started. Nothing here changes that
balance. **T2 remains separate and still worth finishing.**

**What the evidence DOES demand is not in this plan.** Finding 3 is a different subject — the watchdog
checking *other* agents, not the watchdog's own domain — and unlike T1 it is repo-only, needs no console,
and closes the gap that actually went unnoticed for 119 days. Burying it in a console-gated, deprioritised
plan would mean it never ships. It is drafted separately as **Plan 195**.

## Status
**READY** (2026-08-17). Operational reliability (category **A**). Four review rounds: two adversarial, one
proportionality pass that **cut 12 of 16 requirements and 5 of 6 tasks**, and a final targeted pass that returned
**one** correction (step 7's `bootstrap` argument form) and confirmed the rest executable as written.
**Rewritten after the round whose mandate was to REMOVE rather than add** — it cut 12 of 16 requirements and 5 of 6 tasks. This is the small
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
7. Enable, then bootstrap. **`bootstrap` takes a DOMAIN target plus the plist PATH — not a service target**
   (verified against `launchctl` usage on macOS 26.6.1, the build pending on this host):
   ```sh
   sudo launchctl enable system/ch.hydrosolutions.sapphire-watchdog
   sudo launchctl bootstrap system /Library/LaunchDaemons/ch.hydrosolutions.sapphire-watchdog.plist
   ```
   **Do not `kickstart`** — `RunAtLoad=true` already triggers a run, and kickstarting would kill the run being
   measured.
8. From a log tail started **after** GUI removal, observe `deadman_ping_attempted pinged=true` and exit 0.
9. Verify the **system** label present and the **GUI** label absent. Then test **logout** and **reboot**.
10. Delete the legacy plist **only after** acceptance passes.

**Rollback (one sequence):** disable/bootout the system label, move its plist aside, enable/bootstrap the
retained GUI plist.

## T2 — refuse cross-domain MIGRATION, not re-install (Repo; small)

**⚠️ Corrected 2026-08-18, before build.** The earlier wording — *"refuse if the label already exists in either
domain, or a destination plist is present"* — is **too broad and would break the pending deploy.** The
installer's documented contract is idempotent re-install (`install-launchd.sh:2-4`: *"Safe to re-run … bootout +
bootstrap to apply any plist changes"*), and the runbook's own "force a reload" procedure is bootout followed by
re-running it (`docs/deployment/mac-mini-staging.md:487-490`). **Plan 163 changed the watchdog plist** (14
insertions: `--deadman-url-path`, `RunAtLoad`), so re-running this installer is precisely how that change reaches
the mini. A refuse-if-exists guard would make deploying the dead-man switch impossible by the documented path.

**What we actually want to prevent is a silent CROSS-DOMAIN migration** — the GUI↔system move, which belongs to
the T1 console runbook where a human watches it. Same-domain re-install stays supported and unchanged.

**The guard:**
- **Refuse** when the label is registered in the **other** domain, or when a plist for it exists in the other
  domain's directory — i.e. a GUI-domain install while `/Library/LaunchDaemons/<label>.plist` exists or
  `system/<label>` is loaded, and vice versa. Message must name the conflict and point at the T1 runbook.
- **Allow** same-domain re-install exactly as today (bootout + bootstrap to pick up plist edits).
- **Preflight every conflict check BEFORE the first mutation** — no copying, no `launchctl`, no `mkdir` until all
  checks pass. "Refuses and mutates nothing" must hold literally, not by aborting partway.
- Resolve the account with `id`, not a generic `dscl` subsystem.

**Test:** (a) fresh install succeeds; (b) same-domain re-install succeeds and applies an edited plist;
(c) a GUI install with the label loaded in `system` **refuses and mutates nothing** — assert no file was copied
and no `launchctl` subcommand ran; (d) the reverse direction likewise.

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
