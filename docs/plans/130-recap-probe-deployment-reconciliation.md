---
status: READY
created: 2026-07-20
plan: 130
title: Reconcile the recap-probe repo artifacts with the container-exec deployment
scope: Bring scripts/plist/runbook in line with what is actually running on the mac mini. No probe-logic change.
depends_on: []
blocks: []
---

# Plan 130 — recap-probe deployment reconciliation

## Status

**READY** (owner, 2026-07-20). Implementation authorised; hold at PR. Reviewed via the `plan` workflow
(3 rounds, escalated) + two focused independent Codex passes. Round-2 Codex confirmed **9/12 findings resolved** and raised 3
residual majors + 2 minors (non-hermetic test paths, the `--env-file` false premise, a weak key guard,
a citation, a test-soundness claim) — **all folded**; the **final Codex pass returned READY-FOR-OWNER**
(no blockers/majors). The design fork it surfaced (docker-exec vs host-git-auth) is **owner-resolved**
below. Owner owns the READY flip and the branch/PR (hold-at-PR).

## Context — the drift

PR #103 (merged → `main`; harness at `scripts/recap_probe_loop.py`) shipped a launchd plist and a
runbook describing a **host-venv** deployment — `uv run python scripts/recap_probe_loop.py` on the
mac mini, `uv` at `/usr/local/bin/uv`. That does not work on the mac mini, verified live 2026-07-20:

- the host `uv` venv has **no `recap_client`** (`ModuleNotFoundError`), and host-level git has **no
  credential** for the private `hydrosolutions/recap-dg-client` clone (`git ls-remote` → "could not
  read Username … Device not configured");
- `uv` is at `/Users/sapphire/.local/bin/uv`; **`/usr/local/bin/uv` does not exist**.

The probe was therefore deployed a different way (owner decision 2026-07-20): a host wrapper that
`docker exec`s the probe into the running worker container, which **does** have `recap_client` (it is
baked into the image at build time). That deployment is **live and collecting** but exists **only on
the host** — the wrapper and the working plist are untracked, and the committed plist + runbook are
actively wrong.

## Design decision — docker-exec, not host-git-auth (owner, 2026-07-20)

The review correctly noted a simpler alternative: configure host-level git auth for the private clone
(the same `insteadOf` + `RECAP_DG_CLIENT_TOKEN` pattern CI and the Docker build already use —
`.github/workflows/ci.yml:23-25`, `docs/standards/security.md`; the token file already exists on this
host, `docs/deployment/mac-mini-staging.md`), then `uv sync` so the host venv gets `recap_client`, and
fix the plist's `uv` path — which would let the *original* host-venv plist work and delete the wrapper
entirely.

**Owner chose docker-exec anyway**, for two reasons: (1) keep the `RECAP_DG_CLIENT_TOKEN` as a
file-scoped secret and **out of the host account's global git config**; (2) the docker-exec deployment
is already live and validated, and the probe runs against the same environment the operational
pipeline uses. The host-venv path is recorded here as the considered-and-rejected alternative so this
decision is not silently re-made.

## What is actually deployed today

On the mac mini, loaded and firing every 3 h:

- `/Users/sapphire/.config/sapphire/recap_api_key` — gateway key, `0600`.
- `/Users/sapphire/recap-probe/recap_probe_loop.py` — an untracked hand-copy of the committed probe.
- `/Users/sapphire/recap-probe/run.sh` — an untracked wrapper: reads the key, then
  `docker exec -i -e RECAP_API_KEY="$(cat …)" … python - < …/recap_probe_loop.py`, redirecting the
  container's **stderr (JSONL)** to the host JSONL log and **stdout (summary)** to a summary log.
- `~/Library/LaunchAgents/ch.hydrosolutions.sapphire-recap-probe.plist` — runs the wrapper,
  `StartInterval 10800`, `RunAtLoad`. Manually `launchctl load`ed.
- Host JSONL log at `/Users/sapphire/Library/Logs/sapphire-recap-probe.jsonl` (survives container
  recreation — it lives on the host).

Two defects in the live wrapper this plan **corrects**, not just captures:

1. **JSONL impurity** — it routes container stderr *straight* into the JSONL file, so an infra failure
   (Docker error, `recap_client` `ImportError`, missing key) or a stray warning line writes non-JSON
   into the JSONL and breaks the pandas analysis.
2. **Root exec** — plain `docker exec` bypasses the entrypoint's `gosu app` drop
   (`docker/entrypoint.sh`), so the probe runs as **root** inside the container, violating the
   non-root application-process invariant (`docs/standards/security.md`).

## Discovered issue (flagged, not fixed here): the watchdog agent is broken

While auditing the launchd agents this work found that `ch.hydrosolutions.sapphire-watchdog` is
**failing to launch** — `launchctl list` shows last exit status **78** with an empty log — because its
`ProgramArguments` points at the non-existent `/usr/local/bin/uv`
(`scripts/launchd/ch.hydrosolutions.sapphire-watchdog.plist:10`; `uv` is actually at
`/Users/sapphire/.local/bin/uv`). This is a real reliability bug (a dead watchdog on the same host as
the Plan 100 blackout / Plan 115-A4 sleep issues) and it is the reason this plan does **not** reuse the
host-venv `uv` path. **It is out of scope here** — the recap probe uses `docker exec`, not `uv`, so it
is unaffected — but it needs its own small fix (correct the watchdog plist's `uv` path and re-verify
the agent fires). Recorded so it is not lost.

## Objective

Make the repo reproduce (a corrected version of) the running deployment: commit the wrapper and its
plist so both are reproducible, fix the two wrapper defects above, add the wrapper to CI shellcheck,
add a CI-reachable test for the wrapper's JSONL-purity branching, rewrite the runbook for the
container-exec approach, and (post-merge) point the host at the git clone so `git pull` is the single
sync mechanism. **No change to the probe's logic** — only the wrapper's log routing and privilege drop
are hardened relative to the live copy.

## Non-goals

- **Not** bringing HRU 12300 (or any gauge) live through the real ingestion path — a Wave-1 milestone
  gated on Plan 115a + Plan 121 Task 2E + onboarding + gateway subscriptions + deployment isolation.
- **Not** converting the probe to a Prefect flow (the graduation path if it becomes a standing
  monitor).
- **Not** registering the probe in `scripts/launchd/install-launchd.sh` / bootstrap. It stays a
  manually-loaded agent (runbook install/uninstall steps), matching what is deployed and appropriate
  for a time-boxed experiment; the frequently-changing artifacts (wrapper + probe script) sync via
  `git pull` from the clone, and only a rare plist change needs a manual reload.
- **Not** fixing the watchdog bug (separate; see above).
- **Not** changing `scripts/recap_probe_loop.py` behaviour — only two text edits (docstring + the
  `ImportError` message) so both mention the container path. No control-flow change.

## Scope

### 1. Commit the wrapper — `scripts/launchd/run-recap-probe.sh`

It lives in `scripts/launchd/` alongside the sibling launchd wrappers (`start-sapphire.sh`,
`watchdog.sh`), so the existing pre-commit `shellcheck` glob (`scripts/launchd/.*\.sh`) already covers
it. It is **hardcoded to the one host and container it runs against** (mirroring how
`start-sapphire.sh` hardcodes `/Users/sapphire/SAPPHIRE_flow`) — no portability indirection for a
single-host experiment; a top-of-file comment states that. The wrapper is the design deliverable;
its control flow lives in the `.sh` file, described (not duplicated as a full script) here.

Spec:

- `#!/bin/bash`, `set -uo pipefail` — deliberately **not** `-e`, because the wrapper must read the
  `docker exec` exit code and **branch** on it (append to the JSONL only on a clean, pure run;
  otherwise route the failure to the launchd log). Note this inline.
- **Docker binary is overridable for tests:** `DOCKER="${DOCKER_CMD:-/usr/local/bin/docker}"`.
  Production uses the absolute path (Docker Desktop symlinks its CLI there); the test injects a fake
  via `DOCKER_CMD` — the mechanism the repo's existing installer test uses
  (`tests/unit/ops/test_launchd_prune_docker.py`), **not** `PATH` injection.
- `export DOCKER_HOST=unix:///var/run/docker.sock` — the value the live deployment uses and that
  produced records under launchd on 2026-07-20 (proven). (`docs/deployment/mac-mini-staging.md`
  mentions a permission-denied scenario under a different CLI context; the socket value is what works
  here — reconcile only if it regresses.)
- **Host paths are env-overridable for tests** (production values are the defaults, so production is
  unchanged and the pytest in §6 can point them at `tmp_path` — same override pattern as `DOCKER_CMD`):
  `KEY_FILE="${RECAP_PROBE_KEY_FILE:-/Users/sapphire/.config/sapphire/recap_api_key}"`,
  `HOST_JSONL="${RECAP_PROBE_HOST_LOG:-/Users/sapphire/Library/Logs/sapphire-recap-probe.jsonl}"`,
  `HOST_SUMMARY="${RECAP_PROBE_HOST_SUMMARY:-/Users/sapphire/Library/Logs/sapphire-recap-probe.summary.log}"`.
  (These override the *host-side* paths only; the container-side `RECAP_PROBE_LOG=/dev/stderr` is fixed.)
- **Key-file guard first.** Before invoking docker, require the key file to exist, be **readable**, and
  read a **non-empty** value (`[[ -r "$KEY_FILE" ]]`, and the captured value is non-empty) — because
  with `set -e` off, `$(cat "$KEY_FILE")` on a missing/unreadable/empty file silently yields an empty
  string and the probe would otherwise run with no key. On any of those, write one line to stderr and
  exit non-zero. Never invoke docker with an absent/empty key.
- **Non-root:** invoke `docker exec -i --user app --workdir /tmp …`. Without `--user app` the exec runs
  as root (the entrypoint's `gosu app` is bypassed); `--workdir /tmp` because `app` need not own the
  workdir.
- **Key delivery: `-e RECAP_API_KEY="$KEY"`** (the pre-validated value from the guard above) — the
  deployed mechanism. Documented trade-off (state it, do not hide it): the key is briefly visible in
  host `ps` and in the container's process env for the exec window. **`docker exec` *does* support
  `--env-file`** (an earlier draft wrongly claimed it did not) — a `0600` file in `KEY=VALUE` format
  would avoid the `ps` exposure. Owner accepts plain `-e` anyway: on a single-user staging host, for a
  3-hourly read-only probe, the few-seconds `ps` window is negligible, and `--env-file` would require
  maintaining a second key-file representation (the probe's own `RECAP_API_KEY_FILE` contract expects a
  raw-value file, not `KEY=VALUE`). If this probe is ever promoted beyond an experiment, switch to
  `--env-file`.
- The probe is fed via `python -` (stdin): `scripts/` is **never** in the image by design (the final
  image `COPY`s only `.venv`, `src/`, `alembic.ini`, `alembic/` — `Dockerfile`; Plan 122). So
  stdin-piping is the permanent mechanism, not a stale-build stopgap.
- Pass `-e RECAP_TEST_HRU=12300` and `-e RECAP_PROBE_LOG=/dev/stderr` (the probe reads these —
  `scripts/recap_probe_loop.py:47-52`, `RECAP_PROBE_LOG` at :50). `/dev/stderr` is the
  **container-side** in-process JSONL sink; the host JSONL path is the wrapper's `HOST_JSONL`, not this
  env var.
- **JSONL purity.** Capture the container's stderr into a temp buffer (not straight into the JSONL).
  Append the buffer to the host JSONL **only if** the exec exited 0 **and** every non-empty buffered
  line parses as JSON (a per-line check — a stray warning can reach stderr even on a 0 exit). Otherwise
  write the whole buffer + a one-line banner to the wrapper's own stderr (→ the plist launchd log) and
  exit non-zero, leaving the JSONL untouched. Container stdout (the terse summary) always appends to
  the summary log.

### 2. Replace the committed plist

`scripts/launchd/ch.hydrosolutions.sapphire-recap-probe.plist` becomes the container version:

- `ProgramArguments` = `/bin/bash /Users/sapphire/SAPPHIRE_flow/scripts/launchd/run-recap-probe.sh` —
  runs the wrapper straight from the host git clone, exactly as `…-watchdog.plist` runs from
  `/Users/sapphire/SAPPHIRE_flow`. So `git pull` is the single sync path for both the wrapper and the
  probe script (no `~/recap-probe/` copies, no manual re-copy — the drift this plan exists to kill).
- `StartInterval 10800`; `RunAtLoad true`; `UserName sapphire`.
- `Standard{Out,Error}Path` = `/Users/sapphire/Library/Logs/sapphire-recap-probe.launchd.log`
  (**absolute** — launchd does not expand `~`).
- Must pass `plutil -lint`.

Install/uninstall is **manual** (`launchctl bootstrap`/`bootout`), documented in the runbook — not
wired into `install-launchd.sh`. Retiring the experiment is symmetric: `launchctl bootout` + delete
the plist file + the wrapper.

### 3. Add the wrapper to CI shellcheck

Pre-commit already covers it (the `scripts/launchd/.*\.sh` glob — no `.pre-commit-config.yaml` change).
CI, however, shellchecks an **explicit** file list (`.github/workflows/ci.yml`), so:

- add `scripts/launchd/run-recap-probe.sh` to that CI shellcheck argument list;
- update the matching shellcheck row in `docs/standards/cicd.md`.

### 4. Rewrite the runbook — `docs/operations/recap-probe-runbook.md`

Replace the host-venv narrative with the container-exec one actually deployed:

- **Prereq**: Docker up under `sapphire`, worker container running, `recap_client` importable in it as
  `app` (`docker exec --user app --workdir /tmp sapphire_flow-prefect-worker-1 python -c "import
  recap_client"`); sleep already prevented (`pmset sleep 0`, confirmed).
- **Install**: ensure the host clone is present and pulled; place the key `0600`; copy the plist to
  `~/Library/LaunchAgents/`; `launchctl bootstrap gui/$(id -u) …` (or `launchctl load`). No
  `~/recap-probe/` copies — wrapper + script run from the clone.
- **How it works**: the four non-obvious mechanics — `docker exec -i … python -` (stdin, because
  `scripts/` is never in the image); `--user app` (non-root); key via `-e` from the `0600` file
  (documented trade-off); `RECAP_PROBE_LOG=/dev/stderr` → buffered → appended to the host JSONL only
  when the run is clean **and** pure JSON, else routed to the launchd log.
- **Verify / Analyse**: host JSONL log + the pandas snippet (unchanged).
- **Update**: edit `scripts/recap_probe_loop.py` / `scripts/launchd/run-recap-probe.sh` on `main`, then
  `git pull` on the host clone — the next cycle runs the new code, no manual copy. A plist change needs
  a manual `bootout`+`bootstrap`.
- **Uninstall**: `launchctl bootout` the label, delete the plist.
- **Caveats** (both true): runs **inside a production worker container** — keep it lightweight
  (read-only, ~seconds/3 h); HRU 12300 is a **test HRU** with sparse coverage — re-point at a real
  subscribed HRU when available (Plan 121 follow-on).
- **Log growth**: ~KB/day, unbounded, hand-pruned (no rotation).

### 5. Probe-script text-only edits (no logic change)

- **Module docstring**: note it runs either in a host venv with the git-pin synced **or** inside a
  container that already has `recap_client`.
- **`ImportError` message** (`scripts/recap_probe_loop.py:40-42`): reword the "Run in a venv where the
  recap-dg-client git-pin is synced" text to mention **either** a synced host venv **or** a
  container/worker image that already has `recap_client`. String-only; `SystemExit(2)` unchanged.

### 6. Test the wrapper — `tests/unit/ops/test_recap_probe_wrapper.py`

A pytest that runs `scripts/launchd/run-recap-probe.sh` as a subprocess with `DOCKER_CMD` pointed at a
**fake docker** (a temp script) **and** the host paths overridden to `tmp_path` (via
`RECAP_PROBE_KEY_FILE` / `RECAP_PROBE_HOST_LOG` / `RECAP_PROBE_HOST_SUMMARY` from §1, so CI never
touches `/Users/sapphire/...`), under `tests/unit/` so CI's pytest run reaches it
(`.github/workflows/ci.yml`). It locks the three JSONL-purity branches:

- (a) exec exits 0 emitting only valid JSON on stderr → those lines land in the JSONL, nothing else;
- (b) exec exits 0 but a non-JSON line reaches stderr → JSONL untouched, buffer + banner → the launchd
  log;
- (c) exec exits non-zero → JSONL untouched, error → the launchd log.

The fake docker also asserts the exec is invoked with `--user app` and `-e RECAP_API_KEY=…` (locking
the non-root invariant and that the key is passed, without asserting the secret value). It also covers
the key guard: a missing/unreadable/empty key file exits non-zero **without** invoking docker.
*Soundness: branches **(b)** and **(c)** are what prove the fix — each must fail against a wrapper that
pipes raw container stderr straight into the JSONL (the currently deployed wrapper), which would let a
non-JSON line or a failed-exec error into the JSONL. (Branch (a) passes against both, so it is a
guard-rail, not the discriminating case.)*

## Post-merge cutover

1. On the mac mini, `git pull` the clone at `/Users/sapphire/SAPPHIRE_flow` (currently ~33 behind;
   the pull touches only source files — containers run from the built image, so no rebuild/restart).
2. Copy the new plist to `~/Library/LaunchAgents/` and `launchctl bootout` + `bootstrap` the agent (a
   plist path change).
3. Remove the now-obsolete `~/recap-probe/` copies (untracked hand-copied script + `run.sh`).
4. `launchctl kickstart` one cycle; confirm the host JSONL log gains a pure-JSON cycle.

After this, the wrapper and probe script are the clone's files and stay in sync on every `git pull`.

## Risks / open points

- **Container-name coupling** — `sapphire_flow-prefect-worker-1` is hardcoded; a compose project rename
  breaks it. Accepted for a single-host experiment (a future hardening could resolve the worker by
  label — add it *then*).
- **Runs inside a production container** — lightweight and 3-hourly, but a small transient if the
  worker is busy. Documented, not gated.
- **Key via `-e`** — the accepted, documented exposure in §1.
- **Manual load** — the plist is not refreshed by bootstrap; acceptable because wrapper/script sync via
  `git pull` and a plist rarely changes. The runbook documents the manual reload.

## Exit gates

```bash
uv run ruff format --check scripts/ && uv run ruff check scripts/   # probe script: docstring + ImportError text only
uv run pre-commit run shellcheck --files scripts/launchd/run-recap-probe.sh
shellcheck scripts/launchd/run-recap-probe.sh                       # same invocation CI runs
plutil -lint scripts/launchd/ch.hydrosolutions.sapphire-recap-probe.plist
uv run pytest tests/unit/ops/test_recap_probe_wrapper.py            # JSONL-purity branches + --user app
```

(`pyright` scope is `src` only — `scripts/` unchanged and out of scope.)

**Doc sync:** the runbook (§4); the `.github/workflows/ci.yml` shellcheck list + the `docs/standards/cicd.md`
shellcheck row (§3).

## Verification

- The already-running launchd job is untouched by the repo change until the cutover.
- **Byte-identical to the clone** after cutover: `diff` of `git show main:…run-recap-probe.sh` and
  `git show main:…recap_probe_loop.py` against the host clone copies is empty.
- **JSONL purity end-to-end**: a manual `launchctl kickstart` appends valid JSON; an injected infra
  failure (non-zero exit or a non-JSON stderr line) lands in the launchd log and never the JSONL — the
  three branches the §6 test locks.

## References

- PR #103 (the merged harness) — the artifacts this plan corrects.
- `docs/plans/121-recap-flow6-and-integration-followons.md` §Live probe (the findings the probe extends).
- `docs/plans/122-package-operational-scripts.md` (why `scripts/` is never in the image).
- `scripts/launchd/ch.hydrosolutions.sapphire-watchdog.plist`, `scripts/launchd/start-sapphire.sh`
  (launchd + run-from-clone + hardcoded-host conventions mirrored; the watchdog is also where the
  discovered `uv`-path bug lives).
- `tests/unit/ops/test_launchd_prune_docker.py` (the `DOCKER_CMD`-fake test convention mirrored).
- `docker/entrypoint.sh`, `docs/standards/security.md` (the non-root invariant `--user app` preserves).
