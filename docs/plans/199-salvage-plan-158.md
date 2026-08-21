---
status: DRAFT
created: 2026-08-21
plan: 199
title: Salvage the three unlanded pieces of Plan 158
scope: Re-implement, on current main, the parts of the never-merged Plan 158 branch that main still lacks and that do not collide with Plan 194 — the watchdog free-disk-space alert and the shared Docker endpoint contract. NOT a rebase of that branch, NOT the install-launchd expansion (goes to Plan 195), NOT the bootstrap-mac-mini changes (superseded by Plan 194).
depends_on: []
blocks: []
source: branch `docs/plan-158-session-independence` @ 9ef2080 (pushed to origin 2026-08-21)
---

# Plan 199 — salvage the three unlanded pieces of Plan 158

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ PROPORTIONALITY IS A BINDING CONSTRAINT

Two small features with their tests. No new subsystem, no new service, no config framework. Reviewers:
"no findings" is a complete review; a finding must name a concrete failure, not a missing feature.
Adding scope is a cost. **In particular, do not propose rebasing or merging the source branch** — §"Why
this is a re-implementation" explains why that was measured and rejected.

## Provenance — work that existed on one laptop

Plan 158 ("session-independent operational stack") was planned, reviewed and largely built on branch
`docs/plan-158-session-independence`. **It was never pushed.** Sixteen commits and ~5,300 insertions
existed only in a local worktree until 2026-08-21, when a housekeeping pass found it and pushed it to
`origin` as a safety net. **Plan 158's own document is absent from `main`** — the entire thread,
including its decisions, lives only on that branch.

Part of it did reach main by other routes: the **dead-man's-switch landed** (19 references in
`src/sapphire_flow/ops/watchdog.py`), as did `test_launchd_prune_docker.py` and
`test_recap_probe_wrapper.py`. Those are out of scope here.

## What main still lacks (measured 2026-08-21)

| Piece | State on `main` |
|---|---|
| Free-disk-space alert (158 D14) | **absent** — `DiskSpaceResult`, `DEFAULT_DISK_PATH`, `DISK_FREE_THRESHOLD_BYTES` all return 0 hits |
| `scripts/launchd/docker-endpoint.sh` | **absent entirely** (31 lines on the branch) |
| `scripts/launchd/install-launchd.sh` | main has **69 lines**; the branch has **382** |
| `tests/unit/ops/test_bootstrap_mac_mini.py` | absent (406 lines) |
| `tests/unit/ops/test_install_launchd.py` | absent (646 lines) |
| `tests/unit/ops/test_start_sapphire_wrapper.py` | absent (82 lines) |

## ⚠️ Why this is a RE-IMPLEMENTATION, not a cherry-pick — measured, not assumed

The branch diverged **2026-08-13**, and `main` has moved **250 commits** since. Seventeen files overlap,
including `watchdog.py`, `test_watchdog.py`, `bootstrap-mac-mini.sh` and `start-sapphire.sh` — all four
rewritten by **Plan 194 on 2026-08-21**, hours before this plan was written.

**The decisive test:** the branch's three unlanded test files were copied onto current `main` and run.
Result: **42 failed, 7 passed.** They are coupled to the branch's *versions* of the scripts, not to
main's. The tests cannot be lifted without the scripts, and the scripts cannot be lifted without
colliding with Plan 194.

**Therefore the branch is a specification and a reference implementation, not a source of commits.**
Read it, take the design, write the code against today's `main`.

## Tasks

### T1 — the watchdog free-disk-space alert (158 D14)

*In:* `src/sapphire_flow/ops/watchdog.py`, `tests/unit/ops/test_watchdog.py`.

The most independent piece: it touches no shell script and so cannot collide with Plan 194's script
work. The branch's shape — `DiskSpaceResult`, `DEFAULT_DISK_PATH = Path("/")`,
`DISK_FREE_THRESHOLD_BYTES = 20 GiB`, CLI-configurable, alerting on the existing Slack path.

**Follow main's current conventions, not the branch's**, because `run_once` has changed since: the
condition must be a **distinct** condition with its own notification state, and it must not be folded
into staleness — the same rule Plan 194 D4 established for the backup-device condition, and for the
same reason (different operator action). Reuse the transition-latching Plan 194 D6 introduced rather
than inventing a second mechanism.

**Why it earns its place:** the mac mini sat at **95 % full** (3.7 TB used / 214 GB free) with ~3.4 TB
unattributable, and was measured at **25 %** three days later with nobody knowing what freed it. Neither
transition raised anything. A disk that fills silently takes the database, the dumps and the forecasts
with it.

**Red-first:** a test asserting the alert fires below the threshold must fail against current code,
which cannot detect the condition at all.

### T2 — `docker-endpoint.sh`, the shared Docker endpoint contract (158 D8/T4)

*In:* new `scripts/launchd/docker-endpoint.sh`; sourced by the launchd wrappers.

One sourced file defining `DOCKER_BIN` and exporting `DOCKER_HOST`, so every launchd job resolves the
Docker CLI and daemon socket the same way instead of hardcoding both. It preserves the existing
`DOCKER_CMD` test-injection seam that `test_launchd_prune_docker.py` and `test_recap_probe_wrapper.py`
already rely on.

**Why it earns its place — this bit us twice this week.** Every launchd wrapper currently repeats
`/usr/local/bin/docker` and the socket path by hand. The recap probe's fork drifted precisely there and
sat dead for 31 days (Plan 132), and the Plan 132 cutover had to re-establish the same two values by
hand. The branch also notes this is what lets **Plan 159**'s headless runtime repoint both via launchd
`EnvironmentVariables` with no script edits.

**Scope discipline:** add the file and source it from the wrappers. Do **not** also expand
`install-launchd.sh` here (see below).

## Explicitly routed elsewhere

- **`install-launchd.sh` (69 → 382 lines) + `test_install_launchd.py` (646 lines) → Plan 195.** That
  plan is already about launchd agents that fail invisibly; installing them correctly is the same
  subject, and the 646-line test only makes sense against the expanded script. Folding it here would
  double this plan's size and split Plan 195's topic across two documents.
- **`bootstrap-mac-mini.sh` changes + `test_bootstrap_mac_mini.py` (406 lines) → NOT salvaged.**
  Plan 194 rewrote that script on 2026-08-21 (the device-verification predicate, failing closed). The
  branch's version predates it. Reconciling them is a bigger job than either piece is worth today.
  **Recorded so it is a decision, not an oversight:** main still has **no test file for
  `bootstrap-mac-mini.sh`**, and Plan 194 added only the tests for its own change.

## Non-goals

Rebasing or merging `docs/plan-158-session-independence` · the dead-man's switch (already landed) ·
Plan 159's headless runtime · restoring Plan 158's plan document to main · any change to what the
watchdog already alerts on.

## Exit gates

```bash
uv run pre-commit run shellcheck --files scripts/launchd/docker-endpoint.sh
shellcheck scripts/launchd/docker-endpoint.sh
uv run pytest tests/unit/ops/ -q
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright
```

**Doc sync:** `docs/standards/cicd.md` § Host-level watchdog (the new condition);
`docs/deployment/mac-mini-staging.md` (the endpoint contract).

## Open question for the owner

**Is 20 GiB the right free-space threshold for this host?** The branch chose it before the mini's
3.4 TB anomaly was known, and the volume is 3.6 TB — 20 GiB is 0.55 %, which may fire too late to be
useful. A percentage floor, or a higher absolute floor, may serve better. The plan does not assume the
branch's number is still right.
