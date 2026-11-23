from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sapphire_flow.protocols.adapters import WeatherReanalysisSource
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.fine_tuning import FineTuningRequest
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing
    from sapphire_flow.types.ids import ArtifactId, StationId, TenantId
    from sapphire_flow.types.station import StationWeatherSource


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True, kw_only=True, slots=True)
class CandidateReceipt:
    request: FineTuningRequest
    tenant_id: TenantId
    artifact_id: ArtifactId
    artifact_sha256: str
    donor_sha256: str
    incumbent_ids: tuple[ArtifactId, ...]
    requested_stations: tuple[StationId, ...]
    fitted_stations: tuple[StationId, ...]
    fitting_sources_json: str
    evaluation_json: str | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "request": self.request.payload(),
            "configuration_sha256": self.request.sha256,
            "tenant_id": str(self.tenant_id),
            "artifact_id": str(self.artifact_id),
            "artifact_sha256": self.artifact_sha256,
            "donor_sha256": self.donor_sha256,
            "incumbent_ids": [str(aid) for aid in self.incumbent_ids],
            "requested_stations": [str(sid) for sid in self.requested_stations],
            "fitted_stations": [str(sid) for sid in self.fitted_stations],
            "fitting_sources": json.loads(self.fitting_sources_json),
            "evaluation": json.loads(self.evaluation_json)
            if self.evaluation_json is not None
            else None,
            "donor_pretraining_exposure": "not_verified",
        }

    @property
    def sha256(self) -> str:
        return hashlib.sha256(canonical_json(self.payload()).encode()).hexdigest()


class RecordingReanalysisSource:
    """Record compact provenance for rows actually returned by the chosen source."""

    def __init__(self, source: WeatherReanalysisSource) -> None:
        self.source = source
        self._products: dict[tuple[str, str, str], int] = {}
        self._dates: dict[tuple[str, str, str], set[str]] = {}
        self._digest = hashlib.sha256()
        self._calls = 0
        self._rows = 0

    def fetch_reanalysis(
        self,
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str],
    ) -> list[RawHistoricalForcing]:
        rows = self.source.fetch_reanalysis(station_configs, start, end, parameters)
        counts: dict[tuple[str, str, str], int] = {}
        digest = hashlib.sha256()
        for row in sorted(
            rows,
            key=lambda r: (
                str(r.station_id),
                r.valid_time,
                r.parameter,
                r.source,
                r.version,
                r.spatial_type.value,
                str(r.band_id),
                str(r.member_id),
            ),
        ):
            key = row.source, row.version, row.parameter
            counts[key] = counts.get(key, 0) + 1
            self._dates.setdefault(key, set()).add(row.valid_time.date().isoformat())
            # repr preserves NaN/missing evidence without writing non-JSON numbers.
            digest.update(
                canonical_json(
                    [
                        str(row.station_id),
                        row.valid_time.isoformat(),
                        row.parameter,
                        row.source,
                        row.version,
                        row.spatial_type.value,
                        row.band_id,
                        row.member_id,
                        repr(row.value),
                    ]
                ).encode()
            )
            digest.update(b"\n")
        self._calls += 1
        self._rows += len(rows)
        self._digest.update(
            canonical_json(
                {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "parameters": sorted(parameters),
                    "rows_sha256": digest.hexdigest(),
                }
            ).encode()
        )
        for key, count in counts.items():
            self._products[key] = self._products.get(key, 0) + count
        return rows

    def snapshot(self) -> str:
        return canonical_json(
            {
                "fetch_calls": self._calls,
                "returned_rows": self._rows,
                "read_sequence_sha256": self._digest.hexdigest(),
                "products": [
                    {
                        "source": source,
                        "version": version,
                        "parameter": parameter,
                        "returned_rows": count,
                        "utc_dates_with_rows": sorted(
                            self._dates[(source, version, parameter)]
                        ),
                    }
                    for (source, version, parameter), count in sorted(
                        self._products.items()
                    )
                ],
                "count_semantics": "rows returned, including repeated lookback reads",
                "date_semantics": "UTC dates with rows; not complete station coverage",
            }
        )

    def reset(self) -> None:
        self._products.clear()
        self._dates.clear()
        self._digest = hashlib.sha256()
        self._calls = 0
        self._rows = 0
