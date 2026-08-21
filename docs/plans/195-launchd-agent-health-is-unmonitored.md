---
status: DRAFT
created: 2026-08-20
plan: 195
title: A launchd agent that cannot run must not look healthy
scope: The watchdog asserts the last exit status of each installed launchd agent and raises one distinct, transition-latched condition when an agent is failing. No new service, no new config file, no domain migration, no change to any agent's own logic.
depends_on: [194]
blocks: []
source: launchd audit 2026-08-20; see docs/plans/164-watchdog-system-domain.md § REVISIT 2026-08-20
---

# Plan 195 — a launchd agent that cannot run must not look healthy

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ PROPORTIONALITY IS A BINDING CONSTRAINT ON THIS PLAN AND ON ITS REVIEW

**This is one probe, one condition, and their tests.** The predicate is a single `launchctl` call
per agent. The watchdog already runs every 300 s, already keeps notification state, and already has a
transition-latched alerting shape to copy (Plan 194 D6). It needs no new service, no new config file,
no registry, no abstraction over launchd, and no change to any agent's own behaviour.

**Rules for reviewers** (carried from Plan 194, which they served well):

1. **"No findings" is a complete and welcome review.** Do not manufacture findings to justify the pass.
2. **A finding must name a CONCRETE FAILURE** — a condition that would go undetected, an alert that
   would fire wrongly, a call site that would break.
3. **A missing specification is NOT a finding** unless leaving it unsaid produces wrong behaviour.
4. **Do not propose new apparatus.** No generalisation to "any launchd job on the host", no plugin
   seam, no config-driven agent registry.
5. **Adding length is a cost.** Prefer deleting to adding.

## Why this matters — measured, not hypothetical

Verified on the mac mini 2026-08-20:

- `ch.hydrosolutions.sapphire` (the stack-starter, the documented reboot-recovery path) reports
  `last exit code = 1` and `runs = 768`. It has **never** succeeded — since its only commit
  `514ff36`, 2026-04-23, i.e. 119 days. Under launchd's default
  `PATH=/usr/bin:/bin:/usr/sbin:/sbin`, `docker info` returns **127**; the 240 s wait always expires.
- `~/Library/Logs/sapphire-flow.log`: **15,217 lines, one distinct string**, zero compose output in
  58 days.
- `grep -c launchctl src/sapphire_flow/ops/watchdog.py` → **0**. Nothing checks agent health.
- `docs/deployment/mac-mini-staging.md:314-316` documents the expected output as
  `ch.hydrosolutions.sapphire  0  -`. The live value is `1`. The documented check would have caught
  this on any day in 119; it was evidently never run against real output.

**The point is not that this agent is broken** — that is a two-line PR, out of scope here. The point
is that it was broken for 119 days *behind a green-looking status line*, and the monitor whose job is
to notice could not have. A recovery mechanism that cannot work is worse than none, because it looks
present.

## Decisions

- **D1 — The probe is `launchctl print gui/<uid>/<label>`, parsed for `last exit code`.**
  Not `launchctl list`, whose second column is the *status* of the last exit but whose output format
  is not stable across macOS versions. An agent is FAILING when its last exit code is non-zero.
  **Never-run is NOT failing** — a periodic agent that has not yet fired (`runs = 0`,
  `last exit code = (never exited)`) is normal; `ch.hydrosolutions.sapphire-docker-prune` was in
  exactly that state on 2026-08-20 and was correct to be.

- **D2 — Which agents.** The labels the repo installs, read from the `PLISTS` array's own source of
  truth rather than a new list: `ch.hydrosolutions.sapphire`,
  `ch.hydrosolutions.sapphire-watchdog`, `ch.hydrosolutions.sapphire-docker-prune`
  (`scripts/launchd/install-launchd.sh:14-18`), plus the two installed since
  (`-recap-probe`, `-nepal-forcing`). **Not** "every job in the domain" — that is apparatus, and it
  would alert on Adobe.

  ⚠️ **Open — the watchdog must not report on itself.** It cannot observe its own failure (a dead
  watchdog raises nothing), and Plan 163's dead-man switch already covers that within ~20 minutes.
  Excluding it is the obvious call; stated as a fork rather than assumed.

- **D3 — One distinct condition, transition-latched.** Message names the agent and its exit code.
  Copy the shape Plan 194 D6 ratified: alert when an agent transitions into failing, and again on
  recovery — **never every tick**. On the mini the stack-starter is failing *right now*, so a
  per-tick condition would alert every 300 s until the PATH fix deploys. Distinct from every existing
  condition: a failing agent is not a stale backup and not an unhealthy API.

- **D4 — Evaluated where the watchdog already probes, and it must never abort the tick.**
  `launchctl` being absent, slow, or returning an unparseable format must degrade to "unknown, do not
  alert" and leave every other check running. A monitor that dies while checking whether things are
  alive is the failure this plan exists to prevent.

## Sequencing — read before starting

**Plan 194 T3 rewrites the same region of `src/sapphire_flow/ops/watchdog.py`** (it adds
`volume_probe`, config, and two state keys, and touches `run_once`'s 77 test call sites). This plan
adds a third condition alongside those. **Do 194 T3 first**, then this, or the two will conflict in
`run_once` and in `WatchdogState`. `depends_on: [194]` records that; it is a merge-order constraint,
not a logical one.

## Tasks

### T1 — the probe
*In:* `src/sapphire_flow/ops/watchdog.py`.
A pure function from `launchctl print` output to a per-agent verdict (`OK` / `FAILING(code)` /
`UNKNOWN`), and an injectable probe callable so tests never shell out — the same seam
`volume_probe` uses in 194 T3.
**Red-first:** a test asserting FAILING for real captured `launchctl print` output with
`last exit code = 1`, and OK for `= 0`, must fail before the parser exists.

### T2 — the condition
*In:* `src/sapphire_flow/ops/watchdog.py`.
Per D3: distinct message, own notification state, transition-latched. Mirror the state-key handling
Plan 194 T3 establishes, including `WatchdogState.load` tolerating the new keys being absent from an
existing state file.
**Red-first:** a test that a failing agent raises exactly one alert on the transition tick and nothing
on the next; and one asserting this condition neither suppresses nor duplicates any existing alert.

### T3 — the docs that were wrong
*In:* `docs/deployment/mac-mini-staging.md`.
`:431` explains "Docker Desktop did not start within 240s" as a VirtioFS cold-boot hang with remedy
`open -a Docker` — a pre-written wrong diagnosis that absorbed the alarm for four months. `:314-316`
documents an expected `0` that has never been true. Correct both, and say what the new condition
reports.

## Non-goals

Fixing `start-sapphire.sh`'s PATH (a separate two-line PR) · migrating any agent to the system domain
(Plan 164 T1) · the installer's cross-domain guard (164 T2) · log rotation · monitoring non-SAPPHIRE
launchd jobs · anything about Docker Desktop's own startup.

## Exit gates

```bash
uv run pytest tests/unit/ops/ -k watchdog
uv run ruff format --check src/ && uv run ruff check src/
uv run pyright
```

**Red-first:** both T1 and T2 tests must fail against current committed code, which cannot observe a
launchd agent at all.

**Doc sync:** `docs/deployment/mac-mini-staging.md` (T3) · `docs/standards/logging.md` if the new
event names need registering · Plan 164 § REVISIT gains a pointer here.
