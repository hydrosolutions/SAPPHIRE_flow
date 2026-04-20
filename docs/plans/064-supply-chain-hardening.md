# Plan 064 — Supply-chain hardening

**Status**: DRAFT
**Date**: 2026-04-20
**Depends on**: none (independent of Plan 046 staging validation and Plan 054
doc sweep; coordinates with both via shared files — `docker-compose.yml`,
`Dockerfile`, `.github/workflows/ci.yml`, `docs/standards/security.md`,
`docs/standards/cicd.md`).
**Scope**: Close the supply-chain gaps surfaced by the 2026-04-20 audit.
Pins every third-party input the build consumes (Python dependencies, base
images, GitHub Actions), adds automated CVE scanning and dependency
update review, generates an SBOM per image, and documents the new posture
in `docs/standards/security.md`. Strictly defensive hardening — no change
to the forecast pipeline, API behaviour, or deployment topology. Image
signing and runtime egress allowlisting are proposed as optional streams
and gated on user confirmation.

---

## Context

### Why now

The Mac-mini staging deployment (Plan 046) is the first environment where
SAPPHIRE Flow pulls images, wheels, and GitHub Actions from the public
internet on a production-like host. Before Nepal deployment (Oct 2026) we
need the build to be reproducible and every external input to be
attributable, scannable, and upgradable under review. The audit on
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
4. **No automated dependency review.** `dependabot.yml` and
   `renovate.json` are both absent. Dependency upgrades happen manually
   and sporadically.
5. **No SBOM, no image signing.** Nothing downstream (staging, Nepal ops)
   can verify that an image pulled from our registry matches what CI
   built, or enumerate what is inside it.

The lockfile (`uv.lock`) is committed and the Dockerfile already runs
`uv sync --frozen`, so the Python side has resolver determinism. That is
the one strength to build on — the rest of the supply chain is unpinned.

### Principle

Every external input the build consumes must be: (a) pinned by immutable
identifier, (b) scanned for known vulnerabilities on every CI run, and
(c) reviewed before it updates. The goal is not zero risk — it is
*attributable* risk: when something breaks or a CVE lands, we can answer
"what changed and when?" in one `git log`.

### Non-goals

- **No runtime behaviour change.** No forecast, API, or ingestion code
  is modified by this plan.
- **No new secrets or credentials** beyond what a CI registry push would
  require (and only if the signing stream is accepted).
- **No mandatory egress allowlist in v0.** Stream E below is drafted as
  optional because the staging host topology is not yet frozen.
- **No replacement of `uv`.** The lockfile strategy stays; we harden
  what surrounds it.
- **No secret scanning configuration.** GitHub's native push-protection
  secret scanning is adjacent but a different category (it protects our
  secrets from being leaked outward, not our build from being compromised
  inward). Enable it through repo settings separately; out of scope for
  this plan.

### Inputs

- `Dockerfile` — two-stage, `python:3.11.12-slim` base, `uv sync --frozen --no-dev`; uv already installed via `COPY --from=ghcr.io/astral-sh/uv:0.7.3` (tag-only, no digest)
- `docker-compose.yml` — four services with tag-pinned external images (postgis, prefect-server, caddy, plus the locally-built `sapphire-flow:${VERSION}` image used by worker/api/init)
- `.github/workflows/ci.yml` — lint/unit/integration/e2e tiers, tag-pinned actions; integration job declares `postgis/postgis:16-3.4` as a `services.image` (unpinned, outside compose)
- `.github/workflows/live-lindas-weekly.yml` — tag-pinned actions, uses `astral-sh/setup-uv@v5` (different major from `ci.yml`)
- `pyproject.toml` / `uv.lock` — 39 direct deps (26 runtime + 13 dev), lockfile committed
- `docs/standards/security.md` — container hardening, secrets, auth (no `## Supply chain` section today; OWASP A06 row already mentions "Dependabot/Renovate for update alerts" as if configured — stale)
- `docs/standards/cicd.md` — compose topology, volumes, health checks (has a weak "never uses `:latest` in production" rule; no image-build tier documented)

---

## Architecture decisions

> Naming: decisions are numbered D1–D12 in this table. Task codes use
> stream-letter + number (A1, B1, C1, D1 doc task). "Decision D1" and
> "Task D1" share a label; context disambiguates.

| # | Decision | Rationale |
|---|---|---|
| D1 | **Dependabot, not Renovate**, for dependency update PRs, scoped to `pip` (via `pyproject.toml`), `docker` (compose + Dockerfile), and `github-actions`. | Dependabot is first-party on GitHub, needs no external app install, and the config surface (`.github/dependabot.yml`) is small. Renovate is more powerful but the extra power (grouping, custom managers, schedules) is not needed at 39 direct deps. Revisit if we outgrow it. |
| D2 | **Pin every external image by the manifest-list digest** (`image@sha256:...`) in `Dockerfile` and `docker-compose.yml`, not a platform-specific digest. Dependabot's `docker` ecosystem keeps digests current under PR review. | Tags are mutable. A digest pin makes the image a fixed input. The manifest-list digest is what `docker buildx imagetools inspect <tag>` returns — it resolves per-arch on pull, so the same pin works for both amd64 (CI) and arm64 (Mac mini). A platform-specific digest would break cross-arch pulls. Dependabot removes the "manual upgrade is painful" objection by producing the PR for us. |
| D3 | **Pin every GitHub Action by commit SHA**, with the version tag retained as a trailing comment (`uses: actions/checkout@<sha>  # v4.2.2`). | Action tags are mutable. SHAs are immutable. The comment preserves human-readable versioning for review. GitHub's own security guidance and Dependabot's `github-actions` ecosystem both assume this pattern. |
| D4 | **Single CVE scanner, two layers**: `trivy fs .` in the lint tier (scans `uv.lock` + `pyproject.toml` without building the image — fast feedback on dep-only PRs) and `trivy image <tag>` after the build (catches OS-level CVEs). Both fail CI on HIGH+ unfixed. | Earlier draft had `pip-audit` + `trivy`. Trivy already reads `uv.lock` in `fs` mode, so `pip-audit` duplicates ~80% of coverage with negligible Python-advisory edge. One tool means one config, one ignore-list (`.trivyignore`), and one set of CI steps to reason about. Drop the duplication. |
| D5 | **Generate a CycloneDX SBOM with `syft` per built image**, uploaded as a CI artifact on every run and attached to the GitHub Release on **every** tagged build (including patch releases). | SBOM is a recoverability tool: when a future CVE lands, we need to know which historical image contains the affected library. CLAUDE.md bumps a patch version per commit, so "every tag" means one SBOM per shipped commit — storage is cheap, recoverability wins over a tidy Releases page. Syft emits CycloneDX without requiring a signing key. Cheap to add; expensive to add retroactively. |
| D6 | **Add an explicit `[[tool.uv.index]]` block pinning PyPI as the sole index** and forbidding secondary-index fallback. | `uv.lock` already records the source URL per package, so `uv sync --frozen` is protected today. What D6 actually constrains is **future `uv add`**: new dependencies must resolve through PyPI only, not through a secondary index that could leak to a typosquat. Narrow but real — cheap insurance ahead of v1 when we may introduce a private registry. |
| D7 | **Image signing via `cosign` is proposed as an OPTIONAL stream (E)** — needs a signing identity (keyless Sigstore OIDC or a managed key) and a decision on whether staging/Nepal will run `cosign verify` at pull time. Default: defer until the registry story is settled. | Signing without verification is ceremony. The verification side needs a deployment decision we have not made yet (are we pushing to GHCR? a hydrosolutions-controlled registry?). Parking until that is clear avoids premature lock-in. |
| D8 | **Runtime egress allowlist (Stream F)** is also OPTIONAL and deferred to the Mac-mini staging plan (Plan 046) or a dedicated follow-up. | Egress controls depend on the host network topology (bridge vs host networking, Caddy reverse-proxy layout) and are better designed once staging is real. |
| D9 | **Document the new posture in `docs/standards/security.md` §Supply Chain** as part of the last task, not sprinkled across each change. | Matches the convention used by Plan 054 T6 — doc change lands as the capstone, not scattered edits. |
| D10 | **Pin the `uv` toolchain itself in the `Dockerfile`** by copying the binary from a digest-pinned `ghcr.io/astral-sh/uv:<version>` image (`COPY --from=ghcr.io/astral-sh/uv:0.x.y@sha256:... /uv /usr/local/bin/uv`). | `uv` is the tool that enforces `uv.lock`. If the `uv` binary itself is swapped at build time, `uv sync --frozen` guarantees nothing. Pinning uv by image digest closes the loop. Dependabot's `docker` ecosystem will keep it current under PR review. |
| D11 | **apt packages in the builder stage (`build-essential`, `cmake`, `libgeos-dev` for the `exactextract` arm64 sdist) are an accepted residual risk** — not pinned, not snapshotted. | The builder stage runs `apt-get update && apt-get install`, which hits the live Debian mirror at build time — package versions can drift between builds. BUT: the builder is transient. Only the compiled `.venv` is copied into the runtime image; no apt-installed package reaches the deployed artifact surface. Version drift affects the wheel built, not the shipped image. Explicit apt snapshots (snapshot.debian.org, apt preferences) are fragile and high-maintenance for a builder-only concern. Accepted; revisit if a build-reproducibility incident occurs or if requirements tighten. |
| D12 | **SBOM (per-image CycloneDX) and `model_artifacts.sha256_hash` (per-artifact runtime integrity) are complementary, not duplicative.** The new `## Supply chain` section in `security.md` must cross-reference both. | `sha256_hash` (existing, `security.md` §Model code trust) protects the runtime integrity of a specific model artifact at load time. SBOM answers "which historical image contains library X?" when a future CVE lands. Different mechanisms, different purposes. Documenting the relationship prevents readers from treating one as a substitute for the other. |

---

## Task list

### Stream A — Python dependency surface

#### A1 — Pin PyPI as the sole index

**File**: `pyproject.toml`

1. Add an explicit uv index block pinning PyPI:
   ```toml
   [[tool.uv.index]]
   name = "pypi"
   url = "https://pypi.org/simple"
   default = true
   ```
2. Confirm no `extra-index-url` or secondary index is referenced anywhere
   (search `pyproject.toml`, `.env.example`, `Dockerfile`, CI workflows).
3. Run `uv sync --frozen` locally to confirm the lockfile still resolves
   against PyPI only.

**Exit**: `uv.lock` unchanged; `uv sync --frozen` succeeds; no secondary
indexes referenced in the repo.

#### A2 — `trivy fs` in the CI lint tier

**File**: `.github/workflows/ci.yml`, `.trivyignore` (new)

1. Add a step to the lint job that runs `trivy fs --exit-code 1 --severity HIGH,CRITICAL --ignore-unfixed --scanners vuln .`. This reads `uv.lock` and `pyproject.toml` directly — no image build required, so feedback stays fast on dep-only PRs.
2. Create `.trivyignore` at repo root for known-accepted CVEs. Each entry must carry a dated comment explaining why it is ignored and when to re-review. No undated entries.
3. Use the `aquasecurity/trivy-action` (SHA-pinned per C1) with advisory-DB caching enabled.

**Exit**: CI fails on a HIGH/CRITICAL Python CVE with an available fix (verified once via a throwaway branch that introduces a known-vulnerable dep, confirms CI failure, then reverts — verification artifact recorded in the PR); `trivy fs .` passes locally on a clean tree.

#### A3 — Dependabot for `pip`, `docker`, and `github-actions`

**File**: `.github/dependabot.yml` (new)

1. Create the file with three ecosystems: `pip` (targeting `/`, weekly),
   `docker` (targeting `/` and `/docker-compose.yml` if separately addressable,
   weekly), `github-actions` (weekly).
2. Group patch updates into a single PR per ecosystem; minor/major stay
   separate for review attention.
3. Assign the SAPPHIRE Flow maintainer(s) as reviewers.

**Exit**: `.github/dependabot.yml` merged (activation is config-driven — no separate repo-settings toggle is required); first scheduled run is visible under Insights → Dependency graph → Dependabot; initial PRs build cleanly.

### Stream B — Container image pinning and scanning

#### B0 — Add an image-build step to CI

**File**: `.github/workflows/ci.yml`

1. Add a `build` job that runs `docker build -t sapphire-flow:ci-${{ github.sha }} .` on every PR. The current CI has no `docker build` step (e2e uses testcontainers directly), so B3 (`trivy image`) and B4 (`syft <image-tag>`) have nothing to scan without this.
2. Reuse the multi-stage `Dockerfile` as-is; no Dockerfile changes required by B0.
3. Cache layers via `docker/build-push-action` with its built-in cache, or `actions/cache` — both must be SHA-pinned per C1 when added.
4. Gate the e2e tier on the build job succeeding.

**Exit**: `docker build` runs on every PR; the built image tag is available as a downstream input for B3/B4.

#### B1 — Pin Dockerfile base image by digest

**File**: `Dockerfile`

1. Resolve the current **manifest-list** digest for `python:3.11.12-slim` via `docker buildx imagetools inspect python:3.11.12-slim` — this is the top-level `Digest:` field, not any per-platform entry. Pin:
   `FROM python:3.11.12-slim@sha256:<manifest-list-digest>  # python:3.11.12-slim`.
2. Apply to both build stages (builder and runtime).
3. Rebuild on **both** architectures (amd64 and arm64) to confirm the pin resolves correctly per-platform and the `exactextract` sdist compilation still works on arm64. A platform-specific digest would pass one build and silently fail the other — explicit dual-arch check catches the mistake. Enforcement point: CI covers amd64 (via B0); arm64 must be rebuilt locally on the Mac mini before merge. Record the arm64 build output in the PR description.

**Exit**: `docker build` succeeds on both architectures with digest-pinned
base (amd64 in CI, arm64 on Mac mini — both attested in the PR).

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

#### B3 — `trivy fs` scan on built images in CI

**File**: `.github/workflows/ci.yml`

1. After the B0 build step, run `trivy image --exit-code 1 --severity HIGH,CRITICAL --ignore-unfixed <built-tag>`. Prefer `trivy image` over `trivy fs` here — fs coverage is already provided by A2; the post-build scan's added value is OS-level layers, which only `image` mode sees. Depends on B0.
2. Upload the Trivy SARIF output as a workflow artifact for audit.
3. Gate the e2e tier on Trivy passing.

**Exit**: CI fails on a HIGH/CRITICAL OS-level CVE with a fix available (one-shot verification procedure as for A2); SARIF artifact is produced.

#### B4 — SBOM generation with `syft`

**File**: `.github/workflows/ci.yml`

1. After the B0 build step, run `syft <image-tag> -o cyclonedx-json > sbom.cdx.json`. Depends on B0.
2. Upload `sbom.cdx.json` as a workflow artifact on every run; attach to
   the GitHub Release on **every** tagged build (`v*`), including patch
   releases — cadence per D5. Prerequisite: a release-creation step must exist on tag push. If no such step exists today, add `softprops/action-gh-release` (SHA-pinned per C1) as part of this task — confirm during implementation.
3. Retention: workflow artifacts 90 days; release-attached SBOMs
   retained indefinitely with the release record.

**Exit**: Every CI run produces an SBOM artifact; tagged releases carry
an attached `sbom.cdx.json` (release-creation step in place and SHA-pinned).

#### B5 — Pin the `uv` toolchain in the Dockerfile

**File**: `Dockerfile`

1. The Dockerfile already installs uv via `COPY --from=ghcr.io/astral-sh/uv:0.7.3 /uv /usr/local/bin/uv` (tag-pinned, no digest). Append the manifest-list digest to this existing line:
   ```
   COPY --from=ghcr.io/astral-sh/uv:0.7.3@sha256:<digest> /uv /usr/local/bin/uv
   ```
   Resolve the digest via `docker buildx imagetools inspect ghcr.io/astral-sh/uv:0.7.3`. This is an edit to an existing line, not a replacement of an `install` method — the task is ONLY to add the digest.
2. Pin to uv `0.7.3` (the version currently in `Dockerfile:3`, which generated the committed `uv.lock`). Any future uv bump must be a deliberate, reviewed PR — Dependabot's `docker` ecosystem (A3) will raise these under review.
3. Apply to both build stages if `uv` is used in both.
4. Dependabot's `docker` ecosystem keeps the digest current under PR review.

**Exit**: `Dockerfile` contains no unpinned `uv` install; `docker build`
succeeds; `uv --version` inside the built image matches the pinned
version.

### Stream C — GitHub Actions pinning

#### C1 — SHA-pin every `uses:` reference

**File**: `.github/workflows/ci.yml` (and any other workflow files)

1. Replace every `uses: <org>/<action>@<tag>` with
   `uses: <org>/<action>@<sha>  # <tag>` in **all** workflow files under `.github/workflows/`:
   - `ci.yml`: `actions/checkout@v4` (4 occurrences), `astral-sh/setup-uv@v4` (4 occurrences)
   - `live-lindas-weekly.yml`: `actions/checkout@v4`, `astral-sh/setup-uv@v5`
   - Plus any action added by Streams A/B/C (e.g. `aquasecurity/trivy-action`, `anchore/sbom-action`, `docker/build-push-action`, `softprops/action-gh-release`).

   Pin each tag separately — the `@v4` vs `@v5` split for `setup-uv` is a pre-existing state; unifying would be a separate decision outside this plan.
2. Resolve the SHA from the tag at the current release: `gh api repos/<org>/<action>/git/refs/tags/<tag>`.
3. Dependabot's `github-actions` ecosystem (A3) will keep these current
   under PR review going forward.

**Exit**: `grep -nE '^\s*-?\s*uses:\s*[^@#]+@v[0-9]' .github/workflows/*.yml` returns zero matches. The regex is anchored to the `uses:` directive and stops at `@` before any comment, so inline `# v4.2.2` trailing comments on SHA-pinned entries are tolerated and not misreported.

#### C2 — Pin service-container images in CI workflows

**File**: `.github/workflows/ci.yml` (and any other workflow file with a `services:` block)

1. Replace the `integration` job's `image: postgis/postgis:16-3.4` (line ~36) with `image: postgis/postgis:16-3.4@sha256:<manifest-list-digest>`, retaining the tag as an inline comment per the B2 pattern.
2. Resolve the digest the same way as B2 — `docker buildx imagetools inspect postgis/postgis:16-3.4` (top-level `Digest:` field).
3. This service image must match the compose `postgis` digest (B2) to avoid silent integration/prod drift. If the two diverge, one must intentionally lag; document the reason.
4. Dependabot's `docker` ecosystem (A3) keeps this current under PR review going forward.

**Exit**: `grep -nE '^\s*image:\s*[^@#]+\s*$' .github/workflows/*.yml` returns zero matches (all service images are digest-pinned).

### Stream D — Documentation

#### D1 — Supply-chain documentation sweep

**Files**: `docs/standards/security.md`, `docs/standards/cicd.md`

1. Add a `## Supply chain` section to `security.md` covering: Python dependency policy (uv lockfile + Dependabot), image pinning (digest, not tag — base images, compose services, and CI service images), CI action pinning (SHA), CVE scanning layers (`trivy fs` in lint + `trivy image` post-build), SBOM generation (syft → CycloneDX), the PyPI-only index policy (A1), the uv toolchain pin (B5), and the accepted-risk note on builder-stage apt (D11).
2. Update the existing OWASP A06 row in `security.md` (line ~447) to reflect Dependabot-only (drop "Renovate") and name the three ecosystems (`pip`, `docker`, `github-actions`).
3. Cross-reference `model_artifacts.sha256_hash` (existing, §Model code trust) from the new `## Supply chain` section — note that SBOM and `sha256_hash` are complementary per D12 (per-image CVE recoverability vs per-artifact runtime integrity), not substitutes.
4. Update `docs/standards/cicd.md`: supersede the weak "never uses `:latest` in production" rule (line ~229) with a pointer to the digest-pinning rule in `security.md`; document the new CI image-build tier added by B0 (between integration and e2e).
5. Cross-reference Plan 053's `## Future work` section (which deferred digest pinning to this plan) and Plan 064 (this plan) as the implementation record.

**Exit**: `docs/standards/security.md` has a `## Supply chain` section; a search for "digest" / "SBOM" in `security.md` returns results there (and the existing §Model code trust section for `sha256_hash`). `pip-audit` appears **nowhere** in shipped docs (it was never adopted — D4 dropped it). `docs/standards/cicd.md` no longer contains the bare `:latest` rule in isolation; its CI tier list documents the new build step.

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
| 2 | **A3** — Dependabot config | High (compounding) | Low (one YAML file) | Unlocks automated upgrade PRs for all three ecosystems (`pip`, `docker`, `github-actions`). Ranked early for readiness — the `docker` ecosystem produces zero PRs until digests land, so A3's effective coverage starts when B1/B2/B5/C2 merge. |
| 3 | **B1 + B2 + C2** — Digest-pin Dockerfile, compose, and CI service images | Medium-high | Low (~1 h, including dual-arch rebuild) | Removes silent base-image swaps across all three locations. Group them — same pattern, same verification cycle. C2 is new in this revision (covers `ci.yml:36` postgis service image). |
| 4 | **B5** — Pin `uv` toolchain (append digest to existing `COPY --from`) | High (leverage) | Low | `uv` enforces `uv.lock`. An unpinned `uv` makes the locked environment only as trustworthy as wherever `uv` was fetched from. Today the Dockerfile already uses `COPY --from=ghcr.io/astral-sh/uv:0.7.3` — B5 just appends the digest. |

### Tier 2 — Medium effect, medium effort

| Rank | Task | Effect | Effort | Why |
|------|------|--------|--------|-----|
| 5 | **A2** — `trivy fs` in lint tier | Medium-high | Medium (CI wiring + `.trivyignore` discipline) | Continuous visibility into Python CVEs. Value is recurring, not one-shot. |
| 6 | **B0** — Add image-build step to CI | Medium (enabler) | Medium | Prerequisite for B3 and B4. Without a built image, `trivy image` and `syft <image-tag>` have nothing to scan. Today's CI has no `docker build` step. |
| 7 | **B3** — `trivy image` post-build | Medium | Medium | Catches OS-level CVEs the fs scan misses. Depends on B0. Gated before e2e. |
| 8 | **B4** — SBOM generation | Medium (recoverability) | Low-medium | Zero value today; high value the first time a CVE lands and we need to know which historical image is affected. Cheap insurance. Depends on B0. |

### Tier 3 — Lower effect or narrower scope

| Rank | Task | Effect | Effort | Why |
|------|------|--------|--------|-----|
| 9 | **A1** — Pin PyPI as sole index | Low-medium | Trivial | Narrow scope per D6 (constrains future `uv add`, not `uv sync`). Do it because it's trivial, not because it's urgent. |
| 10 | **D1** — Standards doc sweep (security.md + cicd.md) | Low (doc hygiene) | Low-medium | Capstone — must come last so the doc reflects shipped reality. Now covers `cicd.md` as well (supersede `:latest` rule; document the new build tier) and the OWASP A06 row in `security.md`. |

### Deferred / optional (do not schedule)

- **Stream E** — Image signing. Gated on registry/verification decision.
- **Stream F** — Runtime egress allowlist. Gated on Plan 046 staging topology.

## Sequencing notes

- Within each tier, tasks are independent and can run in parallel **except**: B3 and B4 both depend on B0 (image build step).
- Tier 1 → Tier 2 → Tier 3 is a hard ordering: Tier 2 scanners assume Tier 1 Actions pinning is already in place (otherwise the scanner's own action is itself an unpinned liability).
- C2 (new) pairs with B1/B2 in Tier 1 — same pattern, same verification cycle, same Dependabot maintenance story.
- Arm64 verification for B1/B2 is a manual rebuild on the Mac mini; CI is amd64-only. Arm64 build output must be recorded in the PR description before merge.
- Stream D (D1 doc task) runs after every other streamed task lands so the standards doc reflects what shipped, not what was planned. D1 now covers both `security.md` (new `## Supply chain` section + A06 row refresh) and `cicd.md` (supersede `:latest` rule, document new build tier).
- Stream E is gated on an explicit user decision (E1); if accepted, E2 must annotate the signing workflow with `permissions: id-token: write, contents: read` (keyless Sigstore OIDC requirement). Unpinned `permissions:` blocks are themselves a supply-chain surface.
- Stream F is gated on Plan 046 completing.

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
