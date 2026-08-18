"""Plan 182 (M-A10) — the two co-located DHM/Pyramid station pairs.

Pyramid Meteorological Network (Salerno et al. 2025, ESSD 17, 4293; Zenodo
`10.5281/zenodo.15211352`, CC BY 4.0). Only these two pairs qualify — see
the plan's Non-goals: Humde/Olangchunggola have no co-located Pyramid
partner (different valleys, Manang/Kanchenjunga, not the Khumbu).
"""

from __future__ import annotations

from dataclasses import dataclass

from scripts.dhm_precip.domain_types import Station


@dataclass(frozen=True, kw_only=True, slots=True)
class ColocatedPair:
    dhm_station: Station
    """The canonical (suffix-stripped) name shared with `loader.py`'s
    workbook station names, e.g. `"Lukla Airport"`."""
    pyramid_station: Station
    pyramid_csv_filename: str
    """Under `data/dhm_precip/pyramid/` (gitignored, Lvl1 only — Non-goals
    bars the Lvl2 gap-filled monthly reconstruction)."""
    separation_km: float
    elevation_delta_m: float

    def __post_init__(self) -> None:
        if self.separation_km <= 0.0:
            raise ValueError(
                f"separation_km must be positive, got {self.separation_km} "
                f"for {self.dhm_station!r}"
            )
        if self.elevation_delta_m < 0.0:
            raise ValueError(
                f"elevation_delta_m must be >= 0, got {self.elevation_delta_m} "
                f"for {self.dhm_station!r}"
            )


COLOCATED_PAIRS: tuple[ColocatedPair, ...] = (
    ColocatedPair(
        dhm_station=Station("Lukla Airport"),
        pyramid_station=Station("AWS3 Lukla"),
        pyramid_csv_filename="AWS3_Z2660_Lvl1.csv",
        separation_km=1.4,
        elevation_delta_m=200.0,
    ),
    ColocatedPair(
        dhm_station=Station("Syangboche Airport"),
        pyramid_station=Station("AWS5 Namche"),
        pyramid_csv_filename="AWS5_Z3570_Lvl1.csv",
        separation_km=1.9,
        elevation_delta_m=130.0,
    ),
)
