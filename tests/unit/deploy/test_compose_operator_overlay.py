"""Plan 510 D6: the operator overlay carries the optional operator secret and the
`operator` service; the base compose file knows neither."""

from __future__ import annotations

from pathlib import Path

import yaml

_SECRET = "sapphire_operator_db_password"


def _root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "docker-compose.yml").is_file():
            return parent
    raise FileNotFoundError("docker-compose.yml not found above test file")


def _load(name: str) -> dict[str, object]:
    return yaml.safe_load((_root() / name).read_text())


def _operator() -> dict[str, object]:
    return _load("docker-compose.operator.yml")["services"]["operator"]  # type: ignore[index]


def _secret_names(service: dict[str, object]) -> list[str]:
    return [
        s["source"] if isinstance(s, dict) else s
        for s in service.get("secrets", [])  # type: ignore[attr-defined]
    ]


def _mounts(service: dict[str, object]) -> list[str | dict[str, object]]:
    return list(service["volumes"])  # type: ignore[call-overload]


class TestBaseComposeIsUnchanged:
    def test_base_declares_no_operator_secret(self) -> None:
        base = _load("docker-compose.yml")
        assert _SECRET not in base["secrets"]  # type: ignore[operator]

    def test_no_base_service_consumes_or_names_the_operator_secret(self) -> None:
        text = (_root() / "docker-compose.yml").read_text()
        assert "sapphire_operator" not in text
        assert "SAPPHIRE_OPERATOR" not in text

    def test_base_has_no_operator_service(self) -> None:
        assert "operator" not in _load("docker-compose.yml")["services"]  # type: ignore[operator]

    def test_the_mac_mini_and_staging_overlays_do_not_know_the_operator(self) -> None:
        for name in ("docker-compose.macmini.yml", "docker-compose.staging.yml"):
            assert "sapphire_operator" not in (_root() / name).read_text()


class TestOverlaySecret:
    def test_the_secret_is_a_file_under_secrets(self) -> None:
        secrets = _load("docker-compose.operator.yml")["secrets"]
        assert secrets == {_SECRET: {"file": f"./secrets/{_SECRET}"}}  # type: ignore[comparison-overlap]

    def test_init_gets_the_secret_and_its_path_variable(self) -> None:
        init = _load("docker-compose.operator.yml")["services"]["init"]  # type: ignore[index]
        assert _secret_names(init) == [_SECRET]
        assert init["environment"] == {
            "SAPPHIRE_OPERATOR_DB_PASSWORD_FILE": f"/run/secrets/{_SECRET}"
        }

    def test_only_init_and_the_operator_service_consume_it(self) -> None:
        services = _load("docker-compose.operator.yml")["services"]
        consumers = {n for n, s in services.items() if _SECRET in _secret_names(s)}  # type: ignore[attr-defined]
        assert consumers == {"init", "operator"}


class TestOperatorService:
    def test_it_connects_as_the_operator_role_with_its_own_secret(self) -> None:
        service = _operator()
        env = service["environment"]
        assert env["DATABASE_URL_TEMPLATE"].startswith(  # type: ignore[index]
            "postgresql+psycopg://sapphire_operator@postgres:5432/"
        )
        assert env["DB_PASSWORD_SECRET"] == f"/run/secrets/{_SECRET}"  # type: ignore[index]
        assert _secret_names(service) == [_SECRET]

    def test_it_selects_only_the_chwrr_import_overlay(self) -> None:
        env = _operator()["environment"]
        assert env["SAPPHIRE_CONFIG_OVERLAY"] == (  # type: ignore[index]
            "/app/config/overlays/chwrr-import.toml"
        )
        assert env["SAPPHIRE_CONFIG"] == "/app/config.toml"  # type: ignore[index]

    def test_it_mounts_the_inputs_the_import_reads_relative_to_the_config(self) -> None:
        mounts = [m for m in _mounts(_operator()) if isinstance(m, str)]
        assert mounts == [
            "./config.toml:/app/config.toml:ro",
            "./config/overlays/chwrr-import.toml:/app/config/overlays/chwrr-import.toml:ro",
            "./tests/fixtures/dhm/stations.toml:/app/tests/fixtures/dhm/stations.toml:ro",
        ]

    def test_the_overlay_it_mounts_is_the_checked_in_file_unchanged(self) -> None:
        overlay = (_root() / "config/overlays/chwrr-import.toml").read_text()
        assert overlay == '[deployment]\nwritable_tenants = ["chwrr"]\n'

    def test_the_delivery_directory_is_read_only_and_outside_the_checkout(
        self,
    ) -> None:
        (delivery,) = [m for m in _mounts(_operator()) if isinstance(m, dict)]
        assert delivery["read_only"] is True
        assert delivery["bind"] == {"create_host_path": False}  # type: ignore[comparison-overlap]
        assert not str(delivery["target"]).startswith("/app")
        assert "SAPPHIRE_DHM_DELIVERY_DIR" in str(delivery["source"])

    def test_it_runs_the_import_cli_so_run_arguments_are_its_arguments(self) -> None:
        assert _operator()["entrypoint"] == [
            "/entrypoint.sh",
            "python",
            "-m",
            "sapphire_flow.cli.import_dhm_delivery",
        ]

    def test_it_is_not_started_by_a_plain_up(self) -> None:
        assert _operator()["profiles"] == ["operator"]

    def test_it_keeps_the_hardened_container_shape(self) -> None:
        service = _operator()
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["restart"] == "no"
        assert service["networks"] == ["backend"]
