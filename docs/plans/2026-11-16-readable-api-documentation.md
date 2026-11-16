---
status: READY
title: Readable API documentation with one maintained user guide
---

# Readable API documentation with one maintained user guide

Give colleagues a practical API guide they can read on the public GitHub
repository. Preserve implementation history separately and keep the generated
OpenAPI contract authoritative for request and response structure. This is a
documentation change, not an API redesign or a documentation-platform project.

Readiness: orchestrator accepted the revised plan on 2026-11-16 after two bounded
independent Claude review rounds; the second returned READY with no blockers.
Current normative rules remain active, examples use the existing file/test pattern,
and the implementation targets deployed behavior without the unmerged history API.
Full review reports remain local and are not part of the committed documentation.

## Scope and evidence

The inspected deployed guide `docs/spec/api-v1-review.md` has 607 lines mixing
consumer instructions, implementation decisions, test commands and historical
deployment statements. The generated `docs/spec/api-v1-map.openapi.json` has
15 operations and 9,089 lines; its 811 component-property declarations have no
descriptions and its operations have no embedded examples. These measurements
describe release v0.1.1136, not a permanent documentation target.

Keep the existing exporter (`tools/generate_map_contract.py`), contract version
gate and schema drift tests. No new documentation dependency, website, HTML
renderer, public API documentation endpoint, hosting or CI workflow in this pass.
GitHub already renders the public Markdown guide. A browsable generated reference
can be considered later if the guide does not meet a demonstrated need.

## Document ownership and destinations

| Location | Role and maintenance |
|---|---|
| `docs/api/README.md` | The one maintained human-facing API guide; linked from the repository README. |
| `docs/spec/api-v1-map.openapi.json` | Existing generated contract, at its existing path; never hand-edited. |
| `docs/spec/api-v1-implementation-history.md` | Dated historical decisions, qualification evidence and release-status passages extracted from the former guide; not current usage instructions. |
| `docs/spec/api-v1-review.md` | Concise current maintainer reference: contract maintenance rules and technical caveats, with links to the guide and history. |
| `docs/api/examples/*.json` | A few complete synthetic response examples, validated against the generated contract and linked from the guide. |

Classify the existing sections before moving them: current user instructions go
to the guide; current maintainer rules stay in the existing reference; historical
release claims, decisions and qualification evidence go to the dated history.
Do not mark still-current D14 versioning rules, authorization contracts or known
error behavior as obsolete. Where an existing technical specification or schema
source already owns a rule, link to it instead of creating another maintained copy.
Preserve historical decision references and qualifications with an explicit banner
that historical deployment claims are not current availability. Future history
stays in existing plans, PRs and commits rather than another ongoing changelog.

Retain the old reference URL. Inventory inbound anchors before editing; preserve
needed legacy headings with short links to the canonical destination when their
content moves, so externally shared URLs remain useful. Add one maintenance sentence
pointing user-facing semantics to the guide and field details to schema sources.
Current usage instructions
must appear only in the user guide, not duplicated in the maintainer reference.

Existing focused technical specifications, such as `docs/spec/network-overview.md`
and the forecast snapshot specification, remain authoritative for their detailed
semantics. Link to them; do not reproduce their full content in the user guide.

## User guide

Use the title **SAPPHIRE Flow API guide** and this short task-based structure:

1. **Connect and make your first request.** API base URL, Bearer key, dashboard
   email code versus reusable API key, a station-list command and compact output.
2. **Find stations.** Names, codes, UUIDs, configured status, pagination and filters.
3. **Get forecasts.** Overview versus list/detail, both data-use categories,
   category-correct detail URLs and optional publication behavior.
4. **Get observations and discharge history.** Native observations, latest reads,
   and forecast-specific daily rating input history. Shared discharge history is
   explicitly not yet deployed in the initial documented baseline.
5. **Interpret the data.** Units, issue/weather/valid times, null versus zero,
   status versus freshness, QC versus input quality, source and provisional labels.
6. **Resolve common errors.** Short status/action table, with route-specific
   validation differences retained rather than falsely standardized.

Start with an endpoint chooser, not implementation versioning or plan numbers.
Prioritize everyday reads; link to the generated reference and existing technical
specs for QC rules, skill, evaluation and other specialist operations. State that
the exported contract covers the supported dashboard/read subset, not every
internal/admin route in the application.

State required roles beside examples: provisional-rating reads and rating-input
history require reviewer/admin access in the deployed baseline. A consumer key
cannot use them. Keep optional, disabled-by-default Swiss routes in their existing
technical references, linked from the guide rather than presented as universally
available agency-neutral routes.

Each main task gets one copyable curl example using an environment-variable key,
one small valid synthetic response or clearly labelled excerpt, and a few sentences
explaining the fields that matter. Include the recently requested code/name/status
station table. Use separate series examples where ordinary and provisional data
differ. Explain that operational station status does not establish live sensor
availability or forecast delivery. Avoid hard-coded roster counts and statements
such as "today all scores are in-sample" in the general contract.

Use an `API_BASE` placeholder supplied by the deployment administrator; do not
introduce server addresses, recipient names or tenant identifiers into public docs.
Keep one small dated compatibility note for the verified v0.1.1136 deployment and
its contract 2.11 behavior, separate from general agency-neutral instructions.
Describe any new contract metadata version separately; do not imply that a new
package is deployed. Do not create parallel Swiss and Nepal guides. Label optional
or not-yet-deployed features explicitly. Shared instructions should link to a
specific documentation commit/tag compatible with the server, not assume the
latest main branch describes its deployment.

## Schema readability without changing behavior

Add concise property descriptions at the existing Pydantic boundary models and
operation/parameter descriptions in the current exporter where it already owns
them. Regenerate the JSON using the existing command. No separate metadata catalog
or new abstraction for documentation.

Cover the everyday station, observation, overview, forecast and history fields
first. Explain identifiers, units, time meanings, nullability, provenance and the
important category/freshness distinctions. Do not try to annotate every internal
component in this patch. Use clear operation summaries and consistent reader-facing
tags such as Stations, Observations and Forecasts; preserve operation IDs and
component identities to avoid unnecessary client churn.

Add a few complete synthetic JSON files under `docs/api/examples/`, following the
existing `docs/spec/swiss-overview.example.json` precedent. Cover station listing,
ordinary and provisional forecast variants, and observations. Validate them with
the existing jsonschema test pattern; do not add exporter example-injection logic.
Link to canonical full examples from the guide. Mark shortened excerpts clearly.

Only add metadata: descriptions via existing Pydantic `Field(description=...)`
without changing defaults/constraints/aliases, plus summaries/descriptions/tags in
the current exporter. Preserve all operation IDs and component identities. Add no
runtime validation, enum restriction, required field or default change.

The existing gate requires a contract-version increase for documentation metadata.
Use the next minor contract version (2.11 to 2.12 on this baseline); describe it as
metadata-only. If the discharge-history patch lands first, rebase and select the
next unused minor version instead. Do not change the gate. Package release versions
remain separate and do not establish deployment status.

## Migration and coordination

Start in an isolated clone of current main, documenting the verified deployed
v0.1.1136 behavior explicitly. The discharge-history patch is unmerged and excluded
from this implementation: do not copy its unreviewed code/schema or edit its clone.
Fold its guide into the canonical guide in that patch or a follow-up after it lands;
retain an explicit not-deployed notice until deployment is verified. Recheck main
before the documentation PR to resolve any schema version collision without losing
either change.

Update the repository README's task table and active documentation links. For
references to old guide anchors, direct consumer links to the new guide and
historical plan references to the preserved history anchors. Repair relative
links affected by moved sections. Do not broadly rewrite archived plans or unrelated
documentation. Existing externally shared old URLs retain a useful pointer.

## Verification and completion

- Build the generated schema with the existing exporter; run its drift/version
  checks and relevant API contract tests. Documentation-only metadata changes must
  leave runtime schemas, auth, defaults and endpoint behavior unchanged.
- Validate the new example files against exported schemas using existing jsonschema
  tests. Run `tests/unit/api/test_map_contract.py` and the version gate against the
  base SHA. Add no general-purpose documentation test framework.
- Compare base and regenerated contracts once with permitted documentation metadata
  (`description`, `summary`, `tags`) and `info.version` removed; expect no
  structural/validation/security difference.
  Check operation IDs separately. This is a one-off verification, not new tooling.
- Check Markdown links, anchors and code blocks manually before the PR; check the
  public GitHub-rendered guide once pushed. Do not claim an unpushed guide is already
  publicly accessible. Access checks use no personal key or private response fixture.
- Run read-only example requests against the documented deployment where authorized;
  use synthetic test responses for variants not present there. Never put tokens,
  recipient details or restricted observations in docs or test fixtures.
- Confirm station listing, both forecast categories and correct detail lookup are
  possible from the guide alone. Explain seven-day history availability honestly.
- Review a section-by-section content-move diff: current normative rules retained,
  historical decisions preserved, legacy anchors useful, and only one active guide
  containing usage instructions.
- Independent review before implementation and patch review under the applicable
  repository policy. Use an isolated branch; owner approves/merges the PR. Run
  checks required by the actual code/tooling changes, including the full suite
  after final code changes locally or in CI before merge.

```json
{"phases":[{"id":"independent-plan-review","depends_on":[]},{"id":"guide-and-history-move","depends_on":["independent-plan-review"]},{"id":"schema-descriptions-and-examples","depends_on":["guide-and-history-move"]},{"id":"validation-and-patch-review","depends_on":["schema-descriptions-and-examples"]},{"id":"owner-merge","depends_on":["validation-and-patch-review"]}]}
```

Completion means a public, readable guide link suitable for colleagues, retained
implementation history, a richer but structurally unchanged generated contract,
and a concise maintenance note: update user-facing semantics in the guide and
field/operation details at their existing schema source, then regenerate.
