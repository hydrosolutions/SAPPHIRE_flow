# Plan 062 — Prefect state persistence (`PREFECT_HOME` ↔ `prefect_data` volume)

**Status**: SUPERSEDED by Plan 103 (owner decision 2026-07-22)
> Plan 103 ("Supersedes 062") is the single owning plan for the Prefect `PREFECT_HOME`/state work — it
> carries the writable-`PREFECT_HOME` fix (103 D1) plus flow-run-log persistence (103 D2). This 062 draft
> also contains a stale premise (it treats prefect-server state as SQLite-on-`/root/.prefect`, but current
> `docker-compose.yml:54` runs prefect-server on **Postgres** — `PREFECT_API_DATABASE_CONNECTION_URL`); any
> still-valid server-side `read_only` concern should be re-derived inside Plan 103, not from here. Kept for
> provenance; not to be implemented as-is.
> (Original status: DRAFT, 2026-04-20.)
**Date**: 2026-04-20
**Depends on**: none. Unblocks: follow-up `read_only: true` hardening on
`prefect-server` (deferred from Plan 053 T5). Related: Plan 046 (already
adds `PREFECT_API_DATABASE_PRUNE_OLDER_THAN` to `prefect-server`
environment; this plan adds one more env var to the same block, trivial
merge).
**Scope**: Close a latent data-persistence bug surfaced during the
Plan 053 critical review on 2026-04-20. `prefect-server` mounts the
`prefect_data` named volume at `/data/prefect`, but `PREFECT_HOME` is
never set in any compose file. Prefect 3.x defaults `PREFECT_HOME` to
`/root/.prefect`, so the SQLite DB, deployments, work-pool state, and
flow-run history write to the ephemeral container layer and are lost on
`docker compose down` / restart. This plan sets `PREFECT_HOME` to point at
the named-volume mount so the volume actually does what it was created to
do.

---

## Context

### Evidence

1. `docker-compose.yml` mounts `prefect_data` on `prefect-server` at
   `/data/prefect` (named-volume list at the file tail; mount on the
   `prefect-server` service's `volumes:` block).
2. Neither `docker-compose.yml` nor `docker-compose.dev.yml` set
   `PREFECT_HOME`. Verified 2026-04-20 via a project-wide search.
3. The upstream image `prefecthq/prefect:3-python3.11` has no `USER`
   directive and no `ENV PREFECT_HOME=...` in its Dockerfile (verified via
   `docker history` during Plan 053 round 2). Prefect 3.x's default is
   `$HOME/.prefect` — root's home is `/root`, so the effective path is
   `/root/.prefect`.
4. Consequence: the `prefect_data` volume has been **empty** throughout
   the deployment's life; it survives restarts but captures nothing.
   Every `docker compose up` starts Prefect with a fresh in-memory SQLite
   DB in the ephemeral layer. Deployment registration is re-run by the
   `init` container on each boot, so deployments re-appear — but
   flow-run history, work-pool concurrency counters, and any other state
   older than the current container lifetime are lost.

### Why now

Bug is latent but discoverable: any operator who relies on historical
flow-run records after a restart will find them gone. Also blocks any
future hardening (e.g. `read_only: true` on `prefect-server`, deferred
from Plan 053 T5) because the actual write footprint is unknown until
`PREFECT_HOME` is explicitly routed.

### Non-goals

- Migrating `prefect-server` from SQLite to PostgreSQL (separate concern;
  stays SQLite for v0).
- Switching `prefect-server` to run as non-root (deferred; see Plan 053
  D5 and the §Future work note at the bottom of this plan).
- Backfilling lost history (unrecoverable — document the one-time loss
  and move on).

---

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Set `PREFECT_HOME=/data/prefect` on the `prefect-server` service via the compose `environment:` block.** | Aligns the env var with the already-mounted volume path. Smallest possible change; no volume rename, no entrypoint rewrite, no new Dockerfile. |
| D2 | **Do NOT rename the volume or change the mount path.** The named volume stays `prefect_data`, the mount stays `/data/prefect`. | Renaming would force a `docker compose down -v` across deployments. Path alignment via env var is surgically smaller. |
| D3 | **Do not set `PREFECT_HOME` on `prefect-worker`.** The worker's state is held server-side in Prefect 3; the worker process itself stores nothing durable locally. | Avoid accidental configuration drift. If a future need arises (e.g. caching work-queue metadata), revisit. |
| D4 | **Volume ownership: accept root-owned.** `prefect-server` runs as root (Plan 053 D5), so writes to `/data/prefect` succeed without any `chown` dance. No entrypoint modification needed. | The upstream image has no custom entrypoint we control. If Plan 053 D5 is ever reversed (derived non-root image), this plan's decision will need revisiting — noted in §Future work. |
| D5 | **Startup self-check in the `init` container** (or as an explicit exit gate) to detect regression: after a cold `up`, verify `/data/prefect/prefect.db` exists and is non-empty on `prefect-server`. | Cheap regression guard. Without it, future compose churn could silently unset `PREFECT_HOME` again. |

---

## Task list

### T1 — Verify current write path and document the pre-fix state

**No file change. Diagnostic only.**

1. On a representative deployment (staging Mac-mini, or a local `up` if
   no staging is available), run:
   ```bash
   docker compose exec prefect-server printenv PREFECT_HOME || echo "PREFECT_HOME unset"
   docker compose exec prefect-server ls -la /root/.prefect /data/prefect 2>&1 || true
   ```
2. Expected pre-fix output:
   - `PREFECT_HOME unset`.
   - `/root/.prefect/` contains `prefect.db` and `storage/` (live state).
   - `/data/prefect/` is empty or nearly so.
3. Record the output in the commit message or a brief note so future
   operators can confirm the root cause.

**Exit**: the bug is confirmed reproducible in the current deployment;
output recorded.

### T2 — Set `PREFECT_HOME` on `prefect-server`

**File**: `docker-compose.yml`

1. In the `prefect-server` service's `environment:` block, add:
   ```yaml
   PREFECT_HOME: /data/prefect
   ```
   Place it near other `PREFECT_*` variables (alphabetical or grouped with
   `PREFECT_API_DATABASE_PRUNE_OLDER_THAN` from Plan 046 once that lands).
2. No change to the volume declaration or mount line — those are already
   correct.

**Exit**: compose file sets `PREFECT_HOME=/data/prefect`; no other edits.

### T3 — Verify post-fix state (fresh volume) and accept the one-time loss

**No file change. Destructive validation — coordinate with operators.**

1. `docker compose down -v` (destroys the empty `prefect_data` volume).
2. `docker compose up -d`.
3. Wait ~30 s, then:
   ```bash
   docker compose exec prefect-server printenv PREFECT_HOME
   # → /data/prefect
   docker compose exec prefect-server ls -la /data/prefect
   # → prefect.db, storage/, memo_store.toml (or similar)
   docker compose exec prefect-server ls -la /root/.prefect 2>&1 | head
   # → no such file, or empty
   ```
4. Verify deployments are re-registered (run `docker compose logs init`
   and confirm deployment-creation messages) and flow runs against one of
   them — the run history should persist across a subsequent
   `docker compose restart prefect-server`.

**Exit**: Prefect writes land in `/data/prefect`, survive a restart, and
nothing remains in `/root/.prefect`.

### T4 — Regression guard

**File**: `docker/entrypoint.sh` *(if the `init` service uses the shared
image)* OR a new one-line check in the `init` container's startup
command.

1. Add a check that fails loudly if `PREFECT_HOME` is unset or does not
   resolve to a mounted path. Minimal implementation:
   ```sh
   if [ -z "${PREFECT_HOME:-}" ]; then
     echo "FATAL: PREFECT_HOME not set; Prefect state will not persist" >&2
     exit 1
   fi
   ```
   Place in the `init` container's entry sequence (before
   deployment-registration), not in the shared entrypoint — the shared
   entrypoint runs for `prefect-worker` and `api` too, and those services
   do not need `PREFECT_HOME`.
2. Alternative (lighter touch): add the same check as a standalone
   `scripts/check_prefect_home.sh` invoked only from `init`'s command.

**Exit**: if `PREFECT_HOME` is ever accidentally removed from compose in
the future, the `init` container fails fast with a clear message.

### T5 — Document the behaviour and the one-time history loss

**Files**: `docs/standards/cicd.md`, `docs/handover/` (if relevant)

1. In `docs/standards/cicd.md` §Named volumes (or the nearest equivalent
   section), add a one-line note:
   `prefect_data → /data/prefect; Prefect server state (SQLite, deployments,
   flow-run history); requires PREFECT_HOME=/data/prefect env var.`
2. If a handover runbook exists, add a one-sentence note warning that
   deploying this change wipes pre-existing in-memory flow-run history
   (operator expectation-setting only — no actual data is recoverable).

**Exit**: `cicd.md` records the env-var requirement so future drift is
caught by docs review.

---

## Dependency graph

```json
{
  "stream-1": {
    "tasks": ["T1"],
    "sequential": true,
    "depends_on": []
  },
  "stream-2": {
    "tasks": ["T2", "T5"],
    "parallel": "independent file edits",
    "depends_on": ["T1"]
  },
  "stream-3": {
    "tasks": ["T3"],
    "sequential": true,
    "depends_on": ["T2"]
  },
  "stream-4": {
    "tasks": ["T4"],
    "sequential": true,
    "depends_on": ["T2"]
  }
}
```

T1 is diagnostic and runs first. T2 and T5 are independent edits. T3
validates T2 on a destroyed-and-recreated volume. T4 adds a regression
guard once T2 is in place.

---

## Files to modify

| Path | Task | Change |
|---|---|---|
| `docker-compose.yml` | T2 | Add `PREFECT_HOME: /data/prefect` to `prefect-server` environment block |
| `docker/entrypoint.sh` or `scripts/check_prefect_home.sh` | T4 | Add a one-off env-var presence check for the `init` container path only |
| `docs/standards/cicd.md` | T5 | Note `PREFECT_HOME=/data/prefect` requirement alongside the `prefect_data` volume row |

No changes to: volume definitions, networks, other services, Python code.

---

## Exit gates

1. `docker compose exec prefect-server printenv PREFECT_HOME` prints
   `/data/prefect`.
2. After a full `down -v` + `up -d` cycle,
   `docker compose exec prefect-server ls /data/prefect/prefect.db`
   returns a non-zero-size file.
3. `docker compose exec prefect-server ls /root/.prefect 2>&1` returns
   "No such file or directory" (or an empty directory).
4. A flow run is recorded in Prefect UI and survives
   `docker compose restart prefect-server`.
5. `init` container fails fast with the expected message if
   `PREFECT_HOME` is removed from compose (smoke-test by temporarily
   unsetting and re-running `init`).
6. `docs/standards/cicd.md` mentions the env-var requirement for
   `prefect_data`.
7. Version bump per CLAUDE.md (single squashed commit).

---

## Risks

| Risk | Mitigation |
|---|---|
| Destructive `down -v` in T3 wipes the (empty) volume, but also wipes any future volumes if the command is run post-fix | Document clearly in the runbook that T3 is run once, pre-fix only. After the fix lands, never run `down -v` against a healthy deployment. |
| Users expect flow-run history to carry over | It will not — the bug means there's no history to carry. Announce in the commit message and handover doc. |
| `PREFECT_HOME=/data/prefect` conflicts with a future Prefect 3 version that relocates state | Low probability; Prefect 3.x env contract has been stable. Revisit on major Prefect upgrades. |
| Init check (T4) incorrectly fires on `prefect-worker` or `api` | T4 deliberately scopes the check to `init` only — do not add it to the shared entrypoint. |
| Volume ownership issues because `prefect-server` runs as root but an operator later flips it to non-root | Called out in D4 and §Future work. A reversal of Plan 053 D5 will require this plan's path to be re-evaluated (`chown /data/prefect` before privilege drop). |

---

## Open questions

Not blocking DRAFT → READY:

1. **Is `prefect_data` currently empty on the staging deployment?** T1
   answers this operationally. If somehow non-empty (e.g. an earlier
   `PREFECT_HOME` setting was removed but state survived), T3 should
   capture a copy before `down -v`.
2. **Does any external tool (CI, monitoring, backup) assume Prefect
   state lives at `/root/.prefect` in the container?** Unlikely — nothing
   in the repo touches that path — but worth a grep before merging.

---

## Future work

- Re-evaluate `read_only: true` on `prefect-server` once the full runtime
  write footprint is catalogued (the only known write path after this
  plan is `/data/prefect`, but Prefect may also touch `/tmp` for
  intermediate artefacts).
- Reconsider running `prefect-server` as non-root (Plan 053 D5 Option A,
  derived image). With `PREFECT_HOME` explicit, a derived image can
  `chown /data/prefect` before dropping privileges.
- Consider migrating `prefect-server` from SQLite to the existing
  Postgres instance for better concurrency and durability; separate plan.
