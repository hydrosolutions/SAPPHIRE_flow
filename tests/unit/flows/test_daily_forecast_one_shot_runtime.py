import builtins
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from sapphire_flow.flows import daily_forecast_one_shot_runtime as runtime


class OwnedContainer:
    def __init__(self, identity: str = "owned") -> None:
        self.identity = identity
        self.stops = 0

    def start(self) -> None:
        pass

    def get_wrapped_container(self) -> SimpleNamespace:
        return SimpleNamespace(id=self.identity)

    def get_connection_url(self) -> str:
        return "sqlite://"

    def stop(self) -> None:
        self.stops += 1


@pytest.fixture
def owned(monkeypatch: pytest.MonkeyPatch) -> OwnedContainer:
    container = OwnedContainer()
    monkeypatch.setattr(runtime, "_new_container", lambda: container)
    monkeypatch.setattr(runtime, "_migrate", lambda engine: None)
    engine = sa.create_engine("sqlite://")
    monkeypatch.setattr(runtime.sa, "create_engine", lambda *args, **kwargs: engine)
    return container


@pytest.mark.parametrize(
    "argument",
    ["dsn", "database_url", "accepted_writer", "rejected_writer", "ledger", "isolated"],
)
def test_public_factory_rejects_existing_targets_and_mixed_writers(
    argument: str,
) -> None:
    with pytest.raises(TypeError, match="unexpected keyword"):
        runtime.create_local_one_shot_outputs(**{argument: object()})
    with pytest.raises(TypeError, match="unexpected keyword"):
        runtime.LocalOneShotOutputs(**{argument: object()})


def test_close_retains_owned_resource_until_explicit_cleanup(
    owned: OwnedContainer,
) -> None:
    outputs = runtime.create_local_one_shot_outputs()
    assert outputs.resource_id == "owned"
    outputs.close()
    assert outputs.state is runtime.LocalOutputState.RETAINED
    assert owned.stops == 0
    with pytest.raises(RuntimeError, match="closed"):
        _ = outputs.ledger
    with pytest.raises(TypeError, match="positional"):
        outputs.cleanup("unowned")
    outputs.cleanup()
    outputs.cleanup()
    assert owned.stops == 1
    assert outputs.state is runtime.LocalOutputState.DELETED


@pytest.mark.parametrize("failure_stage", ["start", "migration", "composition"])
def test_factory_failure_deletes_only_partial_owned_resource(
    monkeypatch: pytest.MonkeyPatch, owned: OwnedContainer, failure_stage: str
) -> None:
    other = OwnedContainer("unowned")

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected failure")

    if failure_stage == "start":
        monkeypatch.setattr(owned, "start", fail)
    elif failure_stage == "migration":
        monkeypatch.setattr(runtime, "_migrate", fail)
    else:
        monkeypatch.setattr(runtime, "PgCommittedForecastWriter", fail)
    with pytest.raises(RuntimeError, match="injected failure"):
        runtime.create_local_one_shot_outputs()
    assert owned.stops == 1
    assert other.stops == 0


def test_migration_uses_child_environment_not_configured_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "must-not-connect")
    engine = sa.create_engine("sqlite://")
    calls = []

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runtime.subprocess, "run", run)
    runtime._migrate(engine)
    assert calls[0][1]["env"]["DATABASE_URL"] == "sqlite://"
    assert runtime.os.environ["DATABASE_URL"] == "must-not-connect"


def test_missing_dev_tooling_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    original = builtins.__import__

    def without_dev(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("testcontainers"):
            raise ImportError("not installed")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_dev)
    with pytest.raises(RuntimeError, match="development testcontainers"):
        runtime.create_local_one_shot_outputs()


@pytest.mark.parametrize(
    "variable,value",
    [
        ("DOCKER_HOST", "tcp://remote:2375"),
        ("TESTCONTAINERS_HOST_OVERRIDE", "remote"),
    ],
)
def test_remote_provisioning_overrides_refused(
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    value: str,
) -> None:
    monkeypatch.setenv(variable, value)
    with pytest.raises(RuntimeError, match="forbid remote"):
        runtime.create_local_one_shot_outputs()


def test_cleanup_failure_retains_handle_for_retry(
    monkeypatch: pytest.MonkeyPatch,
    owned: OwnedContainer,
) -> None:
    outputs = runtime.create_local_one_shot_outputs()
    stop = owned.stop

    def fail() -> None:
        raise RuntimeError("cannot remove")

    monkeypatch.setattr(owned, "stop", fail)
    with pytest.raises(RuntimeError, match="cannot remove"):
        outputs.cleanup()
    assert outputs.state is runtime.LocalOutputState.RETAINED
    monkeypatch.setattr(owned, "stop", stop)
    outputs.cleanup()
    assert outputs.state is runtime.LocalOutputState.DELETED
