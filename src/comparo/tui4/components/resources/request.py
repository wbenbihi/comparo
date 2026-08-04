"""Request overview with stable raw and masked-resolved HTTP anatomy."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar
from typing import override

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.widgets import Static
from textual.widgets import TabbedContent
from textual.widgets import TabPane

from comparo.tui4.components.badge import Badge
from comparo.tui4.components.badge import BadgeEmphasis
from comparo.tui4.components.badge import BadgeTone
from comparo.tui4.components.badge import badge_text
from comparo.tui4.components.separator import Separator
from comparo.tui4.components.typography import Hint
from comparo.tui4.models.explorer import ResourceEntry
from comparo.tui4.models.resource import InspectionPhase
from comparo.tui4.models.resource import ResolvedRequestTarget
from comparo.tui4.models.resource import ResolvedResourceField
from comparo.tui4.models.resource import ResourceOverviewMode
from comparo.tui4.models.resource import ResourcePresentation
from comparo.tui4.models.resource_presenters import RequestOverview
from comparo.tui4.models.resource_presenters import ResourceSetting
from comparo.tui4.models.resource_presenters import compiled_exact_fields
from comparo.tui4.models.resource_presenters import request_overview
from comparo.tui4.theme import style

from .base import BaseKindResourceDetails
from .shared import ResourceSettingList
from .shared import resolution_status

_REQUEST_TAB_URL = "request-url"
_REQUEST_TAB_QUERY = "request-query"
_REQUEST_TAB_HEADERS = "request-headers"
_REQUEST_TAB_COOKIES = "request-cookies"
_REQUEST_TAB_AUTH = "request-auth"
_REQUEST_TAB_BODY = "request-body"
_REQUEST_TAB_RUNTIME = "request-runtime"
_REQUEST_TAB_CONTRACTS = "request-contracts"
_REQUEST_TABS = frozenset(
    {
        _REQUEST_TAB_URL,
        _REQUEST_TAB_QUERY,
        _REQUEST_TAB_HEADERS,
        _REQUEST_TAB_COOKIES,
        _REQUEST_TAB_AUTH,
        _REQUEST_TAB_BODY,
        _REQUEST_TAB_RUNTIME,
        _REQUEST_TAB_CONTRACTS,
    }
)


class RequestTargetPill(Static):
    """One method-and-target unit with a canonical resolved URL when available."""

    def __init__(self, method: str, target: str, *, resolved: bool) -> None:
        """Render a method badge and route without imposing badge-length limits on URLs."""
        content = Text()
        content.append_text(
            badge_text(
                method,
                tone=_method_tone(method),
                emphasis=BadgeEmphasis.SOLID,
            )
        )
        target_style = "request.target.resolved" if resolved else "request.target.raw"
        content.append(f" {target} ", style(target_style))
        super().__init__(content, classes="request-target-pill")
        self.method = method
        self.target = target
        self.resolved = resolved
        self.tooltip = Text(target)


class RequestContextStrip(Horizontal):
    """Compact request target, streaming, and expansion facts for a Request."""

    def __init__(
        self,
        entry: ResourceEntry,
        overview: RequestOverview,
        resolved_target: ResolvedRequestTarget | None = None,
    ) -> None:
        """Build badges from the same safe projection used by the detail sections."""
        method = (
            resolved_target.method if resolved_target is not None else entry.facts.method or "HTTP"
        )
        target = (
            resolved_target.url
            if resolved_target is not None
            else entry.facts.route
            or (
                "absolute URL"
                if any(item.name == "url" for item in overview.routing)
                else "endpoint"
            )
        )
        children: list[Static] = [
            RequestTargetPill(
                method,
                target,
                resolved=resolved_target is not None,
            ),
        ]
        if any(item.name == "stream" for item in overview.behavior):
            children.append(
                Badge("stream", tone=BadgeTone.WARNING, classes="request-context-badge")
            )
        matrix_count = sum(len(item.details) for item in overview.expansion)
        if matrix_count:
            label = "1 matrix" if matrix_count == 1 else f"{matrix_count} matrices"
            children.append(Badge(label, tone=BadgeTone.ACTIVE, classes="request-context-badge"))
        super().__init__(*children, classes="request-context-strip")


def _method_tone(method: str) -> BadgeTone:
    """Map common HTTP method semantics to theme-independent badge tones."""
    normalized = method.upper()
    if normalized in {"GET", "HEAD", "OPTIONS"}:
        return BadgeTone.SUCCESS
    if normalized in {"POST", "PUT", "PATCH"}:
        return BadgeTone.WARNING
    if normalized == "DELETE":
        return BadgeTone.DANGER
    return BadgeTone.NEUTRAL


@dataclass(frozen=True)
class RequestTabGroup:
    """One optional heading and its complete setting collection inside a Request tab."""

    title: str | None
    settings: tuple[ResourceSetting, ...]


class RequestTabBody(Vertical):
    """Shared empty-state and section layout for every native Request tab pane."""

    def __init__(
        self,
        *groups: RequestTabGroup,
        empty: str,
        resolved: Mapping[tuple[str, ...], ResolvedResourceField],
    ) -> None:
        """Initialize one tab from immutable presentation groups."""
        super().__init__(classes="request-tab-body")
        self._groups = tuple(group for group in groups if group.settings)
        self._empty = empty
        self._resolved = resolved

    @override
    def compose(self) -> ComposeResult:
        """Render only populated groups or one stable semantic empty state."""
        if not self._groups:
            yield Hint(self._empty, classes="request-tab-empty")
            return
        for group in self._groups:
            if group.title is not None:
                yield Separator(label=group.title, align="left", classes="tight")
            yield ResourceSettingList(group.settings, empty="", resolved=self._resolved)


class RequestResourceDetails(BaseKindResourceDetails):
    """Complete Request routing, values, policies, expansion, and evaluation topology."""

    KINDS: ClassVar[frozenset[str]] = frozenset({"Request"})
    PRESENTATION: ClassVar[ResourcePresentation] = ResourcePresentation(
        common_representation_hints=True,
        visible_actions=frozenset(),
    )

    @override
    def compose(self) -> ComposeResult:
        """Render every Request field in stable native tabs for raw and resolved modes."""
        overview = request_overview(self.entry)
        resolved = self._resolved_fields()
        resolved_target = None
        if (
            self.resource.overview_mode is ResourceOverviewMode.RESOLVED
            and self.resource.resolved.phase is InspectionPhase.READY
            and self.resource.resolved.value is not None
        ):
            resolved_target = self.resource.resolved.value.request_target

        yield RequestContextStrip(self.entry, overview, resolved_target)
        if self.resource.overview_mode is ResourceOverviewMode.RESOLVED:
            yield resolution_status(
                self.resource,
                self.entry.summary.name or self.entry.summary.coordinate.resource_id,
            )
        with TabbedContent(
            initial=self._active_tab(),
            id="request-tabs",
            classes="request-tabs",
        ):
            yield TabPane(
                "URL",
                RequestTabBody(
                    RequestTabGroup(None, overview.routing),
                    RequestTabGroup("Parameters", overview.parameters),
                    empty="No HTTP routing fields are available.",
                    resolved=resolved,
                ),
                id=_REQUEST_TAB_URL,
            )
            yield TabPane(
                "Query",
                RequestTabBody(
                    RequestTabGroup(None, overview.query),
                    empty="No query parameters are declared.",
                    resolved=resolved,
                ),
                id=_REQUEST_TAB_QUERY,
            )
            yield TabPane(
                "Headers",
                RequestTabBody(
                    RequestTabGroup(None, overview.headers),
                    empty="No request headers are declared.",
                    resolved=resolved,
                ),
                id=_REQUEST_TAB_HEADERS,
            )
            yield TabPane(
                "Cookies",
                RequestTabBody(
                    RequestTabGroup(None, overview.cookies),
                    empty="No request cookies are declared.",
                    resolved=resolved,
                ),
                id=_REQUEST_TAB_COOKIES,
            )
            yield TabPane(
                "Auth",
                RequestTabBody(
                    RequestTabGroup(None, overview.authentication),
                    empty="No request authentication is declared.",
                    resolved=resolved,
                ),
                id=_REQUEST_TAB_AUTH,
            )
            yield TabPane(
                "Body",
                RequestTabBody(
                    RequestTabGroup(None, overview.payload),
                    empty="This Request has no body.",
                    resolved=resolved,
                ),
                id=_REQUEST_TAB_BODY,
            )
            yield TabPane(
                "Runtime",
                RequestTabBody(
                    RequestTabGroup(None, overview.behavior),
                    empty="No Request runtime overrides are declared.",
                    resolved=resolved,
                ),
                id=_REQUEST_TAB_RUNTIME,
            )
            yield TabPane(
                "Contracts",
                RequestTabBody(
                    RequestTabGroup("Matrix expansion", overview.expansion),
                    RequestTabGroup("Validation & comparison", overview.evaluation),
                    RequestTabGroup("Safety & output", overview.policies),
                    RequestTabGroup("Additional configuration", overview.additional),
                    empty="No execution contracts or policies are attached.",
                    resolved=resolved,
                ),
                id=_REQUEST_TAB_CONTRACTS,
            )

    def _active_tab(self) -> str:
        """Restore only a known Request tab, otherwise start with URL."""
        section = self.initial_section
        return section if section in _REQUEST_TABS else _REQUEST_TAB_URL

    @on(TabbedContent.TabActivated, "#request-tabs")
    def on_request_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Publish native tab selection so the host can retain it across recomposition."""
        event.stop()
        section = event.pane.id
        if section is not None and section in _REQUEST_TABS:
            self.select_section(section)

    def _resolved_fields(self) -> dict[tuple[str, ...], ResolvedResourceField]:
        """Index exact references and the masked resolver overlay without changing layout."""
        if self.resource.overview_mode is not ResourceOverviewMode.RESOLVED:
            return {}
        fields = {field.path: field for field in compiled_exact_fields(self.entry)}
        inspection = self.resource.resolved
        if inspection.phase is InspectionPhase.READY and inspection.value is not None:
            fields.update((field.path, field) for field in inspection.value.fields)
        return fields


__all__ = [
    "RequestContextStrip",
    "RequestResourceDetails",
    "RequestTabBody",
    "RequestTargetPill",
]
