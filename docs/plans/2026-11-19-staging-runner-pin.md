# Keep staging delivery on the qualified runner family

status: READY

## Problem and scope

Release delivery run 44509138367 for v0.1.1158 stopped before image building:
`ubuntu-latest` selected Ubuntu 26.04 with Docker 29.4.2, while the existing
exporter guard requires Docker 28.0.4. The prior successful delivery 44378574964
used Ubuntu 24.04; its current manifest still lists Docker 28.0.4. Nepal remains
on 0.1.1152. This is a delivery-runner repair, separate from CI-speed work.

## Change

Pin only the manual delivery workflow's `verify` job to `ubuntu-24.04`.
Keep the strict Docker 28.0.4 check, per-run exporter smoke, release binding,
source-CI proof, image scans and delivery/load/runtime boundaries unchanged.
Document that an OS-family pin is not an immutable runner image: future Docker
version drift still requires qualification. Add the runner selection to the
existing workflow contract test. Advance the release target with the repository
helper; do not change package versions or published tags.

## Sequence and acceptance

1. Independent Claude plan review approved (19 November); orchestrator marked READY.
2. Apply the workflow, contract assertion and concise deployment-doc update.
3. Run exporter/private-workflow contracts, workflow inventory/release-target
   tests, formatting and lint; independent patch review. Full CI must pass
   after the final change before owner merge.
4. After merge, publish a fresh testing release and qualify the hosted exporter
   on the pinned runner via the public smoke route. Then use exact-source FULL
   CI reuse for delivery, followed by verified load and the authorized Nepal
   rollout. Never rebind or retag 0.1.1158 or reuse its failed attempt approval.

No engine guard widening, runner installation, new export path, dependency
changes, source-CI runner changes or host mutations are part of this patch.

```json
{"milestones":[{"id":"review","depends_on":[]},{"id":"patch","depends_on":["review"]},{"id":"verify","depends_on":["patch"]},{"id":"owner_merge","depends_on":["verify"]},{"id":"deploy","depends_on":["owner_merge"]}]}
```
