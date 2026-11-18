from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest

from sapphire_flow.config.deployment_profile import (
    CONFIG_READER_COVERAGE,
    EvaluationSummary,
    ProfileError,
    ProfileSelection,
    ProfileState,
    ScopeKind,
    SecretUse,
    evaluate_deployment_profile,
    load_agency_profile,
    select_deployment_profile,
)
from tests.unit.config.profile_support import incomplete_profile, recipe_profile

REPO = Path(__file__).resolve().parents[3]


def write_profile(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "agency.toml"
    path.write_text(
        'schema_version = 1\nname = "test-agency"\nstate = "configured"\n'
        'base_config = "base.toml"\noverlays = ["first.toml", "last.toml"]\n'
        'compose_files = ["compose.yml"]\n' + extra
    )
    return path


def write_installation(
    tmp_path: Path, extra: str = "", selector: str = "./agency.toml"
) -> Path:
    path = tmp_path / "installation.toml"
    path.write_text(
        f'schema_version = 1\nagency_profile = "{selector}"\n'
        'project_name = "test-agency"\nproject_directory = "checkout"\n'
        'compose_files = ["compose.yml", "extra.yml"]\n' + extra
    )
    return path


class TestSelectDeploymentProfile:
    @pytest.mark.parametrize(
        ("name", "overlay", "compose"),
        [
            ("swiss", "mac-mini.toml", "macmini"),
            ("nepal", "nepal-cloud.toml", "nepal-cloud"),
        ],
    )
    def test_private_profiles_reference_existing_recipe_chains(
        self,
        name: str,
        overlay: str,
        compose: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("SAPPHIRE_CONFIG=other-country.toml\n")
        selected = select_deployment_profile(
            profile_file=recipe_profile(
                tmp_path, "macmini" if name == "swiss" else "nepal-cloud"
            ),
            ambient_environment={},
        )
        assert selected.profile.base_config == REPO / "config.toml"
        assert selected.profile.overlays == (REPO / "config/overlays" / overlay,)
        assert selected.compose_files == (
            REPO / "docker-compose.yml",
            REPO / f"docker-compose.{compose}.yml",
        )
        assert selected.profile.state is ProfileState.CONFIGURED
        assert all(path.is_file() for path in selected.compose_files)
        assert REPO / "docker-compose.nepal-forcing.yml" not in selected.compose_files

    def test_private_profile_and_installation_resolve_each_file_relative_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        private = tmp_path / "private"
        private.mkdir()
        profile = write_profile(private)
        installation = write_installation(tmp_path, selector="./private/agency.toml")
        monkeypatch.chdir(tmp_path.parent)
        selected = select_deployment_profile(
            profile_file=profile, installation_file=installation, ambient_environment={}
        )
        assert selected.profile.overlays == (
            private / "first.toml",
            private / "last.toml",
        )
        assert selected.installation is not None
        assert selected.installation.project_directory == tmp_path / "checkout"
        assert selected.compose_files == (
            tmp_path / "compose.yml",
            tmp_path / "extra.yml",
        )

    def test_conflicting_manifest_paths_fail_even_when_names_match(
        self, tmp_path: Path
    ) -> None:
        profile = write_profile(tmp_path)
        other = tmp_path / "other.toml"
        other.write_text(profile.read_text())
        installation = write_installation(tmp_path, selector="./other.toml")
        with pytest.raises(ProfileError, match="PROFILE_SELECTION_CONFLICT"):
            select_deployment_profile(
                profile_file=profile,
                installation_file=installation,
                ambient_environment={},
            )

    def test_only_explicit_file_selection_exists(self) -> None:
        with pytest.raises(ProfileError, match="PROFILE_SELECTOR_REQUIRED"):
            select_deployment_profile(ambient_environment={})
        with pytest.raises(TypeError, match="unexpected keyword argument"):
            select_deployment_profile(name="removed")  # type: ignore[call-arg]

    @pytest.mark.parametrize(
        "key",
        [
            "SAPPHIRE_CONFIG",
            "COMPOSE_FILE",
            "SCHEDULE_FORECAST_CYCLE",
            "NHS_PROVIDER_CONFIG_SOURCE",
        ],
    )
    def test_conflicting_ambient_input_is_rejected_without_echo(
        self, tmp_path: Path, key: str
    ) -> None:
        with pytest.raises(ProfileError, match="PROFILE_AMBIENT_CONFLICT") as error:
            select_deployment_profile(
                profile_file=recipe_profile(tmp_path, "macmini"),
                ambient_environment={key: "secret-canary"},
            )
        assert "secret-canary" not in str(error.value)

    def test_matching_explicit_nonsecret_environment_is_allowed(
        self, tmp_path: Path
    ) -> None:
        profile = write_profile(tmp_path)
        installation = write_installation(
            tmp_path, '[environment]\nSAPPHIRE_DOMAIN = "forecast.example.invalid"\n'
        )
        selected = select_deployment_profile(
            profile_file=profile,
            installation_file=installation,
            ambient_environment={"SAPPHIRE_DOMAIN": "forecast.example.invalid"},
        )
        assert selected.installation is not None
        assert selected.installation.environment == (
            ("SAPPHIRE_DOMAIN", "forecast.example.invalid"),
        )

    def test_matching_ambient_selectors_do_not_override_selection(
        self, tmp_path: Path
    ) -> None:
        profile = write_profile(tmp_path)
        installation = write_installation(tmp_path)
        selected = select_deployment_profile(
            profile_file=profile,
            installation_file=installation,
            ambient_environment={
                "COMPOSE_PROJECT_NAME": "test-agency",
                "COMPOSE_FILE": f"{tmp_path}/compose.yml:{tmp_path}/extra.yml",
                "SAPPHIRE_CONFIG": str(tmp_path / "base.toml"),
                "SAPPHIRE_CONFIG_OVERLAY": (
                    f"{tmp_path}/first.toml,{tmp_path}/last.toml"
                ),
            },
        )
        assert selected.profile.manifest_path == profile

    def test_generic_example_has_no_invented_scientific_choices(
        self, tmp_path: Path
    ) -> None:
        selected = select_deployment_profile(
            profile_file=incomplete_profile(tmp_path), ambient_environment={}
        )
        assert selected.profile.state is ProfileState.INCOMPLETE
        assert selected.profile.base_config == tmp_path / "application.example.toml"
        assert selected.compose_files == ()
        assert selected.profile.overlays == ()
        assert "hydrological_day_grid" in selected.profile.required_inputs
        assert "models_and_artifacts" in selected.profile.required_inputs
        assert all(
            not line or line.startswith("#")
            for line in selected.profile.base_config.read_text().splitlines()
        )


class TestLoadAgencyProfile:
    def test_missing_manifest_fails_safely(self, tmp_path: Path) -> None:
        with pytest.raises(ProfileError, match="PROFILE_READ_ERROR"):
            load_agency_profile(tmp_path / "missing.toml")

    def test_repeated_resolved_overlay_is_rejected(self, tmp_path: Path) -> None:
        path = write_profile(tmp_path)
        path.write_text(path.read_text().replace('"last.toml"', '"./first.toml"'))
        with pytest.raises(ProfileError, match="PROFILE_PATH_DUPLICATE"):
            load_agency_profile(path)

    @pytest.mark.parametrize(
        "extra", ['host = "example.invalid"\n', 'schema = "host-profile"\n']
    )
    def test_unknown_and_host_profile_fields_fail(
        self, tmp_path: Path, extra: str
    ) -> None:
        with pytest.raises(ProfileError, match="PROFILE_SCHEMA_INVALID"):
            load_agency_profile(write_profile(tmp_path, extra))

    def test_error_does_not_echo_malformed_input(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.toml"
        path.write_text('token = "secret-canary')
        with pytest.raises(ProfileError, match="PROFILE_READ_ERROR") as error:
            load_agency_profile(path)
        assert "secret-canary" not in str(error.value)

    @pytest.mark.parametrize(
        "reference",
        ["${SAPPHIRE_CONFIG}", "https://example.invalid/config", "~/config.toml", ""],
    )
    def test_paths_are_literal_not_environment_or_remote_references(
        self, tmp_path: Path, reference: str
    ) -> None:
        path = write_profile(tmp_path)
        path.write_text(path.read_text().replace('"base.toml"', f'"{reference}"'))
        with pytest.raises(ProfileError, match="PROFILE_PATH_INVALID"):
            load_agency_profile(path)

    def test_existing_operator_and_forecast_exceptions_stay_distinct(
        self, tmp_path: Path
    ) -> None:
        profile = load_agency_profile(recipe_profile(tmp_path, "nepal-cloud"))
        operator, forecast = profile.scopes
        assert operator.kind is ScopeKind.CHWRR_IMPORT
        assert operator.base_config == REPO / "config.toml"
        assert operator.overlays == (REPO / "config/overlays/chwrr-import.toml",)
        assert operator.station_metadata == REPO / "tests/fixtures/dhm/stations.toml"
        assert forecast.kind is ScopeKind.FORECAST_ONLY
        assert forecast.base_config is None
        assert forecast.overlays == ()
        assert forecast.provider_input == "NHS_PROVIDER_CONFIG_SOURCE"

    def test_operator_cannot_inherit_agency_overlay(self, tmp_path: Path) -> None:
        path = write_profile(
            tmp_path,
            '[[scopes]]\nkind = "chwrr_import"\n'
            f'when_compose = "{REPO}/docker-compose.operator.yml"\n'
            f'base_config = "{REPO}/config.toml"\n'
            f'overlays = ["{REPO}/config/overlays/nepal-cloud.toml"]\n',
        )
        with pytest.raises(ProfileError, match="PROFILE_OPERATOR_SCOPE"):
            load_agency_profile(path)

    def test_nhs_cannot_replace_standard_worker_chain(self, tmp_path: Path) -> None:
        path = write_profile(
            tmp_path,
            '[[scopes]]\nkind = "forecast_only"\n'
            f'when_compose = "{REPO}/docker-compose.nhs-forecast.yml"\n'
            'base_config = "other.toml"\n'
            'provider_input = "NHS_PROVIDER_CONFIG_SOURCE"\n',
        )
        with pytest.raises(ProfileError, match="PROFILE_FORECAST_SCOPE"):
            load_agency_profile(path)

    def test_station_and_rating_files_are_references_not_loaded(
        self, tmp_path: Path
    ) -> None:
        path = write_profile(
            tmp_path,
            '[[inputs]]\npurpose = "stations"\nparser = "stations_toml"\n'
            'path = "missing-stations.toml"\n'
            '[[inputs]]\npurpose = "ratings"\nparser = "rating_delivery_manifest"\n',
        )
        profile = load_agency_profile(path)
        assert profile.inputs[0].path == tmp_path / "missing-stations.toml"
        assert profile.inputs[1].path is None

    def test_input_parser_must_match_its_purpose(self, tmp_path: Path) -> None:
        path = write_profile(
            tmp_path,
            '[[inputs]]\npurpose = "stations"\nparser = "rating_delivery_manifest"\n',
        )
        with pytest.raises(ProfileError, match="PROFILE_SCHEMA_INVALID"):
            load_agency_profile(path)


class TestSecretBindings:
    def test_secret_environment_values_are_not_accessed(self, tmp_path: Path) -> None:
        class GuardedEnvironment(dict[str, str]):
            def __getitem__(self, key: str) -> str:
                if key == "RECAP_DG_CLIENT_TOKEN":
                    pytest.fail("Secret environment value was accessed")
                return super().__getitem__(key)

        selected = select_deployment_profile(
            profile_file=recipe_profile(tmp_path, "macmini"),
            ambient_environment=GuardedEnvironment(RECAP_DG_CLIENT_TOKEN="canary"),
        )
        assert "recap_dg_client_token" in selected.unverified_build_inputs

    def test_runtime_bindings_are_missing_without_an_installation(
        self, tmp_path: Path
    ) -> None:
        selected = select_deployment_profile(
            profile_file=recipe_profile(tmp_path, "nepal-cloud"), ambient_environment={}
        )
        assert set(selected.missing_secret_bindings) == {
            "db_password",
            "sapphire_api_db_password",
            "sapphire_worker_db_password",
            "sapphire_backup_db_password",
            "access_token_pepper",
        }
        assert selected.unverified_build_inputs == (
            "recap_dg_client_token",
            "aquacast_token",
        )
        owner = next(
            item
            for item in selected.profile.secret_requirements
            if item.name == "db_password"
        )
        assert owner.use is SecretUse.RUNTIME
        assert owner.consumers == ("postgres", "prefect-server", "init")

    def test_secret_binding_is_not_read_or_created(self, tmp_path: Path) -> None:
        profile = write_profile(
            tmp_path,
            "[secret_requirements.worker_password]\n"
            'use = "runtime"\nconsumers = ["worker"]\n',
        )
        installation = write_installation(
            tmp_path, '[secret_bindings]\nworker_password = "private/not-created"\n'
        )
        selected = select_deployment_profile(
            profile_file=profile, installation_file=installation, ambient_environment={}
        )
        assert selected.missing_secret_bindings == ()
        assert not (tmp_path / "private").exists()

    def test_existing_secret_contents_are_never_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        profile = write_profile(
            tmp_path,
            "[secret_requirements.worker_password]\n"
            'use = "runtime"\nconsumers = ["worker"]\n',
        )
        secret = tmp_path / "secret"
        secret.write_text("secret-canary")
        installation = write_installation(
            tmp_path, '[secret_bindings]\nworker_password = "secret"\n'
        )
        read_text = Path.read_text

        def guarded_read(path: Path, *args: object, **kwargs: object) -> str:
            if path == secret:
                pytest.fail("Secret content was read")
            return read_text(path, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "read_text", guarded_read)
        selected = select_deployment_profile(
            profile_file=profile, installation_file=installation, ambient_environment={}
        )
        assert selected.missing_secret_bindings == ()

    def test_optional_secret_requires_selected_compose_file(
        self, tmp_path: Path
    ) -> None:
        profile = write_profile(
            tmp_path,
            '[secret_requirements.operator_password]\nuse = "runtime"\n'
            'consumers = ["operator"]\nwhen_compose = "extra.yml"\n',
        )
        assert (
            select_deployment_profile(
                profile_file=profile, ambient_environment={}
            ).missing_secret_bindings
            == ()
        )
        selected = select_deployment_profile(
            profile_file=profile,
            installation_file=write_installation(tmp_path),
            ambient_environment={},
        )
        assert selected.missing_secret_bindings == ("operator_password",)

    def test_optional_requirements_follow_installation_checkout(
        self, tmp_path: Path
    ) -> None:
        installation = tmp_path / "installation.toml"
        installation.write_text(
            "schema_version = 1\n"
            f'agency_profile = "{recipe_profile(tmp_path, "nepal-cloud")}"\n'
            'project_name = "nepal-example"\nproject_directory = "/srv/example"\n'
            'compose_files = ["/srv/example/docker-compose.yml", '
            '"/srv/example/docker-compose.nepal-cloud.yml", '
            '"/srv/example/docker-compose.operator.yml", '
            '"/srv/example/docker-compose.recap.yml"]\n'
        )
        selected = select_deployment_profile(
            profile_file=recipe_profile(tmp_path, "nepal-cloud"),
            installation_file=installation,
            ambient_environment={},
        )
        assert "sapphire_operator_db_password" in selected.missing_secret_bindings
        assert "sapphire_dg_api_key" in selected.missing_secret_bindings

    def test_file_binding_cannot_claim_to_supply_environment_build_token(
        self, tmp_path: Path
    ) -> None:
        profile = write_profile(
            tmp_path,
            '[secret_requirements.build_token]\nuse = "build"\n'
            'consumers = ["worker"]\nenvironment_name = "BUILD_TOKEN"\n',
        )
        installation = write_installation(
            tmp_path, '[secret_bindings]\nbuild_token = "secrets/token"\n'
        )
        with pytest.raises(ProfileError, match="INSTALLATION_SECRET_INVALID"):
            select_deployment_profile(
                profile_file=profile,
                installation_file=installation,
                ambient_environment={},
            )

    def test_secret_values_are_not_permitted_environment_inputs(
        self, tmp_path: Path
    ) -> None:
        profile = write_profile(tmp_path)
        installation = write_installation(
            tmp_path, '[environment]\nDB_PASSWORD = "secret-canary"\n'
        )
        with pytest.raises(ProfileError, match="INSTALLATION_SCHEMA_INVALID") as error:
            select_deployment_profile(
                profile_file=profile,
                installation_file=installation,
                ambient_environment={},
            )
        assert "secret-canary" not in str(error.value)


def evaluation_selection(
    tmp_path: Path, base: str, overlay: str = ""
) -> ProfileSelection:
    profile = write_profile(tmp_path)
    (tmp_path / "base.toml").write_text(base)
    (tmp_path / "first.toml").write_text(overlay)
    (tmp_path / "last.toml").write_text("")
    return select_deployment_profile(profile_file=profile, ambient_environment={})


class TestEvaluateDeploymentProfile:
    @pytest.mark.parametrize("name", ["swiss", "nepal"])
    def test_existing_recipes_have_no_missing_input_exemption(
        self, tmp_path: Path, name: str
    ) -> None:
        result = evaluate_deployment_profile(
            select_deployment_profile(
                profile_file=recipe_profile(
                    tmp_path, "macmini" if name == "swiss" else "nepal-cloud"
                ),
                ambient_environment={},
            )
        )
        assert result.summary is EvaluationSummary.FAIL
        assert result.diagnostics
        assert all(item.code == "INPUT_REQUIRED" for item in result.diagnostics)
        assert "COMPOSE_EVIDENCE_PENDING" in {
            item.code for item in result.pending_checks
        }
        timezone = next(
            item
            for item in result.provenance
            if item.field == "default_display_timezone"
        )
        assert timezone.value == (
            "Europe/Zurich" if name == "swiss" else "Asia/Kathmandu"
        )
        assert "actual_host_configuration" in result.unverified_runtime
        river = next(
            item
            for item in result.provenance
            if item.field == "adapters.river_stations.type"
        )
        assert river.value == "hydro_scraper"
        assert river.source == "base"
        forecast = next(
            item
            for item in result.provenance
            if item.field == "adapters.weather_forecast.enabled"
        )
        assert forecast.value is (name == "swiss")
        assert any(
            item.field == "adapters.weather_reanalysis.type"
            for item in result.provenance
        )

    def test_configured_label_cannot_waive_required_choices(
        self, tmp_path: Path
    ) -> None:
        result = evaluate_deployment_profile(
            evaluation_selection(tmp_path, "max_retention_days = 548\n")
        )
        missing = {
            item.field for item in result.diagnostics if item.code == "INPUT_REQUIRED"
        }
        assert result.summary is EvaluationSummary.FAIL
        assert {
            "daily_grid_origin",
            "available_nwp_parameters",
            "adapters.river_stations.type",
            "qc_rules",
        } <= missing

    def test_generic_example_stays_unresolved(self, tmp_path: Path) -> None:
        result = evaluate_deployment_profile(
            select_deployment_profile(
                profile_file=incomplete_profile(tmp_path), ambient_environment={}
            )
        )
        assert result.summary is EvaluationSummary.FAIL
        assert any(item.code == "INPUT_REQUIRED" for item in result.diagnostics)

    def test_file_overlay_default_and_list_replacement_origins(
        self, tmp_path: Path
    ) -> None:
        base = (REPO / "config.toml").read_text()
        result = evaluate_deployment_profile(
            evaluation_selection(
                tmp_path, base, "forecast_hot_days = 600\nseasons = []\n"
            )
        )
        entries = {item.field: item for item in result.provenance}
        assert entries["daily_grid_origin"].source == "base"
        assert entries["daily_grid_origin"].value == "00:00"
        assert entries["forecast_hot_days"].source == "overlay[0]"
        assert entries["forecast_hot_days"].value == 600
        assert entries["seasons"].operation == "replace"
        assert entries["seasons"].source == "overlay[0]"
        assert entries["seasons"].visibility == "redacted"
        assert entries["operational_weather.route"].operation == "default"
        assert entries["operational_weather.route"].value == "stored_reanalysis"

    def test_interpolated_values_are_redacted_and_environment_is_not_mutated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base = (REPO / "config.toml").read_text()
        selection = evaluation_selection(
            tmp_path, base, 'default_display_timezone = "${SAPPHIRE_TEST_ZONE}"\n'
        )
        monkeypatch.setenv("SAPPHIRE_TEST_ZONE", "secret-canary")
        before = dict(os.environ)
        result = evaluate_deployment_profile(
            selection, environment={"SAPPHIRE_TEST_ZONE": "Asia/Kathmandu"}
        )
        entry = next(
            item
            for item in result.provenance
            if item.field == "default_display_timezone"
        )
        assert entry.visibility == "redacted"
        assert entry.value is None
        assert entry.environment[0].name == "SAPPHIRE_TEST_ZONE"
        assert entry.environment[0].status == "present"
        assert os.environ == before
        assert "Asia/Kathmandu" not in json.dumps(asdict(result))
        assert "secret-canary" not in json.dumps(asdict(result))

    def test_raw_tenants_validate_despite_missing_runtime_placeholder(
        self, tmp_path: Path
    ) -> None:
        selection = evaluation_selection(
            tmp_path,
            "max_retention_days = 548\n"
            'default_display_timezone = "${SAPPHIRE_MISSING}"\n'
            '[tenants.example]\nname = "${SAPPHIRE_LITERAL_NAME}"\n',
        )
        result = evaluate_deployment_profile(selection)
        assert any(item.code == "CONFIG_LOAD_INVALID" for item in result.diagnostics)
        assert not any(
            item.code == "RAW_TENANTS_INVALID" for item in result.diagnostics
        )
        assert any(
            item.field == "tenants.name" and item.source == "base.raw_tenants"
            for item in result.provenance
        )
        reference = next(
            item for item in result.provenance if item.field == "interpolation"
        )
        assert all(item.status == "missing" for item in reference.environment)

    def test_invalid_keys_strings_and_errors_do_not_leak(self, tmp_path: Path) -> None:
        base = (
            (REPO / "config.toml")
            .read_text()
            .replace(
                'default_display_timezone = "Europe/Zurich"',
                'default_display_timezone = "secret-canary"',
            )
        )
        base = '"secret-key-canary" = "secret-value-canary"\n' + base
        result = evaluate_deployment_profile(
            evaluation_selection(
                tmp_path, base, 'daily_grid_origin = "invalid-secret-canary"\n'
            )
        )
        report = json.dumps(asdict(result))
        assert "canary" not in report
        assert str(tmp_path) not in report
        assert any(item.code == "CONFIG_READER_INVALID" for item in result.diagnostics)

    def test_unconstrained_string_value_is_not_printed(self, tmp_path: Path) -> None:
        base = (REPO / "config.toml").read_text()
        result = evaluate_deployment_profile(
            evaluation_selection(
                tmp_path, base, 'default_display_timezone = "secret-canary"\n'
            )
        )
        entry = next(
            item
            for item in result.provenance
            if item.field == "default_display_timezone"
        )
        assert entry.visibility == "redacted"
        assert "secret-canary" not in json.dumps(asdict(result))

    def test_existing_m1_validator_rejects_nonzero_grid(self, tmp_path: Path) -> None:
        result = evaluate_deployment_profile(
            evaluation_selection(
                tmp_path,
                (REPO / "config.toml").read_text(),
                'daily_grid_origin = "18:00"\n',
            )
        )
        assert any(
            item.field == "deployment_config" and item.code == "CONFIG_READER_INVALID"
            for item in result.diagnostics
        )

    def test_onboarding_tenant_must_be_writable(self, tmp_path: Path) -> None:
        result = evaluate_deployment_profile(
            evaluation_selection(
                tmp_path,
                (REPO / "config.toml").read_text(),
                '[onboarding]\ntenant = "another-agency"\n',
            )
        )
        assert any(
            item.code == "ONBOARDING_TENANT_OUTSIDE_AUTHORITY"
            for item in result.diagnostics
        )

    def test_raw_tenant_provenance_uses_last_name_not_last_empty_table(
        self, tmp_path: Path
    ) -> None:
        selection = evaluation_selection(
            tmp_path,
            '[tenants.example]\nname = "Base"\n',
            '[tenants.example]\nname = "Overlay"\n',
        )
        (tmp_path / "last.toml").write_text("[tenants.example]\n")
        result = evaluate_deployment_profile(selection)
        entry = next(item for item in result.provenance if item.field == "tenants.name")
        assert entry.source == "overlay[0].raw_tenants"

    def test_coverage_inventory_requires_classification_for_each_module(self) -> None:
        assert set(CONFIG_READER_COVERAGE) == {
            path.stem for path in (REPO / "src/sapphire_flow/config").glob("*.py")
        }

    def test_evaluation_does_not_write_or_construct_runtime_services(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import socket
        import subprocess

        selection = evaluation_selection(tmp_path, (REPO / "config.toml").read_text())
        # Import/initialize pure readers before prohibiting runtime effects.
        evaluate_deployment_profile(selection)

        def forbidden(*args: object, **kwargs: object) -> None:
            pytest.fail("Unexpected runtime side effect")

        monkeypatch.setattr(Path, "mkdir", forbidden)
        monkeypatch.setattr(Path, "write_text", forbidden)
        monkeypatch.setattr(socket, "create_connection", forbidden)
        monkeypatch.setattr(subprocess, "run", forbidden)
        result = evaluate_deployment_profile(selection)
        assert (
            result.summary is EvaluationSummary.FAIL
        )  # private agency still lacks explicit choices


class TestPureReaderParity:
    @pytest.mark.parametrize("name", ["swiss", "nepal"])
    def test_extracted_readers_match_legacy_loaders(
        self, tmp_path: Path, name: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sapphire_flow.config._overlay import load_merged_toml
        from sapphire_flow.config.deployment import load_config, parse_deployment_config
        from sapphire_flow.config.deployment_identity import (
            load_deployment_identity_config,
            parse_deployment_identity_config,
        )
        from sapphire_flow.config.forecast_qc_rules import (
            load_forecast_qc_rules,
            parse_forecast_qc_rules,
        )
        from sapphire_flow.config.onboarding import (
            load_onboarding_config,
            parse_onboarding_config,
        )
        from sapphire_flow.config.qc_rules import load_qc_rules, parse_qc_rules

        profile = select_deployment_profile(
            profile_file=recipe_profile(
                tmp_path, "macmini" if name == "swiss" else "nepal-cloud"
            ),
            ambient_environment={},
        ).profile
        monkeypatch.setenv(
            "SAPPHIRE_CONFIG_OVERLAY", ",".join(str(path) for path in profile.overlays)
        )
        raw = load_merged_toml(
            profile.base_config, list(profile.overlays), environment={}
        )
        assert parse_deployment_config(raw) == load_config(profile.base_config)
        assert parse_deployment_identity_config(raw) == load_deployment_identity_config(
            profile.base_config
        )
        assert parse_onboarding_config(raw) == load_onboarding_config(
            profile.base_config
        )
        assert parse_qc_rules(raw)[0] == load_qc_rules(profile.base_config)
        assert parse_forecast_qc_rules(raw)[0] == load_forecast_qc_rules(
            profile.base_config
        )


class TestGenericDefaults:
    def test_exact_neutral_allowlist_uses_actual_typed_defaults(
        self, tmp_path: Path
    ) -> None:
        from sapphire_flow.config.deployment import DeploymentConfig
        from sapphire_flow.config.deployment_profile import NEUTRAL_DEFAULT_FIELDS

        expected = {
            "default_display_timezone",
            "calendar",
            "enable_forecast_alerts",
            "enable_observation_alerts",
            "enable_pipeline_alerts",
        }
        assert expected == NEUTRAL_DEFAULT_FIELDS
        result = evaluate_deployment_profile(
            evaluation_selection(tmp_path, "max_retention_days = 3650\n")
        )
        typed = DeploymentConfig(max_retention_days=3650).model_dump(mode="json")
        settings = {item.field: item for item in result.provenance}
        for field in expected:
            assert settings[field].value == typed[field]
            assert settings[field].default_status == "neutral"
            assert settings[field].source == f"DeploymentConfig.{field}"
        missing = {
            item.field for item in result.diagnostics if item.code == "INPUT_REQUIRED"
        }
        assert not missing & expected
        assert {
            "daily_grid_origin",
            "available_nwp_parameters",
            "available_past_only_nwp_parameters",
            "reanalysis_source",
        } <= missing
        assert settings["nwp_cycle_min_age_minutes"].default_status == "review"
        assert settings["reanalysis_source"].default_status == "review"
        assert settings["reanalysis_source"].value == typed["reanalysis_source"]
        assert any(
            item.code == "DEFAULT_REVIEW_PENDING" and item.owner == "developer"
            for item in result.pending_checks
        )

    def test_explicit_overrides_are_not_inherited_defaults(
        self, tmp_path: Path
    ) -> None:
        selected = evaluation_selection(
            tmp_path,
            "max_retention_days = 3650\n",
            'default_display_timezone = "Europe/Paris"\n'
            "enable_pipeline_alerts = true\n",
        )
        fields = {
            item.field: item
            for item in evaluate_deployment_profile(selected).provenance
        }
        assert fields["default_display_timezone"].value == "Europe/Paris"
        assert fields["enable_pipeline_alerts"].value is True
        assert fields["enable_pipeline_alerts"].default_status == "explicit"
        assert fields["enable_pipeline_alerts"].source == "overlay[0]"

    @pytest.mark.parametrize(
        ("provider", "enabled", "age_required"),
        [
            ("meteoswiss_nwp", True, True),
            ("meteoswiss_nwp", False, False),
            ("recap_gateway", True, False),
        ],
    )
    def test_provider_specific_inputs(
        self, tmp_path: Path, provider: str, enabled: bool, age_required: bool
    ) -> None:
        base = (
            "max_retention_days = 3650\navailable_nwp_parameters = []\n"
            "available_past_only_nwp_parameters = []\n"
            f'[adapters.weather_forecast]\ntype = "{provider}"\n'
            f"enabled = {str(enabled).lower()}\n"
        )
        result = evaluate_deployment_profile(evaluation_selection(tmp_path, base))
        missing = {
            item.field for item in result.diagnostics if item.code == "INPUT_REQUIRED"
        }
        assert ("nwp_cycle_min_age_minutes" in missing) == age_required
        assert "available_nwp_parameters" not in missing
        assert "available_past_only_nwp_parameters" not in missing

    def test_legacy_state_is_rejected(self, tmp_path: Path) -> None:
        profile = write_profile(tmp_path)
        profile.write_text(
            profile.read_text().replace('state = "configured"', 'state = "legacy"')
        )
        with pytest.raises(ProfileError, match="PROFILE_SCHEMA_INVALID"):
            load_agency_profile(profile)

    def test_gateway_key_loader_is_not_called(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sapphire_flow.config import recap_gateway

        secret = tmp_path / "key"
        secret.write_text("credential-canary")
        monkeypatch.setenv("RECAP_API_KEY_FILE", str(secret))
        monkeypatch.setattr(
            recap_gateway,
            "load_recap_api_key",
            lambda: pytest.fail("credential loader called"),
        )
        result = evaluate_deployment_profile(
            evaluation_selection(
                tmp_path,
                "max_retention_days = 3650\n[adapters.weather_forecast]\n"
                'type = "recap_gateway"\nenabled = true\n',
            )
        )
        assert "credential-canary" not in json.dumps(asdict(result))

    def test_first_example_names_retention_grid_and_shows_only_absent_neutral_defaults(
        self, tmp_path: Path
    ) -> None:
        from sapphire_flow.config.deployment import DeploymentConfig
        from sapphire_flow.config.deployment_profile import NEUTRAL_DEFAULT_FIELDS

        selected = select_deployment_profile(
            profile_file=incomplete_profile(tmp_path), ambient_environment={}
        )
        evaluated = evaluate_deployment_profile(selected)
        missing = {
            item.field: item
            for item in evaluated.diagnostics
            if item.code == "INPUT_REQUIRED"
        }
        assert {"max_retention_days", "daily_grid_origin"} <= missing.keys()
        assert missing["max_retention_days"].owner == "administrator"
        assert "max_retention_days" in missing["max_retention_days"].next_action
        neutral = {
            item.field: item
            for item in evaluated.provenance
            if item.default_status == "neutral"
        }
        assert set(neutral) == NEUTRAL_DEFAULT_FIELDS
        for field, setting in neutral.items():
            assert setting.value == DeploymentConfig.model_fields[field].default
            assert setting.source == f"DeploymentConfig.{field}"
        assert evaluated.summary is EvaluationSummary.FAIL

    def test_invalid_explicit_neutral_value_is_not_given_default_provenance(
        self, tmp_path: Path
    ) -> None:
        result = evaluate_deployment_profile(
            evaluation_selection(
                tmp_path, 'enable_pipeline_alerts = "credential-canary"\n'
            )
        )
        assert result.summary is EvaluationSummary.FAIL
        assert any(item.code == "CONFIG_READER_INVALID" for item in result.diagnostics)
        assert not any(
            item.field == "enable_pipeline_alerts" for item in result.provenance
        )
        assert any(
            item.field == "calendar" and item.default_status == "neutral"
            for item in result.provenance
        )
        assert "credential-canary" not in json.dumps(asdict(result))
