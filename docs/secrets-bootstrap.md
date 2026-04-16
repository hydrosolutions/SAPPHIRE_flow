# Secrets Bootstrap

Required secrets for SAPPHIRE Flow deployment. These files are read by Docker
Compose as container secrets (see `docker-compose.yml`).

## v0 required secrets

| File | Purpose | Used by |
|------|---------|---------|
| `secrets/db_password` | PostgreSQL password for the `sapphire` user | postgres, prefect-worker, api, init |

## Setup

```bash
mkdir -p secrets
openssl rand -base64 32 > secrets/db_password
sudo chown -R root:root secrets/
sudo chmod 700 secrets/
sudo chmod 600 secrets/*
```

The `secrets/` directory is gitignored. Never commit secrets to version control.

## v1 additional secrets

These are not consumed by v0 docker-compose.yml but are documented here for
forward planning (see `docs/standards/security.md`):

| File | Purpose | When needed |
|------|---------|-------------|
| `secrets/secret_key` | JWT signing key (HS256) | v1 (auth) |
| `secrets/totp_encryption_key` | Fernet key for TOTP seeds | v1 (MFA) |

## Backup

Back up the `secrets/` directory separately from the database. If secrets are
lost, the database password must be reset and all services restarted.
