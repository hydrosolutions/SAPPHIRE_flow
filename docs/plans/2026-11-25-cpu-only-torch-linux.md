---
status: READY
---
# Install CPU-only torch and torchvision on Linux

**Status:** READY rev 4, 2026-11-25. No plan ID yet (owner grants IDs). HIGH RISK under
`AGENTS.md` (CI, dependencies, supply chain, image identity). Reviews: Claude round-2 READY
(`d921aa19a`), rev-3 READY (`bdffac224`); Codex READY (`bdffac224`). Rev 4 adds only the owner
decisions, the GPU note and the FlashRNN check below. The owner merges. Spike: draft PR #663,
commit `e7209f7e4` (not merged; the implementation PR redoes it).

## Owner decisions (2026-11-25)
1. Linux CPU-only torch/torchvision approved; a future GPU route is documented, not built.
2. Both PyTorch hosts accepted with the availability risk; no mirror.
3. The REVIEW classifier (new/changed index, changed lock registry source) goes in this PR.
4. Merge only after successful predictions on x86_64 AND arm64 and a separate FlashRNN CPU
   check. Deployment builds and qualifies images from the exact PR commit while the PR is open.
5. Integration sharding is approved separately, after this change; not started here.

## Problem
`uv.lock` resolves `torch==2.10.0` / `torchvision==0.25.0` from PyPI. On Linux x86_64 that pulls
17 `nvidia-*`/`cuda-*` packages (about 3.0 GB, deployment-side figure). SAPPHIRE infers on CPU.
Linux aarch64 PyPI wheels already carry no CUDA (nvidia deps are x86_64-only), so aarch64 gains
no size; for it the change only moves provenance, hashes and host.

## Measured (spike, one run each; cache state not recorded)
Run 45608279136 (green) vs main run 45607849353. Durations alone do not prove cold caches
(only the Aquacast jobs install torch: `ci.yml:384`, `Dockerfile:44,59`; plain `uv sync` jobs and
the default image variant omit the extra). Savings claims are limited to the extra-enabled unit
shards and the Aquacast image proof:

| Job | main | spike |
|---|---|---|
| aquacast-release-image-proof | 22.4 min | 16.2 (clearly outside noise) |
| unit rest / scripts / adapters-flows / services | 6.3 / 4.5 / 10.2 / 2.6 | 4.9 / 3.1 / 9.5 / 2.3 (indicative) |
| integration (rest), build-image-and-scan | 22.7, unchanged | 21.7, unchanged |

Deployment-side claims (not re-measured): CPU wheels cp314 x86_64 190,890,868 B vs 923,721,187 B.
In the spike lock scikit-learn stays 1.9.0 and triton stays (FlashRNN 1.0.6). Single runs.

## What changes
1. `pyproject.toml` (:127-132): add `[[tool.uv.index]]` `name="pytorch-cpu"`,
   `url="https://download.pytorch.org/whl/cpu"`, `explicit=true`; bind `torch` and `torchvision`
   in `[tool.uv.sources]` to it with `marker = "sys_platform == 'linux'"`. Overrides (:113-118)
   unchanged; `+cpu` satisfies `==2.10.0` (PEP 440 local version; Codex confirmed
   `uv lock --check --offline` passes on the spike with uv 0.11.7).
2. `uv.lock`: Linux torch/torchvision `+cpu`, registry `https://download.pytorch.org/whl/cpu`,
   wheel URLs on `download-r2.pytorch.org`, sha256 per wheel; nvidia/cuda subgraph gone. Lock is
   regenerated only with uv 0.11.7 (`tool.uv.required-version`).
3. `tools/dependency_safety.py` + `tests/unit/tools/test_dependency_safety.py` (see Supply chain):
   small REVIEW classifier. This is code in a CI gate: reviewed as such.
4. Docs: `security.md` :796 ("informational, not restrictive") rewritten and a Supply chain entry
   added; `dependency-maintenance-2026-11.md` :5-9 (override paragraph) and the manual bump
   procedure below; `cicd.md` :94 (image dependency note).
No `Dockerfile`/`ci.yml` change: every install is `uv sync --frozen` (`ci.yml` :73, :384, :403,
:604, :701-702, :834, :1731; `Dockerfile` :44-63; nightly/lindas workflows), and
`scripts/install_host_monitor.py:94` does not install torch. No install path bypasses the lock.

## Platform and Python matrix
The Linux marker covers x86_64, aarch64 and every other Linux arch; Windows stays on PyPI;
macOS keeps existing PyPI wheel coverage (no change expected; confirm by lock diff).
| Target | Python | Expected |
|---|---|---|
| CI Linux x86_64 (`ci.yml:46`) | 3.12 | `+cpu` cp312 manylinux_2_28 |
| Image x86_64 (`Dockerfile:1,68`) | 3.14.8 | `+cpu` cp314 |
| Linux aarch64 image (arm64 builder path `Dockerfile:8-10`, `ci.yml:641`, `cicd.md:94`; the Mac mini builds on arm64 Linux) | 3.14.8 | `+cpu` cp314 manylinux_2_28_aarch64 in lock; in scope, never installed yet |

## Supply chain: new host
Hash pins give integrity against the reviewed lock, not publisher authenticity and not
availability: lock URLs name `download-r2.pytorch.org`, a different origin from the index host; if
PyTorch moves or removes files, frozen installs fail. Accepted and recorded in `security.md`.
`explicit = true` gives only the narrower guarantee that unrelated packages do not resolve from
this index in normal uv project resolution (not pip or source-disabled resolution). The owner
explicitly accepts both hosts. `exclude-newer` is not set in this repo; not introduced.

Gate gap (verified): `classify_pyproject_diff` (:595-660) never reads `[[tool.uv.index]]` or
`[tool.uv.sources]` index bindings, and `classify_uv_lock_diff` (:670-690) only checks major
bumps of `_NATIVE_EXTENSION_PACKAGES` (:116). Without a change this PR classifies ALLOW and a
future index or registry swap would too. Proposal: REVIEW (not BLOCK) finding on (a) a new,
removed or changed `[[tool.uv.index]]` url, (b) a changed `package.source.registry` for any
locked package. Tests: each trigger fires; the unchanged pypi block does not. Fallback if the
owner declines: record the gap in `security.md` as accepted. This PR itself then reports REVIEW,
which is expected and passes.
Wheel-only guard (`ci.yml:701`) omits `--extra aquacast`, so it never checks torch; see Acceptance.

## Dependabot, token guard, tests
- Grouped `uv-patch` (`dependabot.yml` :27-37, patterns `*`) may re-resolve the whole lock and
  drop `+cpu`, the source binding or markers; UNVERIFIED. First weekly run after merge is a named
  owner check (the owner): inspect the whole resulting lock for registry assignments, Linux
  markers, overrides, hashes and absence of CUDA deps. Pre-decided fallback: add `ignore` entries
  for `torch` and `torchvision`, and leave any broken updater PR unmerged (reviewed manual updates).
- Manual torch bump: change both override pins together, confirm both versions exist on the CPU
  index for cp312 and cp314/aarch64, `uv lock` (uv 0.11.7), `uv lock --check`, no `nvidia-*` on
  Linux, full CI.
- Token guard (`ci.yml:363-370, 382`): the extra installs whenever `AQUACAST_TOKEN` is present,
  whoever authored the PR; an absent token fails, except a Dependabot-attributed PR with unchanged
  `uv.lock`. This PR changes the lock, so it needs the token present (fail-closed preserved). No
  change.
- Tests pinning text: `test_recap_dependency_pin.py` (parses recap-dg-client only) and
  `test_image_identity_probe.py:176-185` (Dockerfile `import aquacast, torch`) name no index. No
  new text-pinning test beyond item 3.

## Acceptance evidence
1. Full suite and all CI jobs green on the final commit; `uv lock --check` clean; no `nvidia-*` or
   `cuda-*` Linux entries in `uv.lock`; `dependency-safety` verdict reported (REVIEW expected).
2. Uncached wheel evidence for torch and torchvision on x86_64 and arm64 Linux. A bare
   `--no-build` cannot pass: it also refuses the editable project and every git-source package.
   Reuse the existing guard's exclusions (`ci.yml:701`: `--no-install-project`,
   `--no-install-package forecastinterface`, `--no-install-package recap-dg-client`) and add
   `--extra aquacast --no-install-package aquacast` (a git source build, accounted for as an
   excluded exception, built by the image proof instead). On arm64 also exclude the existing
   `exactextract` sdist exception (`ci.yml:639-641`), nothing else. Command: `uv sync --frozen
   --no-build --no-cache -v <exclusions>`; `--no-cache` makes it uncached, and the log must show
   the `+cpu` wheel URLs and the lock hashes for torch and torchvision. Chosen over `uv pip
   install --only-binary` because it uses the real lock and installs what the image installs.
   `flashrnn` 1.0.6 is locked with both an sdist and a `py3-none-any` wheel (`uv.lock` ~:1442), so
   `--no-build` should take the wheel; add `--no-install-package flashrnn` only if the run shows
   it falls back to the sdist (it would then run a build backend, so name it in the PR). Evidence
   is the `uv sync` log (wheel URLs) cross-checked against the hashes in `uv.lock`. If any other
   package fails `--no-build`, list it as a named exclusion in the PR.
3. One-off SBOM of the exact Aquacast candidate image (CI SBOM covers the default torch-free
   image, `ci.yml:1292`; the Aquacast job has Trivy only); record scanner and DB versions. Both
   `torch` and `torchvision` `+cpu` must appear in the SBOM and scanner input. Presence proves
   discovery only; zero findings for an already-fixed version is not evidence of matching. Also
   show version normalization and range comparison for local versions: in a throwaway environment
   run the same scanner on a torch below the CVE-2026-24747 fix with and without a `+cpu` suffix
   (or another known-vulnerable local-version package) and show it reports the finding in both
   cases, and that `2.10.0+cpu` is evaluated against the fixed range. Existing gates and
   exemptions stay unchanged.
4. Real inference: `import torch` plus an Aquacast predict (cmal_small) in the x86_64 image and in
   the arm64 image. This also covers triton/FlashRNN (triton now installs without CUDA torch;
   import/run on CPU UNVERIFIED until this passes). The PR is not merged before this check unless
   the owner explicitly accepts, with deployment qualification then gating publish.
   Owner decision 4: merge waits for both architectures.
4b. Separate FlashRNN CPU check on both architectures. cmal_small is a plain LSTM and does not
   exercise FlashRNN. Re-inspect the actual candidate's Aquacast source (LSTM vs XLSTM/FlashRNN
   code paths) and state which paths the registered models reach. If any reaches FlashRNN, run it
   on CPU (import and one forward). CPU tests do not qualify GPU Triton kernels.
5. Timings: two real runs each of main and PR with cache evidence recorded; claims limited to the
   extra-enabled jobs.
6. Deployment hand-off sent and its result recorded before any publish decision.

## Image identity and deployment coordination
New digest and SBOM per image; this PR publishes and tags nothing, `RELEASE_TARGET` rule as for
any non-docs PR. The deployment agent owns the constructor-environment-guard fix (branch
`docs/constructor-environment-guard`) and re-runs installed-runtime qualification on the exact
candidate; nothing here widens their guard's exception. Hand-off: exact commit SHA; `uv.lock`
dependency diff (torch, torchvision, 17 removed, wheel hashes); scikit-learn 1.9.0 and the admitted
`sklearn/__init__.py` SHA-256
`ce663dfdb31bdbd4292166527e43a4f861b31b445d2ee894e725e82c1e738f78` (recheck on the candidate;
requalify the initializer if needed); CI timings; the Mac mini arm64 path. Every new image needs
runtime identity admission, including any rebuilt after a revert. A CPU result alone authorizes
neither publishing nor deployment.

## GPU policy and revert
No GPU change beyond the Linux marker: locked Linux installs become CPU-only (Linux GPU use
needs a separate decision). Revert is a new resolution, not a rollback: revert the implementation's
dependency commit together (keeping unrelated work), then `uv lock --check` and the gates. If
re-resolution is needed (uv 0.11.7 only), review the full lock diff.

## Future GPU route (note only, not implemented)
Add a second explicit index (for example `pytorch-cu1xx`) and select it by an extra or a
separate dependency group with conflicting-extras declared in `[tool.uv]`, or by a separate lock
for GPU hosts; the Linux marker on the CPU source would then be replaced by a non-GPU condition.
It needs its own plan, review, image variant, scanner evidence and Triton kernel qualification.
