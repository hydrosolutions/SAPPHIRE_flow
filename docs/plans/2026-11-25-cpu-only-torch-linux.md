---
status: DRAFT
---
# Install CPU-only torch and torchvision on Linux

**Status:** DRAFT 2026-11-25, unreviewed. No plan ID yet (owner grants IDs). HIGH RISK under
`AGENTS.md` (CI, dependencies, supply chain, image and release identity). Needs independent
Claude and Codex plan reviews before READY; the owner merges. Spike: draft PR #663, branch
`spike/cpu-only-torch`, commit `e7209f7e4` (do not merge; the implementation PR re-does or
cherry-picks it).

## Problem
`uv.lock` resolves `torch==2.10.0` / `torchvision==0.25.0` from PyPI. The Linux wheels pull
CUDA: 17 `nvidia-*`/`cuda-*` packages (about 3.0 GB, deployment-side figure). SAPPHIRE runs
inference on CPU. Every CI install and image build downloads that stack, and the image carries it.

## Measured (spike, one cold run each; no warm run yet)
Spike run 45608279136 (all green) vs main run 45607849353:

| Job | main | spike |
|---|---|---|
| aquacast-release-image-proof | 22.4 min | 16.2 min |
| unit (rest) | 6.3 | 4.9 |
| unit (scripts) | 4.5 | 3.1 |
| unit (adapters-flows) | 10.2 | 9.5 |
| unit (services) | 2.6 | 2.3 |
| integration (rest) | 22.7 | 21.7 (unchanged) |
| build-image-and-scan | unchanged | unchanged |

Deployment-side claims (not re-measured here): CPU wheels cp314 x86_64 total 190,890,868 B vs
923,721,187 B for the locked pair. In the spike lock scikit-learn stays 1.9.0 and triton stays
(FlashRNN 1.0.6). Single runs, no variance estimate; no other numbers are claimed.

## What changes (spike diff: `pyproject.toml` and `uv.lock` only)
1. `pyproject.toml` after the pypi `[[tool.uv.index]]` (:127-130): add
   `[[tool.uv.index]]` `name="pytorch-cpu"`, `url="https://download.pytorch.org/whl/cpu"`,
   `explicit=true`. Explicit means only packages that name it resolve from it.
2. `[tool.uv.sources]` (:132): `torch` and `torchvision` bound to `pytorch-cpu` with
   `marker = "sys_platform == 'linux'"`. macOS keeps PyPI wheels.
3. `override-dependencies` (:113-118) unchanged: `torch==2.10.0`, `torchvision==0.25.0`
   (CVE-2026-24747); the local version `+cpu` satisfies `==2.10.0`.
4. `uv.lock`: Linux torch becomes `2.10.0+cpu` / `0.25.0+cpu`, registry
   `https://download.pytorch.org/whl/cpu`; the nvidia/cuda subgraph drops. Wheel URLs point at
   the redirect host `download-r2.pytorch.org`, each with a sha256 hash.
5. Docs: `security.md` :796 says the index block is "informational, not a restrictive control":
   untrue after this change, rewrite it and add a Supply chain entry for the new index; add a
   short paragraph to `docs/deployment/dependency-maintenance-2026-11.md` (torch pins now come
   from the CPU index; manual bump procedure below).

No change to `Dockerfile` :44-63 or `ci.yml`: all installs run `uv sync --frozen`, which reads
the index from the lock, so no `--index` flags or `UV_INDEX` env. If CI shows otherwise, that is
a finding to justify, not an assumption. No change to `dependabot.yml` or `dependency-safety.yml`.

## Platform and Python matrix
| Target | Python | Expected |
|---|---|---|
| CI Linux x86_64 (`ci.yml` :46) | 3.12 | `+cpu` cp312 manylinux_2_28 wheel (in lock) |
| Image x86_64 (`Dockerfile` :1, :68) | 3.14.8 | `+cpu` cp314 wheel (the 190,890,868 B pair) |
| Linux aarch64 | 3.12-3.14 | `+cpu` aarch64 wheels are in the spike lock; never installed: UNVERIFIED |
| macOS (dev) | any | unchanged; confirm by lock diff |

## Supply chain: new host
Policy (`security.md` ~:740-810): lock committed, `--frozen` installs, wheel-only guard,
Dependabot cooldown, Trivy fs and image scans, SBOM. Nothing forbids another index, but this
adds a second index and a second download host (`download.pytorch.org` serves the index and
redirects to `download-r2.pytorch.org`).
- Integrity: every wheel is hash-pinned and `--frozen` verifies it, so a changed file fails the
  install. Residual: trust on first lock (who can publish there). State this in `security.md`.
- `explicit = true` removes dependency-confusion exposure: no other package resolves from it.
- Network: CI and the image build must reach both hosts (spike green on hosted runners). Whether
  any other build environment is firewalled is UNVERIFIED.
- `exclude-newer` is not set in this repo; this plan does not introduce it.
- `tools/dependency_safety.py`: `WATCHED_FILES` (:95-100) include `pyproject.toml` and `uv.lock`.
  The pyproject classifier (:595-660) fires on `requires-python`, build-backend inputs, FI rev;
  the lock classifier (:670) only on a major bump of `_NATIVE_EXTENSION_PACKAGES` (:116, no
  torch). Expected: ALLOW, no allowlist entry. Confirm by the real `dependency-safety` run on
  the implementation PR; extend the classifier only if that run contradicts this.
- Wheel-only guard (`--no-build`, `security.md` :800): all wheels, expected to pass; verify in CI.

## Dependabot, token guard, tests
- Dependabot `uv` (`dependabot.yml` :27-40) with an explicit index and a `+cpu` local version:
  UNVERIFIED. Possible failures: a torch PR resolved against PyPI that mismatches the pinned
  `+cpu` file, or a failed lock update. Torch is pinned by exact overrides, so it may not be
  proposed at all. Check the first weekly run after merge and record it here. If bad, owner
  decides whether to ignore `torch`/`torchvision` in `dependabot.yml`.
- Manual torch bump (goes into the maintenance doc): change both override pins together, confirm
  both versions exist on the CPU index for cp312 and cp314, `uv lock`, confirm no `nvidia-*`
  returns on Linux, full CI, note the new wheel hashes in the PR.
- Aquacast-token guard (`ci.yml` :162, :363-404; `cicd.md` :1065-1085): a PR that changes
  `uv.lock` cannot skip the Aquacast extra; this PR changes it, so it needs a non-Dependabot
  author with the token available. A Dependabot PR touching the lock fails by design. No change.
- Tests: `tests/unit/tooling/test_recap_dependency_pin.py` parses pyproject and lock for
  recap-dg-client only; `tests/unit/tools/test_image_identity_probe.py` :176-185 pins
  `import aquacast, torch` in the Dockerfile. Neither names an index. Run the full suite; add no
  text-pinning test (MVP).

## Image identity, SBOM, Trivy
New image digest and SBOM (17 packages gone, `torch` becomes `2.10.0+cpu`). Trivy config and
`.trivyignore` unchanged. Confirm the scanners still evaluate CVE-2026-24747 against
`2.10.0+cpu`: a local-version string may not match an advisory range, and a scanner miss is not
proof of safety (owner may want a one-off manual check). This PR publishes and tags nothing;
the `RELEASE_TARGET` rule applies as to any non-docs PR.

## Deployment-side coordination
The deployment agent owns the constructor-environment-guard fix (branch
`docs/constructor-environment-guard`) and re-runs installed-runtime qualification on the exact
candidate. Nothing here widens their guard's exception. Hand-off: exact implementation commit
SHA; the `uv.lock` dependency diff (torch, torchvision, 17 removed, wheel hashes); scikit-learn
1.9.0 and the admitted `sklearn/__init__.py` SHA-256
`ce663dfdb31bdbd4292166527e43a4f861b31b445d2ee894e725e82c1e738f78` (re-check on the candidate: a
dependency change may require initializer requalification); the CI timings. Every new image needs
runtime identity admission. A CPU result alone authorizes neither publishing nor deployment.

## GPU policy and revert
No GPU change beyond the Linux marker: locked Linux installs become CPU-only, so Linux GPU use
needs a separate decision. Revert: delete the `pytorch-cpu` index and the two source entries,
`uv lock`, confirm torch returns to PyPI `2.10.0` with the nvidia subgraph.

## Unverified
- FlashRNN/triton with CPU-only torch: triton stays in the lock; whether FlashRNN imports and runs
  without CUDA is UNVERIFIED. The test is a real cmal_small load and predict on the CPU image
  (deployment qualification), not CI import checks.
- aarch64 install, Dependabot behavior, firewalled build hosts, warm-cache timings.

## Acceptance
1. Implementation PR touches only `pyproject.toml`, `uv.lock` and the doc edits above.
2. Full suite and all CI jobs green on the final commit (aquacast image proof, Trivy fs and
   image, SBOM, wheel-only guard, `dependency-safety` verdict reported); `uv lock --check` clean.
3. No `nvidia-*` or `cuda-*` Linux entries remain in `uv.lock`.
4. Cold and warm timings from real runs (main vs PR, both cache states) recorded in the PR; no
   saving is claimed beyond them.
5. Hand-off sent; deployment qualification result recorded before any publish decision.

## Open questions for the owner
1. Is dropping Linux GPU installs acceptable, or keep a future GPU extra/index route?
2. Accept `download.pytorch.org` and `download-r2.pytorch.org` as trusted hosts (hash-pinned,
   trust on first lock)?
3. If Dependabot misbehaves with the index, ignore `torch`/`torchvision` there (manual bumps)?
4. Is aarch64 Linux a real target needing an install test before merge?
