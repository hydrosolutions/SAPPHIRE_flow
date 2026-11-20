---
status: READY
---

# Accept unknown external training provenance for the Nepal MVP

Owner decision, 20 November: unknown training history and completion times must
be accepted for this MVP; do not wait for FI's future provenance release. FI's
opaque artifact contract does not require these dates. This changes SAPPHIRE's
import metadata policy, not the FI artifact or prediction contract.

1. Allow the three external training timestamps to be null together, explicitly
   selected with `training_provenance="unknown"` at import. Default `known`
   continues to require all three; reject mixed/contradictory declarations before
   writes. Persist the unknown reason and MVP acceptance in import provenance
   notes, retaining operator/import time, exact config/artifact hashes and scope.
2. Add an additive migration dropping NOT NULL on those three columns and an
   all-known/all-unknown constraint. No existing rows are rewritten. Downgrade
   refuses while unknown rows exist. Update store/protocol/domain/fake mappings;
   ordinary training continues to supply actual dates.
3. Skill read/API explicitly return `evaluated_on="unknown"` and null training
   bounds when exposure is unknown. Never classify these as independent periods.
   Existing known-period classification remains unchanged. Update API contract
   snapshots and relevant docs.
4. Verify unknown import/store/read, default refusal, contradictory inputs,
   unchanged config/hash/tenant/transaction guards, skill classification/API,
   migration upgrade/downgrade and nullable round trip. Run lint/types and full
   suite locally or CI after final code change; independent patch review; PR for
   owner merge. No release identity publication or live database edits here.

Radiation ingestion, real-input qualification and the separate gateway timestamp
fix remain separate work. This patch removes provenance as an admission blocker;
it does not assert scientific skill or activate a model assignment.

Dependencies: steps 1–3 precede step 4. Rollout requires the migration and matching
reader code together; do not roll back to readers requiring dates once null rows
exist. Full FI per-stage provenance and modeller conformance tooling stay upstream.

## Independent review disposition

One independent Claude review completed (local report, $0.21). Incorporate a
`TrainingProvenance` known/unknown enum persisted on `model_artifacts`, default
known for existing rows and ordinary training. A shared domain invariant and DB
constraint bind the discriminator to the all-known/all-null timestamps. Keep the
existing three public date fields nullable with a validated record, avoiding a
larger training API rewrite. Explicit sites: `types/model.py`, `types/enums.py`,
`protocols/stores.py`, `store/model_artifact_store.py`, test fakes, import service
and flow, `services/skill_read.py`, `api/schemas.py`. Skill classification returns
unknown before date comparison. Validate declarations before parsing flow dates
and again in the service/store boundary. Generate the acceptance note server-side.
Check already-nullable event-review readers and update API docs/snapshots. Test
clean downgrade and refusal with unknown rows; never purge evidence for rollback.
The orchestrator marks this bounded plan READY after review; owner retains merge
and release decisions. No extra review loop or new FI dependency is needed.

## Patch review and compatibility

Independent Claude patch review found no correctness blockers. Clarified unknown
API documentation and normalized invalid flow declarations to ConfigurationError.
API contract 3.0 records the newly nullable skill response. No runtime model or FI
contract change. Local reports remain outside git. This branch includes the existing
PR 577 newline-only dependency; merge that first so the release-target check can
parse main. Full PR CI is required before owner merge.
