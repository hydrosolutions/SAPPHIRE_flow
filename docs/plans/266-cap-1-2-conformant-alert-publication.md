---
status: DRAFT
created: 2026-09-10
plan: 266
title: CAP 1.2-conformant hydrological alert publication
priority: low
reviews: []
open_decisions:
  - CHWRR must approve the later CAP hazard-specific event/text/area mappings for both high- and low-flow warnings before ACTUAL enablement. CAP is disabled for the CHWRR MVP.
related: [041, 147, 341, 340, 342]
depends_on: [341, 342]
scope: Add an opt-in OASIS CAP 1.2 Message Producer and an authenticated WMO-1109-aligned CAP source/RSS feed for station-scoped hydrological alerts, backed by an immutable issuance history. Preserve the existing internal alert engine. NOT a CAP consumer, NOT a public unauthenticated feed, NOT WIS 2.0, NOT notification-channel delivery, NOT impact modelling, NOT pipeline-alert publication, and NOT a claim that SAPPHIRE or a deployment operator is legally recognized as an alerting authority.
source: 2026-09-10 — owner requested a low-priority plan to bring SAPPHIRE Flow into CAP conformance after a repository-grounded gap assessment found an internal alert lifecycle but no CAP message model, serializer, issuance history or feed.
---

# Plan 266 — CAP 1.2-conformant hydrological alert publication

## Status

**DRAFT — NOT READY. Low priority.** This is an external-facing, user-visible warning
contract and a database/API change. Resolve its independent-review findings and follow the
high-risk review rules in `docs/workflow.md` before the orchestrator may set it READY.

**MVP decision (owner, 2026-09-24):** CAP remains `DISABLED` for the CHWRR MVP dashboard and initial
post-event workflow. This plan is a later optional publication path; the MVP must not depend on its
implementation. Reconcile its open review findings before any later CAP enablement.

Its narrow target is technical **CAP 1.2 Message Producer**
conformance plus the CAP source/feed steps from WMO-No. 1109. It deliberately does not claim that
technical conformance makes a deployment an authorized public-warning originator; that requires
an external authority decision and the enablement gate in T7.

## Authorities

- **OASIS Common Alerting Protocol Version 1.2**, especially §3 (message structure) and §4.3
  (Message Producer conformance):
  <https://docs.oasis-open.org/emergency/cap/v1.2/CAP-v1.2-os.html>
- **WMO-No. 1109, Guidelines for Implementation of CAP-Enabled Emergency Alerting**, especially
  §§4.5–4.8 (a CAP source, feed, authority registration and hosting):
  <https://etrp.wmo.int/pluginfile.php/17980/mod_resource/content/1/wmo_1109_en.pdf>
- Project authority: `docs/v0-scope.md`, `docs/architecture-context.md`,
  `docs/spec/types-and-protocols.md`, `docs/standards/wmo.md`,
  `docs/standards/security.md`, and `docs/standards/logging.md`.

CAP is an OASIS message standard. WMO-No. 1109 is implementation guidance for a CAP-enabled
alerting system; it is not a second wire format. This plan targets OASIS CAP 1.2 and uses WMO-1109
to define the bounded dissemination work around it.

## Problem

SAPPHIRE has a working internal threshold-alert projection:

- `Alert` carries an internal UUID, station, source, local danger level, trigger evidence and the
  lifecycle `raised | acknowledged | resolved` (`types/alert.py`).
- forecast and observation services upsert one active row per
  `(station_id, alert_level, source)` and resolve it when the threshold no longer holds
  (`services/alert_checker.py`, `services/observation_alert_checker.py`,
  `store/alert_store.py`);
- `GET /api/v1/alerts` emits application JSON (`api/routes/api_alerts.py`).

That is not a CAP message model:

1. CAP requires `identifier`, globally unique `sender`, `sent`, CAP `status`, `msgType` and
   `scope`. The existing similarly named fields have different meanings: SAPPHIRE `status` is an
   internal lifecycle, and SAPPHIRE `source` is `forecast | observation | pipeline`, not a CAP
   sender.
2. A CAP warning `info` block requires `category`, `event`, `urgency`, `severity` and
   `certainty`. SAPPHIRE has no explicit mapping from its five configurable danger levels and
   probabilistic/observed evidence to those CAP code sets.
3. CAP updates and cancellations are new immutable messages with new identifiers and
   `references` to earlier messages. SAPPHIRE mutates one active alert row in place.
4. A `station_id` is not a CAP affected area. The station point and upstream basin geometry are
   useful hydrological metadata, but neither is automatically the downstream warning area.
5. There is no CAP XML serializer, schema validation, CAP media-type endpoint, message source or
   feed. `docs/standards/wmo.md` correctly records CAP as deferred.

A serializer directly over `Alert` would therefore produce syntactically plausible XML while
losing the message identity, authority, geography and update/cancel semantics CAP exists to
standardize. This plan adds a publication boundary instead.

## Outcomes and non-claims

After implementation, when CAP mode is enabled and its deployment policy passes preflight:

- every stored and served CAP document is an XML 1.0 CAP 1.2 message that validates against the
  pinned official XSD and satisfies the additional §3 rules tested in T2;
- an initial warning, semantic update and cancellation form an immutable, referentially correct
  CAP chain;
- authenticated API consumers can retrieve individual CAP documents and an RSS feed without
  escaping their existing station scope;
- repeated flow execution with unchanged warning semantics emits no duplicate CAP message;
- with CAP disabled (the default), existing alert rows, flow results and API responses are
  unchanged.

This permits the software to claim **“CAP 1.2 Message Producer”** after the conformance tests pass.
It does **not** permit **“operational WMO CAP-enabled public alerting authority”** until an operator
has completed T7's external enablement checklist. CAP consumer conformance is out of scope because
SAPPHIRE does not ingest external public warnings.

## Locked design

### D1 — Internal alerts remain candidates; CAP messages are publications

Do not add CAP fields to `Alert` and do not reinterpret `AlertStatus` or `AlertSource`. The current
record remains a mutable internal decision-support projection. New frozen CAP domain types and new
persistence own the externally issued message.

Pipeline alerts are excluded. A pipeline failure is an operations signal, not a hydrological public
warning, and publishing it would also create a recursive failure path when CAP publication itself
fails.

### D2 — Producer target only

Implement OASIS §4.3 **CAP V1.2 Message Producer**. Do not add a CAP parser, CAP consumer Protocol,
foreign-warning table, EDXL, WaterML or WIS 2.0 integration. The serializer accepts only typed
internal CAP values; it never accepts or round-trips untrusted XML.

### D3 — Opt-in operating mode and distribution scope

`CapOperatingMode` is an enum: `DISABLED`, `TEST`, `ACTUAL`. Default is `DISABLED`.

- `TEST` emits CAP `<status>Test</status>`.
- `ACTUAL` emits CAP `<status>Actual</status>` and is rejected by publication preflight unless every
  required authority, local-level, text and area mapping passes.
- Both modes emit `<scope>Restricted</scope>` with a required non-empty `<restriction>` and are
  served behind the existing bearer-token authentication. No route is added to the public health
  exemption, and no `Public` or `Private` scope is implemented in this plan.

Using a restricted source is explicitly compatible with WMO-1109's incremental implementation
path. A public unauthenticated CAP source would change the repository's security boundary and gets
its own later decision.

`TEST` may exercise synthetic or human-published forecast and observation warning records. `ACTUAL`
accepts only human-PUBLISHED warning decisions from Plan 342, regardless of source. A published
forecast's selected-forecast threshold recheck must supply forecast warning provenance. Raw
forecast/observation candidates and mutable active `alerts` rows cannot issue CAP. Keep `ACTUAL`
disabled for CHWRR until Plans 341 and 342 are implemented and this plan's enablement checks pass.

A database-aware transition guard rejects `TEST`↔`ACTUAL`, enabled→`DISABLED`, sender removal or
station-scope removal while an affected CAP incident remains open. T4 supplies a controlled
operator cancellation command so the old mode/policy can issue referentially correct Cancels before
the deployment changes. Configuration changes must never silently orphan an issued warning.

### D4 — One CAP incident per station, parameter and direction

The alert checker may raise several candidate levels for a station. The CAP reconciler keys one
incident by the Plan 342 human-warning incident identity `(station_id, parameter, direction)`, so
simultaneous high/low or discharge/water-level warnings keep separate chains. Within each key it
selects from human-published, currently valid decisions across the two policy-eligible sources
(`forecast`, `observation`):

1. select the current human-published warning with the greatest
   `DangerLevelDefinition.display_order`;
2. on a tie, prefer observation evidence over forecast evidence;
3. if no open CAP incident exists, emit `msgType=Alert`;
4. if the selected CAP semantic state differs from the open incident, emit `msgType=Update` and
   reference its immediately preceding CAP message;
5. if no human-published warning remains current after audited withdrawal, replacement or expiry,
   emit `msgType=Cancel`, reference the preceding message and close the incident;
6. if the semantic state is unchanged, emit nothing.

The semantic fingerprint includes parameter/direction, selected level, mapped CAP codes,
authority/policy version, message text, affected area and the human-approved `effective`/`expires`
window. It excludes internal processing timestamps, UUIDs and numeric probability movement that
does not change CAP certainty. Thus a 0.61→0.64 forecast is a no-op, while a local level change,
same-level validity renewal, `Possible`→`Likely`, area/text policy change, or observed evidence
taking precedence is an Update. A delayed expiry reconciler cannot make an expired warning current
again; consumers see the stored `<expires>` time even before a later Cancel arrives.

### D5 — Explicit, typed CAP semantics; no name guessing

Deployment policy is keyed by tenant because the host is multi-tenant. The deployment CAP policy
provides the canonical external source base URL used in feed links; `ACTUAL` requires HTTPS, while
`TEST` permits HTTP only for a loopback host. An enabled tenant policy provides a globally unique
sender, sender name, policy version, restriction, event text, message text, urgency-by-source and
an explicit non-empty set of eligible published-warning sources, hazard-specific
`(parameter, direction)` event/text/area mappings for both ABOVE and BELOW, plus an exhaustive
mapping from every configured local danger level to CAP severity. Missing high- or low-flow
hazard mappings fail closed. Configuration loading converts Pydantic boundary models into
frozen domain values; `pipeline` is never a legal CAP candidate source.

The initial mapping rules are:

| CAP field | Source |
|---|---|
| `identifier` | generated by the injected ID factory; legal CAP token, unique for the sender |
| `sender` / `senderName` | tenant authority policy; never `Alert.source` |
| `sent` | injected UTC clock, serialized with numeric `-00:00`, never `Z` |
| `status` | `CapOperatingMode.TEST` or `ACTUAL` |
| `msgType` | incident transition: `Alert`, `Update`, `Cancel` |
| `scope` / `restriction` | fixed `Restricted` plus configured restriction text |
| `category` | configured CAP category; hydrological default/example is `Met` |
| `event` | configured authority text for the warning parameter/direction; `Flood` is an ABOVE example only |
| `urgency` | explicit configured mapping for forecast and observation evidence |
| `severity` | exhaustive local danger-level mapping; never inferred from the level name or colour |
| `certainty` | observation → `Observed`; forecast `p > 0.5` → `Likely`; `0 < p <= 0.5` → `Possible`; missing/out-of-range forecast probability is an error |
| `headline`, `description`, `instruction` | fixed application formatting over configured parameter/direction policy text and station identity; no Jinja/general template engine |
| `effective` / `expires` | human warning publication time / immutable `valid_through`, in CAP time format; no expiry inferred from a later reconciliation run |
| `area` | explicit station and parameter/direction warning-area policy from D6 |
| `references` | previous stored CAP message's `(sender, identifier, sent)` triple for Update/Cancel |
| `note` | configured automatic-cancellation text or a bounded operator reason for an administrative Cancel |

CAP enums use their exact standard values at the XML boundary. DB values may use lower-case project
conventions, but conversion is explicit and tested. An enabled policy that omits a configured local
danger level fails closed before any message is issued.

### D6 — Warning areas are explicit authority data

The CAP-enabled warning-hazard set is exactly the area-policy entries keyed by
`(tenant_code, network, station_code, parameter, direction)`. Publication preflight resolves every
entry to exactly one live station and configured parameter/direction owned by that tenant; a
missing, ambiguous or cross-tenant match blocks the enabled policy. Each entry contains a
non-empty `areaDesc` and may additionally provide an authority
geocode as a typed `(valueName, value)` pair and/or a WGS84 polygon. Polygon parsing occurs once at
the configuration boundary and enforces the CAP ring rules (at least four coordinate pairs, closed
ring, latitude then longitude on output).

There is deliberately no automatic fallback to `stations.location` or `basins.geometry`. A gauge
point is not an affected area, and an upstream catchment is generally the opposite side of the
station from the downstream flood-warning area. Warnings for station/hazard keys outside the explicit set stay
internal and are counted/logged as outside CAP scope; an invalid entry in either Test or Actual
mode fails closed rather than issuing a geographically misleading warning.

### D7 — Mutable incident projection, immutable publication ledger

Use two new tables:

- `cap_incidents`: one current lifecycle projection per warning incident, with
  station/tenant/parameter/direction, open/closed state, policy sender/version, last semantic
  fingerprint, opened/closed timestamps and monotonic sequence. A partial unique index permits at
  most one open incident per `(station_id, parameter, direction)`.
- `cap_messages`: append-only issued messages, with incident + sequence, CAP identifier/sender/sent,
  status/type/scope, previous-message reference, exact `payload_xml` bytes, SHA-256, and creation
  time.
  Unique constraints cover `(sender, identifier)` and `(incident_id, sequence)`.

`cap_messages` has role-independent UPDATE/DELETE/TRUNCATE rejection triggers, mirroring
`audit_log`. It has no FK to `alerts`: resolved internal alerts are deleted after 90 days, while CAP
publication history is permanent. It stores the durable Plan 342 warning-publication decision ID
and source evaluation ID as provenance; an internal alert UUID may be auxiliary only. Station and
tenant relationships remain enforced through `cap_incidents`.

Reconciliation locks the warning-incident key, reads current human-published warning decisions and
that key's open CAP incident, then updates/inserts the incident and appends its message in one real
PostgreSQL transaction. Production
flows currently hold AUTOCOMMIT store connections, so this must use a transaction-owning writer
(`engine.begin()`), not two writes through the existing store connection. A failed message append
must leave no opened, advanced or closed incident. Injected fake writers cover pure flow tests; a
real-PostgreSQL rollback test proves atomicity.

### D8 — Stored bytes are the publication

Serialize and XSD-validate before the transaction appends a message. Persist the exact XML bytes in
a binary column plus a digest; retrieval returns those stored bytes and never regenerates from
current config. Policy changes therefore cannot rewrite history. Update/Cancel messages are newly
serialized and stored publications.

### D9 — Authenticated CAP source and RSS discovery feed

Add read-only routes under the existing `require_principal` boundary:

- `GET /api/v1/cap/messages/{message_id}` → exact stored XML,
  `Content-Type: application/cap+xml; charset=utf-8`;
- `GET /api/v1/cap/feed.rss` → recent message metadata ordered by `(sent DESC, id DESC)`, links to
  the individual CAP resources, `Content-Type: application/rss+xml; charset=utf-8`.

The feed is discovery, not a second representation of the CAP payload. Station scoping is applied
inside the store query before count/order/limit, exactly like the corrected alert API. A consumer
cannot retrieve or discover a message for an out-of-scope station; an admin can read all. No write
route is added.

### D10 — Conformance schema is pinned and tests are offline

Vendor the official CAP 1.2 XSD and every schema import needed to validate it under
`src/sapphire_flow/resources/cap-1.2/`, with source URL, license notice and SHA-256 provenance, and
include them in built package data. Tests and runtime never fetch standards from the network. Add
the minimum runtime dependency needed for XSD validation with `uv`: D8 requires every generated
document to validate before storage. The application exposes no external CAP parse/validation API
and handles only XML it generated itself.

### D11 — Signatures and downstream delivery remain out of scope

XML Signature is optional in CAP 1.2 and is not added. Existing HTTPS and bearer authentication
protect the restricted pull source. Email, SMS, webhooks, sirens, push retries, public RSS, WMO
Register submission and downstream consumer certification are separate operational/integration
work. T7 records them without pretending the software can complete an authority registration.

## Tasks

### T1 — CAP domain and fail-closed deployment policy

**Outcome.** Frozen CAP value types and enums represent only the supported producer state; config
can be disabled, Test or Actual; enabled policy is keyed per tenant/station and rejects incomplete,
ambiguous or CAP-illegal sender, candidate-source, severity, urgency, text and area data before a
flow runs.

**In.** `src/sapphire_flow/types/enums.py`, `types/ids.py`, new `types/cap.py`,
`config/deployment.py`, `config.toml`, `docs/spec/config-reference.toml`, focused type/config tests.
The checked-in default contains only disabled mode; the config reference carries non-authoritative
examples, not a real DHM sender or warning policy.

**Out.** No database work; no serializer; no change to `Alert`, existing danger-level semantics,
alert enablement defaults or tenant ownership. No raw polygon string past the Pydantic boundary.

**Verification.** `uv run pytest tests/unit/types/test_cap.py tests/unit/config/test_cap_config.py`
proves disabled-by-default backward compatibility, exact enum/value conversion, exhaustive
danger-level and high/low hazard mapping coverage, tenant/station/hazard-key uniqueness,
restriction/text requirements, sender token rules, canonical-source URL rules, polygon
closure/order, rejection of `pipeline`, rejection of Actual for unpublished/unrechecked forecast
evidence, and acceptance of a human-approved selected-forecast warning in Actual mode.

**Pre-change.** RED: loading an otherwise valid `[cap]` policy cannot produce a typed CAP policy
because no field/domain type exists; specifically assert the enabled policy accessor and its
fail-closed coverage checks rather than only checking that Pydantic accepts extra TOML.

### T2 — Deterministic CAP XML serializer and conformance harness

**Outcome.** A pure serializer converts `CapMessageContent` to deterministic CAP 1.2 XML in schema
order, without raw XML concatenation. Its output validates offline against the pinned official XSD
and the additional CAP §3 constraints.

**In.** New `src/sapphire_flow/services/cap_xml.py`; official schemas under
`src/sapphire_flow/resources/cap-1.2/` with provenance README and an installed-package resource
test; `pyproject.toml` + `uv.lock` changed only through `uv add <validator>`;
`tests/unit/services/test_cap_xml.py` and `tests/conformance/test_cap_v1_2.py`.

**Out.** No CAP input parser/consumer or external validation endpoint; no XML signature; no
database/API/flow work; no network call in tests or runtime.

**Verification.** `uv run pytest tests/unit/services/test_cap_xml.py
tests/conformance/test_cap_v1_2.py` validates Alert, Update and Cancel documents, references,
required info/area fields, exact namespaces and code values, schema element order, XML escaping,
Unicode, deterministic bytes/digest, and CAP timestamps using `-00:00` rather than `Z`. A negative
fixture missing each mandatory root/info field must fail the conformance harness, so a test that
merely parses well-formed XML is insufficient.

**Pre-change.** RED: no project function can emit an XML document in the CAP namespace, and the
conformance test cannot obtain a SAPPHIRE-produced message to validate.

### T3 — Incident/message schema, append-only store and transaction writer

**Outcome.** The database can atomically maintain the mutable CAP incident projection and append
immutable publication messages; scoped readers return exact stored payloads; the production worker
has only the required mutation grants and the API role remains read-only.

**In.** The next free Alembic revisions at implementation time (table/constraints first,
append-only triggers second); `src/sapphire_flow/db/metadata.py`; new `store/cap_store.py` and store
Protocol; `flows/_db.py`, `api/deps.py`; `tests/fakes/fake_stores.py`; `docker/bootstrap-roles.sql`;
`tests/integration/db/test_cap_migration.py`, `tests/integration/store/test_cap_store.py`, and the
existing role-bootstrap tests.

**Out.** Do not change `alerts` or its 90-day resolved-row cleanup. No UPDATE/DELETE API for
`cap_messages`. No payload regeneration on read. Do not hardcode revision `0056`; another READY
plan may take it first.

**Verification.** Focused integration tests prove:

- at most one open incident per station/parameter/direction and monotonic unique sequences;
- Update/Cancel require a preceding message in the same incident and produce the correct stored
  reference triple;
- `payload_xml` readback and SHA-256 match exactly;
- UPDATE, DELETE and TRUNCATE on `cap_messages` fail even as table owner;
- an injected append failure rolls back the incident open/advance/close mutation;
- `sapphire_worker` can SELECT incidents/messages, INSERT messages, and INSERT/UPDATE incidents but
  cannot mutate/delete message history; `sapphire_api` can SELECT but cannot write either table;
- scoped reads filter on station before limit/offset.

Commands: `uv run pytest tests/integration/db/test_cap_migration.py
tests/integration/store/test_cap_store.py tests/integration/db/test_role_bootstrap.py`.

**Pre-change.** RED: selecting `cap_incidents`/`cap_messages` at Alembic head fails because neither
table exists; an alert row cannot retain an immutable CAP update/cancel chain.

### T4 — Idempotent CAP reconciliation and issuance

**Outcome.** A typed CAP publication service implements D4–D8, selects one warning state per
station/parameter/direction,
maps it through the tenant policy, serializes it, and atomically publishes Alert/Update/Cancel or a
no-op. A narrowly scoped operator command can cancel named/all open incidents under the still-active
policy before a guarded configuration transition. All clocks and ID factories are injected.

**In.** New `src/sapphire_flow/services/cap_publication.py` and transaction-owning writer; existing
station/tenant lookup as a publication-preflight dependency; a `cancel-cap-incidents` CLI using the
existing config-declared write principal and audit conventions, shipped as
`python -m sapphire_flow.cli.cancel_cap_incidents`; focused unit and PostgreSQL integration tests;
only the minimum store Protocol additions needed by this service.

**Out.** No flow wiring (T5); no API (T6), especially no remote cancellation endpoint; no direct
`datetime.now()`, `uuid4()` or random call in business logic; no notification adapter; no
publication of pipeline alerts; no mutation of source alerts.

**Verification.** `uv run pytest tests/unit/services/test_cap_publication.py
tests/integration/services/test_cap_publication.py` covers initial human-published warning, unchanged retry,
level escalation/de-escalation, simultaneous high/low and discharge/water-level chains, approved
hazard-specific text/area, forecast certainty boundary, observation tie precedence,
Update/Cancel references, same-level validity renewal, expiry in stored XML even when Cancel is
delayed, close then later new incident, policy-fingerprint update, missing area or
mapping fail-closed, zero/ambiguous/cross-tenant station-policy resolution, sender/mode change during
an open chain rejected, guarded disable/scope removal, operator cancellation without changing the
internal warning publication, authorized-and-audited CLI use, concurrent reconciliation serialized per station,
and transaction rollback/retry without duplicate messages.

**Pre-change.** RED: with a human-published hydrological warning and a complete CAP policy, no CAP
incident or message is created today. The first test asserts the absent publication, not merely
the absent service symbol.

### T5 — Human warning publication integration

**Outcome.** After a committed human warning publish, replacement or withdrawal decision, reconcile
CAP for the affected enabled station/parameter/direction keys. A bounded expiry reconciliation closes incidents whose
published warning has expired. Both forecast- and observation-sourced decisions require the same
human gate; existing alert-check flow behavior is identical when CAP is disabled.

**In.** Plan 342's publication decision/event store, transaction-owning CAP writer, bounded expiry
reconciliation deployment or task, and focused unit/integration tests.

**Out.** Do not publish CAP before the human decision commits. Do not make CAP responsible for
raising/resolving internal alerts. Do not turn a CAP failure into rollback of stored observations,
forecasts or alert decisions. CAP is downstream of the warning-publication ledger, not the raw
forecast/observation check phases.

**Failure contract.** A CAP failure is logged as `cap.publication_failed` with station/tenant and a
sanitized reason, and leaves a retryable publication event or expiry task visibly failed rather
than reporting success. Retry is idempotent. It never emits a pipeline CAP message.

**Verification.** Focused service/flow tests prove enabled Alert/Update/Cancel and retry paths from
human warning decisions and expiry, that out-of-scope station/hazard keys stay internal, that raw forecast and
observation candidates cannot issue Actual CAP, and that disabled mode produces the same alert
rows/result counts with zero CAP writer calls. Existing direct injection tests must not acquire a
database dependency.

**Pre-change.** RED: a committed human warning decision has no CAP publication consumer today.

### T6 — Scoped CAP message endpoint and RSS feed

**Outcome.** Authenticated consumers retrieve exact stored CAP XML and discover recent messages via
RSS, with existing station scope enforced inside the database query.

**In.** New `src/sapphire_flow/api/routes/api_cap.py`; router inclusion in `api/__init__.py`;
minimal feed response/schema helpers; API unit tests and real auth/scope integration tests.

**Out.** No POST/PATCH/DELETE route; no unauthenticated route; no OpenAPI re-enablement; no embedded
CAP payload in RSS; no JSON substitute for the individual CAP resource; no regeneration from the
current alert/config.

**Verification.** `uv run pytest tests/unit/api/test_api_cap.py
tests/integration/api/test_cap_auth.py tests/unit/api/test_security.py` proves media types, exact
payload bytes, stable RSS ordering/links, 401 without a key, admin access, consumer in-scope access,
404 without existence disclosure for an out-of-scope message, and feed filtering before limit. The
route inventory test must continue to prove that `/api/v1/health` is the sole public endpoint.

**Pre-change.** RED: both CAP GET routes return 404; the existing alert JSON endpoint cannot return
CAP XML or a CAP discovery feed.

### T7 — Documentation, conformance evidence and operational enablement gate

**Outcome.** The single sources of truth describe the exact technical claim, configuration,
lifecycle, API, security boundary, logging, persistence and operator steps; CAP moves from
“deferred” to “verified” in the WMO gap table only with named runnable evidence.

**In.** `docs/standards/wmo.md`, `docs/architecture-context.md`,
`docs/spec/types-and-protocols.md`, `docs/spec/database-schema.md`,
`docs/spec/config-reference.toml`, `docs/standards/security.md`,
`docs/standards/logging.md`, `docs/standards/cicd.md`, `docs/v0-scope.md`, and the relevant API/handover
documentation.

**Out.** Do not claim CAP consumer conformance, public dissemination, XML signatures, WMO/WIS
registration, impact-based warning capability, legal authority or successful downstream
interoperability testing. Do not move the WMO gap on plan status alone.

**Verification.** Bounded inspection confirms that the docs name the exact commands from T2/T4/T6
as evidence and contain this enablement checklist:

1. hydromet owner confirms which organization is the issuing alerting authority;
2. owner approves the globally unique sender, canonical HTTPS source URL and every local-level
   severity/urgency/text mapping;
3. owner approves every station's affected-area description and any geocode/polygon;
4. deployment runs in `TEST` and a real intended consumer validates Alert→Update→Cancel;
5. security owner confirms restricted-feed recipients and token scopes;
6. operational owner confirms feed monitoring and failure response;
7. owner confirms that `ACTUAL` consumes only human-published warning decisions from Plan 342,
   with selected-forecast provenance, approved high/low hazard mappings, stored validity,
   audited cancellation and no raw observation bypass;
8. WMO Register submission/public dissemination is tracked externally if the authority chooses it.

The operations documentation also gives the only supported shutdown/policy-removal sequence: pause
the alert-producing schedules, issue and verify Cancels with `cancel-cap-incidents` under the old
policy, change configuration, then resume schedules. A direct config removal that bypasses this
sequence must fail its database-aware transition check.

Run `rg -n "CAP|cap_" docs/ config.toml src/sapphire_flow` and inspect every claim against the
implemented test/store/route. Documentation-only wording has **Pre-change: N/A**; the WMO table's
status change is gated by the runnable implementation evidence above.

## Exit gates

After the final code change:

1. every task's focused command passes;
2. `uv run ruff check src/ tests/` passes;
3. `uv run ruff format --check src/ tests/` passes;
4. `uv run python tools/pyright_ratchet.py` passes, including all changed modules;
5. `uv run pre-commit run --all-files` passes;
6. `uv run pytest` passes locally or in CI;
7. an implementation patch has the mandatory patch version bump;
8. the complete patch receives the ordinary independent Claude + Codex reviews and one additional
   operational-warning/security review before PR creation.

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1",
      "tasks": ["T1"]
    },
    {
      "id": "phase-2",
      "tasks": ["T2", "T3"],
      "parallel": true,
      "depends_on": ["phase-1"]
    },
    {
      "id": "phase-3",
      "tasks": ["T4"],
      "depends_on": ["phase-2"]
    },
    {
      "id": "phase-4",
      "tasks": ["T5", "T6"],
      "parallel": true,
      "depends_on": ["phase-3"]
    },
    {
      "id": "phase-5",
      "tasks": ["T7"],
      "depends_on": ["phase-4"]
    }
  ]
}
```
