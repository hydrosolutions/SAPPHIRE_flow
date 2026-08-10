# Plan 147 — Mac-mini rollout runbook

**Target box:** `sapphire@192.168.1.136` (mac-mini staging), executed **in the mini's GUI login
session** (Docker socket + launchctl are scoped to that session — do NOT run this over a
non-interactive SSH; sit at the box or a `screen`/login-session shell).

**What this deploy does (why it is safety-critical):**
1. Flips API **access-token authentication ON** (Plan 147 Slice C). Every route except the public
   `GET /api/v1/health` now demands `Authorization: Bearer <token>` — `src/sapphire_flow/api/__init__.py:83-97`.
2. Moves the app services off the Postgres **owner/superuser** onto scoped least-privilege roles
   `sapphire_api` / `sapphire_worker` (Plan 147 Slice D) — `docker-compose.yml:95-100,199-205`,
   `docker/bootstrap-roles.sql`.
3. Applies **8 new migrations 0040→0047**, incl. the tenant model + `tenant_id` backfill and the
   `access_tokens` table.

**Starting state (verified facts):** mini runs **v0.1.653**, git tree at `6e1a8ef`, 53 commits behind
`main`. Target = `main @ eabec1c` (Plan 147 slices A–E merged), `pyproject.toml:3` version
**0.1.691**. Mini `./secrets/` currently holds ONLY `db_password` + `recap_dg_client_token`.

**Migration safety (verified locally, re-verified in Step 4):** 0000→0047 apply cleanly. 0042/0043/0044
add `tenant_id` **nullable → backfill existing rows onto the seeded `sapphire` tenant → NOT NULL**
(`alembic/versions/0042_stations_tenant_id.py:32-64`), so the mini's live stations 2009/2091 + their
group migrate safely. 0041 seeds the default `sapphire` tenant at the fixed id
`00000000-0000-0000-0000-000000000001` (`alembic/versions/0041_tenants_table.py:29-62`).

---

## 0. Preconditions — secret inventory

Every secret the deploy reads, and whether the mini has it. **The three `MISSING` rows must be staged
in Step 1 before any `up --build`, or the deploy fails closed** (init aborts / api refuses to boot).

| Secret file (`./secrets/…`) | Needed by | Mount | Present on mini? | Cited |
|---|---|---|---|---|
| `db_password` | postgres, prefect-server, **init** (owner) | `/run/secrets/db_password` | ✅ present | `docker-compose.yml:40-41,330-331,358-360` |
| `recap_dg_client_token` | BUILD-time (env-sourced) | build secret | ✅ present | `docker-compose.yml:12-16,387-388` |
| `sapphire_api_db_password` | **api** + **init** | `/run/secrets/sapphire_api_db_password` | ❌ **MISSING — STAGE FIRST** | `docker-compose.yml:205,233,304,368-369` |
| `sapphire_worker_db_password` | **prefect-worker**, **prefect-worker-ingest** + **init** | `/run/secrets/sapphire_worker_db_password` | ❌ **MISSING — STAGE FIRST** | `docker-compose.yml:100,121,159,177,305,370-371` |
| `access_token_pepper` | **api** only (auth + token CLI) | `/run/secrets/access_token_pepper` | ❌ **MISSING — STAGE FIRST** | `docker-compose.yml:234,378-379`; `src/sapphire_flow/api/security.py:44-47` |

Two **host** files (not Docker mounts) also matter for the watchdog:

| Host file (`./secrets/…`) | Purpose | Present? | Cited |
|---|---|---|---|
| `slack_webhook_url` | watchdog Slack alerts (pre-existing) | assume present (unchanged) | — |
| `health_probe_token` | admin access-token raw key the launchd watchdog sends as `Bearer` on the now-authed `/health/detail` BAFU probes | ❌ **MISSING — created in Step 4.5 (after the API is up + a token is minted)** | `docs/standards/cicd.md:704,711` |

Also confirm before starting:
- `docker` + `DOCKER_HOST` on PATH for this login session (`/usr/local/bin/docker`; see MEMORY Mac-mini SSH note).
- USB backup disk mounted at `/Volumes/sapphire-backup` (macmini overlay binds
  `/Volumes/sapphire-backup/pg_dumps → /data/backups`, `docker-compose.macmini.yml:29`).
- `RECAP_DG_CLIENT_TOKEN` will be exported from `secrets/recap_dg_client_token` at build time (Step 3).

> **Note — "superuser drop" is a privilege reduction, not a role deletion.** The Postgres owner role
> (`sapphire`, password = `db_password`) is NOT dropped. Slice D stops the **app** services from
> mounting `db_password` and points them at the scoped roles instead; only `postgres` /
> `prefect-server` / `init` keep the owner credential (`docker-compose.yml:330-333` — init keeps all
> three; api/worker mount only their scoped secret). `sapphire_prefect`/prefect-server against the
> separate `prefect` DB is unchanged by this slice (`docker/bootstrap-roles.sql:13-17`).

---

## 1. Stage the missing prerequisites (⚠️ owner-generated secret values)

Run in the mini's `~/SAPPHIRE_flow`. These generate high-entropy secrets — **do not invent or reuse
values; let `openssl` mint them.** Each is an ⚠️ OWNER DECISION only insofar as the owner must be the
one to generate + safeguard them (they are never shown again once services read them).

```bash
cd ~/SAPPHIRE_flow

# ⚠️ OWNER DECISION — generate the two scoped-role DB passwords (Slice D first-deploy,
# docs/standards/cicd.md:761-763). Fresh random, distinct from db_password.
umask 077
openssl rand -base64 32 > ./secrets/sapphire_api_db_password
openssl rand -base64 32 > ./secrets/sapphire_worker_db_password

# ⚠️ OWNER DECISION — generate the HMAC pepper for access-token hashing (Slice C first-deploy,
# docs/standards/cicd.md:708). The api + token CLI fail closed without it.
openssl rand -base64 32 > ./secrets/access_token_pepper

chmod 600 ./secrets/sapphire_api_db_password ./secrets/sapphire_worker_db_password ./secrets/access_token_pepper
ls -l ./secrets/    # confirm all three exist, non-empty, mode 600
```

**Roles themselves need no manual SQL** — `init` runs `docker/bootstrap-roles.sh` which
`CREATE`s (or `ALTER`s the password of) `sapphire_api` + `sapphire_worker` as the owner, then applies
the per-table grant matrix, idempotently, on every deploy (`docker-compose.yml:288-294`;
`docker/bootstrap-roles.sh:1-37`; `docker/bootstrap-roles.sql`). The two secret files above are the
ONLY thing the operator stages for Slice D. `bootstrap-roles.sh:16-23` fails closed (non-zero exit) if
either scoped-password file is unreadable — so staging them now is mandatory, not optional.

**CORS (⚠️ OWNER DECISION, usually "leave empty"):** the mini is LAN-only plain-HTTP with no
browser consumer (`docker-compose.macmini.yml:12`), so leave `SAPPHIRE_CORS_ORIGINS` **unset/empty**
(default). Do NOT set `"*"` — the API raises `RuntimeError` at startup and refuses to boot
(`src/sapphire_flow/api/__init__.py:43-50`; env var read from `docker-compose.yml:214`). Only set an
explicit comma-separated origin list if a browser dashboard is ever added.

---

## 2. Back up (rollback insurance — take BEFORE touching anything)

Migrations are **forward-only / additive**; there is no schema downgrade
(`docs/standards/cicd.md:145-147`). A pre-deploy logical dump is the rollback path if a migration or
data backfill misbehaves.

```bash
cd ~/SAPPHIRE_flow
mkdir -p ~/sapphire-backups

# 2a. Tag the CURRENT (0.1.653) app image as a rollback anchor — images are local-only, no registry
#     (docs/standards/cicd.md:215,342). Do this BEFORE the rebuild prunes/overwrites it.
docker tag sapphire-flow:0.1.653 sapphire-flow:rollback-0.1.653

# 2b. Logical dump of the live DB as the owner (custom format). PGPASSWORD from the owner secret so
#     this works regardless of pg_hba. postgres container is not read_only, exec is fine.
docker compose -f docker-compose.yml -f docker-compose.macmini.yml \
  exec -e PGPASSWORD="$(cat secrets/db_password)" -T postgres \
  pg_dump -U sapphire -Fc sapphire > ~/sapphire-backups/pre-147-$(date +%F-%H%M).dump

ls -lh ~/sapphire-backups/    # confirm a non-trivial dump size
```

Record the current git commit + version for the rollback note: `git -C ~/SAPPHIRE_flow rev-parse HEAD`
(expect `6e1a8ef`), current `.env` `VERSION` (expect `0.1.653`).

---

## 3. Deploy (in the login session)

Follows the standard upgrade procedure (`docs/standards/cicd.md:135-143`) with BOTH overlays.

```bash
cd ~/SAPPHIRE_flow

# 3a. Fetch the target tree (main @ eabec1c or later; Plan 147 A–E merged).
git fetch origin
git checkout main
git pull --ff-only origin main
git rev-parse HEAD            # confirm eabec1c (or a later main)

# 3b. Pin VERSION in .env to the app version at this commit. ⚠️ confirm the exact value:
grep '^version' pyproject.toml     # expect 0.1.691 at eabec1c
#     Edit ./.env so VERSION=0.1.691 (live hosts pin an explicit x.y.z, not `dev`).
#     (compose fails loud if VERSION is unset — docker-compose.yml:82,151,196,287.)

# 3c. Export the private-clone BUILD token (mandatory before any --build — cicd.md:137).
export RECAP_DG_CLIENT_TOKEN=$(cat secrets/recap_dg_client_token)

# 3d. Quiesce both workers before init re-runs alembic (Plan 098 ordering — cicd.md:139,143).
docker compose -f docker-compose.yml -f docker-compose.macmini.yml \
  stop prefect-worker prefect-worker-ingest

# 3e. Build the new image + run init ONCE: alembic upgrade head → bootstrap-roles.sh → register
#     deployments (docker-compose.yml:288-294). This is where migrations 0040→0047 apply and the
#     scoped roles are created. --build rebuilds sapphire-flow:0.1.691 first (cicd.md:140).
docker compose -f docker-compose.yml -f docker-compose.macmini.yml \
  run --rm --build init
#     ⤷ EXPECT the tail to print "Init complete". If bootstrap-roles.sh aborts, a scoped-password
#       secret is missing/unreadable — fix Step 1 and re-run (idempotent).

# 3f. Bring the whole stack up on the freshly built image (no second build).
docker compose -f docker-compose.yml -f docker-compose.macmini.yml up -d
```

**Ordering guarantee (why this is safe):** `init` runs `alembic upgrade head` (→ 0047, creates
`access_tokens`) **then** `bootstrap-roles.sh` (creates the roles the app connects as) **before**
`api`/workers start — they `depends_on: init: service_completed_successfully`
(`docker-compose.yml:111-112,167-168,218-219`). The `api` lifespan then loads the pepper fail-closed
(`src/sapphire_flow/api/deps.py:14-21`). Tokens are minted **after** the API is up (Step 4.5), so the
sequence is: **migrate (0047) → roles → authed API boots → mint tokens**.

---

## 4. Verify

```bash
cd ~/SAPPHIRE_flow
C="docker compose -f docker-compose.yml -f docker-compose.macmini.yml"
```

> **⚠️ CRITICAL exec caveat — read before running any `exec api …` below.** `docker compose exec`
> does NOT run the image ENTRYPOINT (`Dockerfile:89` `ENTRYPOINT ["/entrypoint.sh"]`), so the
> `DATABASE_URL` that `entrypoint.sh` builds from `DATABASE_URL_TEMPLATE` + the DB-password secret is
> **absent** in a plain `exec` session. Both `alembic` (`alembic/env.py:14`) and the token CLI
> (`create_engine_from_env` → `os.environ["DATABASE_URL"]`, `src/sapphire_flow/db/engine.py:9`) read
> `DATABASE_URL` directly and will **`KeyError`/fail** under a bare
> `docker compose exec api python -m …`. **Wrap every in-container app command through the entrypoint:**
> `$C exec api /entrypoint.sh <cmd>` — this reconstructs `DATABASE_URL` (as the `sapphire_api` role for
> the `api` service, `docker-compose.yml:204-205`) and drops to the `app` user before running `<cmd>`.
> (This corrects the mint command as written in `docs/standards/cicd.md:710` and
> `src/sapphire_flow/cli/access_tokens.py:6`, which omit the `/entrypoint.sh` wrapper — see Flags.)
> The `psql` verifications via the `postgres` service below are unaffected (they pass `-U` + `PGPASSWORD`
> explicitly and never read `DATABASE_URL`).

**4.1 — all services healthy**
```bash
$C ps      # postgres/prefect-server/api/caddy/prefect-worker(-ingest) Up; init Exited(0)
```

**4.2 — alembic reached 0047** (re-verify the migration head on the live DB)
```bash
$C exec -T api /entrypoint.sh alembic current    # expect: 0047 (head)
```

**4.3 — tenant seeded + existing stations backfilled** (no operator tenant action was needed)
```bash
$C exec -e PGPASSWORD="$(cat secrets/db_password)" -T postgres \
  psql -U sapphire -d sapphire -c \
  "SELECT code FROM tenants WHERE id='00000000-0000-0000-0000-000000000001';"   # → sapphire
$C exec -e PGPASSWORD="$(cat secrets/db_password)" -T postgres \
  psql -U sapphire -d sapphire -c \
  "SELECT count(*) FROM stations WHERE tenant_id IS NULL;"                       # → 0
```
(Seeded by `0041_tenants_table.py:53-62`; backfilled by `0042_stations_tenant_id.py:53-58` /
0043 / 0044.)

**4.4 — scoped roles exist, NOT superusers** (Slice D landed)
```bash
$C exec -e PGPASSWORD="$(cat secrets/db_password)" -T postgres \
  psql -U sapphire -d sapphire -c "\du sapphire_api sapphire_worker"
#   Both present; Superuser / Create role / Create DB all UNSET (cicd.md:766-767).
```

**4.5 — API rejects unauthenticated + accepts a minted token.** First mint the watchdog admin token
(this token also becomes `health_probe_token`). Minting REQUIRES 0047 present + pepper loaded + the
`sapphire_api` role (the CLI connects as `sapphire_api` inside the `api` container — it has
`GRANT INSERT,UPDATE ON access_tokens` + `INSERT ON access_token_stations, audit_log`,
`docker/bootstrap-roles.sql:132-134`).

```bash
# Public health is OPEN (200) — no bearer:
curl -sf http://localhost:8000/api/v1/health && echo "  <- health OK (public)"

# A protected route must 401 WITHOUT a token (api_stations is require_principal-gated,
# src/sapphire_flow/api/__init__.py:95):
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/api/v1/stations   # → 401

# ⚠️ OWNER DECISION — mint the admin token for the watchdog. Prints the raw key ONCE
# (cli/access_tokens.py:286-289). create-admin is ALWAYS unscoped/global (no --tenant).
# NOTE the /entrypoint.sh wrapper (see the CRITICAL exec caveat above) — a bare
# `exec api python -m …` KeyErrors on DATABASE_URL.
$C exec api /entrypoint.sh python -m sapphire_flow.cli.access_tokens create-admin --name "watchdog-probe"
#   ⤷ copy the printed raw key → <RAW_KEY> below. It is never shown again.

# Confirm the token authenticates against the admin-gated detail route:
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer <RAW_KEY>" http://localhost:8000/api/v1/health/detail  # → 200
```

Then wire the watchdog (its BAFU probes hit the now-authed `/health/detail`, cicd.md:704,711-712):
```bash
printf '%s' '<RAW_KEY>' > ./secrets/health_probe_token && chmod 600 ./secrets/health_probe_token
# Restart / re-trigger the watchdog launchd agent so read_probe_token picks it up (cicd.md:712).
```

**4.6 — worker connects as the non-superuser role**
```bash
$C exec -e PGPASSWORD="$(cat secrets/db_password)" -T postgres \
  psql -U sapphire -d sapphire -c \
  "SELECT usename FROM pg_stat_activity WHERE datname='sapphire' AND usename LIKE 'sapphire_%';"
#   Expect sapphire_worker (and sapphire_api) among active connections — NOT the bare owner for the app.
$C logs prefect-worker --tail 30    # no auth/permission errors; worker claims the default pool
```

**4.7 — snow ingest schedule is a benign no-op** (Plan 146; mini has no snow-bound station). The
`ingest-recap-reanalysis` deployment is registered (`cli/register_deployments.py:120-125`); when no
station is bound to the recap reanalysis source the flow logs `recap_snow_reanalysis.no_stations`,
appends a `PipelineHealthStatus.OK` record with `reason=no_stations_bound`, and returns 0/0/0 without
touching any Recap key/adapter (`src/sapphire_flow/flows/ingest_recap_reanalysis.py:432-446`).
```bash
$C exec -T api python -c "from prefect.client.orchestration import get_client" 2>/dev/null; \
  echo "check the Prefect UI: deployment 'ingest-recap-reanalysis' present; first run = OK no-op"
```

---

## 5. Rollback (revert to 0.1.653)

Migrations are forward-only and additive, so a pre-Slice-D image runs fine against the new schema
(`docs/standards/cicd.md:145-147,790-796`); the roles/grants are additive and safe to leave in place.
**A DB restore from Step 2b is only needed if a migration/backfill corrupted data** — for a pure
"new code misbehaves" rollback, the image pin alone is enough.

**Code/image rollback (default):**
```bash
cd ~/SAPPHIRE_flow
C="docker compose -f docker-compose.yml -f docker-compose.macmini.yml"
$C stop prefect-worker prefect-worker-ingest
git checkout 6e1a8ef                       # the mini's prior tree
# Set .env VERSION=0.1.653 so compose reuses the rollback-tagged image:
docker tag sapphire-flow:rollback-0.1.653 sapphire-flow:0.1.653   # if the tag was pruned
export RECAP_DG_CLIENT_TOKEN=$(cat secrets/recap_dg_client_token)
$C up -d
```
The pre-Slice-D `entrypoint.sh` falls back to `/run/secrets/db_password` (owner) when
`DB_PASSWORD_SECRET` is unset, so the app connects as the owner again with no role changes needed
(`cicd.md:794-796`; `docker/entrypoint.sh:10`). Leave `sapphire_api`/`sapphire_worker` in place.

**Full DB restore (only if data is corrupt):**
```bash
# Stop the app, restore the custom-format dump into a clean DB, then redeploy the old image.
$C stop api prefect-worker prefect-worker-ingest
$C exec -e PGPASSWORD="$(cat secrets/db_password)" -T postgres \
  pg_restore -U sapphire -d sapphire --clean --if-exists < ~/sapphire-backups/pre-147-<stamp>.dump
# then the code/image rollback block above.
```

**Note:** the minted access tokens + the new pepper/role secrets can stay on disk after a rollback —
the old image simply ignores them (it neither mounts the pepper nor reads the scoped-role secrets).

---

## Open questions / owner decisions (collected)

1. ⚠️ **`access_token_pepper` value** — Step 1. Owner generates via `openssl rand -base64 32`; safeguard
   it (losing it invalidates every token). Do not reuse the dev pepper.
2. ⚠️ **`sapphire_api_db_password` + `sapphire_worker_db_password` values** — Step 1. Owner-generated,
   distinct from `db_password`.
3. ⚠️ **Which tokens to mint** — Step 4.5. The **watchdog admin token** is REQUIRED (the launchd
   watchdog's `/health/detail` probes now need `Bearer`). Any **consumer** tokens are optional for the
   headless v1.0 mini; if the owner wants a station-scoped consumer key, use
   `$C exec api /entrypoint.sh python -m sapphire_flow.cli.access_tokens create --name <n> --tenant sapphire --station <STATION_UUID> [--expires-days N]`
   (`cli/access_tokens.py:197-209`; note the `/entrypoint.sh` wrapper). Consumer tokens need the station **UUIDs** (not BAFU codes
   2009/2091) — look them up first: `... psql -c "SELECT id,name FROM stations;"`.
4. ⚠️ **Token expiry** — default 365 days (`cli/access_tokens.py:49,209,222`); override with
   `--expires-days` if a different rotation cadence is wanted.
5. ⚠️ **`SAPPHIRE_CORS_ORIGINS`** — leave EMPTY for the LAN-only mini (recommended). Only set an
   explicit origin list if a browser consumer is added; `"*"` is rejected at boot.
6. ⚠️ **`.env VERSION`** — confirm it matches `pyproject.toml` at the pulled commit (0.1.691 at
   eabec1c) before Step 3f.

---

## Could-not-determine / flags for human verification

- **The documented token-mint command omits the entrypoint wrapper (safety-relevant).**
  `docs/standards/cicd.md:710` and `src/sapphire_flow/cli/access_tokens.py:6` both say
  `docker compose exec api python -m sapphire_flow.cli.access_tokens …`. But `docker compose exec` does
  NOT run the image ENTRYPOINT (`Dockerfile:89`), and the CLI reads `DATABASE_URL` directly
  (`db/engine.py:9`), which only `entrypoint.sh` constructs from `DATABASE_URL_TEMPLATE`
  (`docker/entrypoint.sh:20-23`). As written, that command will fail with `KeyError: 'DATABASE_URL'`.
  This runbook wraps every `exec api …` app command through `/entrypoint.sh` (Steps 4.2, 4.5, and the
  consumer-token command). **Verify on the box** — if the maintainers' shipped path somehow injects
  `DATABASE_URL` another way (I found none in `docker-compose.yml`'s `api` env block, which sets only
  `DATABASE_URL_TEMPLATE`), the plain form may also work; the `/entrypoint.sh` form is the safe
  superset either way. Same caveat applies to `alembic current` in Step 4.2.
- **Watchdog code path for `/health/detail` auth.** The runbook wires `health_probe_token` per
  `docs/standards/cicd.md:704,711-712`, but I did NOT read `ops/watchdog.py` in this pass to confirm it
  actually sends the `Bearer` header on the detail probe and reads `./secrets/health_probe_token`.
  **Verify `sapphire_flow.ops.watchdog` (read_probe_token / the probe request) before relying on
  Step 4.5's watchdog wiring** — if the watchdog does not yet send the token, its BAFU freshness
  probes will 401 after this deploy until the watchdog code/plist is updated.
- **Exact launchd restart command** for the watchdog agent on the mini is deployment-specific
  (`launchctl kickstart`/`bootout`+`bootstrap` on the login-session domain) and is not pinned in the
  repo files I read — the operator uses the mini's existing watchdog-agent label.
- **`.env` on the mini** — I read the repo `.env` (VERSION=dev, dev machine). The mini's `.env` is not
  in the repo; confirm on the box that VERSION is currently `0.1.653` and set it to `0.1.691`.
