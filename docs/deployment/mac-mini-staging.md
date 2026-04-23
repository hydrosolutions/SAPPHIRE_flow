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

### CAMELS-CH staging

Plan 060 removed the `sapphire_data:/data/raw` mount from the base
compose. The Mac-mini overlay binds `~/camels-ch` into the
`prefect-worker` read-only instead. Download the dataset (v1.0 or
newer) and extract it so that `~/camels-ch/CAMELS_CH/` exists with
the full time-series tree before bootstrapping.

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
