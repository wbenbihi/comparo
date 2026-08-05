"""Dedicated Scenario, execution, policy, and profile Explorer contracts."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import override

import pytest
from textual.app import App
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.pilot import Pilot
from textual.widgets import Static
from textual.widgets import TabbedContent

from comparo.application import ProjectWorkspaceRequest
from comparo.application import inspect_project_workspace
from comparo.core.domain.identity import ResourceKind
from comparo.tui4.app import ComparoApp
from comparo.tui4.backend import TuiBackend
from comparo.tui4.components.badge import Badge
from comparo.tui4.components.explorernavigator import ExplorerNavigator
from comparo.tui4.components.resources import resource_widget
from comparo.tui4.components.resources.data_profiles import ArtifactProfileResourceDetails
from comparo.tui4.components.resources.data_profiles import RedactionProfileResourceDetails
from comparo.tui4.components.resources.data_profiles import SafetyProfileResourceDetails
from comparo.tui4.components.resources.evaluation_profiles import AssertionProfileResourceDetails
from comparo.tui4.components.resources.evaluation_profiles import DiffProfileResourceDetails
from comparo.tui4.components.resources.evaluation_profiles import GateProfileResourceDetails
from comparo.tui4.components.resources.evaluation_profiles import RuleProfileRulesBody
from comparo.tui4.components.resources.execution_policy import ExecutionPolicyResourceDetails
from comparo.tui4.components.resources.execution_profile import ExecutionProfileResourceDetails
from comparo.tui4.components.resources.profile_shared import ResourceContextStrip
from comparo.tui4.components.resources.profile_shared import ResourceSettingsBody
from comparo.tui4.components.resources.registry import RESOURCE_WIDGETS
from comparo.tui4.components.resources.scenario import ScenarioResourceDetails
from comparo.tui4.components.resources.scenario import ScenarioStepsBody
from comparo.tui4.models.explorer import ExplorerCatalog
from comparo.tui4.models.explorer import ResourceEntry
from comparo.tui4.models.explorer import build_catalog
from comparo.tui4.models.remaining_resource_presenters import artifact_profile_overview
from comparo.tui4.models.remaining_resource_presenters import execution_profile_overview
from comparo.tui4.models.remaining_resource_presenters import gate_profile_overview
from comparo.tui4.models.remaining_resource_presenters import redaction_profile_overview
from comparo.tui4.models.remaining_resource_presenters import rule_profile_overview
from comparo.tui4.models.remaining_resource_presenters import safety_profile_overview
from comparo.tui4.models.remaining_resource_presenters import scenario_overview
from comparo.tui4.models.resource import InspectionPhase
from comparo.tui4.models.resource import ResourceOverviewMode
from comparo.tui4.models.resource import initial_detail
from comparo.tui4.models.resource_presenters import ResourceSetting
from comparo.tui4.models.resource_presenters import resolved_fields
from comparo.tui4.models.resource_presenters import resource_resolution_paths
from comparo.tui4.models.resource_presenters import resource_resolution_subject_id
from comparo.tui4.workspaces.explorer import ExplorerWorkspace

PROJECT = Path(__file__).parents[2] / "examples" / "tui-showcase"


def _catalog() -> ExplorerCatalog:
    result = inspect_project_workspace(ProjectWorkspaceRequest(config=PROJECT))
    catalog = build_catalog(result)
    assert catalog is not None
    return catalog


def _entry(catalog: ExplorerCatalog, kind: str, resource_id: str) -> ResourceEntry:
    return next(
        entry
        for entry in catalog.entries.values()
        if entry.summary.coordinate.kind == kind
        and entry.summary.coordinate.resource_id == resource_id
    )


def _detail_values(settings: tuple[ResourceSetting, ...]) -> tuple[str, ...]:
    values: list[str] = []
    for setting in settings:
        values.append(setting.value)
        values.extend(detail.value for detail in setting.details)
    return tuple(values)


async def _wait_until(pilot: Pilot[None], predicate: Callable[[], bool]) -> None:
    """Wait for worker-backed Explorer state with a deterministic ceiling."""
    for _attempt in range(200):
        if predicate():
            return
        await pilot.pause(0.01)
    pytest.fail("Explorer condition was not reached")


def test_registry_has_a_dedicated_widget_for_every_current_resource_kind() -> None:
    assert set(RESOURCE_WIDGETS) == {kind.value for kind in ResourceKind}
    assert RESOURCE_WIDGETS["ExecutionPolicy"] is ExecutionPolicyResourceDetails


def test_remaining_presenters_expose_semantics_without_rule_operands() -> None:
    catalog = _catalog()
    scenario = scenario_overview(_entry(catalog, "Scenario", "catalog-workflow"))
    execution = execution_profile_overview(
        _entry(catalog, "ExecutionProfile", "repeated-matrix-release")
    )
    assertion = rule_profile_overview(_entry(catalog, "AssertionProfile", "usable-response"))
    structured = rule_profile_overview(_entry(catalog, "AssertionProfile", "structured-response"))
    diff = rule_profile_overview(_entry(catalog, "DiffProfile", "equivalent-stream-content"))
    asymmetric = rule_profile_overview(_entry(catalog, "DiffProfile", "asymmetric-migration"))
    gate = gate_profile_overview(_entry(catalog, "GateProfile", "strict"))
    safety = safety_profile_overview(_entry(catalog, "SafetyProfile", "local-read-only"))
    redaction = redaction_profile_overview(_entry(catalog, "RedactionProfile", "demo-redaction"))
    artifact = artifact_profile_overview(_entry(catalog, "ArtifactProfile", "demo-artifacts"))

    assert tuple((step.id, step.request, step.after) for step in scenario.steps) == (
        ("check-health", "health", "start"),
        ("fetch-catalog", "catalog", "check-health"),
    )
    assert scenario.matrix_count == 2
    assert execution.authored_selection == "comparison fields"
    assert execution.declared_subject_count == 1
    assert execution.declared_environment_count == 2
    assert execution.declared_trials == 2
    assert {"2", "false"} <= set(_detail_values(execution.controls))
    assert assertion.rules[0].id == "status-is-200"
    assert assertion.rules[0].sides[0].selector == "response.status"
    assert assertion.rules[0].sides[0].pipeline == "equals"
    assert structured.rules[0].name_declared
    assert structured.rules[0].description_declared
    assert structured.rules[0].tags == ("headers", "contract")
    assert structured.rules[0].sides[0].selector == ("response.headers · field x-request-id")
    assert tuple(side.pipeline for side in diff.rules[0].sides) == (
        "streamProject → concatenate",
        "streamProject → concatenate",
    )
    assert diff.rules[0].combination == "derived pairing → exact"
    assert tuple(side.selector for side in asymmetric.rules[0].sides) == (
        "response.body · json · path declared",
        "response.body · json · path declared",
    )
    assert tuple(side.pipeline for side in asymmetric.rules[0].sides) == (
        "normalizeText",
        "identity",
    )
    assert asymmetric.rules[0].combination == "singular → exact"
    assert {"blocking", "baseline", "candidate", "other"} <= set(_detail_values(gate.scope))
    assert set(_detail_values(gate.requirements)) == {"true", "false"}
    assert {"public", "x-private"} <= set(_detail_values(safety.classification))
    assert {"private-response-header", "response.headers:x-private", "remove"} <= set(
        _detail_values(redaction.rules)
    )
    assert "true" in _detail_values(redaction.backstop)
    assert {"true", "false"} <= set(_detail_values(artifact.capture))
    assert {"always", "reject-artifact"} <= set(_detail_values(artifact.storage))
    serialized = repr((assertion, diff, redaction))
    assert "not-a-real-credential" not in serialized


def test_remaining_resource_widgets_mount_continuous_documents_without_tabs() -> None:
    catalog = _catalog()
    resources = (
        _entry(catalog, "Scenario", "catalog-workflow"),
        _entry(catalog, "ExecutionProfile", "repeated-matrix-release"),
        _entry(catalog, "AssertionProfile", "usable-response"),
        _entry(catalog, "DiffProfile", "equivalent-stream-content"),
        _entry(catalog, "GateProfile", "strict"),
        _entry(catalog, "SafetyProfile", "local-read-only"),
        _entry(catalog, "RedactionProfile", "demo-redaction"),
        _entry(catalog, "ArtifactProfile", "demo-artifacts"),
    )

    class RemainingKindsApp(App[None]):
        @override
        def compose(self) -> ComposeResult:
            with VerticalScroll():
                for entry in resources:
                    yield resource_widget(initial_detail(entry))

    async def inspect() -> None:
        app = RemainingKindsApp()
        async with app.run_test(size=(200, 100)):
            assert len(app.query(ScenarioResourceDetails)) == 1
            assert len(app.query(ExecutionProfileResourceDetails)) == 1
            assert len(app.query(AssertionProfileResourceDetails)) == 1
            assert len(app.query(DiffProfileResourceDetails)) == 1
            assert len(app.query(GateProfileResourceDetails)) == 1
            assert len(app.query(SafetyProfileResourceDetails)) == 1
            assert len(app.query(RedactionProfileResourceDetails)) == 1
            assert len(app.query(ArtifactProfileResourceDetails)) == 1
            assert len(app.query(ResourceContextStrip)) == len(resources)
            assert len(app.query(ScenarioStepsBody)) == 1
            assert len(app.query(RuleProfileRulesBody)) == 2
            assert len(app.query(ResourceSettingsBody)) >= 20
            assert len(app.query(TabbedContent)) == 0

    asyncio.run(inspect())


def test_execution_profile_resolution_uses_its_own_precedence_context() -> None:
    """Profile parameters resolve through ExecutionProfile intent, never Project defaults."""

    async def inspect() -> None:
        catalog = _catalog()
        profile = _entry(catalog, "ExecutionProfile", "repeated-matrix-release")
        environment = next(
            entry for entry in catalog.entries.values() if entry.facts.role == "baseline"
        )
        backend = TuiBackend(PROJECT)
        try:
            batch = await backend.inspect_resolved_fields(
                profile.summary.coordinate,
                environment.summary.coordinate,
                resource_resolution_paths(profile),
                subject_id=resource_resolution_subject_id(profile),
            )
        finally:
            await backend.shutdown()

        assert batch.diagnostics == ()
        assert batch.values is not None
        fields = {field.path: field.value for field in resolved_fields(batch.values).fields}
        assert fields[("parameters", "releaseLabel")] == "preview"

    asyncio.run(inspect())


def test_execution_profile_toggle_preserves_layout_and_masks_secret_values() -> None:
    """The shared r binding overlays resolved values without changing the document anatomy."""

    async def inspect() -> None:
        app = ComparoApp(PROJECT)
        async with app.run_test(size=(180, 70)) as pilot:
            navigator = app.query_one(ExplorerNavigator)
            await _wait_until(pilot, lambda: navigator.catalog is not None)
            catalog = navigator.catalog
            assert catalog is not None
            profile = _entry(catalog, "ExecutionProfile", "repeated-matrix-release")
            workspace = app.query_one(ExplorerWorkspace)
            workspace.selected = profile.key
            await _wait_until(
                pilot,
                lambda: (
                    workspace.detail is not None
                    and workspace.detail.key == profile.key
                    and len(app.query(ExecutionProfileResourceDetails)) == 1
                ),
            )
            raw = app.query_one(ExecutionProfileResourceDetails)
            raw_text = "\n".join(str(item.content) for item in raw.query(Static))
            raw_section_count = len(raw.query(ResourceSettingsBody))
            assert "demo-secret-never-use" not in raw_text

            await pilot.press("r")
            await _wait_until(
                pilot,
                lambda: (
                    workspace.detail is not None
                    and workspace.detail.overview_mode is ResourceOverviewMode.RESOLVED
                    and workspace.detail.resolved.phase is InspectionPhase.READY
                    and len(app.query(ExecutionProfileResourceDetails)) == 1
                ),
            )
            await pilot.pause()
            resolved = app.query_one(ExecutionProfileResourceDetails)
            resolved_text = "\n".join(str(item.content) for item in resolved.query(Static))
            assert "releaseLabel  preview" in resolved_text
            assert "releaseToken  <masked>" in resolved_text
            assert "demo-secret-never-use" not in resolved_text
            assert len(resolved.query(ResourceSettingsBody)) == raw_section_count

    asyncio.run(inspect())


def test_malformed_or_huge_authored_trial_counts_never_crash_badges(tmp_path: Path) -> None:
    """Context badges summarize only validated, bounded authored counts."""
    project = tmp_path / "showcase"
    shutil.copytree(PROJECT, project, ignore=shutil.ignore_patterns("artifacts"))
    source = project / ".comparo" / "executions.yaml"
    text = source.read_text()
    source.write_text(text.replace("  trials: 2", f"  trials: {int('9' * 80)}", 1))
    result = inspect_project_workspace(ProjectWorkspaceRequest(config=project))
    catalog = build_catalog(result)
    assert catalog is not None
    profile = _entry(catalog, "ExecutionProfile", "repeated-matrix-release")
    overview = execution_profile_overview(profile)
    assert overview.declared_trials == int("9" * 80)

    class MalformedCountApp(App[None]):
        @override
        def compose(self) -> ComposeResult:
            yield resource_widget(initial_detail(profile))

    async def inspect() -> None:
        app = MalformedCountApp()
        async with app.run_test(size=(180, 55)):
            labels = tuple(badge.spec.label for badge in app.query(Badge))
            assert "trials declared" in labels
            assert all(len(label) <= 32 for label in labels)

    asyncio.run(inspect())


def test_scenarios_have_a_stable_first_class_explorer_section() -> None:
    titles = tuple(section.title for section in _catalog().sections)
    assert titles == (
        "Environments",
        "Requests",
        "Scenarios",
        "Matrices",
        "Schemas",
        "Values",
        "Profiles",
    )
