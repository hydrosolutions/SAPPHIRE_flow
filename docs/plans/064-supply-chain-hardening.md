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
   `caddy:2.9`. Any of these tags can be re-pushed upstream, silently
   changing what ships. Plan 053 D2 explicitly deferred digest pinning to
   "a future security-hardening plan" — this is that plan.
2. **GitHub Actions are tag-pinned, not SHA-pinned.** `.github/workflows/ci.yml`
   references `actions/checkout@v4` and `astral-sh/setup-uv@v4`. Action
   tags are mutable; a compromise of a popular action (precedent: `tj-actions/changed-files`, Mar 2025) can
   inject steps into every CI run.
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

- `Dockerfile` — two-stage, `python:3.11.12-slim` base, `uv sync --frozen`
- `docker-compose.yml` — four services with tag-pinned external images
- `.github/workflows/ci.yml` — lint/unit/integration/e2e tiers, tag-pinned actions
- `pyproject.toml` / `uv.lock` — 38 direct deps, lockfile committed
- `docs/standards/security.md` — container hardening, secrets, auth (no supply-chain section today)
- `docs/standards/cicd.md` — compose topology, volumes, health checks

---

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Dependabot, not Renovate**, for dependency update PRs, scoped to `pip` (via `pyproject.toml`), `docker` (compose + Dockerfile), and `github-actions`. | Dependabot is first-party on GitHub, needs no external app install, and the config surface (`.github/dependabot.yml`) is small. Renovate is more powerful but the extra power (grouping, custom managers, schedules) is not needed at 38 direct deps. Revisit if we outgrow it. |
| D2 | **Pin every external image by the manifest-list digest** (`image@sha256:...`) in `Dockerfile` and `docker-compose.yml`, not a platform-specific digest. Dependabot's `docker` ecosystem keeps digests current under PR review. | Tags are mutable. A digest pin makes the image a fixed input. The manifest-list digest is what `docker buildx imagetools inspect <tag>` returns — it resolves per-arch on pull, so the same pin works for both amd64 (CI) and arm64 (Mac mini). A platform-specific digest would break cross-arch pulls. Dependabot removes the "manual upgrade is painful" objection by producing the PR for us. |
| D3 | **Pin every GitHub Action by commit SHA**, with the version tag retained as a trailing comment (`uses: actions/checkout@<sha>  # v4.2.2`). | Action tags are mutable. SHAs are immutable. The comment preserves human-readable versioning for review. GitHub's own security guidance and Dependabot's `github-actions` ecosystem both assume this pattern. |
| D4 | **Single CVE scanner, two layers**: `trivy fs .` in the lint tier (scans `uv.lock` + `pyproject.toml` without building the image — fast feedback on dep-only PRs) and `trivy image <tag>` after the build (catches OS-level CVEs). Both fail CI on HIGH+ unfixed. | Earlier draft had `pip-audit` + `trivy`. Trivy already reads `uv.lock` in `fs` mode, so `pip-audit` duplicates ~80% of coverage with negligible Python-advisory edge. One tool means one config, one ignore-list (`.trivyignore`), and one set of CI steps to reason about. Drop the duplication. |
| D5 | **Generate a CycloneDX SBOM with `syft` per built image**, uploaded as a CI artifact on every run and attached to the GitHub Release on **every** tagged build (including patch releases). | SBOM is a recoverability tool: when a future CVE lands, we need to know which historical image contains the affected library. CLAUDE.md bumps a patch version per commit, so "every tag" means one SBOM per shipped commit — storage is cheap, recoverability wins over a tidy Releases page. Syft emits CycloneDX without requiring a signing key. Cheap to add; expensive to add retroactively. |
| D6 | **Add an explicit `[[tool.uv.index]]` block pinning PyPI as the sole index** and forbidding secondary-index fallback. | `uv.lock` already records the source URL per package, so `uv sync --frozen` is protected today. What D6 actually constrains is **future `uv add`**: new dependencies must resolve through PyPI only, not through a secondary index that could leak to a typosquat. Narrow but real — cheap insurance ahead of v1 when we may introduce a private registry. |
| D7 | **Image signing via `cosign` is proposed as an OPTIONAL stream (E)** — needs a signing identity (keyless Sigstore OIDC or a managed key) and a decision on whether staging/Nepal will run `cosign verify` at pull time. Default: defer until the registry story is settled. | Signing without verification is ceremony. The verification side needs a deployment decision we have not made yet (are we pushing to GHCR? a hydrosolutions-controlled registry?). Parking until that is clear avoids premature lock-in. |
| D8 | **Runtime egress allowlist (Stream F)** is also OPTIONAL and deferred to the Mac-mini staging plan (Plan 046) or a dedicated follow-up. | Egress controls depend on the host network topology (bridge vs host networking, Caddy reverse-proxy layout) and are better designed once staging is real. |
| D9 | **Document the new posture in `docs/standards/security.md` §Supply Chain** as part of the last task, not sprinkled across each change. | Matches the convention used by Plan 054 T6 — doc change lands as the capstone, not scattered edits. |
| D10 | **Pin the `uv` toolchain itself in the `Dockerfile`** by copying the binary from a digest-pinned `ghcr.io/astral-sh/uv:<version>` image (`COPY --from=ghcr.io/astral-sh/uv:0.x.y@sha256:... /uv /usr/local/bin/uv`). | `uv` is the tool that enforces `uv.lock`. If the `uv` binary itself is swapped at build time, `uv sync --frozen` guarantees nothing. Pinning uv by image digest closes the loop. Dependabot's `docker` ecosystem will keep it current under PR review. |
| D11 | **apt packages in the builder stage (`build-essential`, `cmake`, `libgeos-dev` for the `exactextract` arm64 sdist) inherit pinning transitively from the digest-pinned base image** — no separate stream. | The Debian repo snapshot reachable from a given `python:3.11.12-slim@sha256:...` is effectively frozen because the base image's apt sources and index are frozen with it. Explicitly pinning individual apt packages would be fragile (versions move with Debian point releases) and redundant. |

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

**Exit**: CI fails on a HIGH/CRITICAL Python CVE with an available fix; `trivy fs .` passes locally on a clean tree.

#### A3 — Dependabot for `pip`, `docker`, and `github-actions`

**File**: `.github/dependabot.yml` (new)

1. Create the file with three ecosystems: `pip` (targeting `/`, weekly),
   `docker` (targeting `/` and `/docker-compose.yml` if separately addressable,
   weekly), `github-actions` (weekly).
2. Group patch updates into a single PR per ecosystem; minor/major stay
   separate for review attention.
3. Assign the SAPPHIRE Flow maintainer(s) as reviewers.

**Exit**: Dependabot enabled in the repo settings (manual step after merge);
first scan produces PRs that build cleanly.

### Stream B — Container image pinning and scanning

#### B1 — Pin Dockerfile base image by digest

**File**: `Dockerfile`

1. Resolve the current **manifest-list** digest for `python:3.11.12-slim` via `docker buildx imagetools inspect python:3.11.12-slim` — this is the top-level `Digest:` field, not any per-platform entry. Pin:
   `FROM python:3.11.12-slim@sha256:<manifest-list-digest>  # python:3.11.12-slim`.
2. Apply to both build stages (builder and runtime).
3. Rebuild locally on **both** architectures (amd64 and arm64) to confirm the pin resolves correctly per-platform and the `exactextract` sdist compilation still works on arm64. A platform-specific digest would pass one build and silently fail the other — explicit dual-arch check catches the mistake.

**Exit**: `docker build` succeeds on both architectures with digest-pinned
base.

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

1. After the image build step, run `trivy fs --exit-code 1 --severity HIGH,CRITICAL --ignore-unfixed .` (or `trivy image` against the built tag).
2. Upload the Trivy SARIF output as a workflow artifact for audit.
3. Gate the e2e tier on Trivy passing.

**Exit**: CI fails on a HIGH/CRITICAL OS-level CVE with a fix available;
SARIF artifact is produced.

#### B4 — SBOM generation with `syft`

**File**: `.github/workflows/ci.yml`

1. After image build, run `syft <image-tag> -o cyclonedx-json > sbom.cdx.json`.
2. Upload `sbom.cdx.json` as a workflow artifact on every run; attach to
   the GitHub Release on **every** tagged build (`v*`), including patch
   releases — cadence per D5.
3. Retention: workflow artifacts 90 days; release-attached SBOMs
   retained indefinitely with the release record.

**Exit**: Every CI run produces an SBOM artifact; tagged releases carry
an attached `sbom.cdx.json`.

#### B5 — Pin the `uv` toolchain in the Dockerfile

**File**: `Dockerfile`

1. Replace the current `uv` install (whatever its form — `curl | sh`, `pip install uv`, or similar) with a digest-pinned `COPY --from`:
   ```
   COPY --from=ghcr.io/astral-sh/uv:0.x.y@sha256:<digest> /uv /usr/local/bin/uv
   ```
2. Pick the `uv` version that matches the one used to generate `uv.lock` locally — `uv --version` on a dev machine is the reference. Recording it here prevents silent toolchain drift between dev and CI.
3. Apply to both build stages if `uv` is used in both.
4. Dependabot's `docker` ecosystem keeps the digest current under PR review.

**Exit**: `Dockerfile` contains no unpinned `uv` install; `docker build`
succeeds; `uv --version` inside the built image matches the pinned
version.

### Stream C — GitHub Actions pinning

#### C1 — SHA-pin every `uses:` reference

**File**: `.github/workflows/ci.yml` (and any other workflow files)

1. Replace every `uses: <org>/<action>@<tag>` with
   `uses: <org>/<action>@<sha>  # <tag>` for each action currently referenced
   (`actions/checkout`, `astral-sh/setup-uv`, plus any added by Streams A/B).
2. Resolve the SHA from the tag at the current release: `gh api repos/<org>/<action>/git/refs/tags/<tag>`.
3. Dependabot's `github-actions` ecosystem (A3) will keep these current
   under PR review going forward.

**Exit**: `grep -E 'uses: [^@]+@v[0-9]' .github/workflows/*.yml` returns
zero matches.

### Stream D — Documentation

#### D1 — Add `## Supply chain` to `docs/standards/security.md`

**File**: `docs/standards/security.md`

1. Add a `## Supply chain` section covering: Python dependency policy
   (uv lockfile + `pip-audit` + Dependabot), image pinning (digest, not
   tag), CI action pinning (SHA), CVE scanning layers (pip-audit + trivy),
   SBOM generation (syft → CycloneDX), and the index policy from A1.
2. Point `docs/standards/cicd.md` at the new section for CI-surface
   details rather than duplicating.
3. Cross-reference Plan 053 D2 (which deferred digest pinning to this plan)
   and Plan 064 (this plan) as the implementation record.

**Exit**: `docs/standards/security.md` has a Supply chain section; a
search for "digest" / "SBOM" / "pip-audit" returns results there and only
there.

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
| 1 | **C1** — SHA-pin GitHub Actions | High | Low (~30 min) | Closes the `tj-actions/changed-files` (Mar 2025) class of attack. Mutable-tag exposure is the single largest uncontrolled input today. |
| 2 | **A3** — Dependabot config | High (compounding) | Low (one YAML file) | Unlocks automated upgrade PRs for all three ecosystems (`pip`, `docker`, `github-actions`). Every later task benefits: Dependabot maintains the digests B1/B2/B5 introduce. Do this early so it is ready when digests land. |
| 3 | **B1 + B2** — Digest-pin Dockerfile and compose images | Medium-high | Low (~1 h, including dual-arch rebuild) | Removes silent base-image swaps. Pair them — same pattern, same verification cycle. |
| 4 | **B5** — Pin `uv` toolchain | High (leverage) | Low | `uv` enforces `uv.lock`. An unpinned `uv` makes the locked environment only as trustworthy as wherever `uv` was fetched from. |

### Tier 2 — Medium effect, medium effort

| Rank | Task | Effect | Effort | Why |
|------|------|--------|--------|-----|
| 5 | **A2** — `trivy fs` in lint tier | Medium-high | Medium (CI wiring + `.trivyignore` discipline) | Continuous visibility into Python CVEs. Value is recurring, not one-shot. |
| 6 | **B3** — `trivy image` post-build | Medium | Medium | Catches OS-level CVEs the fs scan misses. Runs after build so slower, but gated before e2e. |
| 7 | **B4** — SBOM generation | Medium (recoverability) | Low-medium | Zero value today; high value the first time a CVE lands and we need to know which historical image is affected. Cheap insurance. |

### Tier 3 — Lower effect or narrower scope

| Rank | Task | Effect | Effort | Why |
|------|------|--------|--------|-----|
| 8 | **A1** — Pin PyPI as sole index | Low-medium | Trivial | Narrow scope per D6 (constrains future `uv add`, not `uv sync`). Do it because it's trivial, not because it's urgent. |
| 9 | **D1** — Standards doc update | Low (doc hygiene) | Low | Capstone — must come last so the doc reflects shipped reality. |

### Deferred / optional (do not schedule)

- **Stream E** — Image signing. Gated on registry/verification decision.
- **Stream F** — Runtime egress allowlist. Gated on Plan 046 staging topology.

## Sequencing notes

- Within each tier, tasks are independent and can run in parallel.
- Tier 1 → Tier 2 → Tier 3 is a hard ordering: Tier 2 scanners assume
  Tier 1 Actions pinning is already in place (otherwise the scanner's
  own action is itself an unpinned liability).
- Stream D (D1 doc task) runs after every other streamed task lands so
  the standards doc reflects what shipped, not what was planned.
- Stream E is gated on an explicit user decision (E1).
- Stream F is gated on Plan 046 completing.

## Open questions for user review

1. ~~**Dependabot vs Renovate** (D1).~~ **Resolved 2026-04-20**: Dependabot. Free, already familiar, sufficient at 38 direct deps.
2. **CVE severity gate** (D4). Fail CI on HIGH+ or CRITICAL-only? HIGH+
   catches more but will occasionally block PRs on transitive deps
   without an available fix — `--ignore-unfixed` in the trivy command
   already skips these, but judgment calls on HIGH-with-fix will appear.
3. **Image signing** (Stream E). Adopt now, or wait until the registry
   story is decided? My recommendation: wait.
4. **Runtime egress allowlist** (Stream F). Fold into this plan or keep
   as a separate follow-up? My recommendation: separate, gated on Plan 046.
5. **Plan 053 D2 reference** — should this plan be linked from the Plan
   053 archive record as the discharge of that deferral? (Convention
   check — not all deferrals are back-linked today.)

## Changelog

- **2026-04-20** — Initial DRAFT.
- **2026-04-20** — D1 resolved to Dependabot. D4 flipped to single-tool
  (`trivy` at both layers; `pip-audit` dropped). D5 cadence clarified
  (every tag, including patch). D6 rationale reframed (narrower — future
  `uv add` protection, not current `uv sync`). D10 added (pin `uv`
  toolchain). D11 added (apt transitivity note). B5 task added. B1/B2
  updated with manifest-list-digest caveat. Priority-order section
  added. Non-goal: secret scanning out of scope.
