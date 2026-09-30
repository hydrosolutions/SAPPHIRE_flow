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
VERSION=<release version, e.g. 0.1.1040>
SAPPHIRE_DOMAIN=nepal-staging.hydrosolutions.ch
EOF_ENV
```

## 2. Secrets (`./secrets/`, mode 600, never committed)

Generate each on the host; none of them passes through a chat or a ticket.

```bash
mkdir -p secrets && chmod 700 secrets
for f in db_password sapphire_api_db_password sapphire_worker_db_password \
         sapphire_backup_db_password access_token_pepper; do
  [ -e "secrets/$f" ] || (umask 077; openssl rand -base64 32 | tr -d '\n' > "secrets/$f")
done
```

Independent passwords for each file. Losing `db_password` after data exists means restoring from backup,
so keep an out-of-band copy in the password manager.

Build-time tokens (private repositories) are read from the environment while building — set them in the
same shell, never on disk:

```bash
export RECAP_DG_CLIENT_TOKEN=...   # hydrosolutions/recap-dg-client
export AQUACAST_TOKEN=...          # aquacast (forecast worker image)
```

## 3. Build and start

Application images are built on the host (there is no registry): both `sapphire-flow` and
`sapphire-flow-aquacast`.

```bash
docker compose -f docker-compose.yml -f docker-compose.nepal-cloud.yml build
docker compose -f docker-compose.yml -f docker-compose.nepal-cloud.yml up -d
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

Also confirm from outside that only 22, 80 and 443 answer, and that no Prefect port is reachable.
`backup-database` stays registered and writes dumps to the local `backups` volume only; **off-box backup is
not set up** (Plan 511 D9) — do not load restricted data before that follow-on is planned
(`docs/decisions/2026-09-30-dhm-data-hosting-assumption.md`).
