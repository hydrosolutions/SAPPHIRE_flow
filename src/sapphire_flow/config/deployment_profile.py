from __future__ import annotations

import math
import os
import re
import tomllib
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sapphire_flow.config._overlay import TomlProvenance
    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.config.weather_forecast import WeatherForecastAdapterConfig

_REPOSITORY = Path(__file__).resolve().parents[3]
_ENVIRONMENT_NAMES = frozenset(
    {
        "VERSION",
        "SAPPHIRE_RELEASE_VERSION",
        "SAPPHIRE_SOURCE_REVISION",
        "SAPPHIRE_DOMAIN",
        "SAPPHIRE_CORS_ORIGINS",
        "SAPPHIRE_REQUIRE_NWP",
        "SAPPHIRE_SKIP_DEPLOYMENTS",
        "SAPPHIRE_ONLY_DEPLOYMENTS",
        "SCHEDULE_REFRESH_OPERATIONAL_ERA5",
        "SAPPHIRE_ERA5_WEATHER_SCOPE",
        "SAPPHIRE_BACKUP_DUMP_TIMEOUT_S",
        "SAPPHIRE_DHM_DELIVERY_DIR",
        "NHS_PROVIDER_CONFIG_SOURCE",
        "NHS_FORECAST_JOB_OVERLAYS",
    }
)
_SELECTORS = frozenset(
    {
        "SAPPHIRE_CONFIG",
        "SAPPHIRE_CONFIG_OVERLAY",
        "COMPOSE_FILE",
        "COMPOSE_PROJECT_NAME",
    }
)


class ProfileError(ValueError):
    def __init__(self, code: str, message: str, *, field: str = "selection") -> None:
        self.code = code
        self.field = (
            field
            if field
            in _ENVIRONMENT_NAMES
            | _SELECTORS
            | {
                "agency_profile",
                "secret_bindings",
                "environment",
                "profile",
                "installation",
            }
            else "selection"
        )
        super().__init__(f"{code}: {message}")


class ProfileState(StrEnum):
    INCOMPLETE = "incomplete"
    CONFIGURED = "configured"


class SecretUse(StrEnum):
    RUNTIME = "runtime"
    BUILD = "build"


class ScopeKind(StrEnum):
    CHWRR_IMPORT = "chwrr_import"
    FORECAST_ONLY = "forecast_only"


@dataclass(frozen=True, kw_only=True, slots=True)
class ConfigScope:
    kind: ScopeKind
    when_compose: Path
    base_config: Path | None
    overlays: tuple[Path, ...]
    provider_input: str | None
    station_metadata: Path | None


@dataclass(frozen=True, kw_only=True, slots=True)
class InputReference:
    purpose: Literal["stations", "ratings", "forecast_provider"]
    parser: Literal["stations_toml", "rating_delivery_manifest", "river_stations"]
    path: Path | None


@dataclass(frozen=True, kw_only=True, slots=True)
class SecretRequirement:
    name: str
    use: SecretUse
    consumers: tuple[str, ...]
    when_compose: Path | None
    environment_name: str | None


@dataclass(frozen=True, kw_only=True, slots=True)
class AgencyProfile:
    manifest_path: Path
    name: str
    state: ProfileState
    base_config: Path
    overlays: tuple[Path, ...]
    compose_files: tuple[Path, ...]
    scopes: tuple[ConfigScope, ...]
    inputs: tuple[InputReference, ...]
    required_inputs: tuple[str, ...]
    secret_requirements: tuple[SecretRequirement, ...]
    runbook: Path | None


@dataclass(frozen=True, kw_only=True, slots=True)
class Installation:
    source_path: Path
    project_name: str
    project_directory: Path
    compose_files: tuple[Path, ...]
    environment: tuple[tuple[str, str], ...]
    secret_bindings: tuple[tuple[str, Path], ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class ProfileSelection:
    profile: AgencyProfile
    installation: Installation | None
    compose_files: tuple[Path, ...]
    missing_secret_bindings: tuple[str, ...]
    unverified_build_inputs: tuple[str, ...]


class EvaluationSummary(StrEnum):
    FAIL = "FAIL"
    PENDING = "PENDING"
    PASS_OFFLINE = "PASS_OFFLINE"


@dataclass(frozen=True, kw_only=True, slots=True)
class ProfileDiagnostic:
    code: str
    field: str
    source: str
    cause: str
    next_action: str
    owner: Literal["administrator", "agency_specialist", "developer"] = "administrator"
    documentation: str = "docs/deployment/profiles.md"


@dataclass(frozen=True, kw_only=True, slots=True)
class EnvironmentReference:
    name: str
    status: Literal["present", "missing"]


@dataclass(frozen=True, kw_only=True, slots=True)
class EffectiveSetting:
    field: str
    source: str
    operation: Literal["set", "replace", "default"]
    value: str | int | float | bool | None
    visibility: Literal["value", "redacted", "absent"]
    environment: tuple[EnvironmentReference, ...] = ()
    default_status: Literal["explicit", "neutral", "review", "fallback", "inactive"] = (
        "explicit"
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class ProfileEvaluation:
    summary: EvaluationSummary
    diagnostics: tuple[ProfileDiagnostic, ...]
    provenance: tuple[EffectiveSetting, ...]
    pending_checks: tuple[ProfileDiagnostic, ...]
    unverified_runtime: tuple[str, ...]


# Each module has an explicit disposition. No dynamic imports/discovery at runtime.
CONFIG_READER_COVERAGE: dict[str, str] = {
    "deployment": "checked with parse_deployment_config, including nested models",
    "weather_forecast": "shared mapping parser; enabled providers remain pending",
    "cors": "pure exact-origin parser used by the API and wiring checker",
    "onboarding": "checked with parse_onboarding_config",
    "fine_tuning": "per-run TOML validated by CLI/worker; not host defaults",
    "qc_rules": "checked with parse_qc_rules; legacy defaults labelled",
    "forecast_qc_rules": "checked with parse_forecast_qc_rules",
    "deployment_identity": "checked with parse_deployment_identity_config",
    "declared_tenants": "checked independently using raw TOML loader",
    "river_stations": "checked by parser; optional provider lifecycle remains pending",
    "dhm": "nested in river_stations when selected",
    "recap_gateway": "checked if declared or selected; no API-key loader invoked",
    "nepal_weather": "named weather scopes checked when declared",
    "operational_weather": "nested in DeploymentConfig",
    "network_overview": "nested in DeploymentConfig",
    "daily_evaluation": "nested in DeploymentConfig",
    "evaluation_forecasts": "nested in DeploymentConfig",
    "provisional_rating": "nested model; delivery files pending",
    "weather_gap": "nested in DeploymentConfig",
    "swiss_overview": "nested model; packaged roster check pending if selected",
    "bafu_units": "external registry remains pending unless worker selection is absent",
    "paths": "data_dir parsed by DeploymentConfig; directory helpers never invoked",
    "_overlay": "shared merge/interpolation and provenance helper",
    "deployment_profile": "profile/installation boundary and this evaluator",
    "__init__": "package helper; no independent configuration reader",
}


class _Boundary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _ScopeInput(_Boundary):
    kind: Literal["chwrr_import", "forecast_only"]
    when_compose: str
    base_config: str | None = None
    overlays: list[str] = Field(default_factory=list)
    provider_input: str | None = None


class _ReferenceInput(_Boundary):
    purpose: Literal["stations", "ratings", "forecast_provider"]
    parser: Literal["stations_toml", "rating_delivery_manifest", "river_stations"]
    path: str | None = None

    @model_validator(mode="after")
    def matching_parser(self) -> _ReferenceInput:
        parsers = {
            "stations": "stations_toml",
            "ratings": "rating_delivery_manifest",
            "forecast_provider": "river_stations",
        }
        if self.parser != parsers[self.purpose]:
            raise ValueError("Parser must match input purpose")
        return self


class _SecretInput(_Boundary):
    use: Literal["runtime", "build"]
    consumers: list[str] = Field(min_length=1)
    when_compose: str | None = None
    environment_name: str | None = None


class _ProfileInput(_Boundary):
    schema_version: Literal[1]
    name: str = Field(min_length=1)
    state: Literal["incomplete", "configured"]
    base_config: str
    overlays: list[str] = Field(default_factory=list)
    compose_files: list[str]
    scopes: list[_ScopeInput] = Field(default_factory=lambda: list[_ScopeInput]())
    inputs: list[_ReferenceInput] = Field(
        default_factory=lambda: list[_ReferenceInput]()
    )
    required_inputs: list[str] = Field(default_factory=list)
    secret_requirements: dict[str, _SecretInput] = Field(default_factory=dict)
    runbook: str | None = None


class _InstallationInput(_Boundary):
    schema_version: Literal[1]
    agency_profile: str
    project_name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    project_directory: str
    compose_files: list[str] = Field(min_length=1)
    environment: dict[str, str] = Field(default_factory=dict)
    secret_bindings: dict[str, str] = Field(default_factory=dict)

    @field_validator("environment")
    @classmethod
    def permitted_environment(cls, value: dict[str, str]) -> dict[str, str]:
        for name in value:
            if name not in _ENVIRONMENT_NAMES and not re.fullmatch(
                r"SCHEDULE_[A-Z_]+", name
            ):
                raise ValueError("Environment key is not permitted")
        return value


def _file_path(value: str, directory: Path) -> Path:
    if (
        not value.strip()
        or any(x in value for x in ("\x00", "\n", "\r", "${", "://"))
        or value.startswith("~")
    ):
        raise ProfileError("PROFILE_PATH_INVALID", "Use a literal local path")
    path = Path(value)
    return (directory / path).resolve()


def _paths(values: list[str], directory: Path) -> tuple[Path, ...]:
    paths = tuple(_file_path(value, directory) for value in values)
    if len(set(paths)) != len(paths):
        raise ProfileError("PROFILE_PATH_DUPLICATE", "File lists must not repeat paths")
    return paths


def _read_toml(path: Path) -> dict[str, object]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        raise ProfileError(
            "PROFILE_READ_ERROR", "Cannot read a valid TOML document"
        ) from None


def _scope(raw: _ScopeInput, directory: Path, repository_root: Path) -> ConfigScope:
    scope = ConfigScope(
        kind=ScopeKind(raw.kind),
        when_compose=_file_path(raw.when_compose, directory),
        base_config=_file_path(raw.base_config, directory)
        if raw.base_config is not None
        else None,
        overlays=_paths(raw.overlays, directory),
        provider_input=raw.provider_input,
        station_metadata=repository_root / "tests/fixtures/dhm/stations.toml"
        if raw.kind == "chwrr_import"
        else None,
    )
    if scope.kind is ScopeKind.CHWRR_IMPORT:
        if (
            scope.when_compose != repository_root / "docker-compose.operator.yml"
            or scope.base_config != repository_root / "config.toml"
            or scope.overlays
            != (repository_root / "config/overlays/chwrr-import.toml",)
            or scope.provider_input is not None
        ):
            raise ProfileError(
                "PROFILE_OPERATOR_SCOPE",
                "CHWRR import requires its unchanged repository config chain",
            )
    elif (
        scope.when_compose != repository_root / "docker-compose.nhs-forecast.yml"
        or scope.base_config is not None
        or scope.overlays
        or scope.provider_input != "NHS_PROVIDER_CONFIG_SOURCE"
    ):
        raise ProfileError(
            "PROFILE_FORECAST_SCOPE",
            "NHS scope must retain the worker prefix and provider-only suffix",
        )
    return scope


def _secret(name: str, raw: _SecretInput, directory: Path) -> SecretRequirement:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or len(set(raw.consumers)) != len(
        raw.consumers
    ):
        raise ProfileError(
            "PROFILE_SECRET_INVALID", "Invalid secret name or repeated consumer"
        )
    if any(
        not re.fullmatch(r"[a-z][a-z0-9_-]*", consumer) for consumer in raw.consumers
    ):
        raise ProfileError("PROFILE_SECRET_INVALID", "Invalid consumer name")
    if (raw.use == "build") != (raw.environment_name is not None):
        raise ProfileError(
            "PROFILE_SECRET_INVALID", "Only build requirements name environment inputs"
        )
    if raw.environment_name is not None and not re.fullmatch(
        r"[A-Z][A-Z0-9_]*", raw.environment_name
    ):
        raise ProfileError("PROFILE_SECRET_INVALID", "Invalid build input name")
    return SecretRequirement(
        name=name,
        use=SecretUse(raw.use),
        consumers=tuple(raw.consumers),
        when_compose=_file_path(raw.when_compose, directory)
        if raw.when_compose is not None
        else None,
        environment_name=raw.environment_name,
    )


def load_agency_profile(
    path: Path, *, repository_root: Path = _REPOSITORY
) -> AgencyProfile:
    """Parse references only; do not load application config or secret contents."""
    path = path.resolve()
    repository_root = repository_root.resolve()
    try:
        raw = _ProfileInput.model_validate(_read_toml(path))
    except ValidationError:
        raise ProfileError(
            "PROFILE_SCHEMA_INVALID", "Invalid agency profile fields"
        ) from None
    scopes = tuple(_scope(scope, path.parent, repository_root) for scope in raw.scopes)
    if len({scope.kind for scope in scopes}) != len(scopes):
        raise ProfileError(
            "PROFILE_SCOPE_DUPLICATE", "Each exceptional scope may appear once"
        )
    if raw.state != "incomplete" and not raw.compose_files:
        raise ProfileError(
            "PROFILE_COMPOSE_REQUIRED",
            "A configured profile needs Compose files",
        )
    if raw.state == "incomplete" and not raw.required_inputs:
        raise ProfileError(
            "PROFILE_INPUTS_REQUIRED",
            "An incomplete profile must list unresolved inputs",
        )
    return AgencyProfile(
        manifest_path=path,
        name=raw.name,
        state=ProfileState(raw.state),
        base_config=_file_path(raw.base_config, path.parent),
        overlays=_paths(raw.overlays, path.parent),
        compose_files=_paths(raw.compose_files, path.parent),
        scopes=scopes,
        inputs=tuple(
            InputReference(
                purpose=item.purpose,
                parser=item.parser,
                path=_file_path(item.path, path.parent)
                if item.path is not None
                else None,
            )
            for item in raw.inputs
        ),
        required_inputs=tuple(raw.required_inputs),
        secret_requirements=tuple(
            _secret(name, item, path.parent)
            for name, item in raw.secret_requirements.items()
        ),
        runbook=_file_path(raw.runbook, path.parent)
        if raw.runbook is not None
        else None,
    )


def _installation(
    path: Path, profile: AgencyProfile, repository_root: Path
) -> Installation:
    path = path.resolve()
    try:
        raw = _InstallationInput.model_validate(_read_toml(path))
    except ValidationError:
        raise ProfileError(
            "INSTALLATION_SCHEMA_INVALID", "Invalid installation fields"
        ) from None
    if _file_path(raw.agency_profile, path.parent) != profile.manifest_path:
        raise ProfileError(
            "PROFILE_SELECTION_CONFLICT",
            "Installation and explicit selector must reference the same manifest",
            field="agency_profile",
        )
    runtime_names = {
        item.name
        for item in profile.secret_requirements
        if item.use is SecretUse.RUNTIME
    }
    if set(raw.secret_bindings) - runtime_names:
        raise ProfileError(
            "INSTALLATION_SECRET_INVALID",
            "Bindings must name declared runtime file requirements",
            field="secret_bindings",
        )
    return Installation(
        source_path=path,
        project_name=raw.project_name,
        project_directory=_file_path(raw.project_directory, path.parent),
        compose_files=_paths(raw.compose_files, path.parent),
        environment=tuple(raw.environment.items()),
        secret_bindings=tuple(
            (name, _file_path(value, path.parent))
            for name, value in raw.secret_bindings.items()
        ),
    )


def select_deployment_profile(
    *,
    profile_file: Path | None = None,
    installation_file: Path | None = None,
    repository_root: Path = _REPOSITORY,
    ambient_environment: Mapping[str, str] | None = None,
) -> ProfileSelection:
    if profile_file is None:
        raise ProfileError(
            "PROFILE_SELECTOR_REQUIRED", "Select an explicit profile file"
        )
    repository_root = repository_root.resolve()
    profile = load_agency_profile(profile_file, repository_root=repository_root)
    installation = (
        _installation(installation_file, profile, repository_root)
        if installation_file is not None
        else None
    )
    compose_files = (
        installation.compose_files
        if installation is not None
        else profile.compose_files
    )
    _check_ambient(
        profile,
        installation,
        compose_files,
        os.environ if ambient_environment is None else ambient_environment,
    )
    project_directory = (
        installation.project_directory if installation is not None else repository_root
    )
    requirements = tuple(
        item
        for item in profile.secret_requirements
        if item.when_compose is None
        or _installed_path(item.when_compose, repository_root, project_directory)
        in compose_files
    )
    bindings = dict(installation.secret_bindings) if installation is not None else {}
    return ProfileSelection(
        profile=profile,
        installation=installation,
        compose_files=compose_files,
        missing_secret_bindings=tuple(
            item.name
            for item in requirements
            if item.use is SecretUse.RUNTIME and item.name not in bindings
        ),
        unverified_build_inputs=tuple(
            item.name for item in requirements if item.use is SecretUse.BUILD
        ),
    )


def _installed_path(path: Path, repository_root: Path, project_directory: Path) -> Path:
    # Repository-owned optional files follow the explicitly declared checkout.
    if path.is_relative_to(repository_root):
        return project_directory / path.relative_to(repository_root)
    return path


def _check_ambient(
    profile: AgencyProfile,
    installation: Installation | None,
    compose_files: tuple[Path, ...],
    environment: Mapping[str, str],
) -> None:
    declared = dict(installation.environment) if installation is not None else {}
    if installation is not None:
        declared["COMPOSE_PROJECT_NAME"] = installation.project_name
    for key in environment:
        if not (
            key in _SELECTORS
            or key in _ENVIRONMENT_NAMES
            or key.startswith("SCHEDULE_")
        ):
            continue
        value = environment[key]
        if not value:
            continue
        if key == "COMPOSE_FILE":
            agrees = _paths(value.split(os.pathsep), Path.cwd()) == compose_files
        elif key == "SAPPHIRE_CONFIG":
            agrees = _file_path(value, Path.cwd()) == profile.base_config
        elif key == "SAPPHIRE_CONFIG_OVERLAY":
            agrees = (
                _paths(
                    [part.strip() for part in value.split(",") if part.strip()],
                    Path.cwd(),
                )
                == profile.overlays
            )
        else:
            agrees = declared.get(key) == value
        if not agrees:
            raise ProfileError(
                "PROFILE_AMBIENT_CONFLICT",
                "Unset conflicting ambient settings or declare matching "
                "installation inputs",
                field=key,
            )


NEUTRAL_DEFAULT_FIELDS = frozenset(
    {
        "calendar",
        "enable_forecast_alerts",
        "enable_observation_alerts",
        "enable_pipeline_alerts",
    }
)

_PREVIEW_FIELDS = (
    "daily_grid_origin",
    "daily_grid_origin_overrides",
    "default_display_timezone",
    "calendar",
    "weather_hot_days",
    "forecast_hot_days",
    "max_retention_days",
    "reanalysis_source",
    "danger_levels",
    "seasons",
    "enable_forecast_alerts",
    "enable_observation_alerts",
    "enable_pipeline_alerts",
    "available_nwp_parameters",
    "available_past_only_nwp_parameters",
    "nwp_cycle_min_age_minutes",
    "threshold_check_mode",
    "operational_weather.route",
    "operational_weather.automatic_issue_timezone",
)
REQUIRED_INPUTS: dict[
    str, tuple[Literal["administrator", "agency_specialist", "developer"], str]
] = {
    "tenant_identity": ("administrator", "tenant identity and write authority"),
    "observation_source": (
        "agency_specialist",
        "the observation source and delivery format",
    ),
    "forecast_weather_source": ("agency_specialist", "the forecast weather source"),
    "historical_weather_source": ("agency_specialist", "the historical weather source"),
    "stations_and_identifiers": ("agency_specialist", "station files and identifiers"),
    "rating_units_datums": ("agency_specialist", "rating files, units and datums"),
    "qc_danger_season_policy": (
        "agency_specialist",
        "QC, danger-level and season policy",
    ),
    "models_and_artifacts": (
        "agency_specialist",
        "model choices and artifact references",
    ),
    "training_time_conventions": (
        "agency_specialist",
        "model training-time conventions",
    ),
    "hydrological_day_grid": ("agency_specialist", "hydrological day and time grid"),
    "schedules": ("administrator", "job schedules and exclusions"),
    "hosting_paths_domain_ports": ("administrator", "hosting paths, domain and ports"),
    "secret_requirements_and_bindings": (
        "administrator",
        "logical secret requirements and delivery references",
    ),
}


_EXPLICIT_AGENCY_FIELDS = (
    "max_retention_days",
    "daily_grid_origin",
    "reanalysis_source",
    "danger_levels",
    "seasons",
    "qc_rules",
    "forecast_qc_rules",
    "deployment",
    "tenants",
    "onboarding.tenant",
    "onboarding.data_source",
    "onboarding.basin_ids",
    "adapters.river_stations.type",
    "adapters.weather_forecast.enabled",
    "adapters.weather_reanalysis.type",
    "adapters.weather_stations.type",
    "available_nwp_parameters",
    "available_past_only_nwp_parameters",
)


def _diagnostic(
    code: str, field: str, cause: str, action: str, source: str = "configuration"
) -> ProfileDiagnostic:
    return ProfileDiagnostic(
        code=code,
        field=field,
        source=source,
        cause=cause,
        next_action=action,
        owner="developer"
        if code
        in {
            "DEVELOPER_WORK_REQUIRED",
            "DEFAULT_REVIEW_PENDING",
            "ADAPTER_POLICY_PENDING",
            "UNCHECKED_SECTIONS",
            "ROSTER_PENDING",
            "SCOPED_INPUTS_PENDING",
            "CONFIG_EXTENSION_PENDING",
            "EXECUTION_COVERAGE_PENDING",
            "BAFU_REGISTRY_PENDING",
            "AGENCY_EVIDENCE_PENDING",
        }
        else "administrator",
    )


def _lookup(raw: dict[str, object], field: str) -> object:
    key, separator, remainder = field.partition(".")
    value = raw.get(key)
    if not separator:
        return value
    if not isinstance(value, dict):
        return None
    return _lookup(cast("dict[str, object]", value), remainder)


def _document_alias(index: int) -> str:
    return "base" if index == 0 else f"overlay[{index - 1}]"


def _environment_references(
    names: tuple[str, ...], supplied: set[str]
) -> tuple[EnvironmentReference, ...]:
    return tuple(
        EnvironmentReference(
            name=name
            if re.fullmatch(r"SAPPHIRE_[A-Z0-9_]{1,72}", name)
            else "[redacted]",
            status="present" if name in supplied else "missing",
        )
        for name in names
    )


def _safe_setting_value(field: str, value: object) -> str | int | float | bool | None:
    if isinstance(value, (bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if not isinstance(value, str):
        return None
    if field == "daily_grid_origin" and re.fullmatch(
        r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value
    ):
        return value
    if field in {
        "default_display_timezone",
        "operational_weather.automatic_issue_timezone",
    }:
        try:
            ZoneInfo(value)
        except (OSError, ValueError, ZoneInfoNotFoundError):
            return None
        return value
    allowed = {
        "calendar": {"gregorian", "bikram_sambat"},
        "reanalysis_source": {"single", "hybrid", "era5_land", "recap_era5_land"},
        "threshold_check_mode": {"raw", "published", "both"},
        "operational_weather.route": {"stored_reanalysis", "recap_combined"},
        "onboarding.data_source": {"camels-ch", "dhm"},
        "adapters.river_stations.type": {"hydro_scraper", "dhm"},
        "adapters.weather_forecast.type": {"meteoswiss_nwp", "recap_gateway"},
    }
    return value if value in allowed.get(field, set()) else None


def _setting(
    field: str,
    value: object,
    trace: TomlProvenance,
    supplied: set[str],
    default_reader: str = "DeploymentConfig",
) -> EffectiveSetting:
    key = tuple(field.split("."))
    origin = trace.origins.get(key)
    if origin is None:
        descendants = [
            item for path, item in trace.origins.items() if path[: len(key)] == key
        ]
        if descendants:
            documents = {item.document for item in descendants}
            source = (
                _document_alias(next(iter(documents)))
                if len(documents) == 1
                else "mixed-documents"
            )
            return EffectiveSetting(
                field=field,
                source=source,
                operation="set",
                value=None,
                visibility="redacted",
            )
        source = f"{default_reader}.{field}"
        operation: Literal["set", "replace", "default"] = "default"
        variables: tuple[str, ...] = ()
    else:
        source = _document_alias(origin.document)
        operation = origin.operation
        variables = trace.variables.get(origin.document, ())
    safe_value = None if variables else _safe_setting_value(field, value)
    return EffectiveSetting(
        field=field,
        source=source,
        operation=operation,
        value=safe_value,
        visibility="value"
        if safe_value is not None
        else "absent"
        if value is None and not variables
        else "redacted",
        environment=_environment_references(variables, supplied),
        default_status="explicit"
        if operation != "default"
        else "neutral"
        if field in NEUTRAL_DEFAULT_FIELDS
        else "review",
    )


def _validate_runtime_readers(
    raw: dict[str, object], diagnostics: list[ProfileDiagnostic]
) -> DeploymentConfig | None:
    from sapphire_flow.config.deployment import parse_deployment_config
    from sapphire_flow.config.deployment_identity import (
        parse_deployment_identity_config,
    )
    from sapphire_flow.config.forecast_qc_rules import parse_forecast_qc_rules
    from sapphire_flow.config.nepal_weather import parse_weather_scope
    from sapphire_flow.config.onboarding import parse_onboarding_config
    from sapphire_flow.config.qc_rules import parse_qc_rules
    from sapphire_flow.config.recap_gateway import parse_recap_gateway_config
    from sapphire_flow.config.river_stations import parse_river_station_config
    from sapphire_flow.exceptions import ConfigurationError

    invalid = (
        ConfigurationError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        OverflowError,
    )
    config: DeploymentConfig | None = None
    try:
        config = parse_deployment_config(raw)
    except ValidationError as exc:
        missing_only = all(
            item["type"] == "missing" for item in exc.errors(include_input=False)
        )
        diagnostics.append(
            _diagnostic(
                "INPUT_REQUIRED" if missing_only else "CONFIG_READER_INVALID",
                "deployment_config",
                "Deployment settings are missing required fields."
                if missing_only
                else "Deployment settings failed their existing validator.",
                "Complete or correct the deployment fields using the config reference.",
            )
        )
    except invalid:
        diagnostics.append(
            _diagnostic(
                "CONFIG_READER_INVALID",
                "deployment_config",
                "Deployment settings failed their existing validator.",
                "Correct the deployment fields using the config reference.",
            )
        )
    readers = (
        ("onboarding", parse_onboarding_config),
        ("deployment", parse_deployment_identity_config),
        ("qc_rules", parse_qc_rules),
        ("forecast_qc_rules", parse_forecast_qc_rules),
        ("adapters.river_stations", parse_river_station_config),
    )
    for field, parser in readers:
        try:
            parser(raw)
        except invalid:
            diagnostics.append(
                _diagnostic(
                    "INPUT_REQUIRED"
                    if field == "deployment" and "deployment" not in raw
                    else "CONFIG_READER_INVALID",
                    field,
                    "This section is missing or failed its existing reader.",
                    "Correct the named section using its maintained reference.",
                )
            )
    if _lookup(raw, "adapters.recap_gateway") is not None or (
        _lookup(raw, "adapters.weather_forecast.enabled") is True
        and _lookup(raw, "adapters.weather_forecast.type") == "recap_gateway"
    ):
        try:
            parse_recap_gateway_config(raw)
        except invalid:
            diagnostics.append(
                _diagnostic(
                    "CONFIG_READER_INVALID",
                    "adapters.recap_gateway",
                    "Gateway settings failed their reader.",
                    "Supply the required non-secret Gateway configuration.",
                )
            )
    scopes = _lookup(raw, "weather_history.scopes")
    if scopes is not None:
        try:
            if not isinstance(scopes, dict):
                raise ValueError("Scopes must be a table")
            for name in cast("dict[str, object]", scopes):
                parse_weather_scope(raw, name)
        except invalid:
            diagnostics.append(
                _diagnostic(
                    "CONFIG_READER_INVALID",
                    "weather_history.scopes",
                    "A named weather scope is invalid.",
                    "Check the weather-history scope definitions privately.",
                )
            )
    source = _lookup(raw, "adapters.river_stations.type")
    if isinstance(source, str) and source not in {"hydro_scraper", "dhm"}:
        diagnostics.append(
            _diagnostic(
                "DEVELOPER_WORK_REQUIRED",
                "adapters.river_stations.type",
                "The selected river-source adapter is not supported by the "
                "existing reader.",
                "Ask deployment support whether to select an existing adapter or "
                "implement the agency integration; do not substitute another "
                "agency's source.",
            )
        )
    return config


def _check_forecast_policy(
    raw: dict[str, object],
    environment: Mapping[str, str],
    diagnostics: list[ProfileDiagnostic],
    pending: list[ProfileDiagnostic],
) -> WeatherForecastAdapterConfig | None:
    from sapphire_flow.config.weather_forecast import (
        parse_require_nwp,
        parse_weather_forecast_adapter_config,
    )
    from sapphire_flow.exceptions import ConfigurationError

    try:
        forecast = parse_weather_forecast_adapter_config(
            raw, require_nwp=parse_require_nwp(environment.get("SAPPHIRE_REQUIRE_NWP"))
        )
        if forecast.require_nwp and not forecast.enabled:
            raise ConfigurationError("NWP is required but disabled")
    except (ConfigurationError, ValueError, TypeError, KeyError, OverflowError):
        diagnostics.append(
            _diagnostic(
                "CONFIG_READER_INVALID",
                "adapters.weather_forecast",
                "Forecast settings failed their reader or NWP requirement.",
                "Correct the forecast adapter and explicit SAPPHIRE_REQUIRE_NWP input.",
            )
        )
        return None
    if forecast.enabled:
        pending.append(
            _diagnostic(
                "ADAPTER_POLICY_PENDING",
                "adapters.weather_forecast",
                "Enabled forecast-provider execution is outside the supported path.",
                "Obtain coverage for the selected provider before offline approval.",
            )
        )
    return forecast


def _inactive_preview_fields(
    config: DeploymentConfig, forecast_disabled: bool
) -> set[str]:
    inactive: set[str] = set()
    if not config.daily_grid_origin_overrides:
        inactive.add("daily_grid_origin_overrides")
    if forecast_disabled:
        inactive.add("nwp_cycle_min_age_minutes")
    if config.operational_weather.route == "stored_reanalysis":
        inactive.add("operational_weather.automatic_issue_timezone")
    if not config.enable_forecast_alerts and not config.enable_observation_alerts:
        inactive.add("threshold_check_mode")
    return inactive


def _unknown_model_keys(raw: object, parsed: object) -> bool:
    # Compare the actual reader's model fields, never a copied schema. Values and
    # arbitrary input keys are not returned in diagnostics/provenance.
    names: set[str] | None = None
    if isinstance(parsed, BaseModel):
        names = set(type(parsed).model_fields)
    elif is_dataclass(parsed) and not isinstance(parsed, type):
        names = {field.name for field in fields(parsed)}
    if names is not None and isinstance(raw, dict):
        table = cast("dict[str, object]", raw)
        if table.keys() - names:
            return True
        return any(
            _unknown_model_keys(value, getattr(parsed, name))
            for name, value in table.items()
        )
    if isinstance(raw, dict) and isinstance(parsed, dict):
        parsed_map = cast("dict[object, object]", parsed)
        return any(
            _unknown_model_keys(value, parsed_map.get(key))
            for key, value in cast("dict[str, object]", raw).items()
        )
    if isinstance(raw, list) and isinstance(parsed, (list, tuple)):
        return any(
            _unknown_model_keys(value, model)
            for value, model in zip(
                cast("list[object]", raw),
                cast("list[object] | tuple[object, ...]", parsed),
                strict=False,
            )
        )
    return False


def _check_configuration_coverage(
    raw: dict[str, object],
    config: DeploymentConfig,
    pending: list[ProfileDiagnostic],
) -> None:
    from sapphire_flow.types.enums import ProvisionalIfsTailMode

    auxiliary = {
        "adapters",
        "onboarding",
        "qc_rules",
        "forecast_qc_rules",
        "deployment",
        "tenants",
        "paths",
    }
    derived = {
        "paths_data_dir",
        "nwp_grid_archive_base_path",
        "bafu_forecast_archive_path",
        "bafu_observation_archive_path",
    }
    model_input = {key: value for key, value in raw.items() if key not in auxiliary}
    unknown = bool(set(model_input) & derived) or _unknown_model_keys(
        model_input, config
    )
    # These non-model readers accept more legacy keys than this initial path
    # covers. Unknown keys and selected references remain pending, even if inert.
    supported_sections = {
        "deployment": {"global_admin", "writable_tenants", "operator"},
        "onboarding": {"tenant", "data_source", "basin_ids"},
        "paths": {"data_dir"},
        "adapters": {
            "river_stations",
            "weather_forecast",
            "weather_reanalysis",
            "weather_stations",
        },
        "adapters.river_stations": {"type", "endpoint"},
        "adapters.weather_reanalysis": {"type"},
        "adapters.weather_stations": {"type"},
        "adapters.weather_forecast": {
            "enabled",
            "type",
            "stac_base_url",
            "stac_collection",
            "scratch_path",
            "max_files",
            "grid_extractor",
            "archive_base_path",
            "disk_guard_scratch_soft_gb",
            "disk_guard_scratch_hard_gb",
            "disk_guard_archive_soft_gb",
            "disk_guard_archive_hard_gb",
            "monitoring",
        },
        "adapters.weather_forecast.monitoring": {"expected_delivery_offset_hours"},
        "qc_rules": {"version", "rules"},
        "forecast_qc_rules": {"version", "rules"},
    }
    for field, allowed in supported_sections.items():
        value = _lookup(raw, field)
        if value is not None and (
            not isinstance(value, dict)
            or set(cast("dict[str, object]", value)) - allowed
        ):
            unknown = True
    if unknown:
        pending.append(
            _diagnostic(
                "UNCHECKED_SECTIONS",
                "configuration",
                "Additional or unknown configuration keys lack consumer coverage.",
                "Have support cover declared extensions; retain required inputs.",
            )
        )
    selected_extensions = (
        config.swiss_overview is not None
        or config.swiss_observation_history
        or bool(config.network_overview.freshness_policies)
        or bool(config.provisional_ratings)
        or config.provisional_ifs_tail_mode is not ProvisionalIfsTailMode.DISABLED
        or config.evaluation_forecasts is not None
        or config.daily_evaluation is not None
        or config.bafu_forecast_archive_path is not None
        or config.bafu_observation_archive_path is not None
        or _lookup(raw, "adapters.river_stations.type") == "dhm"
    )
    if selected_extensions:
        pending.append(
            _diagnostic(
                "CONFIG_EXTENSION_PENDING",
                "inputs",
                "A selected integration or reference lacks offline coverage.",
                "Validate the selected integration and its local references.",
            )
        )
    if config.operational_weather.route != "stored_reanalysis":
        pending.append(
            _diagnostic(
                "ADAPTER_POLICY_PENDING",
                "operational_weather.route",
                "The selected weather route is outside stored-data coverage.",
                "Obtain provider-route coverage before offline approval.",
            )
        )
    if _unchecked_rule_keys(raw):
        pending.append(
            _diagnostic(
                "UNCHECKED_SECTIONS",
                "qc_rules",
                "QC rule fields or threshold shapes lack consumer coverage.",
                "Check rule fields against the existing QC consumer; "
                "scientific calibration remains unverified.",
            )
        )


def _unchecked_rule_keys(raw: dict[str, object]) -> bool:
    from sapphire_flow.types.domain import ForecastQcRuleParams, QcRuleParams

    # Key coverage only, from services/qc.py and services/forecast_qc.py.
    # Threshold magnitudes/calibration are not certified by an offline check.
    thresholds: dict[str, set[str]] = {
        "range_check": {"value_min", "value_max"},
        "rate_of_change": {"max_rate"},
        "frozen_sensor": {"tolerance", "min_consecutive", "exclude_at_or_below"},
        "spike": {"max_delta", "tolerance"},
        "gross_outlier": {"k_sigma"},
        "negative_value": {"value_min"},
        "flat_ensemble": {"tolerance"},
        "ensemble_spread": {"min_spread_ratio", "max_spread_ratio"},
        "climatology_outlier": {"k_sigma"},
        "temporal_consistency": {"max_rate"},
        "quantile_crossing": set(),
    }
    required = {
        "rate_of_change": {"max_rate"},
        "frozen_sensor": {"tolerance", "min_consecutive"},
        "gross_outlier": {"k_sigma"},
        "negative_value": {"value_min"},
        "flat_ensemble": {"tolerance"},
        "ensemble_spread": {"min_spread_ratio", "max_spread_ratio"},
        "climatology_outlier": {"k_sigma"},
        "temporal_consistency": {"max_rate"},
    }
    for section, rule_type in (
        ("qc_rules", QcRuleParams),
        ("forecast_qc_rules", ForecastQcRuleParams),
    ):
        rules = _lookup(raw, f"{section}.rules")
        if rules is None:
            continue
        if not isinstance(rules, list):
            return True
        allowed = {item.name for item in fields(rule_type)} - {"time_step"} | {
            "time_step_seconds"
        }
        for item in cast("list[object]", rules):
            if not isinstance(item, dict):
                return True
            rule = cast("dict[str, object]", item)
            name = rule.get("rule_id")
            values = rule.get("thresholds")
            if (
                set(rule) - allowed
                or not isinstance(name, str)
                or not isinstance(values, dict)
            ):
                return True
            keys = set(cast("dict[str, object]", values))
            if (
                name not in thresholds
                or keys - thresholds[name]
                or required.get(name, set()) - keys
            ):
                return True
            if name == "spike" and not keys & {"tolerance", "max_delta"}:
                return True
            if any(
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                for value in cast("dict[str, object]", values).values()
            ):
                return True
    return False


def evaluate_deployment_profile(
    selection: ProfileSelection, *, environment: Mapping[str, str] | None = None
) -> ProfileEvaluation:
    """Pure offline config evidence; never a Compose or deployment readiness check."""
    from sapphire_flow.config._overlay import TomlProvenance, load_merged_toml
    from sapphire_flow.config.declared_tenants import load_declared_tenants
    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.config.deployment_identity import (
        parse_deployment_identity_config,
    )
    from sapphire_flow.config.onboarding import parse_onboarding_config
    from sapphire_flow.exceptions import ConfigurationError
    from sapphire_flow.types.tenant import DEFAULT_TENANT_CODE

    profile = selection.profile
    supplied_environment: Mapping[str, str] = {} if environment is None else environment
    supplied_names = set(supplied_environment)
    diagnostics: list[ProfileDiagnostic] = []
    provenance: list[EffectiveSetting] = []
    pending = [
        _diagnostic(
            "COMPOSE_EVIDENCE_PENDING",
            "compose",
            "Service wiring has not been checked.",
            "Run the later service-wiring checker with explicit installation inputs.",
        )
    ]
    pending.append(
        _diagnostic(
            "EXECUTION_COVERAGE_PENDING",
            "execution",
            "Complete service execution and selected-job applicability are unchecked.",
            "Validate the supported topology and job subset before offline approval.",
        )
    )
    if selection.installation is None:
        pending.append(
            _diagnostic(
                "INSTALLATION_REQUIRED",
                "installation",
                "No desired installation was supplied.",
                "Supply a private installation file.",
            )
        )
    if selection.missing_secret_bindings:
        pending.append(
            _diagnostic(
                "SECRET_BINDINGS_PENDING",
                "secret_bindings",
                "Required runtime file bindings are missing.",
                "Supply the required logical file references without their contents.",
            )
        )
    raw_tenants: tuple[str, ...] | None = None
    tenant_origins: dict[str, int] = {}
    try:
        tenants = load_declared_tenants(
            profile.base_config, list(profile.overlays), name_origins=tenant_origins
        )
        raw_tenants = tuple(tenant.code for tenant in tenants)
        for tenant in tenants:
            provenance.append(
                EffectiveSetting(
                    field="tenants.name",
                    source=f"{_document_alias(tenant_origins[tenant.code])}.raw_tenants",
                    operation="set",
                    value=None,
                    visibility="redacted",
                )
            )
    except (ConfigurationError, OSError, UnicodeError, ValueError, TypeError):
        diagnostics.append(
            _diagnostic(
                "RAW_TENANTS_INVALID",
                "tenants",
                "Raw tenant declarations failed their reader.",
                "Correct raw tenant declarations; no interpolation is applied.",
                "raw_tenants",
            )
        )

    trace = TomlProvenance()
    raw: dict[str, object] | None = None
    try:
        raw = load_merged_toml(
            profile.base_config,
            list(profile.overlays),
            environment=supplied_environment,
            provenance=trace,
        )
    except (OSError, UnicodeError, ValueError, TypeError):
        diagnostics.append(
            _diagnostic(
                "CONFIG_LOAD_INVALID",
                "configuration",
                "The selected TOML chain could not be parsed or merged.",
                "Check files, TOML, permitted overlays and placeholder inputs.",
            )
        )
    for document, variables in trace.variables.items():
        if variables:
            provenance.append(
                EffectiveSetting(
                    field="interpolation",
                    source=_document_alias(document),
                    operation="set",
                    value=None,
                    visibility="redacted",
                    environment=_environment_references(variables, supplied_names),
                )
            )

    if profile.state is ProfileState.INCOMPLETE and not profile.required_inputs:
        diagnostics.append(
            _diagnostic(
                "INPUT_REQUIRED",
                "agency_inputs",
                "The profile is still declared incomplete.",
                "Record and supply the remaining agency requirements before "
                "marking it configured.",
                "profile",
            )
        )
    for requirement in profile.required_inputs:
        owner, description = REQUIRED_INPUTS.get(
            requirement, ("administrator", "additional deployment requirements")
        )
        diagnostics.append(
            ProfileDiagnostic(
                code="INPUT_REQUIRED",
                field=requirement
                if requirement in REQUIRED_INPUTS
                else "agency_inputs",
                source="profile",
                cause=f"Supply {description}.",
                next_action=(
                    "Record the actual agency decision or file reference in the "
                    "private configuration; remove the requirement only after "
                    "supplying it."
                ),
                owner=owner,
            )
        )
    if raw is not None:
        timezone_present = "default_display_timezone" in raw
        timezone_value = raw.get("default_display_timezone")
        timezone_valid = (
            isinstance(timezone_value, str)
            and _safe_setting_value("default_display_timezone", timezone_value)
            is not None
        )
        if not timezone_present or not timezone_valid:
            diagnostics.append(
                _diagnostic(
                    "DISPLAY_TIMEZONE_INVALID"
                    if timezone_present
                    else "INPUT_REQUIRED",
                    "default_display_timezone",
                    "The supplied display timezone is not a valid named timezone."
                    if timezone_present
                    else "Choose the deployment's display timezone.",
                    "Set default_display_timezone to a valid named timezone, normally "
                    "the local operational zone; explicit UTC is also valid.",
                )
            )
        for field in _EXPLICIT_AGENCY_FIELDS:
            if _lookup(raw, field) is None or _lookup(raw, field) == "":
                diagnostics.append(
                    _diagnostic(
                        "INPUT_REQUIRED",
                        field,
                        "Choose the deployment's data retention period."
                        if field == "max_retention_days"
                        else "A new agency must explicitly choose this setting.",
                        "Set max_retention_days in the application base using the "
                        "retention constraints in the config reference."
                        if field == "max_retention_days"
                        else "Supply the deployment choice; do not inherit another "
                        "deployment's scientific settings.",
                    )
                )
        if _lookup(raw, "adapters.weather_forecast.enabled") is True and not _lookup(
            raw, "adapters.weather_forecast.type"
        ):
            diagnostics.append(
                _diagnostic(
                    "INPUT_REQUIRED",
                    "adapters.weather_forecast.type",
                    "An enabled forecast source needs an explicit choice.",
                    "Select a supported source with the agency's data owner.",
                )
            )
        if (
            _lookup(raw, "adapters.weather_forecast.enabled") is True
            and _lookup(raw, "adapters.weather_forecast.type") == "meteoswiss_nwp"
            and _lookup(raw, "nwp_cycle_min_age_minutes") is None
        ):
            diagnostics.append(
                _diagnostic(
                    "INPUT_REQUIRED",
                    "nwp_cycle_min_age_minutes",
                    "The enabled forecast provider requires an explicit "
                    "publication-delay choice.",
                    "Supply the provider's publication delay; the historical "
                    "typed default is not a neutral deployment choice.",
                )
            )
        config = _validate_runtime_readers(raw, diagnostics)
        forecast = _check_forecast_policy(
            raw, supplied_environment, diagnostics, pending
        )
        forecast_disabled = forecast is not None and not forecast.enabled
        if config is not None:
            _check_configuration_coverage(raw, config, pending)
        if config is not None:
            parsed = config.model_dump(mode="json")
            inactive = _inactive_preview_fields(config, forecast_disabled)
            for field in _PREVIEW_FIELDS:
                if field == "default_display_timezone" and not timezone_valid:
                    continue
                setting = _setting(field, _lookup(parsed, field), trace, supplied_names)
                if setting.default_status == "review" and field in inactive:
                    setting = replace(setting, default_status="inactive")
                provenance.append(setting)
            if config.swiss_overview is not None:
                pending.append(
                    _diagnostic(
                        "ROSTER_PENDING",
                        "swiss_overview",
                        "The packaged roster was not read.",
                        "Validate the selected roster digest/cardinality separately.",
                    )
                )
        else:
            # Only omitted neutral literal defaults are safe to preview when
            # the complete model cannot parse. Never validate fabricated inputs.
            provenance.extend(
                _setting(
                    field,
                    DeploymentConfig.model_fields[field].default,
                    trace,
                    supplied_names,
                )
                for field in _PREVIEW_FIELDS
                if field in NEUTRAL_DEFAULT_FIELDS and field not in raw
            )
        if not timezone_present and selection.profile.state == ProfileState.INCOMPLETE:
            provenance.append(
                replace(
                    _setting(
                        "default_display_timezone",
                        DeploymentConfig.model_fields[
                            "default_display_timezone"
                        ].default,
                        trace,
                        supplied_names,
                    ),
                    default_status="fallback",
                )
            )
        try:
            identity = parse_deployment_identity_config(raw)
            if raw_tenants is not None and identity.writable_tenants - {
                *raw_tenants,
                DEFAULT_TENANT_CODE,
            }:
                diagnostics.append(
                    _diagnostic(
                        "WRITE_TENANT_UNDECLARED",
                        "deployment.writable_tenants",
                        "A writable tenant is not declared by the raw tenant reader.",
                        "Declare the tenant explicitly or correct write authority.",
                    )
                )
            provenance.append(
                _setting(
                    "deployment.writable_tenants",
                    identity.writable_tenants,
                    trace,
                    supplied_names,
                    "DeploymentIdentityConfig",
                )
            )
            provenance.append(
                _setting(
                    "deployment.global_admin",
                    identity.global_admin,
                    trace,
                    supplied_names,
                    "DeploymentIdentityConfig",
                )
            )
            onboarding = parse_onboarding_config(raw)
            if onboarding is not None:
                if (
                    not identity.global_admin
                    and onboarding.tenant_code not in identity.writable_tenants
                ):
                    diagnostics.append(
                        _diagnostic(
                            "ONBOARDING_TENANT_OUTSIDE_AUTHORITY",
                            "onboarding.tenant",
                            "Onboarding tenant is outside write authority.",
                            "Align onboarding with declared writable tenants.",
                        )
                    )
                provenance.append(
                    _setting(
                        "onboarding.data_source",
                        onboarding.data_source,
                        trace,
                        supplied_names,
                        "OnboardingConfig",
                    )
                )
                provenance.append(
                    _setting(
                        "onboarding.basin_ids",
                        onboarding.basin_ids,
                        trace,
                        supplied_names,
                        "OnboardingConfig",
                    )
                )
        except (
            ConfigurationError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OverflowError,
        ):
            pass  # The existing readers already emitted sanitized diagnostics above.
        for section, reader in (
            ("qc_rules", "parse_qc_rules"),
            ("forecast_qc_rules", "parse_forecast_qc_rules"),
        ):
            provenance.append(
                _setting(
                    section,
                    _lookup(raw, section) if section in raw else "builtin_default",
                    trace,
                    supplied_names,
                    reader,
                )
            )
        if "adapters" in raw:
            for field in (
                "adapters.river_stations.type",
                "adapters.weather_forecast.enabled",
                "adapters.weather_forecast.type",
                "adapters.weather_reanalysis.type",
                "adapters.weather_stations.type",
            ):
                value = _lookup(raw, field)
                reader = "unchecked_adapter_default"
                if forecast is not None and field == "adapters.weather_forecast.type":
                    value = forecast.type
                    reader = "WeatherForecastAdapterConfig"
                setting = _setting(field, value, trace, supplied_names, reader)
                if (
                    forecast_disabled
                    and field == "adapters.weather_forecast.type"
                    and setting.operation == "default"
                ):
                    setting = replace(setting, default_status="inactive")
                provenance.append(setting)
        if profile.inputs or profile.scopes:
            pending.append(
                _diagnostic(
                    "SCOPED_INPUTS_PENDING",
                    "inputs",
                    "Referenced inputs and exceptional scopes remain unchecked.",
                    "Validate applicable references and service wiring.",
                )
            )
    if any(item.default_status == "review" for item in provenance):
        pending.append(
            _diagnostic(
                "DEFAULT_REVIEW_PENDING",
                "defaults",
                "Inherited non-neutral defaults require deployment review.",
                "Deployment support must audit applicable scientific/operational "
                "defaults and supply explicit choices; displayed inherited "
                "values are not endorsed.",
            )
        )
    pending.append(
        _diagnostic(
            "BAFU_REGISTRY_PENDING",
            "bafu_units",
            "Worker-selected external registry applicability is not established.",
            "Establish applicability from the explicit worker environment.",
        )
    )
    return ProfileEvaluation(
        summary=EvaluationSummary.FAIL if diagnostics else EvaluationSummary.PENDING,
        diagnostics=tuple(diagnostics),
        provenance=tuple(provenance),
        pending_checks=tuple(pending),
        unverified_runtime=(
            "actual_host_configuration",
            "credential_availability",
            "registered_jobs_and_pools",
            "registered_job_overrides",
            "station_model_database_state",
            "scientific_readiness",
            "actual_image_identity",
            "provider_access",
            "build_credentials",
        ),
    )
