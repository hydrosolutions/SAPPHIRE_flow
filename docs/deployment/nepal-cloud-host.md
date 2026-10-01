# Nepal cloud host — first deployment (Plan 511 T2)

Host: `nepal-staging.hydrosolutions.ch` (Infomaniak Public Cloud, Ubuntu 24.04, Docker). This is a
**staging** host; production stays with the owner. The full runbook (update, restart, rotate keys, take
offline) is Plan 511 T4 and extends this page.

Stack: `docker-compose.yml` + `docker-compose.nepal-cloud.yml`. Only Caddy publishes ports (80, 443);
it forwards `/api/v1/` and answers 404 to every other path. The Swiss BAFU collectors are not registered
(`SAPPHIRE_SKIP_DEPLOYMENTS`), and the seeded `sapphire` tenant has no write authority.

## 1. On the host, once

```bash
git clone <repo> ~/SAPPHIRE_flow && cd ~/SAPPHIRE_flow      # then check out the release tag
cat > .env <<EOF_ENV
VERSION=<the release version you are deploying>
SAPPHIRE_DOMAIN=nepal-staging.hydrosolutions.ch
EOF_ENV
```

## 2. Secrets (`./secrets/`)

Generate each on the host; none of them passes through a chat or a ticket. Independent random value per file.

On Linux a bind-mounted secret file keeps its host owner and mode, and the containers drop capabilities, so a
container user can read a secret only if it owns it or the file is readable by others. The files are therefore
root-owned and readable (644), and **the directory** keeps other host users out: `750 root:docker` (members of
the `docker` group are already root-equivalent on this host).

```bash
sudo install -d -m 750 -o root -g docker secrets
for f in db_password sapphire_api_db_password sapphire_worker_db_password \
         sapphire_backup_db_password access_token_pepper; do
  [ -e "secrets/$f" ] || openssl rand -base64 32 | tr -d '\n' | sudo tee "secrets/$f" >/dev/null
done
sudo chown root:root secrets/* && sudo chmod 644 secrets/*
ls -ln secrets/        # five files, owner 0, mode 644
```

**Verify on the first boot** (section 3) that every service starts; if one reports it cannot read a secret,
check `ls -ln secrets/` and the container log, then correct this section. Keep an out-of-band copy of
`db_password` in the password manager: losing it after data exists means restoring from backup.

Build-time tokens (private repositories) are read from the environment while building and must never reach
shell history or disk. Read them with a silent prompt, then unset them after the build:

```bash
read -rs -p 'RECAP_DG_CLIENT_TOKEN: ' RECAP_DG_CLIENT_TOKEN; echo; export RECAP_DG_CLIENT_TOKEN
read -rs -p 'AQUACAST_TOKEN: ' AQUACAST_TOKEN; echo; export AQUACAST_TOKEN
# ... build (section 3) ...
unset RECAP_DG_CLIENT_TOKEN AQUACAST_TOKEN
```

## 3. Build and start

Application images are built on the host (there is no registry): both `sapphire-flow` and
`sapphire-flow-aquacast`.

```bash
docker compose -f docker-compose.yml -f docker-compose.nepal-cloud.yml build
docker compose -f docker-compose.yml -f docker-compose.nepal-cloud.yml up -d
```

Before starting, check the edge configuration with the real Caddy:

```bash
docker compose -f docker-compose.yml -f docker-compose.nepal-cloud.yml run --rm --no-deps caddy \
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

`init` runs migrations, creates the declared `chwrr` tenant, bootstraps the database roles and registers
deployments **in one command**; if it fails, nothing else starts. Check it first:

```bash
docker compose -f docker-compose.yml -f docker-compose.nepal-cloud.yml ps -a init
docker compose -f docker-compose.yml -f docker-compose.nepal-cloud.yml logs init | tail -30
```

## 4. Bootstrap the admin token

```bash
docker compose -f docker-compose.yml -f docker-compose.nepal-cloud.yml exec api \
  /entrypoint.sh python -m sapphire_flow.cli.access_tokens create-admin --name "<display name>"
```

This mints an unscoped **admin access token** (there is no human user table yet). Store it in the
password manager. Reviewer keys for the dashboard are issued separately (Plan 511 T3).

## 5. Verify

```bash
curl -si https://nepal-staging.hydrosolutions.ch/api/v1/health       # 200, valid certificate
curl -sI https://nepal-staging.hydrosolutions.ch/api/v1/health | grep -i strict-transport-security
curl -so /dev/null -w '%{http_code}\n' https://nepal-staging.hydrosolutions.ch/    # 404
curl -so /dev/null -w '%{http_code}\n' https://nepal-staging.hydrosolutions.ch/docs # 404
docker compose -f docker-compose.yml -f docker-compose.nepal-cloud.yml ps
```

Also confirm from outside that only 22, 80 and 443 answer, and that no Prefect port is reachable. A request to port 80 with `Host: localhost` from outside must get 404 (the health route answers loopback clients only).

**Expected on a fresh host:** `ingest-weather-history` is registered and, with no stations bound yet, writes a CRITICAL `no_stations_bound` pipeline-health record every day. That is normal until Nepal stations are onboarded, not a fault.
`backup-database` stays registered and writes dumps to the local `backups` volume only; **off-box backup is
not set up** (Plan 511 D9) — do not load restricted data before that follow-on is planned
(`docs/decisions/2026-09-30-dhm-data-hosting-assumption.md`).
