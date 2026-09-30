# Decision record — hosting DHM data on a password-protected cloud server (an assumption, not a consent)

**Date**: 2026-09-30
**Decided by**: the project owner (Beatrice Marti), who **takes full responsibility** for this decision.
**Status**: in force until DHM replies or the owner withdraws it.

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
- Citing this record as the `permission_ref` in Plan 516's region configuration for the six DHM stations, for
  the datasets displayed in that password-protected dashboard.

## What it does not cover

- Any public or unauthenticated access, indexing, or open data release.
- Bulk download of the restricted measurements, or publication of derived metrics outside the password-protected
  dashboard. Plan 268 D5's publication limit and the map deployment plan's separate DHM letter (display, download,
  derived metrics) remain open.
- A change to what tokens may read: consumer and reviewer tokens still never receive delivery-tagged rows
  (`security.md` § Restricted DHM history).

## Conditions and consequences

- **Backups become a precondition of loading the data.** Plan 511 D9 defers off-box backups only while no
  restricted data is on the host. Before the DHM data is loaded onto the cloud host, the backup follow-on (Plan 208,
  updated for Cloudflare R2, and Plan 340's preservation bundle where it applies) must be planned.
- **If DHM replies with an objection or conditions**, hosting stops or changes at once: take the dashboard
  offline, remove the data from the cloud host, and record the reply here.
- **If DHM replies with consent**, record the reply and its scope here, and replace this assumption with it.
- The dashboard's page text must not claim DHM's endorsement or permission.

## References

Plan 268 (D5, D9), Plan 511 (D9), Plan 516 (D3 `permission_ref`), Plan 049 (T3), the map project's
`docs/NEPAL_MVP_DEPLOYMENT_PLAN.md` § DHM letter.
