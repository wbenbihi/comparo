"""Admission boundaries for already-frozen ambient environment snapshots."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest

from comparo.adapters.values import EnvironmentSourceLimits
from comparo.adapters.values import PinnedProjectFiles
from comparo.application.environment_binding import CapturedAmbientEnvironment
from comparo.application.environment_binding import build_environment_snapshots
from comparo.application.environment_binding import capture_process_environment
from comparo.application.environment_binding import validate_ambient_environment
from comparo.application.preflight import PreparedProjectPlan
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount
from tests.application.test_environment_binding import _project


def test_process_capture_requires_exact_boundary_types(tmp_path: Path) -> None:
    project = _project(tmp_path, "{}")
    limits = EnvironmentSourceLimits()

    with pytest.raises(TypeError, match="PreparedProjectPlan"):
        capture_process_environment(cast("PreparedProjectPlan", object()), limits)
    with pytest.raises(TypeError, match="EnvironmentSourceLimits"):
        capture_process_environment(project, cast("EnvironmentSourceLimits", object()))


def test_captured_ambient_is_redacted_immutable_and_exactly_typed() -> None:
    canary = "captured-ambient-secret-canary"
    captured = CapturedAmbientEnvironment(
        (("TOKEN", canary),),
        limits=EnvironmentSourceLimits(),
    )

    assert repr(captured) == "<captured-ambient-environment>"
    assert str(captured) == "<captured-ambient-environment>"
    assert canary not in repr(captured)
    with pytest.raises(AttributeError, match="immutable"):
        captured.anything = object()
    with pytest.raises(AttributeError, match="immutable"):
        del captured.anything  # type: ignore[attr-defined]
    with pytest.raises(TypeError, match="limits"):
        CapturedAmbientEnvironment(
            (),
            limits=cast("EnvironmentSourceLimits", object()),
        )


def test_captured_ambient_cannot_launder_permissive_limits(tmp_path: Path) -> None:
    project = _project(tmp_path, "{inheritProcess: true}")
    captured = CapturedAmbientEnvironment(
        (("TOKEN", "large"),),
        limits=EnvironmentSourceLimits(),
    )
    tighter = EnvironmentSourceLimits(maximum_value_bytes=ByteCount(value=2))

    with (
        PinnedProjectFiles(tmp_path.resolve()) as files,
        pytest.raises(ValueError, match="value exceeds"),
    ):
        asyncio.run(
            build_environment_snapshots(
                project,
                files,
                ambient=captured,
                limits=tighter,
            )
        )


@pytest.mark.parametrize(
    "entries",
    [
        cast("tuple[tuple[str, str], ...]", []),
        cast("tuple[tuple[str, str], ...]", (("A",),)),
        cast("tuple[tuple[str, str], ...]", ((1, "value"),)),
        cast("tuple[tuple[str, str], ...]", (("A", 1),)),
    ],
)
def test_ambient_snapshot_requires_immutable_text_pairs(
    entries: tuple[tuple[str, str], ...],
) -> None:
    with pytest.raises(TypeError, match="immutable text pairs"):
        validate_ambient_environment(entries, EnvironmentSourceLimits())


def test_ambient_snapshot_accepts_absence_and_one_valid_entry() -> None:
    validate_ambient_environment(None, EnvironmentSourceLimits())
    validate_ambient_environment((("A", "value"),), EnvironmentSourceLimits())


@pytest.mark.parametrize(
    ("entries", "limits", "message"),
    [
        (
            (("B", "one"), ("A", "two")),
            EnvironmentSourceLimits(),
            "unique and sorted",
        ),
        (
            (("A", "one"), ("A", "two")),
            EnvironmentSourceLimits(),
            "unique and sorted",
        ),
        (
            (("A", "one"), ("B", "two")),
            EnvironmentSourceLimits(maximum_entries=PositiveCount(value=1)),
            "entry limit",
        ),
        (
            (("A", "large"),),
            EnvironmentSourceLimits(maximum_value_bytes=ByteCount(value=2)),
            "value exceeds",
        ),
        (
            (("AB", "cd"),),
            EnvironmentSourceLimits(maximum_total_bytes=ByteCount(value=3)),
            "aggregate",
        ),
    ],
)
def test_ambient_snapshot_rejects_noncanonical_or_over_budget_entries(
    entries: tuple[tuple[str, str], ...],
    limits: EnvironmentSourceLimits,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_ambient_environment(entries, limits)
