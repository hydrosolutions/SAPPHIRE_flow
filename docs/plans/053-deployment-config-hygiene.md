# Plan 053 — Deployment config hygiene (compose + standards alignment)

**Status**: DRAFT
**Date**: 2026-04-18
**Depends on**: Plan 046 DONE (staging host operational; compose-file churn
settled). Plan 049 C1 removes `prefect-server` from the `frontend` network —
this plan MUST NOT conflict with that change.
**Scope**: Close the five deployment-config gaps surfaced by the 2026-04-18
audit. All are pre-existing issues independent of Plan 046's test-harness work:
missing `nwp_grids` volume, unpinned `VERSION:-latest` fallback, missing
`caddy` healthcheck, stale `PREFECT_UI_URL` fallback, and unverified
`prefect-server` container user. Strictly `docker-compose.yml` and one line in
`src/sapphire_flow/api/__init__.py` — no runtime behaviour change to business
logic.

---

## Context

### Why now

The gridded-NWP path (Plan 045) went live on 2026-04-17. It writes to
`/data/nwp_grids` inside the `prefect-worker` container per
`docs/standards/cicd.md` — but the named volume does not exist in
`docker-compose.yml`. Every gridded-cycle archive write currently lands on
the container's ephemeral layer and is lost on restart. This is silent data
loss for a feature that just shipped.

The other four items are lower-severity but cheap to fix in the same pass:

| Finding | Severity | Root cause |
|---|---|---|
| `nwp_grids` volume missing | **HIGH** | Added to standards doc in Plan 021 / 045 but not propagated to `docker-compose.yml`. |
| `VERSION:-latest` fallback | MED | `${VERSION:-latest}` across three services; accidental unpinned deploy possible. |
| `caddy` no healthcheck | MED | cicd.md specifies "TCP check on 443" — absent. |
| `PREFECT_UI_URL` default `localhost:4200` | MED | `api/__init__.py:13` — hard-coded fallback. Inside a container, `localhost` is wrong. |
| `prefect-server` root-user not verified | LOW | Upstream `prefecthq/prefect:3-python3.11` image has no `user:` directive in compose; needs verification per security.md. |

### Coordination with in-flight plans

- **Plan 046** is adding a deployment test harness (`tests/deployment/`,
  entrypoint tweaks). Its `docker-compose.yml` edits are scoped to the test
  harness. T1 and T2 below do not overlap.
- **Plan 049 C1** removes `prefect-server` from the `frontend` network. If
  Plan 049 merges first, this plan is unaffected. If this plan merges first,
  Plan 049's C1 diff stays identical (no additional churn).
- **Plan 048 (DRAFT)** will add `backup_repo_password` Docker secret. Not
  in scope here.

---

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Declare `nwp_grids` as a named volume** and mount on `prefect-worker` at `/data/nwp_grids:rw`. Do not bind-mount to a host path — keep the volume managed. | Named volumes survive `docker compose down` without `-v`; backups can target them via `restic` (Plan 048). Matches the pattern used for `postgres_data`, `prefect_data`, `backups`. |
| D2 | **Require `VERSION` to be set**: replace `${VERSION:-latest}` with `${VERSION:?VERSION is required; set in .env}`. Fail-fast on unset. | cicd.md mandates pinned image tags in production. A fail-fast compose config prevents accidental `:latest` pulls. `.env.example` and the Plan 046 runbook both set `VERSION` explicitly — no real deployment relies on the `latest` fallback. |
| D3 | **Add caddy healthcheck**: `healthcheck: test: ["CMD", "wget", "-qO-", "http://localhost:2019/metrics"]` (Caddy admin API on port 2019, always enabled) or `nc -z localhost 443`. | cicd.md §Health checks specifies caddy needs a check. The admin-API probe is robust because it validates Caddy is actually serving, not just that the port is open. |
| D4 | **Set `PREFECT_UI_URL` env var on the `api` service**: `PREFECT_UI_URL=http://prefect-server:4200` in compose. Keep the Python fallback for local dev, but update to a more obviously-dev value (`http://localhost:4200`). | Production compose explicitly sets the value; dev fallback stays. No Python logic change beyond the env-var expectation. |
| D5 | **Verify `prefect-server` container user**: run `docker compose exec prefect-server id` after a fresh `up`. If UID 0, add `user: "1000:1000"` (the upstream image supports non-root via the `PREFECT_HOME` env var). If already non-root, document and move on. | The upstream Prefect image's default user changed across 3.x minor versions; explicit verification is cheap and removes ambiguity. |

---

## Task list (single stream)

### T1 — Add `nwp_grids` named volume

**File**: `docker-compose.yml`

1. Under `volumes:` at the end of the file, add:
   ```yaml
   nwp_grids: {}
   ```
2. Under `prefect-worker` → `volumes:`, add:
   ```yaml
   - nwp_grids:/data/nwp_grids:rw
   ```
3. Verify `config.toml` references `/data/nwp_grids` as
   `archive_base_path` — confirm no other service needs the mount.
4. Smoke test: `docker compose up -d prefect-worker` → `docker compose exec
   prefect-worker ls -la /data/nwp_grids` should return an empty directory
   owned by the app user (not root).

**Exit**: gridded-cycle archive writes land in a named volume that persists
across container restarts.

### T2 — Require `VERSION` to be set

**File**: `docker-compose.yml`

1. Find every `${VERSION:-latest}` (audit found lines 69, 108, 177). Replace
   with `${VERSION:?VERSION is required; set in .env or export before compose up}`.
2. Confirm `.env.example` has `VERSION=0.1.297` (or current pinned version);
   if missing, add it.
3. Smoke test: `unset VERSION && docker compose config` must fail with
   "VERSION is required" (fail-fast behaviour).

**Exit**: compose config fails fast when `VERSION` is unset; no path to an
unintentional `:latest` pull.

### T3 — Caddy healthcheck

**File**: `docker-compose.yml`

1. Under the `caddy` service, add:
   ```yaml
   healthcheck:
     test: ["CMD", "wget", "--quiet", "--tries=1", "--spider", "http://localhost:2019/metrics"]
     interval: 30s
     timeout: 5s
     retries: 3
     start_period: 10s
   ```
2. Verify Caddy's admin API is enabled on port 2019 inside the container
   (it is by default unless `admin off` in the Caddyfile).
3. Smoke test: `docker compose ps caddy` shows `(healthy)` within ~1 minute
   of startup.

**Exit**: caddy has a Docker healthcheck matching cicd.md §Health checks.

### T4 — Fix `PREFECT_UI_URL` default

**File 1**: `docker-compose.yml`

1. Under the `api` service → `environment:`, add:
   ```yaml
   PREFECT_UI_URL: http://prefect-server:4200
   ```
2. Keep `PREFECT_API_URL` as-is (already set per the audit).

**File 2**: `src/sapphire_flow/api/__init__.py:13`

3. Keep the `os.environ.get("PREFECT_UI_URL", "http://localhost:4200")` line
   — it is the intended dev fallback. Add a brief one-line comment:
   `# localhost fallback is for local dev; production sets PREFECT_UI_URL in compose.`
4. Do NOT remove the fallback — removing it would break local dev without a
   `.env` file.

**Exit**: in-container UI links resolve to `prefect-server:4200`; local dev
still works.

### T5 — Verify `prefect-server` container user

**No file change unless needed.**

1. Bring up the stack clean: `docker compose down -v && docker compose up -d`.
2. `docker compose exec prefect-server id` — record the output.
3. If UID is 0 (root):
   - Add `user: "1000:1000"` under the `prefect-server` service block.
   - Confirm Prefect still starts cleanly; the image supports non-root.
   - If Prefect fails to start, try `user: "1002:1002"` (the `prefect` user
     shipped in some image variants).
4. If UID is non-root already: document the finding in
   `docs/standards/security.md` §Container privilege model and move on.

**Exit**: `prefect-server` runs as a non-root UID; fact documented in
security.md.

---

## Dependency graph

```json
{
  "stream-1": {
    "tasks": ["T1", "T2", "T3", "T4"],
    "parallel": "all four in parallel — independent sections of docker-compose.yml",
    "depends_on": []
  },
  "stream-2": {
    "tasks": ["T5"],
    "sequential": true,
    "depends_on": ["T1", "T2", "T3", "T4"]
  }
}
```

T5 runs last because it requires a clean `up -d` to verify user IDs, which is
more useful after the other compose changes are in place.

---

## Files to modify

| Path | Task | Change |
|---|---|---|
| `docker-compose.yml` | T1, T2, T3, T4, T5 | Add `nwp_grids` volume; `VERSION:?...` fail-fast; caddy healthcheck; `PREFECT_UI_URL` env on `api`; optional `user:` on `prefect-server` |
| `src/sapphire_flow/api/__init__.py` | T4 | One-line comment next to the `PREFECT_UI_URL` fallback |
| `.env.example` | T2 | Ensure `VERSION=` line present |
| `docs/standards/security.md` | T5 | Document prefect-server user verification (one sentence) |

---

## Exit gates

1. `docker compose config` succeeds with `VERSION=0.1.297` set and fails with
   `VERSION is required` when unset.
2. `docker compose up -d` followed by `docker compose ps` shows `caddy
   (healthy)` within 90 s of startup.
3. `docker compose exec prefect-worker ls /data/nwp_grids` shows a mounted
   writable directory owned by the app user (not root).
4. `docker compose exec prefect-server id` returns UID ≠ 0 (after T5 if fix
   needed).
5. From a host browser, UI links on the API HTML dashboard point at
   `http://prefect-server:4200` when the API runs in the container; local-dev
   runs unchanged.
6. `docs/standards/security.md` updated with prefect-server user finding.
7. Version bump applied per CLAUDE.md.

---

## Risks

| Risk | Mitigation |
|---|---|
| T1 introduces a volume conflict with Plan 046's test harness | T1 adds a **new** named volume; Plan 046 does not add one of the same name. Coordinate merge ordering (this plan lands after Plan 046). |
| T2 breaks any developer's existing `docker compose up` without `VERSION` in their local `.env` | `.env.example` update is part of T2. One-line runbook update to `docs/handover/` if such a file exists. |
| T3 Caddy admin API actually disabled in Caddyfile | Fallback healthcheck: `nc -z localhost 443`. Verify via `docker compose exec caddy curl localhost:2019/metrics` before committing the healthcheck block. |
| T5 adds `user:` that breaks Prefect startup | Revert on failure — this task is discovery-mode. Document the blocker and leave the service as-is if upstream requires root. |
| Plan 049 C1 simultaneously edits `prefect-server` block | Merge conflict is small (single service block, different keys). Coordinate via git; T5 produces at most a one-line `user:` addition. |

---

## Open questions

Not blocking DRAFT → READY:

1. `nwp_grids` volume backup strategy: include in the v0 `pg_dump`-only backup
   flow (no — the flow is Postgres-only), or wait for Plan 048 (restic)?
   (Recommendation: defer to Plan 048 — archive data is regeneratable from
   ICON-CH2-EPS.)
2. Caddy healthcheck endpoint: `/metrics` on admin API vs. a public route on
   443? (Recommendation: admin API — no dependency on upstream services and
   does not trigger Caddy's access logs.)
