# NHS forecast configuration and missing discharge inputs

## Outcome

Normal registered provisional forecasts use the approved NHS provider identity configuration and represent interior gaps in daily discharge honestly. They retain source authority, strict weather completeness and the ForecastInterface (FI) contract. Unrelated ingestion, other default-pool jobs, STANDARD forecasts and Swiss behavior remain unchanged.

NHS is the new user-facing name for DHM. Existing `DhmConfig`, configuration keys, persisted `dhm` network/source identifiers, API station identifiers and endpoints remain authoritative. A display-name change does not authorize renaming them or changing the upstream API.

Deliver this as two independently reviewed, reversible slices: provider configuration/registration first, then provisional discharge-calendar handling. Their implementation may be planned separately. Neither slice alone proves that an operational forecast can be accepted.

## Why this is needed

A normal deployed provisional run for 2026-11-05T00:00Z failed before weather fetching or model invocation because its provider loader selected `HydroScraperConfig`, not `DhmConfig`. The forecast worker lacked the approved provider overlay and its read-only mount; the successful ingest worker had both. Fresh deployment, run and pool evidence showed no configuration-path overrides explaining away that difference. Native capture adoption and provisional opt-ins do not themselves select a provider.

Current Aquacast already predicts with missing discharge values. Unmodified AQ0.1.376 (`c271067bc34a0fee1dc0d8a710e95c888d29f72e`), using the real CMAL-small checkpoint and complete numerical P/T fixtures, produced finite, repeatable predictions on 30-row fixtures with six interior discharge NaNs and with seven explicit NaNs including the last day. This did not prove the separate 29-row short-edge delivery path, which still needs validation. The fixture retains its documented historical imputation/rating limitations. This is capability evidence, not current weather readiness or skill under gaps.

Two SAP boundaries remain distinct:

- Explicit Q NaNs exceed the current model's FI declaration (`max_nan=0`) and are rejected by the native group gate.
- Omitted daily Q rows are not automatically NaNs. The operational assembler currently skips an interior calendar gap as a cadence mismatch before reaching that gate.

The approved direction corrects configuration and representation. It does not create a new model capability or bypass its declaration.

## Provider configuration and registration

The normal production path must construct the existing purpose-scoped rating reader from the same approved, hash-verified NHS/DHM provider identity configuration used by native ingestion. Keep exact station-network, endpoint, API-station, unit, datum and approved-reference checks. Missing or conflicting authority must fail closed; provider selection must not be inferred from country, model name or the presence of provisional bindings.

Provider configuration must be available read-only to the forecast process and selected only for the intended forecast deployment. Preserve ordinary ingestion, other deployments, unrelated environment/job variables, and the forecast's existing weather/model/provisional settings. STANDARD runs share the forecast deployment and must remain compatible. Retaining mandatory provider budget fields for parsing does not authorize HTTP polling during reader construction.

The selection must survive routine registration, restart, upgrade and rollback. It must not depend on an untracked live override or a new competing authority for the worker's existing overlay chain. Supported Compose invocations, including explicit file lists, must preserve the reviewed configuration. Compare effective configuration with and without the addition: only the intended provider mount and forecast-registration selection may differ. Preserve unrelated job variables when setting or removing the owned selection; test actual Prefect precedence and clearing rather than assuming dictionary-merge behavior.

Reviewed deployment templates and any necessary focused generation/validation logic belong in version control; installed artifacts must match reviewed bytes. Keep the actual provider identity file out of commits and verify it against approved references. No new deployment runner or configuration framework is needed. Activation must fail closed on missing files, ambiguous environment shadowing or malformed configuration. Make the mount readable and verify it before enabling a persistent selection; clear and verify that selection before removing the mount or rolling back to an incompatible image. Preserve other jobs during worker lifecycle changes.

## Provisional discharge representation

Apply normalization only to explicitly opted-in, daily, discharge-only provisional inputs with real accepted rating lineage and a model participating in the existing `GroupInputGate`. Ungated models retain their previous cadence rejection. Do not add a protocol, global gate or model-name special case.

Within the existing delivered Q extent, represent absent **interior** daily timestamps as explicit NaN. Preserve finite values and their timestamps exactly. Normalize existing null/NaN missing cells consistently on this path. Validate UTC daily alignment, uniqueness, bounds and lossless joining; malformed inputs retain the existing fail-closed cadence-skip channel.

Do not pad leading or trailing undelivered days, trim the delivered extent, or compress time. A supplied edge row with a missing value remains distinct from an absent edge day. Shape and length shortfalls remain the model's responsibility: preserve supported edge-gap forecasts rather than assuming every short series must fail.

An absent discharge product, no accepted rating support, or a failed identity/reconciliation check must not become a fabricated all-missing product. Create no observations, derived records, QC results, fingerprints or support claims. Lineage continues to describe actual contributing samples only.

The existing FI gate still counts missing values across the delivered column and enforces the declared tolerance. Current AQ must still refuse explicit interior NaNs under `max_nan=0`. Do not add target-lookback metadata or reinterpret forcing-only `declared_lookbacks`. The existing Q7/P30 whole-column limitation (7 discharge steps versus 30 weather steps) is unchanged, documented and tested—not silently corrected here. Standard, Swiss, training, hindcast, non-daily and multi-target paths remain unchanged.

Past and future precipitation/temperature requirements remain strict. No new weather filling or tolerance relaxation is part of this work; existing approved tail behavior remains unchanged.

## Observable success and evidence

- Real production loading and store-factory composition—not only injected `NativeRatingInputs`—select the intended provider and purpose-scoped reader. Parse-time provider errors and per-member runtime identity holds retain their correct, distinct outcomes.
- Every active opted-in binding matches approved references. Real identity checks expose only safe booleans/hashes; CI uses synthetic fixtures, not the real provider file or credentials.
- Registration/configuration tests cover defaults, repeated registration, opt-out, unrelated job-variable preservation, pool/deployment/run precedence, missing mounts, ordinary restart and rollback. Effective full configuration and STANDARD behavior remain unchanged outside the intended provider selection.
- Normal assembly→group assessment→model tests cover complete input, interior gaps, short edges, supplied edge NaNs, mixed null/NaN, malformed calendars, ungated models and mixed station extents. Healthy members remain serviceable; all-excluded groups return their normal typed outcome without an unintended model call.
- For the known relative fixture, six interior omissions yield 30 delivered dates: 24 supplied and six inserted NaNs. If the last day is also absent, deliver 29 dates with six interior NaNs; do not manufacture the thirtieth row. Test off-midnight issues against complete UTC-day boundaries.
- Coverage reporting is finite-aware and uses the existing recent-gap DEGRADED/older-gap PARTIAL policy. Use OBSERVATION reporting selected by actual provisional rating lineage. Distinguish missing supplied cells from undelivered edges, and describe the assembly window rather than falsely calling every slot a model-required Q step. Reporting never changes admission. Verify success read-back and refusal diagnostics without inventing forecast rows or source lineage.
- CI uses truthful test models and the strict FI boundary. Recorded no-network tests outside CI with the existing restricted checkpoint verify actual current-model edge behavior before/after changes, with exact versions, commands and hashes. The real Prefect integration harness, full applicable tests and independent patch/high-risk reviews gate delivery; private fixtures are not committed.

## Dependencies, boundaries and authority

AQ PR144 concerns a real declaration/flags mismatch and remains separately modeller-reviewed. This work neither changes that PR nor assumes its adoption. Any compatible dependency/pin change requires its own decision; no SAP-side tolerance override is allowed.

The Gateway 0.1-degree weather repair was expected after this diagnosis but is not qualified evidence. Complete required P/T and usable future forcing still need proof. The separate [operational-weather ingestion vision](2026-11-05-nepal-operational-weather-ingestion-through-datagateway.md) remains independent and paused; this vision does not implement its route or permit provider calls.

Trishuli `00001` is a historical research station, not part of the active NHS set. It is excluded from this configuration, live polling and forecast work.

No schema/trigger changes, FI redesign, retraining, new models, generic framework, observation fabrication or unrelated cleanup are in scope. Persisted provisional forecasts retain native lineage guards. Source/skill evidence must not be presented as a successful deployed forecast.

The preparatory plan remained DRAFT and did not authorize implementation. A later implementation invocation must reconstruct current source and evidence and satisfy fresh repository review and authorization gates. Only the owner merges; root separately controls any live application and acceptance run.
