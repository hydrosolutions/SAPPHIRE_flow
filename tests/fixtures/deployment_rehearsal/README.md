# Disposable development rehearsal fixture

This generic synthetic fixture is only for an isolated test daemon. It is not an
agency deployment and proves no forecast or scientific readiness. Its application
and private Compose overlay are reused from `../deployment_profile/`; its separate
installation template requires real prepared image tags and full source identity.
The older offline fixture retains its deliberately fake identity and must not be
launched.

Prepare both `sapphire-flow:<tag>` and `sapphire-flow-aquacast:<tag>` from the clean
candidate checkout with `SAPPHIRE_RELEASE_VERSION=0.dev0+g<full HEAD>` and matching
source labels, through the existing build tooling. Preload the pinned PostGIS,
Prefect and Caddy images from the root recipe. Builds, pulls and real credential
reads are forbidden during the rehearsal itself. Do not run on a shared/live daemon.

With the normal project environment, the explicit integration qualification is:

```sh
SAPPHIRE_REHEARSAL_ACCEPTANCE=dedicated-test-daemon \
SAPPHIRE_REHEARSAL_IMAGE_TAG=<prepared-tag> \
uv run pytest tests/integration/test_deployment_rehearsal.py -m deployment -q
```

The test materializes all public inputs in its temporary private directory,
substitutes the current source revision and prepared tag, and invokes the actual
CLI from outside the checkout. It runs twice, then kills only the ownership-checked
init container in a third run. An unrelated labelled sentinel container, network
and volume must remain unchanged. A final guard cleans both rehearsal resources
and the test-owned sentinel after failures too. Original fake credential deliveries
must remain unchanged and absent from command output.

The combined test budget is 45 minutes, with reserved cleanup time. Each launcher
run separately allows 15 minutes plus two minutes for cleanup. Only the
allowlisted `qualification.json` summary may be uploaded; recovery records,
configuration snapshots, credentials and raw process output remain private and
must never become CI artifacts. CI sets an explicit output path for that summary.
Hosted development qualification is separate from later release/Linux provider
qualification and from the existing synthetic forecast smoke gate.
