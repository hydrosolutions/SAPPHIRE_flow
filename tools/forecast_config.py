"""Generate and verify forecast configuration without applying it."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from enum import StrEnum
from pathlib import Path
from typing import cast

from sapphire_flow.config.river_stations import (
    FORECAST_PROVIDER_PATH,
    ForecastConfigOverlay,
)

PROVIDER_TARGET = str(FORECAST_PROVIDER_PATH)
REGISTRATION_KEY = "SAPPHIRE_FORECAST_CONFIG_OVERLAY"


class ForecastConfigStage(StrEnum):
    DISABLED = "disabled"
    ENABLED = "enabled"


def forecast_overlay_value(
    selection: ForecastConfigOverlay, stage: ForecastConfigStage
) -> str:
    return (
        "" if stage is ForecastConfigStage.DISABLED else selection.environment_value()
    )


def selection_from_worker_chain(raw: str) -> ForecastConfigOverlay:
    if not raw:
        raise ValueError("approved worker overlay chain is empty")
    selection = ForecastConfigOverlay.parse(raw + "," + PROVIDER_TARGET)
    assert selection is not None
    return selection


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) for key in cast("dict[object, object]", value)
    ):
        raise ValueError("configuration metadata must contain string-keyed mappings")
    return cast("dict[str, object]", value)


def _service(document: dict[str, object], name: str) -> dict[str, object]:
    return _mapping(_mapping(document.get("services")).get(name))


def _environment(service: dict[str, object]) -> dict[str, object]:
    return _mapping(service.get("environment"))


def _volumes(service: dict[str, object]) -> list[dict[str, object]]:
    value = service.get("volumes")
    if not isinstance(value, list):
        raise ValueError("rendered service volumes must be a list")
    return [_mapping(item) for item in cast("list[object]", value)]


def verify_forecast_configuration(raw: object) -> dict[str, object]:
    metadata = _mapping(raw)
    baseline = _mapping(metadata.get("baseline"))
    candidate = copy.deepcopy(_mapping(metadata.get("candidate")))
    pool = _mapping(metadata.get("pool"))
    fields = (
        "forecast_overlay_in_defaults",
        "forecast_overlay_in_template",
        "template_ambiguous",
    )
    if set(pool) != {"pool_name", *fields}:
        raise ValueError("pool metadata must contain only the approved fields")
    if pool["pool_name"] != "default" or any(
        type(pool[key]) is not bool for key in fields
    ):
        raise ValueError("invalid forecast pool metadata")
    if any(pool[key] for key in fields):
        raise ValueError("forecast selection is shadowed by pool configuration")
    for document in (baseline, candidate):
        services = _mapping(document.get("services"))
        for name, raw_service in services.items():
            if name != "init" and REGISTRATION_KEY in _mapping(
                _mapping(raw_service).get("environment", {})
            ):
                raise ValueError(
                    "forecast selection must not be set on service environments"
                )
    raw_stage = metadata.get("stage")
    if not isinstance(raw_stage, str) or raw_stage not in ("disabled", "enabled"):
        raise ValueError("unknown forecast configuration stage")
    stage = ForecastConfigStage(raw_stage)
    source = metadata.get("provider_source")
    ingest_target = metadata.get("ingest_provider_target")
    if (
        not isinstance(source, str)
        or not Path(source).is_absolute()
        or not isinstance(ingest_target, str)
        or not Path(ingest_target).is_absolute()
    ):
        raise ValueError("provider source and ingest target must be absolute paths")
    before_worker = _service(baseline, "prefect-worker")
    worker = _service(candidate, "prefect-worker")
    base_value = _environment(before_worker).get("SAPPHIRE_CONFIG_OVERLAY")
    if not isinstance(base_value, str):
        raise ValueError("approved worker overlay chain is missing")
    selection = selection_from_worker_chain(base_value)
    if _environment(worker).get("SAPPHIRE_CONFIG_OVERLAY") != base_value:
        raise ValueError("existing worker overlay authority changed")
    before_init = _environment(_service(baseline, "init"))
    init = _environment(_service(candidate, "init"))
    if REGISTRATION_KEY in before_init:
        raise ValueError(
            "baseline already has a competing forecast registration selection"
        )
    if init.pop(REGISTRATION_KEY, None) != forecast_overlay_value(selection, stage):
        raise ValueError(
            "forecast registration selection does not match the worker chain"
        )
    mounts = _volumes(worker)
    provider_mounts = [
        mount for mount in mounts if mount.get("target") == PROVIDER_TARGET
    ]
    if len(provider_mounts) != 1:
        raise ValueError("forecast provider mount must exist exactly once")
    mount = provider_mounts[0]
    if (
        mount.get("type") != "bind"
        or mount.get("source") != source
        or mount.get("read_only") is not True
        or _mapping(mount.get("bind")).get("create_host_path", False) is not False
    ):
        raise ValueError("forecast provider must be an existing read-only file bind")
    ingest_mounts = [
        mount
        for mount in _volumes(_service(baseline, "prefect-worker-ingest"))
        if mount.get("target") == ingest_target
    ]
    if (
        len(ingest_mounts) != 1
        or ingest_mounts[0].get("source") != source
        or ingest_mounts[0].get("type") != "bind"
        or ingest_mounts[0].get("read_only") is not True
    ):
        raise ValueError("forecast and ingest must share the approved read-only source")
    remaining_mounts = [item for item in mounts if item is not mount]
    if remaining_mounts or "volumes" in before_worker:
        worker["volumes"] = remaining_mounts
    else:
        worker.pop("volumes", None)
    if candidate != baseline:
        raise ValueError("unrelated rendered configuration changed")
    return {
        "configuration_matches": True,
        "stage": stage,
        "provider_target": PROVIDER_TARGET,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate")
    generate.add_argument("--base-overlays", required=True)
    generate.add_argument("--stage", choices=("disabled", "enabled"), required=True)
    commands.add_parser("verify")
    args = parser.parse_args()
    try:
        if args.command == "generate":
            selection = selection_from_worker_chain(args.base_overlays)
            result: object = {
                REGISTRATION_KEY: forecast_overlay_value(
                    selection, ForecastConfigStage(args.stage)
                )
            }
        else:
            result = verify_forecast_configuration(json.load(sys.stdin))
    except (ValueError, TypeError) as exc:
        parser.exit(1, f"forecast configuration: {exc}\n")
    sys.stdout.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
