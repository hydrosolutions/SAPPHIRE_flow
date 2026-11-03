# Native provisional observation provenance

Native capture types distinguish an accepted provider fetch from its persisted source versions. Request evidence records the actual strict DHM wire filters as `lower_exclusive` and `upper_exclusive`, including the adapter’s existing one-second padding and subsecond precision. It records retrieval time separately. It does not infer these facts from the flow clock or newest observation. Logical observation filtering, inclusive logical end, global-since exclusion, pagination and budgets are unchanged. Wire bounds do not establish complete provider coverage.

Provider identities reject credentials, query strings and fragments. Equivalent host spelling, default HTTPS port and trailing slash normalize for comparison. Legacy evidence bytes are not rewritten.

A capture input digest covers sorted accepted raw rows. Identical duplicates collapse; conflicting values at one natural key fail. A persistence retry must retain its batch ID and input digest. A genuinely new fetch gets a new batch ID, even within a retried Prefect flow.

Capture versions preserve raw measurement identity separately from QC. Provisional derivation must retain source/QC, rating-file and reference evidence. The native writer commits raw state, immutable source captures, sightings and audit together. It checks an existing batch before upsert: a lost-acknowledgement persistence retry returns its original result without reverting a newer correction or QC state. These interfaces do not enable a deployment, provider poll, conversion or evaluation grant.

Recovery selects only current RAW observations with matching immutable raw snapshots. Each read is bounded to six hours and 1000 targets. Its older-RAW count describes captured current RAW rows before the requested start, not future completeness. QC_UNCHECKED rows are not recovery targets.

Native v2 changes provenance, not interpolation. It binds the exact capture and post-QC source while retaining curve expiry and warnings. Legacy v1 content remains unchanged. `stored_order` is database insertion order; migration-assigned order on old rows does not reconstruct original commit chronology.

In `evidence_mode="native_capture"`, configured measured water levels bypass generic raw persistence. Recovery discovery runs even after an empty fetch. Recovery-only QC reads the old chunk and configured context, not a span reaching to the current clock. Neighboring observations provide context but are not extra repair targets. Raw revisions are rechecked with capture evidence in the same bounded SQL snapshot. Normal ingest behavior remains the default.

Native mode rejects static custody fields. Optional approved legacy segments retain exact canonical feed content, its digest, reference digest and persisted v1 fingerprints. A matching persisted v1 result wins over a later native sighting. Changed raw or QC state requires matching current evidence and reconciliation. Unstored candidates remain omitted numerically and are described by optional omission identities; empty omission fields are not serialized.

The DHM factory keeps its existing frozen query end. A separate injected request clock records actual transport start/completion. Neither change adds provider calls or modifies monotonic request budgets.

### Recorded reconciliation outcomes

Native-mode backfill runs bounded recovery-only QC before reconciliation. It does
not expand QC targets to unrelated observations near the current time.

Each reconciliation attempt records its requested window, accepted/rejected counts,
new insert count, accepted-but-unassessed count, actual policy labels, and coded
holds. A zero-insert retry still records an outcome. This is an execution summary,
not a claim of complete inputs or forecast eligibility. The audit timestamp comes
from the database recording clock; validation time remains separate.

Scientific writes and their successful audit summary share a transaction. On
failure, that transaction rolls back and a separate failure summary is attempted.
If that audit also fails, the caller receives `audit_record_state=failed`; no
stored summary is invented. Raw observations and their QC remain independent.

Omitted unreconciled sources keep their immutable identities in stored forecast
lineage. Empty omission fields remain absent for v1 byte compatibility. Database
guards reject malformed, duplicate, cross-station, nonhistorical, or simultaneously
consumed-and-omitted source identities. Downgrade refuses retained omission evidence.

### Evaluation grant foundation (inactive until explicitly provisioned)

Display capability uses a separate station-scoped grant, not REVIEW or PUBLISH.
The grant base table is private. The API receives only an active-grant projection;
workers cannot provision grants. Subject deactivation, expiry and ordinary service
scope removal immediately exclude a grant from that projection. No grants are
created by migration or bootstrap.

Grant creation and one-way revocation use database statement timestamps and
nonempty operator attribution. Identity fields cannot change. Deletion, truncation,
backdating and un-revocation are refused. Principal and station references use
RESTRICT. Privileged provisioning will use the maintenance identity, never the
ordinary API credential. Live maintenance access and frontend identity still need
separate confirmation before operational provisioning.

Derived insertion metadata distinguishes migration registration from new database
sequence allocation. Migration assigns the former basis to existing rows without
rewriting content or fingerprints, then defaults new rows to the latter. Workers
cannot INSERT either ordering column. Sequence allocation is not commit order.

The evaluation read model separates native capture evidence from legacy custody
evidence as a closed union. A stored sample carries advisory source revision,
not model eligibility. Rejected samples retain their rejection code and no value.
Migration-registered samples explicitly report unknown original insertion order.

### Explicit historical adapter window

The historical batch method uses half-open measurement bounds `[start,end)`.
It keeps real transport clocks and the existing padded wire filters. It checks
internal-window overlap conflicts before the outer historical filter. Polling
keeps its existing strictly-after-cursor filtering and conflict order.

Historical capture is a provenance-mode boundary for native rating conversion.
The shared legacy selector refuses identities with any retained native capture
history, including unchanged readings and later uncaptured corrections. It emits
one bounded `native_capture_required` log per chunk and retains the existing
`source_identity_mismatch` result/audit code. History is not proof of the current
version: native mode still requires exact current capture or exact approved v1.
The older distinct provisional-discharge low-level API is outside this gate and
is not an authorized historical producer. No universal database barrier is claimed.

Configured QC resolution retains its ordinary formatted outcomes and also carries
exact network/code keys for each outcome. Historical result counts use those keys
from the same pre-fetch resolution, never string parsing or a second resolution.

### Historical flow boundary

The four historical fields select an early capture-and-QC-only branch. It refuses
all public dependency overrides, BAFU configuration, rating-backfill parameters
and nondefault QC context before bootstrap. Native identity, all requested scopes,
newer cursors, capture/QC capabilities and configured QC overrides are checked
before HTTP. Override resolution runs once against ordinary river/lake/weather
stations; only requested keys contribute threshold counters. No pipeline-health,
alerts, calculated values or rating conversion are produced by this branch.

Operational execution still needs separate approval. The nullable Prefect run ID
is retained honestly; intent attribution through Prefect parameters is unavailable
when it is null. No later official-import compatibility or collision bypass is
provided. API surfaces remain unimplemented, not advertised as available.

Historical recovery tests use real isolated PostgreSQL capture/QC stores and a
private MockTransport client seam. They cover old-row correction/QC reset, no
conversion with native rating configured, failed/empty retrieval, delivery
collision rollback, and unchanged health inventory/latest watchdog selection.
Public dependency rejection is exercised after Prefect parameter validation too.

Worker v2 reconciliation reads immutable capture evidence without requesting an
UPDATE-requiring row lock. Capture UPDATE/DELETE privileges remain absent. Role
bootstrap keeps the active evaluation-grant view API-only, including on reruns.

The default-off `provisional_ifs_tail_mode="control_3h"` enables an in-memory
IFS control-member tail only for approved provisional station/model bindings.
Standard cycles and per-track/training/hindcast paths stay unchanged. Each
parameter/day uses one complete 00–21 UTC grid from one eligible cycle, at
3-hour leads through 144 hours. Reanalysis and interior gaps are not overwritten.
The bridge uses existing future-weather SUM/MEAN timestamp semantics; provider
interval labels remain unverified. Forecast evidence stays forecast evidence,
with weather IDs deduplicated across future/tail lineage. No historical writes,
provider fetches, FI requirement relaxation, or alert activation are implied.
