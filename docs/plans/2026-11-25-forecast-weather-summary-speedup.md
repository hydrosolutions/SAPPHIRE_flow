# Fast retained-weather summaries for forecast details

status: READY

## Goal and measured cause

Any model's forecast detail should avoid replaying historical weather to display
provenance. Nepal CMAL detail repeatedly takes ~21 seconds: forecast fetch 0.026s,
evidence fetch 0.295s, then restore/validate 17 stations × 365 days. Profiling
adds overhead (32.97s), with 29.6s in 17 wire restorations. The response is 27KB;
retained compressed group evidence is 10.4MB. General solution, no model IDs or
station counts in implementation. No change to forecast outputs or public JSON.

## Design

Persist a small derived summary beside immutable canonical evidence. This is a
read optimization, not new scientific evidence. Its writer fully restores and
validates canonical weather once; API reads retain the existing evidence/blob
hash and parent-identity checks but reuse the already validated summary.

1. Move the existing weather-summary reduction into a pure service returning
   frozen domain dataclasses (`types/weather_summary.py`). Preserve ordering,
   current/stale/unused/unknown semantics, source runs and ages exactly. Keep
   Pydantic at the stored-JSON and API boundaries, not in the reducer.
2. Migration0094 (after main's0093) adds `forecast_weather_summaries`: forecast_id
   FK to forecast_evidence, positive schema_version (writer/reader use1),
   composite PK(forecast_id,schema_version), weather_evidence_version1/2,
   manifest_sha256, snapshot_sha256, summary_json text and summary_sha256.
   Immutable UPDATE/DELETE/TRUNCATE guards reuse the existing evidence guard.
   A parent-binding INSERT trigger checks evidence/forecast marker, snapshot hash,
   manifest hash and summary hash (PostgreSQL built-in sha256(convert_to(...))).
   API SELECT only; worker SELECT/INSERT only; update conditional bootstrap grants.
   No original evidence, forecast, publication or station row is changed.
   Downgrade drops only this regenerable projection and guards.
3. `validate_selected_forecast` returns its already validated station/derived
   WeatherUse (or None), instead of discarding it. PgForecastStore builds and
   inserts its summary in the same transaction after canonical evidence insert.
   Existing no-op retry keeps its existing identity/behavior. No second restore
   is introduced on write. FakeForecastStore gains
   matching projection behavior and a purpose-filtered fetch method.
4. ForecastStore gains `fetch_weather_summary(forecast_id)` returning a typed
   projection or None. The reader joins forecasts and filters the store's exact
   data_use, as evidence reads do. Validate JSON strictly and its digest. API
   performs normal scope/publication checks first, then still fetches/hashes the
   retained evidence (including the existing artifact check). Validate the same
   parent fields plus model_id/snapshot/version; compare projection manifest and
   snapshot hashes, version, summary station and issue time. A present invalid
   projection fails closed503; only an absent projection uses the existing full
   restore path. Models with no selected-weather marker keep returning weather_use
   None without touching projections. No shared mutable/process cache or expiry.
5. Backfill CLI accepts required tenant and repeated explicit forecast IDs (1–100),
   exact data_use (standard default, provisional_rating supported), dry run by
   default, --apply to insert only missing summaries. Resolve the deployment write
   principal/tenant using existing helpers. Refuse IDs outside tenant/purpose and
   malformed evidence; never swallow errors or change existing projections.
   Fully validate one forecast at a time, no unbounded group cache; an existing
   identical valid projection is a no-op. Use a transaction per forecast so the
   bounded operation can resume safely. No API write on GET, no automatic data
   rewrite during schema migration, no station promotion or model reimport.

## Integrity and compatibility

The projection is trusted output of the same worker's canonical validation, bound
by exact immutable evidence digests and protected against later edits. It is not
an independent attestation against a malicious DB owner/worker. Retained blobs
continue to be read and hash-checked on every detail request: corruption or missing
canonical evidence must not become a successful cached response. Existing replay,
publication, backup and scientific validators still use full evidence and are not
redirected to summaries. API JSON and map contract version remain unchanged.

Supports V1/V2, station/group/derived weather, standard/provisional purpose, all
models. Existing historical rows remain compatible via fallback until backfilled.
The speedup becomes effective for all newly stored forecasts with updated workers,
and for old forecasts after the explicit backfill. One-time backfill is allowed to
be slow; both cold and repeated detail reads must then be fast.

## Verification

One independent Claude plan review before READY, one Claude patch review afterward
(owner-provided review policy). Tests: reduction byte equivalence across V1/V2,
station/group/derived/stale/unused; API cached and fallback responses identical;
missing/bad blob or manifest, corrupt projection, wrong identity/purpose, invalid supported projection
version/marker fail closed; unauthorized/withheld requests never access summaries. Real
PostgreSQL tests prove atomic write, idempotent backfill, correct tenant/purpose,
role grants and immutable guards. Prove the projected read never invokes full
restore (behavioral instrumentation), with a large group evidence benchmark and
cold/repeated read timings. Use synthetic evidence at the measured scale for local performance measurement;
no live snapshot export is authorized. Target ~1 second on Nepal-equivalent input, report
actual timings, no flaky wall-clock unit assertion. Run changed-module typing,
Ruff, relevant regressions and full final-source CI before owner merge.

## Delivery

Implement on perf/forecast-weather-summary-20261125 based on main7894946c. Docs cover
the projection, trust boundary, backfill invocation and mixed-version rollout.
Open PR, owner merges; deployment/backfill requires later deployment authorization.
No host changes during this implementation task. Absence-reason fields are out of
scope. Release-target helper, normal hooks, no agent merge or feature tags.

```json
{"phases":[{"id":"review_plan","depends_on":[]},{"id":"implement","depends_on":["review_plan"]},{"id":"test_review","depends_on":["implement"]},{"id":"owner_merge_deploy","depends_on":["test_review"]}]}
```

## Independent review resolutions

One Claude plan review completed. Its three blocking clarifications are resolved:
- Add the table to `api/routes/tables.py` ordinary_forecast_rows forecast_id-filtered
  set and prove generic HTML browser isolation. DB SELECT follows the existing
  forecast_evidence grants; no API write grants. Typed forecast store reads also
  filter exact data_use. Extend table/backup/role inventories where they enumerate
  the schema. Full database backups include this projection; restoration without
  a projection remains supported through the canonical fallback.
- Migration itself conditionally grants API SELECT and worker SELECT/INSERT for
  existing roles. New bootstrap has an optional-table guarded INSERT grant for
  fresh/restored installations. Apply migration/grants before new worker/API;
  explicitly verify grants before activation. Do not catch grant errors or weaken
  atomic forecast storage. No projection-only write failure may partially commit
  a forecast. Old workers can keep writing without projection until upgraded.
- Hash the FINAL serialized manifest inserted by PgForecastStore, including
  parent identity and data_use, never the capture-time manifest. The INSERT trigger
  checks canonical forecast_id/model_id/station_id/issued_at/data_use/version
  against the parent and evidence hashes. Compare summary station/issue directly
  from summary_json against the parent (no redundant identity columns needed).
  Reader independently checks model_id, station membership in weather_station_ids,
  final manifest digest, snapshot digest, version and summary station/issue.
  Neither writer nor reader requires evidence status COMPLETE: existing incomplete
  evidence with usable weather remains valid. Full scope/grid/payload semantic
  checks remain in the writer/backfill restore; read integrity is guaranteed by
  the immutable digest binding plus current blob hashes, not a second replay.

Use composite PK(forecast_id,schema_version) so a future reducer version can coexist
with old immutable rows. Reader selects its supported version; other versions are
not trusted and absence of the supported version uses canonical fallback.
An invalid supported row still fails503. Include supersession and IDENTICAL legacy
retry tests: each replacement has its own projection; no-op retries write nothing.

Backfill uses the worker DB role and existing CLI deployment/tenant principal
helpers (as in cli/assign_group_model.py). It uses the same canonical restore as
API fallback, with no assembly expected-facts argument. An existing projection must
be valid and equal to the derived result to be a no-op. Explicit IDs are preflighted
as a whole for tenant/purpose before writes; transactions remain per forecast.

The profile already gives 1.74s per station wire restoration under profiling plus
2.52s group parsing; merely selecting one station still entails full JSON decode
and expensive daily validation. Benchmark synthetic station and group variants,
plus list/detail readers; list routes currently do not hydrate weather evidence.
The blob hash/read remains ~0.3s for the measured case; report its budget explicitly.

No new live data movement: automatic approval review refused exporting retained
weather evidence. Use synthetic scale locally and read-only remote timing after a
separately approved deployment. No extra reviewer or automatic review loop.

## Implementation verification

Independent Claude patch review found no correctness/security defects; requested
extra CLI, parent-trigger, worker-role and lifecycle coverage was added. Reports
remain local. Local synthetic timing (Mac, no network/database transfer): a
365-day single-station history (17,520 samples) fell from 0.196s fallback to
0.0014–0.0021s projected; 17 stations × 365 days (297,840 samples, 1.83MB compressed
evidence) fell from 3.312s fallback to 0.0029–0.0033s projected. Responses were
identical. Projection runs include summary JSON validation and canonical snapshot
hashing, without replay. The live payload is larger (10.4MB); its previously
measured DB evidence fetch/hash cost was 0.295s. These timings are not an end-to-end
live API claim: verify cold and repeated live reads after deployment/backfill.

The first full CI run exposed one omitted schema inventory in the Nepal readiness
isolation fixture (three cases). Extend its unchanged-table inventory to include
`forecast_weather_summaries` and retain the exact metadata-set comparison. This
changes no readiness verdict or scientific oracle; it proves the projection also
stays untouched by station readiness.
