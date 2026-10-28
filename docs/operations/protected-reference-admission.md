# Protected reference admission

This dormant capability does not enable forecasts or authorize a live operation.

- Use `PgProtectedReferenceWriter.append_reference_evidence` only from a separately
  reviewed invocation with the actual protected-table owner session and READ COMMITTED.
  The caller controls the transaction. The writer never commits or changes permission.
- Supply a stable ID. Exact retries return that ID; conflicting content is refused.
- Use a verified proof only with real verification. Owner-assumed-same-zero records
  retain the owner's station-scoped authority separately from root's exact feed/curve
  mapping. A later curve requires a new mapping and immutable record.
- Cite measured-feed and curve metre-unit evidence separately. An owner zero assumption
  is not unit evidence, datum verification, feed authentication or QC passage.
- Assumed records have no numeric offset. Only the protected expired-curve conversion
  can consume them. Existing passing-QC, range, newest-curve and permission gates apply.
- API, worker and ordinary operator roles keep their existing protected-data denials.
  No endpoint, ordinary store registration, feed writer or permission writer is added.
- Actual consumed-lookback/contributor assembly remains separate M4 work. Its future
  implementation must preserve every consumed provisional fingerprint and test-only
  classification, even if only the oldest consumed day used an assumption.
- Test-forecast persistence remains disabled. No model call, scheduling, scientific
  promotion or ordinary observation insert follows from reference admission.

Migration 0073 preserves legacy canonical content and requires explicit basis on
new inserts. Downgrade refuses while assumed evidence exists. Application rollback
must not delete or relabel that evidence; older readers cannot parse assumptions.

Migrate to 0073 before installing the new application reader. Admission requires the
actual table-owner login, even for a superuser or migration login with another
identity. The writer canonicalizes aware timestamps to UTC before INSERT. Direct
SQL assumed timestamps must use the same canonical UTC spelling (six fractional
digits when nonzero); blank attribution is rejected using Python's whitespace set.
Downgrade locks both evidence tables before checking for assumed records.
