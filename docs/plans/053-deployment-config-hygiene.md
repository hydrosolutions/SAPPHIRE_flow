# Plan 053 — Deployment config hygiene (compose + standards alignment)

**Status**: READY (2026-04-20, after three review rounds)
**Date**: 2026-04-18 (original); 2026-04-20 (revisions 1–3)
**Depends on**: Plan 046 (status: READY) — no file-level conflict; Plan
046's sole compose change is `PREFECT_API_DATABASE_PRUNE_OLDER_THAN=30` on
`prefect-server`, and it does not touch `docker/entrypoint.sh` despite
loose "entrypoint tweaks" language in its preamble. Plan 049 (status:
DRAFT) will remove `prefect-server` from the `frontend` network; this plan
must not conflict with that change. Plan 045 delivered the gridded-NWP
forecast path and the `/data/nwp_grids` archive write; its plan file is no
longer under `docs/plans/` — verify via `git log --all -- 'docs/plans/045*'`
or the memory record dated 2026-04-17. **Related (not blocking)**: Plan
062 addresses a latent pre-existing bug where `PREFECT_HOME` is not set
and the `prefect_data` volume therefore does not capture Prefect runtime
state. That bug is out of scope for 053.
**Scope**: Close the deployment-config gaps surfaced by the 2026-04-18
compose audit and the 2026-04-20 secondary review. All are pre-existing
issues independent of Plan 046's test-harness work: missing `nwp_grids`
volume plus entrypoint chown, missing size-limited tmpfs for NWP scratch,
unpinned `VERSION:-latest` fallback, missing `caddy` healthcheck, stale
`PREFECT_UI_URL` default, and `prefect-server` running as root per the
upstream image. Strictly `docker-compose.yml`, `docker/entrypoint.sh`,
`.env.example`, one line in `src/sapphire_flow/api/__init__.py`, and a
security.md note — no runtime behaviour change to business logic (T4
changes only HTML link rendering on the API dashboard).

---

## Context

### Why now

The gridded-NWP path (Plan 045) went live on 2026-04-17. It writes to
`/data/nwp_grids` inside the `prefect-worker` container per
`docs/standards/cicd.md:37` — but (a) the named volume does not exist in
`docker-compose.yml` and (b) `docker/entrypoint.sh:27`'s chown list does
not include `/data/nwp_grids`. Every gridded-cycle archive write currently
lands on the container's ephemeral layer and is lost on restart. This is
silent data loss for a feature that just shipped.

Secondary issues surfaced alongside:

| Finding | Severity | Root cause |
|---|---|---|
| `nwp_grids` volume missing + entrypoint chown stale | **HIGH** | Added to standards doc via Plan 021 / 045 but not propagated to `docker-compose.yml` or `docker/entrypoint.sh`. Without the chown edit the volume boots as `root:root`, unwritable by UID 1000. |
| Size-limited tmpfs for NWP scratch missing | **MED** | `cicd.md:43` specifies `tmpfs: /tmp/sapphire_nwp (size=4g)` on `prefect-worker`; live compose has only unsized `/tmp`. `config.toml:363` relies on this path. Pre-existing gap. |
| `VERSION:-latest` fallback | MED | `${VERSION:-latest}` at lines 69, 109, 180; accidental unpinned deploy possible. |
| `caddy` no healthcheck | MED | `cicd.md:23` specifies "TCP check on 443" — absent from the compose file. |
| `PREFECT_UI_URL` default `localhost:4200` | LOW–MED | `api/__init__.py:13` — hard-coded fallback. Inside a container, `localhost` is wrong; browser-side the link resolves only via the dev overlay or a port-forward. |
| `prefect-server` runs as root | LOW | Confirmed on 2026-04-20 via `docker run --rm prefecthq/prefect:3-python3.11 id` → `uid=0(root)`. The upstream image has no `USER` directive and the Prefect project ships no non-root variant. `security.md:347` forbids compose `user:` overrides — remediation must be either a derived image or accept-and-document. |

### Coordination with in-flight plans

- **Plan 046** (READY, not yet DONE). Verified 2026-04-20: Plan 046's
  file-modification table lists `docker/entrypoint.sh` nowhere, and its
  sole `docker-compose.yml` change is adding
  `PREFECT_API_DATABASE_PRUNE_OLDER_THAN=30` to the `prefect-server`
  environment block. No overlap with any Plan 053 task. Land order is
  flexible; either plan can merge first.
- **Plan 049** (DRAFT) will remove `prefect-server` from the `frontend`
  network. If Plan 049 merges first, this plan is unaffected. If this plan
  merges first, Plan 049's C1 diff stays identical (no additional churn).
- **Plan 048** (DRAFT stub) will add `backup_repo_password` Docker secret
  and the restic backup chain. Not in scope here, but referenced by the
  open question on `nwp_grids` backup strategy.

---

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Declare `nwp_grids` as a named volume** and mount on `prefect-worker` at `/data/nwp_grids:rw`. Do not bind-mount to a host path. **Also add `/data/nwp_grids` to the entrypoint's `chown app:app` list** so the volume becomes app-owned on first boot. | Named volumes survive `docker compose down` without `-v`; backups can target them via `restic` (Plan 048). Matches the pattern used for `pgdata`, `prefect_data`, `backups`. The entrypoint chown is required because the shared `sapphire-flow` image has no `USER` directive: it starts as root, chowns known paths, then drops to UID 1000 via `gosu`. Without extending the chown list, the new mount stays root-owned and app writes fail silently. |
| D1b | **Add size-limited tmpfs for NWP scratch** on `prefect-worker` using the Compose **long-form** syntax per `cicd.md:43`. Short-form (`tmpfs: - /tmp/sapphire_nwp:size=4g`) silently drops the `size` option; the Compose spec only accepts `mode`, `uid`, `gid` in short-form. Long-form is required for the size limit to take effect. | The NWP conversion pipeline writes intermediate GRIB2/Zarr data to `config.toml:363`'s `scratch_path = "/tmp/sapphire_nwp"`. Without a size-limited tmpfs, a runaway cycle can fill rootfs. Pre-existing gap that Plan 053 is the right home for. `/tmp` on `api` and `init` is deliberately left unsized — neither service runs NWP-scale writes. |
| D2 | **Require `VERSION` to be set**: replace `${VERSION:-latest}` with `${VERSION:?VERSION is required; set in .env}`. Fail-fast on unset. | `cicd.md:228` mandates pinned image tags in production. A fail-fast compose config prevents accidental `:latest` pulls. `.env.example` and the Plan 046 runbook both set `VERSION` explicitly — no real deployment relies on the `latest` fallback. Residual risk: this does not pin to SHA digest, so mutable-tag attacks remain possible. Deferred to a future security-hardening plan, not this one. |
| D3 | **Caddy healthcheck via `curl` on port 80**. Use `test: ["CMD", "curl", "-sf", "-o", "/dev/null", "http://localhost:80/"]`. | The official `caddy:2` image installs `curl`, `ca-certificates`, `libcap`, `mailcap` — but neither `nc` nor `wget`. An `nc`-based check (as `cicd.md:23` describes conceptually — "TCP check on 443") would exit 127. A `curl` check is the closest in-image approximation. Port 80 is chosen over 443 because Caddy binds 80 unconditionally (v0 default is plain HTTP per `Caddyfile:5`; when `SAPPHIRE_DOMAIN` is set, Caddy still binds 80 for ACME challenges and HTTPS redirect). Admin API on 2019 is avoided because any container on caddy's network could call it to reconfigure routes — a lateral-movement surface. Update `cicd.md:23` text in a follow-up to reflect `curl -sf :80` instead of "TCP check on 443". |
| D4 | **Set `PREFECT_UI_URL` env var on the `api` service** in compose: `PREFECT_UI_URL=http://prefect-server:4200`. Keep the Python fallback `http://localhost:4200` for local dev and add a one-line comment noting it is dev-only. | Production compose explicitly sets the value; dev fallback stays. This changes only HTML link rendering on the API dashboard — no server-side business logic. **Note**: the in-container URL is a Docker-internal hostname and cannot be opened directly from a host browser without the dev overlay, a port-forward, or Caddy fronting the Prefect UI. Exit gate scoped accordingly. |
| D5 | **Accept `prefect-server` as root; document the exception.** The upstream `prefecthq/prefect:3-python3.11` image has no `USER` directive (verified 2026-04-20). `security.md:347` forbids compose `user:` overrides, and the Prefect team ships no non-root tag. Option A (derived image with `USER 1000` + `chown /opt/prefect`) is a 5-line Dockerfile and technically feasible. Chosen path is Option B (accept + document) for these reasons: (i) Prefect 3's on-disk write paths are not fully enumerated — see Plan 062 finding that `PREFECT_HOME` is not even set, so dropping privileges without first fixing state-persistence is premature; (ii) handover timeline favours minimising surface; (iii) the existing compensating controls are adequate for v0. | Compensating controls already in place: `cap_drop: [ALL]`, no host port binding in base compose, `backend`-only network after Plan 049 C1. Re-evaluate once Plan 062 lands and the full write footprint is known, or if a `-nonroot` upstream tag appears. |

---

## Task list

### T1 — Add `nwp_grids` volume + entrypoint chown

**Files**: `docker-compose.yml`, `docker/entrypoint.sh`

1. In `docker-compose.yml`, under `volumes:` at the end of the file, add:
   ```yaml
   nwp_grids: {}
   ```
2. Under `prefect-worker` → `volumes:`, add:
   ```yaml
   - nwp_grids:/data/nwp_grids:rw
   ```
3. In `docker/entrypoint.sh`, extend the existing chown line (currently
   `chown app:app /data/backups /data/artifacts 2>/dev/null || true` at
   line 27) to include the new path:
   ```sh
   chown app:app /data/backups /data/artifacts /data/nwp_grids 2>/dev/null || true
   ```
4. Verify `config.toml:362` references `/data/nwp_grids` as
   `archive_base_path` (pre-confirmed 2026-04-20).
5. Smoke test: `docker compose down -v && docker compose up -d prefect-worker`
   then
   - `docker compose exec prefect-worker stat -c '%U:%G' /data/nwp_grids`
     → `app:app`
   - `docker compose exec prefect-worker touch /data/nwp_grids/.probe &&
      docker compose exec prefect-worker rm /data/nwp_grids/.probe`
     succeeds.

**Exit**: gridded-cycle archive writes land in a named volume that persists
across container restarts and is writable by the `app` user.

### T1b — Size-limited tmpfs for NWP scratch

**File**: `docker-compose.yml`

1. Under `prefect-worker`, use the **long-form** mount syntax (not `tmpfs:`
   short list) because Compose short-form does not accept a `size` option.
   Add under `volumes:` (or under a new top-level `volumes:` list on the
   service, merging with the existing named-volume mounts):
   ```yaml
   - type: tmpfs
     target: /tmp/sapphire_nwp
     tmpfs:
       size: 4294967296   # 4 GiB
       mode: 1023         # decimal for octal 1777 (sticky + rwxrwxrwx)
   ```
   Use the decimal form for `mode`; `0o1777` is Python-style, not valid
   YAML, and bare `01777` relies on deprecated YAML 1.1 octal syntax.
   Keep any existing `tmpfs:` short-form entries (`/tmp`, `/data/cache`)
   untouched — they remain unsized, which is acceptable per D1b.
2. Smoke test: `docker compose exec prefect-worker mount | grep sapphire_nwp`
   shows a tmpfs line with `size=4194304k` (or equivalent representation
   of 4 GiB) and mode `1777`.

**Exit**: NWP conversion scratch space has a hard 4 GiB ceiling; a runaway
cycle cannot fill rootfs.

### T2 — Require `VERSION` to be set

**Files**: `docker-compose.yml`, `.env.example`

1. Replace every `${VERSION:-latest}` (lines 69, 109, 180) with
   `${VERSION:?VERSION is required; set in .env or export before compose up}`.
2. Add `VERSION=0.1.362` (or the current `pyproject.toml:3` version at
   merge time) to `.env.example`.
3. Smoke test: `unset VERSION && docker compose config` must fail with
   "VERSION is required" (fail-fast behaviour).

**Exit**: compose config fails fast when `VERSION` is unset; no path to an
unintentional `:latest` pull.

### T3 — Caddy curl healthcheck on :80

**File**: `docker-compose.yml`

1. Confirm `curl` is present in the `caddy:2` image (it is — verified via
   the upstream `caddyserver/caddy-docker` Dockerfile template which
   installs `curl ca-certificates libcap mailcap`):
   `docker run --rm caddy:2 which curl` → prints `/usr/bin/curl`.
2. Under the `caddy` service, add:
   ```yaml
   healthcheck:
     test: ["CMD", "curl", "-s", "-o", "/dev/null", "--max-time", "5", "http://localhost:80/"]
     interval: 30s
     timeout: 5s
     retries: 3
     start_period: 30s
   ```
   Notes:
   - **Port 80** is Caddy's baseline bind (per `Caddyfile:5`, v0 runs
     plain HTTP; when `SAPPHIRE_DOMAIN` is set Caddy still binds 80 for
     ACME challenges and the HTTPS redirect). 30 s is sufficient — Caddy
     binds ports within seconds of container start; no ACME wait because
     the probe hits 80 (unrelated to TLS provisioning).
   - **`-s` without `-f`** is deliberate. The Caddyfile's sole handler is
     `reverse_proxy api:8000`. With `-f`, a 502 Bad Gateway from a
     misbehaving `api` upstream would mark Caddy unhealthy — but Caddy
     itself is fine. Dropping `-f` makes curl return 0 for any HTTP
     response received (including 502), so the probe fails only when
     Caddy itself is unreachable (connection refused / timeout). This
     correctly scopes the healthcheck to Caddy, not to the upstream.
   - The `depends_on: api: condition: service_healthy` at
     `docker-compose.yml:160-162` already covers cold-start ordering; by
     the time Caddy starts, `api` is healthy, so there is no cold-start
     flap risk.
3. Smoke test: `docker compose ps caddy` shows `(healthy)` within ~1 minute
   of startup. From a host shell: `docker compose exec caddy curl -sf
   http://localhost:80/ -o /dev/null && echo ok`.

**Exit**: caddy has a Docker healthcheck that actually runs inside the
image (no missing binaries) and matches the intent of `cicd.md:23` (which
should be updated in a follow-up to say "HTTP GET on 80" rather than "TCP
check on 443").

### T4 — Fix `PREFECT_UI_URL` default

**File 1**: `docker-compose.yml`

1. Under the `api` service → `environment:`, add:
   ```yaml
   PREFECT_UI_URL: http://prefect-server:4200
   ```
2. Keep `PREFECT_API_URL` as-is.

**File 2**: `src/sapphire_flow/api/__init__.py:13`

3. Keep the `os.environ.get("PREFECT_UI_URL", "http://localhost:4200")` line
   — the fallback is the intended dev default. Add a one-line comment:
   `# localhost fallback is for local dev; production sets PREFECT_UI_URL in compose (Plan 053 D4).`
4. Do NOT remove the fallback — removing it would break local dev without a
   `.env` file.

**Exit**: in-container HTML link rendering uses `prefect-server:4200`;
local dev still works. **Note**: this URL is opened by the end-user's
browser, not by the `api` container. From a host browser the link resolves
only when the dev overlay is active or Caddy fronts the Prefect UI.
Browser-reachability is out of scope for this plan; the scope is HTML
presentation only.

### T5 — Document `prefect-server` root-user exception

**File**: `docs/standards/security.md`

1. Per D5, do not add `user:` in compose (forbidden by `security.md:347`).
2. In `docs/standards/security.md` §Container privilege model, add a short
   subsection "Upstream images running as root" noting:
   - `prefect-server` uses `prefecthq/prefect:3-python3.11`, which has no
     `USER` directive (verified 2026-04-20 via
     `docker run --rm prefecthq/prefect:3-python3.11 id`).
   - Compensating controls: `cap_drop: [ALL]`, no host port binding in the
     base compose file, `backend`-only network after Plan 049 C1.
   - Re-evaluate after Plan 062 establishes the full write-path footprint,
     or if a `-nonroot` upstream tag / community non-root image appears.

Note: `read_only: true` is NOT probed in this plan. Doing so without first
resolving Plan 062 (`PREFECT_HOME` not set, so actual runtime write paths
are unknown) would almost certainly fail. Deferred.

**Exit**: `docs/standards/security.md` §Container privilege model contains
a new subsection "Upstream images running as root" listing `prefect-server`
as the affected service, its compensating controls (`cap_drop: [ALL]`, no
host port binding, `backend`-only network), and Plan 062 as the
re-evaluation trigger. Verified by reading the section.

---

## Dependency graph

```json
{
  "stream-1": {
    "tasks": ["T1", "T1b", "T2", "T3", "T4", "T5"],
    "parallel": "all six in parallel — no file-level conflicts",
    "depends_on": []
  }
}
```

T5 touches only `docs/standards/security.md`; it has no file-level
dependency on the compose or entrypoint edits and can merge in parallel.

---

## Files to modify

| Path | Task | Change |
|---|---|---|
| `docker-compose.yml` | T1, T1b, T2, T3, T4 | Add `nwp_grids` volume + worker mount; add `/tmp/sapphire_nwp` long-form size-limited tmpfs; `VERSION:?...` fail-fast; caddy HTTP-GET-on-:80 healthcheck (curl); `PREFECT_UI_URL` env on `api` |
| `docker/entrypoint.sh` | T1 | Add `/data/nwp_grids` to the chown list |
| `src/sapphire_flow/api/__init__.py` | T4 | One-line comment next to the `PREFECT_UI_URL` fallback |
| `.env.example` | T2 | Add `VERSION=0.1.362` (or current) |
| `docs/standards/security.md` | T5 | Document `prefect-server` upstream-root exception + compensating controls |

---

## Exit gates

1. `docker compose config` succeeds with `VERSION=0.1.362` set and fails
   with `VERSION is required` when unset.
2. `docker compose up -d` followed by `docker compose ps` shows `caddy
   (healthy)` within 90 s of startup.
3. `docker compose exec prefect-worker stat -c '%U:%G' /data/nwp_grids`
   returns `app:app`, and a `touch`/`rm` probe succeeds as the app user.
4. `docker compose exec prefect-worker mount | grep sapphire_nwp` shows a
   tmpfs with `size=4194304k` (the kernel reports tmpfs size in kibibytes;
   4 GiB = 4194304 KiB), mode `1777`.
5. `docker compose exec prefect-server id` returns UID 0 (expected per D5;
   no fix applied) and `security.md` records the exception + compensating
   controls.
6. With `api` running in-container:
   `docker compose exec api curl -sf http://localhost:8000/ | grep
   'prefect-server:4200'` returns the expected dashboard HTML link. (Port
   8000 is not exposed on the host in base compose per `v0-scope §F` —
   the probe must run inside the container, or under the dev overlay.)
   Browser resolvability of the URL is out of scope — this gate validates
   HTML rendering only.
7. Local dev (`docker compose -f docker-compose.yml -f
   docker-compose.dev.yml up`) still starts after the developer adds
   `VERSION=` to their local `.env`.
8. `docs/standards/security.md` updated with the `prefect-server` user
   note.
9. Version bump applied per CLAUDE.md. Land all T1–T5 changes in a single
   squashed commit so `.env.example`'s `VERSION=` value and the tagged
   repo version stay aligned; a patch bump at commit time sets both.

---

## Risks

| Risk | Mitigation |
|---|---|
| T1 entrypoint edit conflicts with an unforeseen Plan 046 change | Verified 2026-04-20: Plan 046 does not touch `docker/entrypoint.sh`. Residual risk is negligible; coordinate only via standard git practice. |
| T1b tmpfs ceiling of 4 GiB too small for future NWP cycles | Current cycles fit well under 4 GiB. If this becomes tight in v0b+, raise via a single compose edit. |
| T2 breaks developer `docker compose up` without `VERSION` in `.env` | `.env.example` update is part of T2. Add a one-liner to `docs/handover/` if such a file exists. The dev overlay does not set `VERSION`, so local dev also requires it. |
| T2 does not pin SHA digest — mutable-tag attacks remain possible | Out of scope for v0. Track as a follow-up security-hardening item. |
| T3 healthcheck flaps on cold start | `start_period: 30s` covers Caddy's typical port-bind latency. Probe targets :80, not :443, so TLS provisioning is unrelated. Raise to 60s if staging rollout shows flapping. |
| T3 `curl` unavailable in `caddy:2` image | Verified present in upstream `caddyserver/caddy-docker` Dockerfile template. Step 1 sanity-checks before committing. |
| Plan 049 C1 simultaneously edits `prefect-server` block | Merge conflict is small (single service block, different keys). T5 touches `security.md` and optionally `read_only:` — coordinate via git. |

---

## Open questions

Not blocking DRAFT → READY:

1. **`nwp_grids` backup strategy and MeteoSwiss retention window**: plan
   defers to Plan 048 (DRAFT, restic). But ICON-CH2-EPS STAC retention is
   rolling — if the `nwp_grids` volume is lost after the MeteoSwiss window
   passes, the archive cannot be regenerated. Action: document the exact
   retention window in `docs/standards/cicd.md` §NWP archive, and decide
   whether to add a lightweight `tar`-to-`backups`-volume step before
   Plan 048 lands. Recommendation: document the window now; defer the
   `tar` until the window is confirmed.

---

## Future work

Tracked here so the follow-ups are not dropped:

- **Update `cicd.md:23`** to replace "TCP check on 443" with "HTTP GET on
  `:80` via `curl -s`" (matches what Plan 053 T3 actually implements).
- **`read_only: true` on `prefect-server`**: deferred pending Plan 062
  (DRAFT). Once `PREFECT_HOME` is correctly wired and the full write
  footprint is enumerated, evaluate `read_only: true` in a follow-up
  security-hardening plan.
- **SHA digest pinning** for container images (not just tag pinning).
  Residual supply-chain risk after T2; revisit as part of a broader
  security-hardening pass.
- **Caddy `/healthz` route**: optionally add `handle /healthz { respond
  "ok" 200 }` to the Caddyfile and point the healthcheck at it. Cleaner
  separation of Caddy health from upstream health, but requires touching
  the Caddyfile and was kept out of scope for this plan.

---

## Revision notes

### 2026-04-20 (revision 3)

Changes vs revision 2, based on round-3 critical review:

- **T1b `mode: 0o1777` → `mode: 1023`** — the Python-style octal literal
  is invalid in YAML/Compose; Compose expects a plain integer.
- **T3 curl flags changed from `-sf` to `-s` (no `-f`) with `--max-time 5`**
  — `-f` would fail on 502 Bad Gateway when `api` is briefly down,
  incorrectly marking Caddy unhealthy. Without `-f`, the probe correctly
  scopes health to Caddy itself.
- **Dependency graph collapsed to a single stream** — T5 only touches
  `security.md` and has no file-level dependency on T1–T4. Prior "stream-2"
  sequencing was cosmetic.
- **"Files to modify" caddy cell** updated from "caddy TCP healthcheck"
  to "caddy HTTP-GET-on-:80 healthcheck (curl)".
- **T5 exit criterion made affirmative** — now specifies what content
  `security.md` gains, not what doesn't get added to compose.
- **Exit gate 4 tmpfs size** updated from `size=4g` to `size=4194304k`
  (how the kernel actually reports it).
- **Open question #2 moved to §Future work** — with the read_only probe
  removed, it was a follow-up, not an unresolved design choice.
- **Added §Future work section** tracking `cicd.md:23` text update,
  `read_only: true` probe, SHA digest pinning, and the optional Caddyfile
  `/healthz` route — all items the plan references but doesn't
  implement, now in one place so they aren't dropped.
- **Plan 048 / 062 labelled `(DRAFT)`** at their cross-references.

### 2026-04-20 (revision 2)

Changes vs revision 1, based on round-2 critical review:

- **T3 healthcheck switched from `nc` to `curl`** — `nc` is not installed
  in `caddy:2` (only `curl`, `ca-certificates`, `libcap`, `mailcap`). The
  probe now targets `:80` (Caddy's baseline bind in v0), not `:443`, which
  also removes the ACME cold-start concern. Uses `curl -s` (no `-f`) so
  that a 502 from a temporarily-down `api` upstream does not mark Caddy
  unhealthy — the probe scopes health to Caddy, not the reverse_proxy
  target.
- **T1b tmpfs rewritten to long-form syntax** — Compose short-form
  `- /tmp/sapphire_nwp:size=4g,mode=1777` silently drops `size` because
  short-form only accepts `mode`, `uid`, `gid`. Long-form `type: tmpfs`
  with `tmpfs.size` is required for the size limit to take effect.
- **Plan 046 coordination warning dropped** — verified 2026-04-20 that
  Plan 046 does not touch `docker/entrypoint.sh`; its sole compose edit is
  adding one env var to `prefect-server`. No file-level conflict with this
  plan.
- **Exit gate 6 rewritten** to use `docker compose exec api curl ...`.
  Port 8000 is not exposed on the host per `v0-scope §F`; the previous
  `curl http://localhost:8000/` from the host would fail.
- **T5 step 3 removed** — the `read_only: true` probe was optional and
  will almost certainly fail until Plan 062 fixes the `PREFECT_HOME`
  mismatch. Deferred to a follow-up security-hardening plan.
- **D5 rationale strengthened** — acknowledges Option A (derived image)
  is feasible but defers on real grounds (unknown Prefect write paths
  pending Plan 062, handover timeline), not on a weak "v0 scope" appeal.
- **Exit gate 9 clarified** — specifies a single squashed commit to keep
  `.env.example` `VERSION=` and the tagged repo version aligned.
- **New related plan (062) cross-referenced** in header and D5 — Plan 062
  addresses the latent `PREFECT_HOME`/`prefect_data` mismatch surfaced
  during this review.

### 2026-04-20 (revision 1)

Changes vs the 2026-04-18 draft, based on round-1 critical-review findings:

- **Dependency statements corrected**: Plan 046 is READY, not DONE;
  Plan 049 is DRAFT, not "C1"; Plan 045 file location clarified (no longer
  under `docs/plans/`).
- **T1 extended** to include `docker/entrypoint.sh` chown edit. Without it
  the new volume is root-owned and app writes fail silently. Verified
  2026-04-20: entrypoint.sh:27 has a hardcoded chown list covering only
  `/data/backups` and `/data/artifacts`.
- **T1b added** for size-limited tmpfs per `cicd.md:43` — pre-existing
  compose gap absorbed into this plan.
- **T2 line numbers corrected**: `${VERSION:-latest}` is at lines 69, 109,
  180 (not 69, 108, 177). `.env.example` `VERSION` value aligned to the
  current `pyproject.toml:3` version (0.1.362 at time of revision).
- **T3 reshaped** from Caddy admin API (port 2019) to TCP check on 443 —
  matches `cicd.md:23` and avoids admin-API lateral-movement surface.
  *(Superseded by R2: `nc` is not installed in `caddy:2`; revision 2
  switched to `curl -s` on port 80.)*
- **T4 exit gate clarified**: UI link is Docker-internal; host-browser
  resolvability is out of scope. The scope is HTML rendering only.
- **T5 reshaped** from discovery-mode to an explicit decision. D5 accepts
  `prefect-server` as root and documents the exception; no compose `user:`
  override is added, per `security.md:347`. Removed the `1002:1002`
  undocumented fallback. Upstream image user verified as UID 0 (2026-04-20).
- **Open question added** re: MeteoSwiss retention window and `nwp_grids`
  backup strategy — the original "regeneratable" assertion was conditional
  on the retention window.
