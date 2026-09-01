from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from sapphire_flow.db.metadata import model_artifacts, models, skill_diagrams, stations
from sapphire_flow.store.skill_store import PgSkillStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    EnsembleRepresentation,
    FlowRegime,
    ForcingType,
    SkillFreshness,
    SkillSource,
)
from sapphire_flow.types.ids import ArtifactId, ModelId, StationId
from sapphire_flow.types.skill import SkillDiagram, SkillScore
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID

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
            tenant_id=DEFAULT_TENANT_ID,
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
            sha256_hash="",
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

    def test_recompute_after_mark_stale_supersedes_the_corrupted_score(
        self, db_connection: sa.Connection
    ) -> None:
        """Plan 228 review fixer round (blocker): `store_skill_scores` inserts
        with `ON CONFLICT DO NOTHING` on a natural key that includes
        `computation_version`. `mark_stale` only flips `freshness` — it never
        frees the key. Recomputing a corrected score at the SAME
        `computation_version` as the now-stale corrupted row therefore
        collides and is silently dropped; `fetch_latest_scores` keeps
        returning the corrupted value forever. This drives the real
        `compute_skill_for_station` (not a hand-picked literal) end to end:
        store a corrupted v1 row for a stratum, mark it stale, recompute the
        SAME stratum with the real fixed service, store the result, and
        confirm the corrected score — not the stale one — is what
        `fetch_latest_scores` returns.
        """
        import uuid as uuid_mod
        from datetime import timedelta

        import polars as pl

        from sapphire_flow.services.skill.service import (
            _COMPUTATION_VERSION,
            compute_skill_for_station,
        )
        from sapphire_flow.types.ensemble import ForecastEnsemble
        from sapphire_flow.types.forecast import HindcastForecast
        from sapphire_flow.types.ids import HindcastForecastId
        from tests.conftest import make_observation

        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgSkillStore(db_connection)

        # A pre-Plan-228 corrupted v1 score for exactly this stratum
        # (station/artifact/parameter/source/forcing/lead/season/regime/metric).
        corrupt = _make_score(
            sid,
            mid,
            aid,
            computation_version=1,
            lead_time_hours=24,
            metric="mae",
            score=40.0,  # the pre-fix instantaneous-join error this plan measured
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
        )
        store.store_skill_scores([corrupt])

        marked = store.mark_stale(sid, _T0, _T1)
        assert marked == 1

        # Recompute the SAME stratum with the real, fixed service: two daily
        # hindcasts whose forecast exactly equals the (resampled) daily-mean
        # observation, giving a near-zero MAE — nothing like the stale 40.0.
        day = timedelta(days=1)
        issue1 = ensure_utc(datetime(2025, 3, 1, tzinfo=UTC))
        issue2 = ensure_utc(datetime(2025, 3, 2, tzinfo=UTC))

        def _daily_hindcast(issue: object) -> HindcastForecast:
            vt = ensure_utc(issue + day)  # type: ignore[operator]
            df = pl.DataFrame(
                {
                    "valid_time": [vt, vt, vt],
                    "member_id": [0, 1, 2],
                    "value": [42.0, 42.0, 42.0],
                }
            ).with_columns(
                pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
                pl.col("member_id").cast(pl.Int32),
            )
            ensemble = ForecastEnsemble.from_members(
                station_id=sid,
                issued_at=issue,  # type: ignore[arg-type]
                parameter="discharge",
                units="m³/s",
                time_step=day,
                values=df,
            )
            return HindcastForecast(
                id=HindcastForecastId(uuid_mod.uuid4()),
                station_id=sid,
                model_id=mid,
                model_artifact_id=aid,
                hindcast_step=issue,  # type: ignore[arg-type]
                forcing_type=ForcingType.REANALYSIS,
                representation=EnsembleRepresentation.MEMBERS,
                hindcast_run_id=uuid_mod.uuid4(),
                ensemble=ensemble,
                created_at=_NOW,
            )

        hindcasts = [_daily_hindcast(issue1), _daily_hindcast(issue2)]
        observations = [
            make_observation(
                station_id=sid, timestamp=ensure_utc(issue1 + day), value=42.0
            ),
            make_observation(
                station_id=sid, timestamp=ensure_utc(issue2 + day), value=42.0
            ),
        ]

        scores, _ = compute_skill_for_station(
            station_id=sid,
            model_id=mid,
            artifact_id=aid,
            hindcasts=hindcasts,
            observations=observations,
            thresholds=[],
            flow_regime_config=None,
            seasons=[],
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=lambda: _NOW,
            uuid_factory=uuid_mod.uuid4,
            parameter="discharge",
        )
        corrected_mae = [
            s
            for s in scores
            if s.metric == "mae" and s.lead_time_hours == 24 and s.season is None
        ]
        assert corrected_mae
        assert all(s.computation_version == _COMPUTATION_VERSION for s in corrected_mae)

        store.store_skill_scores(scores)

        results = store.fetch_latest_scores(
            sid,
            mid,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            parameter="discharge",
        )
        mae_result = next(
            r for r in results if r.metric == "mae" and r.lead_time_hours == 24
        )
        assert mae_result.computation_version == _COMPUTATION_VERSION
        assert mae_result.score < 1.0, (
            "expected the recomputed near-zero MAE, got the stale corrupted "
            f"score {mae_result.score} — recompute was silently dropped by "
            "the natural-key conflict"
        )

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
            sid, mid, aid, parameter="discharge", diagram_type="roc"
        )
        water_level = _make_diagram(
            sid, mid, aid, parameter="water_level", diagram_type="roc"
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

    def test_fetch_skill_scores_happy_path(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgSkillStore(db_connection)

        s1 = _make_score(sid, mid, aid, metric="crps", lead_time_hours=24)
        s2 = _make_score(sid, mid, aid, metric="bias", lead_time_hours=48)
        store.store_skill_scores([s1, s2])

        results = store.fetch_skill_scores(mid, aid)
        assert len(results) == 2
        ids = {r.id for r in results}
        assert ids == {s1.id, s2.id}
        assert all(r.model_id == mid for r in results)
        assert all(r.model_artifact_id == aid for r in results)

    def test_fetch_skill_scores_with_parameter_filter(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgSkillStore(db_connection)

        discharge = _make_score(sid, mid, aid, parameter="discharge", metric="crps")
        water_level = _make_score(sid, mid, aid, parameter="water_level", metric="crps")
        store.store_skill_scores([discharge, water_level])

        results = store.fetch_skill_scores(mid, aid, parameter="discharge")
        assert len(results) == 1
        assert results[0].parameter == "discharge"
        assert results[0].id == discharge.id

    def test_fetch_skill_scores_empty_when_no_match(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgSkillStore(db_connection)

        other_aid = ArtifactId(uuid.uuid4())
        score = _make_score(sid, mid, aid, metric="crps")
        store.store_skill_scores([score])

        # Different artifact_id — no match
        results = store.fetch_skill_scores(mid, other_aid)
        assert results == ()

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
