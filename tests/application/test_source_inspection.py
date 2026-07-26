"""Tests for revision-pinned masked/raw and effective project inspection."""

from __future__ import annotations

from pathlib import Path

import msgspec
import pytest

from comparo.application.project_workspace import ProjectWorkspaceRequest
from comparo.application.project_workspace import inspect_project_workspace
from comparo.application.source_inspection import EffectiveResourceInspectionRequest
from comparo.application.source_inspection import SourceInspectionError
from comparo.application.source_inspection import SourceInspectionErrorCode
from comparo.application.source_inspection import SourceInspectionLimits
from comparo.application.source_inspection import SourceInspectionRequest
from comparo.application.source_inspection import SourceViewMode
from comparo.application.source_inspection import inspect_effective_resource
from comparo.application.source_inspection import inspect_project_source
from tests.application.test_project_workspace import _SECRET_CANARY
from tests.application.test_project_workspace import _write_project


def _limits(*, lines: int = 100, output: int = 100_000) -> SourceInspectionLimits:
    return SourceInspectionLimits(
        maximum_source_bytes=1_000_000,
        maximum_output_bytes=output,
        maximum_lines=lines,
    )


def test_source_defaults_to_structural_masking_and_requires_explicit_exact_mode(
    tmp_path: Path,
) -> None:
    workspace = ProjectWorkspaceRequest(config=_write_project(tmp_path))
    inspected = inspect_project_workspace(workspace)
    source = next(item for item in inspected.sources if not item.entry)
    assert source.sha256 is not None

    masked = inspect_project_source(
        SourceInspectionRequest(
            workspace=workspace,
            source_id=source.source_id,
            expected_sha256=source.sha256,
            limits=_limits(),
        )
    )
    exact = inspect_project_source(
        SourceInspectionRequest(
            workspace=workspace,
            source_id=source.source_id,
            expected_sha256=source.sha256,
            limits=_limits(),
            mode=SourceViewMode.TRUSTED_EXACT,
        )
    )

    assert _SECRET_CANARY not in masked.content
    assert masked.redaction_count >= 1
    assert masked.contains_unmasked_values is False
    assert _SECRET_CANARY in exact.content
    assert exact.redaction_count == 0
    assert exact.contains_unmasked_values is True
    assert masked.sha256 == exact.sha256 == source.sha256


def test_source_windows_are_bounded_and_reject_changed_revisions(tmp_path: Path) -> None:
    workspace = ProjectWorkspaceRequest(config=_write_project(tmp_path))
    inspected = inspect_project_workspace(workspace)
    source = next(item for item in inspected.sources if not item.entry)
    assert source.sha256 is not None
    first = inspect_project_source(
        SourceInspectionRequest(
            workspace=workspace,
            source_id=source.source_id,
            expected_sha256=source.sha256,
            limits=_limits(lines=3),
        )
    )

    assert first.returned_lines == 3
    assert first.next_line == 4
    source_path = tmp_path / ".comparo" / "resources.yaml"
    source_path.write_text(source_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(SourceInspectionError) as stale:
        inspect_project_source(
            SourceInspectionRequest(
                workspace=workspace,
                source_id=source.source_id,
                expected_sha256=source.sha256,
                limits=_limits(),
            )
        )
    assert stale.value.code is SourceInspectionErrorCode.SOURCE_CHANGED


def test_effective_resource_projection_is_revision_exact_bounded_and_value_safe(
    tmp_path: Path,
) -> None:
    workspace = ProjectWorkspaceRequest(config=_write_project(tmp_path))
    inspected = inspect_project_workspace(workspace)
    assert inspected.snapshot is not None
    resource = next(
        item
        for item in inspected.snapshot.resources
        if item.coordinate.resource_id == "credentials"
    )

    window = inspect_effective_resource(
        EffectiveResourceInspectionRequest(
            workspace=workspace,
            coordinate=resource.coordinate,
            start_field=0,
            maximum_fields=1,
        )
    )

    assert window.coordinate == resource.coordinate
    assert len(window.fields) == 1
    assert window.total_fields > 1
    assert window.next_field == 1
    assert _SECRET_CANARY not in msgspec.json.encode(window).decode()
