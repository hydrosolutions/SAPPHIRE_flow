---
status: READY
created: 2026-08-12
plan: 158
title: Session-independent operational stack — the mac-mini must collect data without a GUI login
scope: Make the mac-mini's data collection OBSERVABLE, and reboot-survivable IF the optional T6 is taken: stage the missing Slack webhook, add an off-box dead-man's-switch, and move the watchdog, stack-starter and prune jobs from LaunchAgents into the system domain, plus a transitional auto-login. Removing the GUI dependency ITSELF (replacing Docker Desktop with a headless launchd-supervised runtime, and the volume/database migration that entails) is Plan 159, which depends on this one. Backup-target relocation and the disk-capacity investigation are named follow-ons.
depends_on: []
blocks: []
supersedes: []
---

# Plan 158 — Session-independent operational stack

## Status
**DRAFT.** **Split on 2026-08-12:** the runtime migration that was Phase 3 is now **Plan 159**, after a `/plan`
review produced 3 blockers and 12 majors against it — the objections were sound and it is a project, not a phase.
158 keeps the low-risk, high-value half: it makes an outage *visible within minutes* and survivable across reboots,
and it lands the system-domain conversion that 159 hard-depends on. **158 does not remove the GUI dependency** — with
Docker Desktop still the engine, a logout still darkens the feed; you would simply know immediately.

**DRAFT.** Operational reliability (category **A**). Prompted by the outage diagnosed on 2026-08-12; the owner has
made this a priority so operational data collection is dependable.

## Build scope — READ BEFORE IMPLEMENTING
**Automated implementation covers the `(Repo, …)` tasks ONLY: T1 and T2.** Every `(Host, …)` task — T3, T4, T5, T6 —
runs commands against the live mac-mini (staging secrets, bootstrapping launchd domains, enabling auto-login,
deliberately exercising failure paths) and is performed by a human in a session at the machine, following the runbook
each task delivers. Do not attempt them from an automated build.

**Open owner decisions do NOT block T1/T2.** D11 (which dead-man service), D12 (auto-login trade-off) and D13 (recap
probe) gate the *host* tasks only: the dead-man ping is implemented **service-agnostically** (POST to a URL read from
a file), so the code lands before the provider is chosen, and the auto-login and probe questions belong to T6/T4.

**READY as of 2026-08-13** — D1 is delivered and verified in production; T1/T2 are specified and independently
reviewed (one `/plan` round plus three Codex passes, two of them opposed-lens).

## Progress
- **2026-08-13 — D1 DONE.** Slack alerting is live: webhook staged on the mini, delivery verified into `#sap3-alerts`
  through the watchdog's own `read_slack_webhook`/`default_slack_poster` path, stack untouched. The 55 previously
  suppressed alerts (48 BAFU-staleness, 1 health-failure) would now be delivered.
  **This does not yet close the outage class:** the watchdog is still a **LaunchAgent**, so a session end kills the
  alerter along with the engine — it cannot report the outage it dies in. That is D2/T3 (LaunchDaemon) plus D3 (the
  off-box dead-man's-switch), and it is why silence must not be read as health until both land.

## Problem

**The mac-mini collected no data for 14 days and nobody found out.** Observations, NWP and forecasts all stopped
mid-day **2026-07-29 ~11:05 UTC** and resumed only when the Docker engine was manually started on **2026-08-12
12:34:24 UTC**. The host never rebooted (51 days uptime).

**Root cause: every moving part is scoped to the `sapphire` GUI login session.** When that session ended, all five
layers went with it — and each one independently prevented recovery:

| Layer | Scope | What it did |
|---|---|---|
| Docker Desktop engine | GUI application | Died with the session. Cannot be started over SSH — `open -a Docker` fails with `OSLaunchdErrorDomain Code=125`, because macOS will not launch a GUI app into an Aqua session from the SSH launchd domain. |
| `ch.hydrosolutions.sapphire` (stack start) | **LaunchAgent** (`scripts/launchd/install-launchd.sh:15`, bootstrapped into `gui/$(id -u)` at `:64`) | Died with the session. Its script `scripts/launchd/start-sapphire.sh:16-23` only **waits** for `docker info` (240 s) — it has no ability to *start* the engine, so even had it survived it would have retried forever. |
| `ch.hydrosolutions.sapphire-watchdog` | **LaunchAgent** (`scripts/launchd/install-launchd.sh:16`) | Died with the session, so nothing detected the outage. Its log jumps straight from `2026-07-29T11:16Z` to `2026-08-12T12:39Z`. |
| `ch.hydrosolutions.sapphire-docker-prune` | **LaunchAgent** (`scripts/launchd/install-launchd.sh:17`; weekly Sunday 04:00, `docs/standards/cicd.md:632`) | **Also died with the session** — same failure mode, previously unlisted. A weekly `docker system prune` that has not run since the session ended is a plausible contributor to the disk-capacity discrepancy below (see Non-goals). |
| `ch.hydrosolutions.sapphire-recap-probe` | **LaunchAgent**, installed by hand (`docs/operations/recap-probe-runbook.md:87-92`) — *not* in the installer's `PLISTS` array | Died with the session. Zero operational impact: the runbook states it is "an **exploratory experiment**, not a pipeline component… it writes an append-only JSONL log and touches **nothing** in the DB or the forecast path" (`docs/operations/recap-probe-runbook.md:16-18`). Listed for completeness, not because it needs fixing. |
| Slack alerting | secret file | `secrets/slack_webhook_url` **does not exist**, so `read_slack_webhook()` (`src/sapphire_flow/ops/watchdog.py:275`) returns `None` and every alert degrades to log-only. Its own log shows **`watchdog.slack_skipped_log_only` ×55**, including 48 `bafu_stale_alert` and one `health_failure_alert`. |

So the system detected failures, wrote them to a file nobody reads, and had no path back up. Containers carry
`unless-stopped` with `RestartCount=0` and came back **3 seconds** after the engine returned — the containers were
never the problem.

**Why this matters beyond one outage:** v1 targets Nepal DHM operational forecasting. A feed that silently stops for
two weeks and depends on someone being logged in is not an operational system.

## Goals and non-goals

**Goals**
1. **Detection** — an outage is known within minutes, off-box, without anyone looking at a log file.
2. **Recovery — conditional, and the condition is T6** *(reviewer major-fix)*. Nothing in 158 starts the Docker
   Desktop **engine**; only T6 (auto-login + Docker-at-login) does. **If T6 is declined, 158 delivers detection and
   host-job survival only — not stack recovery**, because the system-domain starter will wait 240 s for an engine
   that never appears. Recovery from a session ending or an engine crash requires the headless runtime and is
   **Plan 159** regardless.
3. **Host-job independence** — the watchdog, stack starter and prune jobs survive a logout (system domain).
   Full engine independence is **Plan 159**; 158 is its prerequisite.

**Non-goals (named follow-ons, deliberately out of scope)**
- **Backup-target relocation.** `/Volumes/sapphire-backup` is **not a mount** — it is a plain folder on the internal
  disk (`mount | grep sapphire` is empty), so the `pg_dump` output written by `dump_database_task`
  (`src/sapphire_flow/flows/backup.py:49-58`) sits on the same physical disk as the database. Real data-safety
  issue, unrelated to session independence.
- **The full disk-capacity investigation.** APFS reports 3.7 TB used on the Data volume while `du` as `sapphire`
  sees 288 GB; 3,150 subtrees return "Operation not permitted" (TCC), so the remainder is unreadable over SSH. Needs
  Full Disk Access or a GUI-session `sudo du`. **Partially reopened by this plan, in two bounded ways:**
  (a) the dead prune LaunchAgent (inventory row 4) is a plausible contributor and T5 checks whether its silence
  correlates with the growth; (b) Plan 159 T1 **requires** a `df -h` free-space read on the Data volume before a Colima disk
  can be sized — a number we cannot currently trust. Neither reopens the full TCC-blocked audit.
- **Replacing the container runtime** — Plan 159 (headless launchd-supervised engine + volume/DB migration).
- **Migrating off macOS entirely.** A Linux host removes this whole class of problem and is the honest long-term
  answer, but it is a different project.

## Design

Two phases, ordered by **value per unit of risk**. Phase 1 is independently valuable and must not be blocked on
Plan 159. Phase 2 is *not* optional decoration: its LaunchDaemon conversions are a **hard prerequisite** of Plan 159's
acceptance criterion (see the dependency graph).

### Phase 1 — Detection (low risk, no downtime, highest value)

- **D1 — Stage `secrets/slack_webhook_url`. ✅ DONE 2026-08-13.** The file is installed on the mini
  (`~/SAPPHIRE_flow/secrets/slack_webhook_url`, `-rw-------`, owner `sapphire`, created under `umask 077` so it was
  never briefly world-readable), pointing at the HSOL workspace channel **`#sap3-alerts`**. **Verified by delivery,
  not by inspection:** `read_slack_webhook(DEFAULT_SLACK_PATH)` → `True` and `default_slack_poster(...)` → `True`,
  with the message confirmed visible in the channel — i.e. the exact two functions the watchdog calls, exercised
  end-to-end **without stopping the stack** (the repo docs suggest killing the API to test; unnecessary here).
  *Residual:* the webhook URL passed through a chat transcript during setup and **should be rotated** — regenerate in
  Slack and re-write the file via `ssh … 'umask 077; cat > …'` (stdin, so it never enters shell history or `argv`).
  *Original context:* ⚠️ OWNER: the file must be created on the mini; the code already reads
  it (`DEFAULT_SLACK_PATH`, `src/sapphire_flow/ops/watchdog.py:58`), and `read_slack_webhook()`
  (`src/sapphire_flow/ops/watchdog.py:275-285`) returns `None` for a missing/empty/unreadable file, which is exactly
  the degradation observed. Nothing else is needed to turn 55 suppressed alerts into delivered ones.
  **This single change converts "14 days blind" into "alerted on the first failed check."**
- **D2 — Move the watchdog from LaunchAgent to LaunchDaemon.** System scope survives logout. It probes
  `http://localhost:8000/api/v1/health`, so it needs no user session. A LaunchDaemon runs as root unless given
  `UserName`; the plist already sets `UserName=sapphire` and `WorkingDirectory=/Users/sapphire/SAPPHIRE_flow`
  (`scripts/launchd/ch.hydrosolutions.sapphire-watchdog.plist:29-30,37-38`), which is what keeps `./secrets/*`
  relative paths resolving. In the system domain we must **additionally** set `PATH` (uv lives at
  `/Users/sapphire/.local/bin/uv`) and `HOME=/Users/sapphire` explicitly via `EnvironmentVariables`, since daemons
  inherit almost no environment and `uv run --no-sync` needs both.
- **D2b — The installer cannot install a LaunchDaemon today.** `scripts/launchd/install-launchd.sh` hardcodes
  `AGENTS_DIR="${HOME}/Library/LaunchAgents"` (`:11`) and operates exclusively on `gui/${UID_VAL}` (`:60,63-64`);
  `scripts/bootstrap-mac-mini.sh` uninstall likewise boots out only `gui/${UID_VAL}` labels (`:101-105`) and its
  step 11 shells straight into the installer (`:312-314`). D2/D5 are therefore **not plist-content edits** — the
  installer, bootstrap, uninstall path, and verification output all need a domain concept. Design: a per-plist
  `DOMAIN` (agent|daemon) table in the installer; daemon entries copy to `/Library/LaunchDaemons`, `chown root:wheel`,
  `chmod 644`, and `launchctl bootstrap system` — behind an explicit privileged operator step (the installer must
  refuse, not silently `sudo`). Before a system bootstrap, **boot out any stale `gui/<uid>/<label>`** so one label
  never exists in two domains. Verification prints `launchctl print system/<label>`.
- **D3 — Off-box dead-man's-switch.** The watchdog alerts on *detected* failure but is silent when it dies itself,
  which is exactly what happened. Something external must alarm on **silence**. Semantics, decided here so the test
  is writable:
  - **A ping means "the watchdog process completed a tick", NOT "the stack is healthy."** An unhealthy stack still
    pings — Slack is the channel for *detected* failure; the dead-man is the channel for *undetected* failure. If a
    ping meant "healthy", a genuine outage would fire both channels and the dead-man would lose its only distinct
    signal.
  - **Placement:** the ping fires at the end of `run_once()` (`src/sapphire_flow/ops/watchdog.py:442`), after
    `state.dump(config.state_path)` (`:645`), so a ping also implies state was persisted. An exception anywhere in
    the tick propagates to `main()`'s `except Exception` (`:730-733`), which returns 2 **without** pinging — an
    internal watchdog failure therefore correctly reads as silence.
  - **Config:** `--deadman-url-path` (default `./secrets/deadman_url`), read with the same
    missing/empty/unreadable → `None` contract as `read_slack_webhook()`; file `chmod 600`. Absent secret ⇒ feature
    off, logged once per tick as `watchdog.deadman_skipped_no_url` (mirrors `watchdog.slack_skipped_log_only`).
  - **Failure policy:** ping timeout **5 s**; any failure is logged (`watchdog.deadman_ping_failed`) and is
    **non-fatal** — a dead-man outage must never take down the watchdog it monitors.
  - **Client injection:** the poster is injected like `slack_poster` so tests use a fake, not HTTP.
  ⚠️ OWNER DECISION D11: which hosted service (e.g. healthchecks.io) and the grace period (recommend 15 min = 3×
  the 300 s `StartInterval`).

### Phase 2 — Session-independent host jobs (low risk; requires GUI access once)

- **D4 — Auto-login for `sapphire` + Docker Desktop at login — explicitly TRANSITIONAL.** FileVault is **off**
  (`fdesetup status`), so auto-login is possible. **Honest scoping: this fixes reboots, not what actually
  happened.** The July outage involved no reboot — the session ended while the machine stayed up — so D4 alone would
  not have prevented it. It is worth doing because a power cut is the *other* obvious failure mode, and it is cheap.
  ⚠️ Security trade-off (D12): auto-login means physical access to the mini is a logged-in session; acceptable on a
  LAN-only staging box, and it should be recorded, not assumed. **Because it is a compatibility shim for the
  Docker-Desktop era, Plan 159 T5 removes it once the headless runtime is proven** — the trade-off is time-boxed, not permanent.
- **D5 — Move `ch.hydrosolutions.sapphire` AND `ch.hydrosolutions.sapphire-docker-prune` to LaunchDaemons**, same
  treatment as D2, using the D2b domain support. Both are installed by the same installer into the same GUI-scoped
  domain (`scripts/launchd/install-launchd.sh:15,17`) and both died the same death.
  - *Known interim behaviour, accepted:* under Phase 2 the daemon-scoped starter runs at boot **before** any Aqua
    session exists, so `start-sapphire.sh` will burn its 240 s `docker info` wait and exit non-zero
    (`scripts/launchd/start-sapphire.sh:17-21`); launchd retries on `ThrottleInterval=60`. Once auto-login brings
    Docker Desktop up, the next retry succeeds. Noisy but self-correcting, and it goes away when Plan 159 lands.
  - *The prune job also unblocks a diagnosis:* T5 must record whether prune's last successful run predates
    2026-07-29 and whether reclaimable image/build-cache figures are large, since that is the cheapest available
    test of the disk-capacity hypothesis.
  - **`ch.hydrosolutions.sapphire-recap-probe` is deliberately NOT in D5.** It is out-of-band research tooling
    (`docs/operations/recap-probe-runbook.md:16-18`), it is not in the installer's `PLISTS` array, and converting
    its launchd domain buys it nothing before Plan 159 anyway — it `docker exec`s into a running container, so it
    stays dead as long as the engine is GUI-owned. ⚠️ OWNER DECISION D13: **retire it** (time-boxed probe, window
    likely elapsed) or **adopt it** into the installer as a normal job. Do not silently fold a research script into
    a permanent installer.
- **D8 — One Docker runtime-endpoint contract, applied everywhere.** Today the launchd jobs disagree, and two of
  them are pinned to Docker-Desktop-specific paths that will **not** resolve to a Homebrew-installed Colima CLI on
  this arm64 host:
  - `scripts/launchd/run-recap-probe.sh:32` — `DOCKER="${DOCKER_CMD:-/usr/local/bin/docker}"` with the comment
    "Docker Desktop symlinks its CLI there"; `:36` — `export DOCKER_HOST=unix:///var/run/docker.sock`, which is
    **not** Colima's socket (`$HOME/.colima/default/docker.sock`).
  - `scripts/launchd/prune-docker.sh:30` — `export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH}"`,
    also front-loading the Docker Desktop install prefix.
  - `scripts/launchd/start-sapphire.sh:16` — bare `docker info`, resolved from launchd's minimal `PATH`.
  - `scripts/bootstrap-mac-mini.sh:143-148` and `docs/deployment/mac-mini-staging.md:216-245` still document
    Desktop-specific behaviour (per-user `~/.docker/run/docker.sock`, the `desktop-linux` context, "Two agents,
    user-context").
  **Contract:** a single sourced snippet (or an `EnvironmentVariables` block in each plist) that sets `DOCKER_BIN`
  and `DOCKER_HOST` from one place, with `DOCKER_CMD` preserved as the existing test-injection seam
  (`tests/unit/ops/test_launchd_prune_docker.py`, `tests/unit/ops/test_recap_probe_wrapper.py`). **Pragmatic
  alternative, allowed:** symlink Colima's `docker` into `/usr/local/bin` and set only `DOCKER_HOST`, touching
  fewer scripts — record which route was taken. Either way the sweep must also cover: the manual deploy/rollback
  commands in `docs/deployment/mac-mini-staging.md`, `docs/operations/recap-probe-runbook.md`, the CI shellcheck
  list (`.github/workflows/ci.yml:29` — note it currently omits `prune-docker.sh`; add it if that file changes), and
  the infra/ops touchpoint map (`docs/touchpoint-maps.md:547`). The macmini overlay and both `-f` flags stay as they
  are.

## Phases

> **Where the commands live** *(owner decision, 2026-08-12, after two reviews disagreed on depth)*. One reviewer
> asked for exact commands for every host step; another judged parts of this plan already over-specified for a plan.
> Both are right about different things, so the split is explicit: **this document owns decisions, invariants and
> acceptance criteria; each HOST task's deliverable is a runbook in `docs/operations/`** carrying the exact commands
> in order — the same shape as `docs/standards/plan-147-mini-rollout.md`, which exists precisely because host
> operations need commands where they will be executed and maintained, not buried in a design plan. A host task is
> not complete until its runbook exists and has been followed once.
>
> | Task | Runbook deliverable |
> |---|---|
> | T3 (secrets + watchdog daemon + dead-man) | `docs/operations/watchdog-daemon-runbook.md` |
> | T5 (starter + prune daemons) | `docs/operations/launchd-system-domain-runbook.md` |
> | T6 (auto-login + Docker-at-login) | folded into the T5 runbook; **includes the rollback steps**, which the plan text does not carry |

Each task states in/out scope, artefacts, and the **exact** commands that gate it. "Repo" tasks are code/doc changes
reviewable in a diff; "Host" tasks are operator cutovers whose evidence is captured text, not a diff.

### Phase A — Detection

> **Acceptance note for scheduled jobs** *(reviewer major-fix)*: the watchdog and prune jobs are **scheduled
> one-shots**, not long-lived services. A healthy installation shows them **loaded but not running** between
> invocations, so any gate asserting `state = running` would fail on a correct system. Verify instead: the service
> exists in the `system` domain, has the expected program/user/environment, reports a **successful last exit status
> after an explicit `launchctl kickstart`**, and produces its expected log line and dead-man ping.

- **T1 — Watchdog dead-man ping (Repo, D3).**
  *In:* `src/sapphire_flow/ops/watchdog.py` (new `read_deadman_url()`, injected `deadman_poster`, `--deadman-url-path`
  arg, ping call at the end of `run_once` after `state.dump`), `scripts/launchd/watchdog.sh` +
  `scripts/launchd/ch.hydrosolutions.sapphire-watchdog.plist` (pass the flag explicitly, mirroring the existing
  `--probe-token-path` convention), `tests/unit/ops/test_watchdog.py`, `docs/standards/cicd.md`,
  `docs/deployment/mac-mini-staging.md`. *Out:* any change to Slack behaviour or hysteresis.
  **Red-first tests** (each must fail against today's code): healthy stack ⇒ ping fired; **unhealthy** stack ⇒ ping
  **still** fired; exception raised inside the tick ⇒ **no** ping and `main()` returns 2; ping timeout ⇒ logged,
  `run_once` still returns normally; missing/empty secret ⇒ no ping, `watchdog.deadman_skipped_no_url` logged, no
  exception.
  **Verify:** `uv run pytest tests/unit/ops/test_watchdog.py -q` · `uv run ruff check src tests` ·
  `uv run ruff format --check src tests` · `uv run pyright` (ratchet).
  *Rollback trigger:* any existing watchdog test regresses.

- **T2 — launchd installer learns the system domain (Repo, D2b).**
  *In:* `scripts/launchd/install-launchd.sh` (per-plist `DOMAIN`, `/Library/LaunchDaemons` target, `chown root:wheel`,
  stale-`gui/<uid>` bootout before `bootstrap system`, refuse-don't-sudo, `--dry-run` printing exactly what it would
  do), `scripts/bootstrap-mac-mini.sh` (uninstall must boot out **both** domains and cover all installed labels, not
  the two hardcoded at `:101`; step 11 at `:312` must surface the privileged step),
  `scripts/launchd/ch.hydrosolutions.sapphire-watchdog.plist` (`EnvironmentVariables` with `HOME` + `PATH`),
  `docs/deployment/mac-mini-staging.md` §LaunchAgents (retitle; "Two agents, user-context" at `:230` is wrong on both
  counts), `docs/standards/cicd.md`, `docs/touchpoint-maps.md:547`. *Out:* the stack-starter and prune plists (T4).
  **Verify:** `shellcheck scripts/launchd/install-launchd.sh scripts/bootstrap-mac-mini.sh` ·
  `plutil -lint scripts/launchd/*.plist` · `./scripts/launchd/install-launchd.sh --dry-run` on a dev mac prints the
  daemon path and the privileged step and **writes nothing** · `uv run pytest tests/unit/ops -q`.
  *Rollback trigger:* dry-run shows a label targeted in two domains.

- **T3 — Host cutover: secrets + watchdog as a LaunchDaemon + dead-man wired (Host, D1/D2/D3).**
  **`secrets/slack_webhook_url` is already staged and delivery-verified (D1, 2026-08-13) — this task's remaining
  secret is `secrets/deadman_url`.** Create it (and any re-issued webhook) **atomically under `umask 077`** — not created-then-
  `chmod`, which leaves a readable window *(reviewer major-fix)*. **Ownership exception, stated explicitly:**
  `docs/standards/security.md:213` requires `./secrets/` be root-owned 600, but these two are read by the
  **watchdog host process running as `UserName=sapphire`**, so root:600 would make them unreadable. They are
  therefore `sapphire:600` and the standard needs a documented carve-out for host-process secrets (as distinct from
  container-mounted ones) — record it rather than silently contradicting the standard.
  Register the dead-man check (D11) with a 15-min grace; install **the watchdog label only**, which requires T2's
  label selector (`--label`) — the current installer processes every entry in `PLISTS`
  (`scripts/launchd/install-launchd.sh:14`), so "for the watchdog only" is not expressible without it
  *(reviewer major-fix)*.
  **Verify (host):** `launchctl print system/ch.hydrosolutions.sapphire-watchdog` shows the service
  **loaded** with `UserName = sapphire` · then `sudo launchctl kickstart -k system/ch.hydrosolutions.sapphire-watchdog`,
  poll `launchctl print` until the job is no longer running (bounded timeout, and compare against the
  invocation marker captured before the kickstart so a **stale** prior exit status cannot satisfy the gate,
  reviewer major-fix), then assert **`last exit status = 0`** — a scheduled one-shot is loaded-not-running
  between invocations, so the exit status must be produced by an explicit kickstart, not read from whatever ran last · `launchctl print gui/$(id -u)/ch.hydrosolutions.sapphire-watchdog` returns **not
  found** (no duplicate) · the ping lands in the dead-man dashboard within one interval.
  **Acceptance (both must be *demonstrated*):** (a) with the stack deliberately stopped, a Slack message arrives
  within one watchdog interval (300 s); (b) with the **watchdog itself** booted out, the external service alarms
  within the grace period — **and the operator SSHes out and closes the session for the duration of (b)**, which is
  the specific thing that was missing.
  **Restore before the task is complete** *(reviewer major-fix — the first draft deliberately caused an outage and
  never ended it)*: re-bootstrap the watchdog, restart the stack, and **verify ingestion has resumed** (a fresh
  `observations` row past a baseline captured before the test, within an explicit timeout) before T3 is signed off.
  *Evidence:* Slack screenshot, dead-man alert, both `launchctl print` outputs, and the post-restore fresh-row query.
  *Rollback:* boot out `system/…`, re-bootstrap the GUI agent from the previous plist.

### Phase B — Session-independent host jobs

- **T4 — Docker endpoint contract in code (Repo, D8).**
  *In:* `scripts/launchd/run-recap-probe.sh:32,36`, `scripts/launchd/prune-docker.sh:30`,
  `scripts/launchd/start-sapphire.sh:1-23` (comment + wait text), the three plists,
  `scripts/bootstrap-mac-mini.sh:143-148`, `docs/deployment/mac-mini-staging.md:216-245`,
  `docs/operations/recap-probe-runbook.md`, `.github/workflows/ci.yml:29` (add `prune-docker.sh` if it changed),
  `docs/touchpoint-maps.md:547`. *Out:* choosing the runtime (that is Plan 159) — this task makes the endpoint a single
  configurable thing and must leave Docker Desktop working unchanged.
  **Red-first test:** extend `tests/unit/ops/test_launchd_prune_docker.py` / `test_recap_probe_wrapper.py` so a run
  with the contract variable pointed at a fake Colima-style path resolves there (fails today, since `/usr/local/bin`
  and `unix:///var/run/docker.sock` are hardcoded).
  **Verify:** `shellcheck` over the full CI list · `uv run pytest tests/unit/ops -q` ·
  `bash -n scripts/launchd/*.sh`.

- **T5 — Host cutover: stack-starter + prune as LaunchDaemons (Host, D5).**
  Install both via the T2 installer. **Before** converting prune, capture `docker system df` and prune's last
  successful log line, and record whether it predates 2026-07-29 (the disk-capacity check the Non-goals section
  points at).
  **Verify (host):** `launchctl print system/ch.hydrosolutions.sapphire` and
  `…system/ch.hydrosolutions.sapphire-docker-prune` are both **loaded**; neither label present in `gui/$(id -u)`;
  then **kickstart each explicitly and assert `last exit status = 0` afterwards** —
  `sudo launchctl kickstart -k system/ch.hydrosolutions.sapphire` brings the stack up, and
  `sudo launchctl kickstart -k system/ch.hydrosolutions.sapphire-docker-prune` produces its **"stack is running"
  and terminal "done" log lines** — **exit status 0 is NOT sufficient evidence** *(reviewer major-fix)*:
  `prune-docker.sh` deliberately exits 0 when the daemon is unreachable ("Guard-command errors default to SKIP",
  `scripts/launchd/prune-docker.sh:38`), so an exit-0 gate would certify the exact endpoint failure this task exists
  to prevent; the API health check asserts **parsed JSON `status == "ok"`**, not merely a 2xx from `curl -fsS`.
  *Known accepted noise:* pre-auto-login boots will log one 240 s `docker info` timeout per retry (D5).
  *Rollback:* boot out system labels, re-bootstrap GUI agents.

- **T6 — Host: transitional auto-login + Docker Desktop at login (Host, D4). OPTIONAL/TIME-BOXED.**
  Requires physical/GUI access once. **Acceptance:** a full reboot brings the stack back with no human interaction,
  verified by `/api/v1/health` **plus a fresh row appearing in `observations`** (health alone can be green with a
  dead feed — that is the July failure mode). *This task is explicitly scheduled for removal by Plan 159 T5.*

## Phase dependency graph
```json
{
  "phases": [
    { "id": "A1", "tasks": ["T1", "T2"], "parallel": true },
    { "id": "A2", "tasks": ["T3"], "parallel": false, "depends_on": ["A1"] },
    { "id": "B1", "tasks": ["T4"], "parallel": false, "depends_on": ["A1"] },
    { "id": "B2", "tasks": ["T5"], "parallel": false, "depends_on": ["A2", "B1"] },
    { "id": "B3", "tasks": ["T6"], "parallel": false, "depends_on": ["B2"] }
  ]
}
```
**Why these edges:**
- **T5 (starter into the system domain) is the hard prerequisite Plan 159 consumes.** "No GUI session at all" is
  undemonstrable while `ch.hydrosolutions.sapphire` lives in `gui/$(id -u)`
  (`scripts/launchd/install-launchd.sh:65`) — the starter that runs `docker compose up -d` will not launch without
  a session regardless of the engine layer.
- **T3 gates everything downstream:** the migration must be observable. Attempting a runtime cutover (Plan 159) while
  alerting is still blind repeats the original mistake.
- **T6 is a leaf, not a prerequisite.** Auto-login is a transitional convenience; nothing depends on it and Plan 159
  T5 removes it. If the owner declines the D12 trade-off, T6 drops without touching the critical path.

## Added after review — free-space alert (cheap win)
- **D14-free-space-alert** *(reviewer: missing cheap win)*. The Data volume sits at **95%**, both plans already read
  `df`, and nothing warns before exhaustion stops PostgreSQL or the VM. Weekly pruning is not a safety net — it
  skips silently when the daemon is unreachable (above). Add a **free-bytes threshold check to the watchdog tick**,
  alerting on the same Slack path as every other check. It costs almost nothing, needs no privileged access, and
  does **not** depend on resolving the TCC-blocked 3.4 TB attribution question. Folded into T1's scope.

## Open items
- *(Runtime-choice, downtime window, host-RAM go/no-go and rollback boundary moved to **Plan 159**.)*


- **D11-deadman-service — ⚠️ OWNER DECISION.** Which external service, and the grace period (recommend 15 min).
- **D12-autologin-security — ⚠️ OWNER ACKNOWLEDGEMENT.** Auto-login makes physical access equal a logged-in session.
  Time-boxed: Plan 159 T5 removes it. If T6 is skipped, this item lapses.
- **D13-recap-probe — ⚠️ OWNER DECISION.** Retire the time-boxed recap probe, or adopt it into the installer as a
  permanent job. Deliberately excluded from D5 either way.

- **What ended the session on 2026-07-29 is unknown and probably unknowable** — `wtmp` was rotated, macOS unified-log
  retention has rolled past, and no crash report exists. This plan therefore removes the *dependency* rather than
  attempting to prevent one specific trigger. Recorded so nobody later assumes the trigger was diagnosed.

## Notes on review findings not adopted as written
- **"Add the recap probe to D5's LaunchDaemon conversion."** Not adopted as an automatic conversion. Two reviewers
  disagreed (one asked to convert it, one asked to drop it); the plan takes the narrower reading — it is documented
  research tooling with zero pipeline coupling (`docs/operations/recap-probe-runbook.md:16-18`), and a domain change
  buys it nothing before Plan 159 because it `docker exec`s into a container. It is surfaced as an explicit owner
  decision (D13) rather than silently converted or silently dropped.
- **Trade-off accepted, recorded:** converting the stack starter to a LaunchDaemon (T5) *before* the engine is
  headless (Plan 159 T1) produces throttled 240 s `docker info` timeouts on every pre-auto-login boot. This is deliberate —
  the ordering is forced by T5 being a prerequisite of Plan 159 T5 — and the noise disappears at Plan 159's cutover.
