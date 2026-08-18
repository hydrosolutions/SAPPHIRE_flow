"""Shared LINDAS ``foen/hydro`` SPARQL query layer (Plan 186 T1/D2).

Both LINDAS callers query the same graph with the same predicate set — the
per-run operational fetch (``adapters/hydro_scraper.py``) and the
evaluation-only archive collector (``adapters/bafu_observation.py``, Plan
136). D2 shares **only** the query text/constants and a non-raising
subject-URI key helper here. Grouping, validation and failure policy stay
adapter-local (see the plan): the collector's grouping loop raises on a
malformed binding (correct for an archive snapshot), while ingest must never
let one bad binding anywhere fail every station. Reusing that raising loop
operationally would destroy the per-station isolation D4 requires, so it is
NOT part of this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sapphire_flow.types.bafu_observation import LindasKind

GRAPH_URI = "https://lindas.admin.ch/foen/hydro"
BASE_URL = "https://environment.ld.admin.ch/foen/hydro"
DIMENSION_URL = f"{BASE_URL}/dimension"
RIVER_SEGMENT = "/river/observation/"
LAKE_SEGMENT = "/lake/observation/"

# measurementTime supplies the row timestamp; the other three are the only
# recognized parameter predicates (mirrors both adapters' `_PARAM_MAP` target
# values).
DIMENSION_PREDICATES = (
    "discharge",
    "waterLevel",
    "waterTemperature",
    "measurementTime",
)

# Generous safety cap, well above the ~730 rows the live probe (2026-07-21)
# returned for 233 gauges — a bounded-request courtesy guard, not a
# per-page limit.
QUERY_LIMIT = 10000


def build_whole_graph_query() -> str:
    """The whole-``foen/hydro``-graph SPARQL query: projects ``?subject``
    rather than ``BIND``-ing one, so a single request covers every gauge."""
    predicates = ", ".join(f"<{DIMENSION_URL}/{name}>" for name in DIMENSION_PREDICATES)
    return (
        f"SELECT ?subject ?predicate ?object\n"
        f"FROM <{GRAPH_URI}>\n"
        f"WHERE {{\n"
        f"  ?subject ?predicate ?object .\n"
        f"  FILTER (?predicate IN ({predicates}))\n"
        f"}}\n"
        f"LIMIT {QUERY_LIMIT}"
    )


def parse_subject_key(subject: str) -> tuple[str, LindasKind] | None:
    """Non-raising subject-URI -> ``(gauge_code, lindas_kind)``.

    Returns ``None`` for a subject that matches neither the river nor the
    lake URI segment, rather than raising (D2) — ingest treats an
    unrecognised/unmatched subject as a lookup miss (D4/Q2's `NO_DATA`), not
    a batch-wide failure. The collector (Plan 136) wraps this and raises
    `AdapterError` on `None`, since an unparseable subject in the archive
    snapshot IS a schema-drift signal worth failing loudly for.
    """
    if RIVER_SEGMENT in subject:
        return subject.rsplit(RIVER_SEGMENT, 1)[1], "river"
    if LAKE_SEGMENT in subject:
        return subject.rsplit(LAKE_SEGMENT, 1)[1], "lake"
    return None
