from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sapphire_flow.cli.register_deployments import (
    WORK_POOL,
    DeploymentSpec,
    _build_specs,
    _register_one,
    register_all,
)

TEN_DEPLOYMENT_NAMES = {
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
        # Others should have no concurrency limit
        assert by_name["ingest-observations"].concurrency_limit is None
        assert by_name["backup-database"].concurrency_limit is None
        assert by_name["run-hindcast"].concurrency_limit is None

    def test_ingest_observations_routes_to_ingest_pool(self) -> None:
        specs = _build_specs()
        by_name = {s.deployment_name: s for s in specs}

        assert by_name["ingest-observations"].work_pool_name == "ingest"
        for name, spec in by_name.items():
            if name == "ingest-observations":
                continue
            assert spec.work_pool_name == "default"
            assert spec.work_pool_name == WORK_POOL

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

    def test_returns_ten_specs(self) -> None:
        # Plan 071 adds the rolling weather-history ingest deployment.
        specs = _build_specs()
        assert len(specs) == 10
        assert {s.deployment_name for s in specs} == TEN_DEPLOYMENT_NAMES

    def test_ingest_weather_history_daily_deployment(self) -> None:
        """Plan-071 rolling-ingest flow is registered as a daily deployment."""
        specs = _build_specs()
        by_name = {s.deployment_name: s for s in specs}

        spec = by_name["ingest-weather-history"]
        assert spec.cron == "0 6 * * *"
        assert spec.flow_module == "sapphire_flow.flows.ingest_weather_history"
        assert spec.flow_attr == "ingest_weather_history_flow"

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

        assert mock_client.create_work_pool.await_count == 2
        assert {
            c.args[0].name for c in mock_client.create_work_pool.await_args_list
        } == {"default", "ingest"}
        assert {
            c.args[0].deployment_name for c in mock_register.await_args_list
        } == TEN_DEPLOYMENT_NAMES

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
            # ("default" pre-exists) while "ingest" is created.
            await register_all()

        # Both pools were attempted despite one raising.
        assert {
            c.args[0].name for c in mock_client.create_work_pool.await_args_list
        } == {"default", "ingest"}
        assert {
            c.args[0].deployment_name for c in mock_register.await_args_list
        } == TEN_DEPLOYMENT_NAMES

    @pytest.mark.asyncio
    async def test_handles_all_work_pools_existing(self) -> None:
        from prefect.exceptions import ObjectAlreadyExists

        mock_client = AsyncMock()
        mock_client.create_work_pool = AsyncMock(
            side_effect=[
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

            # Both pools already exist — both raises are caught, all specs register.
            await register_all()

        assert mock_client.create_work_pool.await_count == 2
        assert mock_register.await_count == 10
