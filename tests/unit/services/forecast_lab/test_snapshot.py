"""Plan 198 T3 — `build_snapshot()`, the single assembly function (D1).

Locks: AC10 (linear-quantile parity), AC11 (models stay distinguishable),
AC14 (daily completeness boundary), AC15 (no hindcast/skill table access,
structural), AC16/AC24 (deterministic ordering + is_primary tiebreak),
AC20 (an inactive assignment never wins is_primary), AC21 (a
`quantiles`-representation forecast becomes an explicit
`unsupported_representation` entry, never relabelled), AC22 (a poisoned
DB read propagates rather than degrading to partial), AC27 (injected
clock -> byte-identical builds, D20), AC28 (an unavailable model entry
carries only `reason`).
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import polars as pl
import pytest

from sapphire_flow.api.forecast_lab_schemas import (
    BafuForecastAvailableSchema,
    ForecastLabSnapshot,
    QuantileEnvelopeSchema,
    SapphireForecastAvailableSchema,
    SapphireForecastUnavailableSchema,
)
from sapphire_flow.services.forecast_lab.db_sources import ForecastLabStores
from sapphire_flow.services.forecast_lab.snapshot import build_snapshot
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import (
    ForecastStatus,
    ModelAssignmentStatus,
    NwpCycleSource,
    QcStatus,
)
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.ids import ForecastId, ModelId, StationId
from sapphire_flow.types.station import ModelAssignment
from tests.conftest import make_observation, make_station_config
from tests.fakes.fake_stores import (
    FakeArtifactProvenanceStore,
    FakeBasinStore,
    FakeForecastStore,
    FakeModelArtifactStore,
    FakeModelStore,
    FakeObservationStore,
    FakeStationStore,
)

_EPOCH = ensure_utc(datetime(2026, 8, 21, 10, 45, 0, tzinfo=UTC))


def _stores(
    *,
    station_store: FakeStationStore | None = None,
    observation_store: FakeObservationStore | None = None,
    forecast_store: FakeForecastStore | None = None,
) -> ForecastLabStores:
    return ForecastLabStores(
        station_store=station_store or FakeStationStore(),
        observation_store=observation_store or FakeObservationStore(),
        forecast_store=forecast_store or FakeForecastStore(),
        model_store=FakeModelStore(),
        artifact_store=FakeModelArtifactStore(),
        provenance_store=FakeArtifactProvenanceStore(),
        basin_store=FakeBasinStore(),
    )


def _frozen_clock(t: UtcDatetime = _EPOCH) -> Any:
    return lambda: t


def _members_ensemble(
    station_id: StationId,
    *,
    issued_at: UtcDatetime,
    rng: random.Random,
    n_members: int = 5,
    valid_times: list[UtcDatetime] | None = None,
    model_id: ModelId | None = None,
) -> ForecastEnsemble:
    vts = valid_times or [ensure_utc(issued_at + timedelta(days=d)) for d in (1, 2)]
    rows = [
        {"valid_time": vt, "member_id": m, "value": rng.uniform(10.0, 100.0)}
        for vt in vts
        for m in range(n_members)
    ]
    df = pl.DataFrame(rows).with_columns(
        pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
        pl.col("member_id").cast(pl.Int32),
    )
    return ForecastEnsemble.from_members(
        station_id=station_id,
        issued_at=issued_at,
        parameter="discharge",
        units="m3/s",
        time_step=timedelta(days=1),
        values=df,
        model_id=model_id,
    )


def _forecast(
    *,
    station_id: StationId,
    model_id: ModelId,
    ensemble: ForecastEnsemble,
    issued_at: UtcDatetime,
) -> OperationalForecast:
    return OperationalForecast(
        id=ForecastId(uuid4()),
        station_id=station_id,
        model_id=model_id,
        model_artifact_id=None,
        issued_at=issued_at,
        nwp_cycle_reference_time=issued_at,
        nwp_cycle_source=NwpCycleSource.PRIMARY,
        representation=ensemble.representation,
        status=ForecastStatus.RAW,
        version=1,
        warm_up_source=None,
        warm_up_state_age_hours=None,
        observation_staleness_hours=0.5,
        ensemble=ensemble,
        created_at=issued_at,
        updated_at=issued_at,
    )


def _active_assignment(
    station_id: StationId, model_id: ModelId, *, priority: int
) -> ModelAssignment:
    return ModelAssignment(
        station_id=station_id,
        model_id=model_id,
        time_step=timedelta(days=1),
        status=ModelAssignmentStatus.ACTIVE,
        priority=priority,
        created_at=_EPOCH,
    )


class TestQuantileSummaryMatchesNumpyLinear:
    def test_summary_matches_numpy_quantile_linear(self) -> None:
        station = make_station_config(code="2009")
        station_store = FakeStationStore()
        station_store.store_station(station)
        station_store.store_model_assignment(
            _active_assignment(station.id, ModelId("nwp_regression"), priority=10)
        )
        rng = random.Random(7)
        vt = ensure_utc(_EPOCH + timedelta(days=1))
        # n_members=10 -> (n-1)=9 is not a multiple of 4, so the 0.25/0.75
        # quantile index is fractional (2.25/6.75) and genuinely exercises
        # linear interpolation — a round member count (e.g. 21) would make
        # every quantile land on an exact order statistic, which "nearest"
        # would also satisfy and the test would pass for the wrong reason.
        ensemble = _members_ensemble(
            station.id, issued_at=_EPOCH, rng=rng, n_members=10, valid_times=[vt]
        )
        forecast = _forecast(
            station_id=station.id,
            model_id=ModelId("nwp_regression"),
            ensemble=ensemble,
            issued_at=_EPOCH,
        )
        forecast_store = FakeForecastStore()
        forecast_store.store_forecast(forecast)
        stores = _stores(station_store=station_store, forecast_store=forecast_store)

        snapshot = build_snapshot(
            stores,
            stations=[station],
            archive_base_path=None,
            clock=_frozen_clock(),
        )

        entry = snapshot.stations[0].sapphire_forecasts[0]
        assert isinstance(entry, SapphireForecastAvailableSchema)
        point = entry.points[0]

        raw_values = ensemble.values.filter(pl.col("valid_time") == vt)[
            "value"
        ].to_numpy()
        assert point.minimum == pytest.approx(float(np.min(raw_values)))
        assert point.p25 == pytest.approx(
            float(np.quantile(raw_values, 0.25, method="linear"))
        )
        assert point.median == pytest.approx(
            float(np.quantile(raw_values, 0.5, method="linear"))
        )
        assert point.p75 == pytest.approx(
            float(np.quantile(raw_values, 0.75, method="linear"))
        )
        assert point.maximum == pytest.approx(float(np.max(raw_values)))


class TestMultipleModelsStayDistinguishable:
    def test_two_models_are_not_merged(self) -> None:
        station = make_station_config(code="2009")
        station_store = FakeStationStore()
        station_store.store_station(station)
        station_store.store_model_assignment(
            _active_assignment(station.id, ModelId("nwp_regression"), priority=10)
        )
        station_store.store_model_assignment(
            _active_assignment(
                station.id, ModelId("linear_regression_daily"), priority=20
            )
        )
        forecast_store = FakeForecastStore()
        for i, model_id in enumerate(("nwp_regression", "linear_regression_daily")):
            ensemble = _members_ensemble(
                station.id, issued_at=_EPOCH, rng=random.Random(i), n_members=5
            )
            forecast_store.store_forecast(
                _forecast(
                    station_id=station.id,
                    model_id=ModelId(model_id),
                    ensemble=ensemble,
                    issued_at=_EPOCH,
                )
            )
        stores = _stores(station_store=station_store, forecast_store=forecast_store)

        snapshot = build_snapshot(
            stores, stations=[station], archive_base_path=None, clock=_frozen_clock()
        )

        keys = {
            e.model.key
            for e in snapshot.stations[0].sapphire_forecasts
            if isinstance(e, SapphireForecastAvailableSchema)
        }
        assert keys == {"nwp_regression", "linear_regression_daily"}


class TestIsPrimaryTiebreak:
    """AC16/AC24/D12: equal-priority assignments tie-break on model_key
    ascending, deterministically across shuffled input order."""

    def test_lowest_model_key_wins_at_equal_priority(self) -> None:
        station = make_station_config(code="2009")
        station_store = FakeStationStore()
        station_store.store_station(station)
        for model_id in ("zeta_model", "alpha_model"):
            station_store.store_model_assignment(
                _active_assignment(station.id, ModelId(model_id), priority=0)
            )
        forecast_store = FakeForecastStore()
        for i, model_id in enumerate(("zeta_model", "alpha_model")):
            ensemble = _members_ensemble(
                station.id, issued_at=_EPOCH, rng=random.Random(i), n_members=3
            )
            forecast_store.store_forecast(
                _forecast(
                    station_id=station.id,
                    model_id=ModelId(model_id),
                    ensemble=ensemble,
                    issued_at=_EPOCH,
                )
            )
        stores = _stores(station_store=station_store, forecast_store=forecast_store)

        snapshot = build_snapshot(
            stores, stations=[station], archive_base_path=None, clock=_frozen_clock()
        )

        primaries = {
            e.model.key
            for e in snapshot.stations[0].sapphire_forecasts
            if isinstance(e, SapphireForecastAvailableSchema) and e.model.is_primary
        }
        assert primaries == {"alpha_model"}

    def test_stations_are_ordered_by_code(self) -> None:
        stations = [
            make_station_config(code=code, station_id=StationId(uuid4()))
            for code in ("3000", "1000", "2000")
        ]
        station_store = FakeStationStore()
        for s in stations:
            station_store.store_station(s)
        stores = _stores(station_store=station_store)

        snapshot = build_snapshot(
            stores, stations=stations, archive_base_path=None, clock=_frozen_clock()
        )

        assert [s.station.code for s in snapshot.stations] == ["1000", "2000", "3000"]


class TestInactiveAssignmentNeverWinsPrimary:
    def test_inactive_assignment_is_absent_and_cannot_win(self) -> None:
        station = make_station_config(code="2009")
        station_store = FakeStationStore()
        station_store.store_station(station)
        station_store.store_model_assignment(
            _active_assignment(station.id, ModelId("active_model"), priority=50)
        )
        station_store.store_model_assignment(
            ModelAssignment(
                station_id=station.id,
                model_id=ModelId("inactive_model"),
                time_step=timedelta(days=1),
                status=ModelAssignmentStatus.INACTIVE,
                priority=1,
                created_at=_EPOCH,
            )
        )
        forecast_store = FakeForecastStore()
        for model_id in ("active_model", "inactive_model"):
            ensemble = _members_ensemble(
                station.id, issued_at=_EPOCH, rng=random.Random(1), n_members=3
            )
            forecast_store.store_forecast(
                _forecast(
                    station_id=station.id,
                    model_id=ModelId(model_id),
                    ensemble=ensemble,
                    issued_at=_EPOCH,
                )
            )
        stores = _stores(station_store=station_store, forecast_store=forecast_store)

        snapshot = build_snapshot(
            stores, stations=[station], archive_base_path=None, clock=_frozen_clock()
        )

        entries = snapshot.stations[0].sapphire_forecasts
        keys = {e.model.key for e in entries}
        assert keys == {"active_model"}
        assert entries[0].model.is_primary is True  # type: ignore[union-attr]


class TestUnsupportedRepresentation:
    def test_quantiles_representation_yields_explicit_unavailable(self) -> None:
        station = make_station_config(code="2009")
        station_store = FakeStationStore()
        station_store.store_station(station)
        station_store.store_model_assignment(
            _active_assignment(station.id, ModelId("nwp_regression"), priority=10)
        )
        rows = [
            {
                "valid_time": ensure_utc(_EPOCH + timedelta(days=1)),
                "quantile": q,
                "value": 1.0,
            }
            for q in (0.02, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.98)
        ]
        df = pl.DataFrame(rows).with_columns(
            pl.col("valid_time").cast(pl.Datetime("us", "UTC"))
        )
        ensemble = ForecastEnsemble.from_quantiles(
            station_id=station.id,
            issued_at=_EPOCH,
            parameter="discharge",
            units="m3/s",
            time_step=timedelta(days=1),
            values=df,
            model_id=ModelId("nwp_regression"),
        )
        forecast_store = FakeForecastStore()
        forecast_store.store_forecast(
            _forecast(
                station_id=station.id,
                model_id=ModelId("nwp_regression"),
                ensemble=ensemble,
                issued_at=_EPOCH,
            )
        )
        stores = _stores(station_store=station_store, forecast_store=forecast_store)

        snapshot = build_snapshot(
            stores, stations=[station], archive_base_path=None, clock=_frozen_clock()
        )

        entry = snapshot.stations[0].sapphire_forecasts[0]
        assert isinstance(entry, SapphireForecastUnavailableSchema)
        assert entry.reason == "unsupported_representation"


class TestUnavailableModelEntryShape:
    """AC28 — carries `reason` and nothing else (D19)."""

    def test_no_forecast_entry_has_only_model_and_reason(self) -> None:
        station = make_station_config(code="2009")
        station_store = FakeStationStore()
        station_store.store_station(station)
        station_store.store_model_assignment(
            _active_assignment(station.id, ModelId("no_forecast_model"), priority=10)
        )
        stores = _stores(station_store=station_store)

        snapshot = build_snapshot(
            stores, stations=[station], archive_base_path=None, clock=_frozen_clock()
        )

        entry = snapshot.stations[0].sapphire_forecasts[0]
        assert isinstance(entry, SapphireForecastUnavailableSchema)
        assert entry.reason == "no_forecast"
        assert entry.model_fields_set == {
            "model",
            "reason",
        } or entry.model_fields_set <= {"available", "model", "reason"}


class TestDailyCompletenessBoundary:
    """AC14 — BAFU daily completeness gate at exactly 22 vs 21 hours."""

    def _write_run(self, base_path: Path, station_code: str, n_hours: int) -> None:
        issued_at = ensure_utc(datetime(2026, 8, 22, 0, 0, 0, tzinfo=UTC))
        valid_times = [
            ensure_utc(issued_at + timedelta(hours=h)) for h in range(n_hours)
        ]
        rows: list[dict[str, Any]] = []
        for i, vt in enumerate(valid_times):
            for trace, val in (
                ("Min / Max", 10.0),
                ("Median", 20.0),
                ("Min. / Max.", 30.0),
            ):
                rows.append(
                    {
                        "station_key": station_code,
                        "metric": "discharge_ms",
                        "unit": "m3/s",
                        "issued_at": issued_at,
                        "produced_at": issued_at,
                        "valid_time": vt,
                        "trace_name": trace,
                        "point_index": i,
                        "value": val,
                    }
                )
        # 25.-75. Percentile: forward (p25) then backward (p75) then closer.
        band = valid_times + list(reversed(valid_times)) + [valid_times[0]]
        for i, vt in enumerate(band):
            rows.append(
                {
                    "station_key": station_code,
                    "metric": "discharge_ms",
                    "unit": "m3/s",
                    "issued_at": issued_at,
                    "produced_at": issued_at,
                    "valid_time": vt,
                    "trace_name": "25.-75. Percentile",
                    "point_index": i,
                    "value": 15.0 if i < n_hours else 25.0,
                }
            )
        schema = {
            "station_key": pl.Utf8,
            "metric": pl.Utf8,
            "unit": pl.Utf8,
            "issued_at": pl.Datetime("us", "UTC"),
            "produced_at": pl.Datetime("us", "UTC"),
            "valid_time": pl.Datetime("us", "UTC"),
            "trace_name": pl.Utf8,
            "point_index": pl.Int64,
            "value": pl.Float64,
        }
        frame = pl.DataFrame(rows, schema=schema)
        parsed_dir = base_path / "parsed"
        parsed_dir.mkdir(parents=True, exist_ok=True)
        stamp = issued_at.strftime("%Y%m%dT%H%M%SZ")
        frame.write_parquet(parsed_dir / f"{station_code}_q_forecast_{stamp}.parquet")

    def test_22_hours_is_complete_21_is_not(self, tmp_path: Path) -> None:
        station = make_station_config(code="2009")
        station_store = FakeStationStore()
        station_store.store_station(station)
        self._write_run(tmp_path, "2009", 22)
        stores = _stores(station_store=station_store)
        now = ensure_utc(datetime(2026, 8, 23, 0, 0, 0, tzinfo=UTC))

        snapshot = build_snapshot(
            stores, stations=[station], archive_base_path=tmp_path, clock=lambda: now
        )
        bafu = snapshot.stations[0].bafu_forecast
        assert isinstance(bafu, BafuForecastAvailableSchema)
        rows = snapshot.stations[0].aligned_daily_comparison
        assert len(rows) == 1
        assert rows[0].bafu is not None
        assert rows[0].bafu.hour_count == 22
        assert rows[0].bafu.complete is True

    def test_21_hours_is_incomplete(self, tmp_path: Path) -> None:
        station = make_station_config(code="2009")
        station_store = FakeStationStore()
        station_store.store_station(station)
        self._write_run(tmp_path, "2009", 21)
        stores = _stores(station_store=station_store)
        now = ensure_utc(datetime(2026, 8, 23, 0, 0, 0, tzinfo=UTC))

        snapshot = build_snapshot(
            stores, stations=[station], archive_base_path=tmp_path, clock=lambda: now
        )
        rows = snapshot.stations[0].aligned_daily_comparison
        assert len(rows) == 1
        assert rows[0].bafu is not None
        assert rows[0].bafu.hour_count == 21
        assert rows[0].bafu.complete is False


class TestNwpCycleSourceFallbackIsBenign:
    """AC12 — a fallback-cycle forecast produces no error/warning/degraded
    status."""

    def test_fallback_cycle_source_forecast_is_available_and_ok(self) -> None:
        station = make_station_config(code="2009")
        station_store = FakeStationStore()
        station_store.store_station(station)
        station_store.store_model_assignment(
            _active_assignment(station.id, ModelId("nwp_regression"), priority=10)
        )
        ensemble = _members_ensemble(station.id, issued_at=_EPOCH, rng=random.Random(1))
        forecast = OperationalForecast(
            id=ForecastId(uuid4()),
            station_id=station.id,
            model_id=ModelId("nwp_regression"),
            model_artifact_id=None,
            issued_at=_EPOCH,
            nwp_cycle_reference_time=_EPOCH,
            nwp_cycle_source=NwpCycleSource.FALLBACK,
            representation=ensemble.representation,
            status=ForecastStatus.RAW,
            version=1,
            warm_up_source=None,
            warm_up_state_age_hours=None,
            observation_staleness_hours=None,
            ensemble=ensemble,
            created_at=_EPOCH,
            updated_at=_EPOCH,
        )
        forecast_store = FakeForecastStore()
        forecast_store.store_forecast(forecast)
        stores = _stores(station_store=station_store, forecast_store=forecast_store)

        snapshot = build_snapshot(
            stores, stations=[station], archive_base_path=None, clock=_frozen_clock()
        )

        entry = snapshot.stations[0].sapphire_forecasts[0]
        assert isinstance(entry, SapphireForecastAvailableSchema)
        assert snapshot.status.sapphire_forecasts.status == "ok"


class TestNoHindcastOrSkillTableAccess:
    """AC15/D3 — structural: `ForecastLabStores` has no hindcast/skill
    field at all, so build_snapshot cannot query those tables by
    construction."""

    def test_forecast_lab_stores_has_no_hindcast_or_skill_field(self) -> None:
        field_names = {f for f in ForecastLabStores.__dataclass_fields__}
        assert "hindcast_store" not in field_names
        assert "skill_store" not in field_names

    def test_verification_is_always_insufficient_data(self) -> None:
        station = make_station_config(code="2009")
        station_store = FakeStationStore()
        station_store.store_station(station)
        stores = _stores(station_store=station_store)

        snapshot = build_snapshot(
            stores, stations=[station], archive_base_path=None, clock=_frozen_clock()
        )
        assert snapshot.stations[0].verification.status == "insufficient_data"


class TestPoisonedTransactionPropagates:
    """AC22/D13 — a SQLAlchemyError from the shared transaction must
    escape build_snapshot, not degrade to a partial snapshot."""

    def test_observation_store_error_propagates(self) -> None:
        from sqlalchemy.exc import SQLAlchemyError

        class _PoisonedObservationStore(FakeObservationStore):
            def fetch_observations(self, *args: Any, **kwargs: Any) -> Any:
                raise SQLAlchemyError("connection reset")

        station = make_station_config(code="2009")
        station_store = FakeStationStore()
        station_store.store_station(station)
        stores = _stores(
            station_store=station_store, observation_store=_PoisonedObservationStore()
        )

        with pytest.raises(SQLAlchemyError):
            build_snapshot(
                stores,
                stations=[station],
                archive_base_path=None,
                clock=_frozen_clock(),
            )


class TestInjectedClockDeterminism:
    """AC27/D20 — a frozen clock yields byte-identical documents across two
    builds; no `datetime.now()` anywhere in `services/forecast_lab/`."""

    def test_two_builds_with_a_frozen_clock_are_byte_identical(self) -> None:
        station = make_station_config(code="2009")
        station_store = FakeStationStore()
        station_store.store_station(station)
        stores = _stores(station_store=station_store)

        snap1 = build_snapshot(
            stores, stations=[station], archive_base_path=None, clock=_frozen_clock()
        )
        snap2 = build_snapshot(
            stores, stations=[station], archive_base_path=None, clock=_frozen_clock()
        )

        assert json.dumps(snap1.model_dump(mode="json")) == json.dumps(
            snap2.model_dump(mode="json")
        )
        assert snap1.generated_at == _EPOCH
        assert snap1.snapshot_id == "fls1-20260821T104500Z"

    def test_no_bare_datetime_now_in_forecast_lab_services(self) -> None:
        import ast

        root = (
            Path(__file__).resolve().parents[4]
            / "src/sapphire_flow/services/forecast_lab"
        )
        offenders: list[str] = []
        for path in sorted(root.glob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "now"
                ):
                    offenders.append(f"{path.name}:{node.lineno}")
        assert offenders == [], f"datetime.now()-shaped calls found: {offenders}"


class TestForecastLabSnapshotIsValidPydanticModel:
    def test_build_snapshot_returns_a_forecast_lab_snapshot(self) -> None:
        station = make_station_config(code="2009")
        station_store = FakeStationStore()
        station_store.store_station(station)
        stores = _stores(station_store=station_store)

        snapshot = build_snapshot(
            stores, stations=[station], archive_base_path=None, clock=_frozen_clock()
        )
        assert isinstance(snapshot, ForecastLabSnapshot)
        # No observation, no BAFU archive (archive_base_path=None), no model
        # assignment for the one requested station: every source is
        # unavailable for the only station in scope, so D16 rule 2 fires.
        assert snapshot.status.observations.status == "missing"
        assert snapshot.status.bafu_forecasts.status == "missing"
        assert snapshot.status.sapphire_forecasts.status == "missing"
        assert snapshot.status.overall == "unavailable"


def _write_minimal_bafu_run(
    base_path: Path, station_code: str, *, issued_at: UtcDatetime, n_hours: int = 22
) -> None:
    """A complete, reconstructable BAFU archive run — just enough for
    `read_latest_bafu_run` to report the station's BAFU forecast source as
    available. Mirrors `TestDailyCompletenessBoundary._write_run`."""
    valid_times = [ensure_utc(issued_at + timedelta(hours=h)) for h in range(n_hours)]
    rows: list[dict[str, Any]] = []
    for i, vt in enumerate(valid_times):
        for trace, val in (
            ("Min / Max", 10.0),
            ("Median", 20.0),
            ("Min. / Max.", 30.0),
        ):
            rows.append(
                {
                    "station_key": station_code,
                    "metric": "discharge_ms",
                    "unit": "m3/s",
                    "issued_at": issued_at,
                    "produced_at": issued_at,
                    "valid_time": vt,
                    "trace_name": trace,
                    "point_index": i,
                    "value": val,
                }
            )
    band = valid_times + list(reversed(valid_times)) + [valid_times[0]]
    for i, vt in enumerate(band):
        rows.append(
            {
                "station_key": station_code,
                "metric": "discharge_ms",
                "unit": "m3/s",
                "issued_at": issued_at,
                "produced_at": issued_at,
                "valid_time": vt,
                "trace_name": "25.-75. Percentile",
                "point_index": i,
                "value": 15.0 if i < n_hours else 25.0,
            }
        )
    schema = {
        "station_key": pl.Utf8,
        "metric": pl.Utf8,
        "unit": pl.Utf8,
        "issued_at": pl.Datetime("us", "UTC"),
        "produced_at": pl.Datetime("us", "UTC"),
        "valid_time": pl.Datetime("us", "UTC"),
        "trace_name": pl.Utf8,
        "point_index": pl.Int64,
        "value": pl.Float64,
    }
    frame = pl.DataFrame(rows, schema=schema)
    parsed_dir = base_path / "parsed"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    stamp = issued_at.strftime("%Y%m%dT%H%M%SZ")
    frame.write_parquet(parsed_dir / f"{station_code}_q_forecast_{stamp}.parquet")


class TestMultiStationSourceStatusAggregation:
    """D16 — the document-level `status.<source>` is a roll-up over every
    station in the request (`_aggregate_source_status`): `ok` only when
    every requested station has that source, `missing` when none do, and
    `error` when some but not all do. This is AC2's headline scenario (two
    stations, one request) exercised with genuinely different per-station
    availability, not the degenerate single-station or all-missing cases
    the rest of this file covers — and it pins the exact per-source status,
    message and `status.overall` roll-up (AC13), rather than merely
    asserting the result is one of the three valid enum values."""

    def test_mixed_availability_across_two_stations_pins_exact_status(
        self, tmp_path: Path
    ) -> None:
        station_a = make_station_config(code="2009", station_id=StationId(uuid4()))
        station_b = make_station_config(code="2016", station_id=StationId(uuid4()))
        station_store = FakeStationStore()
        station_store.store_station(station_a)
        station_store.store_station(station_b)

        # Observations: BOTH stations have a measured/qc_passed/discharge
        # reading in the window -> status.observations == "ok".
        observation_store = FakeObservationStore()
        observation_store.store_observations(
            [
                make_observation(
                    station_id=station_a.id,
                    parameter="discharge",
                    qc_status=QcStatus.QC_PASSED,
                    timestamp=ensure_utc(_EPOCH - timedelta(hours=1)),
                    rng=random.Random(101),
                ),
                make_observation(
                    station_id=station_b.id,
                    parameter="discharge",
                    qc_status=QcStatus.QC_PASSED,
                    timestamp=ensure_utc(_EPOCH - timedelta(hours=1)),
                    rng=random.Random(102),
                ),
            ]
        )

        # BAFU archive: only station_a has a run on disk ->
        # status.bafu_forecasts == "error" (1 of 2 missing).
        _write_minimal_bafu_run(
            tmp_path, "2009", issued_at=ensure_utc(_EPOCH - timedelta(hours=2))
        )

        # SAPPHIRE forecasts: neither station has an active model
        # assignment -> status.sapphire_forecasts == "missing" (0 of 2).

        stores = _stores(
            station_store=station_store, observation_store=observation_store
        )

        snapshot = build_snapshot(
            stores,
            stations=[station_a, station_b],
            archive_base_path=tmp_path,
            clock=_frozen_clock(),
        )

        assert snapshot.status.observations.status == "ok"
        assert snapshot.status.observations.message is None

        assert snapshot.status.bafu_forecasts.status == "error"
        assert (
            snapshot.status.bafu_forecasts.message
            == "1 of 2 stations missing a BAFU run in the requested window"
        )

        assert snapshot.status.sapphire_forecasts.status == "missing"
        assert (
            snapshot.status.sapphire_forecasts.message
            == "no station has a SAPPHIRE forecast"
        )

        # Not all-ok (bafu/sapphire aren't "ok"), but observations IS "ok"
        # so D16 rule 2 ("no source is ok") does not fire -> partial, not
        # unavailable.
        assert snapshot.status.overall == "partial"

        # Per-station availability still reflects each station's own data,
        # independent of the document-level roll-up.
        by_code = {s.station.code: s for s in snapshot.stations}
        assert by_code["2009"].availability.bafu_forecast is True
        assert by_code["2016"].availability.bafu_forecast is False


class TestNonFiniteValuesNeverReachTheJson:
    """AC5 — every numeric leaf is a JSON number or `null`, never `NaN` or
    `Infinity`.

    The pre-existing AC5 test only re-reads the committed fixture, which by
    construction holds no non-finite value, so it could not catch this.
    Postgres `double precision` accepts NaN and Infinity, and so does a
    parquet float column, meaning both real sources can deliver one. Before
    the fix, a single non-finite member poisoned every summary at that
    valid_time and `json.dumps` wrote bare `NaN`/`Infinity` tokens — invalid
    per RFC 8259 and unparseable by a strict consumer."""

    def test_non_finite_ensemble_members_are_dropped_not_propagated(self) -> None:
        from sapphire_flow.services.forecast_lab.snapshot import _quantile_summary

        summary = _quantile_summary(np.array([10.0, float("nan"), 20.0, float("inf")]))
        assert summary == (10.0, 12.5, 15.0, 17.5, 20.0)
        for leaf in summary:
            assert leaf is None or np.isfinite(leaf)

    def test_all_non_finite_members_summarise_as_all_null(self) -> None:
        from sapphire_flow.services.forecast_lab.snapshot import _quantile_summary

        assert _quantile_summary(np.array([float("nan"), float("inf")])) == (
            None,
            None,
            None,
            None,
            None,
        )

    def test_sanitised_summary_serialises_to_strictly_parseable_json(self) -> None:
        # The exact CLI path (model_dump(mode="json") -> json.dumps) over
        # values that came through _quantile_summary, which is the only
        # producer of these numerics.
        from sapphire_flow.services.forecast_lab.snapshot import _quantile_summary

        minimum, p25, median, p75, maximum = _quantile_summary(
            np.array([10.0, float("nan"), 20.0, float("inf")])
        )
        envelope = QuantileEnvelopeSchema(
            valid_time=ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)),
            minimum=minimum,
            p25=p25,
            median=median,
            p75=p75,
            maximum=maximum,
        )
        rendered = json.dumps(envelope.model_dump(mode="json"), allow_nan=False)

        def _reject(token: str) -> float:
            raise AssertionError(f"non-finite token {token!r} reached the JSON")

        json.loads(rendered, parse_constant=_reject)

    def test_cli_write_guard_raises_rather_than_emitting_invalid_json(
        self, tmp_path: Path
    ) -> None:
        """Last-line guard, exercised through the real writer rather than
        asserted on its source text: if a non-finite ever slips past the
        source sanitisers, `_write_atomically` must fail loudly and leave no
        file, not write one the consumer cannot parse. `jsonschema.validate`
        does NOT reject non-finite floats, so it cannot catch this."""
        from sapphire_flow.cli.export_forecast_lab import _write_atomically

        fixture = (
            Path(__file__).resolve().parents[4]
            / "tests/fixtures/forecast_lab/forecast_lab_snapshot_example.json"
        )
        payload = json.loads(fixture.read_text())
        # Poison one numeric leaf, leaving the document structurally valid so
        # the schema check upstream of the write still passes.
        poisoned = payload["stations"][0]["observations"]["points"][0]
        assert isinstance(poisoned["value"], float | int)
        poisoned["value"] = float("nan")

        out = tmp_path / "snapshot.json"
        with pytest.raises(ValueError, match="Out of range float"):
            _write_atomically(out, payload)

        assert not out.exists()
        assert list(tmp_path.iterdir()) == []
