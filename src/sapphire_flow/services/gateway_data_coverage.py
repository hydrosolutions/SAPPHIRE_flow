"""Aggregate source-coverage evidence, separate from model readiness."""

from __future__ import annotations

import hashlib
import math
import os
import re
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from numbers import Real
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

import pandas as pd

from sapphire_flow.adapters.recap_registration import (
    GatewayAccessError,
    quiet_gateway_transport,
    safe_gateway_failure,
)
from sapphire_flow.types.gateway_preparation import (
    SourceRunContract,
    SourceRunSemantics,
)

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True, kw_only=True, slots=True)
class CoverageScope:
    polygon: str
    start: datetime
    end: datetime
    cadence: timedelta
    source: str
    contract_reference: str
    source_run: SourceRunContract = field(default_factory=SourceRunContract)
    expected_times: tuple[datetime, ...] | None = None

    def __post_init__(self) -> None:
        if not self.contract_reference.strip() or not self.source or not self.polygon:
            raise ValueError("confirmed source support and polygon required")
        if self.cadence <= timedelta(0) or self.start >= self.end:
            raise ValueError("nonempty positive-cadence coverage scope required")
        if any(
            t.tzinfo is None or t.utcoffset() != timedelta(0)
            for t in (self.start, self.end)
        ):
            raise ValueError("coverage scope must be UTC")
        if self.expected_times is not None:
            if not self.expected_times or any(
                time.utcoffset() != timedelta(0) or not self.start <= time < self.end
                for time in self.expected_times
            ):
                raise ValueError(
                    "expected support must be nonempty UTC points inside scope"
                )
            if tuple(sorted(set(self.expected_times))) != self.expected_times:
                raise ValueError("expected support must be ordered and distinct")
        elif (self.end - self.start) % self.cadence:
            raise ValueError("coverage interval must align with confirmed support")


@dataclass(frozen=True, kw_only=True, slots=True)
class CoverageEvidence:
    expected_count: int
    usable_count: int
    missing_intervals: tuple[tuple[str, str], ...]
    holds: tuple[str, ...]
    response_sha256: str
    validated_source: str | None
    validated_source_run: SourceRunContract | None
    validated_run_identities: tuple[str, ...]


def assess_coverage(frame: pd.DataFrame, scope: CoverageScope) -> CoverageEvidence:
    """Check one explicit polygon/variable/member response, without unit conversion."""
    expected = (
        scope.expected_times
        if scope.expected_times is not None
        else tuple(
            scope.start + n * scope.cadence
            for n in range(int((scope.end - scope.start) / scope.cadence))
        )
    )
    holds: set[str] = set()
    usable: set[datetime] = set()
    run_identities: set[str] = set()
    digest = hashlib.sha256()
    columns = {scope.polygon, "source"}
    if scope.source_run.semantics != SourceRunSemantics.ABSENT:
        columns.add("source_run")
    if scope.source_run.semantics == SourceRunSemantics.UNRESOLVED:
        holds.add("source_run_contract_unconfirmed")
    elif (
        scope.source_run.semantics == SourceRunSemantics.ABSENT
        and "source_run" in frame.columns
    ):
        holds.add("unexpected_source_run_column")
    elif not columns <= set(frame.columns) or not frame.columns.is_unique:
        holds.add("missing_or_duplicate_columns")
    elif not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        holds.add("unknown_time_axis")
    else:
        duplicate: set[datetime] = set(
            cast("Any", frame.index)[frame.index.duplicated(keep=False)]
        )
        if duplicate:
            holds.add("duplicate_timestamps")
        expected_set = set(expected)
        for timestamp, row in cast("Any", frame).iterrows():
            if not isinstance(timestamp, pd.Timestamp):
                holds.add("invalid_timestamp")
                continue
            # Digest actual relevant response cells, not raw exported series.
            digest.update(
                repr(
                    (
                        timestamp.isoformat(),
                        row[scope.polygon],
                        row["source"],
                        row.get("source_run"),
                    )
                ).encode()
            )
            if timestamp.utcoffset() != timedelta(0):
                holds.add("non_utc_timestamp")
                continue
            if timestamp not in expected_set:
                holds.add("unexpected_time_support")
                continue
            if timestamp in duplicate:
                continue
            if (
                not isinstance(row["source"], str)
                or row["source"] != scope.source
                or not _matches_source_run(
                    row.get("source_run"), scope.source_run, timestamp
                )
            ):
                holds.add("wrong_source_provenance")
                continue
            value = row[scope.polygon]
            if (
                isinstance(value, bool)
                or not isinstance(value, Real)
                or not math.isfinite(value)
            ):
                holds.add("unusable_values")
                continue
            usable.add(timestamp)
            observed_run = row.get("source_run")
            if isinstance(observed_run, str):
                run_identities.add(observed_run)
            elif isinstance(observed_run, datetime) and observed_run is not pd.NaT:
                run_identities.add(observed_run.astimezone(UTC).isoformat())
    missing = [timestamp for timestamp in expected if timestamp not in usable]
    intervals: list[tuple[str, str]] = []
    following = dict(zip(expected, (*expected[1:], scope.end), strict=True))
    for timestamp in missing:
        if intervals and intervals[-1][1] == timestamp.isoformat():
            intervals[-1] = (intervals[-1][0], following[timestamp].isoformat())
        else:
            intervals.append((timestamp.isoformat(), following[timestamp].isoformat()))
    if missing:
        holds.add("missing_requested_support")
    return CoverageEvidence(
        expected_count=len(expected),
        usable_count=len(usable),
        missing_intervals=tuple(intervals),
        holds=tuple(sorted(holds)),
        response_sha256="sha256:" + digest.hexdigest(),
        validated_source=scope.source if usable else None,
        validated_source_run=scope.source_run if usable else None,
        validated_run_identities=tuple(sorted(run_identities)),
    )


def _matches_source_run(
    value: object, contract: SourceRunContract, valid_time: datetime
) -> bool:
    if contract.semantics in (
        SourceRunSemantics.UTC_INSTANT,
        SourceRunSemantics.UTC_DATE_MIDNIGHT,
    ):
        if (
            not isinstance(value, datetime)
            or value is pd.NaT
            or value.utcoffset() is None
        ):
            return False
        expected = (
            contract.timestamp
            if contract.semantics == SourceRunSemantics.UTC_INSTANT
            else valid_time.astimezone(UTC).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        )
        return value.astimezone(UTC) == expected
    if contract.semantics == SourceRunSemantics.EXACT:
        return isinstance(value, str) and value == contract.value
    if contract.semantics == SourceRunSemantics.NULL:
        return (
            value is None
            or value is pd.NA
            or value is pd.NaT
            or (isinstance(value, Real) and math.isnan(value))
        )
    return contract.semantics == SourceRunSemantics.ABSENT


_Result = TypeVar("_Result")
_DOWNLOAD_DIRECTORY = re.compile(r"^\.recap-download-[a-z0-9_]+$")
_DOWNLOAD_FILE = re.compile(r"^\.tmp_recap_[0-9]+\.parquet$")
_MARKER = "SAP3 private Gateway download attempt v1\n"


def recover_downloads(workspace: Path) -> int:
    """Caller holds exclusive lock; remove only marked owned orphan raw files."""
    removed = 0
    for directory in workspace.iterdir():
        if (
            not _DOWNLOAD_DIRECTORY.fullmatch(directory.name)
            or directory.is_symlink()
            or not directory.is_dir()
        ):
            continue
        marker = directory / ".owned"
        if marker.is_symlink() or not marker.is_file() or marker.read_text() != _MARKER:
            continue
        if directory.stat().st_mode & 0o077:
            raise ValueError("download directory lost private permissions")
        for path in directory.iterdir():
            if (
                _DOWNLOAD_FILE.fullmatch(path.name)
                and not path.is_symlink()
                and path.is_file()
            ):
                path.unlink()
                removed += 1
        if set(directory.iterdir()) == {marker}:
            marker.unlink()
            directory.rmdir()
    return removed


def private_gateway_fetch(workspace: Path, fetch: Callable[[], _Result]) -> _Result:
    """CLI-only process boundary, under its exclusive workspace lock.

    The supported client keeps its redirects unchanged. It writes transient raw
    Parquet in CWD; restrict creation and contain both files and exception chains.
    Resolve all caller paths before entering. Never call from a threaded server.
    """
    if threading.current_thread() is not threading.main_thread():
        raise ValueError("Gateway retrieval requires an exclusive CLI process")
    workspace = workspace.resolve()
    recover_downloads(workspace)
    previous_directory = Path.cwd()
    directory = Path(tempfile.mkdtemp(prefix=".recap-download-", dir=workspace))
    previous_umask = os.umask(0o077)
    error: GatewayAccessError | None = None
    results: list[_Result] = []
    try:
        (directory / ".owned").write_text(_MARKER)
        os.chdir(directory)
        with quiet_gateway_transport():
            try:
                results.append(fetch())
            except Exception as exc:
                error = safe_gateway_failure(exc)
    finally:
        os.chdir(previous_directory)
        os.umask(previous_umask)
        recover_downloads(workspace)
    if error is not None:
        raise error
    return results[0]
