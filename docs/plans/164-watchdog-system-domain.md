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

**⛔ R1 (BLOCKER) — AMENDED by round 2: the enable-before-bootstrap order in T2 step 5 REMOVES this specific
window.** The original hazard (kept for the reasoning, and because rollback must still handle a bootstrapped job): If
`launchctl bootstrap system` SUCCEEDS and `launchctl enable` then FAILS, rollback runs with the new job
**already loaded** and never boots it out. Reinstall path: the restore's own `bootstrap` then fails against the
already-loaded label, leaving the **new, disabled** daemon. First-migration path: it re-bootstraps the legacy GUI
agent **alongside** the loaded system daemon — **two watchdogs**, the precise thing D2 forbids.
**Required, as amended:** rollback removes the system job **only when a bootstrap actually succeeded and a later
step then failed**, verifies that removal, restores the **prior enabled/disabled override** (tri-state), and
re-registers the previous job. Enabling first means a *disabled-then-failed-bootstrap* can no longer strand a
loaded-but-disabled daemon.

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

**R7 — delete the backup only after real verification**, not after mere `bootstrap`/`enable`. **AMENDED by
round 2: NOT by `kickstart`** (see T3 — it kills the run it measures). Verification is the `--invocation-id`
nonce in a completion event, exit status 0, and `watchdog.deadman_ping_attempted pinged=True`.

## Tasks

> **Rewritten 2026-08-17 after round 2.** The previous T2/T3 text prescribed the **opposite** migration order to
> R12 and still mandated `kickstart`, which R13 forbids. That is the same stale-text-vs-new-decision
> contradiction that bit Plan 162 twice. **These tasks are now the single migration spec; R1 and R7 are amended
> in place below.** There is exactly ONE verification mechanism, not alternatives.

### T0 — classify the baseline BEFORE anything (round-2 blocker)

**The real host is neither "currently active" nor "fresh" — it is installed-but-SILENT** (the agent has emitted
nothing since ~03:54 on 2026-08-16). A migration written only for "stop the running one, start the new one" does
not describe the state we will actually meet. Classify and branch explicitly:

| Baseline | Meaning |
|---|---|
| **loaded-and-fresh** | agent loaded, recent successful tick — the textbook case |
| **loaded-but-unhealthy** | loaded but not ticking (**expected on this host**) |
| **plist-only / unloaded** | plist present, no registration |
| **conflicting domains** | present in *both* gui and system |
| **truly fresh** | nothing installed |

**Required:** either restore and prove a **fresh legacy run** before migrating normally, or enter an explicit
**degraded-recovery mode** whose rollback truthfully returns only to the *degraded* baseline — it must not claim
to restore a working watchdog that was never working.

### T1 — service-account resolution (unchanged; foundation for everything else)

Per R2. **Red-first:** with `HOME=/var/root` and the resolver returning the service home, assert the installer
touches the **service** home and leaves `/var/root/Library` untouched.

### T2 — the migration, in this exact order (supersedes the earlier T2; R12 amended)

1. Lock; resolve account + label (R14); classify baseline (T0).
2. Preflight every file, secret, directory, command and the **rendered** plist (R15) — fail before any mutation.
3. Capture both domains' loaded state and **enabled state as TRI-STATE** — explicitly disabled / explicitly
   enabled / no override — never conflating "missing GUI domain" with "enabled". Back up every plist **plus
   metadata**.
4. **Stage the new plist in an UNSCANNED temporary location, not `/Library/LaunchDaemons`** (round-2 blocker):
   staging into a scanned directory means a failure or SSH loss before bootout leaves an **unverified daemon
   that loads beside the GUI agent at the next reboot**. **Arm rollback BEFORE the first persistent mutation**
   and trap `EXIT`, `HUP`, `INT`, `TERM`.
5. **`launchctl enable system/<label>` FIRST** — confirmed correct macOS 26 semantics: enabling clears the
   persistent disabled override **without loading** the service, and a disabled service cannot bootstrap. This
   removes R1's bootstrap-then-enable window entirely. **Rollback must restore the prior override** if a later
   step fails.
6. **Quiesce the old agent before removing it** (round-2 major): disable its scheduling, wait **boundedly** for
   `state = not running`, *then* boot out — otherwise the bootout can kill an in-flight run **mid-state-write**,
   which is R13's hazard reintroduced.
7. Verify the old **service target** is absent — `launchctl print <domain>/<label>` fails **while the parent
   domain is separately proven reachable**. **Do not** claim "the domain is absent": the `system` domain always
   exists and `gui/<uid>` persists while the user is logged in. Never grep human-readable output.
8. **`launchctl bootstrap system`** — after removal, deliberately. **A brief GAP is safer than an OVERLAP**
   (confirmed): `RunAtLoad=true` + `StartInterval=300` mean an overlapping pair would race the **shared state
   file**.
9. Verify per T3.
10. **Delete the legacy plist file** (its *service* was already removed at step 6) and discard backups **only**
    after final one-domain verification (R7, amended: no kickstart).

**⚠️ The gap is real and must be bounded.** D2 (never two) and D3 (never zero) **cannot both hold** across a
cross-domain transition. Steps 6–9 have no watchdog running; normally seconds, but a failure can stretch it
through verification and rollback. It can miss a 300 s tick, a dead-man ping, and any transient outage that
begins and ends inside it. **Required:** an explicit outage deadline, and **D3 rescoped to terminal
postconditions for *handled* failures and signals** — power loss and `SIGKILL` cannot be made atomic without
durable recovery machinery, and the plan must stop implying otherwise.

### T3 — verification: ONE mechanism (R7 and R13 amended; no kickstart)

**`kickstart -k` is forbidden** — `RunAtLoad=true` means bootstrap *already* triggers an invocation, so
kickstarting kills the run being measured, possibly mid-state-write, and launchd throttling then delays the
replacement.

**A pre-bootstrap run counter does not work either** (round-2 major): it does not exist after deliberate service
removal and resets across re-bootstrap; a byte offset alone cannot identify a *completed* invocation.

**Required:** add **`--invocation-id`** to the watchdog CLI plus **structured start and completion events**, with
completion emitted **only after `run_once()` returns successfully**. Verify the nonce **exclusively** in
post-offset events, together with launchd's stopped state and exit status. Record the log **inode and size**, not
just an offset, and fail or re-base explicitly if either changes (rotation/truncation).

Acceptance: loaded in `system` with `UserName = <service account>`; absent from `gui/<uid>`; a completed
invocation carrying our nonce; exit status 0; and `watchdog.deadman_ping_attempted pinged=True`.

### T4 — test harness + a PRIVILEGED host gate (round-2 major)

The unprivileged fake harness (fakes for `dscl`, `id`, `launchctl`, `plutil`, injectable roots, **ordering
assertions**) is buildable and covers transaction logic — but it **cannot** validate real system-domain
authorization, `root:wheel`, `UserName`, TCC, or GUI-domain availability. A **privileged host smoke gate** is
therefore required, not optional.

**Access reality — decide before scheduling the work:** system-domain `launchctl` **can** run over SSH, but needs
an up-front root check, pre-authorised `sudo`/PTY, signal-safe execution and a reconnect plan. **Console or
Screen Sharing (or MDM) is required for TCC prompts and for the literal logout/login test** — so this cannot be
completed end-to-end over SSH alone.

### T5 — docs

`docs/deployment/mac-mini-staging.md` (conversion, rollback, and the degraded-baseline path),
`docs/standards/cicd.md`, `docs/touchpoint-maps.md`.

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

