from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import pytest
import sqlalchemy as sa

from tests.integration.services.nepal_readiness_isolation_fixture import (
    QualificationScenario,
    run_qualification_case,
)
from tests.integration.services.skill_persistence_fixture import (
    skill_persistence_engine as skill_persistence_engine,
)

if TYPE_CHECKING:
    from tests.fakes.fake_models import FakeStationForecastModel


@runtime_checkable
class _NodeRequest(Protocol):
    @property
    def node(self) -> object: ...


def _request_nodeid(request: object) -> str:
    if not isinstance(request, _NodeRequest):
        raise TypeError("fixture request lacks public node access")
    node = request.node
    if not isinstance(node, pytest.Item):
        raise TypeError("function fixture request did not provide a pytest Item")
    return node.nodeid


def _owned_record_exists() -> bool | None:
    configured = os.environ.get("SKILL_PERSISTENCE_EVIDENCE_DIR")
    if configured is None or configured == "":
        return None
    return (Path(configured) / "owned-resources.json").exists()


@pytest.fixture
def qualification_engine(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
) -> sa.Engine:
    directory = tmp_path_factory.mktemp("engine-setup-observation")
    receipt_path = directory / "engine-setup.json"
    before: dict[str, str] | None = None
    after: dict[str, str] | None = None
    owned_before: bool | None = None
    owned_after: bool | None = None
    value: object = None
    acquired = False
    primary: BaseException | None = None
    receipt: dict[str, object] = {
        "nodeid": _request_nodeid(request),
        "acquisition_returned": False,
        "engine_type_valid": False,
        "before_snapshot_observed": False,
        "after_snapshot_observed": False,
        "environment_equal": None,
        "added_names": [],
        "removed_names": [],
        "changed_names": [],
        "database_url_present_before": None,
        "database_url_present_after": None,
        "database_url_equal": None,
        "owned_record_exists_before": None,
        "owned_record_exists_after": None,
        "primary_error_class": None,
        "observation_error_classes": [],
    }
    try:
        owned_before = _owned_record_exists()
        before = dict(os.environ)
        value = request.getfixturevalue("skill_persistence_engine")
        acquired = True
    except BaseException as error:
        primary = error
        raise
    finally:
        errors: list[BaseException] = []
        try:
            after = dict(os.environ)
        except BaseException as error:
            errors.append(error)
        try:
            owned_after = _owned_record_exists()
        except BaseException as error:
            errors.append(error)
        try:
            receipt.update(
                {
                    "acquisition_returned": acquired,
                    "engine_type_valid": isinstance(value, sa.Engine),
                    "before_snapshot_observed": before is not None,
                    "after_snapshot_observed": after is not None,
                    "owned_record_exists_before": owned_before,
                    "owned_record_exists_after": owned_after,
                    "primary_error_class": (
                        None if primary is None else type(primary).__name__
                    ),
                    "database_url_present_before": (
                        None if before is None else "DATABASE_URL" in before
                    ),
                    "database_url_present_after": (
                        None if after is None else "DATABASE_URL" in after
                    ),
                }
            )
            if before is not None and after is not None:
                receipt["added_names"] = sorted(after.keys() - before.keys())
                receipt["removed_names"] = sorted(before.keys() - after.keys())
                receipt["changed_names"] = sorted(
                    name
                    for name in before.keys() & after.keys()
                    if before[name] != after[name]
                )
                equal = before == after
                database_equal = ("DATABASE_URL" in before) == (
                    "DATABASE_URL" in after
                ) and before.get("DATABASE_URL") == after.get("DATABASE_URL")
                receipt["environment_equal"] = equal
                receipt["database_url_equal"] = database_equal
                if not equal or not database_equal:
                    errors.append(AssertionError("engine setup changed environment"))
        except BaseException as error:
            errors.append(error)
        try:
            receipt["observation_error_classes"] = [
                type(error).__name__ for error in errors
            ]
            content = json.dumps(receipt, indent=2, allow_nan=False) + "\n"
            with receipt_path.open("x") as output:
                output.write(content)
        except BaseException as error:
            errors.append(error)
        if errors:
            retained = primary if primary is not None else errors[0]
            secondary = errors if primary is not None else errors[1:]
            for error in secondary:
                retained.add_note(
                    "engine setup observation secondary: " + type(error).__name__
                )
            if primary is None:
                raise retained
    assert isinstance(value, sa.Engine)
    return value


class TestNepalReadinessInputIsolation:
    def test_clean_and_protected_histories_have_identical_qualification(
        self,
        qualification_engine: sa.Engine,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SAPPHIRE_DATA_DIR", str(tmp_path))
        qualification_models: list[FakeStationForecastModel] = []
        clean = run_qualification_case(
            qualification_engine,
            QualificationScenario.CLEAN,
            qualification_models,
            evidence_dir=tmp_path,
        )
        mixed = run_qualification_case(
            qualification_engine,
            QualificationScenario.MIXED,
            qualification_models,
            evidence_dir=tmp_path,
        )
        assert len(qualification_models) == 2
        assert qualification_models[0] is not qualification_models[1]
        assert mixed == clean

    def test_short_ordinary_history_is_not_repaired_by_protected_history(
        self,
        qualification_engine: sa.Engine,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SAPPHIRE_DATA_DIR", str(tmp_path))
        qualification_models: list[FakeStationForecastModel] = []
        run_qualification_case(
            qualification_engine,
            QualificationScenario.SHORT,
            qualification_models,
            evidence_dir=tmp_path,
        )
        assert len(qualification_models) == 1

    def test_protected_only_station_cannot_qualify(
        self,
        qualification_engine: sa.Engine,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SAPPHIRE_DATA_DIR", str(tmp_path))
        qualification_models: list[FakeStationForecastModel] = []
        run_qualification_case(
            qualification_engine,
            QualificationScenario.PROTECTED_ONLY,
            qualification_models,
            evidence_dir=tmp_path,
        )
        assert len(qualification_models) == 1
