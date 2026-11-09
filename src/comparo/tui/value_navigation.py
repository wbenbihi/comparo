"""Standalone Value inspection coordinated independently of Compare navigation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.worker import Worker

from comparo.application.value_inspection import ValueInspectionField
from comparo.application.value_inspection import inspect_value
from comparo.tui.backend import RESOLVED_PROJECTION_LIMITS
from comparo.tui.models.resource import ResolvedResourceField
from comparo.tui.models.resource import ResolvedValueView
from comparo.tui.models.resource import ResourceDetail
from comparo.tui.models.resource import failed_resolved
from comparo.tui.models.resource import loaded_resolved
from comparo.tui.models.resource import loading_resolved
from comparo.tui.models.resource import resolution_environment

if TYPE_CHECKING:
    from comparo.tui.workspaces.explorer import ExplorerWorkspace


def value_field(field: ValueInspectionField) -> ResolvedResourceField:
    """Render only the application-owned public or masked projection."""
    node = field.node
    if node is None:
        return ResolvedResourceField(field.path, "<unavailable>", "warn")
    if node.masked:
        return ResolvedResourceField(field.path, "<masked>", "value.masked")
    value = node.preview
    if value is None:
        value = f"{node.kind.value} · {node.child_count} member(s)"
    elif node.truncated:
        value += "…"
    return ResolvedResourceField(field.path, value)


class ValueNavigation:
    """Resolve the selected Value without leaving it or choosing a consumer."""

    def __init__(self, workspace: ExplorerWorkspace) -> None:
        """Retain the workspace owning workers and exact revision checks."""
        self.workspace = workspace
        self.worker: Worker[None] | None = None

    def activate(self, detail: ResourceDetail) -> bool:
        """Start a standalone inspection for Values only."""
        if detail.entry.summary.coordinate.kind != "Value":
            return False
        if self.worker is not None:
            self.worker.cancel()
        self.workspace.detail = loading_resolved(detail)
        self.worker = self.workspace.run_worker(
            self._load(detail, self.workspace._resolution_generation),
            group="value-resolution",
            exclusive=True,
        )
        return True

    async def _load(self, detail: ResourceDetail, generation: int) -> None:
        backend = self.workspace.backend
        environment = resolution_environment(detail)
        result = await inspect_value(
            backend._workspace_request,
            detail.entry.summary.coordinate,
            environment,
            policy_path=backend.launch.policy,
            policy_limits=backend.launch.limits.policy_load,
            settings=backend.execution_settings,
            limits=RESOLVED_PROJECTION_LIMITS,
        )
        if not self.workspace._is_current_resolution(generation, detail.key):
            return
        current = self.workspace.detail
        if current is None or resolution_environment(current) != environment:
            return
        self.workspace.detail = (
            failed_resolved(current, result.diagnostics[0])
            if result.diagnostics
            else loaded_resolved(
                current,
                ResolvedValueView(
                    environment=environment,
                    fields=tuple(value_field(field) for field in result.fields),
                    incomplete=result.incomplete,
                ),
            )
        )
