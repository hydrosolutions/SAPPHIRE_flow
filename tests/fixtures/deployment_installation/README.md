# Persistent installation qualification

Synthetic CI-only inputs; no agency data or committed credentials. The integration
test reuses `../deployment_rehearsal` and `../deployment_profile`, materializes them
outside the checkout, and selects a unique project, explicit local HTTP/same-origin
route and fresh random delivery files. There is no second profile schema.

Run on a dedicated disposable Linux amd64 Docker Engine 28+ host with matching
preloaded default/Aquacast development images and the standard pinned infrastructure.
The current CI image-proof job prepares these once and runs rehearsal first, then
this qualification. The explicit opt-in is destructive **only for test-owned
resources**; never point it at an operational daemon.

```bash
SAPPHIRE_INSTALL_ACCEPTANCE=dedicated-test-daemon \
SAPPHIRE_INSTALL_IMAGE_TAG=ci-proof-<source-sha> \
SAPPHIRE_INSTALL_QUALIFICATION_REPORT=/private/test-evidence/qualification.json \
uv run --no-sync pytest tests/integration/test_deployment_install.py -m deployment -q --tb=short
```

The host user must be able to create the installation lock in `/run/lock` and use
the local rootful Docker socket. Ports 48189 and 48190 must be free. The success and injected-failure cases use
distinct ports so the second fresh install does not depend on immediate socket reuse. Dependencies and images
are prepared before the test; the installer never pulls or builds. Use a clean
source checkout matching the development image identity.

Acceptance covers real install, paused services, loopback health/unauthenticated
responses and an internal authenticated token probe with revocation. Independent
DB checks confirm revocation. Database and artifact-volume markers survive explicit
stop/start with the same containers and completed init. A missing delivery refuses
before any retained service starts. Read-only verification preserves private state
and token count. An unrelated sentinel remains unchanged during lifecycle and
cleanup. A second fresh install has init killed; data stays retained, repeat install
refuses, and guarded stop still works.

The test harness rechecks daemon identity and installation/project labels before
removing its own containers, networks and volumes. Production lifecycle commands
still retain data. Failures upload only an allowlisted qualification summary;
private state, delivery files and raw logs are not artifacts. A failed cleanup is
a failed qualification. No host reboot, daemon restart, live sources, station/model
onboarding, backup/restore or scientific readiness is proved here (reboot is PI4).


The hosted job stops its own Buildx builder after the image builds, before the
installer's idle-daemon check. Do not loosen that check to accommodate unrelated
running containers. A bounded qualification may fail before all checks run;
missing cleanup evidence never becomes a pass. If an uncatchable interruption
leaves a probe, inspect its private recovery record, exact
`sapphire-install-probe-` name and `org.sapphire.installation` label against the
recorded daemon before manually removing it. Do not delete by prefix alone.

The evidence directory is created with private permissions when absent. An existing
directory must be owned by the invoking user with mode 0700; this is checked before
installation starts. Hosted CI uses a dedicated subdirectory under runner temporary
storage. Failure summaries are written there even when an assertion fails.
