"""Framework-neutral terminal state contracts."""

from __future__ import annotations

from typing import cast

import msgspec
import pytest

from comparo.application.compare import ComparePreparationHandle
from comparo.application.execution_session import ExecutionProgressSnapshot
from comparo.application.execution_session import ExecutionSessionPhase
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import PlanCardinalityInspection
from comparo.application.inspection import PlanInspection
from comparo.application.inspection import ResourceCoordinate
from comparo.application.inspection import SourceCoordinate
from comparo.application.project_workspace import ProjectResourceSummary
from comparo.application.project_workspace import ProjectWorkspaceSnapshot
from comparo.application.report_archive import ReportArchiveListing
from comparo.application.results import ResultProjection
from comparo.application.results import ResultResource
from comparo.application.results import ResultSummary
from comparo.tui.contracts.models import CompareDraft
from comparo.tui.contracts.models import CompareMode
from comparo.tui.navigation.routes import WorkspaceId
from comparo.tui.state.models import ComparePhase
from comparo.tui.state.models import CompareViewState
from comparo.tui.state.models import ContentPhase
from comparo.tui.state.models import ExplorerViewState
from comparo.tui.state.models import ReportViewState
from comparo.tui.state.models import ThemePreference
from comparo.tui.state.models import TuiPreferences


def _draft() -> CompareDraft:
    return CompareDraft(mode=CompareMode.ASSERTIONS, subject="ping", environment="dev")


def _diagnostic() -> ApplicationDiagnostic:
    return ApplicationDiagnostic(code="safe.code", severity="error", stage="configuration")


def _coordinate(kind: str = "Request") -> ResourceCoordinate:
    return ResourceCoordinate(
        namespace="demo",
        kind=kind,
        resource_id="ping",
        fingerprint="a" * 64,
    )


def _plan() -> PlanInspection:
    return PlanInspection(
        api_version="comparo.plan/v1alpha1",
        run_id="run",
        invocation_id="invocation",
        fingerprint="a" * 64,
        ordering="canonical",
        policy=_coordinate("ExecutionPolicy"),
        cardinality=PlanCardinalityInspection(
            cells=1,
            trials=1,
            environment_executions=1,
            comparisons=0,
            maximum_attempts=1,
        ),
        slices=(),
        budgets=(),
        provenance=(),
    )


def _projection() -> ResultProjection:
    project = ResultResource(
        namespace="demo",
        kind="Project",
        resource_id="demo",
        fingerprint="a" * 64,
    )
    return ResultProjection(
        summary=ResultSummary(
            artifact_api_version="comparo.artifact/v1alpha1",
            artifact_id="artifact",
            run_id="run",
            invocation_id="invocation",
            created_unix_nanoseconds=1,
            project=project,
            completion="complete",
            completion_instant_unix_nanoseconds=2,
            completion_reasons=(),
            completion_stage=None,
            completion_code=None,
            gate_verdict="passed",
            gate_profile=None,
            gate_reasons=(),
            gate_coverage=(),
            gate_assessments=(),
            executions=(),
            comparisons=(),
            assertions=(),
            diffs=(),
            coverage_issues=0,
        ),
        executions=(),
        assertions=(),
        comparisons=(),
        diffs=(),
        links=(),
    )


def test_preferences_are_tui_only_and_typed() -> None:
    preferences = TuiPreferences(
        theme=ThemePreference.DARK,
        default_workspace=WorkspaceId.COMPARE,
        dense=True,
        mouse=False,
    )
    assert preferences.default_workspace is WorkspaceId.COMPARE
    for field, value in (
        ("theme", "dark"),
        ("default_workspace", "compare"),
        ("dense", 1),
        ("mouse", 1),
    ):
        with pytest.raises(TypeError):
            msgspec.structs.replace(preferences, **{field: value})


def test_explorer_state_requires_phase_specific_safe_products() -> None:
    loading = ExplorerViewState(phase=ContentPhase.LOADING)
    error = ExplorerViewState(phase=ContentPhase.ERROR, diagnostics=(_diagnostic(),))
    assert loading.project is None
    assert error.diagnostics
    root = ProjectResourceSummary(
        coordinate=_coordinate("Project"),
        name="Demo",
        description=None,
        tags=(),
        source=SourceCoordinate(source="project", document=0, line=1, column=1),
    )
    snapshot = ProjectWorkspaceSnapshot(
        api_version="comparo/v1alpha1",
        namespace="demo",
        project=root,
        resources=(),
    )
    assert ExplorerViewState(phase=ContentPhase.READY, project=snapshot).project is snapshot
    with pytest.raises(ValueError, match="phase"):
        ExplorerViewState(phase=ContentPhase.READY)
    with pytest.raises(TypeError, match="phase"):
        ExplorerViewState(phase=cast("ContentPhase", "ready"))
    with pytest.raises(TypeError, match="snapshot"):
        ExplorerViewState(
            phase=ContentPhase.READY,
            project=cast("ProjectWorkspaceSnapshot", object()),
        )
    with pytest.raises(TypeError, match="diagnostics"):
        ExplorerViewState(
            phase=ContentPhase.ERROR,
            diagnostics=cast("tuple[ApplicationDiagnostic, ...]", (_diagnostic(), object())),
        )


def test_compare_state_rejects_products_owned_by_another_phase() -> None:
    draft = _draft()
    assert CompareViewState(phase=ComparePhase.DRAFT, draft=draft).phase is ComparePhase.DRAFT
    error = CompareViewState(
        phase=ComparePhase.ERROR,
        draft=draft,
        diagnostics=(_diagnostic(),),
    )
    assert error.diagnostics
    plan = _plan()
    handle = ComparePreparationHandle(value="handle")
    assert (
        CompareViewState(
            phase=ComparePhase.PREVIEW,
            draft=draft,
            handle=handle,
            plan=plan,
        ).handle
        is handle
    )
    progress = ExecutionProgressSnapshot(
        revision=0,
        run_id="run",
        phase=ExecutionSessionPhase.RUNNING,
        total_executions=1,
        total_comparisons=0,
    )
    for phase in (ComparePhase.RUNNING, ComparePhase.CANCELLING):
        assert (
            CompareViewState(
                phase=phase,
                draft=draft,
                plan=plan,
                progress=progress,
            ).progress
            is progress
        )
    projection = _projection()
    assert (
        CompareViewState(
            phase=ComparePhase.RESULTS,
            draft=draft,
            plan=plan,
            results=projection,
        ).results
        is projection
    )
    with pytest.raises(ValueError, match="phase"):
        CompareViewState(
            phase=ComparePhase.PREVIEW,
            draft=draft,
            handle=ComparePreparationHandle(value="handle"),
        )
    invalid_fields = (
        ("phase", "draft", TypeError),
        ("draft", object(), TypeError),
        ("handle", object(), TypeError),
        ("plan", object(), TypeError),
        ("progress", object(), TypeError),
        ("results", object(), TypeError),
    )
    for field, value, error_type in invalid_fields:
        with pytest.raises(error_type):
            msgspec.structs.replace(error, **{field: value})


def test_report_state_keeps_archive_results_and_errors_disjoint() -> None:
    listing = ReportArchiveListing(entries=(), failures=())
    empty = ReportViewState(phase=ContentPhase.EMPTY, archive=listing)
    ready = ReportViewState(phase=ContentPhase.READY, archive=listing)
    assert empty.archive == ready.archive
    failed = ReportViewState(phase=ContentPhase.ERROR, error_code="archive-unavailable")
    assert failed.error_code == "archive-unavailable"
    assert ReportViewState(phase=ContentPhase.LOADING).archive is None
    projection = _projection()
    selected = ReportViewState(
        phase=ContentPhase.READY,
        archive=listing,
        selected_bundle_id="bundle",
        results=projection,
    )
    assert selected.results is projection
    with pytest.raises(ValueError, match="phase"):
        ReportViewState(phase=ContentPhase.READY, archive=listing, results=projection)
    with pytest.raises(ValueError, match="phase"):
        ReportViewState(phase=ContentPhase.READY)
    with pytest.raises(TypeError, match="phase"):
        ReportViewState(phase=cast("ContentPhase", "ready"))
    with pytest.raises(TypeError, match="archive"):
        ReportViewState(
            phase=ContentPhase.READY,
            archive=cast("ReportArchiveListing", object()),
        )
    with pytest.raises(TypeError, match="results"):
        ReportViewState(
            phase=ContentPhase.READY,
            archive=listing,
            selected_bundle_id="bundle",
            results=cast("ResultProjection", object()),
        )
    with pytest.raises(ValueError, match="bounded"):
        ReportViewState(phase=ContentPhase.ERROR, error_code="")


def test_ready_state_types_are_not_structurally_forged() -> None:
    with pytest.raises(TypeError, match="snapshot"):
        ExplorerViewState(
            phase=ContentPhase.READY,
            project=cast("ProjectWorkspaceSnapshot", object()),
        )
    with pytest.raises(TypeError, match="plan"):
        CompareViewState(
            phase=ComparePhase.PREVIEW,
            draft=_draft(),
            handle=ComparePreparationHandle(value="handle"),
            plan=cast("PlanInspection", object()),
        )
    with pytest.raises(TypeError, match="results"):
        ReportViewState(
            phase=ContentPhase.READY,
            archive=ReportArchiveListing(entries=(), failures=()),
            selected_bundle_id="bundle",
            results=cast("ResultProjection", object()),
        )
