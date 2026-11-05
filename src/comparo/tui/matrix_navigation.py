"""Latest-wins Matrix inspection coordination and value-free browser memory."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from comparo.application.value_inspection import inspect_value
from comparo.tui.backend import RESOLVED_PROJECTION_LIMITS
from comparo.tui.components.resources.matrix_browser import MatrixBrowser
from comparo.tui.components.resources.matrix_browser import MatrixCursor
from comparo.tui.models.resource import ResourceDetail
from comparo.tui.models.resource import ResourceOverviewMode
from comparo.tui.models.resource import ResourceRepresentation

if TYPE_CHECKING:
    from textual.worker import Worker

    from comparo.tui.workspaces.explorer import ExplorerWorkspace


class MatrixNavigation:
    """Keep case selections across Source/Graph while never retaining preview values."""

    def __init__(self, workspace: ExplorerWorkspace) -> None:
        """Keep inspection ownership and value-free positions scoped to Explorer."""
        self.workspace = workspace
        self.positions: dict[str, MatrixCursor] = {}
        self.browser: MatrixBrowser | None = None
        self.worker: Worker[None] | None = None
        self.generation = 0

    def activate(self, detail: ResourceDetail) -> bool:
        """Let the case browser own Matrix resolution and its explicit context."""
        if detail.entry.summary.coordinate.kind != "Matrix":
            return False
        self.workspace.detail = replace(
            detail,
            overview_mode=ResourceOverviewMode.RESOLVED,
            representation=ResourceRepresentation.OVERVIEW,
        )
        return True

    def ready(self, browser: MatrixBrowser) -> None:
        """Restore the selected Matrix case in the active inspection environment."""
        cursor = self.positions.get(browser.resource.key, MatrixCursor())
        browser.restore(cursor)
        self.changed(browser)

    def gone(self, browser: MatrixBrowser) -> None:
        """Cancel only the worker associated with this removed browser."""
        if self.browser is browser:
            self.generation += 1
            if self.worker is not None:
                self.worker.cancel()
            self.browser = None

    def changed(self, browser: MatrixBrowser) -> None:
        """Invalidate stale output before preparing the newly selected context."""
        detail = self.workspace.detail
        if (
            detail is None
            or detail.key != browser.resource.key
            or not browser.is_mounted
            or detail.execution_environment != browser.resource.execution_environment
            or detail.representation is not ResourceRepresentation.OVERVIEW
            or detail.overview_mode is not ResourceOverviewMode.RESOLVED
        ):
            return
        self.generation += 1
        generation = self.generation
        if self.worker is not None:
            self.worker.cancel()
        self.browser = browser
        self.positions[browser.resource.key] = browser.cursor
        if len(self.positions) > 50:
            del self.positions[next(iter(self.positions))]
        browser.show_loading()
        self.worker = self.workspace.run_worker(
            self._load(browser, generation), exclusive=True, group="matrix-browser"
        )

    async def _load(self, browser: MatrixBrowser, generation: int) -> None:
        backend = self.workspace.backend
        result = await inspect_value(
            backend._workspace_request,
            browser.resource.entry.summary.coordinate,
            browser.resource.execution_environment,
            policy_path=backend.launch.policy,
            policy_limits=backend.launch.limits.policy_load,
            settings=backend.execution_settings,
            limits=RESOLVED_PROJECTION_LIMITS,
            case_id=browser.cursor.case_id,
            start_case=browser.cursor.start,
        )
        detail = self.workspace.detail
        if (
            generation != self.generation
            or not browser.is_mounted
            or detail is None
            or detail.key != browser.resource.key
            or detail.execution_environment != browser.resource.execution_environment
        ):
            return
        browser.apply(result)
        self.positions[detail.key] = browser.cursor
