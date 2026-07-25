"""Headless tests for reusable Textual components."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from collections.abc import Coroutine
from functools import wraps
from pathlib import PurePath
from typing import ClassVar
from typing import cast
from typing import override

import pytest
from textual import on
from textual.app import App
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Button
from textual.widgets import Input
from textual.widgets import ListItem
from textual.widgets import ListView
from textual.widgets import Select
from textual.widgets import Static
from textual.widgets import Switch

from comparo.application import BudgetOverride
from comparo.application import MatrixOverride
from comparo.application import ValueResourceOverride
from comparo.application.execution_session import ExecutionProgressSnapshot
from comparo.application.execution_session import ExecutionSessionPhase
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import EnvironmentPairInspection
from comparo.application.inspection import PlanCardinalityInspection
from comparo.application.inspection import PlanInspection
from comparo.application.inspection import PlanSliceInspection
from comparo.application.inspection import ResourceCoordinate
from comparo.application.inspection import SourceCoordinate
from comparo.application.project_workspace import ProjectResourceSummary
from comparo.application.report_archive import ReportArchiveEntry
from comparo.application.results import ComparisonResultRow
from comparo.application.results import ExecutionResultRow
from comparo.application.results import ResultCrossLink
from comparo.application.results import ResultEntity
from comparo.application.results import ResultEvidence
from comparo.application.results import ResultFact
from comparo.application.results import ResultMetric
from comparo.application.results import ResultProjection
from comparo.application.results import ResultReference
from comparo.application.results import ResultRelation
from comparo.application.results import ResultResource
from comparo.application.results import ResultSummary
from comparo.application.results import ResultTiming
from comparo.application.results import ResultTimingSummary
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.value_references import ValueNamespace
from comparo.tui.commands import COMMAND_CATALOG
from comparo.tui.components import COMPONENT_STYLESHEET
from comparo.tui.components import CommandFooter
from comparo.tui.components import CompareAdvancedOptions
from comparo.tui.components import CompareDraftForm
from comparo.tui.components import CompareModeSelector
from comparo.tui.components import DiagnosticsList
from comparo.tui.components import ExecutionProgress
from comparo.tui.components import FailFastChoice
from comparo.tui.components import PlanPreview
from comparo.tui.components import ProjectResourceDetail
from comparo.tui.components import ProjectResourceItem
from comparo.tui.components import ProjectResourceList
from comparo.tui.components import ReportList
from comparo.tui.components import ReportListItem
from comparo.tui.components import ResultDetail
from comparo.tui.components import ResultIndex
from comparo.tui.components import ResultIndexKind
from comparo.tui.components import ResultIndexSelector
from comparo.tui.components import ResultsWorkspaceView
from comparo.tui.components import StatePanel
from comparo.tui.components import StatePanelMode
from comparo.tui.components import StatusBadge
from comparo.tui.components import status_for_outcome
from comparo.tui.components.compare_input import fail_fast_choice
from comparo.tui.components.compare_input import format_budgets
from comparo.tui.components.compare_input import format_matrices
from comparo.tui.components.compare_input import format_selections
from comparo.tui.components.compare_input import format_value_resources
from comparo.tui.components.compare_input import parse_compare_draft
from comparo.tui.components.compare_input import validation_diagnostics
from comparo.tui.components.results import ResultIndexItem
from comparo.tui.contracts import CompareDraft
from comparo.tui.contracts import CompareDraftValidationCode
from comparo.tui.contracts import CompareMode
from comparo.tui.contracts import ResultAccess
from comparo.tui.contracts import TuiInteractionLimits
from comparo.tui.contracts import tui_interaction_limits_defaults_v1
from comparo.tui.design import LayoutClass
from comparo.tui.design import SemanticStatus
from comparo.tui.workspaces import CompareWorkspace

_INTERACTION_LIMITS = tui_interaction_limits_defaults_v1()


def _run_async[**P](
    function: Callable[P, Coroutine[object, object, None]],
) -> Callable[P, None]:
    """Adapt one async Textual test to the repository's synchronous runner."""

    @wraps(function)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
        asyncio.run(function(*args, **kwargs))

    return wrapper


class _WidgetApp(App[None]):
    CSS_PATH: ClassVar[str | PurePath | list[str | PurePath] | None] = COMPONENT_STYLESHEET

    def __init__(self, *widgets: Widget) -> None:
        super().__init__()
        self._widgets = widgets

    @override
    def compose(self) -> ComposeResult:
        with Vertical():
            yield from self._widgets


class _DraftApp(App[None]):
    CSS_PATH: ClassVar[str | PurePath | list[str | PurePath] | None] = COMPONENT_STYLESHEET

    def __init__(self, form: CompareDraftForm) -> None:
        super().__init__()
        self.form = form
        self.changed: CompareDraft | None = None
        self.submitted: CompareDraft | None = None

    @override
    def compose(self) -> ComposeResult:
        yield self.form

    @on(CompareDraftForm.CompareDraftChanged)
    def record_change(self, message: CompareDraftForm.CompareDraftChanged) -> None:
        self.changed = message.draft

    @on(CompareDraftForm.Submitted)
    def record_submission(self, message: CompareDraftForm.Submitted) -> None:
        self.submitted = message.draft


def _resource(kind: str, resource_id: str, *, name: str | None = None) -> ProjectResourceSummary:
    return ProjectResourceSummary(
        coordinate=ResourceCoordinate(
            namespace="local",
            kind=kind,
            resource_id=resource_id,
            fingerprint="a" * 64,
        ),
        name=name,
        description=None,
        tags=(),
        source=SourceCoordinate(
            source="project.yaml",
            document=1,
            line=1,
            column=1,
        ),
    )


def _resources() -> tuple[ProjectResourceSummary, ...]:
    return (
        _resource("Request", "ping"),
        _resource("Scenario", "checkout"),
        _resource("Environment", "staging"),
        _resource("Environment", "prod"),
        _resource("ExecutionProfile", "release"),
    )


def _projection() -> ResultProjection:
    project = ResultResource(
        namespace="local",
        kind="Project",
        resource_id="demo",
        fingerprint="a" * 64,
    )
    subject = ResultResource(
        namespace="local",
        kind="Request",
        resource_id="ping",
        fingerprint="b" * 64,
    )
    staging = ResultResource(
        namespace="local",
        kind="Environment",
        resource_id="staging",
        fingerprint="c" * 64,
    )
    rule = ResultResource(
        namespace="local",
        kind="AssertionRule",
        resource_id="status-ok",
        fingerprint="d" * 64,
    )
    execution_reference = ResultReference(
        entity=ResultEntity.EXECUTION,
        result_id="execution-1",
    )
    assertion_reference = ResultReference(
        entity=ResultEntity.ASSERTION,
        result_id="assertion-1",
    )
    comparison_reference = ResultReference(
        entity=ResultEntity.COMPARISON,
        result_id="comparison-1",
    )
    diff_reference = ResultReference(
        entity=ResultEntity.DIFF,
        result_id="diff-1",
    )
    assertion = ResultFact(
        reference=assertion_reference,
        owner=execution_reference,
        engine="assertion",
        rule=rule,
        rule_id="status",
        effective_rule_id="status-ok",
        severity="blocking",
        outcome="satisfied",
        counts=(),
        coverage=(),
        evidence=(
            ResultEvidence(
                fact_id="assertion-1",
                outcome="satisfied",
                location="response.status",
                values=(),
            ),
        ),
        evidence_omitted=0,
        issues=(),
    )
    diff = ResultFact(
        reference=diff_reference,
        owner=comparison_reference,
        engine="diff",
        rule=rule,
        rule_id="shape",
        effective_rule_id="shape",
        severity="advisory",
        outcome="equivalent",
        counts=(),
        coverage=(),
        evidence=(),
        evidence_omitted=0,
        issues=(),
    )
    execution = ExecutionResultRow(
        ordinal=0,
        reference=execution_reference,
        subject=subject,
        environment=staging,
        cell_id="cell-1",
        trial_id="trial-1",
        coordinates=(),
        outcome="observed",
        failure=None,
        timing=ResultTimingSummary(
            measurements=(
                ResultTiming(name="total", nanoseconds=5, reason=None),
                ResultTiming(name="dns", nanoseconds=None, reason="not-recorded"),
            ),
            attempts=1,
            exchanges=1,
        ),
        assertions=(assertion_reference,),
        comparisons=(comparison_reference,),
    )
    comparison = ComparisonResultRow(
        ordinal=0,
        reference=comparison_reference,
        subject=subject,
        cell_id="cell-1",
        trial_id="trial-1",
        outcome="evaluated",
        baseline=execution_reference,
        candidate=execution_reference,
        unavailable=(),
        diffs=(diff_reference,),
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
            executions=(ResultMetric(name="observed", value=1),),
            comparisons=(),
            assertions=(ResultMetric(name="satisfied", value=1),),
            diffs=(),
            coverage_issues=0,
        ),
        executions=(execution,),
        assertions=(assertion,),
        comparisons=(comparison,),
        diffs=(diff,),
        links=(
            ResultCrossLink(
                source=execution_reference,
                relation=ResultRelation.ASSERTION,
                target=assertion_reference,
            ),
            ResultCrossLink(
                source=comparison_reference,
                relation=ResultRelation.BASELINE,
                target=execution_reference,
            ),
            ResultCrossLink(
                source=comparison_reference,
                relation=ResultRelation.DIFF,
                target=diff_reference,
            ),
        ),
    )


def _plan() -> PlanInspection:
    cardinality = PlanCardinalityInspection(
        cells=1,
        trials=1,
        environment_executions=2,
        comparisons=1,
        maximum_attempts=2,
    )
    return PlanInspection(
        api_version="comparo.plan/v1alpha1",
        run_id="run-1",
        invocation_id="invocation-1",
        fingerprint="e" * 64,
        ordering="canonical",
        policy=ResourceCoordinate(
            namespace="local",
            kind="ExecutionPolicy",
            resource_id="ci",
            fingerprint="f" * 64,
        ),
        cardinality=cardinality,
        slices=(
            PlanSliceInspection(
                subject=_resource("Request", "ping").coordinate,
                matrices=(),
                trials=1,
                environments=(
                    _resource("Environment", "staging").coordinate,
                    _resource("Environment", "prod").coordinate,
                ),
                comparisons=(
                    EnvironmentPairInspection(
                        baseline=_resource("Environment", "staging").coordinate,
                        candidate=_resource("Environment", "prod").coordinate,
                    ),
                ),
                maximum_attempts_per_execution=2,
                cardinality=cardinality,
            ),
        ),
        budgets=(),
        provenance=(),
    )


def _progress(*, finished: int = 0) -> ExecutionProgressSnapshot:
    return ExecutionProgressSnapshot(
        revision=finished,
        run_id="run-1",
        phase=ExecutionSessionPhase.RUNNING,
        total_executions=2,
        total_comparisons=1,
        queued_executions=2,
        started_executions=finished,
        finished_executions=finished,
        finished_comparisons=0,
    )


def _parse_draft(
    *,
    fallback: CompareDraft | None = None,
    limits: TuiInteractionLimits = _INTERACTION_LIMITS,
    mode: CompareMode = CompareMode.EXECUTION_PROFILE,
    subjects: str = "",
    environments: str = "",
    baseline: str = "",
    candidates: str = "",
    execution_profile: str = "release",
    matrices: str = "",
    trials: str = "",
    maximum_attempts: str = "",
    fail_fast: FailFastChoice = FailFastChoice.INHERIT,
    budgets: str = "",
    parameters: str = "",
    variables: str = "",
    secrets: str = "",
    approve_mutations: bool = False,
) -> CompareDraft:
    return parse_compare_draft(
        CompareDraft(mode=mode) if fallback is None else fallback,
        limits=limits,
        mode=mode,
        subjects=subjects,
        environments=environments,
        baseline=baseline,
        candidates=candidates,
        execution_profile=execution_profile,
        matrices=matrices,
        trials=trials,
        maximum_attempts=maximum_attempts,
        fail_fast=fail_fast,
        budgets=budgets,
        parameters=parameters,
        variables=variables,
        secrets=secrets,
        approve_mutations=approve_mutations,
    )


@_run_async
@pytest.mark.parametrize(
    ("width", "expected"),
    [(80, LayoutClass.COMPACT), (120, LayoutClass.STANDARD), (160, LayoutClass.WIDE)],
)
async def test_result_workspace_uses_shared_breakpoints(width: int, expected: LayoutClass) -> None:
    component = ResultsWorkspaceView(
        _projection(),
        access=ResultAccess.LIVE,
    )
    app = _WidgetApp(component)

    async with app.run_test(size=(width, 36)) as pilot:
        await pilot.pause()
        assert component.layout_class is expected
        assert component.has_class(f"layout-{expected.value}")


@_run_async
@pytest.mark.parametrize(
    ("mode", "visible", "hidden"),
    [
        (
            CompareMode.ASSERTIONS,
            ("compare-field-subjects", "compare-field-environments"),
            ("compare-field-baseline", "compare-field-candidates", "compare-field-profile"),
        ),
        (
            CompareMode.DIFFERENCES,
            ("compare-field-subjects", "compare-field-baseline", "compare-field-candidates"),
            ("compare-field-environments", "compare-field-profile"),
        ),
        (
            CompareMode.EXECUTION_PROFILE,
            (
                "compare-field-subjects",
                "compare-field-environments",
                "compare-field-baseline",
                "compare-field-candidates",
                "compare-field-profile",
            ),
            (),
        ),
    ],
)
async def test_compare_draft_form_supports_all_modes(
    mode: CompareMode,
    visible: tuple[str, ...],
    hidden: tuple[str, ...],
) -> None:
    form = CompareDraftForm(
        CompareDraft(mode=mode),
        limits=_INTERACTION_LIMITS,
        resources=_resources(),
    )
    app = _DraftApp(form)

    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        draft = form.current_draft()
        assert draft.mode is mode
        assert draft.subjects == (() if mode is CompareMode.EXECUTION_PROFILE else ("ping",))
        assert draft.environments == (("staging",) if mode is CompareMode.ASSERTIONS else ())
        assert draft.baseline == ("staging" if mode is CompareMode.DIFFERENCES else None)
        assert draft.candidates == (("prod",) if mode is CompareMode.DIFFERENCES else ())
        assert draft.execution_profile == (
            "release" if mode is CompareMode.EXECUTION_PROFILE else None
        )
        assert all(form.query_one(f".{name}").display for name in visible)
        assert all(not form.query_one(f".{name}").display for name in hidden)


@_run_async
async def test_compare_draft_form_emits_typed_changes_and_submission() -> None:
    form = CompareDraftForm(
        CompareDraft(mode=CompareMode.ASSERTIONS),
        limits=_INTERACTION_LIMITS,
        resources=_resources(),
    )
    app = _DraftApp(form)

    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        subject = form.query_one("#compare-draft-subjects", Input)
        subject.value = "checkout"
        await pilot.pause()
        assert app.changed is not None
        assert app.changed.subjects == ("checkout",)

        await pilot.click("#compare-mode-differences")
        await pilot.pause()
        assert form.current_draft().mode is CompareMode.DIFFERENCES
        assert form.query_one(".compare-field-baseline").display

        form.query_one("#compare-draft-baseline", Input).focus()
        await pilot.press("enter")
        await pilot.pause()
        assert app.submitted == form.current_draft()


@_run_async
async def test_untrusted_text_is_contained_across_project_and_report_components() -> None:
    canary = "unsafe\x1b[31m\u202evalue"
    resource = _resource("Request", "safe-id", name=canary)
    report = ReportArchiveEntry(
        bundle_id="bundle",
        run_id=canary,
        invocation_id="invocation",
        created_unix_nanoseconds=1,
        completion="complete",
        gate="passed",
        subjects=(canary,),
        environments=("staging",),
        executions=1,
        comparisons=0,
        assertions=0,
        diffs=0,
        blobs=0,
        blob_bytes=0,
        coverage_issues=0,
    )
    app = _WidgetApp(
        ProjectResourceList((resource,)),
        ProjectResourceDetail(resource),
        ReportList((report,)),
    )

    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        screenshot = app.export_screenshot(simplify=True)
        assert "\x1b" not in screenshot
        assert "\u202e" not in screenshot
        assert "unsafe" in screenshot


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("COMPLETE", SemanticStatus.SUCCESS),
        ("warning", SemanticStatus.ADVISORY),
        ("drifted", SemanticStatus.FAILURE),
        ("unavailable", SemanticStatus.ERROR),
        ("cancelled", SemanticStatus.CANCELLED),
        ("running", SemanticStatus.RUNNING),
        ("preparing", SemanticStatus.QUEUED),
        ("unknown", SemanticStatus.NEUTRAL),
    ],
)
def test_status_outcomes_use_the_shared_semantic_vocabulary(
    outcome: str,
    expected: SemanticStatus,
) -> None:
    assert status_for_outcome(outcome) is expected


def test_unmounted_component_contracts_validate_and_preserve_state() -> None:
    draft = CompareDraft(mode=CompareMode.ASSERTIONS, subjects=("ping",))
    form = CompareDraftForm(draft, limits=_INTERACTION_LIMITS)
    assert form.current_draft() == draft
    form.show_draft(CompareDraft(mode=CompareMode.DIFFERENCES))
    assert form.current_draft().mode is CompareMode.DIFFERENCES

    selector = CompareModeSelector(CompareMode.ASSERTIONS)
    selector.show_mode(CompareMode.DIFFERENCES)
    assert selector.mode is CompareMode.DIFFERENCES
    selector.select_mode(Button.Pressed(Button("Unrelated")))
    selector.select_mode(Button.Pressed(Button("Unrelated", id="unrelated")))

    progress = ExecutionProgress(_progress())
    progress.show_snapshot(_progress(finished=1))
    assert PlanPreview(None).render()
    assert ProjectResourceDetail(None).render()
    compare_workspace = CompareWorkspace()
    compare_workspace.show_starting()
    compare_workspace.show_starting()
    assert compare_workspace.state.value == "running"
    with pytest.raises(TypeError, match="interaction limits"):
        CompareWorkspace(interaction_limits=cast("TuiInteractionLimits", object()))

    with pytest.raises(TypeError, match="state panel mode"):
        StatePanel(mode=cast("StatePanelMode", object()))
    panel = StatePanel()
    with pytest.raises(TypeError, match="state panel mode"):
        panel.show_state(cast("StatePanelMode", object()))
    with pytest.raises(TypeError, match="SemanticStatus"):
        StatusBadge(cast("SemanticStatus", object()))
    with pytest.raises(TypeError, match="CompareMode"):
        CompareModeSelector(cast("CompareMode", object()))
    with pytest.raises(TypeError, match="CompareMode"):
        selector.show_mode(cast("CompareMode", object()))
    with pytest.raises(TypeError, match="CompareDraft"):
        CompareDraftForm(cast("CompareDraft", object()), limits=_INTERACTION_LIMITS)
    with pytest.raises(TypeError, match="TuiInteractionLimits"):
        CompareDraftForm(
            draft,
            limits=cast("TuiInteractionLimits", object()),
        )
    with pytest.raises(TypeError, match="safe summaries"):
        CompareDraftForm(
            draft,
            limits=_INTERACTION_LIMITS,
            resources=cast("tuple[ProjectResourceSummary, ...]", []),
        )
    with pytest.raises(TypeError, match="safe summaries"):
        CompareDraftForm(
            draft,
            limits=_INTERACTION_LIMITS,
            resources=(cast("ProjectResourceSummary", object()),),
        )
    with pytest.raises(TypeError, match="CompareDraft"):
        form.show_draft(cast("CompareDraft", object()))
    with pytest.raises(TypeError, match="result index kind"):
        ResultIndex(_projection(), kind=cast("ResultIndexKind", object()))
    with pytest.raises(TypeError, match="ResultIndexKind"):
        ResultIndexSelector(cast("ResultIndexKind", object()))
    result_selector = ResultIndexSelector(ResultIndexKind.EXECUTIONS)
    assert result_selector.kind is ResultIndexKind.EXECUTIONS
    unmounted_results = ResultsWorkspaceView(
        _projection(),
        access=ResultAccess.LIVE,
    )
    unmounted_results.follow_reference(_projection().diffs[0].reference)
    assert unmounted_results.current_index is ResultIndexKind.DIFFS
    selected_before_clear: object = unmounted_results.selected_reference
    assert selected_before_clear == _projection().diffs[0].reference
    unmounted_results.clear_reference()
    selected_after_clear: object = unmounted_results.selected_reference
    assert selected_after_clear is None
    with pytest.raises(TypeError, match="ResultReference"):
        ResultsWorkspaceView(
            _projection(),
            access=ResultAccess.LIVE,
        ).follow_reference(cast("ResultReference", object()))


@_run_async
async def test_shared_state_status_diagnostics_and_footer_cover_all_presentations() -> None:
    content = Static("stable", markup=False)
    state = StatePanel(content, mode=StatePanelMode.LOADING, message="unsafe\x1b[31m")
    badge = StatusBadge(SemanticStatus.NEUTRAL)
    diagnostics = DiagnosticsList(
        (
            ApplicationDiagnostic(
                code="one",
                severity="error",
                stage="configuration",
                source=SourceCoordinate(source="project.yaml", document=1, line=2, column=3),
                path=("spec", "request"),
            ),
            ApplicationDiagnostic(code="two", severity="warning", stage="planning"),
            ApplicationDiagnostic(code="three", severity="info", stage="runtime"),
        )
    )
    footer = CommandFooter(COMMAND_CATALOG)
    app = _WidgetApp(state, badge, diagnostics, footer)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        identity = id(content)
        for mode in StatePanelMode:
            state.show_state(mode, f"state {mode.value}")
            await pilot.pause()
            assert state.mode is mode
            assert id(content) == identity
            assert state.has_class(f"state-{mode.value}")
        badge.set_status(SemanticStatus.SUCCESS, detail="safe\x1b[31m")
        assert badge.status is SemanticStatus.SUCCESS
        assert len(diagnostics.query(StatusBadge)) == 3
        assert 0 < len(footer.query(Static)) < len(COMMAND_CATALOG)


@_run_async
async def test_form_updates_plan_and_progress_without_replacing_shared_controls() -> None:
    form = CompareDraftForm(
        CompareDraft(mode=CompareMode.ASSERTIONS),
        limits=_INTERACTION_LIMITS,
        resources=(),
    )
    preview = PlanPreview(None)
    progress = ExecutionProgress(_progress())
    app = _WidgetApp(form, preview, progress)

    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        assert form.query_one("#compare-draft-subjects", Input).placeholder == "Type a resource id"
        subject = form.query_one("#compare-draft-subjects", Input)
        subject.value = "   "
        assert form.current_draft().subjects == ()

        replacement = CompareDraft(
            mode=CompareMode.EXECUTION_PROFILE,
            execution_profile="release",
        )
        form.show_draft(replacement)
        await pilot.pause()
        assert form.current_draft() == replacement
        selector = form.query_one(CompareModeSelector)
        selector.show_mode(CompareMode.ASSERTIONS)
        assert selector.mode is CompareMode.ASSERTIONS

        preview.show_plan(_plan())
        assert "run-1" in str(preview.render())
        progress.show_snapshot(_progress(finished=1))
        await pilot.pause()


@_run_async
async def test_project_and_report_lists_emit_only_typed_safe_identifiers() -> None:
    resource = _resource("Request", "ping")
    entry = ReportArchiveEntry(
        bundle_id="bundle",
        run_id="run",
        invocation_id="invocation",
        created_unix_nanoseconds=1,
        completion="complete",
        gate="passed",
        subjects=("ping",),
        environments=(),
        executions=1,
        comparisons=0,
        assertions=0,
        diffs=0,
        blobs=0,
        blob_bytes=0,
        coverage_issues=0,
    )
    resources = ProjectResourceList((resource,))
    reports = ReportList((entry,))
    app = _WidgetApp(resources, reports)

    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        resource_item = resources.query_one(ProjectResourceItem)
        resources.show_resource(resource)
        resources.show_resource(_resource("Request", "missing"))
        with pytest.raises(TypeError, match="selection"):
            resources.show_resource(cast("ProjectResourceSummary", object()))
        resources.on_list_view_selected(ListView.Selected(resources, resource_item, 0))
        resources.on_list_view_selected(ListView.Selected(resources, ListItem(), 0))
        report_item = reports.query_one(ReportListItem)
        reports.on_list_view_selected(ListView.Selected(reports, report_item, 0))
        reports.on_list_view_selected(ListView.Selected(reports, ListItem(), 0))
        await pilot.pause()


@_run_async
async def test_every_result_index_and_detail_path_reuses_one_navigation_contract() -> None:
    projection = _projection()
    indexes = tuple(ResultIndex(projection, kind=kind) for kind in ResultIndexKind)
    detail = ResultDetail(projection)
    view = ResultsWorkspaceView(projection, access=ResultAccess.LIVE)
    app = _WidgetApp(*indexes, detail, view)

    async with app.run_test(size=(160, 52)) as pilot:
        await pilot.pause()
        for index in indexes:
            item = index.query_one(ResultIndexItem)
            index.on_list_view_selected(ListView.Selected(index, item, 0))
            index.on_list_view_selected(ListView.Selected(index, ListItem(), 0))

        references = (
            projection.executions[0].reference,
            projection.assertions[0].reference,
            projection.diffs[0].reference,
            projection.comparisons[0].reference,
            ResultReference(entity=ResultEntity.EXECUTION, result_id="missing"),
        )
        for reference in references:
            detail.show_reference(reference)
        detail.show_reference(None)
        view.show_selected_result(ResultIndex.ResultSelected(references[0]))
        view.clear_reference()
        assert view.selected_reference is None
        await pilot.pause()


def test_compare_input_adapter_builds_every_typed_application_override() -> None:
    draft = _parse_draft(
        subjects="ping, health",
        baseline="staging",
        candidates="prod, canary",
        matrices="region=regions, tenant=tenants",
        trials="3",
        maximum_attempts="2",
        fail_fast=FailFastChoice.DISABLED,
        budgets="concurrency=4, attempts=10",
        parameters="Value/params, staging=Value/staging-params",
        variables="Value/variables",
        secrets="prod=Value/prod-secrets",
        approve_mutations=True,
    )

    assert draft.validation_code is None
    assert draft.subjects == ("ping", "health")
    assert draft.candidates == ("prod", "canary")
    assert draft.fail_fast is False
    assert draft.approve_mutations
    options = draft.planning_options()
    assert options.execution_profile == "release"
    assert options.matrices == (
        MatrixOverride(binding="region", matrix="regions"),
        MatrixOverride(binding="tenant", matrix="tenants"),
    )
    assert options.budgets == (
        BudgetOverride(resource=BudgetResource.CONCURRENCY, limit=4),
        BudgetOverride(resource=BudgetResource.ATTEMPTS, limit=10),
    )
    assert options.value_resources == (
        ValueResourceOverride(
            namespace=ValueNamespace.PARAMETERS,
            environment=None,
            resource="Value/params",
        ),
        ValueResourceOverride(
            namespace=ValueNamespace.PARAMETERS,
            environment="staging",
            resource="Value/staging-params",
        ),
        ValueResourceOverride(
            namespace=ValueNamespace.VARIABLES,
            environment=None,
            resource="Value/variables",
        ),
        ValueResourceOverride(
            namespace=ValueNamespace.SECRETS,
            environment="prod",
            resource="Value/prod-secrets",
        ),
    )


def test_compare_input_capacity_is_caller_owned_instead_of_a_hidden_project_cap() -> None:
    permissive = TuiInteractionLimits(
        maximum_input_characters=4096,
        maximum_input_items=100,
        maximum_navigation_frames_per_workspace=2,
    )
    subjects = ", ".join(f"request-{index}" for index in range(96))
    accepted = _parse_draft(limits=permissive, subjects=subjects)
    assert accepted.validation_code is None
    assert len(accepted.subjects) == 96

    item_limited = TuiInteractionLimits(
        maximum_input_characters=4096,
        maximum_input_items=2,
        maximum_navigation_frames_per_workspace=2,
    )
    assert (
        _parse_draft(limits=item_limited, subjects="one, two, three").validation_code
        is CompareDraftValidationCode.INVALID_LIMIT
    )
    character_limited = TuiInteractionLimits(
        maximum_input_characters=8,
        maximum_input_items=100,
        maximum_navigation_frames_per_workspace=2,
    )
    assert (
        _parse_draft(limits=character_limited, subjects="ninechars").validation_code
        is CompareDraftValidationCode.INVALID_LIMIT
    )


@pytest.mark.parametrize(
    ("draft", "code"),
    [
        (_parse_draft(subjects="one,,two"), CompareDraftValidationCode.INVALID_SELECTION),
        (_parse_draft(subjects="x" * 64), CompareDraftValidationCode.INVALID_SELECTION),
        (_parse_draft(subjects="x" * 513), CompareDraftValidationCode.INVALID_SELECTION),
        (_parse_draft(baseline="one,two"), CompareDraftValidationCode.INVALID_SELECTION),
        (_parse_draft(matrices="region"), CompareDraftValidationCode.INVALID_MATRIX),
        (_parse_draft(matrices="=regions"), CompareDraftValidationCode.INVALID_MATRIX),
        (_parse_draft(trials="0"), CompareDraftValidationCode.INVALID_LIMIT),
        (_parse_draft(trials="many"), CompareDraftValidationCode.INVALID_LIMIT),
        (_parse_draft(budgets="unknown=1"), CompareDraftValidationCode.INVALID_BUDGET),
        (
            _parse_draft(parameters="private-canary==Value/params"),
            CompareDraftValidationCode.INVALID_VALUE_RESOURCE,
        ),
        (
            _parse_draft(
                environments="staging",
                baseline="staging",
                candidates="prod",
            ),
            CompareDraftValidationCode.INVALID_COMBINATION,
        ),
        (
            _parse_draft(fail_fast=cast("FailFastChoice", object())),
            CompareDraftValidationCode.INVALID_COMBINATION,
        ),
        (
            _parse_draft(approve_mutations=cast("bool", 1)),
            CompareDraftValidationCode.INVALID_COMBINATION,
        ),
    ],
)
def test_invalid_compare_text_becomes_one_value_free_diagnostic(
    draft: CompareDraft,
    code: CompareDraftValidationCode,
) -> None:
    assert draft.validation_code is code
    diagnostics = validation_diagnostics(draft)
    assert diagnostics[0].code == f"tui.compare.input.{code.value}"
    assert "private-canary" not in repr(draft)
    assert "private-canary" not in repr(diagnostics)
    with pytest.raises(ValueError, match=code.value):
        draft.planning_options()


def test_compare_input_formatters_are_exact_and_bounded() -> None:
    matrices = (MatrixOverride(binding="region", matrix="regions"),)
    budgets = (BudgetOverride(resource=BudgetResource.CONCURRENCY, limit=4),)
    resources = (
        ValueResourceOverride(
            namespace=ValueNamespace.PARAMETERS,
            environment=None,
            resource="Value/params",
        ),
        ValueResourceOverride(
            namespace=ValueNamespace.PARAMETERS,
            environment="staging",
            resource="Value/staging-params",
        ),
    )
    assert format_selections(("ping", "health")) == "ping, health"
    assert format_matrices(matrices) == "region=regions"
    assert format_budgets(budgets) == "concurrency=4"
    assert format_value_resources(resources, ValueNamespace.PARAMETERS) == (
        "Value/params, staging=Value/staging-params"
    )
    assert fail_fast_choice(None) is FailFastChoice.INHERIT
    assert fail_fast_choice(True) is FailFastChoice.ENABLED
    assert fail_fast_choice(False) is FailFastChoice.DISABLED
    with pytest.raises(TypeError, match="typed draft and mode"):
        parse_compare_draft(
            cast("CompareDraft", object()),
            limits=_INTERACTION_LIMITS,
            mode=CompareMode.ASSERTIONS,
            subjects="",
            environments="",
            baseline="",
            candidates="",
            execution_profile="",
            matrices="",
            trials="",
            maximum_attempts="",
            fail_fast=FailFastChoice.INHERIT,
            budgets="",
            parameters="",
            variables="",
            secrets="",
            approve_mutations=False,
        )


@_run_async
async def test_advanced_compare_widget_round_trips_and_reports_safe_invalid_text() -> None:
    draft = _parse_draft(
        subjects="ping, health",
        baseline="staging",
        candidates="prod",
        matrices="region=regions",
        trials="3",
        maximum_attempts="2",
        fail_fast=FailFastChoice.ENABLED,
        budgets="concurrency=4",
        secrets="prod=Value/prod-secrets",
        approve_mutations=True,
    )
    form = CompareDraftForm(draft, limits=_INTERACTION_LIMITS)
    app = _DraftApp(form)

    async with app.run_test(size=(160, 52)) as pilot:
        await pilot.pause()
        assert form.current_draft() == draft
        advanced = form.query_one(CompareAdvancedOptions)
        assert advanced.fail_fast is FailFastChoice.ENABLED
        assert advanced.approve_mutations
        assert (
            form.query_one("#compare-draft-matrices", Input).max_length
            == _INTERACTION_LIMITS.maximum_input_characters
        )
        assert (
            form.query_one("#compare-draft-trials", Input).max_length
            == _INTERACTION_LIMITS.maximum_input_characters
        )

        form.query_one("#compare-draft-fail-fast", Select).value = FailFastChoice.DISABLED
        form.query_one("#compare-draft-approve-mutations", Switch).value = False
        form.query_one("#compare-draft-budgets", Input).value = "not-a-budget"
        await pilot.pause()
        invalid = form.current_draft()
        assert invalid.validation_code is CompareDraftValidationCode.INVALID_BUDGET
        assert form.validation_diagnostics[0].code == "tui.compare.input.invalid-budget"
        screenshot = app.export_screenshot(simplify=True)
        assert "tui.compare.input.invalid-budget" in screenshot


def test_unmounted_advanced_options_validate_and_retain_typed_defaults() -> None:
    draft = CompareDraft(
        mode=CompareMode.EXECUTION_PROFILE,
        fail_fast=False,
        approve_mutations=True,
    )
    advanced = CompareAdvancedOptions(draft, limits=_INTERACTION_LIMITS)
    assert advanced.fail_fast.value == "disabled"
    assert advanced.approve_mutations
    advanced.show_validation_code("safe")
    advanced.show_draft(CompareDraft(mode=CompareMode.ASSERTIONS))
    assert advanced.fail_fast.value == "inherit"
    with pytest.raises(TypeError, match="CompareDraft"):
        CompareAdvancedOptions(
            cast("CompareDraft", object()),
            limits=_INTERACTION_LIMITS,
        )
    with pytest.raises(TypeError, match="TuiInteractionLimits"):
        CompareAdvancedOptions(
            draft,
            limits=cast("TuiInteractionLimits", object()),
        )
    with pytest.raises(TypeError, match="CompareDraft"):
        advanced.show_draft(cast("CompareDraft", object()))


@_run_async
async def test_shared_result_view_explores_every_index_and_typed_cross_reference() -> None:
    projection = _projection()
    view = ResultsWorkspaceView(projection, access=ResultAccess.READ_ONLY)
    app = _WidgetApp(view)

    async with app.run_test(size=(160, 52)) as pilot:
        await pilot.pause()
        selector = view.query_one(ResultIndexSelector)
        selector.select_index(Button.Pressed(Button("Unrelated")))
        selector.select_index(Button.Pressed(Button("Unrelated", id="unrelated")))

        for kind in ResultIndexKind:
            await pilot.click(f"#result-index-{kind.value}")
            await pilot.pause()
            assert view.current_index is kind
            assert view.query_one(ResultIndex).kind is kind

        execution = projection.executions[0].reference
        view.follow_reference(execution)
        await pilot.pause()
        assert view.current_index.value == ResultIndexKind.EXECUTIONS.value
        assert "Related assertion: assertion/assertion-1" in str(
            view.query_one(ResultDetail).render()
        )

        comparison = projection.comparisons[0].reference
        view.follow_reference(comparison)
        await pilot.pause()
        assert view.current_index.value == ResultIndexKind.COMPARISONS.value
        detail_text = str(view.query_one(ResultDetail).render())
        assert "Related baseline: execution/execution-1" in detail_text
        assert "Related diff: diff/diff-1" in detail_text
