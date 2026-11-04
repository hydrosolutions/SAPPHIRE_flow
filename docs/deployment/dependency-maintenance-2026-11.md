# Dependency maintenance — November 2026 experiment

This is an owner-authorized SAP-only compatibility experiment, not a deployment
or a claim of upstream Aquacast support. Aquacast stays at revision
`c271067bc34a0fee1dc0d8a710e95c888d29f72e`. Its declared `torch==2.9.*`
requirement is overridden by exact SAP pins `torch==2.10.0` and
`torchvision==0.25.0` to address CVE-2026-24747. The existing Rich override stays.

The targeted uv 0.11.7 resolution changes only Torch, torchvision,
`nvidia-nvshmem-cu12`, Triton, and the required new `cuda-bindings` and
`cuda-pathfinder` records. CUDA/Triton dependencies retain their platform markers.
Python, GIS, scientific configuration, FI, Recap and Aquacast source stay fixed.
No checkpoint change, retraining or source-side model workaround is included.

The same change incorporates the pending setup-uv action 10.3.3 SHA and Caddy
2.11.7 index-digest refresh. The uv binary remains 0.11.7. These updates do not
activate services. We will not manually close the independent Dependabot proposals
before merge; Dependabot may automatically close superseded proposals afterward.

Local Linux/x86_64 CPU qualification on Python 3.14.8 passed with the unchanged
trusted cmal_small checkpoint and recorded historical workshop inputs. Predict
and hindcast each repeated exactly, and baseline/candidate outputs matched
exactly. Those two-day inputs retain their raw-water-level, expired-rating,
unknown-zero, partial-support and historical-imputation warnings. This is
dependency compatibility evidence, not native operational forecast acceptance.

The independently measured Linux baseline reports the existing AQ/Rich mismatch
and missing eccodeslib. The candidate adds only the authorized AQ/Torch mismatch.
Dependency checks still exit nonzero; these reports are not hidden or represented
as clean installs. No GIS repair is included. Full final-commit CI and normal
default/Aquacast release-image proofs plus source/default/Aquacast image security
gates remain required. No baseline HIGH finding is automatically accepted.

The existing Aquacast image-proof CI job now scans its actual normal Dockerfile
image with the existing pinned Trivy action after a successful authorized build.
HIGH/CRITICAL vulnerability and secret findings block the job; the existing
ignore-unfixed policy is unchanged. Its job budget increases from 30 to 35 minutes
to fit the five-minute scan while preserving the prior three-minute overhead.
The no-token degraded case reports that no
image proof or scan ran and remains unavailable when `uv.lock` changes.
