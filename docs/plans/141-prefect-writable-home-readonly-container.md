---
status: DRAFT
created: 2026-07-22
plan: 141
title: Give Prefect a writable home under the read-only container (eliminate the EROFS result-write warning)
scope: Stop the recurring `Failed to write result: [Errno 30] Read-only file system: '/home/app/.prefect'` warning that fires on every Prefect flow/task on the mac-mini. The read-only container (Plan 133) leaves Prefect's default home on the read-only root FS. Point PREFECT_HOME at a writable path. Infra.
depends_on: []
blocks: []
supersedes: []
---

# Plan 141 — Prefect writable home under the read-only container

## Status

**DRAFT.** Surfaced 2026-07-22 during the Plan 138 `onboard-model` retrain on the mac-mini. For a `/plan`
round before READY.

## Context — a benign-but-recurring warning that hides real result-write failures

Every Prefect flow/task on the mac-mini logs, repeatedly:

```
WARNING  Failed to write result: [Errno 30] Read-only file system: '/home/app/.prefect'
         Execution will continue, but the result has not been written
```

It is currently **non-fatal** — Prefect passes results in-memory within a run and continues — but it is
noisy, it means **no task/flow result is ever persisted**, and it is a latent hazard: any Prefect feature
that *depends* on a persisted result (a cross-task cache, a retry that reloads a prior result, a resumable
flow, a future flow that reads another's result) will silently misbehave, and it muddies log-based ops
triage (a real result-write failure is indistinguishable from this constant noise). It should be fixed once,
cleanly.

## Root cause — read-only container + Prefect's default home on the root FS

Plan 133 hardened the containers to **`read_only: true`** (`docker-compose.yml:112,164,211,250,292` — the
`prefect-worker`, `prefect-worker-ingest`, `api`, and `init` services all run Prefect). Their only writable
mounts are `tmpfs: [/tmp, /data/cache]` plus the specific `/data/*` named volumes (artifacts, backups,
nwp_grids, bafu archives) — see the worker block `docker-compose.yml:112-129`. **No `PREFECT_HOME` is set**
(the env has `PREFECT_API_URL` / `PREFECT_LOGGING_LEVEL` only, `:92-97`), so Prefect falls back to its
default **`~/.prefect` = `/home/app/.prefect`**, which lives on the **read-only root filesystem** → every
write there is `EROFS`. The worker uses the remote `prefect-server` (via `PREFECT_API_URL`) for
orchestration, so `PREFECT_HOME` here is only local scratch (result/local storage, profiles) — it just needs
to be *writable*.

## Objective

No `EROFS`/`Failed to write result` warnings from any Prefect service on the mac-mini; Prefect result/local
storage lands on a writable path; all flows (forecast-cycle, ingest, collectors, onboard-model, train-models,
hindcast) continue to run correctly.

## Non-goals

- **Not** relaxing the `read_only: true` hardening (Plan 133 security posture stays).
- **Not** changing Prefect orchestration (the server/postgres backend is untouched).
- **Not** enabling/disabling result *persistence semantics* beyond giving it a writable target (if we later
  want durable cross-run results, that's a separate decision — this plan only removes the EROFS wall).

## Design decision (for `/plan` to confirm) — where PREFECT_HOME points

Two writable options; pick one:

- **Option A — tmpfs `PREFECT_HOME=/tmp/prefect`** (reuse the existing `/tmp` tmpfs). Zero new mounts, minimal
  diff, guaranteed writable. **Ephemeral** — cleared on container restart. Correct *iff* SAPPHIRE never needs
  a Prefect-persisted result to survive a restart (true today: flows run to completion in one process; the
  durable state is in the server's postgres + our own `/data` artifact/forecast stores, not Prefect result
  storage). **Recommended default** for its simplicity, unless `/plan` finds a cross-restart result
  dependency.
- **Option B — persistent named volume `PREFECT_HOME=/data/prefect`.** Add a `prefect_home` named volume
  mounted `:/data/prefect:rw` on all four Prefect services + `PREFECT_HOME=/data/prefect`. Survives restarts;
  robust against any future result-persistence need. Costs a new volume + 4 mounts + the cicd volume-table
  doc update.

`/plan` decides A vs B (default A). Either way, the env var is added to **all four** Prefect-running services
so none is left writing to the read-only root.

## Tasks

### T1 — set PREFECT_HOME to a writable path on all Prefect services

- **Scope:** add `PREFECT_HOME=<A: /tmp/prefect | B: /data/prefect>` to the `environment:` of
  `prefect-worker`, `prefect-worker-ingest`, `api`, and `init` in `docker-compose.yml` (and the mac-mini
  overlay if it overrides env). For Option B only: add the `prefect_home` named volume + a `:/data/prefect:rw`
  mount on each of the four services, and update the `docs/standards/cicd.md` named-volume table. Confirm the
  chosen dir is created/writable at container start (tmpfs is auto-created; a named volume needs the
  entrypoint `chown app:app` treatment like the other `/data/*` volumes — `docker/entrypoint.sh:30` — so
  Option B also touches the chown line, mirroring Plan 136/111's volume fixes).
- **Files:** `docker-compose.yml`; (Option B) `docker/entrypoint.sh` + `docs/standards/cicd.md`; a
  compose-config assertion in `tests/` if one exists for env/volume wiring.
- **Verification:** `docker compose -f … -f docker-compose.macmini.yml config` parses; `PREFECT_HOME` resolves
  on the worker (`printenv PREFECT_HOME`).

### T2 — deploy + verify the warning is gone

- **Scope:** deploy via the standard overlay upgrade sequence (`docs/standards/cicd.md`;
  `-f docker-compose.yml -f docker-compose.macmini.yml`, token, `run --rm --build init`, `up -d` — never a
  bare `up`, see `reference_macmini_ssh_access`). Trigger a representative flow (e.g. a forecast-cycle or a
  small onboard/train run) and confirm **no** `Failed to write result` / `Read-only file system:
  '/home/app/.prefect'` log lines, and that Prefect now writes under `PREFECT_HOME` (or that the write path is
  simply gone from the logs).
- **Files:** deploy actions (version bump only).
- **Verification:** live — a full flow run with **zero** EROFS/result-write warnings in the worker logs; the
  flow completes normally.

## Dependency graph

```json
{
  "phases": [
    { "id": "wire", "tasks": ["T1"], "parallel": false, "depends_on": [] },
    { "id": "deploy", "tasks": ["T2"], "parallel": false, "depends_on": ["wire"] }
  ]
}
```

## Exit gates

```bash
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright src/
uv run pytest
```

## References

- `docker-compose.yml` (read_only services `:112,164,211,250,292`; worker env `:92-97`; worker tmpfs
  `:113-115`; worker `/data/*` volume mounts `:118-129`).
- `docker/entrypoint.sh:30` (the `chown app:app /data/*` line — extended for Option B's named volume).
- Plan 133 (the read-only-data-dir / read-only container hardening this interacts with);
  `docs/plans/133-read-only-data-dir-resilience.md`.
- Observed 2026-07-22 during Plan 138 `onboard-model` (`tidy-mastiff`) — the warning fires on every task.
- memory `reference_macmini_ssh_access` (deploy overlay), `feedback_worktree_discipline_parallel_sessions`.
