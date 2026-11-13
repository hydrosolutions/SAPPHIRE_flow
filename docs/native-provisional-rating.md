# Native provisional rating discharge

The opt-in native rating channel is separate from observations and from protected
expired-rating test discharge. It never certifies measured discharge or changes
source QC. Default deployment configuration has no provisional rating bindings.

## Rating admission

Use the existing DHM delivery command with `ratings-only --manifest FILE --tenant
chwrr`; `--dry-run` rolls back the real transaction. A JSON manifest contains
`deliveries`, each with `delivery_id`, `delivered_on` (ISO date), and `files`.
Each file has `station_code`, `network` (`dhm`), `path` (relative to the manifest
or an operator-local absolute path), and the original byte `sha256`.

Admission stores immutable originals and hashes, resolves existing tenant-owned
stations, and inserts only numerically valid blocks from the newest delivery.
It never creates stations, changes measured observations, or runs reprocessing.
Older originals remain provenance. Same-delivery content conflicts refuse.

Selection uses the original newest file's latest eligible start, before checking
block validity. Invalid or tied historical blocks hold conversion; they never
cause fallback to a previous block or delivery. Both approved Nepal deliveries
contain 23 files for 21 stations. Their 21 newest blocks validate. Twenty older
blocks across five October files fail numerical validation; originals remain
unchanged and validation outcomes are audited. No point repair is applied.

## Reference and ingestion

`provisional_ratings` in deployment configuration scopes each explicit opt-in by
tenant, station, and model IDs. It includes an attributed exact reference binding,
source custody interval/reference and bounded `reconciliation_days` (1–366).
The native DHM adapter mapping must match that binding's endpoint and provider
station ID. Missing or mismatched bindings hold that station. A same-zero owner
assumption remains unverified; it is not extended to new files automatically.

The isolated derived store locks and rereads source/curve rows in a real
transaction. Source value/QC, delivery, reference and policy determine the retry
fingerprint. Calculation time does not. Original source and as-used evidence
cannot be deleted through cascading foreign keys. Historical derived generations
remain immutable; readers match the current generation before use.

`ingest_observations_flow` uses the same service after normal QC, including on
empty fetches. Explicit `rating_backfill_station_ids`, `rating_backfill_start`, and
`rating_backfill_end` take the no-fetch path before provider, alerts and watermark
stages. Existing QC remains as persisted; RAW/unchecked values are labeled
unassessed. Derived failures are reported separately in `rating_results` and do
not roll back genuine raw ingestion. Processing uses bounded six-hour chunks
with a 1,000-source-row ceiling per transaction.

## Native forecast channel

The forecast cycle accepts explicit `data_use=provisional_rating`. The default remains
`standard`. Scoped deployment bindings and a purpose-scoped store are required.
The current cycle uses native GROUP dispatch. Provisional inputs never read or save
standard model state and never enter daily standard attempts or alerts.
Daily conversion runs before aggregation. Accepted measured discharge takes precedence
for the whole day. Partial daily support and unassessed source QC remain visible.

Missing rating-derived discharge does not veto prediction. Daily discharge-only
models implementing the input gate receive an explicitly missing lookback when
no target history exists; the model's FI `max_nan` declaration decides admission.
Partially delivered histories retain their edges. Identity and reconciliation
holds still refuse, and precipitation/temperature requirements stay strict.
No observations or rating evidence are created for missing input slots.

An output that consumes no rating-derived discharge is stored as `standard`,
including measured-only forecasts. Completely missing discharge carries a
DEGRADED observation-input warning. Actual rating consumers retain
`provisional_rating` and their lineage. The same cycle routes successful outputs
and QC rejections to matching purpose-scoped stores; database guards are unchanged.
Consumers requesting only `provisional_rating` must also request `standard` to
see the weather-only outputs. Neither category is a claim of predictive skill.

Authenticated forecast list/detail and `data.json` accept
`data_use=provisional_rating`. They show a persistent PROVISIONAL label and persisted
input warnings. Default requests remain standard-only. Publication remains forbidden.
The bounded lineage reader only returns this new purpose; it does not grant access to
protected expired-test lineage.

Runtime bootstrap grants the worker SELECT/INSERT on the three immutable native
rating tables. It grants API/worker execution only on the purpose-filtered lineage
reader. The generic table browser excludes native source/provenance tables.
Delivery admission and reconciliation serialize through the existing tenant advisory
lock. Immutable delivery rows need no UPDATE permission or row lock.

## Bounded local validation

The local genuine-data replay admitted 23 original files for 21 stations and kept
133 water-level readings unchanged. Backfill and recurring reconciliation produced
133 immutable derived records; retries did not add records. October won the 604.5
and 647 overlaps. September supplied 447, 450, 670 and 684.
The one-day 450 aggregate was 1964.8684 m³/s. All 133 readings remained unassessed.
Expired, assumed-reference and partial-support warnings were retained. No reading
in this bounded sample required extrapolation; endpoint tests cover that path.

This proof used explicit local-only fixture bindings, not activation authority.
It did not forecast: one genuine discharge day is not a complete 30-day Q/P/T
window, and no complete future-weather fixture was available. No provider was called.

The bootstrap operator guard uses `pg_get_triggerdef` for exact WHEN-condition
comparison. Unlike `pg_get_expr`, it handles unrelated OLD/NEW trigger contexts
without relying on optimizer filter order. Missing, disabled and altered operator
conditions still withhold operator writes. Trigger-only native guards use the
migration/schema owner with a fixed catalog/temp search path and qualified objects;
PUBLIC and runtime roles have no direct EXECUTE authority.

### Reconciliation safeguards

Recurring runs cover the configured 1–366 complete UTC days and today's partial
interval in separate bounded calls. A failed binding returns a hold and does not
abort ordinary ingest. Nonfinite raw values retain tagged source evidence and
produce rejected derived records; finite siblings remain available. Missing
observations retain rejection records. Units and datum must match the native DHM
binding and reference evidence. The reference offset is applied once; native
observations are not normalized or rewritten by this channel.

Reconciliation audits report only newly inserted rows. No-op retries add no audit
row. The forecast SQL guard requires nonempty per-day fingerprints on both output
kinds. Native identity, issue time, artifact, lineage and output-curve exclusion
remain immutable. Day bounds require a complete UTC day ending by issue time, using 24 elapsed hours.

Role bootstrap restores the exact native function ACLs after `pg_restore --no-acl`
before its existing unknown-definer preflight. It does not grant direct execution
of trigger guards or relax rejection of unrelated executable definers.

Native runs do not write the standard forecast-freshness heartbeat, including
failure paths. Shared weather health and the native cycle result remain visible.

The bounded genuine-data replay uses fixture station metadata and fixture reference
bindings. It does not validate deployed metadata or authorize live activation.
Actual station units and datum bindings must be checked before activation.

When no native binding is configured, ordinary ingest does not invoke a native
reconciliation task at all, including empty-fetch runs. Explicit backfill still
requires scoped opt-ins and bounds. Enabled empty-fetch runs still reconcile.
