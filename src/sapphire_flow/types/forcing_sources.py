"""Forcing-data provenance tags (Plan 071 T1).

``ForcingSource`` identifies *where a forcing value came from* — the upstream
data product. It is distinct from ``types.enums.ForcingType``, which is the
WMO-aligned *skill-interpretation* tag (e.g. how a forecast verification metric
should treat the input). One is data provenance; the other is statistical
semantics. They never substitute for one another.

``SOURCE_ATTRIBUTIONS`` carries the CC-BY acknowledgement string per source so
attribution is a property of the source tag, not a row-level column.
"""

from __future__ import annotations

from enum import Enum


class ForcingSource(Enum):
    """Provenance tag for a historical-forcing value.

    The string values are the literals persisted to
    ``historical_forcing.source``. ``CAMELS_CH`` keeps the pre-existing
    hyphenated on-disk literal to avoid a migration.
    """

    METEOSWISS_RHIRESD = "meteoswiss_rhiresd"
    METEOSWISS_RPRELIMD = "meteoswiss_rprelimd"
    METEOSWISS_TABSD = "meteoswiss_tabsd"
    METEOSWISS_TMIND = "meteoswiss_tmind"
    METEOSWISS_TMAXD = "meteoswiss_tmaxd"
    METEOSWISS_SRELD = "meteoswiss_sreld"
    CAMELS_CH = "camels-ch"
    # Reserved (no v0b consumer); kept for a potential v0c re-introduction of an
    # NWP-archive forcing source with a train/test-matched design.
    NWP_ARCHIVE = "nwp_archive"


SOURCE_ATTRIBUTIONS: dict[ForcingSource, str] = {
    ForcingSource.METEOSWISS_RHIRESD: "MeteoSwiss (CC-BY)",
    ForcingSource.METEOSWISS_RPRELIMD: "MeteoSwiss (CC-BY)",
    ForcingSource.METEOSWISS_TABSD: "MeteoSwiss (CC-BY)",
    ForcingSource.METEOSWISS_TMIND: "MeteoSwiss (CC-BY)",
    ForcingSource.METEOSWISS_TMAXD: "MeteoSwiss (CC-BY)",
    ForcingSource.METEOSWISS_SRELD: "MeteoSwiss (CC-BY)",
    ForcingSource.CAMELS_CH: "CAMELS-CH (CC-BY 4.0, Höge et al. 2023)",
    ForcingSource.NWP_ARCHIVE: "NWP archive (attribution per source NWP model)",
}
