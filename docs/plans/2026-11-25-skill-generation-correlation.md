---
status: READY
---
# Correlate baseline precedence with its generation

Live BAFU qualification after version-4 scoring found a skill read exceeding
45 seconds. Compiled SQL proves that `baseline_outranks_generation` nests
`FROM skill_scores AS other_baseline, skill_scores`: its scope comparison
references the outer data table two SELECT levels away, so SQLAlchemy introduces
another uncorrelated data table. This scans unrelated scopes and can suppress a
current generation when a higher-version baseline exists for another station.

Replace only that nested scope comparison with `_scope_match(other_baseline,
g_self)`, as its existing comment intends. The immediate parent SELECT contains
`g_self`, allowing normal correlation. Keep artifact, parameter, source and
forcing scope rules and version-first precedence unchanged. No index, migration,
API contract or publication changes.

Test a version-4 generation at station A with a version-5 baseline at station B:
A must remain readable. Retain same-scope higher-version baseline suppression,
artifact isolation and diagram forcing semantics. Run store/reader tests and
measure the corrected query against real BAFU data under a read-only transaction
and a short statement timeout. No deployment until owner merge.

Steps: independent review; one-line fix plus regression/docs; independent patch
review; targeted validation and PR; full CI before owner merge.

Independent Claude plan review approved. Verify row/generation scope agreement
on live data; retain reflected-reader tests. The regression must fail before
the fix. Do not use an exact-SQL-string test in place of behavioral coverage.

Live BAFU audit found zero score/generation scope mismatches. All five new
station/artifact/forcing isolation cases failed on the original predicate.

Validation: 71 store/API/reflected-reader tests passed after the fix. The
read-only corrected query returned 3,057 candidate rows for BAFU 2009 in 1.313 s
and 3,080 rows for BAFU 2150 in 1.327 s. Equivalent scalar response serialization
was 3,267,820 and 3,276,963 bytes respectively (before HTTP compression). The old
query was cancelled after exceeding 45 seconds; no completed old-query timing
is claimed. Both stations have active CMAL-small model assignments. Independent
Claude patch review found no blockers. No live package or configuration changed.
