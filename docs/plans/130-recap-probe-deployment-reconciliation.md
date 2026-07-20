---
status: DRAFT
created: 2026-07-20
plan: 130
title: Reconcile the recap-probe repo artifacts with the container-exec deployment
scope: Bring scripts/plist/runbook in line with what is actually running on the mac mini. No probe-logic change.
depends_on: []
blocks: []
---

# Plan 130 — recap-probe deployment reconciliation

## Status

**DRAFT.** Do not implement until promoted to READY. To be reviewed via the `plan` workflow
(adversarial Codex-in-the-loop) before READY.

## Context — the drift

PR #103 (merged → `main`, harness at `scripts/recap_probe_loop.py`) shipped a launchd plist and a
runbook that describe a **host-venv** deployment: `uv run python scripts/recap_probe_loop.py` on the
mac mini, `uv` at `/usr/local/bin/uv`. That path **does not work on the mac mini**, verified live
2026-07-20:

- the host `uv` venv has **no `recap_client`** (`ModuleNotFoundError`), and the host **cannot clone**
  the private `hydrosolutions/recap-dg-client` repo (`git ls-remote` → "could not read Username …
  Device not configured") — the mac mini only gets the client through the **Docker image build**;
- `uv` is at `/Users/sapphire/.local/bin/uv`; **`/usr/local/bin/uv` does not exist**.

The probe was therefore deployed a **different** way (owner decision 2026-07-20): a host wrapper that
`docker exec`s the probe into the running worker container (which *does* have `recap_client`). That
deployment is **live and collecting** but exists **only on the host** — the wrapper and the working
plist are not in version control, and the committed plist + runbook are actively wrong.

## What is actually deployed (the reconciliation target)

On the mac mini (`sapphire@…`), loaded and firing every 3 h:

- `/Users/sapphire/.config/sapphire/recap_api_key` — the gateway key, `0600`.
- `/Users/sapphire/recap-probe/recap_probe_loop.py` — a copy of the committed probe (byte-identical
  to `main`).
- `/Users/sapphire/recap-probe/run.sh` — wrapper: reads the key from the file, then
  `docker exec -i -e RECAP_API_KEY=… -e RECAP_TEST_HRU=12300 -e RECAP_PROBE_LOG=/dev/stderr
  sapphire_flow-prefect-worker-1 python - < …/recap_probe_loop.py`, redirecting the container's
  **stderr (JSONL)** to the host log and **stdout (summary)** to a summary log.
- `~/Library/LaunchAgents/ch.hydrosolutions.sapphire-recap-probe.plist` — runs the wrapper,
  `StartInterval 10800`, `RunAtLoad`.
- Host log: `~/Library/Logs/sapphire-recap-probe.jsonl` (JSONL survives container recreation because
  it lives on the host, not in the container).

Why this shape: the running image (`sapphire-flow:0.1.595`) **predates** the probe script, so the
script is fed in via `python -` (stdin) rather than run from inside the image; `/dev/stderr` routes
the JSONL out to the host without a compose/volume change; the key stays in a `0600` file, never in
the wrapper or plist.

## Objective

Make the repo reproduce the running deployment: commit the wrapper, replace the wrong plist, rewrite
the runbook for the container-exec approach, and (post-merge) re-sync the host from the repo so
source and deployment are byte-identical. **No change to the probe's logic or behaviour.**

## Non-goals

- **Not** bringing HRU 12300 (or any gauge) live through the real ingestion path — that is a Wave-1
  milestone gated on Plan 115a + Plan 121 Task 2E + onboarding + gateway subscriptions + deployment
  isolation. Out of scope here.
- **Not** converting the probe to a Prefect flow (that is the graduation path if it becomes a
  standing monitor; see the runbook "out of scope").
- **Not** changing `scripts/recap_probe_loop.py` behaviour. Only a one-line docstring note that it can
  run either in a synced host venv **or** inside a container that already has `recap_client`.

## Scope

### 1. Add the wrapper to version control — `scripts/recap-probe/run.sh`

Commit the deployed wrapper, **parameterised** so it is not welded to one host or container name.
Env vars with the current values as defaults:

| var | default | meaning |
|---|---|---|
| `RECAP_PROBE_CONTAINER` | `sapphire_flow-prefect-worker-1` | the worker container to exec into |
| `RECAP_PROBE_SCRIPT` | `/Users/sapphire/recap-probe/recap_probe_loop.py` | host path to the probe |
| `RECAP_API_KEY_FILE` | `/Users/sapphire/.config/sapphire/recap_api_key` | `0600` key file |
| `RECAP_TEST_HRU` | `12300` | HRU to probe |
| `RECAP_PROBE_LOG` | `/Users/sapphire/Library/Logs/sapphire-recap-probe.jsonl` | host JSONL log |
| `RECAP_PROBE_SUMMARY` | `…/sapphire-recap-probe.summary.log` | host summary log |
| `DOCKER` | `/usr/local/bin/docker` | docker binary (host `uv`/`docker` are not on the launchd PATH) |

Requirements the wrapper must meet (so `shellcheck` and review pass):

- `#!/bin/bash`, `set -uo pipefail` (deliberately **not** `-e`: a failed `docker exec` must still let
  the redirects capture the error into the log rather than abort silently; document this choice
  inline).
- Fail loudly if the key file is missing (non-zero exit + a message to the launchd log), rather than
  running the probe with an empty key.
- Quote every expansion; `DOCKER_HOST=unix:///var/run/docker.sock` exported.
- The key is passed via `-e RECAP_API_KEY="$(cat "$KEY_FILE")"`. **Known trade-off (state it):** this
  puts the key in the container's env and briefly in host `ps`. `docker exec` has no `--env-file`;
  the leak-free alternative is mounting the key as a container secret, which needs a compose change +
  restart — deferred as disproportionate for a single-user staging host running a time-boxed
  experiment.

### 2. Replace the committed plist

`scripts/launchd/ch.hydrosolutions.sapphire-recap-probe.plist` becomes the container version:
`ProgramArguments` = `/bin/bash /Users/sapphire/recap-probe/run.sh`; `StartInterval 10800`;
`RunAtLoad true`; `Standard{Out,Error}Path` → `~/Library/Logs/sapphire-recap-probe.launchd.log`;
`UserName sapphire`. (Absolute host paths mirror the existing `…-watchdog.plist` convention.) It must
pass `plutil -lint`.

### 3. Extend the shellcheck pre-commit hook to cover the wrapper

`.pre-commit-config.yaml`'s `shellcheck` hook `files` regex is
`^(scripts/launchd/.*\.sh|scripts/bootstrap-mac-mini\.sh)$` — it would **not** lint
`scripts/recap-probe/run.sh`. Widen it to include `scripts/recap-probe/.*\.sh` (or `scripts/.*\.sh`)
so the new wrapper is gated. Confirm `shellcheck` passes on the wrapper.

### 4. Rewrite the runbook — `docs/operations/recap-probe-runbook.md`

Replace the host-venv narrative with the container-exec one actually deployed:

- **Prereq**: Docker up, worker container running, `recap_client` importable inside it (the one-line
  verify), sleep already prevented (`pmset sleep 0`, confirmed).
- **Install**: copy `scripts/recap-probe/run.sh` + `scripts/recap_probe_loop.py` to
  `~/recap-probe/`; place the key `0600`; copy the plist to `~/Library/LaunchAgents/`;
  `launchctl load`.
- **How it works**: the three non-obvious mechanics — `docker exec -i … python -` (stdin, because the
  image predates the script), key via `-e` from the `0600` file, `RECAP_PROBE_LOG=/dev/stderr` →
  host log.
- **Verify / Analyse**: unchanged (host JSONL log + the pandas snippet).
- **Caveats** (both already true): the probe runs **inside a production worker container** — keep it
  lightweight (read-only gateway calls, ~seconds every 3 h); HRU 12300 is a **test HRU** with sparse
  coverage, re-point at a real subscribed HRU when available (Plan 121 follow-on).
- **Log growth**: JSONL is ~KB/day; note it is unbounded and hand-pruned (no rotation wired).

### 5. Probe-script docstring note (no logic change)

`scripts/recap_probe_loop.py` module docstring: note it runs either in a host venv with the git-pin
synced **or** inside a container that already has `recap_client`. One-line clarification; behaviour
unchanged.

## Post-merge consistency step (in the runbook + this plan)

After the PR merges, **re-copy `run.sh` from the repo to the host** so the running wrapper is
byte-identical to the committed source (the committed one is parameterised, so it differs from the
currently-deployed hardcoded copy). Then trigger one manual cycle and confirm the host JSONL log
still grows. This closes the drift for good; without it the repo and the host diverge again on the
first edit.

## Risks / open points for review

- **Container-name coupling.** `sapphire_flow-prefect-worker-1` is a compose-derived name; a project
  rename breaks the wrapper. Mitigated by the `RECAP_PROBE_CONTAINER` env override; a future
  hardening could resolve the worker by label.
- **Runs inside a production container.** Acceptable (lightweight, 3 h), but if the worker is under
  load the exec adds a small transient. Documented, not gated.
- **Key exposure via `-e`** — see §1 trade-off.
- **Redeploy interaction.** When the mac mini is later rebuilt to an image that *does* contain the
  script, the wrapper still pipes the host copy (harmless, decoupled from image version). No action.

## Exit gates

```bash
uv run ruff format --check scripts/ && uv run ruff check scripts/   # probe script only; no py change here beyond docstring
uv run pre-commit run shellcheck --files scripts/recap-probe/run.sh
plutil -lint scripts/launchd/ch.hydrosolutions.sapphire-recap-probe.plist
```

(`pyright` scope is `src` only — `scripts/` is out of scope, unchanged.)

**Doc sync:** the runbook is the doc; `.pre-commit-config.yaml` change is captured in §3.

## Verification

- The already-running launchd job is untouched by the repo change; it keeps collecting.
- Post-merge: after the re-sync step, `diff <(git show main:scripts/recap-probe/run.sh) ~/recap-probe/run.sh`
  is empty (modulo the documented parameterisation defaults), and a manual `launchctl kickstart`
  cycle appends to the host JSONL log.

## References

- PR #103 (the merged harness) — the artifacts this plan corrects.
- `docs/plans/121-recap-flow6-and-integration-followons.md` §Live probe (the findings the probe extends).
- `docs/operations/recap-probe-runbook.md` (rewritten here).
- `scripts/launchd/ch.hydrosolutions.sapphire-watchdog.plist` (the launchd convention mirrored).
