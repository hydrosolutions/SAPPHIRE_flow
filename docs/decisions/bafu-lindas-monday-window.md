# Decision Record — BAFU LINDAS Monday-morning publishing window

**Date**: 2026-05-11
**Status**: Active observation; not yet escalated to BAFU support
**Owners**: Bea (orchestrator), Hydrologist (review)

## Context

`live-lindas-weekly.yml` (`.github/workflows/live-lindas-weekly.yml`)
runs the BAFU LINDAS schema-drift check (`tests/integration/live/test_lindas_live_schema.py`)
on a Monday 06:00 UTC cron. The test queries the BAFU LINDAS SPARQL
endpoint at <https://lindas.admin.ch/query> against the
`<https://lindas.admin.ch/foen/hydro>` named graph and asserts that
quorum-stations under `…/foen/hydro/river/observation/<code>`
return well-formed observations with `discharge`, `water_level`, and
`water_temperature` predicates.

## Observed pattern

| Date | Trigger | Run ID | Outcome |
|---|---|---|---|
| 2026-04-23 08:18 UTC | dispatch | 24824715145 | success |
| 2026-04-27 06:53 UTC | schedule | 24980849671 | success |
| 2026-05-04 07:02 UTC | schedule | 25305623990 | **failure** |
| 2026-05-04 15:58 UTC | dispatch | 25329079256 | success |
| 2026-05-11 07:12 UTC | schedule | 25655741596 | **failure** |

**Monday-schedule outcomes**: 2026-04-27 SUCCESS, 2026-05-04 FAILURE,
2026-05-11 FAILURE. 2 of 3 observed Mondays failed; failures are
characterised as **intermittent** rather than "recurring" (one
Monday-schedule has succeeded).

## Evidence that the failures are upstream, not a schema change

1. The BAFU VoID descriptor at
   <https://environment.ld.admin.ch/.well-known/void/dataset/hydro>
   continues to declare `schema:hasPart` for both
   `https://environment.ld.admin.ch/foen/hydro/lake` and
   `…/foen/hydro/river`, and the schema description still mentions
   "discharge and water level data on rivers… water temperatures".
   The schema is unchanged.

2. The 2026-05-11 incident timeline:
   - **04:46 UTC** — `integration-nightly.yml` (run `25650404812`)
     executed `test_lindas_live_schema` as part of its `tests/integration/live/`
     selection (2 of 2 collected items, both passed). The full BAFU
     dataset was present at this time.
   - **07:03:34 UTC** — BAFU VoID descriptor `dateModified` advances
     (`<http://schema.org/dateModified> "2026-05-11T07:03:34.246+00:00"`).
     BAFU's hydro publishing pipeline runs.
   - **07:12 UTC** — `live-lindas-weekly.yml` (run `25655741596`)
     executes the same test against the now-republished dataset and
     **fails**: all 6 reference river stations return 0 observations
     (HTTP 200, 117-byte empty SPARQL results); the lake-path station
     2004 still returns data.
   - **~08:00 UTC** — manual SPARQL probe of the
     `https://lindas.admin.ch/foen/hydro` named graph counts:
     0 subjects under `/foen/hydro/river/`, 0 `discharge` triples,
     0 `waterTemperature` triples, 34 `waterLevel` triples. The
     river half of the dataset is gone.

   The 04:46 UTC pass followed by 07:12 UTC failure on the same code
   demonstrates that BAFU's 07:03 UTC republish **overwrote a working
   dataset with an incomplete one** — this is an upstream publishing
   regression, not a schema redesign on our side.

3. The 2026-05-04 manual-dispatch success at 15:58 UTC (run
   `25329079256`) — same code as the failed 07:02 UTC schedule that
   morning — shows BAFU does republish a complete dataset later in the
   day, at least sometimes.

## Implications

- The `live-lindas-weekly.yml` cron at `0 6 * * 1` catches BAFU's
  Monday-morning publishing transient. The test correctly detects the
  upstream incomplete-publish; the failure signal is real and is doing
  its job.
- A `live-lindas-weekly.yml` failure on a Monday morning is **not by
  itself evidence of schema drift on the adapter side**. The triage
  procedure is:
  1. Fetch the BAFU VoID descriptor at the URL above and confirm
     `schema:hasPart` still lists `…/foen/hydro/river` and the
     description still mentions discharge + water_level + water
     temperature. If yes → upstream publishing problem; if no →
     genuine schema drift, escalate to a Plan 074-style remediation.
  2. Re-run the workflow manually 4–8 hours later
     (`gh workflow run live-lindas-weekly.yml --ref main`). If it
     passes, BAFU has republished and the workflow is healthy.
  3. If the same upstream outage persists across multiple manual
     reruns in a day, escalate by email to
     **`abfragezentrale@bafu.admin.ch`** (BAFU "Hydrologische
     Abfragezentrale", listed as the dataset contact point in the
     VoID descriptor).

## Open follow-ons

- If a third consecutive Monday-schedule fails, consider rescheduling
  the cron from `0 6 * * 1` to a later UTC slot (e.g. `0 14 * * 1`,
  matching the empirically-observed afternoon recovery window). A
  separate single-task plan would handle this; not in scope for
  Plan 070.
- Optional: add an automated retry (e.g. 4 hours after a Monday
  failure) before alarming. Deferred until/unless the failure pattern
  recurs a third time.

## References

- `.github/workflows/live-lindas-weekly.yml` — the workflow under
  discussion.
- `tests/integration/live/test_lindas_live_schema.py` — the test that
  asserts schema integrity.
- `src/sapphire_flow/adapters/hydro_scraper.py` — the BAFU SPARQL
  adapter (unchanged through this incident).
- `docs/plans/070-precommit-and-gate-parity.md` §C2 — the LINDAS
  carve-out anchored on this evidence.
- `docs/plans/archive/074-*.md` — the plan that introduced the
  schema-drift check (referenced from
  `tests/integration/live/test_lindas_live_schema.py`).
