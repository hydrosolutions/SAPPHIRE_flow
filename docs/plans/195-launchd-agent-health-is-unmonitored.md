---
status: DRAFT
created: 2026-08-20
plan: 195
title: A launchd agent that cannot run must not look healthy
scope: The watchdog asserts the last exit status of each installer-managed launchd agent and raises one distinct, per-label, transition-latched condition when an agent is failing or absent. No new service, no new config file, no domain migration, no change to any agent's own logic.
depends_on: [194]
blocks: []
source: launchd audit 2026-08-20; see docs/plans/164-watchdog-system-domain.md § REVISIT 2026-08-20
---

# Plan 195 — a launchd agent that cannot run must not look healthy

## Status

**DRAFT.** Not for implementation until the owner confirms.

**Revised 2026-08-21 after an independent Codex review** (NEEDS_CHANGES: 1 blocker, 4 majors, 1 minor;
every finding verified against the repo before folding). What changed: the latch is **per label**, not
one boolean; the probe moved from `launchctl print` to `launchctl list`; D2 gained a coherent
source and "absent = failing"; D4 gained a timeout contract and made `UNKNOWN` observable; the Plan 194
seam name was corrected — the one I cited did not exist. Details inline.

## ⛔ PROPORTIONALITY IS A BINDING CONSTRAINT ON THIS PLAN AND ON ITS REVIEW

**This is one probe, one condition, and their tests.** The predicate is a single `launchctl` call.
The watchdog already runs every 300 s, already keeps notification state, and already has a
transition-latched alerting shape to copy. It needs no new service, no new config file, no registry,
no abstraction over launchd, and no change to any agent's own behaviour.

**Rules for reviewers** (carried from Plan 194, which they served well):

1. **"No findings" is a complete and welcome review.** Do not manufacture findings to justify the pass.
2. **A finding must name a CONCRETE FAILURE** — a condition that would go undetected, an alert that
   would fire wrongly, a call site that would break.
3. **A missing specification is NOT a finding** unless leaving it unsaid produces wrong behaviour.
4. **Do not propose new apparatus.** No generalisation to "any launchd job on the host", no plugin
   seam, no config-driven agent registry.
5. **Adding length is a cost.** Prefer deleting to adding.

## Why this matters — measured, not hypothetical

Verified on the mac mini 2026-08-20 (host-side measurements; not reproducible from this repo):

- `ch.hydrosolutions.sapphire` (the stack-starter, the documented reboot-recovery path) reports
  `last exit code = 1` and `runs = 768`. It has **never** succeeded — since its only commit
  `514ff36`, 2026-04-23, i.e. 119 days. Under launchd's default
  `PATH=/usr/bin:/bin:/usr/sbin:/sbin`, `docker info` returns **127**; the 240 s wait always expires.
- `~/Library/Logs/sapphire-flow.log`: **15,217 lines, one distinct string**, zero compose output in
  58 days.

Verifiable from this repo:

- `grep -c launchctl src/sapphire_flow/ops/watchdog.py` → **0**. Nothing checks agent health.
- `docs/deployment/mac-mini-staging.md:314-316` documents the expected output as
  `ch.hydrosolutions.sapphire  0  -`. The live value is `1`. The documented check would have caught
  this on any day in 119; it was evidently never run against real output.

**The point is not that this agent is broken** — that is a two-line PR, out of scope here. The point
is that it was broken for 119 days *behind a green-looking status line*, and the monitor whose job is
to notice could not have. A recovery mechanism that cannot work is worse than none, because it looks
present.

## Decisions

- **D1 — The probe is `launchctl list`, parsed as its documented three columns.**
  *(Revised: the draft specified `launchctl print`. That was backwards.)* `launchctl(1)` documents
  `list`'s output — *"The first column displays the PID of the job if it is running. The second column
  displays the last exit status of the job"* (`/usr/share/man/man1/launchctl.1:567-573`) — while
  saying of `print`: *"This output is **NOT** API in any sense at all. Do **NOT** rely on the structure
  or information emitted for **ANY** reason."* (`:269-276`). Apple recommends `print` as the
  interactive subcommand; only `list` has a documented format to parse.

  Verdicts, per label:
  - **FAILING** — second column is non-zero. A negative value is the negation of the stopping signal
    (`-15` = SIGTERM), and is failing too.
  - **ABSENT → FAILING** — the label does not appear at all. An agent that was booted out is the
    loudest version of this defect, not a reason for silence. (This is why `list` beats `print` twice:
    under `print`, an unloaded label has no exit code at all and would degrade to UNKNOWN.)
  - **OK** — second column is `0`. This *conflates never-run with succeeded*, and that is
    deliberate: a periodic agent that has not yet fired is normal, and only non-zero is evidence of
    failure. It also removes the draft's never-run special case entirely.
  - **UNKNOWN** — `launchctl` missing, timed out, or output that does not parse. See D4: this is
    **reported**, never silently swallowed.

  **The alert says "last run failed", not "is failing now."** A `KeepAlive` agent can be running while
  its second column still shows the previous invocation's non-zero status — true of the stack-starter
  today (`state = running`, `last exit code = 1`). That prior failure is real and unresolved, which is
  worth an alert; claiming the current invocation has failed would not be true.

- **D2 — Which agents: the installer-managed labels, minus the watchdog itself.**
  *(Revised: the draft named the `PLISTS` array as its source of truth and then listed two agents that
  are not in it — the same invisibility this plan exists to end, reproduced inside the plan.)*
  The set is `ch.hydrosolutions.sapphire` and `ch.hydrosolutions.sapphire-docker-prune`
  (`scripts/launchd/install-launchd.sh:14-18`, minus `-watchdog`).

  **`-recap-probe` and `-nepal-forcing` are deliberately out**, because they are manually bootstrapped
  rather than installer-managed, and recap is explicitly time-boxed and uninstallable
  (`docs/operations/recap-probe-runbook.md:98`, `:193`; `docs/operations/nepal-forcing-runbook.md`).
  *Trade-off, stated not hidden:* the Nepal forcing feed is live and stays unmonitored by this plan.
  When either becomes installer-managed it is covered automatically, with no edit here — which is the
  whole reason the source is the installer array and not a list in this document.

  **The watchdog excludes itself.** It cannot observe its own failure — a dead watchdog raises nothing
  — and Plan 163's dead-man switch already covers that within ~20 minutes.

- **D3 — One distinct condition, latched PER LABEL.**
  *(Revised: this was the review's blocker. The draft said "copy `_backup_notification_kind`", which
  tracks a single boolean (`was_stale: bool, is_stale: bool` —
  `src/sapphire_flow/ops/watchdog.py:813`). Two concrete failures followed: agent A stays failing and
  agent B then fails, but the aggregate boolean is already true so **B is never reported**; and a
  failing agent that later probes UNKNOWN reads as "not failing", producing a **false recovery** with
  no successful run.)*

  State is the **set of currently-failing labels**, persisted. A transition is a set difference:
  alert when a label **enters** the set, alert again when it **leaves**. Never every tick — on the
  mini the stack-starter is failing right now, so a per-tick condition would alert every 300 s until
  the PATH fix deploys.

  **UNKNOWN preserves the prior verdict for that label and produces no transition** — neither a new
  incident nor a recovery. Absence of evidence is not evidence of recovery.

  Distinct from every existing condition: a failing agent is not a stale backup and not an unhealthy
  API.

- **D4 — Never abort the tick, and never fail silent.**
  The probe runs with a **finite subprocess timeout**. `launchctl` missing, hanging, or emitting
  unparseable output must degrade to UNKNOWN and leave every later probe running — the BAFU (`:1277`), forecast-freshness
  (`:1418`), state-persistence (`:1480`) and dead-man (`:1496`) steps all follow it in `run_once`. A monitor that dies while checking whether things are alive is the failure this plan
  exists to prevent.

  **UNKNOWN is itself reportable.** If the probe cannot read launchd at all — an OS format change, a
  missing binary — that must surface in the same transition-latched condition rather than presenting
  as "no agents failing". Silent degradation is the exact defect being fixed, one level up.

## Sequencing — resolved

**Plan 194 has MERGED to `main`** (verified 2026-08-21). The merge-order constraint the draft worried
about no longer exists; `depends_on: [194]` is satisfied, and this plan can start whenever the owner
promotes it.

**The seam to copy is `backup_device_verifier`, defaulting to `default_backup_target_verifier`** —
an injected callable, so tests never touch the OS:

| Symbol | On `main` |
|---|---|
| `backup_target_verified(backup_dir, *, data_dir)` | `src/sapphire_flow/ops/watchdog.py:624` |
| `default_backup_target_verifier` | `:649` |
| `backup_device_verifier` parameter on `run_once` | `:1087` |
| its call site | `:1144` |

*(Revised: the draft twice called this `volume_probe` and said 194 adds a config field. Neither was
true — that name came from a superseded draft of 194, and 194 T3 explicitly forbids a config field.)*

**Reference points in the same file** (re-derived on `main` 2026-08-21, not inherited): the per-tick
probe order is `device_verified` `:1144` → BAFU `:1277` → forecast freshness `:1418` →
`state.dump(...)` `:1480` → dead-man ping `:1496`. `WatchdogState.load` is `:241` with tolerant
`raw.get(...)` defaults through `:285`; `_backup_notification_kind` — the single-boolean helper D3
must NOT copy verbatim — is `:813`.

## Tasks

### T1 — the probe
*In:* `src/sapphire_flow/ops/watchdog.py`.
A pure function from `launchctl list` output to a per-label verdict (`OK` / `FAILING(status)` /
`ABSENT` / `UNKNOWN`), and an injectable probe callable — the same seam `backup_device_verifier` uses
— so tests never shell out. The subprocess call carries a finite timeout.
**Red-first:** a test asserting FAILING for real captured `launchctl list` output with a non-zero
second column, OK for `0`, ABSENT for a label missing from the output, and UNKNOWN for unparseable
output, must fail before the parser exists.
**Also test:** timeout, missing executable, and malformed output are each contained — the function
returns UNKNOWN and does not raise.

### T2 — the condition
*In:* `src/sapphire_flow/ops/watchdog.py`.
Per D3: distinct message naming the agent and its status, own persisted state (the failing-label set),
transition-latched. Mirror the state handling Plan 194 established, including `WatchdogState.load`
tolerating the new key being absent from an existing state file (`watchdog.py:241`, tolerant
`raw.get(...)` defaults through `:285`).
**Red-first, and these are the blocker's tests:**
- A failing agent alerts exactly once on the transition tick and nothing on the next.
- **A-failing then B-failing raises a SECOND alert** — the case a single boolean would swallow.
- **failing → UNKNOWN raises no recovery** and preserves the incident.
- A later probe still runs after the launchd probe times out.
- This condition neither suppresses nor duplicates any existing alert.

### T3 — the docs that were wrong
*In:* `docs/deployment/mac-mini-staging.md`.
`:431-439` explains "Docker Desktop did not start within 240s" as a VirtioFS cold-boot hang with remedy
`open -a Docker` — a pre-written wrong diagnosis that absorbed the alarm for four months. `:314-316`
documents an expected `0` that has never been true. Correct both, and say what the new condition
reports.

## Non-goals

Fixing `start-sapphire.sh`'s PATH (a separate two-line PR) · migrating any agent to the system domain
(Plan 164 T1) · the installer's cross-domain guard (164 T2) · log rotation · monitoring manually
bootstrapped or non-SAPPHIRE launchd jobs · anything about Docker Desktop's own startup.

## Exit gates

```bash
uv run pytest tests/unit/ops/ -k watchdog
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright
```

*(Revised: the draft checked `src/` only, while CI checks `src/ tests/` — `docs/standards/cicd.md:469`
— so the declared gate could pass locally and fail CI on the new tests.)*

**Red-first:** both T1 and T2 tests must fail against current committed code, which cannot observe a
launchd agent at all.

**Doc sync:** `docs/deployment/mac-mini-staging.md` (T3) · `docs/standards/logging.md` if the new
event names need registering · Plan 164 § REVISIT already points here.
