---
status: READY
title: Require a deployment display-timezone choice
---

# Require a deployment display-timezone choice

Owner decision: local operational users should normally see their deployment's
local time. Require an explicit named display timezone during setup; explicit UTC
is valid. UTC may remain a clearly provisional fallback for incomplete previews.
This supersedes the five-field neutral-default decision in the generic-input plan.

## T1 — Profile boundary and guidance

Paths: `src/sapphire_flow/config/deployment_profile.py`, `tools/deployment_profile.py`, generic
examples, quick-start/profile reference, config reference and related tests.

- Remove `default_display_timezone` from the neutral allowlist (four fields remain).
  Add it to required deployment inputs with administrator-owned, actionable guidance.
- Validate a supplied value with the existing `zoneinfo.ZoneInfo` boundary pattern;
  require a nonempty valid named timezone, accept explicit `UTC`, reject invalid
  strings/types and numeric offset strings with sanitized diagnostics. Do not infer
  a zone from the host, agency, station coordinates or hydrological grid.
  Detect omission in the merged raw TOML before reader defaults are applied.
  Redaction alone is not validation: emit an explicit fixed-field diagnostic for
  invalid supplied values, covering empty string, numeric type and offset strings.
- Missing timezone is `INPUT_REQUIRED`. Incomplete `show` remains informational;
  `check` and configured `show` fail for that missing choice. An incomplete preview
  may show the reader's UTC literal default with an explicit `fallback` provenance
  label and missing-choice diagnostic. Configured profiles with no choice must not
  display fallback UTC as effective/approved. Supplied invalid values never fall back.
  Extend `EffectiveSetting.default_status` with `fallback`, propagate it through
  JSON/text output and document its meaning. Filter fallback rows from configured
  profiles both when full parsing succeeds and when it cannot complete.
- Keep `DeploymentConfig`'s legacy runtime default and existing displays unchanged.
  This changes preparation validation, not dashboard/report formatting or runtime
  rollout. Storage/transport timestamps, hydrological boundaries, model training
  conventions and time-grid activation restrictions remain separate and unchanged.
- Generic templates show a commented named-timezone choice, with no agency default.
  Docs explain one choice per deployment, normally local time, with named zones
  handling daylight saving where applicable. No per-user timezone feature is added.

## T2 — Verification and delivery

One independent Claude plan review precedes READY and implementation; one patch
review follows. Reports stay local. Existing worker implements the bounded change.
Test missing/empty/invalid timezone, explicit UTC and a DST-observing named zone,
overlay provenance, incomplete versus configured exit behavior, fallback labels,
redaction and unchanged hydrological-grid declarations. Do not test invented UI
conversion or make scientific changes. Keep existing four neutral-default tests.
Update the two existing tests that currently assert five neutral fields to four;
preserve explicit-zone, interpolation-provenance and secret-canary regressions.

The feature branch was reconciled with latest main `9de2d439e`: preserve its new
registrar subset/source-root options. The one overlap was resolved by retaining
subset validation/filtering and extending the explicit environment injection to
that selector, suppressing logs only for pure preview. Add parity/isolation tests;
do not expand profile installation schema or launch behavior for these new options.
Concrete integration paths: `src/sapphire_flow/cli/register_deployments.py`
(`_only_deployments`, `_flow_source_root`, `_build_specs`, `build_deployment_specs`)
and `tests/unit/cli/test_register_deployments.py`. Source-root behavior is unchanged;
subset filtering must use explicit preview input rather than ambient environment.

Run focused suites, lint/types, existing Compose safety regressions and full
final-code CI before ready-for-review. Keep the current single PR release-target
bump based on the reconciled base. No merge, deployment, credential or live-config
changes. PR #544 remains draft until these gates pass.

```json
{"phases":[{"id":"T1","depends_on":[]},{"id":"T2","depends_on":["T1"]}]}
```

Independent Claude plan review completed with no hard blockers. Its clarifications
above specify raw-input presence, explicit invalid-value diagnostics, fallback
typing/rendering, affected tests and exact registrar paths. The reviewer did not
verify the registrar reconciliation; the implementation review and parity tests
must cover it. Report remains local at
`/private/tmp/sapphire-display-timezone-plan-review.txt`. Orchestrator sets READY.

## Implementation checkpoint

T1/T2 implemented in the preparation boundary: explicit valid named timezone,
fixed invalid-input diagnostics, omission-only incomplete UTC fallback and four
neutral defaults. Generic guidance and the earlier plan's supersession notice
are updated. Legacy runtime defaults, formatting and scientific grid/model
semantics are unchanged. Registrar subset isolation/runtime parity tests cover
the orchestrator’s rebase adjustment to inject subset selection and suppress
preview logging; the implementation worker made no further registrar source changes.

Focused validation: 698 config/profile-tool/forecast-tool/registrar tests passed
with Compose 5.6.0; 66 profile-tool tests passed with Compose 2.38.2. Focused
profile/registrar source and config-profile test type checks have zero errors.
Independent patch review and final-code full CI remain orchestrator-owned gates;
this checkpoint does not approve the patch or imply a complete offline pass.

Independent Claude patch review completed with no blockers. Its directory-key
timezone edge case was reproduced and fixed: timezone filesystem errors now
produce sanitized invalid-input diagnostics. The source-root preview concern was
not applicable: that read is inside registration, not spec construction; a new
assertion proves pure preview cannot invoke it. The full report stays local at
`/private/tmp/sapphire-display-timezone-implementation-review.txt`.
Post-review config-profile/tool/registrar verification passed 215 tests, focused
types, Ruff/format and whitespace checks. Full final-code CI is the remaining
delivery gate and its evidence is recorded in PR #544.

Independent patch review found no blockers. Its timezone-directory edge case is
fixed with sanitized `OSError` handling and directory-key/fault-injection tests.
Pure-preview tests now forbid runtime source-root lookup. After these corrections,
215 focused profile/config/registrar tests passed; focused types and Ruff passed.
The earlier broad run and both Compose-version results precede this bounded fix;
final-code full CI remains outstanding.
