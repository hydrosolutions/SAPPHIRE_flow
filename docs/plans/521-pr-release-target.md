---
status: READY
plan: 521
---
# Per-PR release target

Root authorized this bounded implementation after three independent successor planning
reviews. The owner selected a release target per PR; package identity remains SCM-derived.

## Contract
- `RELEASE_TARGET` alone holds canonical ASCII `X.Y.Z` and one LF. Each PR advances
  the tested base patch by one, without changing major/minor. Additional commits do
  not bump again. This is release intent, not a package version or release publication.
  Minor/major changes require explicit checker policy changes and owner review.
- Existing Python runs the stdlib wrapper with `-I`, without an outer `uv run` or sync.
  Validate raw regular files and the exact safe bump config before no-op or execution.
  Pinned `bump-my-version==1.6.0` performs the write. No second current version, hooks,
  extra file rules, commits or tags. Use an explicit child environment allowlist and
  native isolated tool flags. Record actual Python 3.12 tool dependencies; floating
  transitives are an accepted developer-tool risk, not reproducible release artifacts.
- CI checks `github.sha` equals HEAD with exactly the event base/head parents before
  reading raw regular Git blobs and checking arithmetic. Keep full-history checkout.
- Bootstrap only at `259f16e1f32dec0379f4ce9d2175657f944a2dab`, with absent base target
  and seed `0.1.1068`. Verified latest canonical tag `v0.1.1067` object
  `3286b358bf3e67a28ed16277f23ffcf4ad66ab9c` peels to
  `4ebf49c44942bd7db9f4d38d1ec72cf64cff910a`, an ancestor of that base.
  No public release-state ref was advertised. Recheck before publication/rebase;
  changed bootstrap facts require root review, not a guessed seed.
- Preserve historical validated publisher acknowledgment and old token reconstruction.
  Gate NEW publication on exact-source target equality immediately before NEW token
  creation. Preserve remote verification, monotonicity and atomic CAS.
- Owner checks current base and latest exact-head CI immediately before merge. A moved
  base requires target update and fresh CI. This is a required owner checklist, not
  a claim that GitHub protection enforces freshness.

## Scope
Exactly 14 files: `RELEASE_TARGET`, `.bumpversion.toml`, `tools/release_target.py`,
`tools/release_identity.py`, `.github/workflows/ci.yml`,
`tests/unit/tools/test_release_target.py`, `tests/unit/tools/test_release_identity.py`,
`tests/integration/tools/test_release_identity_git.py`,
`tests/unit/tooling/test_release_identity_workflow_policy.py`,
`tests/unit/tooling/test_workflow_policy_coherence.py`, `AGENTS.md`,
`docs/standards/cicd.md`, `docs/standards/security.md`, and this plan.
No dependency, lockfile, backend, generated-version, Docker or Plan520 changes.
No commit/push until root patch/gate disposition. No merge, tag, release or deployment.

## Phases and gates
1. Verified separate base, root READY and isolated native test environment.
2. Early existing packaging tests and genuine REDs; implement parser, wrapper, checker,
   precise publisher gate and native race fixtures that reach CAS with valid targets.
3. Native lowercase BUMPVERSION/CA/proxy controls, exact final tool receipt, quality
   checks and independent multi-model CODE review of frozen source.
4. Full stable tests after final changes; root publication disposition and owner PR
   approval. Plan520 integration requires a separate release after this policy lands.

```json
{"P0":[],"P1":["P0"],"P2":["P1"],"P3":["P2"]}
```

Evidence and full reviews remain outside commits. Existing packaging baseline: 76 passed.
