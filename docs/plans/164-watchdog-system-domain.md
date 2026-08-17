---
status: DRAFT
created: 2026-08-17
plan: 164
title: Run the watchdog in the system domain, so it stops dying with the login session
scope: Convert the watchdog from a per-user LaunchAgent to a system-domain LaunchDaemon, transactionally, so that a logout, an OS update or an unattended reboot cannot silently stop monitoring. Rebuilt on current main rather than merged from the Plan 158 branch (72 commits behind), carrying forward that branch's three rounds of review findings as pre-folded requirements. Scope is the WATCHDOG ONLY — converting the stack-starter and docker-prune jobs is a named follow-on. The dead-man's switch (Plan 163) makes this failure visible; this plan is what prevents it.
depends_on: []
blocks: []
supersedes: []
---

# Plan 164 — Watchdog in the system domain

## Status
**DRAFT** (2026-08-17). Operational reliability (category **A**).

## Problem

The watchdog is installed as a **per-user LaunchAgent** (`install-launchd.sh:11`,
`AGENTS_DIR="${HOME}/Library/LaunchAgents"`), so it **dies with the login session**. It has now failed that way
twice:

- **29 July 2026** — the session ended, the watchdog died with it, and the outage ran **14 days** undetected.
- **~03:54, 16 August 2026** — alerts stopped mid-incident. Backups were broken at the time and could not have
  recovered (Plan 162 Phase A was merged but not deployed), so the alerts stopping meant **the monitor stopped**,
  not the problem resolving.

**Plan 163's dead-man's switch makes this visible within ~20 minutes. It does not prevent it.** Every OS update,
logout or session end repeats it, and this host takes updates automatically — macOS **26.6.1** is pending now.

### Honest caveat on causation

**The 03:54 cause is not yet confirmed.** Auto-login is enabled and reboot recovery was verified working on
2026-08-13, so a LaunchAgent *should* have reloaded. If the host-side diagnosis (Plan 163's step 1: last log
timestamp vs `uptime`) shows auto-login completed and the agent still died, **the cause is something else and
this plan is not the fix for that specific incident.** It remains correct regardless: a monitoring process that
depends on a GUI session being alive is the wrong shape, independent of which failure mode fires first. Do the
diagnosis before concluding.

### What actually exists on main

`install-launchd.sh` is **69 lines, GUI-domain only, with no system-domain support and no tests at all**
(`tests/unit/ops/` contains no `test_install_launchd.py`). **This is a greenfield feature on main, not a patch.**
The Plan 158 branch built 487 script lines + 878 test lines for it and had **three rounds of review**; that
branch is 72 commits behind main and is used here as a **design reference**, not merged. Its reviewed findings
are folded in below as requirements, so they are not rediscovered the expensive way.

## Goal

The watchdog runs as a **LaunchDaemon in the `system` domain**, surviving logout, reboot and OS update, with
**no duplicate** left in the GUI domain — and the installer that converts it is **transactional**, so a failed
conversion never leaves the host with zero watchdogs or two.

## Non-goals

- **Converting the stack-starter and docker-prune jobs** — named follow-on. Reboot recovery for the *stack* was
  verified working via auto-login + Login Items on 2026-08-13; the monitoring gap is the urgent one.
- **Removing auto-login** — it is Plan 158 D4's transitional shim and out of scope here.
- **Replacing Docker Desktop with a headless runtime** — Plan 159.

## Decisions

- **D1 — the watchdog runs as the service account, not root.** The plist sets `UserName`; the daemon must reach
  the same `secrets/` files and log path it does today.
- **D2 — one domain at a time, never both.** The conversion must remove the GUI agent *and* verify its removal.
  Two watchdogs racing one state file would double-alert and corrupt the notification state machine Plan 162/163
  built.
- **D3 — the installer is transactional.** A failure at any step must leave exactly one working watchdog —
  either the new daemon or the previous agent — never zero, never two.

## Requirements folded from the Plan 158 reviews (do not rediscover these)

**⛔ R1 (BLOCKER) — rollback cannot undo a bootstrapped-but-not-enabled daemon.** If
`launchctl bootstrap system` SUCCEEDS and `launchctl enable` then FAILS, rollback runs with the new job
**already loaded** and never boots it out. Reinstall path: the restore's own `bootstrap` then fails against the
already-loaded label, leaving the **new, disabled** daemon. First-migration path: it re-bootstraps the legacy GUI
agent **alongside** the loaded system daemon — **two watchdogs**, the precise thing D2 forbids.
**Required:** boot out and *verify removal of* any newly bootstrapped system job at the top of rollback, before
restoring anything; require the rollback's `enable` to succeed.

**⛔ R2 — resolve the SERVICE ACCOUNT explicitly; not `$HOME`, not `SUDO_USER`.** `$HOME` is root's under
`sudo`; `SUDO_USER` is whichever admin typed sudo, while the daemon runs as `sapphire`. Resolve one explicit
account via `/usr/bin/dscl /Search -read "/Users/$u" NFSHomeDirectory UniqueID`, validate the home is absolute
and the UID matches `id -u "$u"`, and **reject** lookup failure or mismatch. Not `eval echo ~user`. Must be
independent of `$HOME` so it survives `sudo -H`/`sudo -i`. **This applies to EVERY `$HOME`-derived path, not just
the agent directory** — the 158 build fixed `AGENTS_DIR` and left `${HOME}/Library/Logs` creating root-owned
logs.

**R3 — capture backups BEFORE booting out or overwriting anything.** The 158 build booted out the legacy agent
before backing up its plist, so a failing `mktemp`/copy left **no active watchdog** under `set -e`. Back up the
existing `/Library/LaunchDaemons` plist before it is overwritten *and* before the old registration is booted out.

**R4 — rollback must RE-REGISTER, not merely keep a file.** First migration restores **and bootstraps** the
legacy GUI agent; reinstall restores the daemon plist **with ownership and mode** and re-bootstraps the old
daemon. A backup file alone does not restore monitoring.

**R5 — create only missing directories, with service-account ownership**, without recursively re-owning existing
user directories.

**R6 — a `--label` selector**, so the watchdog can be converted alone; the current installer processes every
entry in `PLISTS`.

**R7 — delete the backup only after real verification**, not after mere `bootstrap`/`enable`: kickstart with a
**fresh invocation marker**, exit status 0, the expected tick log line, **and** (new, now that Plan 163 has
landed) `watchdog.deadman_ping_attempted pinged=True`.

## Tasks

### T1 — service-account resolution (foundation for everything else)

A resolver returning `(user, uid, home)` per R2, with rejection on lookup failure, non-absolute home, or UID
mismatch. Every `$HOME`-derived path in the installer goes through it.

**Red-first:** with `HOME=/var/root` and the resolver returning the service home, assert the installer touches
the **service** home and leaves `/var/root/Library` **untouched** — the 158 build's test collapsed the two, so it
passed against the bug.

### T2 — system-domain install, transactionally (R1, R3, R4, R6)

Install the watchdog plist to `/Library/LaunchDaemons`, `root:wheel`, `0644`; bootstrap into `system`; enable;
remove the GUI agent and **verify** it is gone. Ordering: back up → install → bootstrap → enable → verify → only
then remove the legacy agent → only then discard the backup.

**Red-first, and these are the cases the 158 build shipped unproven:**
(a) `bootstrap` succeeds, `enable` fails → rollback leaves **exactly one** working watchdog and **no** loaded
new job; (b) same for the reinstall path (a pre-existing daemon plist is restored with ownership/mode and
re-bootstrapped); (c) a failure while backing up leaves the existing watchdog **running**.
A **stateful `launchctl` stub** is required — a stub that always succeeds cannot exercise any of this.

### T3 — verification that proves the daemon actually works (R7)

`launchctl print system/<label>` loaded with `UserName = <service account>`; **not** present in
`gui/<uid>`; kickstart against a **fresh invocation marker** (so a stale prior exit status cannot satisfy the
gate) then assert `last exit status = 0`; the tick log advances; and the dead-man ping fires.

### T4 — docs

`docs/deployment/mac-mini-staging.md` (the conversion + rollback procedure), `docs/standards/cicd.md`,
`docs/touchpoint-maps.md`.

## Exit gates

`shellcheck` + `bash -n` on the installer; `plutil -lint` on the plist; the new unit suite; full
`uv run pytest` **with an isolated `PREFECT_HOME`** (see [[project_prefect_home_test_contention]]); ruff; pyright
ratchet. **Every red-first case above proven RED against current main.**

## Host acceptance (requires mini access)

Convert, then **log out and back in** — the watchdog must still be running and pinging. Then reboot and confirm
the same. Confirm `gui/<uid>` has no duplicate. Rollback rehearsal: force a failure and confirm exactly one
watchdog survives.

## ✅ The working-directory question — RESOLVED by review (was the open question)

**My framing was half wrong.** The relative secret paths are **not** the danger: the plist already sets an
absolute `WorkingDirectory` (`ch.hydrosolutions.sapphire-watchdog.plist:37`), so a verbatim conversion leaves
`--probe-token-path` and `--deadman-url-path` resolving **correctly**. They become silently wrong only if that
key is ever removed or changed.

**The actual hard failures are two different paths:**

**⛔ R8 (BLOCKER) — the state file is `HOME`-derived** (`watchdog.py:85`, `Path.home()`). A daemon whose `HOME`
becomes `/var/root` **cannot persist there** — a hard failure — and a daemon with a *different* `HOME` starts
from a **fresh state file**, silently discarding the notification hysteresis that Plans 162 and 163 spent three
review rounds building (pending/recovery-pending, alert-once). Losing it means re-alerting and a broken
recovery path. **Required:** pass an absolute `--state-path` of the existing
`/Users/<service>/.sapphire-watchdog-state.json`; verify it is readable/writable **as the service account** and
that its parent exists; and **test that migration AND rollback keep using the same file.**

**⛔ R9 (BLOCKER) — the installer provisions `${HOME}/Library/Logs`** (`install-launchd.sh:23`), which under
`sudo` creates **root's** log directory. The plist's log paths are absolute (`:39`) and therefore fine — but only
**if the service account's log directory exists**. If it does not, the daemon **fails to launch outright**.
**Required:** provision the absolute service-account log directory, owned by the service account (per R5,
create-only-if-missing, no recursive re-owning).

**Decision — do BOTH, not either:** keep `WorkingDirectory` (it is what makes `uv run` resolve the project) **and**
pass absolute, service-account-resolved `--slack-path`, `--probe-token-path`, `--deadman-url-path` and
`--state-path`. Never rely on the daemon's `HOME`.

**⚠️ R10 — the startup assertion must be SELECTIVE, and this is a trap.** My instinct was "fail loudly on any
unresolvable configured secret". That would **break Plan 163 D3**: `--deadman-url-path` is always configured in
the plist but is *legitimately absent* in dev/CI, where feature-off is the documented, tested behaviour. Assert
only on paths declared **required**; absent-by-design must stay silent. Note the assertion is also **not
sufficient** on its own — it cannot catch a bad state, log or executable path.

## Further requirements from the same review

**⛔ R11 (BLOCKER) — `UserName=<service account>` is necessary but does NOT prove access.** Same UID normally
grants the same POSIX access, but it still fails if a secret is root-owned, a parent directory is not
traversable, or the state/log target is not writable — and **the service UID does not inherit GUI-process TCC
grants**, so anything under a privacy-protected location can be denied even with the correct UID. (We already
lost a `du` to a mode-700 directory this week; same class.) **Required:** *before stopping the old job*, run
access checks **as the service account** for the executable, the repository, all three secrets, the state file
and its parent, and the log directory — and make a real system-domain read/write/ping check part of host
acceptance.

**R12 — the transactional sequence, explicitly ordered** (supersedes the looser ordering in T2):
lock the installer → resolve account + selected label → preflight every file, secret, directory, command and the
rendered plist → capture **both** domains' loaded/enabled state and back up every existing plist **plus
metadata** → stage the new daemon plist **without loading it** → boot out the currently active watchdog and
**verify its domain is absent** → `enable system/<label>` **before** bootstrap, then bootstrap → verify a fresh
completed invocation, tick, and successful dead-man attempt → remove the legacy agent **and verify removal** →
delete backups **only** after the final one-domain verification.
On any failure after the bootout step, rollback must first boot out and **verify removal of** any new system
job, restore persistent enable state and plist metadata, re-bootstrap the previously active job, and **verify a
fresh successful run**. A fresh host with no previous job is a **distinct baseline**, not a degenerate case.

**R13 — verification must not kill the run it is measuring.** `RunAtLoad` is `true`, so bootstrap **already
triggers an invocation**; an immediate `kickstart -k` can **kill that run mid-flight**, possibly while it is
persisting state, and launchd throttling then delays the replacement. **Required:** record a pre-bootstrap run
counter and log byte offset (or add an explicit invocation identifier — the CLI has none today), poll with a
**bounded timeout** for a *strictly newer completed* run, then check its exit status and only post-marker log
events.

**R14 — selector semantics before side effects.** The installer currently processes all three plists
unconditionally, and under `sudo` its `$HOME`/`id -u` logic targets **root**. Parse and validate `--label`
**before any filesystem or launchctl mutation**; require the exact watchdog label for this conversion path;
guarantee non-selected jobs receive **no** copies, bootouts, bootstraps, directory changes or warnings; and
either reject an unqualified "install all" for this migration or implement explicit per-label domain dispatch.

**R15 — fresh-host ordering.** The installer today deliberately allows installation before the probe token
exists, which conflicts with strict daemon startup. Document and test the sequence: service account + repository
→ all production secrets with correct ownership/mode → state and log locations → *then* bootstrap. Missing
prerequisites must fail **before** any launchctl mutation.

**R16 — scheduling and logs.** Assert in plist tests that `StartInterval=300`, `RunAtLoad=true` and `KeepAlive`
is **absent/false** (adding `KeepAlive` to a one-shot program would cause rapid-restart behaviour). Define
expected **boot-time degradation**: the daemon starts before Docker and the network are ready, so its first
ticks legitimately fail — that must not be read as an outage. The shared stdout/stderr log is currently
**unbounded**; add or document a rotation policy with retention and ownership.

**Minors:** `watchdog.sh` sets the repo directory and passes only the probe-token path, leaving Slack and
dead-man implicit — once absolute daemon arguments exist, either mirror them or document the wrapper as
non-production. The installer header says "two LaunchAgents" while listing three. If the plist becomes rendered
from resolved paths, `plutil` must validate the **rendered** artifact, not just the checked-in source.

### Test harness (R2/R12/R13 depend on it, and main has NO test file for this installer)

Name the file and build an **unprivileged temporary-root harness** with fakes for `dscl`, `id`, `launchctl`,
`plutil` and failure injection. **Assert command ORDERING, not only final state.** Cover: first migration,
system reinstall, fresh host, bootstrap/enable/verification failure, legacy bootout/removal failure, backup
failure, rollback restoration **and metadata**, missing/unreadable secrets, wrong `HOME`, invalid selector,
concurrent invocation, and proof that **non-selected plists are untouched**. The R1 test must make a duplicate
bootstrap **fail** and verify the new system job is booted out *before* old-job restoration.

