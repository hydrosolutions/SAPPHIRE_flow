---
status: READY
created: 2026-08-21
plan: 199
title: Salvage the three unlanded pieces of Plan 158
scope: Re-implement, on current main, the parts of the never-merged Plan 158 branch that main still lacks and that do not collide with Plan 194 — the watchdog free-disk-space alert and the shared Docker endpoint contract. NOT a rebase of that branch, NOT the install-launchd expansion (left unowned on the branch — Plan 164 owns the narrower guard and deliberately rejected it), NOT the bootstrap-mac-mini changes (deferred, not superseded).
depends_on: []
blocks: []
source: branch `docs/plan-158-session-independence` @ 9ef2080 (pushed to origin 2026-08-21)
---

# Plan 199 — salvage the three unlanded pieces of Plan 158

## Status

**READY** — owner flip 2026-08-21, with the threshold decided (D1 below).

**Independent Codex review 2026-08-21 — 0 blockers, 3 majors, 2 minors, all VERIFIED AND FOLDED.** The
majors: the installer was routed to the wrong plan; T2 missed a wrapper newer than the branch plus both
shellcheck gates; T1's red-first test was too weak to lock the behaviour T1 itself demands. The reviewer
confirmed every inventory figure, and confirmed that re-implementation is right — **no branch commit is
individually cherry-pickable** (disk space is bundled with the dead-man and installer work in `d740597`;
T2's file is inside the 25-file `5c9ce9f`).

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
work. Take the branch's shape — `DiskSpaceResult`, `DEFAULT_DISK_PATH = Path("/")`, alerting on the
existing Slack path — but **not its threshold**.

**D1 (owner, 2026-08-21) — the threshold is 5 % of the volume's total capacity, not an absolute byte
count.** This REPLACES the branch's `DISK_FREE_THRESHOLD_BYTES = 20 GiB`, which was chosen before the
mini's disk behaviour was known and is **0.55 % of a 3.6 TB volume** — it would fire far too late to be
actionable, and only after the host was effectively already full.

Consequences to implement deliberately, not incidentally:
- **Compute the total from the volume being checked**, not from a constant, so the rule travels to any
  host (`shutil.disk_usage(path)` gives `total` and `free` together — take both from one call, so the
  ratio cannot be computed from two different instants).
- **The percentage is the contract; report the bytes too.** An alert saying "4.1 % free" is not
  actionable on its own — the operator needs "4.1 % free (152 GB of 3.6 TB)" to judge urgency. Put both
  in the message.
- On today's mini that is a **~184 GB floor**, which the host has crossed in living memory: it sat at
  214 GB free (95 % used) and nothing alerted.

**Follow main's current conventions, not the branch's**, because `run_once` has changed since: the
condition must be a **distinct** condition with its own notification state, and it must not be folded
into staleness — the same rule Plan 194 D4 established for the backup-device condition, and for the
same reason (different operator action). Reuse the transition-latching Plan 194 D6 introduced rather
than inventing a second mechanism.

**Why it earns its place:** the mac mini sat at **95 % full** (3.7 TB used / 214 GB free) with ~3.4 TB
unattributable, and was measured at **25 %** three days later with nobody knowing what freed it. Neither
transition raised anything. A disk that fills silently takes the database, the dumps and the forecasts
with it.

**Red-first — and one "it alerts" test is NOT enough.** *(Independent review, MAJOR.)* A single
fires-below-threshold assertion is satisfied by the branch's own implementation, which alerts **every
tick** and calls `slack_poster` **directly** rather than through `_safe_slack_post` (`watchdog.py:1047`)
— on current main a raising poster would then abort before state persistence and the dead-man ping
(ordering at `watchdog.py:1480`). Mirror the device-condition contracts already locked from
`tests/unit/ops/test_watchdog.py:1156`: sustained-low **silence** after the first alert, exactly one
recovery, failed-delivery **retry**, and a disk alert and a device alert firing in the same tick
remaining **independent**. Give disk space its own persisted counter and pending kind — there is no
inherent collision with `backup_device_notification_pending`, but only if it does not share state.

### T2 — `docker-endpoint.sh`, the shared Docker endpoint contract (158 D8/T4)

*In:* new `scripts/launchd/docker-endpoint.sh`; sourced by the launchd wrappers.

One sourced file defining `DOCKER_BIN` and exporting `DOCKER_HOST`, so every launchd job resolves the
Docker CLI and daemon socket the same way instead of hardcoding both. It preserves the existing
`DOCKER_CMD` test-injection seam that `test_launchd_prune_docker.py` and `test_recap_probe_wrapper.py`
already rely on.

**Why it earns its place — this bit us twice this week.** Every launchd wrapper currently repeats
`/usr/local/bin/docker` and the socket path by hand. The recap probe's fork drifted precisely there and
sat dead for 31 days (Plan 132), and the Plan 132 cutover had to re-establish the same two values by
hand. The branch also notes this is what lets a future **headless container runtime** repoint both via
launchd `EnvironmentVariables` with no script edits. *(The branch calls that "Plan 159"; do not follow
the number — main's Plan 159 is the unrelated aquacast shim. The branch used its own numbering.)*

**Source it from EVERY wrapper, enumerated — the branch's list is out of date.** *(Independent
review, MAJOR.)* The branch knew `start-sapphire.sh`, `prune-docker.sh` and `run-recap-probe.sh`.
Current main also has **`scripts/launchd/run-nepal-forcing.sh`** (added by Plan 192, merged
2026-08-21), which independently hardcodes both values at `:19-20`. Following the branch mechanically
would leave Nepal forcing pointed at Docker Desktop after an endpoint repoint — the precise failure
this contract exists to prevent.

**Both lint gates need `shellcheck -x`.** A sourced file is invisible to plain `shellcheck`, and today
`.pre-commit-config.yaml:51` and `.github/workflows/ci.yml:56` both run it plain — CI's list also omits
several launchd scripts. The branch's original T4 changed both gates; this plan must too, or the new
file is unchecked and the `source` line trips SC1091.

**Scope discipline:** add the file, source it from the four wrappers, fix the two gates. Nothing else.

## Explicitly routed elsewhere

- **`install-launchd.sh` (69 → 382 lines) + `test_install_launchd.py` (646 lines) → NOT Plan 195, and
  NOT owned by anything today.** *(Corrected after independent review; the draft routed it to 195 and
  was wrong.)* Plan 195's scope line explicitly excludes domain migration and agent-behaviour change,
  and its tasks touch only the watchdog and docs — sending a 382-line transactional installer there
  would over-expand it. The installer guard belongs to **Plan 164**, which is `status: DEPRIORITISED`
  and which **deliberately rejected the branch's transactional expansion** in favour of a much smaller
  cross-domain guard (164 T2, corrected 2026-08-18). So: leave it on the branch, unowned. If the
  installer is ever wanted, it is a fresh decision against 164's narrower one — not a salvage.
- **`bootstrap-mac-mini.sh` changes + `test_bootstrap_mac_mini.py` (406 lines) → NOT salvaged.**
  Plan 194 rewrote that script on 2026-08-21 (the device-verification predicate, failing closed). The
  branch's version predates it. Reconciling them is a bigger job than either piece is worth today.
  **DEFERRED, not superseded** *(independent review, MINOR — the draft's wording was inaccurate)*:
  Plan 194 covered only device verification, so the branch's service-account and teardown fixes remain
  unique — `docs/plans/README.md:347` records them as such, and main still swallows a
  `docker compose down` failure and reports success at `scripts/bootstrap-mac-mini.sh:191`.
  **Also recorded so it is a decision, not an oversight:** main has **no test file for
  `bootstrap-mac-mini.sh`** at all; Plan 194 added only tests for its own change.

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

## Decisions

- **D1 — 5 % of volume capacity** (owner, 2026-08-21). See T1. The branch's absolute 20 GiB is rejected.
