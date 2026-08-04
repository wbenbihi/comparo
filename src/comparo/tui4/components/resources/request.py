"""Request overview with stable raw and masked-resolved HTTP anatomy."""

from typing import ClassVar
from typing import override

from textual.app import ComposeResult
from textual.containers import Horizontal

from comparo.tui4.components.badge import Badge
from comparo.tui4.components.badge import BadgeEmphasis
from comparo.tui4.components.badge import BadgeTone
from comparo.tui4.components.separator import Separator
from comparo.tui4.models.explorer import ResourceEntry
from comparo.tui4.models.resource import InspectionPhase
from comparo.tui4.models.resource import ResolvedResourceField
from comparo.tui4.models.resource import ResourceActionId
from comparo.tui4.models.resource import ResourceOverviewMode
from comparo.tui4.models.resource import ResourcePresentation
from comparo.tui4.models.resource_presenters import RequestOverview
from comparo.tui4.models.resource_presenters import compiled_exact_fields
from comparo.tui4.models.resource_presenters import request_overview

from .base import BaseKindResourceDetails
from .shared import ResourceSettingList
from .shared import resolution_status


class RequestContextStrip(Horizontal):
    """Compact method, routing, streaming, and expansion facts for a Request."""

    def __init__(self, entry: ResourceEntry, overview: RequestOverview) -> None:
        """Build badges from the same safe projection used by the detail sections."""
        method = entry.facts.method or "HTTP"
        route = (
            "absolute URL" if any(item.name == "url" for item in overview.routing) else "endpoint"
        )
        children = [
            Badge(
                method,
                tone=_method_tone(method),
                emphasis=BadgeEmphasis.SOLID,
                classes="request-context-badge",
            ),
            Badge(route, tone=BadgeTone.INFO, classes="request-context-badge"),
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


class RequestResourceDetails(BaseKindResourceDetails):
    """Complete Request routing, values, policies, expansion, and evaluation topology."""

    KINDS: ClassVar[frozenset[str]] = frozenset({"Request"})
    PRESENTATION: ClassVar[ResourcePresentation] = ResourcePresentation(
        common_representation_hints=True,
        visible_actions=frozenset(
            {
                ResourceActionId.USE_IN_COMPARE,
                ResourceActionId.INSPECT_COMPOSITION,
            }
        ),
    )

    @override
    def compose(self) -> ComposeResult:
        """Render every Request field in the same hierarchy for raw and resolved modes."""
        overview = request_overview(self.entry)
        resolved = self._resolved_fields()

        yield RequestContextStrip(self.entry, overview)
        if self.resource.overview_mode is ResourceOverviewMode.RESOLVED:
            yield resolution_status(
                self.resource,
                self.entry.summary.name or self.entry.summary.coordinate.resource_id,
            )

        yield Separator(label="Routing", align="left", classes="tight")
        yield ResourceSettingList(
            overview.routing,
            empty="No HTTP routing fields are available.",
            resolved=resolved,
        )

        for title, settings in (
            ("Parameters", overview.parameters),
            ("Inputs", overview.inputs),
            ("Payload", overview.payload),
            ("Behavior", overview.behavior),
            ("Matrix expansion", overview.expansion),
            ("Validation & comparison", overview.evaluation),
            ("Safety & output", overview.policies),
        ):
            if settings:
                yield Separator(label=title, align="left")
                yield ResourceSettingList(settings, empty="", resolved=resolved)

        if overview.additional:
            yield Separator(label="Additional configuration", align="left")
            yield ResourceSettingList(
                overview.additional,
                empty="No additional Request settings are declared.",
                resolved=resolved,
            )

    def _resolved_fields(self) -> dict[tuple[str, ...], ResolvedResourceField]:
        """Index exact references and the masked resolver overlay without changing layout."""
        if self.resource.overview_mode is not ResourceOverviewMode.RESOLVED:
            return {}
        fields = {field.path: field for field in compiled_exact_fields(self.entry)}
        inspection = self.resource.resolved
        if inspection.phase is InspectionPhase.READY and inspection.value is not None:
            fields.update((field.path, field) for field in inspection.value.fields)
        return fields


__all__ = ["RequestContextStrip", "RequestResourceDetails"]
