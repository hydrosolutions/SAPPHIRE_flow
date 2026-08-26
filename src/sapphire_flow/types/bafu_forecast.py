"""Domain types for the BAFU forecast collector (Plan 111, route-C).

EVALUATION-ONLY, quarantined archive — see
``src/sapphire_flow/adapters/bafu_forecast.py`` and
``src/sapphire_flow/flows/collect_bafu_forecasts.py`` for the safeguards.
These types are never written to the operational DB and never referenced by
a ``ModelId``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime

BafuForecastVariant = Literal["q_forecast", "p_forecast"]
BafuMetric = Literal["discharge_ms", "masl"]

BafuWaterBodyKind = Literal["river", "lake"]


class BafuGaugeDataStatus(Enum):
    """Whether the live gauge behind a BAFU icon currently has data. A named
    domain state, not a bare bool (CLAUDE.md: enums over booleans for a
    two-state domain concept)."""

    PRESENT = auto()
    MISSING = auto()


@dataclass(frozen=True, kw_only=True, slots=True)
class BafuWaterBodyIcon:
    """A recognised BAFU map-symbol icon, modelled compositionally (Plan 160
    D1) as water-body kind plus whether the live gauge currently has data —
    derived from the ``{kind}`` / ``{kind}_missing`` pattern. This makes
    ``lake_missing`` supported before it has ever been seen in the feed:
    routing (``_variants_for_station``, D2) reads off ``kind`` alone, so an
    unseen ``{kind}_missing`` combination behaves identically to its
    data-present counterpart rather than requiring its own case.
    """

    kind: BafuWaterBodyKind
    data_status: BafuGaugeDataStatus


# BAFU's own map legend documents a fourth, LEGACY icon value, bare "missing"
# (station with no current data at all, `square_keine_daten.svg` — no water
# body kind). It is absent from the live feed (0/54 as of 2026-08-13) but the
# inventory parse must still accept it: nothing guarantees BAFU cannot emit it
# again, and there is no per-station isolation without this acceptance. It is
# deliberately NOT unified with BafuWaterBodyIcon's `{kind}_missing` case —
# unlike that case (which still names a water body and is confirmed to still
# publish a forecast, D8), bare "missing" states nothing about kind, so `()`
# (no fetch) remains its only defensible routing (D2).
BafuIcon = BafuWaterBodyIcon | Literal["missing"]


def parse_bafu_icon(raw: str) -> BafuIcon:
    """Parse BAFU's ``{kind}`` / ``{kind}_missing`` / legacy ``missing`` icon
    vocabulary into the compositional :data:`BafuIcon` (Plan 160 D1).

    Raises ``ValueError`` on anything else — an unrecognised icon is the
    caller's signal to skip the station (D4: fail-safe, never fall through to
    a default routing), not to guess.
    """
    match raw:
        case "river" | "lake":
            return BafuWaterBodyIcon(kind=raw, data_status=BafuGaugeDataStatus.PRESENT)
        case "river_missing":
            return BafuWaterBodyIcon(
                kind="river", data_status=BafuGaugeDataStatus.MISSING
            )
        case "lake_missing":
            return BafuWaterBodyIcon(
                kind="lake", data_status=BafuGaugeDataStatus.MISSING
            )
        case "missing":
            return "missing"
        case _:
            raise ValueError(f"unrecognised BAFU icon value: {raw!r}")


@dataclass(frozen=True, kw_only=True, slots=True)
class BafuForecastStation:
    key: str
    label: str
    icon: BafuIcon
    metric: BafuMetric
    unit: str
    plot_path: str


@dataclass(frozen=True, kw_only=True, slots=True)
class BafuStationInventory:
    stations: list[BafuForecastStation]
    produced_at: UtcDatetime
    # Count of features that failed per-feature validation and were skipped
    # rather than aborting the whole inventory (Plan 160 D3). Defaults to 0 so
    # existing construction sites (tests, and any future caller building an
    # inventory directly) do not need to name it.
    skipped_count: int = 0
    # Plan 198 T9a/O3: the raw inventory GeoJSON payload, preserved so the
    # flow can archive it (the collector fetches it hourly and otherwise
    # discards it — every hour without this is station-inventory history
    # that cannot be recovered, F7). Defaults to None so existing
    # construction sites that build an inventory directly (tests) need not
    # name it. Not parsed or used here — see T9b (deferred) for
    # river/display_name extraction.
    raw_payload: dict[str, Any] | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class BafuForecastRow:
    station_key: str
    metric: BafuMetric
    unit: str
    issued_at: UtcDatetime
    produced_at: UtcDatetime
    valid_time: UtcDatetime
    trace_name: str
    # Position of this point within its trace. The "25.-75. Percentile" band is
    # a Plotly area polygon (forward LOWER edge — p25 — then backward UPPER
    # edge — p75; measured 2026-08-21 on three live runs across three
    # stations and corroborated by the checked-in reference fixture,
    # Plan 198 F3 — an earlier version of this comment had the two edges
    # backwards), so the same valid_time appears twice with different
    # values; point_index preserves the polygon order so p25/p75 stay
    # reconstructable from the parquet alone, independent of any downstream
    # row re-ordering.
    point_index: int
    value: float | None


@dataclass(frozen=True, kw_only=True, slots=True)
class BafuVariantFetch:
    station_key: str
    variant: BafuForecastVariant
    metric: BafuMetric
    issued_at: UtcDatetime
    rows: list[BafuForecastRow]
    raw_payload: dict[str, Any]
