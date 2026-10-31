---
status: READY
---
# Swiss dashboard live-data last mile

## Scope and evidence
Owner approved continuing in an isolated clone, leaving the old dirty checkout untouched. Existing private tunnel is active under the three-reviewer, one-active-tab-each, hard30 requests/min/page exception; no fleet reads are admitted yet. Owner sees some live data; complete frontend acceptance is pending.

Existing one-station 168-hour snapshot:985 observations, seven SAPPHIRE models, BAFU reference,106950bytes/168ms loopback/no-store. Existing134-station1-hour snapshot:2270879bytes/11.27s/no compression. Neither measurement certifies concurrent/browser latency. Live runtime remains1082; this branch is based on current main,1089. No implicit whole-stack upgrade, migration, model run or publication-policy change.

## Contracts and invariants
- All station selection is authenticated and scope-filtered BEFORE counts, pagination and observation reads. Never query a fleet by looping over unbounded history windows.
- Preserve station/network identity, typed QC flags, latest missing records, unit-read holds/quarantine, delivery visibility restrictions and tenant publication gates. No latest-good fallback disguised as latest. No raw/unchecked reading described as QC-passed.
- Preserve existing full forecast-lab/v2 responses by default. Ordinary comparison forecasts do not become authoritative evaluation forecasts. No changes to Nepal publication/CMAL semantics, alerts, skill computation or scientific units.
- Live failure remains unavailable. Separate dated historical section is explicitly lower priority.
- All new response types, parameters, error responses and authentication appear in the maintained map contract, with version/drift checks. Public OpenAPI stays disabled.

## Phase A — scoped latest observations (priority)
Add one authenticated GET /api/v1/observations/latest, with required parameter, typed source (default measured), and bounded limit/offset (maximum200). Response has scoped station identity plus a nullable existing ObservationResponse for the latest visible record, not fabricated empty measurements; response clock/as-of is explicit. Missing and stale history remain distinguishable. Do not invent a per-record unit or source-unit certification absent verified metadata.

Use a typed store batch capability selecting at most one record per scoped station, parameter and source at/before the injected clock. Use an indexed bounded selection (e.g. scoped station IDs with LATERAL LIMIT1), not materialized full history. Apply trusted-unit predicate and service-role delivery exclusion BEFORE selecting latest. Preserve the latest QC status, including MISSING/failed; do not filter to passed then call it latest. Deterministic ordering, empty-scope behavior, pagination/count semantics and fakes must match PostgreSQL. Return no-store initially; no cache-policy expansion.

Tests: authenticated/scoped/no-grants/admin behavior, out-of-scope exclusion, stable pages, source identity, stale/no-history/latest-missing/latest-failed, future records, equal-time stability, delivery exclusions, unit holds, injected clock, bounded query plan and real PostgreSQL behavior. No live fleet benchmark until explicitly admitted.

## Phase B — maintained contract export
Include existing forecast-lab/snapshot and the new observation route in api/map_contract.py and docs/spec/api-v1-map.openapi.json. Repair nested discriminator schema generation at its supported schema boundary; do not ship traceback scraping or global monkeypatches from the temporary handoff exporter. Preserve runtime response validation, nested oneOf/const semantics, all existing schema fields and wire data. Validate native OpenAPI generation, resolve refs, validate real response samples, exercise members/quantiles/unavailable variants and retain drift/version gates. Document the pullable maintained file and regeneration command.

## Phase C — fleet response optimization
Profile request-local work offline. Add standards-compliant gzip negotiation for eligible JSON without changing no-store, auth, errors or leaking credentials; test decompression parity, Vary, identity and unsupported encodings. Add bounded typed field selection with explicit partial-response semantics/version and generated schema; omission must not masquerade as unavailable data. Skip excluded section reads, not merely serialization. Default full v2 response remains compatible. Preserve all authorization/publication gates even if a section is omitted. Request-local reuse of immutable model/artifact metadata is allowed; no cross-principal cache or shared DB connection reuse. Prove query work and bytes improve before claiming a fleet latency guarantee.

## Phase D — inferred BAFU thresholds
Read-only archive/reference cross-check first. Candidate set is intersection of approved134 stations and available BAFU forecast stations (currently37 of reference40), not all40. Parse actual danger boundaries, not axis maxima, percentile bands or guessed neighboring values. Require station-specific discharge units, ordered levels, finite values and immutable raw-payload hash/path/issue evidence. Conflicts/missing evidence remain unavailable.

Prepare a dry-run manifest and idempotent import using source=inferred, no overwrite of authority rows or divergent existing values. Preserve a reproducible provenance/rollback record. The station_thresholds table is also read by forecast-cycle alerting, observation alerts and skill computation: implementation must inventory these effects and not silently adopt inferred values operationally. Live writes require an explicit reviewed apply decision covering downstream effects, not just the dashboard display request. Other stations remain unknown until separate station-specific evidence is available. Expose thresholds through existing station detail first; any bulk read must preserve the same scope and source labels.

## Delivery and verification
One owner coordinates the plan. Independent Claude and Codex plan and patch reviews; high-risk API/live-write aspects receive relevant independent review under owner authority. Implementation begins after review/readiness per project safeguards. Full suite after final code change, lint/format/type checks, contract drift/version and native PostgreSQL checks before merge. Patch version bump on code commit; owner merges. No deployment bundled into a PR or an unrelated1089 upgrade. Frontend selected-station access continues against current runtime during development.

```json
{"phases":[{"id":"A","depends_on":[]},{"id":"B","depends_on":["A"]},{"id":"C","depends_on":["A","B"]},{"id":"D-evidence","depends_on":[]},{"id":"D-import","depends_on":["D-evidence"],"gate":"reviewed provenance and explicit downstream-effects/apply approval"}]}
```

## A/B review decisions (Codex round 1)
- Phase A is bearer-only via require_principal (consumer, reviewer, admin), not human-session auth. Page ALL stations in the principal scope, including stations with no visible discharge record. Sort by UUID before slicing. Default and maximum limit200; offset>=0. Total is the scoped station count, including null items; an empty scope/page is200. Query observations only for that page.
- Initial parameter is required Literal["discharge"]; blank/other parameter, invalid source, invalid bounds produce the existing422 validation envelope. Source is ObservationSource, default MEASURED. Preserve missing/invalid token401 behavior; do not invent station404 for an empty scoped list.
- Each item includes station_id, code, network, and observation: ObservationResponse|null. Top-level generated_at is the injected UTC query cutoff, with total/limit/offset. Null means no VISIBLE record at/before cutoff, not no underlying history. A MISSING observation remains an object with null value and original timestamp/QC. Staleness is client-derived from timestamp and generated_at; no new backend stale-age threshold.
- Consumer AND reviewer exclude exactly the existing DELIVERY_ID. Admin includes it. SQL exclusion is IS DISTINCT FROM (NULL-safe), before latest selection; do not exclude arbitrary deliveries. Preserve this role matrix in fakes and PostgreSQL tests.
- No-store must cover success,401,422 and unexpected errors via the existing middleware boundary, not only handler headers. Snapshot contract explicitly records existing403 publication-gate refusal (not reviewer-only),404 scope/eligibility failures,503 read-unavailable and422 validation. Preserve error-envelope shapes and snapshot failure no-store.
- PostgreSQL query acceptance includes absent parameters, long unit-held tails, hidden delivery tails, mixed sources and unit-read predicate cost. LATERAL LIMIT1 alone is not performance proof. Any index migration becomes an explicit separately reviewed change; no implicit live migration permission.
- Contract minor version increments from2.2 for additive paths. Preserve snapshot JSON Schema as well as OpenAPI and add negative members/quantiles/unavailable fixtures. Phase C and threshold persistence remain gated; a display-only threshold representation must not write the shared operational table without an additional explicit scientific-use decision.

## Final A/B decisions after Claude review
- B is independently implementable and will be delivered with A; it does not require waiting for the new endpoint to fix the existing snapshot contract.
- A searches an explicit bounded observation window, not all-time history. observation_hours defaults168, range1..720, matching the existing snapshot window contract. Return window_start and generated_at/as-of. The store constrains timestamp>=window_start AND timestamp<=as_of before latest selection. Null is no visible record WITHIN THIS WINDOW; never claim no history. Older data remains available via existing explicit historical-window routes. This replaces any earlier wording implying an all-history latest search.
- Page all scoped stations and include measured_parameters in station metadata to distinguish stations not configured for discharge. Null remains intentionally non-revealing. UUID sorting remains the stable order. Source takes exactly one ObservationSource value. Observation publication behavior matches the existing observation route (no forecast publication gate added).
- Bound work by page/window and test adversarial PostgreSQL cases. Time bounding is not a guarantee of a row count or latency; validate actual plans. If still too costly, revise this design before releasing rather than silently add a migration or drop unit/delivery filters.
- FastAPI422 uses its native detail-list envelope; HTTP401/other HTTP errors retain the existing error/detail envelope. Explicit response models and no-store middleware coverage are required.
- B export only: generate the snapshot schema using a supported GenerateJsonSchema subclass in serialization mode; omit boolean available discriminator annotations but retain oneOf/const and string representation discriminator mappings. Standalone default model_json_schema and its committed v2 schema stay byte-identical. Assemble operation metadata from a detached response-field-free APIRoute copy (never mutate app routes); merge snapshot components with collision-equality checks and explicitly bind200 to ForecastLabSnapshot. Test runtime response field is untouched.
- Validate the generated document with FastAPI OpenAPI model validation, local-ref resolution, and Draft202012 actual/negative response samples. No new validator dependency is needed. Contract version2.3 for this additive delivery.
- Root orchestrator authorizes A/B implementation after both independent reviews and these dispositions. C and D remain gated research/design, not blanket implementation or live-write approval. No operational inferred threshold import is authorized by this readiness.

## Delivery checkpoint
A/B implemented and independently reviewed by Claude, Codex and an access-safety
reviewer, with no blocking production defects remaining. Local full suite before
the base refresh:10950 passed,53 skipped,15 deselected. Base refreshed to
`ef00299823e4a19b9451b6da7d3f21d7951578b5`; reviewed production patch bytes were
unchanged by that refresh. Final merged-base verification remains a CI gate.
Release target0.1.1091; map contract2.3. Nothing deployed by this patch.

C/D remain deferred. Owner explicitly requires database-backed station threshold
metadata: investigate the existing schema before proposing an extension; do not
substitute a file-based API. Preserve provenance and separate interpretation from
alert/skill use. Any schema/import decision returns to the owner before applying.
