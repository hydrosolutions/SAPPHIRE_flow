# Model display labels for forecast clients

status: READY

## Scope

Expose registered presentation metadata as `model_display_name` and
`model_display_name_short` in station forecast listings, forecast details and
network overview forecast entries (standard and provisional-rating purposes,
including missing-output entries). Keep stable `model_id` as the identity.
Labels do not change forecast values, scientific provenance, QC, publication,
tenant/station visibility or model execution. No frontend or live deployment.

## Implementation

1. Add nullable `models.display_name_short` in migration 0092, SQLAlchemy metadata,
   ModelRecord and ModelRegistryEntry. An optional declared short name must be a
   trimmed, non-empty string of at most 32 characters; enforce at registration
   and in the database. Omitted names remain null. Persist this metadata through
   native and external-artifact registration and preserve declared full/short
   names across existing FI wrapping without altering FI's protocol.
2. Declare `CMAL SE` for `cmal_nepal_static_encoder_20261123`. Migration backfills
   only that existing model's null short name, preserving full names and all
   other model metadata. Re-registration retains existing conflict-do-nothing
   semantics; this task does not add a general metadata editing endpoint.
3. Add a batch model lookup to the existing ModelStore and fake, and a small API
   presentation helper. Load labels once for the visible response's model IDs,
   not once per station/forecast; never discover/import model code in API reads.
   Full-name fallback is the stable model ID if no usable stored name exists;
   short-name fallback is null, never a guessed acronym. Scope checks precede
   label loading, and no unreferenced model metadata is returned.
4. Extend forecast response builders and overview entries, including publication
   and provisional paths sharing the same response schemas. Pure builders may
   receive optional label mappings and fall back to IDs. Preserve auth, selected
   publication and error behavior. Missing overview forecasts also carry labels.
   Overview ETags must reflect label changes; update the map contract/generated
   schema/version according to the existing additive-contract convention.
5. Update model-onboarding instructions, API/overview contracts, domain/store
   specs and touchpoint maps. Document frontend selection as short name ?? full
   name ?? model ID, and short names as non-unique presentation text.

## Verification and gates

One independent Claude plan review before READY, then one implementation pass
and independent Claude patch review (owner-provided AGENTS policy). Test declared
names surviving discovery/FI wrapping, registration persistence, existing-CMAL
backfill and legacy nulls, standard/provisional listing/detail labels, missing
and available overview entries, scoped visibility, bounded label lookup and ETag
changes. Include real PostgreSQL migration/store and API tests. Existing model
IDs and forecast data must remain identical. Run Ruff, changed-module typing and
pre-push ratchet. Full final-source CI must pass before owner merge. Use current
release-target tooling, not package-version edits. No review reports in commits.

```json
{"phases":[{"id":"plan_review","depends_on":[]},{"id":"implementation","depends_on":["plan_review"]},{"id":"validation_and_patch_review","depends_on":["implementation"]},{"id":"owner_merge","depends_on":["validation_and_patch_review"]}]}
```

## Review decisions

Independent Claude review completed with no hard blockers. Clarifications applied:
short name is a class attribute on CmalNepalStaticEncoder20261123; preserve the
existing full name. Both discovery's `_assert_model_classification_declared` and
`carry_model_classification` preserve declared display names using the existing
sentinel approach. Persist through model_registry, onboard_model and model_import.
Both dataclasses default the optional short name to None. Migration 0092 adds a
nullable constrained column, backfills only the named CMAL row if present and
null, and drops its constraint/column on downgrade. Existing table-wide model
read/insert grants cover it. No privileges are expanded.

Batch lookup is `fetch_models_by_ids`, deduplicated and skipped for empty IDs.
Full labels are always strings (trim stored full names, fallback to ID); missing
short labels serialize explicitly as null. New short declarations are validated;
existing full-name registration rules remain unchanged. Model assignments,
rejected forecasts and HTML model pages are outside this change. Overview body
hashing already includes all forecast fields, with no cached body to invalidate.
Map OpenAPI additive version advances 3.3 to 3.4, generated via
`uv run python tools/generate_map_contract.py`; overview envelope version remains
unchanged for this additive metadata. No FI inputs, results or protocol change.
