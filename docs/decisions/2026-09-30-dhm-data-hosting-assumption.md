# Decision record — hosting DHM data on a password-protected cloud server (an assumption, not a consent)

**Date**: 2026-09-30
**Decided by**: the project owner (Beatrice Marti), who **takes full responsibility** for this decision.
**Status**: in force until DHM replies or the owner withdraws it; scope and backup
sequencing updated by the owner on 2026-10-21 (below). This is not DHM consent.

## The decision

The owner has emailed DHM about hosting the restricted delivery data. **DHM has not replied.** The owner decided
to **proceed on the assumption that DHM has no objection** to SAPPHIRE Flow hosting that data on a **cloud server
that is password protected**, and to showing forecasts derived from it, behind a password, to people who hold that
password.

This is an **assumption**, made because no answer has come. It is **not** DHM's consent and must never be
described as consent in any document, the dashboard, or a message to DHM.

## What it covers

- Hosting the DHM delivery data (Plan 268) and forecasts trained on it on the Nepal cloud host (Plan 511).
- Showing those forecasts in the flow-map Nepal dashboard behind its passphrase gate (Plan 049), to people the
  owner gives the passphrase to.
- Citing this record as the `permission_ref` for the delivered Nepal station data
  displayed in that password-protected dashboard. The original reference named
  six stations; the owner's 2026-10-21 clarification covers **all 21 delivered
  stations, including historical water-level records**. This does not qualify
  observations/conversions/models scientifically or change token/data-use guards.

## What it does not cover

- Any public or unauthenticated access, indexing, or open data release.
- Bulk download of the restricted measurements, or publication of derived metrics outside the password-protected
  dashboard. Plan 268 D5's publication limit and the map deployment plan's separate DHM letter (display, download,
  derived metrics) remain open.
- At this decision's date, consumer and reviewer tokens could not receive delivery-tagged rows.
  **Superseded for private reviewer access on 2026-10-31:** the owner authorized historical discharge
  reads through the existing tenant/station-scoped reviewer role. Consumers remain excluded;
  this does not authorize public release (`security.md` § Restricted DHM history).

## Conditions and consequences

- **Historical backup prerequisite — superseded for the active Nepal21 path on
  2026-10-21.** Earlier sequencing made backup planning/verification a precondition
  of loading data (Plans 511/208 and applicable preservation work). The owner now
  defers backup commissioning and recovery testing, including plaintext tests.
  They are not active M1/M2 or forecast/dashboard prerequisites and are not claimed
  complete. This does not authorise destructive/in-place source changes, removal
  of protected evidence or bypass of preservation/publication/access guards.
- **If DHM replies with an objection or conditions**, hosting stops or changes at once: take the dashboard
  offline, remove the data from the cloud host, and record the reply here.
- **If DHM replies with consent**, record the reply and its scope here, and replace this assumption with it.
- The dashboard's page text must not claim DHM's endorsement or permission.

## References

Plan 268 (D5, D9), Plan 511 (D9), Plan 516 (D3 `permission_ref`), Plan 049 (T3), the map project's
`docs/NEPAL_MVP_DEPLOYMENT_PLAN.md` § DHM letter.

## Historical private recovery destination — 2026-10-20

For the Nepal21 test deployment, the owner confirmed the existing company
Cloudflare account for the planned off-box recovery copy. The deferred design is
one verified private recovery set, with restricted objects encrypted before upload
and predecessor removal only after replacement verification. Commissioning this
policy is deferred by the later decision below, not accepted or complete. The
delivery/recovery policy is in the [continuing Nepal vision](../../planning/visions/2026-10-01-nepal6-current-forecast-data-for-the-existing-dashboard.md).

This is owner-authorised private recovery under the same assumption, not DHM
consent or public-release permission. The objection/changed-conditions process
above applies to Cloudflare copies too: stop new replication and handle existing
copies under the required hosting-removal/change action. Encryption does not waive
these conditions. Original protected preservation retains its separate custody;
rolling recovery retention does not authorise deleting scientific evidence.

## Owner scope and sequencing update — 2026-10-21

The owner confirmed that the existing hosting/display assumption covers all 21
delivered Nepal stations and their historical water-level records. This is owner
authority under the same assumption, **not independent legal consent from DHM**.
Public access, bulk download and derived-metric publication restrictions remain;
objections or changed conditions still require the actions above.

The owner deferred full backup commissioning and recovery testing: Cloudflare
allowance/capacity work, rotating complete sets, daily/prechange backups, fresh
verified off-host prechange points and timed loss/restoration proofs are not on
the current forecast/dashboard critical path. Cloudflare account access and a
recovery machine are not current blockers. Plaintext recovery tests are deferred;
this update does not grant another host or CI authority to decrypt protected data
or runtime-secret capsules. Later recovery work needs its own review and explicit
activation; no paid fallback or additional automatic replica is authorised.

There is no verified-DR claim. Loss of current state may cause prolonged downtime,
reconstruction work or unrecoverable data. Preserve the original isolated archive,
UUIDs/RAW lineage, original dirty checkout on the local Mac, local dashboards and
scientific evidence. Only separately authorised additive isolated target work is
contemplated; destructive/in-place source changes remain held for separate
protection and approval. Record actual independent original delivery/evidence
custody without inventing copies, locations or permission to delete them.

This records a planning scope change. It does not approve code, implementation,
deployment, model execution, station activation or scientific adoption. Owner
milestone acceptance and all unchanged security/scientific gates still apply.
