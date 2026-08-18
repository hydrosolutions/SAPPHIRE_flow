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
    overlap_start_year: int = 2020
    """D5a — the JJAS overlap window's first year for THIS station. Not
    uniform across pairs: 'Lukla's overlap is only 2021-2023.'"""
    overlap_end_year: int = 2023
    """D5a — the JJAS overlap window's last year (inclusive) for this
    station."""
    dhm_start_year: int = 2020
    """D11/D5b — the first JJAS season-year of the DHM FULL RECORD, which is
    what the verdict is adjudicated on. The authoritative DHM source
    workbook spans 2020-01-01 -> 2025-12-31 in its entirety
    (`docs/design/dhm-precipitation-vision.md:20`)."""
    dhm_end_year: int = 2025
    """D11/D5b — the last JJAS season-year (inclusive) of the DHM full
    record. 2020-2025 is 6 season-years, clearing D5's 5-season adequacy
    threshold."""
    pyramid_start_year: int = 2002
    """D11/D5b — the first JJAS season-year of the PYRAMID full record
    (Namche 2002, Lukla 2005). Per-station, unlike DHM's shared span."""
    pyramid_end_year: int = 2023
    """D11/D5b — the last JJAS season-year (inclusive) of the Pyramid full
    record; both Lvl1 files end in 2023."""

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
        if self.overlap_start_year > self.overlap_end_year:
            raise ValueError(
                f"overlap_start_year ({self.overlap_start_year}) must be <= "
                f"overlap_end_year ({self.overlap_end_year}) for "
                f"{self.dhm_station!r}"
            )
        if self.dhm_start_year > self.dhm_end_year:
            raise ValueError(
                f"dhm_start_year ({self.dhm_start_year}) must be <= "
                f"dhm_end_year ({self.dhm_end_year}) for {self.dhm_station!r}"
            )
        if self.pyramid_start_year > self.pyramid_end_year:
            raise ValueError(
                f"pyramid_start_year ({self.pyramid_start_year}) must be <= "
                f"pyramid_end_year ({self.pyramid_end_year}) for "
                f"{self.dhm_station!r}"
            )
        # D5a — the overlap window is by definition the CONTEMPORANEOUS
        # slice of the two full records; a window reaching outside either
        # one would silently report a corroboration population that cannot
        # exist.
        common_start = max(self.dhm_start_year, self.pyramid_start_year)
        common_end = min(self.dhm_end_year, self.pyramid_end_year)
        if self.overlap_start_year < common_start or self.overlap_end_year > common_end:
            raise ValueError(
                f"overlap window {self.overlap_start_year}-"
                f"{self.overlap_end_year} lies outside the common span of "
                f"the two full records ({common_start}-{common_end}) for "
                f"{self.dhm_station!r}"
            )


COLOCATED_PAIRS: tuple[ColocatedPair, ...] = (
    ColocatedPair(
        dhm_station=Station("Lukla Airport"),
        pyramid_station=Station("AWS3 Lukla"),
        pyramid_csv_filename="AWS3_Z2660_Lvl1.csv",
        separation_km=1.4,
        elevation_delta_m=200.0,
        overlap_start_year=2021,
        pyramid_start_year=2005,
    ),
    ColocatedPair(
        dhm_station=Station("Syangboche Airport"),
        pyramid_station=Station("AWS5 Namche"),
        pyramid_csv_filename="AWS5_Z3570_Lvl1.csv",
        separation_km=1.9,
        elevation_delta_m=130.0,
    ),
)
