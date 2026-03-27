from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from sapphire_flow.db.metadata import model_artifacts, models, skill_diagrams, stations
from sapphire_flow.store.skill_store import PgSkillStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    FlowRegime,
    ForcingType,
    SkillFreshness,
    SkillSource,
)
from sapphire_flow.types.ids import ArtifactId, ModelId, StationId
from sapphire_flow.types.skill import SkillDiagram, SkillScore

_T0 = ensure_utc(datetime(2024, 1, 1, tzinfo=UTC))
_T1 = ensure_utc(datetime(2024, 6, 1, tzinfo=UTC))
_T2 = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_T3 = ensure_utc(datetime(2025, 6, 1, tzinfo=UTC))
_NOW = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))


def _seed_station(conn: sa.Connection) -> StationId:
    sid = StationId(uuid.uuid4())
    conn.execute(
        sa.insert(stations).values(
            id=sid,
            code=f"SK-{sid.hex[:6]}",
            name="Skill Test Station",
            location="SRID=4326;POINT(8.5 47.4)",
            station_kind="river",
            network="bafu",
            timezone="Europe/Zurich",
            measured_parameters=["discharge"],
            ownership="own",
        )
    )
    return sid


def _seed_model(conn: sa.Connection) -> ModelId:
    mid = ModelId(f"test_skill_model_{uuid.uuid4().hex[:8]}")
    conn.execute(
        sa.insert(models).values(
            id=mid,
            display_name="Skill Test Model",
            artifact_scope="station",
            description="Integration test",
        )
    )
    return mid


def _seed_artifact(
    conn: sa.Connection, station_id: StationId, model_id: ModelId
) -> ArtifactId:
    aid = ArtifactId(uuid.uuid4())
    conn.execute(
        sa.insert(model_artifacts).values(
            id=aid,
            model_id=model_id,
            station_id=station_id,
            group_id=None,
            status="active",
            artifact_path=f"artifacts/{aid}.bin",
            training_period_start=_T0,
            training_period_end=_T1,
            trained_at=_T1,
            promoted_at=_T1,
            promoted_by=None,
            superseded_at=None,
            created_at=_T1,
        )
    )
    return aid


def _make_score(
    station_id: StationId,
    model_id: ModelId,
    artifact_id: ArtifactId,
    *,
    parameter: str = "discharge",
    computation_version: int = 1,
    lead_time_hours: int = 24,
    metric: str = "crps",
    score: float = 0.5,
    skill_source: SkillSource = SkillSource.HINDCAST_NWP_ARCHIVE,
    flow_regime: FlowRegime | None = None,
    forcing_type: ForcingType | None = None,
    freshness: SkillFreshness = SkillFreshness.CURRENT,
    eval_period_start: object = None,
    eval_period_end: object = None,
) -> SkillScore:
    return SkillScore(
        id=uuid.uuid4(),
        station_id=station_id,
        model_id=model_id,
        parameter=parameter,
        model_artifact_id=artifact_id,
        skill_source=skill_source,
        forcing_type=forcing_type,
        computation_version=computation_version,
        computed_at=_NOW,
        lead_time_hours=lead_time_hours,
        season=None,
        flow_regime=flow_regime,
        flow_regime_config_id=None,
        metric=metric,
        score=score,
        sample_size=500,
        freshness=freshness,
        eval_period_start=eval_period_start or _T0,  # type: ignore[arg-type]
        eval_period_end=eval_period_end or _T1,  # type: ignore[arg-type]
        created_at=_NOW,
    )


def _make_diagram(
    station_id: StationId,
    model_id: ModelId,
    artifact_id: ArtifactId,
    *,
    parameter: str = "discharge",
    computation_version: int = 1,
    lead_time_hours: int = 24,
    diagram_type: str = "reliability",
    skill_source: SkillSource = SkillSource.HINDCAST_NWP_ARCHIVE,
) -> SkillDiagram:
    return SkillDiagram(
        id=uuid.uuid4(),
        station_id=station_id,
        model_id=model_id,
        parameter=parameter,
        model_artifact_id=artifact_id,
        skill_source=skill_source,
        computation_version=computation_version,
        lead_time_hours=lead_time_hours,
        season=None,
        flow_regime=None,
        flow_regime_config_id=None,
        diagram_type=diagram_type,  # type: ignore[arg-type]
        threshold_level=None,
        data={"bins": [0.1, 0.5, 0.9], "values": [0.08, 0.48, 0.91]},
        eval_period_start=_T0,
        eval_period_end=_T1,
        created_at=_NOW,
    )


class TestPgSkillStore:
    def test_store_and_fetch_scores(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgSkillStore(db_connection)

        s = _make_score(sid, mid, aid, metric="crps", score=0.42)
        store.store_skill_scores([s])

        results = store.fetch_latest_scores(sid, mid)
        assert len(results) == 1
        r = results[0]
        assert r.id == s.id
        assert r.station_id == sid
        assert r.model_id == mid
        assert r.metric == "crps"
        assert r.score == 0.42
        assert r.freshness == SkillFreshness.CURRENT
        assert r.skill_source == SkillSource.HINDCAST_NWP_ARCHIVE

    def test_fetch_latest_scores_returns_max_version(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgSkillStore(db_connection)

        # v1: two metrics at version 1
        v1_crps = _make_score(
            sid, mid, aid, computation_version=1, metric="crps", score=0.6
        )
        v1_bias = _make_score(
            sid, mid, aid, computation_version=1, metric="bias", score=0.1
        )
        # v2: version 2 scores — different natural keys
        v2_crps = _make_score(
            sid, mid, aid, computation_version=2, metric="nse", score=0.8
        )
        store.store_skill_scores([v1_crps, v1_bias, v2_crps])

        results = store.fetch_latest_scores(sid, mid)
        assert len(results) == 1
        assert results[0].computation_version == 2
        assert results[0].metric == "nse"

    def test_fetch_latest_scores_with_source_filter(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgSkillStore(db_connection)

        hindcast = _make_score(
            sid,
            mid,
            aid,
            skill_source=SkillSource.HINDCAST_NWP_ARCHIVE,
            metric="crps",
        )
        operational = _make_score(
            sid,
            mid,
            aid,
            skill_source=SkillSource.OPERATIONAL,
            metric="bias",
        )
        store.store_skill_scores([hindcast, operational])

        hindcast_results = store.fetch_latest_scores(
            sid, mid, skill_source=SkillSource.HINDCAST_NWP_ARCHIVE
        )
        assert len(hindcast_results) == 1
        assert hindcast_results[0].skill_source == SkillSource.HINDCAST_NWP_ARCHIVE
        assert hindcast_results[0].metric == "crps"

        operational_results = store.fetch_latest_scores(
            sid, mid, skill_source=SkillSource.OPERATIONAL
        )
        assert len(operational_results) == 1
        assert operational_results[0].skill_source == SkillSource.OPERATIONAL

    def test_store_and_fetch_diagrams(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgSkillStore(db_connection)

        d = _make_diagram(sid, mid, aid, diagram_type="reliability")
        store.store_skill_diagrams([d])

        results = store.fetch_latest_diagrams(sid, mid)
        assert len(results) == 1
        r = results[0]
        assert r.id == d.id
        assert r.diagram_type == "reliability"
        assert r.data == {"bins": [0.1, 0.5, 0.9], "values": [0.08, 0.48, 0.91]}

    def test_fetch_latest_diagrams_with_type_filter(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgSkillStore(db_connection)

        rel = _make_diagram(sid, mid, aid, diagram_type="reliability")
        roc = _make_diagram(sid, mid, aid, diagram_type="roc")
        store.store_skill_diagrams([rel, roc])

        results = store.fetch_latest_diagrams(sid, mid, diagram_type="reliability")
        assert len(results) == 1
        assert results[0].diagram_type == "reliability"

    def test_fetch_scores_by_regime(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgSkillStore(db_connection)

        low = _make_score(
            sid, mid, aid, flow_regime=FlowRegime.LOW, metric="crps", score=0.3
        )
        high = _make_score(
            sid, mid, aid, flow_regime=FlowRegime.HIGH, metric="crps", score=0.5
        )
        no_regime = _make_score(sid, mid, aid, flow_regime=None, metric="bias")
        store.store_skill_scores([low, high, no_regime])

        low_results = store.fetch_scores_by_regime(sid, mid, FlowRegime.LOW)
        assert len(low_results) == 1
        assert low_results[0].flow_regime == FlowRegime.LOW
        assert low_results[0].score == 0.3

        high_results = store.fetch_scores_by_regime(sid, mid, FlowRegime.HIGH)
        assert len(high_results) == 1
        assert high_results[0].flow_regime == FlowRegime.HIGH

    def test_mark_stale(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgSkillStore(db_connection)

        # overlapping: eval [T0, T2), mark window [T1, T3)
        overlapping = _make_score(
            sid,
            mid,
            aid,
            metric="crps",
            freshness=SkillFreshness.CURRENT,
            eval_period_start=_T0,
            eval_period_end=_T2,
        )
        # non-overlapping: eval [T0, T1), mark window [T1, T3) — touches, no overlap
        non_overlapping = _make_score(
            sid,
            mid,
            aid,
            metric="bias",
            freshness=SkillFreshness.CURRENT,
            eval_period_start=_T0,
            eval_period_end=_T1,
        )
        store.store_skill_scores([overlapping, non_overlapping])

        count = store.mark_stale(sid, _T1, _T3)

        assert count == 1

        results = store.fetch_latest_scores(sid, mid)
        by_id = {r.id: r for r in results}
        assert by_id[overlapping.id].freshness == SkillFreshness.STALE
        assert by_id[non_overlapping.id].freshness == SkillFreshness.CURRENT

    def test_mark_stale_no_overlap(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgSkillStore(db_connection)

        # eval [T0, T1), mark window [T2, T3) — entirely before
        score = _make_score(
            sid,
            mid,
            aid,
            metric="crps",
            freshness=SkillFreshness.CURRENT,
            eval_period_start=_T0,
            eval_period_end=_T1,
        )
        store.store_skill_scores([score])

        count = store.mark_stale(sid, _T2, _T3)

        assert count == 0
        results = store.fetch_latest_scores(sid, mid)
        assert results[0].freshness == SkillFreshness.CURRENT

    def test_fetch_filters_by_parameter(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgSkillStore(db_connection)

        discharge = _make_score(sid, mid, aid, parameter="discharge", metric="crps_q")
        water_level = _make_score(
            sid, mid, aid, parameter="water_level", metric="crps_q"
        )
        store.store_skill_scores([discharge, water_level])

        discharge_results = store.fetch_latest_scores(sid, mid, parameter="discharge")
        assert len(discharge_results) == 1
        assert discharge_results[0].parameter == "discharge"

        all_results = store.fetch_latest_scores(sid, mid, parameter=None)
        assert len(all_results) == 2

    def test_fetch_diagrams_by_parameter(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgSkillStore(db_connection)

        discharge = _make_diagram(
            sid, mid, aid, parameter="discharge", diagram_type="roc_param"
        )
        water_level = _make_diagram(
            sid, mid, aid, parameter="water_level", diagram_type="roc_param"
        )
        store.store_skill_diagrams([discharge, water_level])

        discharge_results = store.fetch_latest_diagrams(sid, mid, parameter="discharge")
        assert len(discharge_results) == 1
        assert discharge_results[0].parameter == "discharge"

        all_results = store.fetch_latest_diagrams(sid, mid, parameter=None)
        assert len(all_results) == 2

    def test_fetch_scores_by_regime_with_parameter(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgSkillStore(db_connection)

        discharge = _make_score(
            sid,
            mid,
            aid,
            flow_regime=FlowRegime.LOW,
            parameter="discharge",
            metric="nse_regime",
        )
        water_level = _make_score(
            sid,
            mid,
            aid,
            flow_regime=FlowRegime.LOW,
            parameter="water_level",
            metric="nse_regime",
        )
        store.store_skill_scores([discharge, water_level])

        discharge_results = store.fetch_scores_by_regime(
            sid, mid, FlowRegime.LOW, parameter="discharge"
        )
        assert len(discharge_results) == 1
        assert discharge_results[0].parameter == "discharge"

        all_results = store.fetch_scores_by_regime(
            sid, mid, FlowRegime.LOW, parameter=None
        )
        assert len(all_results) == 2

    def test_mark_stale_filters_by_parameter(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgSkillStore(db_connection)

        discharge = _make_score(
            sid,
            mid,
            aid,
            parameter="discharge",
            metric="crps_ms",
            freshness=SkillFreshness.CURRENT,
            eval_period_start=_T0,
            eval_period_end=_T2,
        )
        water_level = _make_score(
            sid,
            mid,
            aid,
            parameter="water_level",
            metric="crps_ms",
            freshness=SkillFreshness.CURRENT,
            eval_period_start=_T0,
            eval_period_end=_T2,
        )
        store.store_skill_scores([discharge, water_level])

        count = store.mark_stale(sid, _T1, _T3, parameter="discharge")
        assert count == 1

        all_scores = store.fetch_latest_scores(sid, mid, parameter=None)
        by_param = {r.parameter: r for r in all_scores}
        assert by_param["discharge"].freshness == SkillFreshness.STALE
        assert by_param["water_level"].freshness == SkillFreshness.CURRENT

        remaining_count = store.mark_stale(sid, _T1, _T3, parameter=None)
        assert remaining_count == 1

        all_scores = store.fetch_latest_scores(sid, mid, parameter=None)
        assert all(r.freshness == SkillFreshness.STALE for r in all_scores)

    def test_store_diagrams_idempotent(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgSkillStore(db_connection)

        diagrams = [
            _make_diagram(sid, mid, aid, diagram_type="rank_histogram"),
            _make_diagram(sid, mid, aid, diagram_type="roc"),
        ]
        store.store_skill_diagrams(diagrams)
        store.store_skill_diagrams(diagrams)

        count = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(skill_diagrams)
            .where(
                skill_diagrams.c.station_id == sid,
                skill_diagrams.c.model_id == mid,
            )
        ).scalar()
        assert count == 2
