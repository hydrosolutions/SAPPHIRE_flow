---
status: READY
created: 2026-07-21
plan: 135
title: Scoped read-only credential for recap-dg-client — replace the broad personal token with a repo-scoped read-only credential (fine-grained PAT or GitHub App install token — grill-me), use it in Actions + Dependabot + build hosts, revoke the broad token
scope: The private `recap-dg-client` git dependency is cloned in CI and the Docker builder, authenticated by RECAP_DG_CLIENT_TOKEN — currently a BROAD personal token (a `gho_` GitHub-CLI OAuth token with write access to all repos, live-verified 2026-07-21). Dependabot-triggered runs get no Actions secrets, so the two jobs that do a FRESH clone (`build-image-and-scan` + `wheel-only-guard`) fail on Dependabot PRs #114–#116; the cache-reliant jobs pass today but are a latent failure. Rather than copy the broad token into the empty Dependabot store (exposing an all-repos write token to PR-submitted code), replace it everywhere with a repo-scoped, read-only credential — either a fine-grained PAT or a GitHub App installation token (owner decides, see § Alternatives grill-me) — scoped to ONLY `hydrosolutions/recap-dg-client`, and revoke the broad one. Mostly an owner/repo-admin runbook; no application code change.
depends_on: []
---

# Plan 135 — scoped read-only token for recap-dg-client

## Why

Dependabot PRs (#114–#116) fail the CI jobs that do a **fresh** clone of the private `recap-dg-client`
git-pin. Confirmed cause: **`RECAP_DG_CLIENT_TOKEN` is empty on Dependabot runs.** It exists in **Actions**
secrets (created 2026-07-17) but the **Dependabot** secret store is empty, and GitHub does **not**
share Actions secrets with Dependabot-triggered workflows.

**Which jobs actually fail today — and which only *look* safe.** `recap-dg-client` is a **hard**
dependency (`pyproject.toml:37`, git source at `pyproject.toml:85`, locked at `uv.lock:3275`), so
every `uv sync --frozen` needs it. But not every job re-clones it on every run:

- **`build-image-and-scan`** and **`wheel-only-guard`** do a **fresh clone** and **fail today** on
  Dependabot PRs #114–#116. `wheel-only-guard` opts out of the uv cache (`enable-cache: false`,
  `ci.yml:116`) and additionally runs `uv sync --no-cache` (`ci.yml:129-130`), so it always clones.
  `build-image-and-scan` builds in an **isolated Docker builder** (BuildKit secret mount,
  `ci.yml:196-197`) with no shared uv cache, so it too always clones.
- **`lint`, `unit`, `integration`, `dependency-safety` PASS today** — not because they need no token,
  but because `setup-uv`'s uv-cache is enabled by default (no explicit toggle; only `wheel-only-guard`
  disables it) and restores an already-resolved `recap-dg-client` build from a prior *authenticated*
  run on `main`. Dependabot PRs read the base-branch cache, so `uv sync` finds recap already built and
  skips the clone — masking the empty token. Note the `cache: "true"` lines at `ci.yml:52` / `ci.yml:217`
  are **Trivy's** scan cache, not the uv dependency cache — the uv cache is `setup-uv`'s default-on
  behaviour, which is what serves these jobs.

So **today** only the two fresh-clone jobs are red. The four cache-reliant jobs are a **latent**
failure: a single cache eviction (7-day GitHub Actions cache TTL, a lockfile-key change, or a cache
purge) drops the cached build and they clone with the empty token and fail too. **That latent risk —
not a false "all jobs fail today" claim — is why the replacement credential must go everywhere the
token lives, not only into the Dependabot store.**

### Evidence (run before executing — record the outputs in the PR/plan)

```bash
# Which stores hold the secret today (expect: actions=present, dependabot=absent):
gh secret list --app actions    --repo hydrosolutions/SAPPHIRE_flow | grep RECAP_DG_CLIENT_TOKEN
gh secret list --app dependabot --repo hydrosolutions/SAPPHIRE_flow | grep RECAP_DG_CLIENT_TOKEN || echo "absent"

# Observed failing-check shape on a Dependabot PR (snapshot the full list — do not assume):
gh pr checks 114 --repo hydrosolutions/SAPPHIRE_flow
```

Observed on #114/#115/#116: `build-image-and-scan` + `wheel-only-guard` **red**; `lint`, `unit`,
`integration`, `dependency-safety` **green** (cache-served, per above). The `build-image-and-scan`
failure is **a git authentication failure (empty/invalid token) during the buildx secret-mounted
`uv sync` clone** — the BuildKit secret resolves to an empty token, so the clone is rejected:

```
##[warning] recap_dg_client_token= is not a valid secret
remote: Invalid username or token. Password authentication is not available...
fatal: Authentication failed for 'https://github.com/hydrosolutions/recap-dg-client.git/'
... failed to fetch commit
```

The `gh pr checks 114` snapshot is the source of truth for exactly which jobs are red, and acceptance
(below) requires **all** token-consuming jobs green after the fix.

The naive fix — copy the existing token into the Dependabot store — is **rejected**, and a
**live GitHub-API audit of the current token on 2026-07-21** (read from the mac-mini
`secrets/recap_dg_client_token`) shows why the blast radius is unacceptable:

- **Type:** prefix `gho_` — a GitHub **CLI OAuth** token (tied to a user's `gh` session), **not** a PAT.
- **Scopes:** `gist, read:org, repo, workflow` — i.e. full read **and write** to **all** repos the
  owner can access, plus the ability to edit workflow files.
- **Not scoped to recap:** it reads `SAPPHIRE_flow` (HTTP 200), so it is not confined to
  `recap-dg-client`.
- **Not read-only:** it has `admin`/`push` permissions on `recap-dg-client`.

Dependabot secrets are exposed to workflows that execute **PR-submitted code**, so a malicious
dependency update could exfiltrate this token — and it can *write* to every repo the owner can access.
Separately, `gho_` CLI tokens can rotate or expire whenever the owner's `gh` session changes, so even
ignoring blast radius it is an unstable credential for CI. Both facts mean it must be **replaced in the
Actions store too**, not merely withheld from Dependabot.

**Fix:** a **repo-scoped, read-only credential for only `hydrosolutions/recap-dg-client`** — a
fine-grained PAT (`Contents: Read`) or a GitHub App installation token (`Contents: Read-only`); see the
**PAT-vs-App grill-me** in § Alternatives. Worst-case leak = read access to one private repo that CI
already clones anyway. Use it in every place the token lives, and revoke the broad token.

## Where the token is used today (all must be updated)

Per `docs/plans/121-recap-flow6-and-integration-followons.md:70` the secret is wired into **every**
workflow that clones the git-pin. Split by trigger, because trigger determines which store feeds it:

**A. `pull_request`-triggered (run on Dependabot PRs — the two fresh-clone jobs fail today, the rest
are latent per § Why):**

1. **`ci.yml`** — five jobs clone recap and consume the secret: `lint` (`ci.yml:23`), `unit`
   (`ci.yml:69`), `wheel-only-guard` (`ci.yml:123`, fresh-clone → **fails today**), `integration`
   (`ci.yml:161`), and `build-image-and-scan` via a BuildKit build secret (`ci.yml:197`, fresh-clone →
   **fails today**). `lint`/`unit`/`integration` are cache-served and pass today (latent). Triggers:
   `push` + `pull_request` (`ci.yml:3-7`).
2. **`dependency-safety.yml`** — unconditional `pull_request` gate; configures git auth then
   `uv sync --frozen` (`dependency-safety.yml:35,38`). Cache-served → passes today (latent), but
   token-consuming, so it must get the replacement credential too.

**B. `schedule`-triggered (run in the `main` context with the Actions secret — NOT on Dependabot
PRs, but they still consume the token, so replacing the Actions secret must keep them working):**

3. **`integration-nightly.yml`** — nightly `cron: "0 3 * * *"`; git auth + `uv sync --frozen`
   (`integration-nightly.yml:49,51`).
4. **`live-lindas-weekly.yml`** — weekly `cron: "0 6 * * 1"`; git auth + `uv sync --frozen`
   (`live-lindas-weekly.yml:35,37`).

**C. Secret stores + build hosts (the actual credentials to change):**

5. **GitHub Actions secret** `RECAP_DG_CLIENT_TOKEN` — feeds every workflow in A + B.
6. **Dependabot secret** `RECAP_DG_CLIENT_TOKEN` — **currently absent** (the gap).
7. **Build-host token file** on the **mac-mini** at `/Users/sapphire/SAPPHIRE_flow/secrets/recap_dg_client_token`
   (the deployment repo lives at `/Users/sapphire/SAPPHIRE_flow` — `docs/deployment/mac-mini-staging.md:122`,
   `scripts/launchd/start-sapphire.sh:25-26`). This file is the operator-managed **source** for
   `export RECAP_DG_CLIENT_TOKEN=$(cat secrets/recap_dg_client_token)` (`docs/standards/cicd.md:130`,
   `mac-mini-staging.md:147`); docker-compose then passes that **env value** into the BuildKit build
   secret (`recap_dg_client_token` → `environment: RECAP_DG_CLIENT_TOKEN`, `docker-compose.yml:323-324`).
   The builder never reads the file directly. Applies equally to any **dev machine** that builds the
   image locally (repo-root-relative `secrets/recap_dg_client_token`).

## Procedure (🔑 = owner / repo-admin GUI action; the rest is CLI I can run once the credential exists)

> **Credential choice first.** This runbook is written for the owner's current lean, a **fine-grained
> PAT** (simple static secret). If the owner instead picks the **GitHub App** path (§ Alternatives
> grill-me), steps 1–2 are replaced by "create + install the App and add `actions/create-github-app-token`
> to each workflow" and step 7 (renewal) drops away entirely; steps 3–6, 8–9 are unchanged (both mint a
> `x-access-token:<token>@…` credential consumed identically). Do not run steps 1–2 until the fork is
> decided.

1. 🔑 **Org policy check.** Ensure `hydrosolutions` allows fine-grained PATs (Org → Settings → Third-party
   Access / Personal access tokens). If the org requires approval for fine-grained tokens, the new token
   will need org-admin approval before it works.
2. 🔑 **Mint the fine-grained PAT** (github.com → Settings → Developer settings → **Fine-grained tokens** →
   Generate new token):
   - **Resource owner:** `hydrosolutions` (the org, NOT the personal account).
   - **Repository access:** *Only select repositories* → **`recap-dg-client`** and nothing else.
   - **Permissions:** Repository → **Contents: Read-only** (all a `git clone` needs). Metadata: Read
     (auto-selected). **No other scopes.**
   - **Expiration:** GitHub forces fine-grained PATs to expire (max 1 year). Set an explicit expiry
     (e.g. 90 days or a fixed date) and record it per step 7 — on lapse the token fails CLOSED across CI
     **and** the mac-mini production build (see § Alternatives for the blast-radius analysis).
   - Copy the token value (shown once).
3. **Set the Dependabot secret:** `gh secret set RECAP_DG_CLIENT_TOKEN --app dependabot --body "<PAT>"`
   (or UI: Settings → Secrets and variables → **Dependabot** → New repository secret).
4. **Replace the Actions secret** with the same PAT: `gh secret set RECAP_DG_CLIENT_TOKEN --body "<PAT>"`
   (overwrites the broad token in the Actions store; feeds every workflow in groups A + B above).
5. **Update the build-host token files** with the new PAT:
   - mac-mini (run **as the `sapphire` user**, or use the explicit path):
     `printf '%s' '<PAT>' > /Users/sapphire/SAPPHIRE_flow/secrets/recap_dg_client_token && chmod 600 /Users/sapphire/SAPPHIRE_flow/secrets/recap_dg_client_token`
   - any dev machine that builds locally (from the repo root — the `secrets/` dir may not exist yet):
     `mkdir -p secrets && chmod 700 secrets && printf '%s' '<PAT>' > secrets/recap_dg_client_token && chmod 600 secrets/recap_dg_client_token`
6. 🔑 **Revoke the old broad personal token** (the `gho_` CLI/OAuth token — revoke via the owner's
   `gh` session / GitHub authorized-apps settings), AFTER the verifications in step 8 pass.
7. **Record the renewal — a simple, honest note (PAT path only; N/A for the GitHub App).** A
   fine-grained PAT expires, and on lapse the failure mode is large (CI + mac-mini production build
   break at once — § Alternatives). v0 has **no** notification dispatch (`docs/v0-scope.md`) and the
   `pipeline_health` store is an internal method, not an alerting API — so do **not** build a
   Slack/`pipeline_health` renewal-alert workflow here. Instead:
   - Record the **expiry date** in `docs/standards/security.md` § recap-dg-client (step 9) and set a
     **human calendar reminder** ~14 days ahead.
   - Note the **fail-closed blast radius on lapse**: CI (push-to-`main` + Dependabot) and the mac-mini
     build both break at once.
   - *(Optional, not mandated)*: a tiny scheduled `gh api` check of the token's expiry that fails the
     workflow loudly N days out is a fine lightweight guard if someone wants one — but it is not
     required, and it must not depend on any notification machinery v0 does not have.
   - If the owner picks the **GitHub App** (§ Alternatives), skip this step entirely — App private keys
     do not force-expire like fine-grained PATs, so there is no renewal to guard.
8. **Verify:**
   - **Dependabot CI:** re-trigger a Dependabot PR's checks (e.g. comment `@dependabot recreate` on #114,
     or close/reopen), then `gh pr checks 114 --watch` → **every token-consuming `pull_request` check**
     (the group A jobs: `lint`, `unit`, `wheel-only-guard`, `integration`, `build-image-and-scan`, plus
     `dependency-safety`) now PASSES. Compare against the pre-fix `gh pr checks 114` snapshot from
     § Evidence.
   - **Normal CI:** confirm a push/PR from a branch (non-Dependabot) still passes the recap-cloning jobs
     (the Actions secret was replaced).
   - **Scheduled workflows:** `integration-nightly` + `live-lindas-weekly` still clone recap on their
     next scheduled run (or a `workflow_dispatch`) — the Actions-secret swap must not break them. A
     manual `gh workflow run integration-nightly.yml` is the fastest confidence check.
   - **Mini Docker build:** `export RECAP_DG_CLIENT_TOKEN=$(cat /Users/sapphire/SAPPHIRE_flow/secrets/recap_dg_client_token)`
     then a `docker compose … build` succeeds (recap clones with the new PAT).
9. **Doc sync (both standards docs):**
   - **`docs/standards/security.md:395`** — replace the stale "does not exist yet as of this plan
     landing" wording: the credential is now a repo-scoped read-only credential (fine-grained PAT or
     GitHub App install token) for `recap-dg-client`, present in BOTH the Actions and Dependabot secret
     stores; if a PAT, record the expiry date + renewal reminder (step 7); keep the removal trigger
     (private-index wheel) as the endgame.
   - **`docs/standards/cicd.md`** — the CI table's `dependency-safety.yml` (`cicd.md:439`),
     `integration-nightly.yml` (`cicd.md:443`), and `live-lindas-weekly.yml` (`cicd.md:446+`) rows list
     `uv sync --frozen` but are **missing** the "Configure git auth … requires `RECAP_DG_CLIENT_TOKEN`"
     note that the `ci.yml` rows carry (e.g. `cicd.md:415,424,427,431`). Add that requirement to each so
     the table reflects **every** token-consuming workflow, not just `ci.yml`.

## Acceptance

- Dependabot PRs #114/#115/#116 (safe base-image/action bumps) go green on **all token-consuming
  `pull_request` checks** — the `ci.yml` group A jobs (`lint`, `unit`, `wheel-only-guard`,
  `integration`, `build-image-and-scan`) plus `dependency-safety` — and can merge. (#117 = uv-build
  bump; re-check after this fix and triage any residual, non-token failure independently.)
- Normal CI + the mac-mini Docker build still work; a manual `integration-nightly` / `live-lindas-weekly`
  run still clones recap (the Actions-secret swap didn't break the scheduled workflows).
- The broad `gho_` personal token is revoked; no all-repos write token in either secret store.
- If the PAT path was chosen: its expiry date is recorded in `security.md` § recap-dg-client and a
  calendar reminder is set (step 7). If the GitHub App path was chosen: no renewal item (non-expiring).

## Alternatives (and the credential grill-me)

**Recurring-expiry cost is a real line item for a fine-grained PAT.** GitHub forces fine-grained PATs to
expire (max 1 year). On lapse the token fails CLOSED not just for Dependabot but for **push-to-main
CI** (the Actions secret now gates `ci.yml`, which fires on `push` — `ci.yml:3-7`) **and** the
mac-mini **production Docker build** (BuildKit secret mount, `ci.yml:196-197` / `cicd.md:130`) —
simultaneously. That blast radius is materially larger than the Dependabot-only problem this plan sets
out to fix. The PAT's offsetting upside is that it is a one-line value swap into the existing HTTPS
`x-access-token` mechanism (no Dockerfile/CI auth rewiring), with the expiry mitigated by a simple
recorded renewal reminder (step 7). The **GitHub App** below removes the expiry problem outright.

- **GitHub App installation token** (the strongest alternative — surfaced as a grill-me below). Create
  a GitHub App installed on **only** `recap-dg-client` with **`Contents: Read-only`**, and mint a
  short-lived installation token at job runtime via `actions/create-github-app-token`. It is consumed
  **exactly** like the PAT — `https://x-access-token:<token>@github.com/…`, the **same** `insteadOf`
  line, the **same** Dockerfile buildx secret mount — so **zero auth-mechanism rewiring** (unlike the
  deploy key). Crucially, App private keys **do not force-expire** like fine-grained PATs, so it
  **eliminates the renewal-guard burden entirely** (step 7 becomes N/A). Cost: a one-time App
  create + install (comparable effort to minting a PAT), a token-mint step added to each workflow, and
  the mac-mini/dev build hosts need a way to mint an installation token at build time (slightly more
  than reading a static file). The `actions/create-github-app-token` step needs the App id + private
  key as its own repo secrets — but those grant nothing beyond minting this one read-only token.
- **Read-only deploy key** (SSH, per-repo). Also **non-expiring**, but its downside is worse than the
  App's: one-time migration of the clone auth from HTTPS `x-access-token` to SSH — touching the
  `git config … insteadOf` in every workflow (`ci.yml:23,69,123,161`, `dependency-safety.yml:35`,
  `integration-nightly.yml:49`, `live-lindas-weekly.yml:35`) and the Dockerfile secret mount.
  **Not chosen**: the GitHub App gets the same non-expiring benefit with **zero** auth rewiring, so the
  deploy key's 7+-touch-point SSH migration buys nothing extra.
- **Endgame (removes the credential entirely):** publish `recap-dg-client` as a versioned wheel to a
  private hydrosolutions package index (Plan 080-style) and migrate off the git pin — then drop both
  secrets and the wheel-only-guard exception. Tracked in `security.md:395` as the removal trigger; out
  of scope here.

### Grill-me — fine-grained PAT vs GitHub App (owner decides; do NOT pre-pick)

Both mint a repo-scoped, read-only `x-access-token` credential consumed identically by CI and the
Docker build, so this is purely a **credential-lifecycle** trade, not an integration one:

| | **Fine-grained PAT** (owner's current lean) | **GitHub App installation token** |
|---|---|---|
| Secret shape | One static value in each store — simplest to set | App id + private key secrets; token minted per job via `actions/create-github-app-token` |
| Expiry / renewal | **Expires** (max 1 yr) → recurring renewal + fail-closed-on-lapse risk (step 7) | Private key **does not force-expire** → **no renewal item at all** |
| Setup cost | Mint a token (steps 1–2) | One-time App create + install + add a mint step to each workflow + build hosts |
| Auth rewiring | None | None (same `insteadOf` / buildx mount) |

**RESOLVED (owner, 2026-07-21): fine-grained PAT.** Simplest static secret; the expiry/renewal cost is
accepted as bounded by the private-index-wheel endgame (which removes the token entirely). The **GitHub
App** stays documented above as the zero-renewal alternative if the renewal chore proves annoying, but
this plan executes the PAT path (steps 1–9 as written; the App-only note in step 7 does not apply).

## Provenance

Dependabot PRs #114–#116 failing on the two fresh-clone recap jobs (`build-image-and-scan` +
`wheel-only-guard`; the cache-served jobs pass today but are a latent failure — token empty on
Dependabot runs). A live 2026-07-21 audit found the current token is a broad `gho_` CLI/OAuth token
(write to all repos), so it must be replaced everywhere, not just withheld from Dependabot. Owner
opted for a scoped read-only credential rather than exposing the broad token to PR-triggered Dependabot
workflows. `/plan`-reviewed (2026-07-21): root-cause corrected to the two-fresh-clone-jobs picture,
live token audit + GitHub App alternative folded, over-engineered renewal automation trimmed.
**Credential grill-me RESOLVED (owner, 2026-07-21): fine-grained PAT.** Interim hardening ahead of the
private-index-wheel endgame. **READY** — owner runs the 🔑 steps (mint the fine-grained PAT); I run the
secret + host-file updates + verification once it exists. Relates to Plan 064 (supply-chain hardening),
Plan 079/080 (FI/recap distribution), Plan 121 (secret-wiring inventory), and `security.md:395`.
