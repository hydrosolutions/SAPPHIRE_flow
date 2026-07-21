---
status: DRAFT
created: 2026-07-21
plan: 135
title: Scoped read-only token for recap-dg-client — mint a fine-grained PAT (repo-scoped, Contents:Read), use it in Actions + Dependabot + build hosts, revoke the broad personal token
scope: The private `recap-dg-client` git dependency is cloned in CI and the Docker builder, authenticated by RECAP_DG_CLIENT_TOKEN — currently a BROAD personal PAT. Dependabot-triggered runs get no Actions secrets, so `build-image-and-scan` + `wheel-only-guard` fail on every Dependabot PR. Rather than copy the broad token into the Dependabot store (exposing an all-repos token to PR-submitted code), mint a FINE-GRAINED read-only PAT scoped to ONLY `hydrosolutions/recap-dg-client`, use it everywhere the current token is used, and revoke the broad one. Mostly an owner/repo-admin runbook; no application code change.
depends_on: []
---

# Plan 135 — scoped read-only token for recap-dg-client

## Why

Dependabot PRs (#114–#117) fail exactly `build-image-and-scan` + `wheel-only-guard` (unit/integration/
lint/dependency-safety pass). Confirmed cause: **`RECAP_DG_CLIENT_TOKEN` is empty on Dependabot runs.**
It exists in **Actions** secrets (created 2026-07-17) but the **Dependabot** secret store is empty, and
GitHub does **not** share Actions secrets with Dependabot-triggered workflows. So the jobs that clone the
private `recap-dg-client` git-pin get no token and fail (`cat: /run/secrets/recap_dg_client_token: No such
file or directory` → `uv sync` clone fails).

The naive fix — copy the existing token into the Dependabot store — is **rejected**: the current token is
the **broad personal PAT** (owner-flagged to scope down; `security.md` §recap-dg-client + memory). Dependabot
secrets are exposed to workflows that execute **PR-submitted code**, so a malicious dependency update could
exfiltrate it — and that token can read *every* repo the person can access. Unacceptable blast radius.

**Fix:** a **fine-grained, read-only PAT scoped to only `hydrosolutions/recap-dg-client`**. Worst-case leak
= read access to one private repo that CI already clones anyway. Use it in all three places the token lives,
and revoke the broad token.

## Where the token is used today (all must be updated)

1. **GitHub Actions secret** `RECAP_DG_CLIENT_TOKEN` — `lint`/`unit`/`integration`/`wheel-only-guard`/
   `build-image-and-scan` jobs clone recap-dg-client via `git config url."https://x-access-token:$TOKEN@…".
   insteadOf …` (see `docs/standards/security.md` §recap-dg-client; `docs/standards/cicd.md` CI table).
2. **Dependabot secret** `RECAP_DG_CLIENT_TOKEN` — **currently absent** (the gap).
3. **Build-host token file** `~/SAPPHIRE_flow/secrets/recap_dg_client_token` on the **mac-mini** (the
   Dockerfile builder `--mount=type=secret,id=recap_dg_client_token`), and any **dev machine** that builds
   the image locally.

## Procedure (🔑 = owner / repo-admin GUI action; the rest is CLI I can run once the PAT exists)

1. 🔑 **Org policy check.** Ensure `hydrosolutions` allows fine-grained PATs (Org → Settings → Third-party
   Access / Personal access tokens). If the org requires approval for fine-grained tokens, the new token
   will need org-admin approval before it works.
2. 🔑 **Mint the fine-grained PAT** (github.com → Settings → Developer settings → **Fine-grained tokens** →
   Generate new token):
   - **Resource owner:** `hydrosolutions` (the org, NOT the personal account).
   - **Repository access:** *Only select repositories* → **`recap-dg-client`** and nothing else.
   - **Permissions:** Repository → **Contents: Read-only** (all a `git clone` needs). Metadata: Read
     (auto-selected). **No other scopes.**
   - **Expiration:** set an explicit expiry (e.g. 90 days or a fixed date). Fine-grained PATs expire — record
     the renewal date (see step 7).
   - Copy the token value (shown once).
3. **Set the Dependabot secret:** `gh secret set RECAP_DG_CLIENT_TOKEN --app dependabot --body "<PAT>"`
   (or UI: Settings → Secrets and variables → **Dependabot** → New repository secret).
4. **Replace the Actions secret** with the same PAT: `gh secret set RECAP_DG_CLIENT_TOKEN --body "<PAT>"`
   (overwrites the broad token in the Actions store).
5. **Update the build-host token files** with the new PAT:
   - mac-mini: `printf '%s' '<PAT>' > ~/SAPPHIRE_flow/secrets/recap_dg_client_token && chmod 600 ~/SAPPHIRE_flow/secrets/recap_dg_client_token`
   - any dev machine that builds locally: same file.
6. 🔑 **Revoke the old broad personal PAT** (Settings → Developer settings → Tokens → revoke), AFTER the
   verifications in step 8 pass.
7. **Record the renewal** — the fine-grained PAT expires; add the expiry date to `security.md` (step 9) and
   a calendar reminder, so CI/builds don't break silently on expiry. (A deploy key — read-only, per-repo,
   non-expiring — is a lower-maintenance alternative; see § Alternatives.)
8. **Verify:**
   - **Dependabot CI:** re-trigger a Dependabot PR's checks (e.g. comment `@dependabot recreate` on #114, or
     close/reopen), then `gh pr checks 114 --watch` → `build-image-and-scan` + `wheel-only-guard` now PASS.
   - **Normal CI:** confirm a push/PR from a branch (non-Dependabot) still passes the recap-cloning jobs (the
     Actions secret was replaced).
   - **Mini Docker build:** `export RECAP_DG_CLIENT_TOKEN=$(cat ~/SAPPHIRE_flow/secrets/recap_dg_client_token)`
     then a `docker compose … build` succeeds (recap clones with the new PAT).
9. **Doc sync (`security.md` §recap-dg-client):** the token is now a fine-grained read-only PAT scoped to
   `recap-dg-client`, present in BOTH the Actions and Dependabot secret stores; record the expiry/renewal
   cadence; keep the removal trigger (private-index wheel) as the endgame.

## Acceptance

- Dependabot PRs #114/#115/#116 (safe base-image/action bumps) go green on `build-image-and-scan` +
  `wheel-only-guard` and can merge. (#117 = uv-build bump fails all jobs incl. lint/unit → a separate,
  non-token issue; re-check after this and triage independently.)
- Normal CI + mini Docker build still work.
- The broad personal PAT is revoked; no all-repos token in either secret store.

## Alternatives (noted, not chosen)

- **Read-only deploy key** (SSH, per-repo, non-expiring): even narrower and no renewal, but changes the auth
  from the current HTTPS `x-access-token` flow to SSH (more moving parts in the Dockerfile + CI). Fine-grained
  PAT keeps the existing HTTPS-token mechanism with a one-line value swap — preferred for this change.
- **Endgame (removes the token entirely):** publish `recap-dg-client` as a versioned wheel to a private
  hydrosolutions package index (Plan 080-style) and migrate off the git pin — then drop both secrets and the
  wheel-only-guard exception. Tracked in `security.md` as the removal trigger; out of scope here.

## Provenance

Dependabot PRs #114–#117 failing on the recap-clone jobs (token empty on Dependabot runs). Owner opted to
mint a scoped read-only PAT rather than expose the broad personal token to PR-triggered Dependabot workflows.
Interim hardening ahead of the private-index-wheel endgame. DRAFT — owner runs the 🔑 steps; I run the secret
+ host-file updates + verification once the PAT exists. Relates to Plan 064 (supply-chain hardening),
Plan 079/080 (FI/recap distribution), and `security.md` §recap-dg-client.
