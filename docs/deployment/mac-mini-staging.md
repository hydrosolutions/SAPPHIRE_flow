# Mac-mini Staging Deployment

Operator runbook for running SAPPHIRE Flow v0 on a dedicated Apple
Silicon Mac mini. Target audience: the IT specialist doing the
install, plus any hydrologist or ML expert who needs to reach the
staging API from a team laptop.

Paired plan: `docs/plans/046-mac-mini-staging-deployment.md`
(Stream C + D).

## TL;DR

```bash
cd ~ && git clone https://github.com/hydrosolutions/SAPPHIRE_flow.git
cd SAPPHIRE_flow
./scripts/bootstrap-mac-mini.sh
```

The bootstrap script handles everything except the three things
macOS / the hardware won't let a script do:

1. Install **Docker Desktop** from
   <https://www.docker.com/products/docker-desktop/>, accept the
   licence, and launch it once so the daemon is running.
2. Attach the **external USB SSD** and initialise it so that
   `/Volumes/sapphire-backup/pg_dumps/.sapphire-backup-volume`
   exists (the sentinel the backup flow checks for).
3. Enable **automatic login** for the `sapphire` user in
   System Settings -> Users & Groups -> Automatic Login.

The script detects each of these, reports what's missing, and exits
with clear remediation instructions. Re-run it after every fix.

## Prerequisites

### Hardware

- Mac mini, Apple Silicon (M2 or newer), >= 16 GB RAM, >= 1 TB SSD.
- External USB SSD >= 500 GB, APFS-formatted, mounted at
  `/Volumes/sapphire-backup`.
- UPS (>= 600 VA) strongly recommended. macOS supports UPS shutdown
  signalling via System Settings -> Battery -> UPS.

### macOS

- macOS 14 (Sonoma) or newer.
- Verify Apple Silicon: `uname -m` -> `arm64`.
- Disable auto-updates for macOS and Docker Desktop for unattended
  runs. Re-evaluate before planned maintenance windows.
  - System Settings -> General -> Software Update -> uncheck
    "Install macOS updates" and "Install application updates".
  - Docker Desktop -> Settings -> Software Updates -> uncheck
    "Automatically check for updates".

### Docker Desktop resource config

- RAM: >= 16 GB
- CPUs: >= 8
- Virtual disk: >= 100 GB

Set these in Docker Desktop -> Settings -> Resources before first run.

### User + paths

- User: `sapphire`
- Hostname: `sapphire-staging.local`
- Repo path: `/Users/sapphire/SAPPHIRE_flow` (LaunchAgent plists
  reference this exact path).
- Login credentials for the `sapphire` macOS account live in the
  project OneDrive under `admin/11_secrets` (restricted team access).
  Never commit the password itself — this runbook records only its
  location.

### CAMELS-CH staging

Plan 060 removed the `sapphire_data:/data/raw` mount from the base
compose. The Mac-mini overlay binds `~/camels-ch` into the
`prefect-worker` **read-only** (`/Users/sapphire/camels-ch:/data/raw:ro`)
instead, so the worker cannot download into it — the dataset must be
**pre-staged on the host** at `~/camels-ch/CAMELS_CH/` before
bootstrapping (the worker reads it at `/data/raw/CAMELS_CH`).

Download it with the project's own `camelsch` library, which fetches
the ~1.5 GB dataset from Zenodo (record `7784632`, Höge et al. 2023)
and lays out the directory tree the onboarding flow expects:

```bash
CA="$(uv run --no-project --with certifi python -c 'import certifi; print(certifi.where())')"
export SSL_CERT_FILE="$CA" REQUESTS_CA_BUNDLE="$CA"
uv run --no-project --with camelsch \
  python -c "import camelsch; print(camelsch.download_camels_ch(dest='/Users/sapphire/camels-ch/CAMELS_CH'))"
```

`--no-project --with camelsch` installs only `camelsch` and its
prebuilt-wheel deps into a throwaway environment. A plain `uv run`
would sync the whole project, which builds `exactextract` from an
arm64 source dist needing cmake/libgeos via Homebrew — unavailable to
the unprivileged `sapphire` user on this host (see "Host notes").

The first two lines are **required on this host**: uv-managed
(standalone) Python ships its own OpenSSL and does **not** read the
macOS system CA roots, so `camelsch`'s download fails with
`SSLCertVerificationError: unable to get local issuer certificate`.
Pointing `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE` at `certifi`'s bundle
fixes it (covers both `urllib`/OpenSSL and `requests` clients). The
exports persist for the rest of the shell session. If you instead see
this error from a *network* (a TLS-inspecting proxy re-signing with
its own root CA), `certifi` won't help — add the org root CA;
`curl -sSI https://zenodo.org/records/7784632` succeeding rules that
out.

Verify before bootstrapping:

```bash
ls ~/camels-ch/CAMELS_CH/        # dataset tree present
du -sh ~/camels-ch               # ~1.5 GB
```

## Install

```bash
cd /Users/sapphire/SAPPHIRE_flow
./scripts/bootstrap-mac-mini.sh
```

Pin the release with `VERSION=v0.1.402 ./scripts/bootstrap-mac-mini.sh`
for production. If `VERSION` is unset it defaults to `latest` and the
script warns.

Flags:

- `--dry-run` — print each intended command with `would run:`; make
  no changes. Useful for verifying the plan on a dev machine.
- `--uninstall` — bootout the two LaunchAgents and `docker compose
  down` the stack. Leaves `secrets/` and LaunchAgent plist files in
  place so re-install is fast.
- `--help` — show usage.

### Build-time secret — `RECAP_DG_CLIENT_TOKEN`

Any image build (a first `up -d --build`, or a rebuild after an image
prune) clones the private `hydrosolutions/recap-dg-client` dependency
during `uv sync`, which needs a read-scoped GitHub token. Export it
before building:

```bash
export RECAP_DG_CLIENT_TOKEN=$(cat secrets/recap_dg_client_token)
docker compose -f docker-compose.yml -f docker-compose.macmini.yml up -d --build
```

The base `docker-compose.yml` declares `recap_dg_client_token` as an
env-sourced build secret and passes it into the four building services
(`prefect-worker`, `prefect-worker-ingest`, `api`, `init`), so plain
`docker compose ... up -d --build` now clones the private dependency —
the old manual `docker build --secret id=recap_dg_client_token,env=RECAP_DG_CLIENT_TOKEN .`
pre-build is no longer required (it stays a valid fallback). The token
is never stored in a repo file; the host must supply it (keep it in
`secrets/recap_dg_client_token`, which is git-ignored, or the CI secret
store). The launchd `up -d` wrapper reuses the already-built
`sapphire-flow:${VERSION}` image and does not build, so it needs no
token at boot.

## What the bootstrap does

1. **Arch check** — aborts if not `arm64`.
2. **Docker Desktop presence** — refuses to continue if the daemon
   isn't running.
3. **Homebrew + uv** — installs both if missing (via `brew`).
4. **Repo path check** — warns if not at `/Users/sapphire/SAPPHIRE_flow`.
5. **Secrets** — `mkdir -p secrets && chmod 700`; generates
   `secrets/db_password` with `openssl rand -base64 32` if absent;
   reports whether `secrets/slack_webhook_url` is configured.
6. **USB disk** — verifies
   `/Volumes/sapphire-backup/pg_dumps/.sapphire-backup-volume` exists.
7. **CAMELS-CH** — verifies `~/camels-ch` exists and is non-empty.
8. **VERSION** — defaults to `latest` if unset (with a warning).
9. **Compose up** — `docker compose -f docker-compose.yml -f
   docker-compose.macmini.yml up -d`.
10. **Health wait** — polls `http://localhost:8000/api/v1/health`
    every 5 s for up to 300 s; breaks when `.status == "ok"`.
11. **LaunchAgent install** — copies the two plists to
    `~/Library/LaunchAgents/` and `launchctl bootstrap`s them.
12. **Summary** — prints stack URLs, health status, LaunchAgent
    listing, next steps.

## Host notes — `sapphire-staging.local` (as-built)

What was actually done on the physical mini, where it diverged from
the generic steps above. Keep this current so the host's real state is
auditable.

### Shared-account Homebrew (resolved 2026-06-22)

Homebrew was already installed on this mini under a **different admin
account** (`sandrohunziker`) and owns `/opt/homebrew`. Running
`brew install …` as `sapphire` fails with *"/opt/homebrew/Cellar is
not writable"*. We deliberately did **not** `chown` the prefix to
`sapphire` — that only moves the breakage to the other user (one Unix
owner per Homebrew prefix). Instead we sidestepped brew entirely, which
the bootstrap supports natively:

- **Docker Desktop** — installed from the `.dmg`
  (<https://www.docker.com/products/docker-desktop/>), *not*
  `brew install --cask docker`. Bootstrap step 2 only checks
  `command -v docker`; it never invokes brew for Docker.
- **uv** — installed via the standalone installer
  (`curl -LsSf https://astral.sh/uv/install.sh | sh`) into
  `~/.local/bin`, *not* `brew install uv`. Ensure `~/.local/bin` is on
  `PATH` (add to `~/.zshrc`) so bootstrap step 3 finds it.

Net effect: with `docker` and `uv` both already on `PATH`, bootstrap
step 3 reports "Homebrew present" / "uv present" and performs **zero**
brew writes — the shared Homebrew under `sandrohunziker` is left
untouched.

### Docker Desktop is per-user

Docker Desktop on macOS initializes per-user. The `sapphire` account
must `open -a Docker` once and complete the first-run privileged-helper
prompt; only then does the `desktop-linux` CLI context and the per-user
socket (`~/.docker/run/docker.sock`) exist. Until that happens,
`docker context ls` shows only `default` (which targets
`/var/run/docker.sock`) and `docker ps` returns *permission denied*.
The first-run helper install needs admin rights — if `sapphire` is a
Standard user, grant it temporary admin (System Settings → Users &
Groups), complete Docker Desktop setup, then demote if desired.

## LaunchAgents

Two agents, user-context (`gui/$(id -u)`):

- **`ch.hydrosolutions.sapphire`** — runs
  `scripts/launchd/start-sapphire.sh` at login. The wrapper waits up
  to 240 s for Docker Desktop to expose its socket (cold-boot on
  Apple Silicon can take 90–120 s for VirtioFS + Linux VM init), then
  `docker compose up -d` with the macmini overlay. `KeepAlive =
  { SuccessfulExit = false }` + `ThrottleInterval = 60` means launchd
  retries after 60 s only if the wrapper exits non-zero (i.e. Docker
  Desktop never came up); it does NOT relaunch after a clean
  `up -d` exit.
- **`ch.hydrosolutions.sapphire-watchdog`** — runs
  `uv run python -m sapphire_flow.ops.watchdog` every 300 s. Probes
  `/api/v1/health`, checks `pg_dumps/*.dump` mtimes against a 26 h
  threshold, posts Slack alerts with hysteresis (1st failure, every
  6th thereafter, and recovery). Without `secrets/slack_webhook_url`
  the watchdog runs log-only.

### Watchdog log rotation (manual, one-time)

`newsyslog.d` is a system directory, so this step cannot be scripted:

```bash
sudo cp scripts/launchd/newsyslog-sapphire-watchdog.conf \
    /etc/newsyslog.d/sapphire-watchdog.conf
sudo chown root:wheel /etc/newsyslog.d/sapphire-watchdog.conf
sudo chmod 644 /etc/newsyslog.d/sapphire-watchdog.conf
```

Rotation: 7 keeps, 1 MB size threshold, bzip2 compression.

## Post-install verification

From the Mac mini itself:

```bash
curl -s http://localhost:8000/api/v1/health | jq .
# {"status": "ok", "prefect_status": "ok", "checked_at": "..."}

launchctl list | grep hydrosolutions
# ch.hydrosolutions.sapphire          0  -
# ch.hydrosolutions.sapphire-watchdog 0  -

docker compose -f docker-compose.yml -f docker-compose.macmini.yml ps
# all services healthy
```

From a team laptop on the office LAN, via SSH tunnel (see below):

```bash
curl -s http://localhost:8010/api/v1/health \
    | jq -e '.status == "ok" and .prefect_status == "ok"'
# true

# Prefect UI: http://localhost:4200 in your browser (also via tunnel)
```

## LAN access (SSH tunnel)

### Network posture

The Mac mini staging stack is LAN-only and serves plain HTTP on the
host. There is no caddy TLS service in this overlay per Plan 046 D1;
public HTTPS is owned by Plan 049. Use the SSH tunnel below for team
laptop access.

On each team laptop, add to `~/.ssh/config`:

```ssh-config
Host sapphire-staging
    HostName sapphire-staging.local
    User sapphire
    LocalForward 8010 localhost:8000
    LocalForward 4200 localhost:4200
```

Then:

```bash
ssh -N sapphire-staging &
curl -s http://localhost:8010/api/v1/health | jq .
open http://localhost:4200   # Prefect UI
```

Ad-hoc (without the SSH config stanza):

```bash
ssh -N -L 8010:localhost:8000 -L 4200:localhost:4200 \
    sapphire@sapphire-staging.local
```

## Dedicated host

The Mac mini is a dedicated SAPPHIRE staging host. Do not run other
projects, personal Docker stacks, or browser sessions on it — port
conflicts, resource starvation, and forgotten-tab battery drain
have all cost us runs in the past.

## Forecast-cycle NWP mode

The Mac mini runs the `forecast-cycle` deployment with NWP enabled.
`docker-compose.macmini.yml` sets
`SAPPHIRE_CONFIG_OVERLAY=/app/config/overlays/mac-mini.toml` and
`SAPPHIRE_REQUIRE_NWP=1` for the `prefect-worker` service only, and
bind-mounts that overlay read-only there. The overlay sets:

```toml
enable_observation_alerts = true

[adapters.weather_forecast]
enabled = true
```

`SAPPHIRE_REQUIRE_NWP=1` makes a disabled weather-forecast adapter a
startup-time configuration error for the forecast worker. The ingest
worker uses the same overlay without that environment guard.

`/data/nwp_grids` is a named Docker volume; `docker/entrypoint.sh`
chowns it to `app:app` on container start, so no host `chown` is needed.
`/tmp/sapphire_nwp` is a 4 GiB sticky tmpfs from the base compose file,
writable by the non-root `app` user and ephemeral per container. Verify
both paths from the running worker with:

```bash
docker compose exec -u app prefect-worker sh -c 'touch /data/nwp_grids/.w /tmp/sapphire_nwp/.w && echo ok && rm /data/nwp_grids/.w /tmp/sapphire_nwp/.w'
```

Run Plan 100 administration checks from the worker environment with
`scripts/plan100_forecast_feed_resilience.py` before and after priority
reconciliation. The reconciliation subcommand is dry-run by default; use
`--apply --backup-reference <snapshot> --maintenance-mode-confirmed` only after:

1. The immutable Step 0 snapshot has been captured.
2. `forecast-cycle` and onboarding/model-onboarding deployments are paused.
3. Active forecast/onboarding runs have drained or been intentionally stopped.
4. A fresh database backup reference is recorded.

The script enforces the explicit maintenance-mode confirmation flag, but it
does not pause Prefect deployments itself. Treat the priority reconciliation,
floor audit/backfill verification, and fallback-alert audit as operator-run
deployment checks, not automated application gates.

Plan 100 operator visibility:

- Check `/api/v1/health/detail` or `/health/detail/` for recent
  `pipeline_health` records such as stale NWP grids, dark station
  forecasts, or fallback-only forecast-alert suppression.
- Forecast and model-assignment dashboard surfaces display `skill` /
  `fallback` model-tier badges derived from model ID, not assignment
  priority.
- Station detail displays `no_floor` when no active
  `climatology_fallback` artifact is present. This is a derived badge,
  not a station status or schema column.

## Troubleshooting

### "Docker Desktop did not start within 240s" in `sapphire-flow.log`

Docker Desktop's VirtioFS layer occasionally hangs on cold boot.

1. `open -a Docker` from Terminal (or the Dock icon).
2. Wait for the green "running" indicator.
3. `launchctl kickstart -k gui/$(id -u)/ch.hydrosolutions.sapphire`
   to retry the main agent immediately (otherwise it retries after
   the 60 s throttle).

### USB disk not detected

```bash
ls /Volumes/sapphire-backup/pg_dumps/.sapphire-backup-volume
# ls: ...: No such file or directory
```

Reattach the USB SSD. If macOS didn't automount it:

```bash
diskutil list
diskutil mount /dev/disk<N>s<M>    # from the listing above
mkdir -p /Volumes/sapphire-backup/pg_dumps
touch /Volumes/sapphire-backup/pg_dumps/.sapphire-backup-volume
```

Then restart the stack:

```bash
docker compose -f docker-compose.yml -f docker-compose.macmini.yml \
    up -d --force-recreate prefect-worker
```

### Stack won't come up

```bash
docker compose -f docker-compose.yml -f docker-compose.macmini.yml logs
```

Common causes:

- `secrets/db_password` missing or empty — re-run bootstrap.
- `~/camels-ch` missing — re-extract CAMELS-CH.
- Port 8000 already in use — stop the other process or reboot.
- Docker Desktop running low on RAM/disk — bump the resource limits.

### LaunchAgent isn't firing

```bash
launchctl list | grep hydrosolutions        # is it loaded?
launchctl print gui/$(id -u)/ch.hydrosolutions.sapphire | head -40
tail -50 ~/Library/Logs/sapphire-flow.log
```

To force a reload:

```bash
launchctl bootout gui/$(id -u)/ch.hydrosolutions.sapphire \
    ~/Library/LaunchAgents/ch.hydrosolutions.sapphire.plist
./scripts/launchd/install-launchd.sh
```

### Watchdog isn't alerting

```bash
tail -100 ~/Library/Logs/sapphire-watchdog.log
```

Look for `pipeline.health_check_completed` every 5 min. If you see
`watchdog.slack_skipped_log_only`, Slack isn't wired up:

```bash
echo 'https://hooks.slack.com/services/...' > secrets/slack_webhook_url
chmod 600 secrets/slack_webhook_url
launchctl kickstart -k gui/$(id -u)/ch.hydrosolutions.sapphire-watchdog
tail -f ~/Library/Logs/sapphire-watchdog.log
```

You can simulate a failure by stopping the API:

```bash
docker compose -f docker-compose.yml -f docker-compose.macmini.yml \
    stop api
# wait 5 min for the next watchdog tick; Slack alert should arrive
```

### Direct-invoke flows (Plan 060 D4)

Some flows can't be triggered through the Prefect UI because their
inputs are not JSON-serialisable (`MeteoSwissNwpAdapter`,
`forcing_source`, model objects). Invoke via:

```bash
docker compose -f docker-compose.yml -f docker-compose.macmini.yml \
    exec -T prefect-worker python -c "<flow_invocation>"
```

Canonical templates:

- `forecast-cycle` — see `docs/plans/060-...md` §T4.
- `onboard-model` (non-empty) — TODO: add template here once the
  adapter-registry pattern is decided.
- `train-models` (non-empty) — TODO: same.

## Upgrade procedure

```bash
cd /Users/sapphire/SAPPHIRE_flow
git pull
./scripts/bootstrap-mac-mini.sh
```

The script is idempotent: it re-runs compose `up -d` (which picks up
the new image) and re-bootstraps the LaunchAgents to apply any plist
changes.

For a version-pinned upgrade:

```bash
VERSION=v0.1.410 ./scripts/bootstrap-mac-mini.sh
```

## Uninstall

```bash
./scripts/bootstrap-mac-mini.sh --uninstall
```

Boots out both LaunchAgents and runs `docker compose down`. Leaves
secrets and LaunchAgent plist files in place. For a full wipe:

```bash
rm -f ~/Library/LaunchAgents/ch.hydrosolutions.sapphire*.plist
rm -rf ~/SAPPHIRE_flow/secrets
```

## Cross-references

- Operational validation: `docs/plans/046-mac-mini-staging-deployment.md`
  Stream D.
- v0 scope + simplifications: `docs/v0-scope.md`.
- Secrets model: `docs/standards/security.md` § Secrets management
  (Slack is a host-process secret, not a Docker secret).
- Compose topology: `docs/standards/cicd.md` § Config overlays.
- Logging conventions: `docs/standards/logging.md`.
