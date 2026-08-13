"""Plan 155 T1/T1b — additively import Caravan's CAMELS-CH attributes.

Persistence orchestration for `adapters/caravan_attributes.py`'s pure parser:
join each parquet row onto its matching `(network, code)` station, and merge
the namespaced attributes into that station's basin via
`PgBasinStore.merge_namespaced_attributes` -- never the basin-PACKAGE
importer's correction branch (`store/basin_importer.py`), which is the WRONG
tool here (see that module's docstring and `merge_namespaced_attributes`'s):
this ADDS a disjoint keyspace, it never corrects an existing package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from sapphire_flow.adapters.caravan_attributes import load_caravan_attribute_rows
from sapphire_flow.services.caravan_statics import CARAVAN_PREFIX
from sapphire_flow.types.caravan_attributes import (
    CaravanImportProvenance,
    CaravanImportResult,
)

if TYPE_CHECKING:
    from pathlib import Path

    from sapphire_flow.store.basin_store import PgBasinStore
    from sapphire_flow.store.station_store import PgStationStore

log = structlog.get_logger(__name__)


def import_caravan_attributes(
    path: str | Path,
    *,
    station_store: PgStationStore,
    basin_store: PgBasinStore,
    network: str = "bafu",
    extractor_version: str | None = None,
    source_dataset_version: str | None = None,
    expected_codes: frozenset[str] | None = None,
) -> CaravanImportResult:
    """T0a's frozen manifest is a station-level concept; this joins on
    station identity (T1b step 1: `caravan_camels_ch_<code>` ->
    `(network, code)`) and merges into whatever basin that station is
    ALREADY bound to. A station with no `basin_id` yet (should not occur
    for the T0a manifest -- onboarding assigns a basin before a station is
    a discharge candidate) is reported, not silently skipped-and-forgotten.

    `expected_codes` -- the T0a manifest, when the caller has it -- lets
    this function report a manifest station that never showed up as a row
    in the parquet AT ALL, a gap `matched_codes`/`unmatched_codes` alone
    cannot surface (they only ever see codes the parquet actually
    contains). `source_dataset_version` threads the modeller-confirmed
    Caravan release into the returned provenance once known (plan's own
    guidance: ask BEFORE a real import -- see
    `types/caravan_attributes.py::CaravanImportProvenance`); omitted, the
    provenance keeps its honest "unconfirmed" placeholder.
    """
    rows = load_caravan_attribute_rows(path)

    matched: set[str] = set()
    unmatched: set[str] = set()
    no_basin: set[str] = set()

    for code, raw_attrs in rows.items():
        station = station_store.fetch_station_by_code(code, network)
        if station is None:
            unmatched.add(code)
            continue
        if station.basin_id is None:
            no_basin.add(code)
            continue
        namespaced = {f"{CARAVAN_PREFIX}{col}": val for col, val in raw_attrs.items()}
        basin_store.merge_namespaced_attributes(station.basin_id, attributes=namespaced)
        matched.add(code)

    missing_from_manifest: frozenset[str] = (
        frozenset(expected_codes) - rows.keys()
        if expected_codes is not None
        else frozenset()
    )

    log.info(
        "caravan_import.complete",
        matched=len(matched),
        unmatched=sorted(unmatched),
        stations_without_basin=sorted(no_basin),
        missing_from_manifest=sorted(missing_from_manifest),
    )
    provenance_kwargs: dict[str, str] = {}
    if source_dataset_version is not None:
        provenance_kwargs["source_dataset_version"] = source_dataset_version
    return CaravanImportResult(
        matched_codes=frozenset(matched),
        unmatched_codes=frozenset(unmatched),
        stations_without_basin=frozenset(no_basin),
        missing_from_manifest=missing_from_manifest,
        provenance=CaravanImportProvenance(
            extractor_version=extractor_version, **provenance_kwargs
        ),
    )
