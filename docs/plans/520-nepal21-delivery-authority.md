---
status: READY
created: 2026-10-12
revised: 2026-10-12
title: Nepal21 executable DB-authority delivery replacement and protected conversion plan
scope: Implement deterministic code and synthetic tests for Nepal21 typed manifests, static-only gates, DB-authorized old6→new21 restricted delivery replacement, immutable declaration/origin/attribution catalog, QC publication, restricted read boundaries, and expiry-only protected conversion. Real package import, real delivery/QC write, live conversion, TEST forecast output, Gateway forcing, model assignment, training changes, forecasts, publication, alerts and deployment remain held.
risk: high
related: [143, 268, 300, 511, 519]
provisional_plan_number: 520
---

# Nepal21 executable DB-authority delivery replacement and protected conversion plan

## Status

The orchestrator sets this plan **READY for code and synthetic tests only** after independent Claude, current-base implementation, and science reviews of revision 18. Real package/history import, real QC, live conversion, remote DB/SSH operations, deployment, credentials, and activation are not released. Independent patch review and owner approval remain required.

Owner policy is unchanged:

- Use all 21 approved DHM station codes: `225`, `280`, `350`, `404.7`, `439.7`, `440`, `445`, `447`, `450`, `550.05`, `602`, `602.5`, `604.5`, `610`, `620`, `647`, `650`, `652`, `670`, `684`, `695`.
- Preserve old six station metadata exactly.
- Treat new15 package outlet metadata as candidate/provenance only, not gauge datum/BIPAD/live proof.
- Use latest declared validity-start precedence for rating conflicts.
- Keep equal-start non-equivalent ties held.
- Allow expiry-only protected conversion testing, not operational activation.
- Keep Plan519 TEST forecast/rejection output persistence refused.


## Round 1 patch review correction status (2026-10-13)

Independent round1 patch reviews returned CHANGES REQUIRED. The implementation now records correction evidence outside Git in the local session artifact correction matrix. Current code/doc state remains synthetic-only and unreleased. Corrections made in this pass include:

- Release authority hardening: writer release UPDATE grants removed; release authority fields are immutable; publication consumes the release through SQL authority and rechecks witness hash and expiry.
- Generation lifecycle hardening: runtime writer direct-active INSERT is refused; publication uses building→active transitions; published identity fields are frozen; deletion/truncation is guarded.
- Restricted row hardening: OLD and NEW delivery identity are checked; the fixture-user bypass is removed; Nepal21 INSERT must be RAW before QC.
- QC authority hardening: SQL completion requires delivery rows, evaluated non-RAW observations and complete attribution count; QC-complete observations are immutable.
- Lock/provenance hardening: Nepal21 helpers use the shared tenant advisory namespace; replacement recomputes predecessor semantic digest under lock before deletion.
- Conversion selector parity: SQL/Python selector checks include open-ended/current outsiders and Kathmandu local-date semantics; SQL NULL equivalence participates in tie detection.
- Validation repair: the protected readiness oracle no longer copies actual conversion output into expected rows, and a deliberate wrong-result regression test is present.
- Downgrade repair: migration 0072 restores the 0068 provisional discharge trigger body on downgrade.

Validation after these corrections is still partial until the final full-suite run after all code changes. Current focused evidence includes F05/F06/F07 SQL witness tests `11 passed, 40 deselected` and the fresh Plan520 migration authority suite `50 passed`. The earlier `plan520-f07-migration-suite-20261013.log` is not green evidence because `tee` masked pytest failures; the corrected run is `plan520-f07-migration-suite-fixed-20261013.log`.

## Fixed source facts

- DFL union: 353,585 distinct reported Q records, zero conflicting overlapping Q values.
- 450 uses old+new complementary files. 604.5 and 647 collapse exact duplicate rows with both origins. 447/670/684 are old-only. Remaining 15 are new-only.
- RT original declarations remain distinct for latest-start ranking. Do not collapse recurring same-function starts before precedence.
- Historical equal-start non-equivalent holds remain: 350 RT36/37 and 404.7 RT2/3.
- 602.5 same-table subset is equivalent function coverage; keep both origins.
- 20 raw invalid blocks stay visible for selection. 13 duplicate-point-only cases require explicit canonicalization approval and revalidation; seven decreasing blocks remain held.
- All21 latest global blocks are curve-only expired conversion candidates. Verified live-ready count is zero.

## Transaction and lock entrypoints

All protected paths enter through a new orchestration/store boundary that opens a real non-AUTOCOMMIT transaction and verifies READ COMMITTED before protected DML or row locks. A helper call on an AUTOCOMMIT connection is rejected.

Protected transaction entrypoints:

1. `run_dhm21_delivery_replacement_transaction`.
2. `run_dhm21_qc_transaction`.
3. `run_dhm21_conversion_append_transaction`.
4. `run_dhm21_permission_change_transaction`.
5. `run_nepal_static_import_transaction`.

Ordinary nonprotected Swiss/native curve/weather/Gateway writers remain compatible and do not need Nepal21 release ids. The protected path's table locks serialize them while held. Only rows carrying restricted delivery ids, rows linked into a published declaration/proof, protected lifecycle rows, and exact selected protected candidates are all-role guarded. Ordinary unlinked native curve inserts for the 21 stations remain allowed and cause later conversion HOLD when relevant.

### SECURITY DEFINER helpers

Add narrow SECURITY DEFINER lock/assert helpers owned by the migration owner. Every helper uses:

- `SET search_path = pg_catalog, public, pg_temp`.
- Explicit `public.<relation>` and `pg_catalog.<catalog>` names.
- Fixed relation OIDs resolved from schema-qualified names.
- No dynamic SQL and no arbitrary relation/scope arguments.
- PUBLIC EXECUTE revoked.
- EXECUTE granted only to specific runtime roles.
- Bootstrap convergence for function EXECUTE ACLs, not only table ACLs.
- Actual-role temp-shadow tests proving public relations/release facts/lock OIDs are authoritative.

Advisory keys:

- Tenant key exactly matches `tenant_lock_key`: `pg_advisory_xact_lock(0x53464C4B, int32(first 4 bytes of tenant UUID in big-endian signed form))`.
- Provisional key exactly matches 0068: `pg_advisory_xact_lock(hashtextextended('provisional-discharge:' || tenant_id::text, 0))`.

Master relation order after advisory locks:

1. `public.restricted_delivery_releases`
2. `public.restricted_delivery_generations`
3. `public.provisional_discharge_permissions`
4. `public.expired_rating_test_permissions`
5. `public.dhm_delivery_origins`
6. `public.dhm_origin_sets`
7. `public.dhm_origin_set_members`
8. `public.dhm_rating_declarations`
9. `public.dhm_delivery_observation_attributions`
10. `public.rating_curves`
11. `public.observations`

Helpers take the ordered subsequence they need, using `SHARE ROW EXCLUSIVE MODE` for relation locks:

- `sf_lock_dhm21_delivery_write(tenant_id, release_id)`: validates tenant/release scope, takes tenant and provisional advisory locks, then the full ordered relation list.
- `sf_lock_dhm21_conversion_append(tenant_id, generation_id)`: validates tenant/generation scope, takes tenant and provisional advisory locks, then this exact master-order subsequence:
  1. `public.restricted_delivery_generations`.
  2. `public.provisional_discharge_permissions`.
  3. `public.expired_rating_test_permissions`.
  4. `public.dhm_delivery_origins`.
  5. `public.dhm_origin_sets`.
  6. `public.dhm_origin_set_members`.
  7. `public.dhm_rating_declarations`.
  8. `public.dhm_delivery_observation_attributions`.
  9. `public.rating_curves`.
  10. `public.observations`.
- `sf_lock_dhm21_permission_change(tenant_id, generation_id)`: generation-scoped waiver changes; takes tenant and provisional advisory locks, then this exact master-order subsequence:
  1. `public.restricted_delivery_generations`.
  2. `public.provisional_discharge_permissions`.
  3. `public.expired_rating_test_permissions`.
  4. `public.dhm_delivery_origins`.
  5. `public.dhm_origin_sets`.
  6. `public.dhm_origin_set_members`.
  7. `public.dhm_rating_declarations`.
  8. `public.rating_curves`.
- `sf_lock_provisional_permission_disable(tenant_id)`: tenant-only no-generation branch for existing input-permission disable; tenant and provisional advisory locks, then `public.provisional_discharge_permissions` only.
- `sf_lock_nepal_static_guard(tenant_id)`: tenant advisory lock, then table-level `SHARE ROW EXCLUSIVE MODE` locks on `station_weather_sources`, `recap_gateway_polygon_bindings`, `basin_versions`, `basins`, `model_assignments`, `group_model_assignments`, `model_artifact_basin_versions`. These global table costs are accepted for the guarded static command.

One tenant per guarded transaction. A future multi-tenant command must acquire all tenant advisory keys before any relation lock; this slice does not implement multi-tenant writes.

### Direct guard and 0068 successor behavior

Add statement-level or first-action nonwaiting assertions before old row-level 0068 locking behavior.

- Successor migration does not edit 0068 in place.
- The successor provisional insert function first classifies scope by nonwaiting reads: station in Nepal21 roster plus active generation means Nepal21 protected scope. Missing generation for a Nepal21-protected station is HOLD/refusal, never legacy fallback.
- Existing authorized non-Nepal/no-generation Plan519 scope keeps old measured-QC/proof/range/permission/selection behavior and old newest-curve check.
- Protected Nepal21 scope requires prelock assertions, DB waiver, catalog/native selector and `NEW.rating_curve_id = selected usable_rating_curve_id` when a usable curve is expected.
- It replaces the incompatible `ORDER BY valid_from DESC, version DESC LIMIT 1` winner check only for protected Nepal21 scope.
- It preserves 0068 measured-QC, proof, future-date, math, range, permission and snapshot protections.
- Statement-level assertions check backend pid, database oid, granted locks, actual public relation OIDs, accepted modes and both advisory key encodings. They do not use caller GUCs as authority.
- Per-row checks enforce tenant/release/station/delivery/QC facts.
- Missing prelocks raise fail-closed immediately; guards do not acquire earlier-order locks after implicit DML locks.
- Deadlock/timeout is reported with finite lock waits and no automatic retry.
- Disable is a serialized cutoff: no append after disable commits; an append already linearized first can finish.

Required integration points:

- PgProvisionalDischargeStore uses the protected transaction entrypoint and no longer locks `rating_curves` before the protocol.
- Existing input-permission disable uses tenant-only disable entrypoint.
- PgRatingCurveStore remains ordinary for nonprotected callers. Selected/linked/proof curve UPDATE/DELETE/tag-clearing is guarded by OLD and NEW row identity/catalog membership/delivery tag checks. Unlinked native-only inserts remain possible and cause unmatched-candidate HOLD.
- Station-weather-source and Gateway binding writers remain ordinary outside Nepal21. During static import, table locks serialize first-binding phantoms and active-consumer recheck.

## Owner release and generation state

### Owner release anchor

Create `restricted_delivery_releases` as owner-only authorization. Delivery writer cannot insert/update/delete it.

Columns:

- `id uuid primary key`.
- `tenant_id uuid not null references tenants(id)`.
- `network text not null check (network = 'dhm')`.
- `cohort text not null check (cohort = 'nepal21')`.
- `manifest_sha256 text not null check 64 lowercase hex`.
- `station_set_sha256 text not null check 64 lowercase hex` over sorted exact 21 station codes.
- `canonicalization_policy_sha256 text null check 64 lowercase hex when present`; null means no duplicate-point canonicalization is authorized for this release.
- `expected_predecessor_delivery_id text not null` = `dhm-barkhk-2026-09-08` for first release.
- `expected_predecessor_semantic_sha256 text not null check 64 lowercase hex` from shared predecessor codec, not preimage artifact bytes.
- `allowed_new_delivery_id text not null` = `dhm-nepal21-<manifest_sha256[0:16]>`.
- `expires_at timestamptz null`.
- `status text not null check in ('authorized','used','revoked')`.
- `created_at timestamptz not null`, `used_at timestamptz null`, `created_by_run_id uuid null`, `used_by_run_id uuid null`.

Constraints:

- Unique active authorization on `(tenant_id, network, cohort, manifest_sha256)` where `status='authorized'`.
- Unique `(tenant_id, network, cohort, allowed_new_delivery_id)`.

Owner preparation is two stages:

1. Bounded snapshot-read stage: a real `REPEATABLE READ READ ONLY` transaction takes the tenant advisory lock and reads old observations/curves to compute the predecessor semantic digest. It writes nothing and does not hold locks across human approval.
2. Owner-only write transaction inserts the authorized release row.

Replacement recomputes the predecessor digest under write locks and rejects drift. Publication atomically marks release `used`; revocation serializes with publication. A `used` release is accepted for QC/no-op replay but rejected for fresh build.

Owner-approved witness material:

Create `restricted_delivery_release_witnesses`:

- `release_id uuid not null references restricted_delivery_releases(id) on delete restrict`.
- `witness_kind text not null check in ('manifest_catalog','canonicalization_policy')`.
- `content_sha256 text not null check 64 lowercase hex`.
- `content_bytes bytea not null` containing the exact supplied canonical bytes.
- `created_at timestamptz not null`.
- Primary key `(release_id, witness_kind)`.
- Check/trigger: for `manifest_catalog`, `content_sha256` equals `restricted_delivery_releases.manifest_sha256`; for `canonicalization_policy`, `content_sha256` equals `canonicalization_policy_sha256` and the release policy is non-null.

The owner release write verifies `sha256(content_bytes) = content_sha256` before storing. Witness bytes are immutable, protected from `tables.py` and broad SELECT, and DB-readable only to guarded publication/append functions. Hashes are over these exact bytes, not JSONB reserialization. SQL verifies the hash over stored exact bytes before parsing.

The `manifest_catalog` witness is the owner-approved complete catalog. It binds station scope, source scope, every raw RT declaration including invalid/tied entries, every expected raw declaration payload, and expected origin identities. Publication performs equality in both directions between this witness and persisted generation rows: no missing declarations, no extra declarations, no same-count/max-date substitutions, no omitted earlier invalid winning interval, and no digest-label-only proof. The witness contains actual raw payload bindings and source origin identities, not only counts, file names, latest dates or digest labels.

The `canonicalization_policy` witness, when present, binds exact declaration scope and exact duplicate-pair transform authority. A non-null policy hash does not authorize every duplicate-point block. Protected append uses the sealed verified catalog/policy authority; it does not look up external files.


Canonicalization authority:

- `canonicalization_policy_sha256` is the only DB authority for duplicate-point canonicalization in this release.
- Null policy means `canonicalized_usable` is refused and duplicate-point-only declarations remain held.
- Non-null policy authorizes only exact duplicate-pair removal for declarations in the release scope named by the policy; it does not authorize removal of differing discharge values at one stage, smoothing, sorting repairs, or decreasing-curve repair.
- SQL publication validates the stored `canonicalization_policy` witness bytes, policy hash, declaration scope, raw duplicate-pair transform and converter validity. Writer-supplied approval UUID/hash alone is never authority.

### Restricted delivery generations

Create `restricted_delivery_generations`.

Columns:

- `id uuid primary key`.
- `release_id uuid null references restricted_delivery_releases(id) on delete restrict`.
- `tenant_id uuid not null references tenants(id)`.
- `network text not null`.
- `cohort text not null check in ('legacy_dhm6','nepal21')`.
- `delivery_id text not null unique`.
- `manifest_sha256 text null check 64 lowercase hex when present`.
- `legacy_evidence_sha256 text null check 64 lowercase hex when present`.
- `immutable_content_sha256 text null check 64 lowercase hex when present`.
- `preimage_export_sha256 text null check 64 lowercase hex when present`.
- `qc_state text not null check in ('not_required','pending','complete')`.
- `qc_policy_sha256 text null check 64 lowercase hex when present`.
- `qc_completion_sha256 text null check 64 lowercase hex when present`.
- `lifecycle_state text not null check in ('legacy_evidence','building','active_pending_qc','active_qc_complete','superseded')`.
- `visibility text not null check in ('restricted_history','private_preimage','public_none')`.
- `created_at timestamptz not null`, `activated_at timestamptz null`, `superseded_at timestamptz null`, `run_id uuid null`, `audit_id bigint null`.

Constraints:

- Unique `(id, tenant_id)`, `(id, tenant_id, cohort)`, `(id, tenant_id, cohort, delivery_id)`.
- Unique `(tenant_id, network, cohort, manifest_sha256)` where manifest is not null.
- Partial unique selected generation per `(tenant_id, network, cohort)` where lifecycle is `active_pending_qc` or `active_qc_complete`.
- `active_pending_qc` requires release id, `qc_state='pending'`, manifest hash, immutable content digest and preimage hash.
- `active_qc_complete` requires `qc_state='complete'`, QC policy hash, QC completion hash and immutable content digest.
- `legacy_dhm6` cannot claim nepal21 manifest semantics.

No CHWRR tenant means no generation row; lookup returns `not_onboarded`. If CHWRR exists but old rows are absent, lookup returns `present_empty` without a generation row until a release needs classification. Valid old rows are classified lazily inside replacement from predecessor semantic digest. Inconsistent old rows abort with named reason and no writes.

After publication, parent generation content/identity fields are frozen: release id, tenant, network, cohort, delivery id, manifest, predecessor/preimage hashes, immutable content digest and visibility cannot be retargeted or reopened as `building`. Generation DELETE/TRUNCATE is rejected. Only named lifecycle/QC transition functions may update enumerated QC/lifecycle columns.

Legacy reader behavior before replacement is preserved. Existing old restricted row/QC semantics continue until atomic switch.

QC behavior:

- Replacement creates selected `active_pending_qc`.
- QC writer reads selected pending generation and RAW rows.
- Accepted-history readers require `active_qc_complete` with expected policy hash plus existing per-row QC filters.
- Failed/interrupted QC rolls back row changes and leaves generation pending; failure evidence is audit-only. Retry is explicit and manual.
- Pre-QC no-op verifies immutable content/catalog/attribution digest and UUID/readback state.
- Post-QC no-op verifies immutable digest plus QC completion digest/policy and preserves UUIDs, QC, curves and audit identity.

## Canonical codecs and predecessor digest

Use existing finite-native float representation. Do not claim source-exact decimals after parser `_number` has converted to float. Raw file bytes/hashes preserve source evidence.

Codec domains:

- `sapphire-flow:dhm-predecessor:v1` for old DB row-set predecessor semantic digest.
- `sapphire-flow:dhm-rating-declaration:v1` for source-independent raw declaration content.
- `sapphire-flow:dhm-rating-function:v1` for effective mathematical function payload.
- `sapphire-flow:dhm-origin-set:v1` for persisted origin memberships.
- `sapphire-flow:dhm-generation-content:v1` for immutable generation payload.

Serialization:

- Python canonical JSON uses sorted keys, compact separators, UTF-8, finite rejection.
- SQL does not rely on `jsonb::text` for Python-byte equality. SQL authority is structural comparison of stored fields and native rows.
- Float-derived numbers are canonicalized from finite Python/PostgreSQL double values using one implemented routine with golden vectors for `12` vs `12.0`, zero, negative zero normalized to `0`, exponent inputs, rounding boundaries and nonfinite rejection.
- Native historical intervals use Kathmandu local date -> UTC instant via existing `nepal_day_start`; `to_local_date_inclusive` maps to next civil day exclusive. Include 1986-01-01 parity probes and adjacent inclusive end probes. Real release fails closed on Python/Postgres timezone disagreement.

Predecessor semantic digest:

- Shared routine used by owner snapshot-read preparation and writer comparison.
- Includes all current `observations` columns: id, station_id, timestamp, parameter, value, source, rating_curve_id, rating_curve_correction_version, delivery_id, qc_status, qc_flags, qc_rule_version, created_at.
- Includes all current `rating_curves` columns: id, station_id, version, valid_from, valid_to, points, interpolation, uploaded_by, delivery_id, rating_type_label, created_at.
- Row ordering: observations by `(station_id, timestamp, parameter, source, id)`; curves by `(station_id, version, id)`.
- Nulls are explicit JSON null; timestamps are UTC ISO strings with offset; JSON fields use canonical JSON; floats use the shared finite float rule.
- Detects QC/value/id/curve mutation. Distinct from private preimage artifact bytes.

Generation content digest member list includes release id, manifest hash, delivery id, preimage artifact hash, sorted station set, DFL logical records, origin records, origin sets, origin-set members, declaration rows, attribution rows, native curve payload bindings, and protected schema/codec version. `dhm_origin_sets` and `dhm_origin_set_members` are the single authoritative membership representation; there are no `dhm_declaration_origins` or `dhm_observation_origins` tables, locks, FKs or digest inputs. QC fields are excluded from immutable content and included only in QC completion digest.

QC completion digest member list is separate and versioned: generation id, QC policy hash, processed observation ids and natural keys, final QC status, QC flags, QC rule version for each processed row, completion timestamp and run id. Post-QC no-op replay verifies both immutable content digest and this QC completion digest before accepting no-op.

## Origins, declaration catalog and attribution

Create `dhm_delivery_origins`, protected from table browser and broad SELECT:

- `id uuid primary key`.
- `generation_id uuid not null`, `tenant_id uuid not null`, `cohort text not null`, composite FK `(generation_id, tenant_id, cohort) -> restricted_delivery_generations(id, tenant_id, cohort)`.
- Unique `(id, generation_id, tenant_id, origin_kind)` as origin-link FK target.
- `origin_kind text not null check in ('DFL','RT','GHT_HASH_ONLY','PREIMAGE','SYNTHETIC_TEST')`.
- `source_root_label text not null check in ('old','new','preimage','synthetic_test')`.
- `relative_path text not null`.
- `file_sha256 text not null check 64 lowercase hex`.
- `parser_version text not null`.
- `row_or_block_ordinal integer null`.
- `origin_payload_sha256 text not null check 64 lowercase hex`.

Conditional invariant: DFL and RT origins require exact source root/path/file hash plus non-null original row/block ordinal. GHT_HASH_ONLY uses its declared hash/presence ordinal policy and is never measurement/unit/datum proof.

Create `dhm_origin_sets`:

- `generation_id uuid not null`, `tenant_id uuid not null`, `origin_set_sha256 text not null check 64 lowercase hex`.
- Composite FK to generation.
- Primary key `(generation_id, tenant_id, origin_set_sha256)`.
- `member_count integer not null check member_count > 0`.
- `set_kind text not null check in ('DFL_RECORD','RT_DECLARATION','GHT_HASH_ONLY','PREIMAGE')`.

Create `dhm_origin_set_members`:

- `generation_id uuid not null`, `tenant_id uuid not null`, `origin_set_sha256 text not null`, `origin_id uuid not null`, `origin_kind text not null`, `member_ordinal integer not null`.
- FK `(generation_id, tenant_id, origin_set_sha256) -> dhm_origin_sets`.
- FK `(origin_id, generation_id, tenant_id, origin_kind) -> dhm_delivery_origins(id, generation_id, tenant_id, origin_kind)`.
- Unique `(generation_id, tenant_id, origin_set_sha256, origin_id)` and `(generation_id, tenant_id, origin_set_sha256, member_ordinal)`.

Create `dhm_rating_declarations` for all raw RT declarations:

- `id uuid primary key`.
- `generation_id uuid not null`, `tenant_id uuid not null`, `station_id uuid not null`, `cohort text not null` with composite same-scope constraints.
- Unique `(id, generation_id, tenant_id, station_id)` and `(id, generation_id, station_id)`.
- exact station code text.
- `from_local_date date not null`, `to_local_date_inclusive date not null`, `rating_type_label text not null`.
- `from_stage_m double precision not null`.
- `raw_declaration_payload_json jsonb not null`: original local dates, label, from_stage_m, delivered-order finite-float points including duplicates/decreasing sequence, parser/policy version.
- `declaration_content_sha256 text not null check 64 lowercase hex`, computed from raw declaration payload.
- `function_payload_json jsonb null`: effective normalized function points/interpolation/units/reference assumptions after approved raw->effective transformation; null for invalid shape.
- `function_equivalence_sha256 text null check 64 lowercase hex`; non-null only when function_payload_json is non-null.
- `native_curve_payload_json jsonb null`: normalized actual native curve payload/date/label binding for usable declarations.
- `inventory_semantic_sha256 text null` preserved as evidence.
- `shape_state text not null check in ('raw_usable','duplicate_point_needs_approval','canonicalized_usable','decreasing_invalid')`.
- `canonicalization_approval_id uuid null` and approval hash/provenance fields when duplicate-point canonicalization is used.
- `usable_rating_curve_id uuid null`, composite FK `(usable_rating_curve_id, station_id) -> rating_curves(id, station_id)`.
- `origin_set_sha256 text not null check 64 lowercase hex`, FK `(generation_id, tenant_id, origin_set_sha256) -> dhm_origin_sets`.

Constraints:

- Unique `(generation_id, station_id, declaration_content_sha256)`.
- Usable/canonicalized declarations require non-null function payload, function hash, native curve id and native payload. Invalid declarations require null native curve id; decreasing invalid keeps raw payload.
- Declaration-origin membership is represented by its origin_set. Publication/replay verifies that the origin_set kind is RT_DECLARATION and every member origin kind is RT.

Structural authority:

- Raw-to-effective guard is explicit. For `raw_usable`, SQL derives converter-normalized raw finite points from `raw_declaration_payload_json`, independently rejects duplicate stages and decreasing discharge under current linear converter rules, and requires `function_payload_json` points/interpolation to match that derived result. For `canonicalized_usable`, SQL requires a non-null release `canonicalization_policy_sha256`, verifies the declaration is in policy scope, applies only the approved exact duplicate-pair removal transform, reruns converter validity, and requires `function_payload_json` to match that approved transform; the raw payload remains unchanged. For `duplicate_point_needs_approval` and `decreasing_invalid`, `function_payload_json`, `function_equivalence_sha256`, `native_curve_payload_json` and `usable_rating_curve_id` must be null. Differing-Q same-stage removal and decreasing-curve repair are never authorized.
- Equivalence compares function_payload_json mathematical content only: effective points, interpolation, stage/discharge units and vertical-reference assumption. Approval identity is checked separately as provenance/authorization; it is not mathematical equivalence.
- For every usable/canonicalized declaration, function_payload_json points/interpolation must equal native_curve_payload_json points/interpolation and the actual `rating_curves.points`/`interpolation` after normalization.
- Native date/label fields must equal declaration original dates/label after Kathmandu half-open conversion, and `rating_curves.delivery_id` must equal the generation delivery id.
- SQL/function checks reject identical function blobs paired with different independently correct native blobs, changed date/label/from_stage, invalid declarations with invented effective/native payloads, and forged equivalence.

Create `dhm_delivery_observation_attributions`:

- `id uuid primary key`.
- `observation_id uuid not null`, `station_id uuid not null`, composite FK to observations `(id, station_id)` ON DELETE RESTRICT.
- `generation_id uuid not null`, `tenant_id uuid not null`, `cohort text not null`, `delivery_id text not null`, composite FK `(generation_id, tenant_id, cohort, delivery_id) -> restricted_delivery_generations(id, tenant_id, cohort, delivery_id)`.
- Unique `(id, generation_id, tenant_id)` as origin-link FK target.
- `selected_declaration_id uuid null`, composite FK `(selected_declaration_id, generation_id, station_id) -> dhm_rating_declarations(id, generation_id, station_id)`.
- `rating_curve_id uuid null`, composite FK `(rating_curve_id, station_id) -> rating_curves(id, station_id)`.
- `manifest_sha256 text not null check 64 lowercase hex`.
- exact station code text, `local_day date not null`, `unit text not null`, `parameter text not null`, `source text not null`.
- `status text not null check in ('unique_valid_curve','missing_curve','invalid_curve_shape','equal_start_tie','held_unresolved','equivalent_redundant_collapsed')`.
- `covering_declarations_sha256 text not null check 64 lowercase hex`, `origin_set_sha256 text not null check 64 lowercase hex`, `held_reason_code text null`.
- FK `(generation_id, tenant_id, origin_set_sha256) -> dhm_origin_sets`.

Constraints:

- Unique `(observation_id)`.
- Unique `(generation_id, station_id, local_day, parameter, source)`.
- Consistency trigger checks copied parameter/source/local day/delivery id against referenced observation and generation.
- `unique_valid_curve`: selected declaration and rating curve required; same generation/station; usable/canonicalized shape; `observations.rating_curve_id == dhm_delivery_observation_attributions.rating_curve_id == dhm_rating_declarations.usable_rating_curve_id`; the selected curve delivery id equals the generation delivery id.
- `equivalent_redundant_collapsed`: one selected usable curve plus all equivalent same-max-start declarations in covering digest; the same three-pointer curve equality and curve delivery-id equality are required.
- `missing_curve`: no selected declaration and `observations.rating_curve_id` and attribution `rating_curve_id` are both NULL.
- `invalid_curve_shape`, `equal_start_tie`, `held_unresolved`: observation and attribution curve IDs are both NULL; covering digest and held reason required.
- DB constraints/triggers enforce this with actual-role tests, including a mismatch using different valid same-station curves, not only cross-station FK violations.
- Observation origin membership is represented by origin_set. Publication/replay verifies set kind DFL_RECORD and member origins kind DFL.

Origin-set invariant:

- `dhm_origin_sets` plus `dhm_origin_set_members` are the single authoritative membership representation for declarations and attributions. There are no parallel declaration-origin or observation-origin link tables.
- For every declaration/attribution, the referenced origin_set members are the record's exact persisted origins.
- Member ordering is deterministic. Publication sorts members by canonical full origin identity `(origin_kind, source_root_label, relative_path, file_sha256, parser_version, row_or_block_ordinal, origin_payload_sha256, origin_id)` and assigns `member_ordinal` as a contiguous zero-based sequence in that order. Input order does not define identity.
- Digest is recomputed from the sorted actual members using the same full origin identity. `parser_version` is part of origin identity. `origin_payload_sha256` binds the parsed row/block payload under that parser version; it does not replace the explicit parser_version field.
- Empty required sets, missing members, extra members, swapped members, wrong kind, duplicate ordinals, non-contiguous ordinals and cross-generation links reject publication and replay. Permuting the same members produces the same set digest and record identity.
- Deterministic source-origin UUIDs are acceptable when derived from reproducible source hashes and origin identity. Source-manifest determinism is distinct from persisted-generation identity: no-op replay reuses saved observation/curve/origin IDs and verifies them; predecessor digest retains all actual old IDs. Do not globally remove UUIDs from digests, and do not reduce DFL lineage to file-only provenance.

ON DELETE is RESTRICT. This release supports one old6→new21 transition. Later destructive supersession remains separate.

## Build, publication and replacement order

There is one population window.

- Generation starts as `building` after release verification.
- Only `sapphire_dhm21_delivery_writer` can populate payload tables for that building generation through the protected transaction entrypoint.
- Publication verifies manifest witness bytes/hash, policy witness bytes/hash when present, release facts, station set, predecessor semantic digest, preimage artifact digest, origin records, origin-set memberships, declarations, raw/function/native bindings, attributions, and immutable content digest.
- Publication sets `immutable_content_sha256`, transitions to `active_pending_qc`, and marks release `used` in one transaction.
- After publication, INSERT/UPDATE/DELETE/TRUNCATE are rejected for generation payload tables: origins, origin sets, origin members, declarations and attributions. Parent generation identity/content is frozen as above. QC fields change only through QC transition.
- Deferred final-transaction-state invariant: a transaction in the new release-bound old6→new21 transition may not commit a new Nepal21 `building` generation, partial replacement, or old-row deletion without a matching `active_pending_qc` publication and the corresponding release marked `used`. Deleting the building row and committing old-row deletion is refused. Rollback remains allowed. This invariant is scoped only to the one new writer old6→new21 transition. Authorized legacy `sapphire_operator` old6→old6 replacement remains tested and allowed before the switch under the old path; after selected new generation exists, old-tag resurrection or mutation is refused by selected generation guards. Caller GUCs cannot classify a transaction as legacy.
- Publication SQL/constraints validate scope, kind, counts, contiguous ordinals, raw→effective→native validity, origin-set equality and parent/child consistency in the guarded DB transition, not only in Python prechecks.

Replacement flow:

1. Acquire real transaction and `sf_lock_dhm21_delivery_write`.
2. Resolve CHWRR tenant. If absent, return `not_onboarded`; no generation row.
3. If same active generation exists, run replay no-op checks.
4. Compute predecessor semantic digest over old DB rows.
5. Verify owner release row.
6. Verify no unrelated/manual/live natural-key collisions and no dependency blockers.
7. Create private full preimage with every old observation/curve column needed for exact restore: values, ids, station ids, timestamps, parameter, source, delivery id, created_at, uploaded_by/correction fields where present, QC fields, rating payloads and curve versions.
8. Create preimage with private permissions and collision-safe exclusive creation. fsync file bytes, close, fsync containing directory, reopen/reread and verify artifact SHA. Failed export stops deletion and records orphan failure evidence.
9. Delete only owned old observations where `delivery_id='dhm-barkhk-2026-09-08'` and station is prior six.
10. Delete only owned old rating curves for that old delivery and prior six after observation deletion and dependency checks.
11. Insert new usable curves, observations, declarations, origins, origin sets, memberships and attributions.
12. Publish and read back counts/digests/ACL classification before commit.

Observation natural-key uniqueness is satisfied because old owned rows are deleted before overlapping new rows are inserted in the same transaction. Unrelated rows are untouched.

Post-commit restore command is out of scope. Synthetic exact reconstruction oracle is required. Never delete immutable catalog/provenance as rollback or recovery.

## Roles, guards and bootstrap

Role `sapphire_dhm21_delivery_writer`:

- Created/converged by bootstrap, disabled until credential release.
- SELECT only on safe release-check view/function and tenants lookup.
- DML only through guarded entrypoints needed for this slice.
- No static/weather/model/forecast activation authority.
- Narrow audit readback via safe function/view, not general audit SELECT.

0067 successor:

- Do not edit 0067.
- Existing `sapphire_operator` guard remains for old delivery.
- New role-specific guards cover `sapphire_dhm21_delivery_writer`.
- General restricted-delivery guards cover all roles for old/new restricted delivery ids and reject unauthorized `sapphire_worker`/ordinary modifications.
- QC successor guards allow only QC-column mutation on exact selected pending rows; immutable observation/curve/membership content remains protected.
- Tests use actual `session_user`, not only SET ROLE.

Role `sapphire_static_importer`:

- SELECT on guard inputs: station_weather_sources, assignments, groups, lineage/artifact-impact state, basin/Gateway resolver inputs.
- DML only on required station/basin/static tables and append-only static audit.
- No DML on station_weather_sources, weather/model activation, restricted delivery releases/generations/catalog/QC, observations, rating_curves or forecast tables.
- Static route contains zero `store_weather_source(...)` calls.
- Static guard checks active ERA5 `REANALYSIS` and IFS `FORECAST` consumers for absent→present, changed and removed resolver outcomes, then rechecks before commit under locks.

Bootstrap/ACL:

- Update `docker/bootstrap-roles.sql` and migration revokes.
- Exclude protected new relations before broad SELECT grants.
- Revoke stale table and column ACLs on upgrade.
- Converge function EXECUTE ACLs and stale role memberships.
- Expose only minimal safe predicate/view columns needed by ordinary readers.
- Tests: fresh install, existing-volume upgrade, bootstrap rerun, stale ACL/membership, table-browser classification.

## Expired conversion permission

Create `expired_rating_test_permissions`.

Columns:

- `id uuid primary key`.
- `tenant_id uuid not null`.
- `generation_id uuid not null`.
- `cohort text not null check (cohort='nepal21')`.
- Composite FK `(generation_id, tenant_id, cohort) -> restricted_delivery_generations(id, tenant_id, cohort)`.
- `permission_version integer not null`.
- `state text not null check in ('disabled','enabled')`.
- `owner_authorization_sha256 text not null check 64 lowercase hex`.
- `precedence_sha256 text not null check 64 lowercase hex`.
- `selection_manifest_sha256 text not null check 64 lowercase hex`.
- `enabled_at`, `disabled_at`, `enabled_by_run_id uuid`, `disabled_by_run_id uuid`.

Constraints/transitions:

- Unique `(tenant_id, generation_id, cohort, permission_version)`.
- Partial unique one enabled row per `(tenant_id, generation_id, cohort)`.
- Owner-only insert creates enabled version row.
- Owner-only update may change only `state` from enabled to disabled plus disabled metadata.
- Re-enable creates a new version row.
- Enablement requires referenced generation currently `active_qc_complete`.

Protected append requires active QC-complete generation, enabled input permission, enabled expiry permission, exact hashes, catalog winner, live binding proof, QC/range/unit/datum/feed/reference checks and lock protocol. TOML can only further disable; it is not SQL authority. Permission id/version and hashes are included in provisional content/fingerprint.

## QC publication

QC command uses selected `active_pending_qc` generation.

- Requires all21 `ExpectedQcPolicyPin`.
- Updates only exact generation RAW rows.
- Commits QC row changes and QC completion record atomically.
- On exception/interruption, transaction rolls back and generation remains pending. Failure evidence is audit-only and retry requires explicit command.
- Accepted-history readers require complete state and matching policy hash.

## Selector rules

- Historical attribution filters original declarations covering local day, finds maximal applicable start, collapses only structurally equivalent functions at that max start, rejects non-equivalent ties, then validates shape.
- Expired conversion uses all station native curves plus catalog reconciliation. Relevant native-only candidate means same station with `valid_from` at or after the catalog latest start, or equal to it. Unmatched newer/tied native candidates hold conversion.
- Invalid latest declaration is hold/reject, never fallback.

Golden vectors cover numeric spellings, zero/negative zero, finite rejection, permuted origins, repeated points, approved duplicate-point canonicalization, raw duplicate order preservation, decreasing invalid declarations, identical function payload with different native payload refusal, changed date/label/from_stage, 602.5 unequal intervals but same function, recurring same-function starts, forged same-function hash with different native points, 350/404.7 equal-start ties, Kathmandu 1986 date parity and predecessor drift.

## Reader closure

- Direct station observation API hides old, new, unknown and pending-QC restricted rows from non-admin station-authorized users.
- `tables.py` excludes protected new relations from count/list/detail/page.
- Delivery/QC/readiness: QC writer reads pending generation; accepted-history readers use active QC-complete generation. Old six behavior is preserved until switch.
- Training/hindcast/operational inputs may use accepted historical reported Q only via existing authority and QC-complete generation. EXPIRED_RATING_TEST/provisional Q is always excluded.
- Alert/review/snapshot/export/tools obey restricted predicate and provisional/test exclusions.
- Cross-tenant HTTP and actual-role DB tests required.

## Static route and package gates

Static-only route:

- Uses `sapphire_static_importer`.
- Requires corrected package pin for new `package_id` and HRU `nepal21_20261012`.
- Does not call current weather-binding path and contains zero activation calls.
- Preserves exact old six station metadata and new15 candidate provenance.
- Runs ERA5+IFS absent→present/changed/removed guard under `sf_lock_nepal_static_guard` and rechecks before commit.

## Validation and task graph

Tasks:

1. Migrations, roles, bootstrap, lock helpers, assertion guards.
2. Canonical codecs, raw/function/native payload bindings, predecessor digest, typed manifests, exact roster/new15 provenance and validate-only mode.
3. Static-only route with ERA5/IFS active-consumer guard.
4. Replacement writer, preimage, old-row deletion, dry-run-write rollback.
5. Origin memberships, attribution, QC publication and reader closure.
6. Protected expired conversion and waiver permission.
7. Docs and final validation.

Implementation ledger (current partial status, keep YAML READY):

- T1/T2: implemented additional SQL publication authority for synthetic manifest/catalog/policy witnesses. `sf_assert_restricted_publication_catalog(uuid)` now runs inside the guarded `building` → `active_pending_qc` SQL transition and checks exact Nepal21 roster scope, optional policy scope, release/generation parent seal equality, manifest declaration equality against persisted declaration rows including raw payload JSON and shape label, raw/effective/native pointer consistency, selected-attribution three-pointer equality (`declaration.usable_rating_curve_id`, attribution curve, observation curve/delivery/origin), and origin-set membership digest/count equality. It is `SECURITY DEFINER` for catalog reads but authorizes by `session_user`: the real writer session or the actual table owner only. It no longer has a test-username bypass. Behavioral tests cover SET ROLE direct SQL publication attempts as limited evidence and true `SET SESSION AUTHORIZATION sapphire_dhm21_delivery_writer` direct SQL publication attempts with asserted `session_user` and `current_user` for omitted newest invalid declaration, omitted earlier invalid winner, same-start tie omission, same-count raw substitution, unauthorized policy scope, origin member mismatch, parent/seal mismatch, selected-attribution three-pointer mismatch, and positive complete synthetic publication; they also cover copied hash with changed witness bytes and an unauthorized `session_user` call where the definer function runs with owner `current_user` but still refuses. Observed RED before the SQL guard was added: `/Users/bea/.prime/agent/session-artifacts/01a0f2cb-7ac1-73fa-9c0c-187e270a3fa7/sub-e1ae0c97/plan520-t2-witness-red-before-fix-rerun2.log` (`6 failed, 2 passed`; the six negative publication cases were accepted by current behavior). GREEN after current fixes: `tests/integration/db/test_migration_nepal21_delivery_authority.py` passed (`26 passed`); targeted pyright for the changed service and migration test is clean. Still needs final review against full lock/role/ACL oracle and actual-role concurrency/COPY obligations.
- T3: implemented for synthetic static-only package execution. `run_nepal_static_import_transaction` now validates the package/HRU pins, enters the SQL static lock, writes through the canonical basin/static importer, preserves unrelated old-six station metadata, does not write station-weather activation rows, and compares active ERA5 (`era5_land`/reanalysis) plus IFS (`ifs_ecmwf`/forecast) resolver snapshots before and after the proposed import under the lock. It refuses absent-to-present, removed, and changed resolver outcomes and rolls back its savepoint on refusal. Behavioral tests: `tests/integration/services/test_nepal21_static_import.py` and `tests/unit/cli/test_import_basin_package_nepal21.py` cover pin validation, old station preservation, new station/static candidate persistence, no weather activation, and resolver added/removed/changed refusal. Earlier no-op defect was found by inspection/root review, not by an observed RED run. Added SQL static lock serialization proof for concurrent importers. Remaining T3 obligation: actual-role/static-importer ACL proof.
- T4: implemented synthetic dry-run/replay coverage for the replacement writer. `run_dhm21_delivery_replacement_dry_run` executes the guarded replacement inside a savepoint, verifies proposed persisted delivery count, rolls back DB writes, and returns the release-bound preimage digest. The migration integration test proves dry-run leaves old rows in place and no new delivery rows, then replays the same build/payload as the real replacement and publishes it with matching preimage files. Restore is still an oracle/readback check, not an executable restore command.
- T5: corrected the QC runner after root rejected the first blanket-pass implementation. `run_dhm21_qc_transaction` now requires an `ExpectedQcPolicyPin`, takes protected locks, loads exact attributed RAW observations, evaluates them through the existing `Stage1QualityChecker` and injected synthetic `QcRuleSet`, refuses missing/non-evaluating station policy before mutation, preserves failed/suspect outcomes and flags, and completes atomically. `complete_generation_qc` now stores the exact QC policy pin in `qc_policy_sha256` and a separate `qc_completion_sha256` over row IDs/natural keys/status/flags/rule versions/time/run. Migration 0072 and metadata include `qc_completion_sha256`. Tests prove missing station policy leaves rows RAW, an out-of-range row becomes `qc_failed` with a range flag, policy hash remains the input pin, completion digest is separate, and the prior replacement/QC path reaches `active_qc_complete`. The old 26-pass result is insufficient QC contract evidence and is superseded by the corrected targeted runs. Full reader closure review remains.
- T6: expanded protected expired-conversion integration. `PgProvisionalDischargeStore` now requires Nepal21 provisional content to include an immutable `nepal21_expired_permission` fingerprint with permission id/version, generation id, owner authorization, precedence, selection manifest, QC policy pin, QC completion digest, and selected rating curve id. Store and SQL trigger paths verify the selected curve is in the active QC-complete catalog. Synthetic tests prove missing fingerprint is refused through the store and direct SQL/COPY, correct fingerprint can append, the Nepal21 selector refuses unmatched newer native candidates, equal-start non-equivalent declaration ties, and invalid latest declarations with no fallback; an equivalent same-start higher-version native curve no longer gets vetoed by the legacy newest-version ordering. `sf_lock_dhm21_conversion_append` serializes concurrent writers.
- T4: replay/no-op behavior was added for an already active generation. A second `run_dhm21_delivery_replacement_transaction` with matching release/build/payload validates generation identity, catalog and row counts, writes no new preimage, and leaves row counts unchanged.
- T7: partial; final full-suite rerun and independent patch reviews remain after final code changes.

Dependency graph:

```json
{
  "T1_migrations_roles_locks": [],
  "T2_codecs_manifests_validate_only": ["T1_migrations_roles_locks"],
  "T3_static_route_guard": ["T1_migrations_roles_locks", "T2_codecs_manifests_validate_only"],
  "T4_replacement_preimage_dry_run": ["T1_migrations_roles_locks", "T2_codecs_manifests_validate_only"],
  "T5_qc_publication_readers": ["T4_replacement_preimage_dry_run"],
  "T6_protected_conversion": ["T1_migrations_roles_locks", "T2_codecs_manifests_validate_only"],
  "T7_docs_validation": ["T3_static_route_guard", "T5_qc_publication_readers", "T6_protected_conversion"]
}
```

Validation before merge:

- targeted tests for all tasks;
- release-owned canonicalization policy tests for null, mismatched, exact duplicate-pair approved, differing-Q same-stage refused and decreasing-curve refused cases;
- SQL witness tests: omit latest invalid, omit tied candidate, omit earlier invalid winning interval, same-count/max-date substituted raw payload, out-of-policy declaration, forged witness bytes under copied hash, and positive complete synthetic catalog;
- deferred commit invariant tests for building commit, partial replacement commit, old-row deletion without publication, and rollback allowed;
- three-pointer curve equality tests for unique/equivalent attribution and NULL curve tests for held/missing/invalid/tied statuses;
- role/ACL/bootstrap fresh and upgrade tests;
- actual login/session_user tests;
- concurrency barrier tests for lock protocol;
- direct SQL/COPY negative tests;
- API/table/read-boundary tests;
- synthetic exact preimage reconstruction oracle;
- validate-only no DB mutation;
- dry-run-write executes real DB transaction and rolls back;
- write batches bounded: observations 5,000; attribution rows 5,000; rating curves/declarations 500 or fewer per statement;
- `uv run ruff format --check .`;
- `uv run ruff check .`;
- full test suite after final code change, locally or in CI. CI hold is not a waiver.

## Real-world held gates

- Corrected basin-static package with new package id and HRU `nepal21_20261012`.
- Package, delivery and QC pins.
- Duplicate-point canonicalization policy if used.
- Static/delivery credentials released by root.
- Live endpoint identity, station id, stage unit and vertical datum/reference proof.
- DB waiver enabled and protected input permission.
- Gateway forcing, model assignment, training changes, forecast targets, TEST forecast output, publication, alerts, deployment and post-commit restore remain out of scope.
