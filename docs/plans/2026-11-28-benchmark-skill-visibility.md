# Scored benchmark visibility without operational activation

status: READY

Owner authorized API fixes after the BAFU model comparison/activation. Track #677;
reuse existing readers for all deployments. This milestone addresses the 34 scored
BAFU persistence stations excluded by model-assignment filters. Overview parity
(comparator, observation policy, approved roster) follows separately.

## Contract

- Station authorization and reviewer role remain at the existing route boundary.
- `skill-options` discovers published eligible standard discharge skill artifacts
  for the authorized station, whether or not the model is actively assigned.
  Preserve model filtering, generation/version selection, evaluation-time checks,
  artifact/model identity join and the 256-evaluation bound. Current forecasting
  status must require BOTH an active model assignment and active artifact.
- `skill` without `artifact_id` keeps current operational assignment/artifact
  selection. With an explicit artifact, resolve its model identity (or require it
  match supplied model_id) and return only eligible published station/model/artifact
  scores. No artifact bytes, training, assignment or promotion writes.
- `skill-diagrams` accepts the same scored artifacts. Unassigned artifacts are
  eligible only with matching published station/model/artifact scalar evidence.
  Preserve exact generation/period matching and response bounds. Do not disclose
  another station's artifact training metadata when no eligible local score exists.
- No fallback to arbitrary/latest candidates; no new route or query flag.

## Work

1. Update catalogue, explicit scalar read, and diagram admission. Preserve default
   operational behavior and protected-data/generation semantics.
2. Tests: unassigned benchmark discovered/read explicitly; defaults unchanged;
   inactive artifact labelled accurately; foreign station/artifact no disclosure;
   wrong model, unpublished/future/obsolete evidence excluded; existing limits.
3. Update API consumer docs and relevant touchpoint map. Independent patch review,
   focused tests/lint/types, full suite in CI before owner merge. No deployment or
   stable publication bundled with this code change.

Dependency graph: {"reader_fix": [], "tests_docs": ["reader_fix"],
"review_ci": ["tests_docs"]}

Independent plan review completed; disposition below.

## Independent review disposition

Claude review completed 28 November. Clarifications incorporated before READY:
- For newly discoverable **unassigned** models, allow artifact statuses
  `pending_approval`, `active`, `superseded`; hide `training` and `rejected`.
  Existing assigned-model candidate review eligibility remains unchanged.
- Standard means the existing ordinary skill store and all its skill-source
  categories (including transfer validation), not protected forecast data-use
  categories. Require actual published scores at this station; do not restrict
  artifact scope to station-only because group and transferred artifacts already
  have legitimate station-specific evaluations. No new artifact byte access.
- Catalogue without model_id intentionally includes scored unassigned benchmarks.
  The 256-evaluation bound applies to the full eligible result and may return the
  existing 503 asking the caller to select model_id. No silent truncation.

Orchestrator marks READY; implementation authorized by owner. No release/merge.
