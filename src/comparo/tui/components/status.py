"""Shared status, diagnostic, and command-control components."""

from __future__ import annotations

from typing import final
from typing import override

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.containers import VerticalScroll
from textual.widgets import Static

from comparo.application.inspection import ApplicationDiagnostic
from comparo.tui.commands import CommandDescriptor
from comparo.tui.design import SemanticStatus
from comparo.tui.design import semantic_style
from comparo.tui.presentation import safe_terminal_text

_SUCCESS_OUTCOMES = frozenset(
    {"complete", "equivalent", "evaluated", "observed", "passed", "satisfied"}
)
_ADVISORY_OUTCOMES = frozenset({"not-evaluated", "partial", "skipped", "warning"})
_FAILURE_OUTCOMES = frozenset({"drifted", "failed", "violated"})
_ERROR_OUTCOMES = frozenset(
    {"blocked", "error", "incomplete", "internal-error", "rejected", "unavailable"}
)


def status_for_outcome(outcome: str) -> SemanticStatus:
    """Map an artifact or application outcome to the shared visual vocabulary."""
    normalized = outcome.casefold()
    if normalized in _SUCCESS_OUTCOMES:
        return SemanticStatus.SUCCESS
    if normalized in _ADVISORY_OUTCOMES:
        return SemanticStatus.ADVISORY
    if normalized in _FAILURE_OUTCOMES:
        return SemanticStatus.FAILURE
    if normalized in _ERROR_OUTCOMES:
        return SemanticStatus.ERROR
    if normalized == "cancelled":
        return SemanticStatus.CANCELLED
    if normalized == "running":
        return SemanticStatus.RUNNING
    if normalized in {"preparing", "queued"}:
        return SemanticStatus.QUEUED
    return SemanticStatus.NEUTRAL


@final
class StatusBadge(Static):
    """The single glyph, label, and class rendering for a semantic status."""

    def __init__(
        self,
        status: SemanticStatus,
        *,
        detail: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize a badge through the central semantic mapping."""
        super().__init__("", id=id, classes=classes, markup=False)
        self._status = SemanticStatus.NEUTRAL
        self._detail = detail
        self.set_status(status, detail=detail)

    @property
    def status(self) -> SemanticStatus:
        """Return the badge's current semantic state."""
        return self._status

    def set_status(self, status: SemanticStatus, *, detail: str | None = None) -> None:
        """Update one badge through the central semantic style catalog."""
        if type(status) is not SemanticStatus:
            raise TypeError("status badge requires SemanticStatus")
        old_style = semantic_style(self._status)
        style = semantic_style(status)
        self.remove_class(old_style.css_class)
        self.add_class(style.css_class)
        self._status = status
        self._detail = detail
        suffix = (
            "" if detail is None else f" · {safe_terminal_text(detail, maximum_characters=128)}"
        )
        self.update(f"{style.glyph} {style.label}{suffix}")


@final
class DiagnosticsList(VerticalScroll):
    """Consistent value-free diagnostic rows for load and preparation failures."""

    def __init__(
        self,
        diagnostics: tuple[ApplicationDiagnostic, ...],
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize rows from value-free application diagnostics."""
        super().__init__(id=id, classes=classes)
        self._diagnostics = diagnostics

    @override
    def compose(self) -> ComposeResult:
        """Render typed diagnostics with shared severity semantics."""
        for item in self._diagnostics:
            source = "" if item.source is None else f" · {item.source.source}:{item.source.line}"
            path = "" if not item.path else f" · {'.'.join(item.path)}"
            text = safe_terminal_text(
                f"{item.code} · {item.stage}{source}{path}",
                maximum_characters=512,
            )
            with Horizontal(classes="diagnostic-row"):
                yield StatusBadge(_diagnostic_status(item.severity))
                yield Static(text, classes="diagnostic-text", markup=False)


def _diagnostic_status(severity: str) -> SemanticStatus:
    if severity.casefold() == "error":
        return SemanticStatus.ERROR
    if severity.casefold() == "warning":
        return SemanticStatus.ADVISORY
    return SemanticStatus.NEUTRAL


@final
class CommandFooter(Horizontal):
    """Shared active-command hints sourced from the central command catalog."""

    def __init__(
        self,
        commands: tuple[CommandDescriptor, ...],
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize hints from active central command descriptors."""
        super().__init__(id=id, classes=classes)
        self._commands = tuple(item for item in commands if item.footer)

    @override
    def compose(self) -> ComposeResult:
        """Render command labels without declaring keys inside a screen."""
        for command in self._commands:
            key = safe_terminal_text(command.keys[0], maximum_characters=32)
            label = safe_terminal_text(command.label, maximum_characters=64)
            yield Static(f"{key} {label}", classes="command-hint", markup=False)


__all__ = (
    "CommandFooter",
    "DiagnosticsList",
    "StatusBadge",
    "status_for_outcome",
)
