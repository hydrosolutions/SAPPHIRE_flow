# Plan 064 — Supply-chain hardening

**Status**: READY
**Date**: 2026-04-20
**Depends on**: none (independent of Plan 046 staging validation and Plan 054
doc sweep; coordinates with both via shared files — `docker-compose.yml`,
`Dockerfile`, `.github/workflows/ci.yml`, `docs/standards/security.md`,
`docs/standards/cicd.md`).
**Scope**: Close the supply-chain gaps surfaced by the 2026-04-20 audit.
Pins the major mutable third-party inputs we directly reference in source
control (Python dependency definitions, container images, GitHub Actions),
adds automated CVE scanning and reviewed dependency-update PRs,
adds a CI guard against new Python install-time code execution from source
builds, generates an SBOM per built image, and documents the resulting
posture plus the remaining OS-package residual risk in
`docs/standards/security.md`.
Strictly defensive hardening — no change to the forecast pipeline, API
behaviour, or deployment topology. Image signing and runtime egress
allowlisting are proposed as optional streams and gated on user
confirmation.

---

## Context

### Why now

The Mac-mini staging deployment (Plan 046) is the first environment where
SAPPHIRE Flow pulls images, wheels, OS packages, and GitHub Actions from
the public internet on a production-like host. Before Nepal deployment
(Oct 2026) we need the build to be more reproducible and its highest-value
external inputs to be attributable, scannable, and upgradable under
review. The audit on
2026-04-20 found five concrete gaps:

1. **Base images are tag-pinned, not digest-pinned.** `Dockerfile` pulls
   `python:3.11.12-slim`; `docker-compose.yml` pulls
   `postgis/postgis:16-3.4`, `prefecthq/prefect:3-python3.11`, and
   `caddy:2.9`. Additionally, `.github/workflows/ci.yml` uses
   `postgis/postgis:16-3.4` as a GitHub Actions `services.image` in the
   integration job — a fourth unpinned external-image pull outside
   compose. Any of these tags can be re-pushed upstream, silently
   changing what ships. Plan 053's `## Future work` section explicitly
   deferred digest pinning to "a future security-hardening plan" — this
   is that plan.
2. **GitHub Actions are tag-pinned, not SHA-pinned.** `.github/workflows/ci.yml`
   references `actions/checkout@v4` and `astral-sh/setup-uv@v4`;
   `.github/workflows/live-lindas-weekly.yml` references
   `actions/checkout@v4` and `astral-sh/setup-uv@v5` (mixed major —
   unifying would be a separate decision outside this plan). Action tags
   are mutable; a compromise of a popular action (precedent:
   `tj-actions/changed-files`, Mar 2025) can inject steps into every CI run.
3. **No CVE scanning.** Neither `pip-audit`, `trivy`, nor any equivalent
   runs in CI. We learn about vulnerable dependencies by accident.
4. **No automated dependency update PRs.** `dependabot.yml` and
   `renovate.json` are both absent. Dependency upgrades happen manually
   and sporadically. A separate PR-time dependency-review gate would be
   a later tightening step, not a v0 prerequisite here.
5. **No SBOM, and no image-level provenance / attestation workflow.**
   Nothing downstream can enumerate what is inside an image today.
   SBOM generation is the sensible v0 fix. Formal provenance
   attestations or image signing are possible later controls, but
   without a settled registry and verification path they are not
   sensible defaults in this plan.

The lockfile (`uv.lock`) is committed and the Dockerfile already runs
`uv sync --frozen`, so the Python side has resolver determinism. That is
the one strength to build on — the rest of the supply chain is unpinned.

A later review also identified a narrower but important install-time risk:
Python dependencies can still execute arbitrary code when they have to be
built from source (PEP 517 build backends / sdists), even with a frozen
lockfile. This repo already has one documented source-build path
(`exactextract` on linux/arm64 in the Docker builder). The goal in v0 is
not to eliminate every such path, but to prevent *new* ones from first
executing on a long-lived developer machine when a package update lands.

### Principle

Every high-leverage third-party input we directly reference in source
control should be: (a) pinned by immutable identifier where practical,
(b) scanned for known vulnerabilities on every CI run, and (c) updated
only via reviewed change. Where we still rely on live OS-package feeds
during Docker builds, that residual risk must be explicit, documented, and
monitored. The goal is not zero risk — it is *attributable* risk: when
something breaks or a CVE lands, we can answer "what changed and when?" in
one `git log`.

### Non-goals

- **No runtime behaviour change.** No forecast, API, or ingestion code
  is modified by this plan.
- **No new secrets or credentials** beyond what a CI registry push would
  require (and only if the signing stream is accepted).
- **No mandatory egress allowlist in v0.** Stream E below is drafted as
  optional because the staging host topology is not yet frozen.
- **No replacement of `uv`.** The lockfile strategy stays; we harden
  what surrounds it.
- **No guarantee of wheel-only installs on every platform in v0.** The
  plan adds a GitHub-hosted CI guard against new source-build execution
  for dependency updates, but documented exceptions and platform-specific
  wheel gaps can still exist.
- **No mandatory PR-time dependency-review action in v0.** Dependabot
  update PRs plus Trivy scanning cover the baseline we need here. Add a
  stricter dependency-diff merge gate later only if the extra workflow
  overhead becomes worth it.
- **No secret scanning configuration.** GitHub's native push-protection
  secret scanning is adjacent but a different category (it protects our
  secrets from being leaked outward, not our build from being compromised
  inward). Enable it through repo settings separately; out of scope for
  this plan.

### Inputs

- `Dockerfile` — two-stage, `python:3.11.12-slim` base, `uv sync --frozen --no-dev`; uv already installed via `COPY --from=ghcr.io/astral-sh/uv:0.7.3` (tag-only, no digest). Runtime stage also installs Debian + PGDG packages and currently fetches the PGDG signing key over the network at build time.
- `docker-compose.yml` — four services with tag-pinned external images (postgis, prefect-server, caddy, plus the locally-built `sapphire-flow:${VERSION}` image used by worker/api/init)
- `.github/workflows/ci.yml` — lint/unit/integration/e2e tiers, tag-pinned actions; integration job declares `postgis/postgis:16-3.4` as a `services.image` (unpinned, outside compose); workflow uses plain `uv sync` (not `--frozen`)
- `.github/workflows/live-lindas-weekly.yml` — tag-pinned actions, uses `astral-sh/setup-uv@v5` (different major from `ci.yml`), and also uses plain `uv sync`
- `pyproject.toml` / `uv.lock` — 39 direct deps (26 runtime + 13 dev), lockfile committed; no `[tool.uv] required-version` yet, so the repo's uv-version policy is still implicit today
- `docs/standards/security.md` — container hardening, secrets, auth (no `## Supply chain` section today; OWASP A06 row already mentions "Dependabot/Renovate for update alerts" as if configured — stale)
- `docs/standards/cicd.md` — compose topology, volumes, health checks (has a weak "never uses `:latest` in production" rule; no image-build tier documented)

---

## Architecture decisions

> Naming: decisions are numbered D1–D13 in this table. Task codes use
> stream-letter + number (A1, B1, C1). The Stream D doc tasks are split
> into D1a (`security.md`) and D1b (`cicd.md`) — this avoids the earlier
> "Decision D1" / "Task D1" label collision.

| # | Decision | Rationale |
|---|---|---|
| D1 | **Dependabot, not Renovate**, for dependency update PRs, scoped to `uv` (repo root), `docker` (Dockerfile), `docker-compose` (compose file), and `github-actions`. | Dependabot is first-party on GitHub, needs no external app install, and the config surface (`.github/dependabot.yml`) is small. This repo is `uv`-managed, and GitHub documents Docker Compose as a separate ecosystem from Docker, so treating them separately matches the actual repo shape. Renovate is more powerful but the extra power (custom managers, broad grouping rules) is not needed at current scale. Revisit if we outgrow it. |
| D2 | **Pin every external image by the manifest-list digest** (`image@sha256:...`) in `Dockerfile` and `docker-compose.yml`, not a platform-specific digest. Dependabot's Docker-related ecosystems keep digests current under PR review where supported. | Tags are mutable. A digest pin makes the image a fixed input. The manifest-list digest is what `docker buildx imagetools inspect <tag>` returns — it resolves per-arch on pull, so the same pin works for both amd64 (CI) and arm64 (Mac mini). A platform-specific digest would break cross-arch pulls. Dependabot removes the "manual upgrade is painful" objection by producing the PR for us. |
| D3 | **Pin every GitHub Action by commit SHA**, with the version tag retained as a trailing comment (`uses: actions/checkout@<sha>  # v4.2.2`). | Action tags are mutable. SHAs are immutable. The comment preserves human-readable versioning for review. GitHub's own security guidance and Dependabot's `github-actions` ecosystem both assume this pattern. |
| D4 | **Single CVE scanner, two layers**: `trivy fs .` in the lint tier (scans `uv.lock` + `pyproject.toml` without building the image — fast feedback on dep-only PRs) and `trivy image <tag>` after the build (catches OS-level CVEs). Both fail CI on HIGH+ unfixed. | Earlier draft had `pip-audit` + `trivy`. Trivy already reads `uv.lock` in `fs` mode, so `pip-audit` duplicates ~80% of coverage with negligible Python-advisory edge. One tool means one config, one ignore-list (`.trivyignore`), and one set of CI steps to reason about. Drop the duplication. |
| D5 | **Generate a CycloneDX SBOM with `syft` per built image**, uploaded as a CI artifact on every run. Release attachment or registry attestation is a follow-up once an image-publish workflow exists. | SBOM is a recoverability tool: when a future CVE lands, we need to know which image contains the affected library. Uploading the SBOM on every CI run gives immediate value without inventing a release workflow the repo does not yet have. Syft emits CycloneDX without requiring a signing key. |
| D6 | **Add an explicit `[[tool.uv.index]]` block documenting PyPI as the default index**. Treat this as low-priority hygiene, not a primary control. | `uv` already defaults to PyPI and uses a safer first-index strategy. The main benefit here is explicitness and future-proofing ahead of any private-index introduction, not fixing an urgent current exposure. |
| D7 | **Image signing via `cosign` is proposed as an OPTIONAL stream (E)** — needs a signing identity (keyless Sigstore OIDC or a managed key) and a decision on whether staging/Nepal will run `cosign verify` at pull time. Default: defer until the registry story is settled. | Signing without verification is ceremony. The verification side needs a deployment decision we have not made yet (are we pushing to GHCR? a hydrosolutions-controlled registry?). Parking until that is clear avoids premature lock-in. |
| D8 | **Runtime egress allowlist (Stream F)** is also OPTIONAL and deferred to the Mac-mini staging plan (Plan 046) or a dedicated follow-up. | Egress controls depend on the host network topology (bridge vs host networking, Caddy reverse-proxy layout) and are better designed once staging is real. |
| D9 | **Document the new posture in `docs/standards/security.md` §Supply Chain** as part of the last tasks (Task D1a + Task D1b — this eliminates the Decision-D1 / Task-D1 label collision warned about in the header), not sprinkled across each change. | Matches the convention used by Plan 054 T6 — doc change lands as the capstone, not scattered edits. |
| D10 | **Pin the `uv` toolchain itself wherever this repo runs it**: digest-pin the `ghcr.io/astral-sh/uv:<version>` image in the `Dockerfile`, declare the repo-standard uv version in `pyproject.toml` via `[tool.uv] required-version`, make CI install that same version explicitly in `setup-uv`, and use `uv sync --frozen` in workflows. | `uv` is the tool that enforces `uv.lock`. If the `uv` binary or its invocation semantics drift between Docker and CI, the lockfile guarantees weaken. A repo-level `required-version` gives local development a guardrail and documents the source of truth. Explicit CI `version:` inputs are still needed while this repo mixes `setup-uv@v4` and `@v5`, because their default version-discovery behaviour differs. |
| D11 | **Do not attempt Debian/PGDG snapshot pinning in v0; vendor the PGDG signing key, but accept live OS-package feeds as residual risk.** | Both builder and runtime stages use live apt indexes, and the runtime stage ships Debian/PGDG packages into the final image. Vendoring the PGDG signing key removes one unnecessary live trust-on-first-use fetch cheaply. Full apt snapshotting or internal mirrors would materially increase maintenance for modest benefit at current scale. Residual OS-package drift remains explicit and is monitored via `trivy image`. |
| D12 | **SBOM (per-image CycloneDX) and `model_artifacts.sha256_hash` (per-artifact runtime integrity) are complementary, not duplicative.** The new `## Supply chain` section in `security.md` must cross-reference both. | `sha256_hash` (existing, `security.md` §Model code trust) protects the runtime integrity of a specific model artifact at load time. SBOM answers "which historical image contains library X?" when a future CVE lands. Different mechanisms, different purposes. Documenting the relationship prevents readers from treating one as a substitute for the other. |
| D13 | **Use GitHub-hosted CI as the first execution environment for dependency-update installs, via a wheel-only guard (`uv sync --frozen --no-build --no-cache`) on dependency updates.** Document and review any accepted source-build exceptions instead of pretending they do not exist. | `uv --no-build` prevents running arbitrary Python build code from sdists, but cached built wheels can mask that requirement, so `--no-cache` is needed for a reliable guard. Running this on GitHub-hosted runners means new package versions are exercised on an ephemeral machine before a developer is expected to sync an updated lockfile locally. This reduces, but does not eliminate, platform-specific install-time code risk. |

---

## Task list

**Repo-standard uv version**: `0.11.7` (current `Dockerfile:5`). Tasks B5, C3, D1a all reference this single declared value. Any uv bump is a separate reviewed PR via Dependabot's `docker` ecosystem (A3).

### Stream A — Python dependency surface

#### A1 — Document PyPI as the default index

**File**: `pyproject.toml`

1. Add an explicit uv index block documenting PyPI:
   ```toml
   [[tool.uv.index]]
   name = "pypi"
   url = "https://pypi.org/simple"
   default = true
   ```
2. Note any existing `extra-index-url` or secondary-index references for
   reviewer awareness (search `pyproject.toml`, `.env.example`,
   `Dockerfile`, CI workflows). This task does not forbid secondary
   indexes — see D6. If any are found, document them briefly in the PR
   description; do not remove them as part of this task.
3. Run `uv sync --frozen` locally to confirm the lockfile still resolves
   against PyPI only.

**Exit**: `uv.lock` unchanged; `uv sync --frozen` succeeds.

#### A2 — `trivy fs` in the CI lint tier

**File**: `.github/workflows/ci.yml`, `.trivyignore` (new)

1. Add a step to the lint job that runs `trivy fs --exit-code 1 --severity HIGH,CRITICAL --ignore-unfixed --scanners vuln .`. This reads `uv.lock` and `pyproject.toml` directly — no image build required, so feedback stays fast on dep-only PRs.
2. Create `.trivyignore` at repo root for known-accepted CVEs. Each entry must carry a dated comment explaining why it is ignored and when to re-review. No undated entries.
3. Use the `aquasecurity/trivy-action` (SHA-pinned per C1) with advisory-DB caching enabled.

**Exit**: CI fails on a HIGH/CRITICAL Python CVE with an available fix (verified once via a throwaway branch that introduces a known-vulnerable dep, confirms CI failure, then reverts — verification artifact recorded in the PR); `trivy fs .` passes locally on a clean tree.

#### A3 — Dependabot for `uv`, `docker`, `docker-compose`, and `github-actions`

**File**: `.github/dependabot.yml` (new)

1. Create the file with four ecosystems: `uv` (targeting `/`, weekly),
   `docker` (targeting `/`, weekly), `docker-compose` (targeting `/`,
   weekly), and `github-actions` (weekly).
2. Group patch updates into a single PR per ecosystem; minor/major stay
   separate for review attention.
3. Assign the SAPPHIRE Flow maintainer(s) as reviewers.

**Exit**: `.github/dependabot.yml` passes `yamllint` (or equivalent YAML schema validation) and contains exactly the four ecosystems specified in A3 step 1 (`uv`, `docker`, `docker-compose`, `github-actions`), each with `schedule.interval: weekly`. Post-merge monitoring (NOT a merge gate): the first scheduled run should appear under Insights → Dependency graph → Dependabot within one week; initial update PRs should build cleanly.

#### A4 — Wheel-only guard for dependency-update installs

**File**: `.github/workflows/ci.yml`

1. Add a dedicated GitHub-hosted CI job or early step that runs
   `uv sync --frozen --no-build --no-cache`. This makes CI the first
   execution environment for dependency-update installs and fails if a
   new package version requires Python build-backend / sdist execution on
   the CI platform.
2. Prefer to run the guard on PRs that modify `pyproject.toml` or
   `uv.lock`; running it on every PR is also acceptable if it stays fast.
   The key requirement is that dependency-update PRs hit this guard
   before developers are expected to sync the updated lockfile locally.
3. Record accepted source-build exceptions by package, platform, and
   reason in the implementation PR and later in `security.md`. Current
   known exception: `exactextract` may require source builds in the
   linux/arm64 Docker builder path; standard GitHub-hosted amd64 CI
   should remain wheel-only.
4. Contributor workflow note: for dependency version bumps, prefer
   bot-opened PRs (Dependabot) or another disposable CI/container
   environment first. Avoid syncing a freshly updated lockfile on a
   long-lived developer workstation until this guard passes.

**Exit**: dependency-update PRs fail in GitHub-hosted CI if they
introduce new source-build execution on that platform; reviewers can
inspect CI before pulling an updated lockfile locally.

### Stream B — Container image pinning and scanning

#### B0 — Add an image-build step to CI

**File**: `.github/workflows/ci.yml`

1. Add a `build-image` job that runs `docker build -t sapphire-flow:ci-${{ github.sha }} .` on every PR. The current CI has no `docker build` step (e2e uses testcontainers directly), so B3 (`trivy image`) and B4 (`syft <image-tag>`) have nothing to scan without this.
2. Reuse the multi-stage `Dockerfile` as-is; no Dockerfile changes required by B0.
3. Keep the B0 build, B3 image scan, and B4 SBOM generation in the **same job / runner context** unless the image is explicitly exported and re-imported. GitHub-hosted jobs run on fresh VMs, so a local `sapphire-flow:ci-${{ github.sha }}` tag built in one job is **not** automatically available in another. If separate jobs are preferred, export a Docker image tarball from B0, upload it as an artifact, then download it and `docker load` it before B3/B4.
4. If `docker/build-push-action` is used for caching, set `load: true` (or an equivalent `type=docker` output) so later steps in the same job can run `trivy image` and `syft` against the local image. Any added action must still be SHA-pinned per C1.
5. Gate the e2e tier on the image build/scan path succeeding, not just on a bare `docker build`.

**Exit**: `docker build` runs on every PR; the resulting image is available to B3/B4 in the same job, or is explicitly passed across jobs via an uploaded image artifact plus `docker load`.

#### B1 — Pin Dockerfile base image by digest

**File**: `Dockerfile`

1. Resolve the current **manifest-list** digest for `python:3.11.12-slim` via `docker buildx imagetools inspect python:3.11.12-slim` — this is the top-level `Digest:` field, not any per-platform entry. Pin:
   `FROM python:3.11.12-slim@sha256:<manifest-list-digest>`.
   If the original tag should remain visible for reviewers, put it in a
   **preceding comment line**, not as an inline `# ...` comment on the
   same `FROM` line — Dockerfile treats `#` mid-line as part of the
   instruction arguments.
2. Apply to both build stages (builder and runtime).
3. Rebuild on **both** architectures (amd64 and arm64) to confirm the pin resolves correctly per-platform and the `exactextract` sdist compilation still works on arm64. A platform-specific digest would pass one build and silently fail the other — explicit dual-arch check catches the mistake. Verification point: CI covers amd64 (via B0); arm64 must be rebuilt locally on the Mac mini before merge. Record the arm64 build output in the PR description. The PR description MUST include a fenced code block labelled `arm64-build-output` containing at minimum: the `docker buildx` platform flag used, the resolved digest, the final image ID, and the exit code of a subsequent `uv run pytest -q tests/unit` inside the arm64 image. Reviewers MUST reject the PR if this block is absent. (There is no CI gate for arm64 — this prose requirement plus reviewer discipline is the verification path until the Mac mini is in a CI runner pool.)

**Exit**: `docker build` succeeds on both architectures with digest-pinned
base (amd64 in CI, arm64 on Mac mini with `arm64-build-output` block in
the PR description — both attested in the PR).

#### B2 — Pin docker-compose images by digest

**File**: `docker-compose.yml`

1. Pin `postgis/postgis:16-3.4`, `prefecthq/prefect:3-python3.11`, and
   `caddy:2.9` each to `@sha256:<manifest-list-digest>` (same
   manifest-list rule as B1) with the tag retained as an inline comment.
2. Confirm `docker compose pull && docker compose up` still brings the
   stack up locally on both amd64 and arm64 hosts (CI and Mac mini).
3. Do **not** digest-pin the local `sapphire-flow:${VERSION}` service
   build — it is built by us, not pulled.

**Exit**: `docker compose pull` succeeds; healthchecks still pass end-to-end.

#### B3 — `trivy image` scan on built images in CI

**File**: `.github/workflows/ci.yml`

1. After the B0 build step, run `trivy image --exit-code 1 --severity HIGH,CRITICAL --ignore-unfixed <built-tag>`. Prefer `trivy image` over `trivy fs` here — fs coverage is already provided by A2; the post-build scan's added value is OS-level layers, which only `image` mode sees. Depends on B0 and should run in the same job/runner context unless the image is explicitly exported/imported as described in B0.
2. Upload the Trivy SARIF output as a workflow artifact for audit.
3. Gate the e2e tier on Trivy passing.

**Exit**: CI fails on a HIGH/CRITICAL OS-level CVE with a fix available (one-shot verification procedure as for A2); SARIF artifact is produced.

#### B4 — SBOM generation with `syft`

**File**: `.github/workflows/ci.yml`

1. After the B0 build step, run `syft <image-tag> -o cyclonedx-json > sbom.cdx.json`. Depends on B0 and should run in the same job/runner context unless the image is explicitly exported/imported as described in B0.
2. Upload `sbom.cdx.json` as a workflow artifact on every run.
3. Explicitly defer release attachment / registry attestation until a real
   image-publish workflow exists. Do **not** add a release-creation action
   in this plan just to carry the SBOM.

**Exit**: Every CI run produces an SBOM artifact; the repo gains immediate
recoverability value without introducing a new release pipeline.

#### B5 — Pin the `uv` toolchain in the Dockerfile

**File**: `Dockerfile`

1. The Dockerfile already installs uv via `COPY --from=ghcr.io/astral-sh/uv:0.7.3 /uv /usr/local/bin/uv` (tag-pinned, no digest). Append the manifest-list digest to this existing line:
   ```
   COPY --from=ghcr.io/astral-sh/uv:0.7.3@sha256:<digest> /uv /usr/local/bin/uv
   ```
   Resolve the digest via `docker buildx imagetools inspect ghcr.io/astral-sh/uv:0.7.3`. This is an edit to an existing line, not a replacement of an `install` method — the task is ONLY to add the digest.
2. Pin to uv `0.11.7` — the repo-standard uv version declared above, which generated the committed `uv.lock`. Any future uv bump must be a deliberate, reviewed PR — Dependabot's `docker` ecosystem (A3) will raise these under review.
3. Apply to both build stages if `uv` is used in both.
4. Dependabot's `docker` ecosystem keeps the digest current under PR review.

**Exit**: `Dockerfile` contains no unpinned `uv` install; `docker build`
succeeds; `uv --version` inside the built image matches the pinned
version.

#### B6 — Vendor the PGDG signing key

**Files**: `docker/keys/apt.postgresql.org.asc` (new), `Dockerfile`

1. Add the current PostgreSQL Global Development Group signing key to the
   repo under `docker/keys/`.
2. Replace the runtime-stage `curl -fsSL
   https://www.postgresql.org/media/keys/ACCC4CF8.asc` fetch with a
   `COPY` from the repo.
3. Record the key's provenance / expected fingerprint in a short comment
   or doc note so future rotations are deliberate.
4. Do **not** attempt to snapshot Debian or PGDG package repos in this
   plan; that remains D11's accepted residual risk.

**Exit**: Docker builds no longer depend on a live network fetch for the
PGDG signing key; OS package versions remain a documented residual risk
covered by `trivy image`.

### Stream C — GitHub Actions pinning

#### C1 — SHA-pin every `uses:` reference

**File**: `.github/workflows/ci.yml` (and any other workflow files)

1. Replace every `uses: <org>/<action>@<tag>` with
   `uses: <org>/<action>@<sha>  # <tag>` in **all** workflow files under `.github/workflows/`:
   - `ci.yml`: `actions/checkout@v4` (4 occurrences), `astral-sh/setup-uv@v4` (4 occurrences)
   - `live-lindas-weekly.yml`: `actions/checkout@v4`, `astral-sh/setup-uv@v5`
   - Plus any action added by Streams A/B/C (e.g. `aquasecurity/trivy-action`, `docker/build-push-action`, `actions/upload-artifact`).

   Pin each tag separately — the `@v4` vs `@v5` split for `setup-uv` is a pre-existing state; unifying would be a separate decision outside this plan.
2. Resolve the commit SHA from the tag in a way that handles both
   lightweight and annotated tags:
   - Call `gh api repos/<org>/<action>/git/ref/tags/<tag>`.
   - If the returned `object.type` is `commit`, use `object.sha`
     directly.
   - If the returned `object.type` is `tag`, call
     `gh api repos/<org>/<action>/git/tags/<object.sha>` and use the
     nested `object.sha` commit SHA in `uses:`.
   - Do **not** pin the annotated-tag object's SHA; GitHub Actions
     expects the commit SHA.
3. Dependabot's `github-actions` ecosystem (A3) will keep these current
   under PR review going forward.

**Exit**: `grep -nE '^\s*-?\s*uses:\s*[^@#]+@v[0-9]' .github/workflows/*.yml` returns zero matches. The regex is anchored to the `uses:` directive and stops at `@` before any comment, so inline `# v4.2.2` trailing comments on SHA-pinned entries are tolerated and not misreported.

#### C2 — Pin service-container images in CI workflows

**File**: `.github/workflows/ci.yml` (and any other workflow file with a `services:` block)

1. Replace the `integration` job's `image: postgis/postgis:16-3.4` (line ~36) with `image: postgis/postgis:16-3.4@sha256:<manifest-list-digest>`, retaining the tag as an inline comment per the B2 pattern.
2. Resolve the digest the same way as B2 — `docker buildx imagetools inspect postgis/postgis:16-3.4` (top-level `Digest:` field).
3. This service image must match the compose `postgis` digest (B2) to avoid silent integration/prod drift. If the two diverge, one must intentionally lag; document the reason.
4. Treat this as a workflow-level pin that may still require manual upkeep
   even after A3; if Dependabot later proves able to update
   `services.image` references in workflows, note that in the
   implementation PR.

**Exit**: `grep -nE '^\s*image:\s*[^@#]+\s*$' .github/workflows/*.yml`
returns zero matches (all service images are digest-pinned).

#### C3 — Pin `uv` in CI and consume the lockfile in frozen mode

**Files**: `pyproject.toml`, `.github/workflows/ci.yml`,
`.github/workflows/live-lindas-weekly.yml`

1. Add `[tool.uv] required-version = "==0.11.7"` to `pyproject.toml`,
   matching the repo-standard uv version declared above (and pinned in
   B5) unless there is a deliberate reason to diverge.
2. For every `astral-sh/setup-uv` step, pass `with: version: 0.11.7`
   matching the repo-standard uv version declared above and
   `required-version`. This explicit CI pin is required while `ci.yml`
   still uses `setup-uv@v4`, whose default is `latest` rather than the
   project's `required-version`.
3. Replace workflow install steps from `uv sync` to `uv sync --frozen`.
4. Keep Python-version selection separate from uv-version pinning — the
   `python-version` input already exists for interpreter choice.
5. If the `setup-uv` major versions remain mixed (`@v4` in `ci.yml`,
   `@v5` in `live-lindas-weekly.yml`), pin both by SHA per C1 and apply
   the same explicit uv version input in both workflows.

**Exit**: local `uv` fails if it drifts from the repo-declared
`required-version`; CI installs that same deliberate uv version and
fails rather than re-resolving when lockfile / manifest drift exists.

### Stream D — Documentation

#### D1a — `security.md` supply-chain section + OWASP A06 row refresh

**File**: `docs/standards/security.md`

1. Add a `## Supply chain` section to `security.md` covering: Python dependency policy (uv lockfile + `[tool.uv].required-version` + Dependabot `uv` + frozen CI sync), the wheel-only dependency-update guard (`uv sync --frozen --no-build --no-cache`) plus any documented source-build exceptions (A4 / D13), image pinning (digest, not tag — base images, compose services, and CI service images), CI action pinning (SHA), CVE scanning layers (`trivy fs` in lint + `trivy image` post-build), SBOM generation (syft → CycloneDX artifact), the PyPI-default index policy (A1), the uv toolchain pin at the repo-standard uv version (0.11.7) across `pyproject.toml` + Docker + CI (B5 + C3), the vendored PGDG signing key (B6), and the accepted-risk note on live Debian / PGDG package feeds (D11).
2. Rewrite the existing OWASP A06 row in `security.md` (line ~447) so it reflects the **actual shipped control set after Plan 064**, not just a terminology cleanup. The row should:
   - drop "Renovate"
   - stop claiming that the `uv` lockfile "pins all dependencies" in an absolute sense
   - distinguish committed Python dependency resolution (`uv.lock`) from reviewed update automation (Dependabot for `uv`, `docker`, `docker-compose`, `github-actions`)
   - mention CI scanning as part of the vulnerable-components story (`trivy fs` for Python deps, `trivy image` for built images)
   Keep the row short, but make it substantively accurate.
3. Cross-reference `model_artifacts.sha256_hash` (existing, §Model code trust) from the new `## Supply chain` section — note that SBOM and `sha256_hash` are complementary per D12 (per-image CVE recoverability vs per-artifact runtime integrity), not substitutes.
4. Treat `security.md` as the **canonical policy source** for supply-chain controls. This is the normative-rules home; D1b's `cicd.md` edits point back here rather than duplicating the policy surface.

**Exit**: `docs/standards/security.md` has a `## Supply chain` section and an OWASP A06 row that accurately describes the shipped controls (`uv.lock`, Dependabot ecosystems, and Trivy scanning) without over-claiming that the lockfile alone "pins all dependencies". A search for "digest" / "SBOM" / "PGDG" in `security.md` returns results there (and the existing §Model code trust section for `sha256_hash`). `pip-audit` appears **nowhere** in shipped docs (it was never adopted — D4 dropped it).

#### D1b — `cicd.md` image-tagging rewrite + `:latest` rule supersede + build-tier doc

**File**: `docs/standards/cicd.md`

Depends on D1a — `cicd.md` must summarize the operational workflow and point back to `security.md` as the canonical policy source.

1. `cicd.md` should summarize the operational workflow and point back to `security.md` (per D1a) for the normative rules, instead of duplicating the full policy surface.
2. Rewrite the `## Image tagging and versioning` subsection in `docs/standards/cicd.md` so it explicitly separates three different concepts that are currently blurred together:
   - the locally built app image version tags (`sapphire-flow:${VERSION}`), including when operators update that tag during deployment
   - third-party image **digest** pins in compose / CI / Dockerfile, which are reviewed immutable references rather than operational version tags
   - CI validation builds and scans versus any separate image publish / release workflow
   Remove the stale claim that CI "builds and tags images on every merge to `main`" unless that workflow has actually shipped by the time this doc task lands. Also avoid wording that suggests third-party images are managed through the same tag-bump workflow as the local app image once digest pinning is in place.
3. Update `docs/standards/cicd.md`: supersede the weak "never uses `:latest` in production" rule (line ~229) with a pointer to the digest-pinning rule in `security.md`; document the new CI image-build tier added by B0 (between integration and e2e).
4. Cross-reference Plan 053's `## Future work` section (which deferred digest pinning to this plan) and Plan 064 (this plan) as the implementation record.

**Exit**: `docs/standards/cicd.md` no longer contains the bare `:latest` rule in isolation, no longer claims CI builds/tags images on every merge unless that is true in shipped workflow code, and its image-tagging / CI-tier text cleanly distinguishes local app version tags, third-party digest pins, validation builds, and any separate publish path. `cicd.md` points back to `security.md` as the canonical supply-chain policy source rather than duplicating it.

### Stream E — Image signing (OPTIONAL, gated on user decision)

#### E1 — Decide signing identity and verification posture

Before writing any tasks under this stream: agree on (a) the registry
images will be pushed to (GHCR vs hydrosolutions-controlled), (b) the
signing identity (keyless Sigstore OIDC via GitHub OIDC vs a managed KMS
key), and (c) whether staging and Nepal deploys will run `cosign verify`
at pull time. If any of these are unanswered, park the stream.

**Exit**: Either a written decision that unblocks E2, or explicit
deferral to a follow-up plan.

#### E2 — `cosign sign` in CI, `cosign verify` at deploy (only if E1 green)

Tasks to be specified after E1.

### Stream F — Runtime egress allowlist (OPTIONAL, gated on Plan 046)

#### F1 — Defer until Mac-mini staging topology is frozen

The staging host's network layout (bridge networking, Caddy reverse
proxy, outbound firewall) decides what shape an egress allowlist takes.
Park this stream until Plan 046 is DONE, then open a dedicated follow-up.

**Exit**: A one-line reference added to Plan 046's follow-ups section
so the work is not forgotten.

---

## Priority order (effect × effort)

Tasks ranked by impact-per-unit-effort. The top tier alone closes ~70% of
the exposure at ~20% of the work; run it first. Within a tier, tasks are
independent and can run in parallel.

### Tier 1 — High effect, low effort (do first)

| Rank | Task | Effect | Effort | Why |
|------|------|--------|--------|-----|
| 1 | **C1** — SHA-pin GitHub Actions | High | Low (~30 min) | Closes the `tj-actions/changed-files` (Mar 2025) class of attack. Mutable-tag exposure is the single largest uncontrolled input today. Scope now includes both `ci.yml` and `live-lindas-weekly.yml`. |
| 2 | **A3** — Dependabot config | High (compounding) | Low (one YAML file) | Unlocks automated upgrade PRs for the four ecosystems that actually exist here (`uv`, `docker`, `docker-compose`, `github-actions`). Ranked early for readiness — the Docker-related ecosystems become much more useful once digests land. |
| 3 | **B1 + B2 + C2** — Digest-pin Dockerfile, compose, and CI service images | Medium-high | Low (~1 h, including dual-arch rebuild) | Removes silent base-image swaps across all three locations. Group them — same pattern, same verification cycle. C2 covers `ci.yml:36` postgis service image. |
| 4 | **B5 + C3** — Pin `uv` across project config, Docker, and CI | High (leverage) | Low | `uv` enforces `uv.lock`. An unpinned or default-floating uv in local use, Docker, or CI weakens that guarantee. Pairing these tasks closes the loop cleanly. |

### Tier 2 — Medium effect, medium effort

| Rank | Task | Effect | Effort | Why |
|------|------|--------|--------|-----|
| 5 | **A4** — Wheel-only dependency-update guard | Medium-high | Low-medium | Keeps GitHub-hosted CI as the first place new dependency versions execute install logic, and fails when updates introduce new source-build / build-backend execution on the CI platform. Good protection against contaminating long-lived developer machines, with clear limits on non-CI platforms. |
| 6 | **A2** — `trivy fs` in lint tier | Medium-high | Medium (CI wiring + `.trivyignore` discipline) | Continuous visibility into Python CVEs. Value is recurring, not one-shot. |
| 7 | **B0** — Add image-build step to CI | Medium (enabler) | Medium | Prerequisite for B3 and B4. Without a built image, `trivy image` and `syft <image-tag>` have nothing to scan. Today's CI has no `docker build` step. |
| 8 | **B3** — `trivy image` post-build | Medium | Medium | Catches OS-level CVEs the fs scan misses. Depends on B0. Gated before e2e. |
| 9 | **B4** — SBOM generation | Medium (recoverability) | Low-medium | Zero value today; high value the first time a CVE lands and we need to know which historical image is affected. Cheap insurance. Depends on B0. |

### Tier 3 — Lower effect or narrower scope

| Rank | Task | Effect | Effort | Why |
|------|------|--------|--------|-----|
| 10 | **A1** — Document PyPI as default index | Low | Trivial | Useful explicitness for future changes, but `uv` is already reasonably safe by default. Do it because it is cheap, not because it closes a major hole. |
| 11 | **B6** — Vendor PGDG signing key | Low-medium | Low | Narrow scope, but a real improvement: removes one live key fetch from the Docker build without taking on full apt snapshot maintenance. |
| 12 | **D1a** — `security.md` supply-chain section + OWASP A06 row refresh | Low (doc hygiene) | Low | Capstone (first half) — must come last so the doc reflects shipped reality. Adds the new `## Supply chain` section and refreshes the OWASP A06 row. |
| 13 | **D1b** — `cicd.md` image-tagging rewrite + `:latest` rule supersede + build-tier doc | Low (doc hygiene) | Low | Capstone (second half). Depends on D1a so `cicd.md` can point back to `security.md` as the canonical policy source. Supersedes the `:latest` rule and documents the new build tier. |

### Gated — not part of the main execution graph

- **Stream E / E1** — User-decision task: agree on registry, signing identity, and verification posture. E1's output either unblocks E2 (sign + verify workflow) or parks the stream with an explicit deferral note. E1 is scheduled as a decision task, not a code task; E2 is specified only if E1 is green.
- **Stream F / F1** — Cross-reference task: add a one-line entry to Plan 046's follow-ups so the runtime egress allowlist work is not forgotten. F1 is a tiny piece of work in itself, but it is gated on Plan 046 being DONE before any substantive allowlist design follows. F1 sits outside the main dependency graph until that gate opens.

## Sequencing notes

- Within each tier, tasks are independent and can run in parallel **except**: B3 and B4 both depend on B0 (image build step).
- B0, B3, and B4 share a local image artifact. On GitHub-hosted runners they should normally run in the same job unless the image is intentionally exported as an artifact and re-loaded downstream.
- Tier 1 → Tier 2 → Tier 3 is a hard ordering: Tier 2 scanners assume Tier 1 Actions pinning is already in place (otherwise the scanner's own action is itself an unpinned liability).
- C2 pairs with B1/B2 in Tier 1 — same pattern, same verification cycle, same Dependabot maintenance story.
- A4 is most useful once A3 exists, because Dependabot PRs let GitHub-hosted CI exercise updated dependency installs before any developer is expected to sync the new lockfile locally. For manual dependency changes, prefer a disposable container / VM or wait for the PR CI to pass before syncing locally.
- C3 should land alongside B5 or immediately after it so CI and Docker do not drift on uv handling.
- Arm64 verification for B1/B2 is a manual rebuild on the Mac mini; CI is amd64-only. Arm64 build output must be recorded in the PR description before merge.
- B6 is independent and can land whenever the Dockerfile is already being touched.
- Stream D (D1a + D1b doc tasks) runs after every other streamed task lands so the standards doc reflects what shipped, not what was planned. D1a covers `security.md` (new `## Supply chain` section + A06 row refresh); D1b covers `cicd.md` (supersede `:latest` rule, document new build tier) and depends on D1a so `cicd.md` can reference `security.md` as the canonical policy source.
- Stream E is gated on an explicit user decision (E1); if accepted, E2 must use minimal workflow permissions (`id-token: write`, `contents: read`, plus any registry-specific permission) because signing increases the workflow privilege surface.
- Stream F is gated on Plan 046 completing.

## Dependency graph

Streams E (E1) and F (F1) are user-decision-gated / blocked on Plan 046
DONE and are NOT scheduled in the main execution graph below.

```json
{
  "phases": [
    {
      "id": "phase-tier1",
      "tasks": ["C1", "A3", "B1", "B2", "C2", "B5", "C3"],
      "parallel": true,
      "depends_on": []
    },
    {
      "id": "phase-tier2",
      "tasks": ["A4", "A2", "B0", "B6"],
      "parallel": true,
      "depends_on": ["phase-tier1"]
    },
    {
      "id": "phase-tier2b",
      "tasks": ["B3", "B4"],
      "parallel": true,
      "depends_on": ["phase-tier2"]
    },
    {
      "id": "phase-tier3",
      "tasks": ["A1"],
      "parallel": true,
      "depends_on": ["phase-tier2b"]
    },
    {
      "id": "phase-docs",
      "tasks": ["D1a", "D1b"],
      "parallel": true,
      "depends_on": ["phase-tier3"]
    }
  ],
  "gated": ["E1", "F1"]
}
```

Notes on the graph:

- **phase-tier2b** carries B3 and B4 because both require B0's built image in-context (see B0 step 3 / B3 step 1 / B4 step 1). They could run in the same job as B0, but modelling them as a dependent phase is cleaner for the execution graph.
- **phase-docs** (D1a, D1b) runs last so the standards docs describe what actually shipped. D1b depends on D1a in prose (points back to `security.md` as canonical), but both land in the same tier after tier-3 completes.
- `gated` lists tasks that are user-decision-gated (E1) or blocked on Plan 046 DONE (F1). They are tracked but not scheduled.

## Open questions for user review

1. ~~**Dependabot vs Renovate** (D1).~~ **Resolved 2026-04-20**: Dependabot. Free, already familiar, sufficient at 39 direct deps.
2. ~~**CVE severity gate** (D4).~~ **Resolved 2026-04-20**: HIGH+ with `--ignore-unfixed`. Catches more than CRITICAL-only; `--ignore-unfixed` already suppresses transitive-dep noise without a fix. Judgment calls on HIGH-with-fix are handled via `.trivyignore` with dated comments per A2.2.
3. **Image signing** (Stream E). Adopt now, or wait until the registry
   story is decided? My recommendation: wait.
4. **Runtime egress allowlist** (Stream F). Fold into this plan or keep
   as a separate follow-up? My recommendation: separate, gated on Plan 046.
5. ~~**Plan 053 back-link** — should this plan be linked from the Plan 053 archive record as the discharge of that deferral?~~ **Resolved 2026-04-20**: Not required by any convention. Leave Plan 053 archive as-is; rely on this plan's §Context.1 forward-pointer.

## Changelog

- **2026-04-20** — Initial DRAFT.
- **2026-04-20** — D1 resolved to Dependabot. D4 flipped to single-tool
  (`trivy` at both layers; `pip-audit` dropped). D5 cadence clarified
  (every tag, including patch). D6 rationale reframed (narrower — future
  `uv add` protection, not current `uv sync`). D10 added (pin `uv`
  toolchain). D11 added (apt transitivity note). B5 task added. B1/B2
  updated with manifest-list-digest caveat. Priority-order section
  added. Non-goal: secret scanning out of scope.
- **2026-04-20** — Critical-review feedback applied (three parallel
  Sonnet agents + synthesis). Factual corrections: B5 rewritten — uv is
  already installed via `COPY --from=ghcr.io/astral-sh/uv:0.7.3`, the
  task is to append the digest, not replace the install method; Plan 053
  citation corrected to `## Future work` (no "D2" label existed);
  direct-dep count corrected (39, not 38). Scope expansions: added
  `.github/workflows/live-lindas-weekly.yml` (uses `setup-uv@v5`) to
  C1; added new task C2 for the `postgis/postgis:16-3.4` service image
  in `ci.yml:36`. New task B0 added (CI image build step — prerequisite
  for B3 and B4; today's CI has no `docker build` step). D1 extended to
  cover `cicd.md` (supersede the `:latest` rule, document the new build
  tier) and the OWASP A06 row in `security.md`. D11 reframed as
  accepted residual risk (builder-stage apt is transient; does not
  reach runtime image). D12 added (SBOM vs `sha256_hash`
  complementarity). Open Q2 resolved (HIGH+ with `--ignore-unfixed`).
  Open Q5 resolved (no back-link required). Exit gates tightened:
  A2/B3 one-shot verification procedure; C1/C2 regex anchored to
  tolerate trailing `# v4.2.2` comments; A3 gate corrected (no manual
  repo-settings step — activation is config-driven). Stream E gains
  explicit `permissions: id-token: write, contents: read` note for
  keyless OIDC. B4 gains a release-creation prerequisite note.
  Disambiguation note added to the Architecture decisions header
  (Decision D1 vs Task D1 collision).
- **2026-04-21** — Tightened after repo-context review. Scope/principle
  language softened to avoid over-claiming full reproducibility. D1/A3
  corrected to use `uv`, `docker`, `docker-compose`, and
  `github-actions` (instead of folding compose into `docker`). D5/B4
  simplified to CI SBOM artifacts only — release attachment deferred
  until a real publish workflow exists. D10 expanded to cover CI uv
  pinning and `uv sync --frozen`; new task C3 added. D11 broadened to
  cover live Debian/PGDG package feeds; new task B6 added to vendor the
  PGDG signing key and remove the runtime-stage network key fetch.
- **2026-04-21** — Tightened again after implementation-focused review.
  Gap 4 renamed from "dependency review" to the control this plan
  actually adds (automated dependency-update PRs), while PR-time
  dependency review is now explicitly deferred from v0 scope. Gap 5
  wording aligned with the plan's real target (SBOM now; provenance /
  signing later). C1's SHA-resolution recipe corrected to the
  documented `git/ref/tags/<tag>` flow, including annotated-tag
  dereference. D10/C3 now add `[tool.uv] required-version` in
  `pyproject.toml` as the repo-level uv policy and keep explicit CI
  `version:` pins to stay safe while this repo still mixes
  `setup-uv@v4` and `setup-uv@v5`.
- **2026-04-21** — Tightened once more after CI/implementation review.
  B0/B3/B4 no longer assume a Docker image built in one GitHub-hosted
  job is magically available in later jobs by tag; the plan now
  requires build, `trivy image`, and `syft` to share a runner context
  unless the image is explicitly exported and re-imported as an
  artifact. B0 also now calls out `docker/build-push-action` `load:
  true` when a local image is needed for later steps. B1's Dockerfile
  example drops the inline `# tag` comment from the `FROM` line and
  moves that reviewer note to a preceding comment instead, matching
  Dockerfile parsing rules.
- **2026-04-21** — Install-time code execution guard added after
  follow-up review. Scope, context, non-goals, and docs now explicitly
  cover Python source-build / build-backend execution as a narrower
  supply-chain risk. New decision D13 and task A4 add a GitHub-hosted
  wheel-only guard for dependency updates using
  `uv sync --frozen --no-build --no-cache`, so updated packages are
  exercised on ephemeral CI runners before developers are expected to
  sync an updated lockfile locally. The plan now also documents that
  this reduces, but does not eliminate, platform-specific source-build
  risk (for example, the existing `exactextract` linux/arm64 builder
  exception).
- **2026-04-21** — Documentation task tightened after final review.
  D1 now explicitly treats `security.md` as the canonical supply-chain
  policy source and requires a real rewrite of `cicd.md`'s image-tagging
  / versioning section, including removal of the stale claim that CI
  builds and tags images on every merge to `main` unless that workflow
  truly exists by implementation time.
- **2026-04-21** — Documentation instructions tightened again. D1 now
  explicitly requires a substantive rewrite of the OWASP A06 row in
  `security.md` so it describes the real shipped control set (`uv.lock`,
  Dependabot ecosystems, Trivy scanning) without over-claiming that the
  lockfile alone "pins all dependencies". The `cicd.md` rewrite now also
  explicitly separates local app image version tags from third-party
  digest-pinned images, so the operational workflow cannot be read as one
  shared tag-bump process.
- **2026-04-21** — Opus self-review fixes applied. JSON dependency graph
  added after Sequencing notes. A1 reconciled with the softened D6
  (step 2 is informational, not enforcement; exit gate no longer asserts
  "no secondary indexes"). A3 exit gate rewritten to be pre-merge
  verifiable via `yamllint` + ecosystem schema check, with post-merge
  Dependabot observation demoted to monitoring. Streams E/F "do not
  schedule" heading renamed to "Gated — not part of the main execution
  graph" so E1 and F1 are correctly described as gated tasks. B1 step 3
  now requires an `arm64-build-output` fenced block in the PR description,
  and "Enforcement point" rewritten to "Verification point". D1 split
  into D1a (`security.md`) and D1b (`cicd.md`, depends on D1a) to
  eliminate the Decision-D1 / Task-D1 collision. Repo-standard uv
  version (0.7.3) declared once at the top of the Task list; B5 and C3
  placeholders replaced with explicit references.
