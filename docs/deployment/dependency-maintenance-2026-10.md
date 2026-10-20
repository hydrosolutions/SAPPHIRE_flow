# Dependency maintenance — October 2026

This maintenance change is separate from Nepal21 implementation and does not
activate or deploy a release. Owner review and merge remain required.

## Updates

- `aiohttp` 3.14.3 → 3.14.4 addresses the two HIGH findings reported by the
  filesystem and default-image Trivy gates (CVE-2026-111064 and
  GHSA-2g87-pp6c-29x5). Scans remain enforced; no ignore entry is added.
- `yarl` 1.24.5 → 1.25.1 is required by aiohttp 3.14.4's `>=1.25.1,<2.0`
  dependency, not an unrelated bulk upgrade. All other lock package records and
  lock metadata remain unchanged, including FI, Aquacast, Recap and Prefect pins.
  uv 0.11.7 regenerated the lock; aiohttp remains transitive.
- Both Docker stages use the official Python 3.14.8-slim index digest. This stays
  on Python 3.14; native unit CI remains Python 3.12. The actual image checks must
  establish runtime compatibility. The packaging test's exact image-pin assertion
  moves with the pin; it is not removed or skipped.
- Caddy moves from 2.11.6 to 2.11.7 at the verified official index digest. Upstream
  describes fixes for HTTP/2 proxy crashes and prematurely cut-off streams.
- CodeQL upload-sarif moves from 4.38.3 to 4.39.0 at its verified upstream tag
  commit. This repository uses upload-sarif only, not CodeQL init/analyze or a
  selected CodeQL tools bundle. The upload check still needs to succeed.

Official image index/tag identities were checked on 2026-10-20 and include
linux/amd64 and linux/arm64. Identity checks do not substitute for tests. Historical
plan/test-fixture version examples remain historical; active pin comments and
standards are updated. No source dependency is made public, and local resolution
excluded ambient personal credentials rather than acquiring new private access.

## Runtime installer removal

The first maintenance image scan found four HIGH findings in libraries vendored
by the base image's global pip 26.2.1: msgpack 1.1.2, setuptools 70.3.0 and urllib3
2.7.0. These are separate from the application lockfile. Pip 26.2.1 was still the
latest upstream release when checked on 2026-10-20.

Only the final runtime removes the complete global pip package, its metadata and
launchers, and the ensurepip bootstrapper including bundled wheels. The builder
and application venv remain unchanged. This is not metadata-only stripping or a
scan exception. Deletion affects the merged runtime filesystem; the original
public files remain in the base image layers.

In-container `python -m pip`, `python -m ensurepip` and default pip-seeded
`python -m venv` are intentionally unsupported. Install dependencies by rebuilding
the image, not by modifying a running container. Bare venv creation would require
`--without-pip`; runtime package-installing Prefect steps are not part of the
shipped local-source deployments.

The build asserts absence using the explicit global Python interpreter and checks
both imports and installer paths. CI also exercises the real entrypoint and
isolated application venv for both default and Aquacast images, in addition to
the existing final-image identity/import proofs. A stock-base smoke alone is not
application compatibility evidence.

## Acceptance

Require the full CI suite on the final base/head pair, filesystem/default-image
security gates, actual default/Aquacast image identity proof, SARIF upload and
isolated Caddy config/startup checks. Preserve failed evidence and inspect causes;
never weaken tests or scans to merge. Do not infer a live upgrade or running-service
health from successful builds. Existing Dependabot proposals are coordinated in
the maintenance PR; their closure/merge remains owner-controlled.
