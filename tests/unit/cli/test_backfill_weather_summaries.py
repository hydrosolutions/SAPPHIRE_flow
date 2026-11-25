from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import UUID

import pytest

from sapphire_flow.cli import backfill_weather_summaries as cli
from sapphire_flow.exceptions import TenantIsolationError
from sapphire_flow.store.weather_summary_backfill import BackfillMode, BackfillResult


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object]]:
    calls: list[tuple[str, object]] = []

    @contextmanager
    def begin() -> Iterator[object]:
        calls.append(("begin", None))
        yield object()
        calls.append(("commit", None))

    engine = SimpleNamespace(
        begin=begin, dispose=lambda: calls.append(("dispose", None))
    )
    monkeypatch.setenv("SAPPHIRE_CONFIG", "fixture.toml")
    monkeypatch.setattr(cli, "create_engine_from_env", lambda: engine)
    monkeypatch.setattr(cli, "load_deployment_identity_config", lambda path: object())
    monkeypatch.setattr(cli, "PgTenantStore", lambda conn: object())

    def principal(*args: object, **kwargs: object) -> object:
        calls.append(("tenant", kwargs["tenant_code"]))
        return SimpleNamespace(tenant_id=UUID(int=1))

    monkeypatch.setattr(cli, "resolve_run_principal", principal)

    def preflight(conn: object, ids: list[UUID], **kwargs: object) -> None:
        calls.append(("preflight", tuple(ids)))
        if not 1 <= len(ids) <= 100 or len(set(ids)) != len(ids):
            raise ValueError("provide 1–100 distinct forecast IDs")

    monkeypatch.setattr(cli, "check_backfill_scope", preflight)

    def backfill(*args: object, **kwargs: object) -> BackfillResult:
        calls.append(("backfill", kwargs["mode"]))
        return BackfillResult.WOULD_CREATE

    monkeypatch.setattr(cli, "backfill_weather_summary", backfill)
    return calls


@pytest.mark.parametrize(
    "apply,mode", [(False, BackfillMode.DRY_RUN), (True, BackfillMode.APPLY)]
)
def test_default_dry_run_and_whole_list_preflight(
    harness: list[tuple[str, object]], apply: bool, mode: BackfillMode
) -> None:
    args = [
        "--tenant",
        "fixture",
        "--forecast-id",
        str(UUID(int=1)),
        "--forecast-id",
        str(UUID(int=2)),
    ]
    assert cli.main(args + (["--apply"] if apply else [])) == 0
    assert harness[1] == ("tenant", "fixture")
    assert harness[2] == ("preflight", (UUID(int=1), UUID(int=2)))
    assert [entry for entry in harness if entry[0] == "backfill"] == [
        ("backfill", mode)
    ] * 2
    assert harness[-1] == ("dispose", None)


@pytest.mark.parametrize("count,duplicate", [(2, True), (101, False)])
def test_invalid_batch_refuses_before_any_backfill(
    harness: list[tuple[str, object]], count: int, duplicate: bool
) -> None:
    args = ["--tenant", "fixture"]
    for i in range(count):
        args += ["--forecast-id", str(UUID(int=1 if duplicate else i + 1))]
    assert cli.main(args) == 3
    assert not any(name == "backfill" for name, _ in harness)


def test_tenant_refusal_precedes_preflight(
    harness: list[tuple[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise TenantIsolationError("not writable")

    monkeypatch.setattr(cli, "resolve_run_principal", refuse)
    assert cli.main(["--tenant", "other", "--forecast-id", str(UUID(int=1))]) == 3
    assert not any(name in ("backfill", "preflight") for name, _ in harness)


def test_scope_refusal_precedes_any_write(
    harness: list[tuple[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise ValueError("tenant and purpose mismatch")

    monkeypatch.setattr(cli, "check_backfill_scope", refuse)
    assert (
        cli.main(["--tenant", "fixture", "--forecast-id", str(UUID(int=1)), "--apply"])
        == 3
    )
    assert not any(name == "backfill" for name, _ in harness)


def test_protected_store_failure_is_sanitized(
    harness: list[tuple[str, object]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from sapphire_flow.exceptions import StoreError

    def refuse(*args: object, **kwargs: object) -> None:
        raise StoreError("private database detail")

    monkeypatch.setattr(cli, "backfill_weather_summary", refuse)
    assert cli.main(["--tenant", "fixture", "--forecast-id", str(UUID(int=1))]) == 3
    captured = capsys.readouterr()
    assert "private database detail" not in captured.out + captured.err
