from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sapphire_flow.cli.register_deployments import (
    BACKUP_POOL,
    INGEST_POOL,
    WORK_POOL,
    DeploymentSpec,
    _build_specs,
    _register_one,
    register_all,
)


def _cron_minute_set(cron: str | None) -> set[int]:
    """Minimal 5-field cron minute-field parser — only the forms this repo's
    schedules actually use: a literal minute (``37``), a step (``*/5``), or a
    comma-separated list of literals (Plan 176 D1, e.g.
    ``1,4,7,11,...``). Not a general cron parser."""
    assert cron is not None
    minute_field = cron.split()[0]
    if minute_field == "*":
        return set(range(60))
    if minute_field.startswith("*/"):
        step = int(minute_field[2:])
        return set(range(0, 60, step))
    return {int(m) for m in minute_field.split(",")}


DEPLOYMENT_NAMES = {
    "ingest-observations",
    "forecast-cycle",
    "backup-database",
    "train-models",
    "run-hindcast",
    "compute-skills",
    "compute-combined-skills",
    "onboard-stations",
    "onboard-model",
    "ingest-weather-history",
    "ingest-recap-reanalysis",
    "collect-bafu-forecasts",
    "collect-bafu-observations",
    # Plan 157 T3: the external-artifact import path — manually triggered,
    # no cron, on the `default` pool like every other non-ingest deployment.
    "import-model-artifact",
}

# ---------------------------------------------------------------------------
# _build_specs — pure function (env-var driven)
# ---------------------------------------------------------------------------


class TestBuildSpecs:
    def test_default_schedules(self) -> None:
        specs = _build_specs()
        by_name = {s.deployment_name: s for s in specs}

        assert by_name["ingest-observations"].cron == "*/5 * * * *"
        assert by_name["forecast-cycle"].cron == "0 */6 * * *"
        assert by_name["backup-database"].cron == "0 2 * * *"

    def test_on_demand_flows_have_no_cron(self) -> None:
        specs = _build_specs()
        on_demand = {
            "train-models",
            "run-hindcast",
            "compute-skills",
            "compute-combined-skills",
            "onboard-stations",
            "onboard-model",
        }
        for spec in specs:
            if spec.deployment_name in on_demand:
                assert spec.cron is None, f"{spec.deployment_name} should have no cron"

    def test_concurrency_limits(self) -> None:
        specs = _build_specs()
        by_name = {s.deployment_name: s for s in specs}

        assert by_name["forecast-cycle"].concurrency_limit == 1
        assert by_name["train-models"].concurrency_limit == 1
        assert by_name["onboard-model"].concurrency_limit == 1
        assert by_name["ingest-weather-history"].concurrency_limit == 1
        assert by_name["ingest-recap-reanalysis"].concurrency_limit == 1
        # Others should have no concurrency limit
        assert by_name["ingest-observations"].concurrency_limit is None
        assert by_name["backup-database"].concurrency_limit is None
        assert by_name["run-hindcast"].concurrency_limit is None

    def test_ingest_observations_routes_to_ingest_pool(self) -> None:
        specs = _build_specs()
        by_name = {s.deployment_name: s for s in specs}

        assert by_name["ingest-observations"].work_pool_name == "ingest"
        for name, spec in by_name.items():
            if name in (
                "ingest-observations",
                "backup-database",
                "collect-bafu-observations",
            ):
                continue
            assert spec.work_pool_name == "default"
            assert spec.work_pool_name == WORK_POOL

    def test_backup_database_routes_to_the_dedicated_backup_pool(self) -> None:
        """Plan 162 D2: the only worker serving this pool holds the
        read-everything `sapphire_backup` credential."""
        by_name = {s.deployment_name: s for s in _build_specs()}
        assert by_name["backup-database"].work_pool_name == "backup"
        assert by_name["backup-database"].work_pool_name == BACKUP_POOL

    def test_env_var_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCHEDULE_INGEST_OBSERVATIONS", "*/10 * * * *")
        monkeypatch.setenv("SCHEDULE_FORECAST_CYCLE", "0 */3 * * *")
        monkeypatch.setenv("SCHEDULE_BACKUP_DATABASE", "0 4 * * *")
        monkeypatch.setenv("SCHEDULE_INGEST_WEATHER_HISTORY", "30 5 * * *")

        specs = _build_specs()
        by_name = {s.deployment_name: s for s in specs}

        assert by_name["ingest-observations"].cron == "*/10 * * * *"
        assert by_name["forecast-cycle"].cron == "0 */3 * * *"
        assert by_name["backup-database"].cron == "0 4 * * *"
        assert by_name["ingest-weather-history"].cron == "30 5 * * *"

    def test_returns_all_specs(self) -> None:
        # Plan 071 adds weather-history ingest; Plan 111 adds the BAFU
        # forecast collector; Plan 136 adds the BAFU observation collector;
        # Plan 146 adds the recap-reanalysis snow ingest; Plan 157 adds the
        # external-artifact import path.
        specs = _build_specs()
        assert len(specs) == 14
        assert {s.deployment_name for s in specs} == DEPLOYMENT_NAMES

    def test_import_model_artifact_is_manual_and_on_the_default_pool(self) -> None:
        """Plan 157 T3. Manually triggered (no cron) and serialized. It sits on
        `default`: a dedicated forecast-cycle pool was built for it and
        REVERTED — with the same image on both sides it bought nothing while
        adding a third pool and a mixed-version upgrade window. A shim-backed
        model will need routing to whichever worker carries that distribution
        (see the split-out shim plan)."""
        by_name = {s.deployment_name: s for s in _build_specs()}
        spec = by_name["import-model-artifact"]
        assert spec.cron is None
        assert spec.concurrency_limit == 1
        assert spec.work_pool_name == WORK_POOL

    def test_bafu_collector_hourly_and_serialized(self) -> None:
        by_name = {s.deployment_name: s for s in _build_specs()}
        bafu = by_name["collect-bafu-forecasts"]
        assert bafu.cron == "0 * * * *"  # hourly default
        assert bafu.concurrency_limit == 1  # never overlap runs
        assert bafu.work_pool_name == WORK_POOL  # default pool, not ingest

    def test_bafu_cron_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCHEDULE_COLLECT_BAFU_FORECASTS", "*/30 * * * *")
        by_name = {s.deployment_name: s for s in _build_specs()}
        assert by_name["collect-bafu-forecasts"].cron == "*/30 * * * *"

    def test_bafu_observation_collector_serialized_and_on_ingest_pool(self) -> None:
        """Plan 176 D5/T1: routed onto the `ingest` pool (nearly-idle,
        dedicated event loop) rather than the shared `default` pool, so a
        CPU-pegging forecast-cycle run cannot starve the collector's poll
        loop the way Plan 098 measured on `default`."""
        by_name = {s.deployment_name: s for s in _build_specs()}
        bafu_obs = by_name["collect-bafu-observations"]
        assert bafu_obs.concurrency_limit == 1  # never overlap runs
        assert bafu_obs.work_pool_name == INGEST_POOL  # Plan 176 D5, not default

    def test_bafu_observation_cron_env_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCHEDULE_COLLECT_BAFU_OBSERVATIONS", "*/15 * * * *")
        by_name = {s.deployment_name: s for s in _build_specs()}
        assert by_name["collect-bafu-observations"].cron == "*/15 * * * *"

    def test_bafu_observation_default_minute_is_disjoint_from_the_other_schedules(
        self,
    ) -> None:
        """Plan 175 D4 lock: a future "tidy the cron back onto a round
        number" edit must not silently re-collide the collector with
        ingest-observations' `*/5` tick or the BAFU-forecast collector's
        `0 * * * *` tick."""
        by_name = {s.deployment_name: s for s in _build_specs()}
        bafu_obs_minutes = _cron_minute_set(by_name["collect-bafu-observations"].cron)
        ingest_minutes = _cron_minute_set(by_name["ingest-observations"].cron)
        bafu_forecast_minutes = _cron_minute_set(by_name["collect-bafu-forecasts"].cron)

        assert all(m % 5 != 0 for m in bafu_obs_minutes)
        assert bafu_obs_minutes.isdisjoint(ingest_minutes)
        assert bafu_obs_minutes.isdisjoint(bafu_forecast_minutes)


def _cyclic_gaps(minutes: set[int]) -> list[int]:
    """Consecutive gaps between sorted minutes, INCLUDING the wrap from the
    largest minute back to the smallest across the `:59 -> :01` hour
    boundary — a plain `diff` would miss exactly that wrap gap."""
    ordered = sorted(minutes)
    # strict=False (deliberate, not the default omission): a single-minute
    # cron has an empty `ordered[1:]` — zip must degrade to [] there, not
    # raise, so the wrap-only gap below still produces a clean (large)
    # assertion failure rather than a crash.
    gaps = [b - a for a, b in zip(ordered, ordered[1:], strict=False)]
    gaps.append((ordered[0] + 60) - ordered[-1])
    return gaps


class TestBafuObservationCadenceProperties:
    """Plan 176 D1, tightened by Plan 189 T2 — the schedule is locked as
    PROPERTIES, not a literal cron string: max cyclic gap <=3 min (a 2.33x
    margin on Plan 176 T7's measured 7.0 min minimum publish gap — the
    original 4 min bound was only a 1.75x margin against that same
    measurement, below D1's own "tighten if under ~8 min" pre-commitment),
    min gap >=2 min (RELAXED from D1's original >=3 — `max<=3` AND `min>=3`
    together are arithmetically incompatible with "no minute divisible by
    5", see docs/plans/189-audit-window-edge-and-poll-bound.md § T2), and
    every minute non-divisible by 5 (never shares a minute with
    ingest-observations' `*/5` tick)."""

    def test_max_cyclic_gap_is_at_most_three_minutes(self) -> None:
        by_name = {s.deployment_name: s for s in _build_specs()}
        minutes = _cron_minute_set(by_name["collect-bafu-observations"].cron)
        assert max(_cyclic_gaps(minutes)) <= 3

    def test_min_gap_is_at_least_two_minutes(self) -> None:
        by_name = {s.deployment_name: s for s in _build_specs()}
        minutes = _cron_minute_set(by_name["collect-bafu-observations"].cron)
        assert min(_cyclic_gaps(minutes)) >= 2

    def test_cron_is_accepted_by_prefects_cron_schedule(self) -> None:
        """A property test over the minute FIELD ALONE would miss a
        malformed full cron string (bad field count, invalid day/month
        syntax) — parse the whole thing through Prefect's own validator,
        the way the reviewer checked the proposed list (Plan 189 T2)."""
        from prefect.client.schemas.schedules import CronSchedule

        by_name = {s.deployment_name: s for s in _build_specs()}
        cron = by_name["collect-bafu-observations"].cron
        assert cron is not None
        CronSchedule(cron=cron)  # raises on an invalid expression

    def test_runs_every_hour_of_every_day(self) -> None:
        # The gap/divisibility assertions above read ONLY the minute field, so
        # a cron restricted to (say) hour 00 would satisfy every one of them
        # while silently dropping 23 hours of slots a day. Pin the rest of the
        # expression too.
        by_name = {s.deployment_name: s for s in _build_specs()}
        cron = by_name["collect-bafu-observations"].cron
        assert cron is not None
        fields = cron.split()
        assert len(fields) == 5, f"expected a 5-field cron, got {cron!r}"
        assert fields[1:] == ["*", "*", "*", "*"], (
            f"collector cron must run every hour of every day, got {cron!r} — "
            "a restricted hour/day field loses whole days of 10-minute slots "
            "while still passing the minute-only gap assertions"
        )

    def test_every_scheduled_minute_is_non_divisible_by_five(self) -> None:
        by_name = {s.deployment_name: s for s in _build_specs()}
        minutes = _cron_minute_set(by_name["collect-bafu-observations"].cron)
        assert all(m % 5 != 0 for m in minutes)
        assert len(minutes) >= 15  # roughly 2.5x the grid's 6/hour

    def test_ingest_weather_history_daily_deployment(self) -> None:
        """Plan-071 rolling-ingest flow is registered as a daily deployment."""
        specs = _build_specs()
        by_name = {s.deployment_name: s for s in specs}

        spec = by_name["ingest-weather-history"]
        assert spec.cron == "0 6 * * *"
        assert spec.flow_module == "sapphire_flow.flows.ingest_weather_history"
        assert spec.flow_attr == "ingest_weather_history_flow"

    def test_ingest_recap_reanalysis_daily_deployment(self) -> None:
        """Plan 146 D2: the recap-reanalysis snow ingest is a daily, serialized
        deployment — mirrors ingest-weather-history's shape."""
        specs = _build_specs()
        by_name = {s.deployment_name: s for s in specs}

        spec = by_name["ingest-recap-reanalysis"]
        assert spec.cron == "0 5 * * *"
        assert spec.concurrency_limit == 1
        assert spec.flow_module == "sapphire_flow.flows.ingest_recap_reanalysis"
        assert spec.flow_attr == "ingest_recap_reanalysis_flow"
        assert spec.work_pool_name == WORK_POOL

    def test_ingest_recap_reanalysis_cron_env_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCHEDULE_INGEST_SNOW_REANALYSIS", "15 4 * * *")
        by_name = {s.deployment_name: s for s in _build_specs()}
        assert by_name["ingest-recap-reanalysis"].cron == "15 4 * * *"

    def test_all_deployment_names_unique(self) -> None:
        specs = _build_specs()
        names = [s.deployment_name for s in specs]
        assert len(names) == len(set(names))

    def test_all_flow_modules_are_valid_python_paths(self) -> None:
        specs = _build_specs()
        for spec in specs:
            parts = spec.flow_module.split(".")
            assert all(p.isidentifier() for p in parts), (
                f"{spec.flow_module} is not a valid Python module path"
            )

    def test_all_flow_modules_importable_and_attrs_exist(self) -> None:
        """Catch stale module/attr references — the #1 risk mocks hide."""
        import importlib

        specs = _build_specs()
        for spec in specs:
            module = importlib.import_module(spec.flow_module)
            flow_fn = getattr(module, spec.flow_attr, None)
            assert flow_fn is not None, (
                f"{spec.flow_module}.{spec.flow_attr} does not exist"
            )
            assert hasattr(flow_fn, "fn"), (
                f"{spec.flow_module}.{spec.flow_attr} is not a Prefect flow"
            )


# ---------------------------------------------------------------------------
# _register_one — needs mocked import + adeploy
# ---------------------------------------------------------------------------


class TestRegisterOne:
    @pytest.mark.asyncio
    async def test_registers_scheduled_flow(self) -> None:
        spec = DeploymentSpec(
            flow_module="sapphire_flow.flows.backup",
            flow_attr="backup_database_flow",
            deployment_name="backup-database",
            cron="0 2 * * *",
            work_pool_name=WORK_POOL,
        )

        mock_sourced_flow = MagicMock()
        mock_sourced_flow.adeploy = AsyncMock(return_value="deploy-id-123")
        mock_flow = MagicMock()
        mock_flow.afrom_source = AsyncMock(return_value=mock_sourced_flow)
        mock_module = MagicMock()
        mock_module.backup_database_flow = mock_flow

        with patch("importlib.import_module", return_value=mock_module) as mock_import:
            await _register_one(spec)

        mock_import.assert_called_once_with("sapphire_flow.flows.backup")
        mock_flow.afrom_source.assert_awaited_once()
        from_source_kwargs = mock_flow.afrom_source.call_args[1]
        assert from_source_kwargs["source"] == "/app"
        assert (
            from_source_kwargs["entrypoint"]
            == "src/sapphire_flow/flows/backup.py:backup_database_flow"
        )
        mock_sourced_flow.adeploy.assert_awaited_once()
        call_kwargs = mock_sourced_flow.adeploy.call_args[1]
        assert call_kwargs["name"] == "backup-database"
        assert call_kwargs["work_pool_name"] == spec.work_pool_name
        assert call_kwargs["cron"] == "0 2 * * *"
        assert call_kwargs["build"] is False
        assert call_kwargs["push"] is False
        assert "concurrency_limit" not in call_kwargs

    @pytest.mark.asyncio
    async def test_registers_on_demand_flow_with_concurrency(self) -> None:
        spec = DeploymentSpec(
            flow_module="sapphire_flow.flows.train_models",
            flow_attr="train_models_flow",
            deployment_name="train-models",
            concurrency_limit=1,
        )

        mock_sourced_flow = MagicMock()
        mock_sourced_flow.adeploy = AsyncMock(return_value="deploy-id-456")
        mock_flow = MagicMock()
        mock_flow.afrom_source = AsyncMock(return_value=mock_sourced_flow)
        mock_module = MagicMock()
        mock_module.train_models_flow = mock_flow

        with patch("importlib.import_module", return_value=mock_module):
            await _register_one(spec)

        call_kwargs = mock_sourced_flow.adeploy.call_args[1]
        assert "cron" not in call_kwargs
        assert call_kwargs["concurrency_limit"] == 1

    @pytest.mark.asyncio
    async def test_register_one_uses_spec_work_pool(self) -> None:
        spec = DeploymentSpec(
            flow_module="sapphire_flow.flows.ingest_observations",
            flow_attr="ingest_observations_flow",
            deployment_name="ingest-observations",
            cron="*/5 * * * *",
            work_pool_name="ingest",
        )

        mock_sourced_flow = MagicMock()
        mock_sourced_flow.adeploy = AsyncMock(return_value="deploy-id-789")
        mock_flow = MagicMock()
        mock_flow.afrom_source = AsyncMock(return_value=mock_sourced_flow)
        mock_module = MagicMock()
        mock_module.ingest_observations_flow = mock_flow

        with patch("importlib.import_module", return_value=mock_module):
            await _register_one(spec)

        assert mock_sourced_flow.adeploy.call_args[1]["work_pool_name"] == "ingest"

    @pytest.mark.asyncio
    async def test_import_error_propagates(self) -> None:
        spec = DeploymentSpec(
            flow_module="nonexistent.module",
            flow_attr="some_flow",
            deployment_name="bad-deploy",
        )
        with (
            patch(
                "importlib.import_module",
                side_effect=ModuleNotFoundError("nonexistent"),
            ),
            pytest.raises(ModuleNotFoundError),
        ):
            await _register_one(spec)


# ---------------------------------------------------------------------------
# register_all — needs mocked Prefect client
# ---------------------------------------------------------------------------


class TestRegisterAll:
    @pytest.mark.asyncio
    async def test_creates_work_pool_and_registers_all(self) -> None:
        mock_client = AsyncMock()
        mock_client.create_work_pool = AsyncMock()

        mock_register = AsyncMock()

        with (
            patch(
                "prefect.client.orchestration.get_client",
            ) as mock_get_client,
            patch(
                "sapphire_flow.cli.register_deployments._register_one",
                mock_register,
            ),
        ):
            mock_get_client.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)

            await register_all()

        assert mock_client.create_work_pool.await_count == 3
        assert {
            c.args[0].name for c in mock_client.create_work_pool.await_args_list
        } == {"default", "ingest", "backup"}
        assert {
            c.args[0].deployment_name for c in mock_register.await_args_list
        } == DEPLOYMENT_NAMES

    @pytest.mark.asyncio
    async def test_handles_existing_work_pool(self) -> None:
        from prefect.exceptions import ObjectAlreadyExists

        def _raise_if_default(work_pool):  # type: ignore[no-untyped-def]
            if work_pool.name == "default":
                raise ObjectAlreadyExists("pool exists")
            return None

        mock_client = AsyncMock()
        mock_client.create_work_pool = AsyncMock(side_effect=_raise_if_default)

        mock_register = AsyncMock()

        with (
            patch(
                "prefect.client.orchestration.get_client",
            ) as mock_get_client,
            patch(
                "sapphire_flow.cli.register_deployments._register_one",
                mock_register,
            ),
        ):
            mock_get_client.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)

            # Should NOT raise — the per-iteration guard catches the raising call
            # ("default" pre-exists) while "ingest" and "backup" are created.
            await register_all()

        # All three pools were attempted despite one raising.
        assert {
            c.args[0].name for c in mock_client.create_work_pool.await_args_list
        } == {"default", "ingest", "backup"}
        assert {
            c.args[0].deployment_name for c in mock_register.await_args_list
        } == DEPLOYMENT_NAMES

    @pytest.mark.asyncio
    async def test_handles_all_work_pools_existing(self) -> None:
        from prefect.exceptions import ObjectAlreadyExists

        mock_client = AsyncMock()
        mock_client.create_work_pool = AsyncMock(
            side_effect=[
                ObjectAlreadyExists("pool exists"),
                ObjectAlreadyExists("pool exists"),
                ObjectAlreadyExists("pool exists"),
            ]
        )

        mock_register = AsyncMock()

        with (
            patch(
                "prefect.client.orchestration.get_client",
            ) as mock_get_client,
            patch(
                "sapphire_flow.cli.register_deployments._register_one",
                mock_register,
            ),
        ):
            mock_get_client.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)

            # All three pools already exist — every raise is caught, all specs
            # register.
            await register_all()

        assert mock_client.create_work_pool.await_count == 3
        assert mock_register.await_count == len(DEPLOYMENT_NAMES)
