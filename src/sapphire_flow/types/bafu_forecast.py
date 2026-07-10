"""Domain types for the BAFU forecast collector (Plan 111, route-C).

EVALUATION-ONLY, quarantined archive — see
``src/sapphire_flow/adapters/bafu_forecast.py`` and
``src/sapphire_flow/flows/collect_bafu_forecasts.py`` for the safeguards.
These types are never written to the operational DB and never referenced by
a ``ModelId``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime

BafuForecastVariant = Literal["q_forecast", "p_forecast"]
BafuMetric = Literal["discharge_ms", "masl"]
# BAFU's own map legend documents a third icon value, "missing" (station with
# no current data, `square_keine_daten.svg`). The inventory parse MUST accept it
# — a single no-data station would otherwise fail whole-FeatureCollection
# validation and take down the entire hourly run (no per-station isolation on
# the inventory fetch). The flow skips "missing" stations at collection time.
BafuIcon = Literal["river", "lake", "missing"]


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
    # a Plotly area polygon (forward upper edge then backward lower edge), so the
    # same valid_time appears twice with different values; point_index preserves
    # the polygon order so p25/p75 stay reconstructable from the parquet alone,
    # independent of any downstream row re-ordering.
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
