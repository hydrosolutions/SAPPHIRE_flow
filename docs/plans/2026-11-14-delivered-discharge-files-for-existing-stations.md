---
status: DRAFT
created: 2026-11-14
title: Delivered daily-discharge files for existing tenant stations (#453 M1)
---

# Delivered daily-discharge files for existing tenant stations

DRAFT. Not executable until independently reviewed and set READY by the orchestrator.
Program #450, Effort #453, milestone M1. Authority: the vision
`planning/visions/2026-11-07-prepare-new-stations-in-sapphire.md`, sections "MVP first
proof and deferrals" and "Delivered historical files for existing stations (first slice,
MVP)". Planning base: `origin/main` at `0020d0498` (Alembic head `0084`,
`RELEASE_TARGET` `0.1.1134`). No live operation, deployment or restricted file is part
of writing or reviewing this plan.

## Outcome

An authorized operator admits a delivered batch of DHM daily-discharge files (`DFL_*`)
for stations that already exist in their tenant, and a hydrologist sees each station's
quality problems. The first customer is DHM's October 2026 delivery for tenant `chwrr`.
The second delivery needs no code change and no migration. A third (for example DHM's
next batch) needs only a new run-time manifest entry and one admission command.

## Non-goals

- Water-level (`GHT_`) files, the 24:00 convention and datum offsets (vision § Later).
- Rating tables: the October tables are already admitted through `ratings-only`.
- New stations, station metadata changes, tenant creation or re-tenanting.
- Retraining, model-input qualification, forecast activation, publication.
- Per-station or per-delivery QC ceilings, changes to the shared `config.toml`.
- Per-measurement or per-period precedence review, fallback gap filling.
- A new API endpoint (only if the operator report proves insufficient; see D-g).
- Changing the September command (`stations | replace | qc`) beyond its admission seed.

## Closed decisions (owner, 2026-11-13)

- **D-a** This slice is the one exception to "new trial stations only".
- **D-b** It does not wait for the Gateway Effort (#452).
- **D-c** Delivery facts are run-time data: the existing rating manifest gains a file
  kind. The manifest is not committed and holds no values. The new route hard-codes no
  delivery identity, station list or station count. The September command stays as is;
  only its admission is seeded.
- **D-d** Admission is a command run with the owner-level database role, not hand-written
  SQL. One migration adds the admission table and rewrites the operator guard, seeded with
  September. The guard keeps every current property: untagged rows refused, other tenants
  refused, no row moves between stations or deliveries, the operator cannot admit itself.
  Scoping an admission to stations or parameters is deferred.
- **D-e** Values load as delivered. QC uses the network (`dhm`) rules with the same
  `gross_outlier` skip as September, no per-station ceilings, no change to the shared
  config. Flagged values are stored with their flags; nothing is dropped or corrected.
- **D-f** (owner, second revision 2026-11-14) The latest delivery wins. A day already held
  by another admitted delivery is skipped when equal. When it differs, the newer
  delivery's value is loaded and the day is marked as revised: the previous value and
  delivery are kept in a small revisions table, and an administrator reviews the day and
  marks it accepted. "Latest" is the later `delivered_on` in the manifest, never arrival
  order. Until reviewed, the revised value is the one in use. Replacement is delete the
  old row plus insert the new one in the same transaction, so the guard keeps its current
  property that no row moves between deliveries. If the old row is referenced elsewhere
  and cannot be deleted, that day is held and reported instead. Reports show station,
  dates and counts only. Mapping the mark to a QC flag or an API field is deferred.
- **D-g** Problems are visible first as operator CLI output per station: coverage, counts
  by QC status, gaps, flagged ranges, no values in logs or issues. An API endpoint only if
  that proves insufficient, after auditing existing endpoints. Mapping flagged ranges to
  model windows is deferred.
- **D-j** (owner, 2026-11-14) October daily values link to their rating tables, as
  September's do: each row carries the `rating_curve_id` of the latest valid rating table
  covering that day. Where several valid tables overlap, the October rating delivery's
  table is used. Tables that fail numerical validation (20 older October blocks do) are
  never candidates. A day with no covering valid table (for example after the last
  block's end) is **provisional** and links to the most recent past valid table, as agreed
  earlier for provisional ratings. Provisional is derivable (the linked table's validity
  does not cover the day), so no new column is added; the report counts provisional days
  per station as dates and counts. A day before the earliest valid table, or at a station
  with no valid table, stays unlinked and is listed. Nothing else is guessed.
- **D-k** (owner, 2026-11-14) October values get exactly September's visibility: hidden
  from consumer tokens, visible to reviewers in scope and to admins (restricted DHM data;
  public release was never authorized). Hence T4.
- **D-h** Discharge only. Water level, 24:00 and datum offsets are out.
- **D-i** Coordinated with the Nepal-cloud session through the owner; implementation
  starts only on the owner's go-ahead. Deployment to the Nepal host belongs to the owner
  and the designated deployment operator, never to implementers.

## Facts from the code that shape the design

1. `types/dhm_delivery.py::DELIVERY_ID` (September) is a literal in migration `0067`'s
   guard function and its error text; `docker/bootstrap-roles.sql` checks the guard's
   trigger names, types, `WHEN` text and function name, not the function body.
2. The guard is `SECURITY INVOKER`: any table it reads needs `SELECT` for
   `sapphire_operator`. Bootstrap grants the operator anything only while every guard
   trigger is present and enabled (`operator_guard_ok`).
3. Natural key `uq_observations_natural_key` = `(station_id, timestamp, parameter,
   source)`, with no delivery identity. `store_raw_observations` raises
   `DeliveryCollisionError` when a key is held by a different tag (including untagged), and
   its upsert writes only when value or rating provenance changed (a same-tag re-run with
   equal values writes nothing). This forces D-f.
4. **Restricted-data withholding is keyed on the September literal**:
   `api/routes/api_stations.py` (latest observations and the station observations route),
   `api_swiss_overview.py`, `api_swiss_observation_history.py` and
   `store/observation_store.py` (`ProtectedObservationVisibility.EXCLUDE`, used by
   `api/network_overview.py`). Rows tagged with any other delivery are returned to
   consumer tokens. Loading October without changing this would expose restricted
   values. Generalising it is in scope as a safeguard, not a new feature (T4).
5. `ratings-only` cannot run as the operator: the guard refuses its `rating_curves` tags
   and the operator holds no grant on `rating_delivery_files`/`_curves` (bootstrap revokes
   them as protected tables). Admitted native rating curves are immutable
   (`native_rating_curve_guard`, migration `0079`).
6. `init` runs `alembic upgrade head` before `provision_tenants`; on a fresh database the
   `chwrr` tenant does not exist when migrations run. Today's guard therefore looks the
   tenant up by code, and a missing tenant refuses.
7. `services/nepal_onboarding.py::DeliveryObservationReader` (training readiness) reads only
   the September delivery. October rows will not reach retraining without a follow-up (Q6).
8. `fetch_delivery_observations` uses the per-row unit-read predicate (PR #507). New bulk
   reads must follow the same pattern; the old flattened form held the tenant lock for over
   10 minutes.

## Design

**Admission table** (migration `0085`, chained on `0084`). `delivery_admissions`:
`delivery_id text PRIMARY KEY` (one tenant per id), `tenant_code text NOT NULL` (by code,
as today's guard: fact 6), `manifest_entry_sha256 text NULL` (NULL only for the September
seed, `CHECK` on a 64-hex pattern otherwise), `admitted_at`, `admitted_by` (label, not an
authenticated identity), `reason`. The migration seeds `('dhm-barkhk-2026-09-08', 'chwrr',
NULL, …)`. Triggers for every role: `UPDATE` refused; `DELETE` refused while any
`observations` or `rating_curves` row carries that tag. Grants: `SELECT` only, to
`sapphire_operator` (the guard's lookup), `sapphire_api` and `sapphire_worker` (withholding).
Nobody but the owner writes it.

**Guard rewrite.** `CREATE OR REPLACE FUNCTION public.operator_guard_delivery_row()` keeps
its name, `SECURITY INVOKER`, pinned `search_path`, schema-qualified relations, OLD/NEW
checks and the no-move rule. Only the predicate changes, from "tag = literal AND station in
tenant `chwrr`" to "an admission exists for `NEW/OLD.delivery_id` AND the station's tenant
code equals that admission's `tenant_code`". `NULL` tags fail the `EXISTS`, so untagged rows
stay refused. Triggers, the station-insert guard (still `chwrr`-only, used only by the
September `stations` command) and the truncate guard are untouched, so the bootstrap's
`operator_guard_ok` check stays valid. Bootstrap adds the three `SELECT` grants inside its
existing blocks; the operator grant moves inside `\if :operator_guard_ok`.

**Downgrade** (security-sensitive ordering): (1) revoke operator DML, as `0067` does;
(2) refuse if any row carries a tag admitted other than the seed (rows would become
unwithheld under the older code: fact 4); (3) restore the `0067` literal function body;
(4) drop the table.

**Manifest file kind.** In `cli/rating_delivery_manifest.py` each file gains
`kind: Literal["rating_table", "daily_discharge"] = "rating_table"`, so existing manifests
stay valid. `load_rating_manifest` takes only `rating_table` files, so `ratings-only` is
unchanged for any manifest. A new `load_discharge_manifest(path, delivery_id)` returns the
selected delivery's `daily_discharge` files as a frozen dataclass. It checks: size bound,
SHA-256 of the original bytes, `parse_daily_flow`, the header code against the declared
code, at most one file per station, the path outside the checkout. The **manifest-entry
hash** is SHA-256 of a canonical JSON of `{delivery_id, delivered_on, sorted [(network,
station_code, kind, sha256)]}` for the selected entry. It excludes paths, so a dry-run copy
and the host give the same hash, and other entries, so adding November later does not
invalidate October.

**Admission command** (owner role): `python -m sapphire_flow.cli.delivery_admission admit
--tenant T --manifest M --delivery D --admitted-by NAME --reason TEXT [--dry-run]`, run as a
one-off command of the existing `init` service, which already holds the owner credential (Q4).
It checks the tenant exists and every declared station exists, uniquely, in that tenant. It
records the row and one `DELIVERY_IMPORTED` audit entry (`command: "admit"`, hash, station
count; no values). The same id with the same hash is a no-op; the same id with a different
hash, or another tenant, is refused. `revoke --delivery D` deletes the row (the trigger
refuses while tagged rows exist) and writes an audit entry.

**Operator commands** (added to `cli/import_dhm_delivery.py`, same `operator` service and
CHWRR overlay; the tenant-generic CLI identity is deferred):

- `discharge --tenant T --manifest M --delivery D [--dry-run]`. Load, verify, then refuse
  unless the admission for `D` exists with the same tenant and hash. Take `lock_tenant`
  (serialises with `replace`/`qc`). Resolve stations by code and network in the tenant, as
  `admit_rating_deliveries` does: missing, ambiguous or foreign refuses. Build
  `RawObservation(parameter="discharge", source=MANUAL_IMPORT, delivery_id=D,
  rating_curve_id=<single valid curve of the manifest's rating delivery covering the day,
  else None>)` at `nepal_day_start(day)` (D-j). The curve lookup reads `rating_curves` rows
  tagged with the manifest's rating delivery id; T3 first verifies the operator can
  `SELECT` them. If it cannot, the fallback is a separate owner-run link step (no new
  operator grant on the protected `rating_delivery_*` tables). Classify each key against
  existing rows, read per station by time range with the per-row predicate (fact 8):
  *new* → insert; *own tag* → upsert (equal = no-op); *other admitted tag, equal value* →
  skip and record; *other admitted tag, different value* → replace the day and record a revision (D-f; the
  interim behaviour until T3b lands is to hold that day only);
  *untagged or non-admitted tag* → refuse (`DeliveryCollisionError`). Equality means exact
  float equality of the parsed value with the stored value. Read back and compare the
  intended and written key sets. Write one audit entry: per station written/skipped
  counts, skipped and held date ranges with the holding delivery id, days without a
  rating link, the manifest-entry hash.
- `discharge-qc --tenant T --delivery D [--dry-run]`. Rows tagged `D` only. Rules: `dhm`
  network discharge rules from the loaded config, `skipped_rule_ids={"gross_outlier"}`,
  `overrides=[]`. Refuse if no `dhm` discharge rule resolves. Reuse
  `_local_day_segments`, `resolve_selection`, `Stage1QualityChecker` and
  `update_delivery_qc`. Single-day segments stay `QC_UNCHECKED`. The audit entry carries
  status counts, the resolved rule ids and thresholds, and `obs_qc_rule_version`.
- `discharge-report --tenant T --delivery D [--output FILE]`. Read-only. Per station: first
  and last day, day count, missing-day count and gap ranges, counts by QC status, flagged
  date ranges per rule id. Dates and counts only. A table goes to stdout; optional JSON goes
  to a file outside the checkout (Q-settled 6).
- `discharge-withdraw --tenant T --delivery D [--dry-run]`. Deletes this delivery's rows in
  the tenant, then audit counts. Refuses the seeded September id (its own `replace` exists).
  A referencing row (for example provisional lineage) aborts it atomically.

**Withholding.** Replace the September-literal exclusion (fact 4) with "the row's
`delivery_id` is in `delivery_admissions`". Use one store helper and one SQL predicate
(`delivery_id IS NULL OR delivery_id NOT IN (SELECT delivery_id FROM
delivery_admissions)`). It is evaluated per row, not as a flattened semi-join, and kept
behind the existing `excluded_delivery_id`/`ProtectedObservationVisibility` call sites.
September behaviour is unchanged (it is the seed). Untagged and non-admitted rows (for
example BIPAD-feed tags) stay visible. Visibility of October rows equals September's (D-k).

**Revised days (D-f).** The migration also creates `delivery_value_revisions`: station,
timestamp, parameter, previous value, previous delivery, new delivery, revised time,
status (`pending` or `accepted`), reviewer name, reviewed time. It holds restricted
values, so no API role can read it and the operator may only insert and select. The
owner-level `discharge-review --tenant T --delivery D [--accept --reviewer NAME]` lists
pending days (dates and counts) and marks them accepted, with an audit entry. The
existing `observation_versions` cannot serve here: it stores a value only together with
the rating curve that produced it.

## Questions settled from the code

1. **Natural key vs delivery tag:** the tag is not in the key, so per-day ownership is
   exactly one tag. Skip-equal leaves the September row and tag in place; the October audit
   entry records that day as held by September.
2. **Live BIPAD water-level rows:** no interaction. They are `water_level`/`measured`, a
   different key. The guard refuses operator writes to them (tags not admitted), and
   withholding leaves them visible. Their `bipad-water-level-feed-*` tags have no producer
   on main; that part is not verified.
3. **Migration number:** `0085`. No remote branch claims it. The stopped local one-off patch
   has an uncommitted `0085`, which must not be revived. Bump `_RELEASE_B_HEAD` in
   `tests/unit/db/test_alembic_head_release_b.py` to `"0085"` with its comment line.
4. **Idempotent re-runs:** `admit` with the same hash is a no-op. `discharge` with
   unchanged files writes zero rows and still writes its audit entry. `discharge-qc`
   re-evaluates and gives the same result.
5. **`--dry-run`:** as today, the real transaction (audit included) rolls back. It does not
   prove a later commit will succeed against concurrently changed data.
6. **Report production:** a read-only operator subcommand with `SELECT` it already has. No
   new grant, no API.

## Open questions for the owner (plain language)

1. **Name of the October discharge delivery.** *Closed 2026-11-14: same name.* The October rating files are already
   recorded under one delivery name. Should the discharge files go under the same name or
   a new one? *Recommendation:* the same name. It is one DHM delivery, and the manifest
   gains a file kind rather than a second entry.
2. **Who may see October values.** *Closed 2026-11-14 (D-k):* same as September.
3. **Link to rating tables.** *Closed 2026-11-14 (D-j):* yes, link; days without exactly
   one valid curve stay unlinked and are listed.
4. **How the owner-level admission is run.** *Closed 2026-11-14: as recommended.*
   *Recommendation:* a one-off command through the
   existing set-up container, which already holds the owner credential. This adds no new
   service and puts the credential nowhere new. The security reviewer should confirm it.
5. **One differing day.** *Closed 2026-11-14 (D-f):* hold only the differing day; load
   everything else; corrections are an explicit later step. *Superseded by D-f (second
   revision): latest delivery wins, marked, admin review.*
6. **Retraining.** *Closed 2026-11-14: follow-up, as recommended.* The training-readiness check reads only September today. Is it fine that
   making it read October is the next milestone's first small task, not this one?
   *Recommendation:* yes. It keeps this slice to loading and checking.

7. **Days after the last rating block ends.** *Closed 2026-11-14 (D-j):* provisional,
   linked to the most recent past valid table.

## Tasks

Red-first means each new behaviour has a failing test first, failing for the reason the
gap exists. Integration tests run against the real PostGIS testcontainer through real
logins (`sapphire_operator`, `sapphire_api`, owner), as the existing operator tests do.
Fixtures are synthetic; no delivered values.

- **T1 Admission table, revisions table, guard rewrite, bootstrap** (high risk; security review). Files:
  `alembic/versions/0085_delivery_admissions.py`, `src/sapphire_flow/db/metadata.py`,
  `docker/bootstrap-roles.sql`, `tests/unit/db/test_alembic_head_release_b.py`,
  `tests/integration/db/test_operator_role.py`, `test_operator_grant_matrix.py` (incl. the
  new `delivery_value_revisions` grants),
  `test_operator_guard_historical.py`, new `tests/integration/db/test_delivery_admission_guard.py`.
  Tests: the September `replace`/`qc` pass unchanged on the seed. Untagged, non-admitted,
  admitted-but-other-tenant and missing-tenant writes are refused (INSERT NEW, DELETE OLD,
  UPDATE both). No move of station or tag. Curve validity dates are fixed. The operator,
  api and worker cannot INSERT/UPDATE/DELETE/TRUNCATE `delivery_admissions` (grant matrix
  updated and still minimal). UPDATE is refused for every role. DELETE is refused while
  tagged rows exist. A temporary table named `delivery_admissions` cannot satisfy the
  guard. The function stays `SECURITY INVOKER` with a pinned `search_path`, and no
  `SECURITY DEFINER` function writes the guarded tables or the admission table.
  `operator_guard_ok` is still true after upgrade. Downgrade revokes DML before restoring
  the literal body, and refuses while non-seed tagged rows exist. Upgrade, downgrade,
  upgrade round-trips. Exit gate: security review clean; full suite green.
- **T2 Manifest file kind and admission command.** Files:
  `src/sapphire_flow/cli/rating_delivery_manifest.py`, `src/sapphire_flow/types/rating_delivery.py`
  (or a sibling `types/discharge_delivery.py`), new `src/sapphire_flow/cli/delivery_admission.py`,
  new `tests/unit/cli/test_rating_delivery_manifest.py` (the loader has no test today),
  `tests/integration/cli/test_rating_only.py`,
  new `tests/integration/cli/test_delivery_admission.py`. Tests: old manifests load
  unchanged. A mixed manifest gives `ratings-only` only the rating files. The discharge
  loader refuses a hash mismatch, a header mismatch, a duplicate station, an unknown kind,
  an oversize file or a path inside the checkout. The entry hash ignores paths and other
  entries. `admit` works as owner and fails as operator (permission). Same hash: no-op;
  different hash or tenant: refused. Dry-run leaves no row. `revoke` is refused while
  tagged rows exist.
- **T3 Load, QC, report, withdraw.** Files: `src/sapphire_flow/cli/import_dhm_delivery.py`,
  new `src/sapphire_flow/services/delivered_discharge.py`, `src/sapphire_flow/store/observation_store.py`
  (bounded per-station existing-key reader), `protocols/stores.py` and the fake store if a
  method is added, new `tests/unit/services/test_delivered_discharge.py` (pure overlap
  classification, report aggregation), `tests/integration/cli/test_import_dhm_delivery.py`
  plus a new integration module for the discharge route. Tests: with the September seed
  loaded, equal days are skipped and recorded. A differing day is held alone in this task (the existing
  value is untouched and every other day and station loads); T3b replaces the hold. An untagged collision
  refuses. Each loaded day links to the single valid curve of the rating delivery; a day
  with none or several stays unlinked and is reported; no link is guessed. A non-admitted or wrong-hash delivery is refused
  by Python and, bypassing Python, by the guard. Missing or foreign stations refuse. A
  re-run writes zero rows. Dry-run rolls back. Audit `detail` contains no delivered value
  (asserted by scanning for every fixture value). QC uses no station override,
  `gross_outlier` is never applied, single-day segments are `QC_UNCHECKED`, and flags are
  stored, not dropped. The report has dates and counts only. Withdraw removes only `D`'s
  rows and refuses the seed. Water-level rows at the same stations are untouched.
- **T3b Revised days and review** (depends on T3; can be cut if the cut line is at risk,
  leaving the hold behaviour). Files: `src/sapphire_flow/services/delivered_discharge.py`,
  `src/sapphire_flow/cli/delivery_admission.py` (`discharge-review`), store method for
  delete-plus-insert in one transaction, the `delivery_value_revisions` table is created in
  T1. Tests: a differing day is replaced, the revision row holds the previous value and
  delivery, status is pending; a referenced old row holds the day instead; a re-run writes
  nothing; `discharge-review` lists dates and counts only, `--accept` marks accepted with
  the reviewer name and an audit entry, and fails as operator; no API role reads the
  table; the report counts pending revisions.
- **T4 Withholding generalised** (restricted data; covered by the same security review).
  Files: `src/sapphire_flow/api/routes/api_stations.py`, `api_swiss_overview.py`,
  `api_swiss_observation_history.py`, `src/sapphire_flow/store/observation_store.py`,
  `src/sapphire_flow/api/network_overview.py` (call site only), their API/store tests.
  Tests: a consumer never sees rows of any admitted delivery on any of the five paths. A
  reviewer sees them only in scope; an admin sees them. Untagged and non-admitted tagged
  rows stay visible. September behaviour is unchanged. The query plan or timing on a
  fixture of realistic size shows a per-row check, not a full view scan.
- **T5 Documentation** (see below). Exit gate: docs match the merged code.
- **T6 Dry run and host run** (owner and deployment operator only; not implementers).
  See Rollout.

```json
{
  "tasks": [
    {"id": "T1", "depends_on": [], "estimate_days": 2.0, "risk": "high"},
    {"id": "T2", "depends_on": ["T1"], "estimate_days": 1.25, "risk": "medium"},
    {"id": "T3", "depends_on": ["T1", "T2"], "estimate_days": 2.5, "risk": "medium"},
    {"id": "T3b", "depends_on": ["T3"], "estimate_days": 1.0, "risk": "medium"},
    {"id": "T4", "depends_on": ["T1"], "estimate_days": 1.0, "risk": "high"},
    {"id": "T5", "depends_on": ["T2", "T3", "T3b", "T4"], "estimate_days": 0.5, "risk": "low"},
    {"id": "T6", "depends_on": ["T5"], "estimate_days": 0.75, "risk": "high", "owner_run": true}
  ]
}
```

Estimates are unmeasured and follow the overarching vision's track B as given in the brief:
plan 1 day; build 5-7 days (T1-T5 including T3b sum to 8.25; the critical path T1-T2-T3-T3b-T5 is about 7.25, above
the vision's 5-7. Cutting T3b brings it back to about 6.25 and keeps the hold behaviour); dry run plus host run 0.5-1 day; cut line
24 November. T2 and T4 can run in parallel after T1. If the cut line is at risk, cut the
report's flagged-range detail first (keep coverage and status counts), never T1 or T4.

**PRs.** PR-A = T1 + T4 (database and withholding; the security review covers both).
PR-B = T2 + T3 + T5. PR-A merges and deploys first or together with PR-B; no October load
before both are deployed. Each code PR advances `RELEASE_TARGET` exactly once
(`.venv/bin/python -I tools/release_target.py bump --base-sha <base>`; `docs/standards/cicd.md`
§ Release identity), with no hand-edited package versions and no tags. Lint, format,
changed-module types and the full suite must pass after the final change.

## Rollout (owner-run)

1. **Dry run on a copy.** Use a copy of the Nepal database restored on the Nepal host (never
   on a laptop or in CI), with the release under test. Run `admit --dry-run`, then for real
   on the copy, then `discharge --dry-run`, `discharge`, `discharge-qc`, `discharge-report`.
   Record the counts, skipped ranges, refusals and wall times. This measures the overlap
   with September (the vision leaves that to the dry run). Run `discharge` again and expect
   zero rows written. Run `discharge-withdraw`, then `revoke`, to prove the undo path.
2. **Owner go-ahead**, coordinated with the Nepal-cloud session (D-i).
3. **Host run** by the owner or the designated deployment operator: the same commands
   without `--dry-run`, through the operator overlay; `admit`/`revoke` through `init`.
4. **Read-back:** report counts equal the dry run. A consumer-token request to a station
   observations route returns no October rows; a reviewer token in scope does. September row
   counts and QC counts are unchanged. Water-level row counts are unchanged. The audit
   entries exist and carry no values. The operator role goes back to no login (deploy
   without the overlay) when done.

## Rollback

- **Undo a loaded delivery:** `discharge-withdraw` (operator), then `revoke` (owner). The
  append-only audit log keeps the history; admission and withdraw entries both stay.
  September rows are never touched, because skipped days were never written under October.
- **Image rollback below PR-A while October rows exist is forbidden.** The older code hides
  only September (fact 4), so October values would reach consumer tokens. Withdraw first.
  The migration downgrade enforces this (T1); the runbook states it for image-only
  rollback, which the downgrade cannot catch.
- **Guard problem:** the existing emergency revoke (operator `NOLOGIN` plus
  `pg_terminate_backend`, `docs/standards/cicd.md`) stops all operator writes at once.

## Documentation updates (T5)

- New admin checklist `docs/runbooks/delivered-discharge-files.md` in the style of
  `docs/operations/recap-gateway-runbook.md` § admin checklist. It has an inputs table
  (manifest, delivery dir, delivery id, tenant, admitter name, reason), a stage table
  (admit, load, QC, report, read-back, close login), copy-paste commands, "rules that never
  change" (no values in logs or issues; files outside the checkout; no image rollback below
  PR-A while rows exist) and an "if it stops" table (refused admission, held days,
  collision, missing station, lock timeout, withdraw blocked by a reference).
- `docs/runbooks/chwrr-dhm-history-import.md`: cross-reference. September is now an
  admitted delivery (seed); its commands are unchanged.
- `docs/touchpoint-maps.md`: Persistence / API write path operator bullets (the "a second
  delivery is a new migration" line becomes admission), Observation QC threshold
  configuration (the delivered-discharge QC uses network rules only), and a short new
  "Delivered discharge admission" map.
- `docs/standards/security.md`: three levels of identity (the owner also admits deliveries;
  the guard predicate; the admission-table grants) and the restricted DHM history bullet
  (all admitted deliveries). `docs/standards/cicd.md` § Operator role (admission through
  `init`). `docs/spec/database-schema.md` (new table). `docs/native-provisional-rating.md`
  (manifest `kind`). The `0067` docstring stays historical; `0085` states the supersession.
  `docs/plans/README.md` index entry.

## Risks

- **Restricted-data exposure** through withholding gaps or image rollback (facts 4, T4,
  Rollback). This is the highest risk, and the reason PR-A deploys first.
- **Guard weakening by the rewrite.** Mitigation: the T1 test matrix mirrors every current
  property, plus a security review of the exact diff.
- **Owner credential use outside deploy.** Bounded to a one-off `init` command; reviewed.
- **Lock duration on the live host.** About 254k new rows under `lock_tenant` would block
  `replace`/`qc`, and ingest's shared station locks. Mitigation: per-row predicate reads,
  5,000-row batches, wall time measured in the dry run; run outside ingest peaks.
- **QC across delivery boundaries** (for example station 450, whose October period may
  start right after September's) is not checked. QC runs per delivery. Deferred.
- **Old-period rows in current inputs:** the latest October day is 2025 (briefing). Verify
  in the dry run that no active forecast lookback reaches it; September rows set the
  precedent.
- **Rating link access:** the operator may not be able to read the October curves (fact 5).
  Mitigation: verify first in T3; fallback is an owner-run link step. Unlinked days are
  reported, never guessed.
- **Wrong admission:** harmless until rows are written; `revoke` removes it.

## MVP deferrals

Station or parameter scoping of admissions; a tenant-generic operator CLI identity (the
overlay stays CHWRR); re-approval bound to exact input and rule versions; cross-delivery QC
continuity; mapping flags to model windows; an API endpoint or dashboard view; reading
October in training readiness (Q6); water level, 24:00 and datum (vision § Later);
attribution beyond a label (the existing forged-audit residual stays).

## Review plan

- Plan: one independent Claude review (owner confirmed 2026-11-14).
- PR-A: an independent security-minded review of the migration, bootstrap grants, downgrade
  ordering and withholding, on the exact diff. PR-B: one independent review.
- The orchestrator sets READY after review. The owner decides findings, approves and merges
  every PR, and authorizes the host run.
