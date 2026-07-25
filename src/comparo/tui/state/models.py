"""Immutable UI state independent from Textual widget lifetimes."""

from __future__ import annotations

from enum import StrEnum
from typing import final

import msgspec

from comparo.application.compare import ComparePreparationHandle
from comparo.application.execution_session import ExecutionProgressSnapshot
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import PlanInspection
from comparo.application.project_workspace import ProjectWorkspaceSnapshot
from comparo.application.report_archive import ReportArchiveListing
from comparo.application.results import ResultProjection
from comparo.tui.contracts.models import CompareDraft
from comparo.tui.navigation.routes import WorkspaceId


class ContentPhase(StrEnum):
    """Shared lifecycle for non-effectful workspace content."""

    IDLE = "idle"
    LOADING = "loading"
    READY = "ready"
    EMPTY = "empty"
    ERROR = "error"


class ComparePhase(StrEnum):
    """Closed interactive comparison lifecycle."""

    DRAFT = "draft"
    PREPARING = "preparing"
    PREVIEW = "preview"
    RUNNING = "running"
    CANCELLING = "cancelling"
    RESULTS = "results"
    ERROR = "error"


class ThemePreference(StrEnum):
    """TUI-only theme preference."""

    SYSTEM = "system"
    DARK = "dark"
    LIGHT = "light"


@final
class TuiPreferences(msgspec.Struct, frozen=True, kw_only=True):
    """Presentation preferences that never alter project execution semantics."""

    theme: ThemePreference = ThemePreference.SYSTEM
    default_workspace: WorkspaceId = WorkspaceId.EXPLORER
    dense: bool = False
    mouse: bool = True

    def __post_init__(self) -> None:
        """Reject project-facing or untyped preference values."""
        if type(self.theme) is not ThemePreference:
            raise TypeError("TUI theme preference must be typed")
        if type(self.default_workspace) is not WorkspaceId:
            raise TypeError("TUI default workspace must be typed")
        if type(self.dense) is not bool or type(self.mouse) is not bool:
            raise TypeError("TUI boolean preferences must be bool")


DEFAULT_TUI_PREFERENCES = TuiPreferences()


@final
class ExplorerViewState(msgspec.Struct, frozen=True, kw_only=True):
    """Safe Explorer state without compiled resource specifications."""

    phase: ContentPhase = ContentPhase.IDLE
    project: ProjectWorkspaceSnapshot | None = None
    diagnostics: tuple[ApplicationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        """Keep ready, error, and transient states structurally disjoint."""
        if type(self.phase) is not ContentPhase:
            raise TypeError("Explorer phase must be typed")
        if self.project is not None and type(self.project) is not ProjectWorkspaceSnapshot:
            raise TypeError("Explorer project must be a safe workspace snapshot")
        _validate_diagnostics(self.diagnostics)
        if self.phase is ContentPhase.READY and self.project is not None and not self.diagnostics:
            return
        if self.phase is ContentPhase.ERROR and self.project is None and self.diagnostics:
            return
        if (
            self.phase in {ContentPhase.IDLE, ContentPhase.LOADING, ContentPhase.EMPTY}
            and self.project is None
            and not self.diagnostics
        ):
            return
        raise ValueError("Explorer state does not match its phase")


@final
class CompareViewState(msgspec.Struct, frozen=True, kw_only=True):
    """One Compare workspace lifecycle shared across every execution topology."""

    phase: ComparePhase
    draft: CompareDraft
    handle: ComparePreparationHandle | None = None
    plan: PlanInspection | None = None
    progress: ExecutionProgressSnapshot | None = None
    results: ResultProjection | None = None
    diagnostics: tuple[ApplicationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        """Require exactly the products admitted by the active phase."""
        if type(self.phase) is not ComparePhase or type(self.draft) is not CompareDraft:
            raise TypeError("Compare phase and draft must be typed")
        if self.handle is not None and type(self.handle) is not ComparePreparationHandle:
            raise TypeError("Compare handle must be typed")
        if self.plan is not None and type(self.plan) is not PlanInspection:
            raise TypeError("Compare plan must be typed")
        if self.progress is not None and type(self.progress) is not ExecutionProgressSnapshot:
            raise TypeError("Compare progress must be typed")
        if self.results is not None and type(self.results) is not ResultProjection:
            raise TypeError("Compare results must be typed")
        _validate_diagnostics(self.diagnostics)
        if not _valid_compare_state(self):
            raise ValueError("Compare state does not match its phase")


@final
class ReportViewState(msgspec.Struct, frozen=True, kw_only=True):
    """Authenticated Report archive state and optional shared result projection."""

    phase: ContentPhase = ContentPhase.IDLE
    archive: ReportArchiveListing | None = None
    selected_bundle_id: str | None = None
    results: ResultProjection | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        """Keep listing, selection, and error states coherent and value-free."""
        if type(self.phase) is not ContentPhase:
            raise TypeError("Report phase must be typed")
        if self.archive is not None and type(self.archive) is not ReportArchiveListing:
            raise TypeError("Report archive must be typed")
        for value in (self.selected_bundle_id, self.error_code):
            if value is not None and (type(value) is not str or not value or len(value) > 128):
                raise ValueError("Report identifiers and errors must be bounded text or absent")
        if self.results is not None and type(self.results) is not ResultProjection:
            raise TypeError("Report results must be typed")
        if not _valid_report_state(self):
            raise ValueError("Report state does not match its phase")


def _valid_compare_state(state: CompareViewState) -> bool:
    empty = all(item is None for item in (state.handle, state.plan, state.progress, state.results))
    if state.phase in {ComparePhase.DRAFT, ComparePhase.PREPARING}:
        return empty and not state.diagnostics
    if state.phase is ComparePhase.PREVIEW:
        return (
            state.handle is not None
            and state.plan is not None
            and state.progress is None
            and state.results is None
            and not state.diagnostics
        )
    if state.phase in {ComparePhase.RUNNING, ComparePhase.CANCELLING}:
        return (
            state.handle is None
            and state.plan is not None
            and state.progress is not None
            and state.results is None
            and not state.diagnostics
        )
    if state.phase is ComparePhase.RESULTS:
        return (
            state.handle is None
            and state.plan is not None
            and state.results is not None
            and not state.diagnostics
        )
    return (
        state.handle is None
        and state.progress is None
        and state.results is None
        and bool(state.diagnostics)
    )


def _valid_report_state(state: ReportViewState) -> bool:
    if state.phase is ContentPhase.READY:
        valid = state.archive is not None and state.error_code is None
    elif state.phase is ContentPhase.EMPTY:
        valid = (
            state.archive is not None
            and not state.archive.entries
            and state.selected_bundle_id is None
            and state.results is None
            and state.error_code is None
        )
    elif state.phase is ContentPhase.ERROR:
        valid = state.archive is None and state.results is None and state.error_code is not None
    else:
        valid = (
            state.archive is None
            and state.selected_bundle_id is None
            and state.results is None
            and state.error_code is None
        )
    if state.results is not None:
        return valid and state.selected_bundle_id is not None
    return valid


def _validate_diagnostics(value: tuple[ApplicationDiagnostic, ...]) -> None:
    if type(value) is not tuple or any(type(item) is not ApplicationDiagnostic for item in value):
        raise TypeError("view diagnostics must be an immutable typed tuple")


__all__ = (
    "DEFAULT_TUI_PREFERENCES",
    "ComparePhase",
    "CompareViewState",
    "ContentPhase",
    "ExplorerViewState",
    "ReportViewState",
    "ThemePreference",
    "TuiPreferences",
)
