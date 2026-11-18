---
status: READY
title: Generic deployment tooling and administrator-owned inputs
---

# Generic deployment tooling and administrator-owned inputs

## Decision and boundary

Owner decision, 2026-11-18: actual agency profiles belong outside the public
application repository. General instructions and examples must not name a real
hydromet. Reuse general defaults so administrators supply differences, not a full
copy of every application option. This revision supersedes the built-in agency
catalogue and path-based exemptions in the 2026-11-17 profile plan. Its preparation
scope remains: no launching, migration, complete offline PASS or forecast smoke
test is added. No existing live deployment is changed.

The existing feature is PR #544. Work stays in its isolated worktree and branch.
The branch has been reconciled with remote main
`e05fac5dd928aa283abaed8a33e4de7f1692fc36`, including the separate Gateway file-key
and time-grid work. Preserve those changes; use pure config parsers, never key
loaders. Supplied owner instructions require one independent Claude review per
nontrivial plan/patch, superseding the older repository multi-review wording.

## G1 — External profiles and generic public examples

Paths: `src/sapphire_flow/config/deployment_profile.py`,
`tools/deployment_profile.py`, `deploy/examples/`, associated config/tool tests.

- Remove the new `deploy/profiles/agency/` catalogue and
  `config/agencies/` template. Do not relocate real deployment data into fixtures.
- Keep one explicit selection form: `--profile-file PATH show|check`.
  Installation `agency_profile` is a manifest path resolved relative to its file.
  The profile name is an arbitrary private label, never a built-in lookup key.
- Remove named selection, agency-specific output branches, and file-path-based
  validation exemptions. Keep only incomplete/configured states; no legacy label
  can waive required inputs. Existing runtime readers and launch files stay intact.
  Specifically remove `_BUILTINS`, `shipped_legacy`, the built-in branch of
  `_manifest_path`, `name=` selection, and the schema/enum `legacy` state. Legacy
  recipe tests may correctly report missing inputs; never restore an exemption to
  make a compatibility test pass. Exercise wiring independently when appropriate.
- Publish generic incomplete profile/application/installation examples. They have
  no active scientific/source/identity values or runnable host assumptions. Standard
  service credential names/consumer declarations may remain generic technical
  examples; actual references and credentials remain administrator-controlled.
- Use generated synthetic private manifests in tests. Existing deployment wiring
  regression tests may reference existing tracked recipes without introducing a
  shipped agency catalogue or making legacy configurations pass stricter checks.

Acceptance: external arbitrary-label profiles work outside the repository/CWD;
  selector agreement, ordered relative paths, redaction and no-secret-read contracts
  remain. Public examples cannot silently select an existing agency's configuration.

## G2 — Defaults and administrator workflow

Paths: profile evaluator and tests, `docs/deployment-quickstart.md`,
`docs/deployment/profiles.md`, `docs/spec/config-reference.toml`, `README.md`,
relevant deployment touchpoint/CI notes and earlier profile plan status notice.

- Retain existing typed defaults as the single source of truth; do not introduce a
  second merge engine, a copied full defaults file or change runtime defaults.
- UTC display and Gregorian calendar are optional presentation overrides, not
  required agency decisions. Disabled forecast/observation/pipeline alerts remain
  safe initial defaults. Preview their actual effective values and default origin.
  Remove presentation fields from `_EXPLICIT_AGENCY_FIELDS` and the generic
  required-input checklist. Include pipeline alerts in preview. A small name-only
  neutral-default allowlist contains exactly these five fields; their values come
  from existing readers, not a duplicated defaults table. Mark each previewed
  default as neutral or requiring review; aggregate the latter into a developer-
  owned pending diagnostic. Explicit values are not labelled inherited defaults.
- Hydrological grid and model training conventions remain distinct, explicit
  scientific inputs. Preserve current time-grid activation restrictions.
- Keep required identity, sources, station/rating/model references and scientific
  policy separate from host/secret-delivery inputs. Preserve missing-input errors
  and developer-owned coverage gaps. Unknown inputs never become a pass.
- Audit provider-derived typed defaults (for example weather feature lists and
  source publication delay). Require explicit applicable provider choices rather
  than silently presenting those defaults as general. No wholesale policy redesign:
  unaudited scientific/operational defaults remain visible developer-owned review
  gaps under the accepted preparation scope.
  The closed audit in this increment is `reanalysis_source` (remains required),
  `available_nwp_parameters`, `available_past_only_nwp_parameters` (both required
  capability declarations, explicitly empty allowed), and
  `nwp_cycle_min_age_minutes` when the enabled forecast adapter consumes that
  setting. Missing explicit choices fail even if runtime readers retain historical
  defaults. An inherited value in a failed preview must not look endorsed. Existing
  adapter/section evidence gaps remain in addition to default-review diagnostics.
- Generic quick-start: copy annotated examples outside git; fill the short required
  checklist with data/model owners; select, show, then check the private profile.
  Explain required inputs, safe defaults, optional tuning and unresolved developer
  work. Link the maintained reference for details instead of duplicating defaults.
- Explain that private files may live in a separate private Git repository; secrets
  stay in a secret manager or delivery files, not Git. Existing country-specific
  runbooks/configurations are legacy compatibility material and are not a generic
  starter. Their later migration is separate work. Keep history separate from the
  generic instructions; do not erase existing source-specific adapter documentation.
- Existing optional operator/provider scopes are tightly bound legacy-runtime
  contracts, not generic extension points. Retain their structural constraints and
  omit them from generic examples; external manifests may explicitly reference
  supported checkout paths. Do not redesign those scopes in this pass.

Acceptance: omitting neutral presentation/disabled-alert settings uses actual typed
  defaults with provenance; explicit overrides win; missing scientific/source
  choices still fail. A reader can distinguish inputs to supply from settings to
  leave alone. No real agency name is needed in the general quick-start/examples.

## G3 — Review and delivery

One implementer owns G1 then G2. Reuse the existing worker. Independent Claude
plan review precedes READY; a single independent patch review follows implementation.
Fix concrete findings without automatic review loops. Keep full reports local.

Meaningful verification: selector/path and no-exemption tests; neutral defaults and
override provenance; source-specific missing-input tests; synthetic public examples;
redaction and malformed inputs; outside-checkout CLI walkthrough; existing reader
and registrar compatibility; real Compose rendering on versions 2.38.2 and 5.6.0;
no env-file/secret reads including the new Gateway file-key boundary. Run Ruff and
the type ratchet. Update the existing PR, retain its single release-target bump
against the reconciled base, and require full final-code CI before ready-for-review.
Never merge, deploy, modify live configuration or touch other sessions' worktrees.

Also verify incomplete examples (including missing retention/grid inputs) return
safe actionable diagnostics, not crashes. Check only the new generic quick-start,
profile reference and public examples for real agency names; do not ban legitimate
adapter identifiers, historical plans or legacy operational documentation.

Independent Claude plan review completed 2026-11-18. Clarifications above address
removal sites, the five-field neutral allowlist, closed provider-default audit,
default-review evidence and legacy scope boundaries. Its claim that the initial
draft omitted removal of `legacy` was inaccurate; that requirement is now also
spelled out at implementation symbols. The report remains local at
`/private/tmp/sapphire-generic-profile-plan-review.txt`. Orchestrator sets READY;
no unresolved material owner decision remains. Implementation may proceed.

## Implementation evidence

G1/G2 are implemented. Generic examples, external-file selection and uniform
validation replace the new agency catalogue. The five neutral defaults retain
their typed source of truth; missing retention is named, and initial incomplete
profiles can show omitted neutral defaults without fabricating a complete config.
Supplied invalid values never receive default provenance. Actual runtime readers,
legacy recipes, Gateway key loading and hydrological-grid activation remain intact.

One independent Claude implementation review completed with no blockers. Its two
non-blocking usability/test findings were addressed: named retention diagnostics
and explicit evidence that inherited source defaults remain unendorsed. The full
report stays local at `/private/tmp/sapphire-generic-profile-implementation-review.txt`.
The standard build-secret examples were checked against the actual base Compose
contract and retained. No additional review loop was needed.

Validation before final CI: 663 related tests, 54 tool tests on Compose 2.38.2,
source type ratchet (344 <= 400), Ruff and a copied-template walkthrough outside
the checkout passed. The two small review corrections then passed 113 related
tests and focused types. Final-code CI remains the delivery gate; its result is
recorded in PR #544. No deployment, live configuration or credential changes.

```json
{"phases":[{"id":"G1","depends_on":[]},{"id":"G2","depends_on":["G1"]},{"id":"G3","depends_on":["G2"]}]}
```
