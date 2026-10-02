---
status: DRAFT
created: 2026-10-01
title: Release version identity without routine PR lock conflicts
---

# Release version identity without routine PR lock conflicts

Vision: `planning/visions/2026-10-01-faster-github-actions-feedback-within-the-existing-budget.md`

This plan owns only the release-identity slice of the broader faster-CI vision.
It starts from `origin/main` and is not stacked on the CI/local-feedback work.
It must land only after the first CI/local-feedback PR lands, because that PR
keeps the current per-code-commit version policy intact while it changes the CI
and local feedback surface.

## Outcome

Routine code PRs stop conflicting only because they each changed release-version
metadata. A real release identity is assigned deliberately by the owner after a
reviewed source commit exists on `main`. Built artifacts, local Docker images,
operator receipts, and runtime version consumers still expose a clear package
version, source revision, and immutable local image identity.

The current root version bump rule remains in force until this plan's reviewed
transition policy and tooling land. Implementation commits for this plan still
follow the active `AGENTS.md` rule unless and until the merged replacement rule
changes it.

## In scope

- Replace the checked-in root package version as the ordinary development source
  of truth with SCM-derived package identity.
- Keep `sapphire_flow.__version__` and the current runtime consumers working:
  `services/forecast_evidence.py`, `adapters/bafu_observation.py`,
  `adapters/bafu_forecast.py`, and `cli/onboard_nepal.py`.
- Preserve ordinary `uv lock` stability for sibling code PRs before tags exist.
- Preserve safe dependency-edit behaviour when two branches edit dependencies:
  compatible edits merge normally; incompatible constraints still fail at the
  resolver instead of being hidden by the versioning change.
- Replace the automatic `tag-main.yml` release-tag workflow with an
  owner-invoked local release command.
- Add a separate reviewed artifact build/receipt command that has no release
  write authority.
- Add bounded proof for wheel, sdist, source archive, shallow checkout, both
  Docker image variants, and the compose build path.
- Update affected standards, runbooks, workflow-policy tests, dependency-safety
  rules, and the stale `.pre-commit-config.yaml` comment.

## Out of scope

- No source/model/API behaviour change beyond version identity plumbing.
- No unrelated dependency upgrade or dependency-policy bypass.
- No GitHub release, registry publish workflow, release ledger, public schema,
  deployment, tag creation by agents, branch protection/ruleset change, new
  GitHub environment, bot token, or privileged `workflow_dispatch` surface.
- No real publication by an implementation agent. The first real publication is
  a human-owner operation after merge.

## Source facts and design evidence

- `pyproject.toml` currently has `version = "0.1.1044"`,
  `src/sapphire_flow/__init__.py` has the same `__version__`, and
  `[tool.bumpversion]` rewrites both plus runs `uv lock` on every code commit.
- `uv.lock` currently records the root package version, so independent code PRs
  routinely touch the same metadata even when they did not change dependencies.
- `uv_build>=0.12.3,<0.13.0` rejected dynamic version metadata in the native
  versioning probe. `uv-dynamic-versioning` is Hatchling-specific. The isolated
  `setuptools` + `setuptools-scm` probe with repo-standard `uv==0.11.7` removed
  the root editable lock version, kept five sibling lockfiles byte-identical,
  and allowed both merge orders to pass `uv lock --check` / ordinary `uv lock`.
- A bounded local probe under
  `.worktrees/visions/build-backend-experiments` showed `uv sync` has no
  build-constraints CLI and did not enforce `UV_BUILD_CONSTRAINT` in the probe.
  A separate probe showed `[tool.uv] build-constraint-dependencies` is recognized
  by `uv sync`, but this plan chooses the more portable minimal approach: one
  canonical exact-pin source in `[build-system].requires` for the isolated
  backend and its observed transitive build requirements: `setuptools==84.0.0`,
  `setuptools-scm==10.3.4`, `packaging==26.3`, and
  `vcs-versioning==2.5.0`. These are isolated build pins, not runtime dependency
  upgrades. Cold-cache logs observed exactly those four Python 3.12 backend
  inputs for `uv sync` editable builds and `uv build`.
- Package data is runtime data, not decoration. Current consumers include
  `sapphire_flow/py.typed`, `sapphire_flow/data/icon_ch2_eps_grid.npz`, API
  templates under `sapphire_flow/api/templates/`, and Aquacast config YAML files
  under `sapphire_flow/models/aquacast/configs/`.
- Docker has two application images: default `sapphire-flow:${VERSION}` and
  Aquacast `sapphire-flow-aquacast:${VERSION}` built with `WITH_AQUACAST=1`.
  The Aquacast worker is the operational forecast-cycle image. Current CI only
  builds the default image path.
- The current Dockerfile installs the editable root package in a dependency
  layer after copying only `pyproject.toml`, `uv.lock`, `README.md`, and a stub
  `src/sapphire_flow/__init__.py`; it copies real `src/` later. That layering
  cannot be accepted unchanged with generated version metadata.
- The Docker build context excludes `.git`. Release version and full source SHA
  must therefore be verified outside the image build and passed into the build;
  the build itself cannot infer provenance from Git history.
- Current `tag-main.yml` runs on every push to `main` with `contents: write` and
  creates best-effort annotated tags from `pyproject.toml`. It is intentionally
  gappy and race-tolerant today. This plan retires it only as part of the
  coherent migration, not as a separate cleanup.
- Release-publication research proved that a release-state branch pointing
  directly at the source commit is unsafe: same-source / different-version
  contenders can both publish because the second branch update becomes a no-op
  and bypasses the stale lease. The smallest proven Git-only correction is a
  deterministic pair-specific state-token commit.
- `.claude/` exists in the repository checkout. Do not invoke its settings,
  hooks, or agent definitions.

## Design decisions

### D1 — Version identity source and build-backend provenance

Use `setuptools` + `setuptools-scm` for the SAPPHIRE Flow root package. The
ordinary checked-in project no longer carries a changing root version in
`uv.lock`, and ordinary code PRs no longer need a version bump just to be valid.

The implementation must:

- configure dynamic package versioning in `pyproject.toml`;
- pin all isolated root build-backend requirements exactly in
  `[build-system].requires`: `setuptools==84.0.0`,
  `setuptools-scm==10.3.4`, `packaging==26.3`, and
  `vcs-versioning==2.5.0` unless implementation proves a safer exact set;
- treat those pins as isolated build inputs only, not runtime dependency
  upgrades or a reason to re-resolve unrelated project dependencies;
- do not add a second committed backend-version list that can drift; use
  `[build-system].requires` as the one canonical exact-pin source for both
  `uv sync` and `uv build`;
- add cold-cache proof that `uv sync --no-cache -v` and `uv build --no-cache -v`
  use exactly those backend versions from `[build-system].requires`;
- add a dependency-safety REVIEW classifier for `[build-system]` changes and for
  any future `[tool.uv].build-constraint-dependencies` use, plus tests for
  base/head classifier behaviour;
- update `docs/standards/security.md` as the canonical supply-chain policy for
  this new executable build-backend surface;
- keep the public import `from sapphire_flow import __version__` working;
- configure `setuptools-scm` to share the publisher's strict canonical grammar,
  without a custom backend, release framework, or network access inside the build
  backend: release tags are exactly
  `^v(?P<version>(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*))$`;
  discovery uses a restrictive `git_describe_command` such as
  `git describe --dirty --tags --long --match v[0-9]*.[0-9]*.[0-9]* --exclude *-*`,
  and the anchored `tag_regex` is the final authority. Noncanonical version-like
  tags such as the existing `v0.1.281-review`, prefixed/suffixed forms, and
  leading-zero forms must not silently become release versions. If Git describe
  selects an ambiguous version-like tag that the strict regex rejects, the build
  fails closed rather than guessing;
- generate private package version metadata during builds, not track it in Git;
- require normal `uv` editable installs to generate that metadata; a missing
  generated module must fail clearly rather than fabricate a release-looking
  fallback;
- require an explicit verified full-SHA development override in shallow CI paths
  that do not have tag history;
- include that override value in every relevant `uv` cache key so changing or
  clearing it rebuilds the editable root package;
- avoid unrelated dependency upgrades while changing the build backend.

### D2 — Auditable version-policy transition

The current version bump policy remains active through the first CI/local-
feedback slice. This release-identity slice performs the final legacy patch bump
before deleting configured `bump-my-version` version fields and replacing the
policy docs. The final legacy bump, backend/config migration, lock regeneration,
removal of root-version bump instructions, retirement of `tag-main.yml`, and
updated docs/tests must land coherently in the same implementation commit (or in
one atomic PR state with no intermediate commit presented as complete).

After the dynamic-version switch, the old `bump-my-version bump patch` command
must not be required or documented for ordinary code commits because its
configured targets are gone. If the owner declines the final legacy bump, record
that as an explicit one-time owner exception; do not pretend the command works
post-switch.

### D3 — Release publication surface and destination boundary

Release publication is a local command deliberately invoked by the human owner
from a clean checkout of reviewed `origin/main`, using the owner's existing Git
credentials for the final push. There is no new privileged GitHub Actions
dispatch, bot token, release environment, or protection-rule change in this
plan.

Owner-only is an operational authority boundary under existing Git ACLs. It is
not a new enforceable ACL. The local owner Git configuration and credential
helper are part of the trusted computing base. If another actor with sufficient
Git write permission pushes or deletes raw refs, this cooperative helper cannot
prevent that. It detects and fails closed only for mismatches covered by the
frozen-ceiling binding, current token, and post-ceiling helper-era invariants. A
new canonical tag inserted at or below the frozen ceiling cannot be attributed
without a frozen legacy inventory, signatures, a ledger, or new ACL/ruleset
controls; new release requests still require a version greater than every live
canonical tag.

The helper must authenticate its destination and remote state:

- validation reads prefer the canonical public HTTPS URL for
  `hydrosolutions/SAPPHIRE_flow` and use anonymous/no-credential reads where
  possible;
- all fetched remote state is remote-authoritative: fetch into isolated
  temporary refs or reconcile every ref/OID against `git ls-remote`; do not use
  stale local tags to compute ceilings, max versions, or ancestry;
- reject unexpected `origin.url`, `remote.pushDefault`, `pushurl`, or
  `url.*.insteadOf`/rewrite configuration that would read from or push to a
  different repository, unless the owner explicitly confirms a displayed exact
  canonical write URL in the final prompt;
- display the final write destination in a redacted, credential-free form before
  owner confirmation;
- do not print, copy, cache, or pass credential-bearing URLs or tokens as command
  arguments;
- if the owner chooses SSH for the final push, do not claim that earlier SSH
  reads lacked write-capable credentials; either use public HTTPS reads or state
  honestly that owner SSH configuration is trusted for that invocation.

### D4 — Trusted helper and no target-source execution in publisher

The mutating release command executes helper code from clean reviewed
`origin/main`. The requested version and requested full source SHA are data only.

The mutating command must not:

- check out the requested target source as executable tooling;
- import or run code from the requested target source;
- run package builds, Docker builds, hooks, or scripts from the requested target
  source;
- place secrets in the target worktree or in generated receipts.

Build and runtime checks are separate read-only proof steps. They can execute the
reviewed target source without release-write authority. They do not hold or use
release-push credentials.

### D5 — Git-only release state token and helper-era tag metadata

Use one implementation-detail current-state branch, `refs/heads/release-state`,
whose tip is a deterministic state-token commit for `(canonical version, full
source SHA, frozen legacy ceiling)`. This branch is not access-private; anyone
with repository read access can see it. It is private only in the sense that it
is not a public API, release ledger, or schema for external consumers.

For `(vX.Y.Z, SOURCE, CEILING)` the token commit has:

- tree: `SOURCE^{tree}`;
- sole parent: `SOURCE`;
- message: fixed encoding of the helper marker, canonical tag name, full
  lowercase source SHA, frozen legacy ceiling tag, and frozen legacy ceiling
  source SHA;
- author and committer name, email, timestamp, and message encoding: fixed
  constants owned by the helper.

Token construction must use fixed environment/config and Git plumbing, disable
signing, and ignore hostile ambient user Git config. The same tuple produces the
same token object. Changing any tuple member changes the object.

Each future helper-era canonical tag above the frozen ceiling is an immutable
annotated tag object that directly targets the expected commit, not another tag
object. Its message contains a fixed helper marker plus the same version, source
SHA, frozen legacy ceiling tag, and frozen legacy ceiling source SHA. This uses
the existing canonical tag as independent baseline evidence; it adds no new
remote ref, ledger, or public schema.

Publication is a single atomic push of:

- an immutable annotated canonical tag `refs/tags/vX.Y.Z`; and
- the exact leased update of `refs/heads/release-state` to the token commit.

The state branch update is a non-fast-forward update by design. If GitHub or the
current repository rules reject that exact atomic non-fast-forward update, the
first real owner publication is blocked. The helper must not fall back to a tag-
only push, force/move a tag, update `main`, or split the operation into two
pushes.

### D6 — Bootstrap, current-state validation, and reconciliation invariants

A canonical release tag is exactly `vX.Y.Z`, where each component is an unsigned
canonical decimal integer. Compare parsed integer tuples, not strings. For
example `v0.1.1000 > v0.1.999`.

Before migration/bootstrap, the old automatic `tag-main.yml` publisher must be
quiescent: the workflow has been retired on `main`, no old run remains in flight,
and the owner has freshly fetched/reconciled remote tags after that point.

Default publish with `refs/heads/release-state` absent fails closed. A deliberate
`--bootstrap` mode is the only path that may create the first state token, and
only if all of these are true:

1. remote `main`, all canonical tags and peeled targets, and the absent state ref
   have been fetched/reconciled from the canonical remote namespace;
2. no helper-era annotated tag metadata exists anywhere in canonical tags;
3. the live legacy ceiling is the greatest canonical version at that instant;
4. the ceiling tag peels to a source on fetched first-parent `origin/main`;
5. the requested source is a strict first-parent descendant of that ceiling
   source;
6. requested version is greater than every fetched canonical version; and
7. the exact empty lease wins the atomic tag + state push.

With state present, one invariant function validates the whole release state:

- parse and recompute the token exactly;
- require unchanged frozen legacy ceiling in the token;
- require the encoded frozen legacy ceiling tag exists in the remote-
  authoritative namespace, has the encoded canonical version, and peels to the
  encoded ceiling source; deletion or wrong target rejects and is never repaired
  by the publisher;
- require the token-encoded canonical tag exists, is annotated, directly targets
  the token parent/source, carries the helper marker and matching metadata, and
  peels to the token parent/source;
- require the token-encoded version equals the greatest canonical version;
- require the token source is on first-parent `origin/main`;
- validate every canonical tag above the frozen ceiling as helper-era state:
  annotated tag object directly targeting the expected commit, supported helper
  marker/metadata form, same frozen ceiling, unique target source, numeric
  version ordering, and source ancestry forming a strict first-parent-descendant
  chain from the ceiling source to current state;
- require no future canonical tag above the ceiling points to a duplicate target;
- require a requested tag that already exists to peel to the requested source and
  pass the same global invariant check before idempotent success is reported;
- require a requested tag that exists on another target to fail.

For a new publication with valid state present, the request itself must also pass
checks that are not implied by validating existing tags: requested version is
greater than the current numeric max; requested source is on first-parent
`origin/main`; requested source is a strict first-parent descendant of the
current released source recorded by the state token; and no existing canonical
tag already peels to the requested target source.

After success, network error, transport error, or lost response, the helper must
refetch remote `main`, tags/peeled targets, and release-state, then apply the
same invariant function before reporting success, idempotence, or failure.

If state is absent but helper-era tag metadata exists, publish fails closed. The
publisher must not recreate state, auto-bootstrap, auto-repair, or reclassify
history. The operator stops and escalates.

Damage repair is not an automated publisher capability and not a new recovery
framework in this plan. If the owner later explicitly authorizes restoration,
the repair must use a separately verified expected latest successful release
receipt or object IDs for the exact `(version, source, frozen ceiling)` tuple,
not the greatest surviving tag. The expected tag must exist, be annotated in the
supported helper-era form, directly target the expected source, and pass the full
remote refetch and invariant check including the ceiling-tag binding. If the
expected tag is missing, repair stops; there is no fallback to an older surviving
tag. Any restoration writes only the expected deterministic token with an exact
empty lease, after full destination confirmation, then refetches and reruns the
same invariants. This is documented external owner repair, not a normal helper
command.

If a privileged actor deletes `refs/heads/release-state` and the latest helper-
era tag suffix, remaining tags cannot prove that the greatest surviving tag was
the latest successful release. If all helper-era annotated tag metadata is gone,
the helper has no cooperative evidence that distinguishes that state from a
never-bootstrapped repository. The honest limitation is: default publish still
rejects absent state; explicit `--bootstrap` is an owner-attested first-
migration operation; and known loss requires external owner evidence. The helper
must not claim it can detect state-plus-latest-tag-suffix deletion by itself.

### D7 — Artifact identity and no-Git matrix

Expected behaviour is explicit:

| Source shape | Expected identity behaviour |
|---|---|
| Clean verified canonical tag with full history | package version is exactly `X.Y.Z` |
| Built wheel from verified release source, installed without `.git` | imports as exactly `X.Y.Z`; package resources are present |
| Built sdist from verified release source, rebuilt/installed without `.git` | imports as exactly `X.Y.Z`; package resources are present |
| Ordinary CI shallow checkout | succeeds only with exact development override `0.dev0+g<FULL_ACTUAL_CHECKOUT_SHA>` |
| Raw GitHub source archive or no-Git checkout without generated metadata or approved override | fails clearly; never silently reports `X.Y.Z` or fabricates a release-looking fallback |

The only approved shallow development override is package-specific:
`SETUPTOOLS_SCM_PRETEND_VERSION_FOR_SAPPHIRE_FLOW=0.dev0+g<FULL_ACTUAL_CHECKOUT_SHA>`.
CI must verify `HEAD == github.sha` before setting it. On PR events this is the
actual merge SHA checked out by GitHub Actions, not the PR head SHA. Ordinary
unreleased versions remain clearly development versions carrying the full
checkout revision. Generic `uv build` metadata is not an attestation of owner
publication. Canonical release versions are assigned only by the separate
verified release path.

### D8 — Reviewed artifact build/receipt command and Docker identity

Add a reviewed artifact build/receipt command that is distinct from the mutating
release publisher. It has no write authority to release refs and receives no
release-push credentials. It builds from reviewed target source for artifact
proof only.

The command must:

1. use the same canonical-destination and remote-authoritative read boundary as
   the release helper, but without any release-write credential;
2. fetch the exact annotated tag object and peeled target from the canonical
   repository read-only namespace into isolated refs, or reconcile both OIDs
   against canonical `git ls-remote`;
3. require the remote tag object to be the locally inspected supported helper-era
   annotated tag, directly targeting the supplied full source SHA, with matching
   helper metadata, frozen ceiling, and global release-state invariants;
4. reject missing remote tag, local-only tag, stale/wrong local tag object,
   remote tag-object mismatch, target mismatch, and version/source mismatch;
5. only after proven remote publication, execute reviewed target source for
   artifact proof without release-write authority;
6. verify `HEAD` equals that full source SHA;
7. verify the index and worktree are clean, including relevant untracked files
   that would enter the build context;
8. verify the package version input is the canonical version for that remote tag;
9. reject wrong `HEAD`, dirty tracked source, relevant untracked build-context
   files, tag/source mismatch, and version/source mismatch;
10. construct an immutable clean Git-tree build context from the verified source
    tree, including the correct `src/`, resources, `.dockerignore`, `uv.lock`,
    `pyproject.toml`, Dockerfile, docs/runbook files needed for receipts, and no
    locally generated version metadata from the host;
11. build both Docker variants from that same captured context;
12. record remote tag object OID, peeled source OID, commit, tree, version,
    source SHA, labels, mutable local tag, immutable local image ID, and actual
    container import result for each variant.

Dockerfile implementation must fix the current stub-layer hazard. Two acceptable
minimal designs are:

- **preferred:** dependency-only cached layer uses `uv sync --frozen --no-dev
  --no-install-project` (and `--extra aquacast` for the Aquacast dependency set),
  then `COPY` the real verified source context and install the root package with
  the exact version override; prove native `uv 0.11.7` supports the chosen
  command shape; or
- copy the full verified source before root install and document the cache cost.

In both designs, generated version metadata must be produced inside the verified
context build, not copied from the host. Add the generated metadata path to
`.dockerignore` so local developer metadata cannot leak into images.

Both application image variants must receive the same verified package version
and full source SHA inputs:

- default `sapphire-flow:${VERSION}` / `WITH_AQUACAST=0`;
- Aquacast `sapphire-flow-aquacast:${VERSION}` / `WITH_AQUACAST=1`.

Each variant must expose:

- OCI labels for package version and full source revision;
- runtime `sapphire_flow.__version__` matching the expected package version;
- a recorded immutable local image ID after build.

Do not call a local image ID a registry digest. If a future registry-publish
workflow is added, registry manifest digests become a separate receipt. Today
these images are local-only and mutable tags can be overwritten, so the operator
receipt must record the immutable local image ID outside the mutable tag.

The current operator compose path must be covered too. Update the `x-app-build`
anchor and the `prefect-worker` build block so compose builds pass the version
and source revision build args from required environment variables, alongside the
existing BuildKit secret mounts. Update `docs/standards/cicd.md` so
`docker compose build prefect-worker` and `docker compose run --rm --build init`
are run from the verified context/receipt command or from an explicitly verified
checkout, not an accidental live current working directory.

## Test path status

Existing tests cited for update or preservation were verified in this checkout:

- existing `tests/unit/tooling/test_workflow_policy_coherence.py`
- existing `tests/unit/tools/test_dependency_safety.py`
- existing `tests/unit/tooling/test_tag_main_workflow.py` (to be deleted or
  replaced with release-helper workflow-policy tests)
- existing `tests/unit/deploy/test_compose_aquacast_image.py`

All other test paths named below are proposed new paths.

## Implementation tasks

### T1 — Dynamic root package version and backend supply-chain policy

**In**

- Change `pyproject.toml` from `uv_build` to `setuptools` + `setuptools-scm`.
- Add exact `[build-system].requires` pins for the isolated backend set from D1;
  these are not runtime dependency upgrades.
- Keep `[build-system].requires` as the one canonical exact-pin source for root
  build backend inputs; do not add a duplicate build-constraints version list.
- Configure package-data inclusion for current runtime resources.
- Replace the literal `src/sapphire_flow/__init__.py` version with an import of
  generated version metadata at the exact path `src/sapphire_flow/_version.py`.
  Normal `uv` editable installs must generate that metadata; missing generated
  metadata fails clearly.
- Add `src/sapphire_flow/_version.py` to both `.gitignore` and `.dockerignore`.
  Prove a normal editable install can create it while `git status --porcelain`
  stays clean, and prove the verified Docker context excludes host-generated
  metadata and regenerates it inside the build.
- Remove the now-unused `bump-my-version>=1.2.7` dev dependency in the coherent
  migration and let `uv lock` remove only its unused dependency graph. If any
  maintained non-archived command still needs it, name that maintained use and
  retain it intentionally.
- Update `tools/dependency_safety.py` and tests so `[build-system]` changes and
  any future `[tool.uv].build-constraint-dependencies` use classify at least
  REVIEW.
- Update `docs/standards/security.md` for root build-backend provenance.
- Preserve active bump policy until D2 transition lands.

**Out**

- No changes to model logic, stores, APIs, migrations, or dependency versions
  except the build backend/versioning requirements needed for this task.

**RED / proof strategy**

Add failing tests before the implementation change where practical:

- proposed new `tests/unit/versioning/test_runtime_version.py`
  - `sapphire_flow.__version__` is a non-empty string;
  - the four current runtime consumers continue to read the public import;
  - missing generated metadata does not masquerade as a release.
- proposed new `tests/unit/packaging/test_package_resources.py`
  - asserts the resource inventory expected in wheel/sdist checks.
- proposed new `tests/integration/packaging/test_scm_tag_policy.py`
  - local fixture proves exact canonical `vX.Y.Z` tag builds as `X.Y.Z`;
  - existing noncanonical shape `v0.1.281-review`, prefixed/suffixed forms, and
    leading-zero forms do not silently become release versions;
  - ambiguous describe selection fails closed;
  - multiple canonical tags on one target are rejected by helper/artifact
    validation rather than left to SCM tie-breaking;
  - an untagged descendant yields the planned clear dev/full-revision identity.
- existing `tests/unit/tools/test_dependency_safety.py`
  - `[build-system]` backend changes classify REVIEW;
  - `[tool.uv].build-constraint-dependencies` changes classify REVIEW if that
    supported setting appears later;
  - self-review classifier base/head rules are preserved.
- existing `tests/unit/tooling/test_workflow_policy_coherence.py`
  - asserts the new transition policy text and absence of the old automatic
    `tag-main` rule after the release helper lands.

**Focused commands**

```bash
uv run pytest tests/unit/versioning/test_runtime_version.py tests/unit/packaging/test_package_resources.py tests/unit/tools/test_dependency_safety.py tests/unit/tooling/test_workflow_policy_coherence.py
uv run python tools/dependency_safety.py --base-ref <pre-change-base-sha>
uv lock --check
uv build --no-cache -v
uv sync --no-cache -v
```

### T2 — Lock stability, merge-order, and dependency-edit proofs

**In**

- Add local fixture tests or documented test helpers that exercise the real
  SAPPHIRE package shape, not only the prior generic probe.
- Prove code-only sibling branches no longer produce root-version lock churn.
- Prove both merge orders for ordinary sibling code PRs.
- Prove compatible dependency edits update only the intended dependency graph.
- Prove incompatible constraints still fail resolver checks.
- Add a dependency-safety regression for disappearance of the root package's
  `version` field in `uv.lock`.

**Out**

- No unrelated dependency resolution or application dependency upgrade.

**RED / proof strategy**

Proposed update plus new tests:

- existing `tests/unit/tools/test_dependency_safety.py`
  - old lock has root `sapphire-flow` version and new lock omits it;
  - omission is not classified as a dependency bump or crash.
- proposed new `tests/unit/versioning/test_lock_stability.py`
  - fixture or temp-repo proof for same-source sibling edits in both merge
    orders;
  - each controlled `A→B` and `B→A` integration result flows through local
    publication simulation or equivalent identity validation and produces a
    unique traceable version/source mapping without remote test tags;
  - compatible dependency edit proof;
  - incompatible constraints proof.

**Focused commands**

```bash
uv run pytest tests/unit/tools/test_dependency_safety.py tests/unit/versioning/test_lock_stability.py
uv lock --check
```

### T3 — Owner-invoked release identity helper

**In**

- Add a local command under `tools/` with a stable domain name such as
  `tools/release_identity.py`.
- Inputs: canonical version and full target source SHA only.
- Validate clean helper checkout at reviewed `origin/main` before any write.
- Enforce the canonical destination boundary from D3.
- Fetch and validate `origin/main`, tags, peeled targets, and release-state from
  remote-authoritative refs.
- Implement canonical numeric version parsing and ordering.
- Implement deterministic token-commit construction and validation, including
  the frozen legacy ceiling in the token encoding and ambient Git config
  hardening.
- Implement helper-era annotated tag metadata.
- Implement complete D6 invariants for bootstrap, present state, missing state,
  documented external owner damage-repair limits, idempotence, stale lease,
  duplicate target, duplicate version, out-of-order request, same-pair race,
  different-pair race, lost-response reconciliation, and observable mismatch
  fail-closed behaviour.
- Retire `.github/workflows/tag-main.yml` only in this coherent migration, with
  tests and docs updated at the same time.

**Out**

- No real remote publication by implementation agents.
- No GitHub Actions dispatch replacement.
- No tag force/move, main update, two-push fallback, ledger, or public state
  schema.

**RED / proof strategy**

Proposed new tests:

- `tests/unit/tools/test_release_identity.py`
  - canonical parser treats `v0.1.1000` greater than `v0.1.999`;
  - noncanonical text and leading-zero components fail;
  - token commit object is deterministic for the same `(version, source,
    legacy ceiling)` tuple and distinct when any tuple member differs;
  - hostile local Git config, signing config, author/committer config, and
    message settings do not change the token object ID;
  - helper-era annotated tag metadata round-trips marker, version, source, and
    frozen legacy ceiling;
  - destination validation rejects unexpected origin/push URL/rewrite config;
  - source must be on first-parent `origin/main`;
  - future one-canonical-tag-per-target rule rejects a second future tag;
  - legacy duplicate targets can be grandfathered only at or below the frozen
    migration ceiling;
  - a successor token that changes or omits the frozen ceiling fails validation.
- proposed new `tests/integration/tools/test_release_identity_git.py`
  - default publish with state absent fails;
  - deliberate `--bootstrap` requires a successful, complete, paginated,
    read-only GitHub workflow-run query showing no old `tag-main.yml` run in
    any nonterminal state, including queued, waiting, pending, requested, or
    in-progress; requested source must descend from the max legacy tag source;
  - bootstrap refuses query errors, incomplete results, unknown run states, or
    any nonterminal old run. Elapsed time is never substitute evidence: a job
    timeout does not bound queue delay or delayed run creation;
  - bootstrap also verifies retirement on fetched `main` and freshly reconciles
    remote refs after the run query; it does not authorize rerunning the retired
    publisher or claim protection against actors bypassing the helper;
  - bootstrap is rejected if any helper-era tag metadata already exists;
  - present-state validation checks token/tag/max-version/ancestry/all-future-
    tag invariants;
  - encoded ceiling tag deletion or wrong target rejects in bootstrap, present-
    state validation, and post-error reconciliation; publisher never recreates
    or moves that tag;
  - deletion of `refs/heads/release-state` after helper-era evidence fails
    closed and does not recompute a higher ceiling;
  - deletion of state plus latest helper-tag suffix cannot fall back to the
    greatest surviving tag;
  - missing state with helper-era evidence stops and escalates; publisher never
    recreates state in that path;
  - documented owner damage repair requires separately verified expected latest
    release receipt/object IDs, exact destination confirmation, exact empty
    lease, and full invariant refetch; missing expected tag stops with no older
    fallback;
  - same-pair concurrent publication reconciles to success for the loser;
  - same-source/different-version race rejects atomically;
  - stale lease fails without tag creation;
  - simulated lost response refetches and reconciles;
  - direct source-pointer CAS is not used.
- Replace `tests/unit/tooling/test_tag_main_workflow.py` with explicit
  `tests/unit/tooling/test_release_identity_workflow_policy.py` tests that prove
  no automatic tag workflow remains and no privileged `workflow_dispatch` was
  added.

**Focused commands**

```bash
uv run pytest tests/unit/tools/test_release_identity.py tests/integration/tools/test_release_identity_git.py tests/unit/tooling/test_workflow_policy_coherence.py
uv run pytest tests/unit/tooling/test_release_identity_workflow_policy.py
```

### T4 — Wheel, sdist, shallow checkout, and raw archive proof

**In**

- Build wheel and sdist from the migrated SAPPHIRE package.
- Inspect both artifacts for package data parity:
  - `sapphire_flow/py.typed`;
  - `sapphire_flow/data/icon_ch2_eps_grid.npz`;
  - all current API templates;
  - `sapphire_flow/models/aquacast/configs/*.yaml`.
- Install from the built wheel in a clean environment and run runtime resource
  checks.
- Rebuild/install from the sdist without `.git` and run the same resource
  checks.
- Exercise the explicit no-Git matrix from D7.

**Out**

- No publication to PyPI, GitHub Releases, or an image registry.

**RED / proof strategy**

Proposed new tests:

- `tests/integration/packaging/test_artifact_identity.py`
  - wheel metadata and import version match release input;
  - sdist rebuild metadata and import version match release input;
  - wheel and sdist contain all package resources;
  - clean wheel install can load API templates, load the MeteoSwiss grid asset,
    and read Aquacast config YAML/hash data;
  - raw archive / no-Git / no-generated-metadata path fails clearly and never
    fabricates a release-looking fallback.

**Focused commands**

```bash
uv build
uv run pytest tests/integration/packaging/test_artifact_identity.py
```

### T5 — CI and cache-key integration

**In**

- Update CI jobs that install the editable root package to provide exactly
  `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_SAPPHIRE_FLOW=0.dev0+g${FULL_ACTUAL_CHECKOUT_SHA}`
  when they do not have sufficient tag history.
- Verify `git rev-parse HEAD` equals `github.sha` before setting that override.
  On PR events this is the merge SHA checked out by GitHub Actions.
- Include that override value in all relevant `uv` cache keys, including PR,
  push, scheduled, live, and Docker development-input paths. Changing or
  clearing it must invalidate the editable-root cache.
- Preserve every current CI gate: lint, pyright ratchet, unit shards, coverage,
  integration, wheel-only guard, Trivy fs/image scan, SBOM enforcement,
  dependency-safety, scheduled/live workflows, and current private-dependency
  credential scoping.
- Keep private clone credentials out of logs and caches.

**Out**

- No reduction of test coverage or required checks.
- No new privileged CI path.

**RED / proof strategy**

Update or add:

- proposed new `tests/unit/tooling/test_ci_version_override.py`
  - CI uses full `github.sha` in the exact development override where needed;
  - CI verifies `HEAD == github.sha` before setting the override;
  - cache keys include the override;
  - private tokens are not part of cache keys or logged command text.
- Existing workflow tests continue to assert action SHA pins, credential
  scoping, and gate policy.

**Focused commands**

```bash
uv run pytest tests/unit/tooling/test_ci_version_override.py tests/unit/tooling
uv run check
```

### T6 — Artifact build/receipt and Docker identity

**In**

- Add the reviewed artifact build/receipt command from D8.
- Add Docker build inputs for verified package version and full source revision.
- Add OCI labels for both values.
- Ensure runtime `sapphire_flow.__version__` in the image matches the package
  version input.
- Fix Dockerfile root-install ordering so generated version metadata is produced
  from the verified source context and cannot leak from the host.
- Update `docker-compose.yml` `x-app-build` and `prefect-worker` build blocks to
  pass required version/source build args while preserving existing BuildKit
  secret mounts and `WITH_AQUACAST=1` only for the Aquacast worker.
- Build/prove both variants from the same captured context:
  - default `WITH_AQUACAST=0` image;
  - Aquacast `WITH_AQUACAST=1` image with existing BuildKit secret handling.
- For each variant, inspect OCI labels, run a container command that imports
  `sapphire_flow.__version__`, capture `docker image inspect --format '{{.Id}}'`,
  and compare that ID to the operator receipt.
- Add a `--receipt PATH` option that atomically creates a credential-free JSON
  receipt, rejects overwriting an existing path, records package version, full
  source SHA, Git tree, OCI labels, mutable local tag, immutable local image ID,
  and actual container import result for each variant, then reads the image ID
  back from Docker to verify the receipt. This is a bounded release-identity
  receipt, not a generic receipt system.
- Update operator documentation, including
  `docs/operations/mac-mini-deploy-runbook.md`, for the new receipt fields and
  verified-context build path.

**Out**

- No registry publish or registry digest claim.
- No copying of `RECAP_DG_CLIENT_TOKEN`, `AQUACAST_TOKEN`, owner Git
  credentials, or credential-bearing URLs into image layers, logs, caches,
  command arguments, or receipts.
- No deployment.

**RED / proof strategy**

Proposed new or existing tests:

- proposed new `tests/unit/deploy/test_docker_release_identity.py`
  - Dockerfile declares and applies version/source build args and OCI labels;
  - Dockerfile uses a dependency-only cached layer before root install, or the
    test documents the deliberate full-source-before-install cache cost;
  - generated metadata path is `.dockerignore`d;
  - compose default and Aquacast build blocks carry required version/source args;
  - compose uses BuildKit secrets, not credential build args;
  - both compose image variants are represented in receipt docs/tests;
  - `--receipt PATH` writes credential-free JSON atomically, rejects overwrite,
    and verifies image IDs by reading them back.
- existing `tests/unit/deploy/test_compose_aquacast_image.py` still proves only
  the Aquacast worker gets `WITH_AQUACAST=1`.
- proposed new artifact command tests use local fixture remotes plus test-only
  dependency injection, never real remote tag creation, and reject local-only tag,
  remote tag object mismatch, remote target mismatch, wrong HEAD, dirty tracked
  source, relevant untracked build-context files, tag/source mismatch, and
  version/source mismatch.
- receipt tests prove two writers cannot both claim the same path, existing
  receipt is never replaced, failed/interrupted build leaves no valid partial
  receipt, temporary files contain no credentials and are cleaned or invalid,
  Docker image ID readback happens before final atomic JSON creation, and schema
  validation rejects missing or unknown identity-critical fields.

Manual/owner-authorized proof for the Aquacast image may be required if CI
cannot build it without the private Aquacast token. The plan must record that as
an acceptance receipt, not silently skip the variant.

**Focused commands**

```bash
uv run pytest tests/unit/deploy/test_docker_release_identity.py tests/unit/deploy/test_compose_aquacast_image.py
# Raw build proof from a verified captured context, with secrets as secrets, never args:
docker build --secret id=recap_dg_client_token,env=RECAP_DG_CLIENT_TOKEN --build-arg SAPPHIRE_RELEASE_VERSION=<version> --build-arg SAPPHIRE_SOURCE_REVISION=<full-sha> -t sapphire-flow:<version> <verified-context>
docker build --secret id=recap_dg_client_token,env=RECAP_DG_CLIENT_TOKEN --secret id=aquacast_token,env=AQUACAST_TOKEN --build-arg WITH_AQUACAST=1 --build-arg SAPPHIRE_RELEASE_VERSION=<version> --build-arg SAPPHIRE_SOURCE_REVISION=<full-sha> -t sapphire-flow-aquacast:<version> <verified-context>
docker image inspect --format '{{.Id}} {{json .Config.Labels}}' sapphire-flow:<version>
docker image inspect --format '{{.Id}} {{json .Config.Labels}}' sapphire-flow-aquacast:<version>
docker run --rm sapphire-flow:<version> python -c "import sapphire_flow; print(sapphire_flow.__version__)"
docker run --rm sapphire-flow-aquacast:<version> python -c "import sapphire_flow; print(sapphire_flow.__version__)"
# Compose proof uses required env vars and the same verified context/checkout:
docker compose build prefect-worker
docker compose run --rm --build init python -c "import sapphire_flow; print(sapphire_flow.__version__)"
```

### T7 — Documentation and transition cleanup

**In**

- Update `AGENTS.md` version-bumping instructions to the new reviewed policy.
- Update `docs/standards/cicd.md` and
  `docs/operations/mac-mini-deploy-runbook.md` image tagging/versioning, compose
  build args, verified-context build procedure, receipts, and upgrade steps.
- Update `docs/standards/security.md` for build-backend provenance,
  dependency-safety classifier coverage, and credential boundaries.
- Update `docs/touchpoint-maps.md` only if routing facts for Docker/versioning
  changed.
- Update `.pre-commit-config.yaml` comments that still cite the mandatory
  bump-my-version commit sequence, without changing the check-only hook policy.
- Remove or replace `tag-main.yml` documentation and tests in the same change.
- Document bootstrap/migration outcomes and failure modes for the first owner
  publication.

**Out**

- Do not copy review round history into docs.
- Do not claim new GitHub ACL enforcement.

**Focused commands**

```bash
uv run pytest tests/unit/tooling/test_workflow_policy_coherence.py tests/unit/docs
uv run check
```

## Acceptance criteria

- Ordinary sibling code PRs do not change `uv.lock` solely because of root
  version metadata, and both merge orders pass `uv lock --check`.
- A compatible sibling dependency edit remains reviewable as a dependency edit;
  an incompatible constraints edit fails the resolver.
- `tools/dependency_safety.py` does not crash or flag a false dependency bump
  when the root package `version` field disappears from `uv.lock`.
- `[build-system]` changes and any future `[tool.uv].build-constraint-dependencies`
  use classify REVIEW, and the canonical security standard covers root build-
  backend provenance.
- Cold-cache `uv sync` and `uv build` proof observes the reviewed exact isolated
  backend versions, proves they are the complete backend inputs on Python 3.12
  host/CI and Python 3.14 Docker builder paths, and shows no unconstrained latest
  backend execution.
- Wheel and sdist include all current package data and install cleanly without
  `.git`.
- Runtime resource checks pass from a clean artifact install.
- Verified release tag builds report exactly `X.Y.Z`.
- SCM tag discovery and parsing share the publisher's strict canonical `vX.Y.Z`
  grammar; noncanonical version-like tags such as `v0.1.281-review`, leading-
  zero forms, prefixed/suffixed forms, ambiguous multiple-tag cases, and
  untagged descendants have the documented release/dev/fail-closed behaviour.
- Shallow CI succeeds only with exact override
  `0.dev0+g<FULL_ACTUAL_CHECKOUT_SHA>` after `HEAD == github.sha` validation.
- Raw no-Git archives without generated metadata or approved override fail
  clearly and do not silently report a release version.
- The owner-invoked publisher authenticates/reconciles the canonical destination
  and publishes only via atomic annotated-tag + leased state-token branch update,
  after concrete old auto-publisher quiescence evidence is recorded.
- Default publish with state absent fails; deliberate bootstrap requires no
  helper-era evidence and a requested source strictly descending from the max
  legacy tag source.
- Present-state reconciliation validates token, tag metadata, numeric max,
  ancestry, all helper-era tag forms, and a consistent frozen legacy ceiling
  whose canonical tag still exists remotely and peels to the encoded ceiling
  source.
- Missing state with helper-era evidence fails closed and does not re-bootstrap,
  recreate state, or recompute a higher ceiling; documented owner damage repair
  requires separately verified expected latest release object IDs and stops if
  the expected tag is missing.
- Same-pair publication is idempotent only after full refetch validation,
  including the unchanged frozen legacy ceiling.
- Same-source/different-version and different-source/different-version races
  reject one contender atomically.
- Out-of-order versions, changed/omitted legacy ceilings, lightweight future
  tags, and future duplicate target tags fail closed.
- Docker/images and release receipts are built only after read-only validation of
  the canonical remote annotated tag object OID, direct commit target, helper
  metadata, ceiling binding, and global state; local forged tags, remote tag
  object mismatch, and target mismatch reject.
- Docker images are built from a verified clean Git-tree context, not merely
  caller labels, and generated version metadata cannot leak from the host.
- `src/sapphire_flow/_version.py` is ignored by both Git and Docker context;
  editable install can generate it while `git status --porcelain` remains clean.
- The coherent migration removes obsolete `bump-my-version` dev dependency and
  only its unused lock graph, unless a maintained use is explicitly named.
- Both default and Aquacast Docker variants expose matching package version and
  full source revision labels and runtime version.
- Operator receipts are atomically created credential-free JSON files that reject
  overwrite and record remote tag object OID, peeled source OID, package version,
  full source SHA, Git tree, mutable local tag, immutable local image ID, labels,
  and actual container import result for both Docker variants, with image IDs
  verified by readback; concurrency and failure-path tests prove no overwrite,
  no partial valid receipt, and no credential leakage.
- Compose build path passes the same version/source args and preserves BuildKit
  secrets, not credential build args.
- Existing CI gates and supply-chain credential boundaries remain preserved.
- `tag-main.yml` is retired only together with the new reviewed release helper
  and policy docs.

## Dependency graph

```json
{
  "T1": [],
  "T2": ["T1"],
  "T3": ["T1"],
  "T4": ["T1"],
  "T5": ["T1", "T2", "T4"],
  "T6": ["T1", "T4"],
  "T7": ["T1", "T2", "T3", "T4", "T5", "T6"]
}
```

## Required review before implementation

This is high-risk supply-chain-adjacent tooling under current `AGENTS.md` and
the global implementation workflow. The parent/orchestrating session sequences
renewed independent full-plan review before implementation; the owner has already
commissioned the extra supply-chain design review. Implementation waits for the
current review and owner-approval path. This plan does not add a separate
readiness gate.

## Open questions and blocked mechanics

1. GitHub's actual handling of an atomic push that creates an immutable
   annotated tag while non-fast-forward updating `refs/heads/release-state` is
   not verified against the real repository and current rules. If the first
   owner publication rejects the state-branch update, the design is blocked and
   must not weaken to tag-only publication.
2. The exact setuptools package-data configuration must be proven against the
   real SAPPHIRE artifact. The plan names the required resource inventory, but
   implementation must verify the generated wheel and sdist contents.
3. CI may not be able to build the Aquacast image without the private Aquacast
   token. If so, T6 acceptance needs an owner-authorized local/mac-mini receipt
   for that variant before the release-identity transition is accepted.
4. If the owner does not want the final legacy patch bump before the dynamic-
   version switch, that needs an explicit one-time owner exception. The old bump
   command cannot remain the post-switch policy after its configured version
   fields are removed.
5. Cooperative state cannot distinguish the expected latest successful helper-era
   release from the greatest surviving helper tag if a privileged actor deletes
   `refs/heads/release-state` plus a latest tag suffix. If all helper-era
   annotated tag metadata is gone, it also cannot distinguish that state from a
   never-bootstrapped repository. Default publish rejects absent state, explicit
   `--bootstrap` remains owner-attested first migration, and known damage repair
   requires external expected-latest evidence; the helper must not claim generic
   ACL guarantees or impossible detection.
6. Pre-merge artifact/receipt tests use local fixture remotes only. Actual
   production artifact receipts are owner-run after an owner-published release;
   implementation agents must not create real remote tags to satisfy this proof.
