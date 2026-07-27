"""Tests for revision-pinned masked/raw and effective project inspection."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import cast

import msgspec
import pytest

from comparo.application import source_inspection as source_module
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import ResourceCoordinate
from comparo.application.project_workspace import ProjectWorkspaceRequest
from comparo.application.project_workspace import inspect_project_workspace
from comparo.application.source_inspection import EffectiveResourceInspectionRequest
from comparo.application.source_inspection import SourceInspectionError
from comparo.application.source_inspection import SourceInspectionErrorCode
from comparo.application.source_inspection import SourceInspectionLimits
from comparo.application.source_inspection import SourceInspectionRequest
from comparo.application.source_inspection import SourceViewMode
from comparo.application.source_inspection import SourceWindow
from comparo.application.source_inspection import inspect_effective_resource
from comparo.application.source_inspection import inspect_project_source
from comparo.configuration.loader import compile_project_path
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
    assert "https://staging.example.test" in masked.content
    assert "/ping" in masked.content
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


def test_masked_source_conservatively_hides_every_scalar_below_resource_specs(
    tmp_path: Path,
) -> None:
    workspace = ProjectWorkspaceRequest(config=_write_project(tmp_path))
    source_path = tmp_path / ".comparo" / "resources.yaml"
    source_path.write_text(
        source_path.read_text(encoding="utf-8")
        + "---\n"
        + "apiVersion: comparo/v1alpha1\n"
        + "kind: Value\n"
        + "metadata: {id: masking-contract, name: Masking contract}\n"
        + "spec:\n"
        + "  value:\n"
        + "    payload: arbitrary-payload-canary\n"
        + "    nested:\n"
        + "      alias: nested-alias-canary\n"
        + "      interpolation: ${secrets.ARBITRARY_TOKEN}\n",
        encoding="utf-8",
    )
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

    assert "arbitrary-payload-canary" not in masked.content
    assert "nested-alias-canary" not in masked.content
    assert "${secrets.ARBITRARY_TOKEN}" not in masked.content
    assert "payload:" in masked.content
    assert "nested:" in masked.content
    assert "alias:" in masked.content
    assert "arbitrary-payload-canary" in exact.content
    assert "nested-alias-canary" in exact.content
    assert "${secrets.ARBITRARY_TOKEN}" in exact.content


def test_alias_bearing_invalid_yaml_fails_closed_without_payload_egress(tmp_path: Path) -> None:
    workspace = ProjectWorkspaceRequest(config=_write_project(tmp_path))
    source_path = tmp_path / ".comparo" / "resources.yaml"
    source_path.write_text(
        source_path.read_text(encoding="utf-8")
        + "---\n"
        + "apiVersion: comparo/v1alpha1\n"
        + "kind: Value\n"
        + "metadata: {id: alias-contract}\n"
        + "spec:\n"
        + "  value:\n"
        + "    payload: &shared alias-payload-canary\n"
        + "    nested: {copy: *shared}\n",
        encoding="utf-8",
    )
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

    assert "alias-payload-canary" not in masked.content
    assert all(character.isspace() or character == "*" for character in masked.content)


def test_secret_interpolation_is_masked_even_outside_a_resource_spec(tmp_path: Path) -> None:
    config = _write_project(tmp_path)
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "description: Explorer fixture",
            "description: ${secrets.PROJECT_DESCRIPTION}",
        ),
        encoding="utf-8",
    )
    workspace = ProjectWorkspaceRequest(config=config)
    inspected = inspect_project_workspace(workspace)
    source = next(item for item in inspected.sources if item.entry)
    assert source.sha256 is not None

    masked = inspect_project_source(
        SourceInspectionRequest(
            workspace=workspace,
            source_id=source.source_id,
            limits=_limits(),
            expected_sha256=source.sha256,
        )
    )

    assert "${secrets.PROJECT_DESCRIPTION}" not in masked.content
    assert "description:" in masked.content


def test_source_request_and_window_contracts_reject_invalid_disclosure_state(
    tmp_path: Path,
) -> None:
    workspace = ProjectWorkspaceRequest(config=_write_project(tmp_path))
    limits = _limits()
    with pytest.raises(TypeError, match="error code"):
        SourceInspectionError(cast("SourceInspectionErrorCode", object()))
    limit_cases: tuple[dict[str, object], ...] = (
        {"maximum_source_bytes": 0},
        {"maximum_output_bytes": cast("int", True)},
        {"maximum_lines": -1},
    )
    for changes in limit_cases:
        with pytest.raises(SourceInspectionError) as caught:
            msgspec.structs.replace(limits, **changes)
        assert caught.value.code is SourceInspectionErrorCode.INVALID_REQUEST

    request = SourceInspectionRequest(
        workspace=workspace,
        source_id="source",
        limits=limits,
    )
    request_cases: tuple[dict[str, object], ...] = (
        {"workspace": cast("ProjectWorkspaceRequest", object())},
        {"source_id": ""},
        {"source_id": "x" * 1025},
        {"limits": cast("SourceInspectionLimits", object())},
        {"start_line": 0},
        {"start_line": cast("int", True)},
        {"mode": cast("SourceViewMode", object())},
        {"expected_sha256": "A" * 64},
        {"expected_sha256": "0" * 63},
    )
    for changes in request_cases:
        with pytest.raises(SourceInspectionError) as caught:
            msgspec.structs.replace(request, **changes)
        assert caught.value.code is SourceInspectionErrorCode.INVALID_REQUEST

    window = SourceWindow(
        source_id="source",
        sha256="0" * 64,
        byte_count=1,
        mode=SourceViewMode.MASKED,
        start_line=1,
        returned_lines=1,
        total_lines=2,
        content="x\n",
        next_line=2,
        line_truncated=False,
        redaction_count=0,
        contains_unmasked_values=False,
    )
    diagnostic = ApplicationDiagnostic(code="source.test", severity="info", stage="test")
    window_cases: tuple[tuple[dict[str, object], type[Exception]], ...] = (
        ({"mode": cast("SourceViewMode", object())}, TypeError),
        ({"byte_count": -1}, ValueError),
        ({"returned_lines": cast("int", True)}, ValueError),
        ({"start_line": 0}, ValueError),
        ({"content": cast("str", object())}, TypeError),
        ({"next_line": 1}, ValueError),
        ({"next_line": 3}, ValueError),
        ({"line_truncated": cast("bool", 1)}, TypeError),
        ({"contains_unmasked_values": True}, ValueError),
        ({"diagnostics": cast("tuple[ApplicationDiagnostic, ...]", [diagnostic])}, TypeError),
        ({"diagnostics": (cast("ApplicationDiagnostic", object()),)}, TypeError),
    )
    for changes, error in window_cases:
        with pytest.raises(error):
            msgspec.structs.replace(window, **changes)


def test_effective_inspection_rejects_invalid_windows_and_missing_resources(
    tmp_path: Path,
) -> None:
    workspace = ProjectWorkspaceRequest(config=_write_project(tmp_path))
    inspected = inspect_project_workspace(workspace)
    assert inspected.snapshot is not None
    coordinate = inspected.snapshot.resources[0].coordinate
    request = EffectiveResourceInspectionRequest(
        workspace=workspace,
        coordinate=coordinate,
        start_field=0,
        maximum_fields=1,
    )
    for changes in (
        {"workspace": cast("ProjectWorkspaceRequest", object())},
        {"coordinate": cast("ResourceCoordinate", object())},
        {"start_field": -1},
        {"start_relationship": cast("int", True)},
        {"maximum_fields": 0},
        {"maximum_relationships": cast("int", True)},
    ):
        with pytest.raises(SourceInspectionError) as caught:
            msgspec.structs.replace(request, **changes)
        assert caught.value.code is SourceInspectionErrorCode.INVALID_REQUEST

    with pytest.raises(SourceInspectionError) as untyped:
        inspect_effective_resource(cast("EffectiveResourceInspectionRequest", object()))
    assert untyped.value.code is SourceInspectionErrorCode.INVALID_REQUEST

    absent = msgspec.structs.replace(
        request,
        coordinate=ResourceCoordinate(
            namespace="local",
            kind="Request",
            resource_id="absent",
            fingerprint="0" * 64,
        ),
    )
    with pytest.raises(SourceInspectionError) as missing:
        inspect_effective_resource(absent)
    assert missing.value.code is SourceInspectionErrorCode.RESOURCE_NOT_FOUND

    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("not: [valid", encoding="utf-8")
    with pytest.raises(SourceInspectionError) as project:
        inspect_effective_resource(
            msgspec.structs.replace(
                request,
                workspace=ProjectWorkspaceRequest(config=invalid),
            )
        )
    assert project.value.code is SourceInspectionErrorCode.PROJECT_INVALID


def test_source_inspection_maps_missing_changed_non_utf8_and_budget_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = ProjectWorkspaceRequest(config=_write_project(tmp_path))
    inspected = inspect_project_workspace(workspace)
    source = next(item for item in inspected.sources if not item.entry)
    assert source.sha256 is not None
    request = SourceInspectionRequest(
        workspace=workspace,
        source_id=source.source_id,
        limits=_limits(),
        expected_sha256=source.sha256,
    )
    with pytest.raises(SourceInspectionError) as untyped:
        inspect_project_source(cast("SourceInspectionRequest", object()))
    assert untyped.value.code is SourceInspectionErrorCode.INVALID_REQUEST

    with pytest.raises(SourceInspectionError) as missing:
        inspect_project_source(msgspec.structs.replace(request, source_id="missing"))
    assert missing.value.code is SourceInspectionErrorCode.SOURCE_NOT_FOUND

    monkeypatch.setattr(source_module, "_read_source", lambda _source, _maximum: b"changed")
    with pytest.raises(SourceInspectionError) as changed:
        inspect_project_source(request)
    assert changed.value.code is SourceInspectionErrorCode.SOURCE_CHANGED

    monkeypatch.setattr(source_module, "_read_source", lambda _source, _maximum: b"\xff")
    monkeypatch.setattr(source_module, "_digest", lambda _content: source.sha256)
    with pytest.raises(SourceInspectionError) as unavailable:
        inspect_project_source(request)
    assert unavailable.value.code is SourceInspectionErrorCode.SOURCE_UNAVAILABLE


def test_low_level_source_reads_and_windows_fail_closed_at_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = ProjectWorkspaceRequest(config=_write_project(tmp_path))
    loaded = compile_project_path(workspace.config, limits=workspace.load_limits)
    source = next(item for item in loaded.catalog if item.source_id.value != "entry-config")

    with pytest.raises(SourceInspectionError) as absent_size:
        source_module._read_source(
            msgspec.structs.replace(source, byte_count=None, sha256=None),
            1024,
        )
    assert absent_size.value.code is SourceInspectionErrorCode.BUDGET_EXCEEDED
    with pytest.raises(SourceInspectionError) as declared_size:
        source_module._read_source(source, 1)
    assert declared_size.value.code is SourceInspectionErrorCode.BUDGET_EXCEEDED

    directory_source = msgspec.structs.replace(
        source,
        path=tmp_path.resolve(),
        byte_count=tmp_path.stat().st_size,
    )
    with pytest.raises(SourceInspectionError) as changed:
        source_module._read_source(directory_source, max(1, tmp_path.stat().st_size))
    assert changed.value.code is SourceInspectionErrorCode.SOURCE_CHANGED

    canary = "source-path-secret-canary"

    def unavailable(*_args: object, **_kwargs: object) -> int:
        raise OSError(2, canary, str(tmp_path / canary))

    monkeypatch.setattr(os, "open", unavailable)
    with pytest.raises(SourceInspectionError) as failed:
        source_module._read_source(source, 1_000_000)
    assert failed.value.code is SourceInspectionErrorCode.SOURCE_UNAVAILABLE
    assert failed.value.__cause__ is None
    assert failed.value.__context__ is None
    assert canary not in repr(failed.value)
    monkeypatch.undo()

    descriptor = 42
    reads = iter((b"xxxx", b""))
    monkeypatch.setattr(os, "open", lambda *_args: descriptor)
    monkeypatch.setattr(stat, "S_ISREG", lambda _mode: True)
    monkeypatch.setattr(os, "read", lambda *_args: next(reads))
    monkeypatch.setattr(os, "close", lambda _descriptor: None)
    fake_metadata = type("Metadata", (), {"st_mode": 0, "st_size": 3})()
    monkeypatch.setattr(os, "fstat", lambda _descriptor: fake_metadata)
    oversized_source = msgspec.structs.replace(source, byte_count=3)
    with pytest.raises(SourceInspectionError) as exceeded:
        source_module._read_source(oversized_source, 3)
    assert exceeded.value.code is SourceInspectionErrorCode.BUDGET_EXCEEDED

    assert source_module._mask_source("plain", (), "source") == ("plain", 0)
    comment_source = (
        'plain: "# quoted" # comment payload\n'
        "single: 'it''s # quoted' # another payload\n"
        'escaped: "quote \\" # still quoted" # final payload'
    )
    masked_comments, comment_count = source_module._mask_source(
        comment_source,
        (),
        "source",
    )
    assert "comment payload" not in masked_comments
    assert "another payload" not in masked_comments
    assert "final payload" not in masked_comments
    assert "# quoted" in masked_comments
    assert "# still quoted" in masked_comments
    assert comment_count == 3
    assert source_module._mask_unparsed_source(" \n\t") == (" \n\t", 0)
    assert source_module._mask_unparsed_source("secret\n") == ("******\n", 1)
    assert source_module._line_window("", start_line=1, maximum_lines=1, maximum_bytes=1) == (
        "",
        0,
        0,
        None,
        False,
    )
    with pytest.raises(SourceInspectionError) as line:
        source_module._line_window("one\n", start_line=2, maximum_lines=1, maximum_bytes=1)
    assert line.value.code is SourceInspectionErrorCode.INVALID_REQUEST
    truncated = source_module._line_window(
        "éé\n",
        start_line=1,
        maximum_lines=1,
        maximum_bytes=3,
    )
    assert truncated == ("é", 1, 1, None, True)
    empty_prefix = source_module._line_window(
        "é\n",
        start_line=1,
        maximum_lines=1,
        maximum_bytes=1,
    )
    assert empty_prefix == ("", 0, 1, None, True)
    stopped_after_complete_line = source_module._line_window(
        "a\nlong\n",
        start_line=1,
        maximum_lines=2,
        maximum_bytes=2,
    )
    assert stopped_after_complete_line == ("a\n", 1, 2, 2, False)
