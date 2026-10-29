---
status: READY
execution_scope: D1-build
---
# BAFU observation units: normalize new readings and repair proven errors

Planning date: 2026-10-28. D1-build readiness recorded by the coordinating root
session after owner instruction to proceed and independent Claude, Codex and
scientific/data-repair readiness reviews found no implementation blockers.
Execution scope is D1-build only. D1 activation, D2 historical apply, upstream
submission and deployment remain separately gated; this status does not approve
them. Full review reports stay outside Git.

## Outcome and scope

All operational BAFU discharge inputs must be in m³/s before persistence, QC,
aggregation or model inference. Source values and their unit evidence must remain
traceable. Fix the boundary; do not weaken QC or patch CMAL/FI inputs downstream.

Two separately gated deliveries:
1. Stop introducing incorrect units; preserve unaffected stations and parameters.
2. Repair only demonstrably incorrect existing rows and reassess dependent outputs.

This does not implement weather gap filling, change QC thresholds, retrain models,
repair all BAFU history by assumption, change Nepal, or configure remote access.
The independent weather catalogue gap remains a separate operational issue.

## Evidence and uncertainty

The 2026-10-28 audit covered 148 configured BAFU river stations:

| Current official discharge display | Count | Interpretation |
|---|---:|---|
| l/s | 5 | 2206 Melera; 2251 and 2252 Plaffeien-Schwyberg; 2283 Wasen-Riedbad; 2414 Mosnang-Rietholz |
| m³/s | 134 | No litres conversion indicated by the current display; not a historical certification |
| No current discharge table | 9 | Unresolved, not inferred to be m³/s or l/s |

All five l/s stations have LINDAS numbers matching the displayed magnitude. For
2206, the captured LINDAS reading and measured database row at 2026-10-28 08:00 UTC
are both 22.0. BAFU's current table explicitly reports l/s and its surrounding
24-hour range is 21–22 l/s. Correct conversion is 22 / 1000 = 0.022 m³/s.
That exact database comparison used the first capture, at 09:00 +01:00. The
upstream-report table below uses a separate later roster capture (2206 at
09:10 +01:00); these are distinct snapshots, not conflicting timestamps.
For 2252, a same-time raw/page comparison at 09:00 +01:00 shows 0.135 l/s.
Do not imply all other web/raw comparisons were simultaneous: some differ by
10–60 minutes and web values can be rounded.

The public predicate `http://example.com/isLiter` is `true` at 133 of the 134
m³/s-display stations as well as the five l/s stations. It is NOT usable as a
conversion switch. Station 2044 is a control: LINDAS 17.645 with `isLiter=true`,
while BAFU displays approximately 18 m³/s. The nine-station unresolved group includes
2356 Cavergno: stale LINDAS discharge, no current display table, and `isLiter=true`;
its unit must not be guessed from that flag or its baseline.

The shared query omits unit information. `HydroScraperAdapter` parses discharge
with `float(...)`; `PgObservationStore.store_raw_observations` writes that value
unchanged. Independent Claude and Codex reviews found no later discharge
conversion. The archive collector has the same implicit-unit assumption.
The existing raw upsert CAN change a same-identity row and resets QC on changed
values: a corrected adapter deployment is therefore not automatically an
insert-only operation. A same-timestamp collision is part of the repair boundary.

QC is not a unit validator. Large errors become `QC_SUSPECT`; some wrongly scaled
small readings pass QC. The six exclusions do not bound historical impact.
All 138 daily API checks passed for October 28 (132 produced, six excluded).
That CMAL group is a subset of the 148 river-station audit roster; API acceptance verifies delivery/authority, not historical input-unit correctness.

[Plan 029](archive/029-lindas-adapter-fix.md) records an April claim that the flag
is misleading and values are already m³/s. Retain it as dated history, with a
short correction pointer when this fix lands. The flag warning is corroborated;
the blanket numeric-unit claim is contradicted by the current five-station data.
The original April evidence and any upstream change date remain unknown.

Evidence is local under
`/tmp/daily-evaluation-implementation-evidence/overnight-return/must-have/`:
`bafu-roster-unit-audit.json`, `all-river-lindas-units.json`, saved official pages,
`exact-timestamp-observations.txt`, `parser-reproduction.txt`, and `qc-baselines.txt`.
Before implementation, retain small sanitized, dated source fixtures and their
hashes durably; temporary evidence paths are not a long-term dependency.

### Additional published-metadata cross-check (2026-10-28)

The live hydro graph's SHACL discharge property shape declares
`qudt:hasUnit <http://qudt.org/vocab/unit/M3-PER-SEC>`. Its `isLiter` shape declares
`xsd:string`; the captured `sh:in` list contains only the literal `"true"`. No
semantic explanation for that flag was found in the inspected property metadata.
The river cube is marked `CreativeWorkStatus/Draft` and names the contact
Hydrologische Abfragezentrale, `abfragezentrale@bafu.admin.ch`.

This is material source-contract evidence: SAP3's historic m³/s assumption agrees
with the published unit declaration, but the five station-specific numeric/display
comparisons conflict with it. Do not frame the incident solely as SAP3 failing to
read a reliable unit flag. The defensive normalization implementation can proceed;
source-policy activation and historical correction must retain this contradiction
as an unresolved publisher-contract issue. Do not automatically prefer the global
metadata, the flag or the station-page label to resolve a contradictory source.
Ask the publisher whether numeric normalization or metadata/public display is wrong,
which field is authoritative, and the affected historical intervals.

Direct official-page and bounded named-graph queries supplied this evidence; no
exhaustive web-document search is claimed. Saved responses are under the existing
local evidence root's `lindas-documentation/` directory. General web search was not
configured. The upstream report below must incorporate these findings before sending.

## Design decisions proposed for approval

### 1. Explicit source-unit contract, not the boolean flag

Use a reviewed, versioned BAFU source-unit registry injected into the existing
adapter construction. Key by source/network, gauge code, station kind and
parameter. The database station key is `(network, code)`; ingest loads the fleet
and the LINDAS adapter currently matches `(code, station_kind)`, not tenant/network.
Validate each requested binding against the expected BAFU source/network/kind
before resolving units, then preserve the caller's station UUID. Reject ambiguous
or contradictory bindings explicitly; do not invent a tenant-scoped lookup or
silently change legitimate same-source duplicate configurations. Authorization
and repair manifests independently restrict tenant and exact station UUIDs.
A record carries source unit, canonical unit, evidence identity/hash, verification
time and the measurement-time interval for which the evidence is valid.

The registry is source-owned, not duplicated per tenant. Two configurations for
the same source/code/kind must resolve identically; tenant scope belongs to station
binding, reads and repair authorization. Store it in reviewed versioned config,
not a new registry database service.

Use a constrained unit enum and immutable domain records; parse configuration at
the boundary. Support exactly l/s → m³/s and m³/s → m³/s (identity)
for discharge. Specify one pure numerical operation: `float(source_literal) / 1000.0`
for l/s. The adapter and repair tool must call the SAME function, not equivalent
multiplication or separately rounded formulas. Prove bit-identical corrected
values and that re-ingesting the same source literal performs no value/QC rewrite.
Water level and temperature require no discharge-unit registry entry. Keep `RawObservation` canonical; do not make every internal
consumer guess units or add a numeric unit channel to FI inputs.

Seed only from verified source evidence, including m³/s controls; this is not a
five-station global exception list with an implicit m³/s default. The 134 display
checks are candidate identity entries, subject to source-contract validation.
For unresolved stations, missing/conflicting unit evidence or a reading outside
the verified measurement-time interval, withhold that discharge with an explicit
typed fetch diagnostic. Do not discard independently usable water-level/temperature
readings or fail another station. A station with no discharge measurement is not
a discharge-unit failure.

The current `StationFetchOutcome` forbids observations together with a failure
cause. Add a separate immutable typed parameter-diagnostic collection for partial
outcomes, rather than violating that invariant. Keep whole-station failure causes
for outcomes with no usable observations and preserve the list-only façade and
one-outcome-per-station contract. Update batch counts and fetch-health details to
report withheld discharge alongside retained parameters; partial success must not
be reported as either complete success or zero station output. Cover replay,
fakes and real ingest health accounting, including all parameters unresolved.

LINDAS is latest-only: withheld discharge cannot be recovered by a later history
request. D1 must retain the exact received source literal, measurement time,
identity, payload hash and unresolved reason in a bounded immutable quarantine
outside canonical observations, before claiming successful capture. Reuse suitable
raw-evidence storage if available; do not assume the independently scheduled
collector captured that same reading. Document retention, budget and recovery
procedure. Failed quarantine persistence is an explicit capture failure, never a
silent successful skip. Owner accepts remaining loss/coverage risk at cutover.

Do not make website scraping a requirement of each five-minute ingest. Propose
an operator-managed registry initially, validated before activation and at each
onboarding/source change. Each entry has an evidence-backed measurement-time start
and an open-ended forward interval until a verified source change closes it.
A separate refresh-due date raises a health warning, not automatic fleet-wide
withholding. Approve refresh ownership/cadence before activation. This is an
explicit continuing-source-contract assumption, not proof of unchanged future
units or historical validity; conflicts still fail closed. No guessed historical
start dates. If
upstream provides a documented reliable unit field later, parse and validate it
against this contract; disagreeing sources fail closed, not silently override.

`isLiter` remains diagnostic evidence from the bounded audit, never an authority.
Do not expand the production SPARQL query for this MVP. Preserve its limiter,
subject-local operational parsing and the collector's strict schema boundary.

### 2. Normalize once, retain evidence, keep replay unambiguous

Normalize at the LINDAS boundary before construction of operational discharge
observations. No change to water level, temperature, manual imports, DHM, rating
curves, CAMELS-CH or model outputs. Capture source numeric value, source unit,
normalization-policy version and source-evidence identity in a durable companion
record tied to the existing observation natural key/ID. Do not misuse delivery IDs,
rating-curve provenance or forecast `input_lineage` for unit provenance.

Persist value, unit evidence and any collision journal atomically in one real
transaction established by the narrow writer. Production connection setup in
`flows/_db.py` uses AUTOCOMMIT: do not assume an encompassing transaction exists,
or that a Python transaction context alone changes driver autocommit semantics.
P1 must specify the transaction-capable connection/boundary and verify it under
production-equivalent isolation, including interruption between writes. Any failure
rolls back the whole bounded batch's values, evidence and journal together; an
uncertain commit acknowledgement is reconciled by batch identity before retry.
An unchanged poll currently returns no observation ID: resolve its stable natural
key without requiring a value change, and attach newly established evidence only
when the old value's semantics are proven. Preserve prior evidence on a publisher's
same-timestamp restatement, and journal the actual persisted old/new state on a
conversion collision. A policy/evidence refresh with unchanged scientific value
must not gratuitously reset QC. Zero l/s also remains numerically zero in m³/s:
record proven unit evidence without forcing a value rewrite, and distinguish
numeric-unchanged from semantics-verified in the manifest. Test failure between
writes, repeated identical polls, zero conversion, newly evidenced unchanged
values and changed source revisions.

### Selected persistence contract (P1 proposal, not implemented)

Existing `measurement_feed_evidence` belongs to protected rating-reference feed
attestation; `ArchivedObservationValue` belongs to rating-curve reprocessing.
Neither is a generic unit-normalization journal. Do not overload either contract.
Use a narrow migration for three related records, not a generic event platform:

- **Unit batch receipt:** immutable batch ID, operation enum (ingest/repair/rollback),
  source-policy version/hash, input/manifest digest, exact expected counts and final
  persisted-result digest. Committed in the same transaction as its effects; no
  separately committed ACTIVE claim or timeout-based takeover.
- **Unit event:** immutable event ID, batch reference, tenant/station/natural key,
  source numeric literal and parsed value, measurement time, source unit or explicit
  unresolved state, evidence identity, outcome enum, optional observation ID and
  exact before/after value/QC/provenance snapshots for mutation events. Quarantined
  source-native payloads use this record with no canonical observation ID; canonical
  events bind to the observation identity AND current value/source-provenance
  fingerprint (independent of mutable QC verdicts). Historical repair may lack the
  original source string: represent that explicitly and retain the stored old float,
  never fabricate an original literal. A quarantine event cannot masquerade as a
  canonical observation. Apply boundary validation and database shape constraints.
- **Unit read disposition:** versioned explicit tenant/station/parameter/source/time
  scope, trusted-read hold, reason, policy identity and approval evidence. Restrict
  updates to the authorized maintenance path; ordinary ingest cannot release a hold.
  Keep prior dispositions auditable. No inferred hold merely because an old row has
  no event; the owner-accepted 134-station legacy policy remains unchanged.

Resolve the exact column/constraint/grant definitions in the migration review.
Source registry config remains source-owned and versioned in Git; the database
records capture what policy actually governed a write or hold, not a second mutable
source-unit registry. Preserve evidence as long as referenced observations/repairs
need it. Deduplicate identical source revisions; define quarantine retention and
capacity before activation, with explicit capture failure at a limit, never silent
purge of still-needed repair evidence.

A dedicated writer accepts a typed captured batch, not an unlabelled list of
numbers. Add immutable source captures alongside batch observations/parameter
diagnostics at the adapter boundary; preserve the legacy list façade for callers
such as recording, but operational ingest must not discard capture metadata before
writing. Other adapters and already-canonical replay paths keep their current
contract. The ordinary raw-upsert path must reject a protected live BAFU write
without the required capture rather than silently bypass normalization evidence.

Reuse the dedicated-engine transaction-factory pattern already used by forecast
writers: `engine.begin()` on a non-AUTOCOMMIT connection, bounded connect/lock/
statement timeouts, and no network fetches inside the transaction. Do not change
the shared ingest connection's isolation level in place. Validate all inputs first;
then lock/check natural keys in deterministic order, write observations/events/
receipt in one transaction and commit. Proposed initial bounds are existing writer
precedents (5 s connect/lock, 30 s statement), to verify under the actual batch size.

An identical source revision returns its committed result without duplicate events
or QC resets. A changed same-timestamp literal is a new revision with its own
journal. Retry uses the same batch identity/input digest; identity reused with a
different digest is an integrity failure. After uncertain acknowledgement, reconcile
receipt and result digest using a fresh connection. If unresolved, stop that batch;
never divide again or assume absence from an unreliable read proves rollback.
QC judgement remains a separate explicit phase; it never makes value/evidence
atomicity depend on the shared AUTOCOMMIT connection.

### Selected read contract (P1 proposal, not implemented)

Use construction-time reader purpose, not a heuristic based on `qc_status` filters:
`TRUSTED`, `QC_CONTEXT`, or `SOURCE_AUDIT`, represented by an enum. Keep existing
ObservationStore fetch signatures; default production construction is TRUSTED.
- TRUSTED applies the explicit disposition to single/batch reads and by-ID numeric
  exposure, before resampling/model input selection. Manual imports and unaffected
  source scopes are unchanged. A withheld reading is not an invented zero or pass.
- QC_CONTEXT admits canonical post-cutover rows even while a model-history hold
  remains, but excludes unit-uncertain legacy neighbours. Require matching current
  value/source-provenance evidence, not merely any old event for that observation
  ID. It cannot release a hold.
- SOURCE_AUDIT exposes the old source values only to explicit restricted diagnostic/
  repair wiring, with their unit uncertainty evident; never to a numeric evaluation
  endpoint or a model. Parameter/tenant authorization remains independently enforced.

Wire a dedicated QC-context store into `_run_qc_task`; do not switch the shared
store's purpose dynamically. Preserve latest-capture cursor semantics independently
from trusted model visibility. Cover all ObservationStore read entry points and
`PgProvisionalDischargeStore`'s direct observation SQL; inventory aliases/direct SQL
and every production constructor, including API readers, rather than assuming one
fetch method covers all paths. Fake stores must model the same contract. Protected
numeric reads must fail closed if the disposition query fails; no fall-through to
raw rows. Restrict any untrusted audit display to authorized diagnostic use.

For this cutover, the five l/s stations plus explicitly unresolved actual-discharge
ranges get holds; scope them by source/time so manual imports remain usable. The
eight unknowns outside CMAL and already-excluded2356 do not imply new CMAL losses,
but other consumers still need the approved impact list. New canonical l/s rows can
be stored and checked without claiming complete trusted history. Restoring model
eligibility is an explicit maintenance decision after D2, not an automatic count
of fresh readings or an ordinary QC-status update.

Keep the evaluation-only collector's numeric archive source-native in this MVP;
do not normalize its parsed values or change its query, slot identity, freshness,
completion markers or raw-only behavior. Preserve existing raw companion files
immutably; these are reserialized decoded JSON, not original HTTP wire bytes.
Document explicitly that legacy/current collector values are NOT guaranteed m³/s.
Confirm the current reader inventory (including external configured consumers)
before relying on the finding that the in-repo audit CLI reads only timestamps and
completeness. No reader may expose source-native discharge as canonical. If an
active numeric reader exists, block that use or plan its reviewed normalization
before activation. Future numeric readers must apply the source contract by
measurement time. This avoids an unnecessary collector format migration now.

Replay/recording must distinguish canonical observations from raw-source fixtures.
Existing canonical replay data must not be divided again. Wire the registry into
`tools/record_fixtures.py`'s operational adapter construction; the existing synthetic
canonical reference fixture does not need wholesale regeneration. Include mixed-unit and
unknown-unit fixtures in recorded contract tests. Units come from a validated
source contract, never the file name, value magnitude, or station name.

### Contract decisions from focused review (2026-10-28)

These decisions refine the selected contracts above. They are approved for the
D1-build implementation pass and remain subject to patch review, not live permissions.
The final readiness pass also requires these implementation invariants:
- Once later work for a station has started, an unresolved older batch may only be
  reconciled, never re-applied. Acquire its batch-ID lock on a fresh connection
  before interpreting receipt absence; a failed/unreliable read is not absence.
- Current applicability uses the latest event for the natural key and its policy,
  not any historical matching fingerprint (including A→B→A under a changed policy).
- Quarantine capacity failure rolls back the entire atomic station batch, including
  otherwise usable parameters, and reports that loss explicitly. Other stations
  continue. This narrow failure behavior must appear in activation impact/budget
  approval; do not silently split the batch to preserve only convenient parameters.
- The database guard is invoker-rights, not SECURITY DEFINER; grant only required
  safe projections/columns, short-circuit unrelated DHM writes, and test all actual
  roles. Check OLD and NEW protected scope and final transaction state.
- Deduplicate and sort advisory lock keys themselves, not only natural keys. Repeated
  stale quarantined captures cannot create unbounded duplicate events/receipts.

**Capture and revision identity.** Validate source-number lexical syntax before
`float`: finite decimal/scientific notation only, no underscores or non-finite
spellings; retain original text, canonicalize signed zero, and compare numeric
semantics by the specified float conversion. Equivalent repeated predicates collapse;
conflicting discharge literals become a parameter diagnostic with all originals
quarantined. Conflicting measurement times invalidate the subject's measurement
identity, so no parameter from that subject is assigned an invented timestamp.
Quarantine can carry raw time text with no parsed time; canonical captures cannot.
Conflicting unordered captures for one persistence key are not last-wins.

Source identity is shared across source/code/kind, but persistence identity includes
the independently validated tenant/station/parameter/time/source. Different valid
caller bindings cannot disappear through source-payload deduplication. Two batch
captures of the same persisted key must be equivalent or rejected as ambiguous.

A station batch has a stable retry ID assigned before its first persistence attempt
and an immutable input digest. Cross-run idempotency compares the current numeric/
provenance/policy-semantic fingerprint under serialization, not a globally unique
raw-payload hash. `22` and `22.000` alone are not new numerical revisions. A newly
observed A→B→A reversion is a new event if current state is B; retrying old batch A
only reports its historical committed receipt, with current applicability reported
separately as current/superseded/unknown. It cannot restore A over B. A new policy
can resolve the same formerly quarantined literal into a new canonical event;
quarantine stays immutable. Conflicting policy cannot reuse an old trusted result.
Normal revisions after activation are allowed only within the proven source-unit
interval and audited writer. Legacy same-key corrections need the approved collision
manifest. Require one active operational BAFU fetch/persist sequence at cutover;
no competing manual fetch writer or overlapping scheduled run may reorder captures.

**Station-sized transactions and failure semantics.** Predeclare one atomic batch
per station outcome, including its captures and diagnostics; different stations
may succeed independently. Within a transaction acquire a batch-ID advisory lock,
then transaction-scoped advisory locks for every natural key in deterministic order.
Use a stable documented digest-derived lock key, not Python's randomized hash.
Hash collisions may serialize unrelated work but must not corrupt identity checks.
These locks cover absent as well as present rows. Reload predecessors and validate
delivery provenance after locking; preserve `DeliveryCollisionError` protection.
Concurrent receipt reuse with a different digest fails; lock timeout rolls back
that station batch and leaves others available. No lease takeover or blind retry.

Captured batches do not use the existing catch-and-split delivery fallback: their
station partitions and IDs are fixed before writing. Always invoke persistence
when captures exist, even if canonical-observation count is zero; bypass the current
empty-raw-observations early return for that case. Keep HTTP fetch health before
storage, and report durable capture success/failure separately after receipt
reconciliation. Partition unique captures into inserted, restated, evidence-only,
unchanged, quarantined and rejected counts; counts sum to the declared input set.
An unresolved commit is not a successful capture or a proven rollback.

**Persisted scope and hold precedence.** The writer resolves the supplied UUID
against persisted tenant/network/code/kind and validates it against the captured
binding. `source=measured` alone is not LINDAS proof: require a verified BAFU adapter
binding and source contract. Reject binding changes since capture/approval; do not
retarget an old manifest to another source. Enumerate every allowed persisted UUID
for a source binding, or refuse an incomplete disposition approval.

All intervals are half-open `[start,end)`. Holds are additive: any applicable active
hold wins. Releases identify the exact hold and approved subrange; a narrower release
cannot override another overlapping hold. Disposition versions are immutable;
maintenance-only supersession records define the active set, not last-row-wins.
Query current dispositions in each SQL read (no stale process-local cache) and bind
filtering and numeric selection to the same statement snapshot. Query failure is
unavailable/error, never a raw-data fallback.

| Row condition | TRUSTED | QC_CONTEXT |
|---|---|---|
| Unaffected or accepted legacy source scope | Existing eligibility | Existing context |
| Held legacy unit-uncertain measured row | Excluded | Excluded |
| Held new/repaired canonical row with matching current fingerprint | Excluded until explicit hold release | Eligible canonical context |
| Manual import outside an explicitly approved hold | Unchanged | Unchanged |
| Protected value/evidence fingerprint mismatch or policy conflict | Excluded/error | Excluded/error |
| Disposition lookup failure | Error | Error |

Source scopes must protect manual imports from a measured-feed hold. Canonical
post-cutover evidence never overrides a TRUSTED hold. TRUSTED
`fetch_latest_timestamp` obeys the same holds because forecast freshness uses it;
BAFU's ignored `since` does not justify a raw freshness bypass. Preserve unaffected
DHM/other-source cursor behavior and test all cursor callers.

**QC and races.** Preserve current any-rule judgement: a range or baseline check
can yield QC_PASSED even if a temporal rule cannot judge. This does NOT release a
unit hold. Do not claim all rules ran or change aggregation semantics. At the unit
cutover, segment eligible context at known unit-uncertain boundaries; do not let
filtering bridge an uncertain interior interval into a fake continuous neighbour
pair. Preserve ordinary cadence/rule selection outside this scope; explicitly test
boundary segments, cadence inference and range-pass/temporal-unjudged cases.
Legacy RAW/UNCHECKED rows hidden by a unit hold deliberately await D2 recheck rather
than normal pickup. D2 still rechecks its approved QC-only neighbours.

QC_CONTEXT grants no numeric-write or hold-release authority. Introduce a narrow
conditional QC-update capability comparing the current value/source-provenance
fingerprint with the one evaluated. A mismatch writes no verdict, is counted as
stale evaluation and is re-read on an eligible later check; never report that write
as judged successfully. Apply this to affected ordinary QC and repair rechecks.
For a zero-valued legacy collision, numerical equality is not sufficient to retain
old QC: journal and reset/recheck if the evaluated unit/context semantics changed.
A pure evidence refresh with unchanged proven semantics need not reset QC.

**Roles, bypasses and activation.** Use a dedicated BAFU-unit maintenance principal;
`sapphire_operator` remains the constrained DHM delivery role, not implicit authority.
Migration/bootstrap review must implement this matrix:

| Principal | Allowed unit operations |
|---|---|
| API | SELECT safe current hold/canonical-fingerprint projection only |
| Normal worker | Safe hold/fingerprint/receipt reconciliation reads; append normal-ingest batches/events; no repair, release, UPDATE/DELETE/TRUNCATE of journal |
| BAFU-unit maintenance | Approved repair/rollback batches and explicit disposition changes; no journal rewriting |

Protect operation kinds by database principal, not merely Python enum values.
Exclude raw event/quarantine/before-image tables from bootstrap's blanket SELECT
and `api/routes/tables.py`'s table browser. Safe projections must expose neither raw
payloads nor before-images; normal reconciliation needs IDs/digests/counts, not an
unrestricted journal reader. Enforce append-only events/receipts, including DELETE
and TRUNCATE, consistent with existing protected-evidence conventions.

Protect scoped BAFU numeric writes with a deferred database constraint trigger
requiring an authorized matching unit event/current fingerprint at commit. Cover
INSERT, value/provenance updates and deletion of protected observations; QC-only
updates follow the conditional application capability above. Test transaction end,
not only individual statements. Both Python upserts, delivery mutation/deletion
paths and supported direct SQL writers must obey the database guard. Bind the guard
to approved source/station/time scopes, not all `measured` rows. This is integrity
protection against bypasses, not a claim that a compromised worker cannot forge
source assertions using its normal write authority. Confirm safe administrative
recovery and migration rollback paths before activation.

Activation order is migration → bootstrap/grants → real-role verification → all
hold-aware images/processes verified → approved holds and cutover. Check exact image
digests/source versions for API and workers; stop or disable obsolete CLI/tool
writers during cutover rather than assume every process advertises a version.
The work cannot activate under missing grants or a mixed-version read window.

**Read inventory and raw diagnostics.** Add `api/routes/stations.py`'s direct
observations/hindcasts numeric queries, `api/routes/tables.py`,
`services/onboarding.py`, `services/calculated_station_onboarding.py`,
`flows/run_forecast_cycle.py` freshness and `tools/observation_coverage_summary.py`
to the existing inventory. Admin status does not make a raw number canonical;
existing numeric endpoints apply TRUSTED holds rather than exposing held values
without unit labels. Enumerate constructors in `_db.py`, `api/deps.py`,
`cli/export_forecast_lab.py`, `cli/import_dhm_delivery.py`, the coverage tool and
provisional store. Keep the provisional water-level feature's existing isolation
and permissions; it is not evidence of a current BAFU discharge bypass.

Defer SOURCE_AUDIT consumer wiring to D2-tool. Its restricted diagnostics return an
explicit audit envelope with source-unit status/provenance, not bare Observation
values labelled m³/s. No general raw HTTP route is added by D1. Batch captures and
parameter diagnostics default to empty for DHM/replay result construction; legacy
canonical fixtures are not silently reinterpreted.

Per-reading canonical evidence for new BAFU rows is retained in this design for
atomic current-value binding, including identity conversions. Before activation,
measure event/receipt volume, deduplication, retention and disk headroom with the
real roster; do not infer acceptable capacity from small fixtures. No migration
or deployment until the owner accepts that bounded capacity budget. The nine
unknowns get individual availability/source decisions, not an unapproved blanket
history hold; list any new quarantine and derived-MISSING consequences explicitly.

### 3. Historical repair is a finite, audited transaction plan

**Owner-accepted legacy trust decision (2026-10-28):** preserve existing read
eligibility at the 134 current m³/s-display stations unless concrete contrary
evidence is found. Their pre-registry history is labelled legacy/unverified-unit
provenance, not newly certified; the owner accepted the residual risk that a past
source unit may have differed after the review handoff. This approves that scope,
not a live repair manifest, deployment, or upstream submission. Do not make presence
of a new evidence record a fleet-wide historical read gate. If contrary evidence
appears, stop for an explicit revised audit/withholding decision rather than silently
dropping the fleet.

Use a finite versioned station/parameter/time-range disposition for the five proven
l/s cases and any individually unresolved or contradictory source ranges. Trusted
readers use that explicit disposition (and canonical post-cutover evidence), not
`evidence_record IS NOT NULL` across all observations. No unit arithmetic or
reclassification is authorized for the nine unknowns without individual evidence.
List their actual availability changes for approval; missing discharge is not a
reason to fabricate a value or a presumed unit.

Build a read-only inventory first. Freeze exact tenant/station IDs, parameter,
source, timestamp ranges, row IDs, old values/QC/provenance, source evidence,
proposed new values and manifest checksum. Define two explicit write sets:
value-correction rows and QC-only dependent rows. Freeze old QC status, flags and
rule version for both. Both sets enter approval, compare-and-set checks, journaling
and rollback. Derive the QC-only set and read-only context from the actual selected
temporal rules, not a blanket two-hour window. Separate:
- demonstrably wrong measured LINDAS rows with period-specific l/s evidence;
- already-canonical manual/CAMELS-CH imports and any corrected rows;
- ambiguous rows within the explicitly affected/unresolved disposition that remain
  unchanged and unavailable as trusted model inputs until units are established.
  The accepted legacy exception above is separate and must not be silently widened.

`source=measured` alone is not sufficient proof. A current display does not prove
all earlier values used the same unit. Neither stored operational rows nor older
collector payloads necessarily contain unit metadata. Request historical unit
semantics from the publisher; preserve unresolved ranges, do not invent a cutover.
Bound the first repair to the active CMAL input window if historical evidence
supports that window; quantify wider impact separately rather than repairing all
history merely to finish the MVP.

Dry-run must report candidate/unchanged/ambiguous counts, per-station ranges,
conversion checks, impacted dependencies, resource bounds and expected row hashes.
Apply requires owner approval of the exact manifest, a verified repair-specific
backup/export and rollback rehearsal on a disposable copy. Checksum/TOC verification
alone is not a restore rehearsal. Do not use the nearly full live filesystem for
an unbounded database clone.

Application invariants:
- explicit BAFU tenant and station allowlist; no broad network-wide arithmetic SQL;
- compare-and-set on identity, old value and provenance; any unexpected change
  aborts the affected atomic batch;
- immutable before/after journal and unique repair identity; retries cannot divide
  twice; distinguish not-applied, applied and uncertain outcomes;
- preserve IDs/timestamps/source. There is no existing generic audited unit
  restatement path: implement the narrow manifest repair writer explicitly. Each
  batch's compare-and-set update, before/after journal and QC reset commit in ONE
  transaction. Do not delete and reinsert observations or call the raw upsert as
  an unaudited repair shortcut;
- stop conflicting ingest, forecast and training readers at a documented boundary,
  or prove equivalent locking. A corrected adapter must not overwrite an old row
  before the journal captures it. Coordinate first fixed ingest and manifest apply;
- re-run existing QC with unchanged rules/baselines and sufficient neighbouring
  context. A bounded normal ingest window does not recheck a repaired historical
  range automatically. Explicitly re-evaluate already-judged neighbours in the
  approved QC-only set; the normal pending-row filter otherwise skips them. Test
  a neighbour whose verdict changes and restoration of both value and QC-only rows;
- rollback verifies current post-repair state and restores recorded values/QC,
  never multiplies an arbitrary current value by 1000. Do not resume the old buggy
  adapter as an unrestricted rollback path.

## Dependency and scientific-impact audit

Trace measured inputs through resampling, calculated stations, aggregates,
climatological baselines, flow-regime configurations, scalers, training/artifact
provenance, warm-up state,
forecast evidence, forecast/hindcast outputs, verification scores and alerts.
Query active calculated-station formula dependencies for affected and unresolved
stations. Current-run derivation does not repair historical components: include
proven affected derived row IDs in the approved repair closure, recompute with the
existing `derive_point` logic, and journal/CAS/rollback those rows too. Unknown
component-unit ranges stay withheld, not silently recomputed.

Seed the trusted-reader inventory with `api/routes/api_stations.py`,
`services/forecast_lab/db_sources.py`, `store/provisional_discharge_store.py`,
`services/observation_alert_checker.py`, `services/training_data.py`,
`services/hindcast.py`, `services/operational_inputs.py`,
`services/track_assembly.py` and `flows/compute_skills.py`. Trace actual wrappers
and calculated-station consumers as well; a shared read gate must not accidentally
hide source-native inspection or bypass explicit tenant restrictions.

Read-only P1 check (2026-10-28): all 366 baseline rows for each of the five proven
l/s stations match reconstruction from QC-passed `manual_import` observations in
sample count, mean and population standard deviation (1,830/1,830 matches within
1e-10; maximum observed differences below 1.6e-16). This supports retaining those
baselines; repeat the check at repair preflight if their inputs/state change.
Baseline creation timestamps alone were not used as proof: the existing upsert
preserves them when values change.

Before re-QC, prove any additional relevant baselines are uncontaminated. Onboarding
can build baselines from QC-passed rows of any source; if affected measured inputs contributed,
block re-QC until the owner approves the baseline disposition. Apply the same
provenance gate to flow-regime configs before verification/scoring reuse. Unchanged
thresholds do not authorize reuse of contaminated reference statistics.

Keep frozen evidence immutable. Mark/invalidate affected derived products using
existing supported mechanisms; absence of a safe mechanism is a blocking design
finding, not permission to rewrite them silently.

Check the active CMAL artifact's actual training sources and date ranges. Its
August training date alone does not prove it was isolated from this defect. No
automatic retraining or blanket assertion that other 132 stations are unaffected
by GROUP execution. Verify group-roster effects with corrected inputs.

Existing completed daily summaries and exact output references remain immutable.
Do not manufacture a third daily attempt, backdate a run or replace a completed
attempt's values in place. Use the isolated one-shot path for comparison, and the
next genuinely eligible scheduled issue for operational acceptance. Assess whether
any existing outputs require withholding; owner approves that action and any
publication change before it is made.

## Phased delivery and exit gates

### P1 — Confirm source contract and minimal impact (read-only)
- Retain the 148-station matrix and resolve the nine unknowns separately.
- Capture exact-time source/page series pairs for l/s and m³/s controls; preserve
  raw precision, source times and retrieval times rather than rounded display truth.
- Confirm deployed code, station bindings, active baseline/artifact provenance,
  formula dependencies and current read paths for trusted observations.
- All five proven l/s stations are among today's six input exclusions; prove this
  against the frozen roster again at cutover. That coincidence is NOT a quarantine:
  QC-passed wrong values and other consumers still require protection.
- Finalize registry/refresh ownership, atomic evidence/journal persistence, reader
  inventory and withholding mechanisms. List newly withheld stations and derived
  dependents, including any of the nine previously usable unknowns. Owner accepts
  this impact before activation; do not infer acceptance from this plan.
- Keep the upstream draft unsent pending approval. Current source evidence can
  authorize forward conversion without proving historical validity.

### D1-build — Stop new corruption (independent of broad historical repair)
- Implement typed config, shared conversion, partial-parameter diagnostics,
  atomic unit evidence and audited same-key collisions. Wire operational adapter,
  replay/fakes and recorder; leave the source-native collector numeric format alone.
- Implement narrow withholding of unresolved/legacy unit-uncertain discharge across
  the identified trusted consumers, not merely an informational log. Do not repurpose
  QC suspicion to encode unit uncertainty or silently change model inputs.
- D1 interim policy: converted new l/s readings may be stored, but affected discharge
  remains unavailable to forecasts/warm-up and other trusted numeric consumers until
  the required history/context is canonical. Do not mix 0.022 with legacy 22 in QC
  temporal context: only canonical context is eligible. Individual temporal checks
  lacking context do not judge; existing any-rule row aggregation is unchanged,
  so another rule may still produce QC_PASSED. That never unblocks consumers. D1 does not repair historical temporal context. Withholding until D2
  is the only standalone D1 route.
- For verified m³/s entries, numbers remain unchanged. Resolve same-timestamp
  collisions explicitly: LINDAS provides only the latest reading, so inventory the
  current eligible collision rows at the paused cutover rather than reconstructing
  an unbounded event stream. New historical restatement evidence remains out of scope.
- Regression tests and independent reviews cover D1 on its own; no dependency on
  a full-history repair tool merely to stop new corruption.

### D1-activate — Rehearse and release the boundary fix
- Rehearse the small collision manifest and withholding/QC-context behavior on a
  disposable copy; verify atomic evidence, bit-identical re-polls and rollback.
- Owner approves exact collision rows, interruption window and withholding list.
- Pause conflicting writers/readers, retain scoped backups and rollback identities.
  Verify every active trusted reader/writer process (API and all applicable workers)
  runs the hold-aware version before activating holds; old processes ignore the new
  policy. No mixed-version window may claim isolation. Journal/apply approved
  collisions and activate fixed ingest in the rehearsed order.
  Resume only reconciled paths; do not resume unrestricted buggy ingest on rollback.
- Verify fresh l/s conversion, unchanged m³/s controls/other parameters and visible
  partial-parameter health. No historical repair or restored station coverage claim.
- Verify the next eligible scheduled daily attempt immediately after D1, independently
  of D2: produced/excluded counts versus the approved withholding list, GROUP roster
  behavior, exact persisted outputs and authenticated API labels/authority. Unexpected
  loss or silent exclusions stop acceptance and trigger the agreed rollback/hold
  procedure. This D1 acceptance cannot wait for uncertain historical repair.

### D2-plan — Finite historical repair evidence (may run alongside D1 work)
- Inventory candidate/canonical/ambiguous histories and affected derived rows;
  freeze the two QC write sets and any derived-value closure described above.
- Prove period-specific source units, baseline validity and artifact/state impact.
- Prefer the minimum active CMAL/history/QC window; wider historical remediation
  stays separately scoped. Unproven ranges do not block D1's safe withholding route.

### D2-tool — Implement and rehearse manifest repair
- Implement dry-run and finite transactional apply/rollback with the shared converter,
  immutable journal, CAS, idempotency and uncertain-acknowledgement handling.
- Rehearse value/QC-only/derived closure, conflict abort, crash recovery and restoration
  on a disposable copy. Full-database live clones are not required.
- Owner approves the exact manifest, dependency dispositions and restoration evidence.

### D2-apply — Restore trusted history under controlled writes
- Require fixed D1 ingestion and all D2 gates. Pause conflicting work, apply only
  approved rows, re-QC with validated baselines and full rule-specific context,
  reconcile derived outputs and verify rollback receipts.
- Unblock affected consumers only once their required histories/state are verified.
  Do not force Cavergno or another sparse/stale source to become forecastable.

### Final-acceptance — Verify restored scheduled delivery
- Use the approved isolated one-shot comparison before restoring affected daily
  coverage; no extra/backdated daily attempts, silent output replacement or skill claim.
- Verify the next eligible scheduled attempt, exact native-quantile rows and
  authenticated API labels/authority; continue seven unattended daily checks.
- Preserve residual exclusions and source gaps. Remote access remains separate.

**Both deliveries' release gates:** independent Claude and Codex plan/patch reviews,
plus the owner-commissioned scientific/data-repair review required for high-risk
work; focused checks, full final-code test suite, lint, formatting, changed-module
types, owner-merged PR and current release/deployment procedures. Diagnosis review
is not plan/patch approval. Historical value/context repair follows D2's explicit
gates and dependency graph; there is no alternative combined cutover shortcut.
D1's narrowly inventoried latest-reading collisions remain covered by its own
atomic writer, journal and owner-approved collision manifest, not a broad repair.

```json
{
  "phases": [
    {"id": "P1", "depends_on": [], "parallel": false},
    {"id": "D1-build", "depends_on": ["P1"], "parallel": true},
    {"id": "D1-activate", "depends_on": ["D1-build"], "parallel": false},
    {"id": "D2-plan", "depends_on": ["P1"], "parallel": true},
    {"id": "D2-tool", "depends_on": ["D2-plan"], "parallel": false},
    {"id": "D2-apply", "depends_on": ["D1-activate", "D2-tool"], "parallel": false},
    {"id": "Final-acceptance", "depends_on": ["D2-apply"], "parallel": false}
  ]
}
```

## Tests and routing

Use the [LINDAS/BAFU touchpoint map](../touchpoint-maps.md#touchpoint-map-lindas--bafu-observation-ingest)
and its QC, replay, recorder, DHM and health regression lists as routing authority.
Primary code: `adapters/lindas_hydro_query.py`, `adapters/hydro_scraper.py`,
`adapters/bafu_observation.py`, `types/observation.py`,
`flows/ingest_observations.py`, `flows/collect_bafu_observations.py`,
`store/observation_store.py`, and their existing tests. Consult
[WMO](../standards/wmo.md), [security](../standards/security.md),
[logging](../standards/logging.md), [orchestration](../standards/orchestration.md)
and [CI/CD](../standards/cicd.md) for their affected boundaries.

Required cases: 22 l/s → 0.022 m³/s; 0.135 l/s → 0.000135 m³/s even when the old
number passed QC; 17.645 m³/s remains 17.645 despite `isLiter=true`; unknown/conflicting
unit, out-of-period evidence, overdue refresh warning without fleet withholding,
malformed metadata and duplicate/conflicting bindings; mixed stations/parameters; lake/weather isolation; no double conversion
on replay or retry; unchanged manual/CAMELS/DHM/derived inputs; changed-same-time
upsert is audited; finite manifest isolation; partial failure and rollback; QC
neighbour context and QC-only rollback; unchanged source-native collector behavior;
partial-discharge outcomes retaining other parameters without counting a station as
wholly failed; recorder registry wiring; calculated-station component correction;
bit-identical same-literal re-ingest after repair; no mutation of daily authority or
frozen evidence; correct GROUP/FI/API behavior.

Canonical documentation changes at implementation: update the existing LINDAS map,
adapter/protocol and configuration references, relevant archive/read contracts,
and the operational runbook. Add only a dated correction pointer to Plan 029.

## Draft report to LINDAS / BAFU publisher — NOT SUBMITTED

**Subject:** Clarification needed: `isLiter=true` on both l/s and m³/s discharge observations

We ingest the public `https://lindas.admin.ch/foen/hydro` graph through
`https://lindas.admin.ch/query`. On 2026-10-28 we compared its observations with
BAFU's official station pages. The predicate `http://example.com/isLiter` appears
as the literal `true` for stations displaying either unit:

| Station | LINDAS time (UTC+01) / discharge | Official display | Flag |
|---|---|---|---|
| 2206 Melera | 09:10 / 22.000 | 09:20: 22 l/s; 24h range 21–22 l/s | true |
| 2252 Plaffeien-Schwyberg | 09:00 / 0.135 | 09:00: 0.135 l/s | true |
| 2044 | 09:10 / 17.645 | 09:20: approximately 18 m³/s | true |

The display is rounded and not every sample is simultaneous; the unit labels are
explicit. Across 148 configured river stations, five displayed l/s and 134 m³/s;
133 of the m³/s-display stations carried `isLiter=true`; station 2602 (Domat/Ems)
had no flag in this snapshot. All five l/s-display stations carried `true`.
The flag binding was `{"type": "literal", "value": "true"}`, with no datatype
field, not a typed boolean in the captured SPARQL JSON. Nine pages had no current
discharge table and are not classified. This is a single-day snapshot, not a
historical comparison. In particular, the 2044 raw/page samples are ten minutes
apart and the displayed value is rounded. These counts concern our roster, not a
census of all LINDAS gauges. We have not inferred the semantics of missing flags.

Could you confirm:
1. What does `http://example.com/isLiter` mean? Is it intended to describe the
   numeric discharge unit, a presentation preference, or something else?
2. Are `dimension/discharge` literals in a uniform unit or station-specific units?
   Which machine-readable field is authoritative for the numeric unit?
3. If this metadata is incorrect, can you provide a correction and the affected
   stations/date ranges? Have station units or this flag's meaning changed over
   time? If so, from when?
4. How should consumers detect future unit changes and interpret historical values?

Reproduction (read-only, three stations, no credentials):
```sparql
SELECT ?subject ?predicate ?object
FROM <https://lindas.admin.ch/foen/hydro>
WHERE {
  VALUES ?subject {
    <https://environment.ld.admin.ch/foen/hydro/river/observation/2206>
    <https://environment.ld.admin.ch/foen/hydro/river/observation/2252>
    <https://environment.ld.admin.ch/foen/hydro/river/observation/2044>
  }
  ?subject ?predicate ?object .
  FILTER (?predicate IN (
    <https://environment.ld.admin.ch/foen/hydro/dimension/discharge>,
    <https://environment.ld.admin.ch/foen/hydro/dimension/measurementTime>,
    <http://example.com/isLiter>
  ))
}
LIMIT 30
```

Official comparison pages:
- https://www.hydrodaten.admin.ch/en/seen-und-fluesse/stations/2206
- https://www.hydrodaten.admin.ch/en/seen-und-fluesse/stations/2252
- https://www.hydrodaten.admin.ch/en/seen-und-fluesse/stations/2044

We can supply timestamped sanitized responses. The query reproduces the query
shape; only the dated saved snapshots reproduce the historical numbers above.
We are asking for the source contract, not assigning fault between the publisher
and LINDAS platform. Please route this to the responsible
BAFU data publisher if appropriate.

Before sending: verify the official contact channel, freeze the cited public
snapshots with hashes, and obtain owner approval. Do not include credentials,
internal database identifiers, deployment addresses or private operational logs.
