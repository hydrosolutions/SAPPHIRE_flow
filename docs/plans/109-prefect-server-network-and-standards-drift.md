# Plan 109 — Restore prefect-server backend-only network + standards-doc drift cleanup

**Status:** DRAFT
**Type:** Infra/security fix (hold-at-PR) + standards-doc hygiene (direct-to-main)
**Owner:** Bea (marti@hydrosolutions.ch)
**Created:** 2026-07-08
**Surfaced by:** the Prefect / Docker / deployment touchpoint map (`docs/touchpoint-maps.md`),
codex-confirmed 2026-07-08. Do **not** start until reviewed — this is a next-session plan.

> Three drifts the infra touchpoint map flagged and codex confirmed. #1 is a real (low-severity)
> security defense-in-depth regression; #2/#3 are doc hygiene. Grouped because they share the
> infra/standards-doc surface.

## #1 (primary) — `prefect-server` is on the DMZ network, contradicting its documented control

**Finding.** `prefect-server` runs as **root** (upstream `prefecthq/prefect:3-python3.11`, a
documented exception, `security.md:366`) and unauthenticated. `security.md:368` lists its
compensating controls as: `cap_drop:[ALL]`, no host port binding, **`backend`-only network after
Plan 049 C1**. But `docker-compose.yml:58` has `networks: [backend, frontend]` — the `frontend`
bridge shared with the internet-facing `caddy`. The documented "backend-only" control has
regressed; the doc claims a mitigation that is not in place.

**Severity: defense-in-depth regression, NOT active internet exposure.**
- No host port binding in prod (only `docker-compose.dev.yml:16` publishes 4200).
- `caddy` does **not** proxy the Prefect UI (SSH-tunnel-only, per `Caddyfile`).
- Residual risk: a `caddy` compromise reaches a root, unauthenticated Prefect admin API directly
  on the shared `frontend` network — exactly what backend-only was meant to prevent.

**Why the fix is safe.** Nothing on `frontend` consumes `prefect-server`: `caddy` doesn't proxy it;
`api` is on `[backend, frontend]` and reaches it via `backend` (`PREFECT_API_URL=http://prefect-server:4200`);
workers are `backend`-only and reach it via `backend`. The `frontend` attachment appears to be a
leftover.

**Proposed change (hold-at-PR — high-risk/infra/security).**
- `docker-compose.yml`: `prefect-server` `networks: [backend, frontend]` → `networks: [backend]`.
- Confirm `security.md:368`'s "backend-only network after Plan 049 C1" is then true (no doc edit
  needed if the fix lands; if deferred, the doc must be corrected to reflect reality instead).

**Open question to resolve BEFORE the PR.** `git log -S` shows the `[backend, frontend]` string
present as of `9f1760d` ("Plan 037 — Docker hardening"). Determine whether Plan 037 intentionally
widened it (and Plan 049 C1's "backend-only" was aspirational/never-applied) or whether it is an
unintentional regression. Either way the end state is backend-only unless a concrete `frontend`
consumer is found.

**Verification.**
- `docker compose config` renders; `docker compose -f docker-compose.yml -f docker-compose.macmini.yml up -d`
  brings up `prefect-server`, workers, and `api` healthy; a worker registers/heartbeats to the server.
- `grep -n frontend docker-compose*.yml` shows no remaining `prefect-server` frontend dependency.
- Multi-model review (security lens) + a codex `-s read-only` grounding pass before PR.

## #2 (doc) — `cicd.md` "no downgrade path" vs. real `downgrade()`

`cicd.md` (rollback section, ~`:268`) states migrations have "no downgrade path," but all 27
`alembic/versions/*.py` implement a real `downgrade()` (codex-confirmed; e.g.
`0026_forecast_provenance_runoff_only.py`, `0027_station_water_level_datum.py`). Decide the policy
and make the doc true: either (a) reword to "downgrades exist but are untested/unsupported; rollback
= restore-from-backup," or (b) commit to supporting downgrades. Recommended: (a) — least-change,
matches current operational reality. Direct-to-main doc edit.

## #3 (doc) — base-image version stale in three places

Actual base image is `python:3.14.6-slim` (`Dockerfile:3,29`), but `cicd.md:29` / `security.md:288`
say `3.11-slim` and the Dockerfile's own header comments (`Dockerfile:1,27`) say `3.12.13`. Correct
all three to `3.14.6-slim` (and add a note that the Dockerfile `FROM` is authoritative). Direct-to-main
doc/comment edit.

## Sequencing / non-goals

- #1 is a code change → its own branch + PR + review + human merge. #2/#3 are docs → can go
  direct-to-main (per repo convention) in one small commit, independent of #1.
- Non-goal: adding auth to Prefect server, changing the root-exception posture, or touching the
  dev-overlay port publish.

## Acceptance criteria

1. `prefect-server` is `backend`-only in `docker-compose.yml`, or the open question resolves to a
   documented, concrete reason it must stay on `frontend` (in which case `security.md:368` is
   corrected instead).
2. Stack comes up healthy with workers heartbeating after the change.
3. `cicd.md` downgrade-path wording and the base-image version (`cicd.md`, `security.md`, Dockerfile
   comments) match reality.
